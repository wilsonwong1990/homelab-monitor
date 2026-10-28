"""Minimal Agent Client Protocol (ACP) client: NDJSON JSON-RPC 2.0 over TCP.

The gateway is a read-only client. It advertises no filesystem or terminal
capabilities and rejects every permission request, so an agent can answer but
cannot be authorized by chat to change anything. Changes go through the
executor's Discord/GitHub approval lanes instead.
"""

from __future__ import annotations

import asyncio
import json
import logging

LOG = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MAX_LINE = 1024 * 1024
MAX_TEXT = 16000


class ACPError(RuntimeError):
    pass


def _permission_response(params):
    options = params.get("options") if isinstance(params, dict) else None
    for option in options if isinstance(options, list) else []:
        if isinstance(option, dict) and option.get("kind") == "reject_once" and option.get("optionId"):
            return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


class _Connection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.next_id = 0
        self.text = []
        self.size = 0
        self.denied = 0

    async def send(self, message):
        self.writer.write(json.dumps(message).encode() + b"\n")
        await self.writer.drain()

    async def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            line = await self.reader.readline()
            if not line:
                raise ACPError("agent closed the connection")
            if len(line) > MAX_LINE:
                raise ACPError("agent message exceeds size limit")
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):
                raise ACPError("agent sent invalid JSON") from None
            if not isinstance(message, dict):
                raise ACPError("agent sent an invalid message")
            if "method" in message:
                await self._handle(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {}
                raise ACPError(f"agent error {error.get('code', '?')}")
            result = message.get("result")
            if result is None:
                return {}
            if not isinstance(result, dict):
                raise ACPError("agent sent an invalid result")
            return result

    async def _handle(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if method == "session/update":
            update = params.get("update") if isinstance(params, dict) else None
            if (isinstance(update, dict) and update.get("sessionUpdate") == "agent_message_chunk"
                    and isinstance(update.get("content"), dict) and update["content"].get("type") == "text"):
                chunk = str(update["content"].get("text", ""))
                if self.size < MAX_TEXT:
                    self.text.append(chunk[:MAX_TEXT - self.size])
                    self.size += len(chunk)
            return
        if "id" not in message:
            return
        if method == "session/request_permission":
            self.denied += 1
            LOG.info("ACP permission request rejected (chat gateway is read-only)")
            await self.send({"jsonrpc": "2.0", "id": message["id"], "result": _permission_response(params)})
            return
        await self.send({"jsonrpc": "2.0", "id": message["id"],
                         "error": {"code": -32601, "message": "method not supported by read-only client"}})


async def prompt_agent(host, port, prompt, *, cwd="/sandbox", timeout=180):
    """Send one stateless prompt and return (text, rejected_permission_count)."""

    async def run():
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=MAX_LINE + 1), timeout=10)
        connection = _Connection(reader, writer)
        try:
            await connection.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": {"name": "homelab-monitor", "title": "Homelab Discord gateway", "version": "0.1"},
            })
            session = await connection.request("session/new", {"cwd": cwd, "mcpServers": []})
            session_id = session.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise ACPError("agent did not return a session")
            await connection.request("session/prompt", {
                "sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]})
            return "".join(connection.text), connection.denied
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    try:
        return await asyncio.wait_for(run(), timeout=timeout)
    except asyncio.TimeoutError:
        raise ACPError("agent timed out") from None
    except ACPError:
        raise
    except (OSError, asyncio.LimitOverrunError, ValueError) as error:
        raise ACPError(f"agent unavailable ({type(error).__name__})") from None
    except Exception as error:
        LOG.warning("ACP protocol error: %s", type(error).__name__)
        raise ACPError("agent protocol error") from None
