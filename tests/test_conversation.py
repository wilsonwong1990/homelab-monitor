import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from homelab_monitor import acp, conversation, core, discord_bot, executor
from homelab_monitor.cli import main

ROOT = Path(__file__).resolve().parent.parent


class FakeAgent:
    """ACP agent that streams text and asks for a write permission mid-prompt."""

    def __init__(self, permission_options=None, unknown_request=False):
        self.permission_options = permission_options
        self.unknown_request = unknown_request
        self.received = []
        self.permission_reply = None
        self.unknown_reply = None

    async def handle(self, reader, writer):
        async def send(message):
            writer.write(json.dumps(message).encode() + b"\n")
            await writer.drain()

        async def read():
            return json.loads(await reader.readline())

        while True:
            line = await reader.readline()
            if not line:
                break
            message = json.loads(line)
            self.received.append(message)
            method = message["method"]
            if method == "initialize":
                await send({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": 1}})
            elif method == "session/new":
                await send({"jsonrpc": "2.0", "id": message["id"], "result": {"sessionId": "s1"}})
            elif method == "session/prompt":
                await send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk",
                                                  "content": {"type": "text", "text": "pods "}}}})
                if self.permission_options is not None:
                    await send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission",
                                "params": {"sessionId": "s1", "options": self.permission_options,
                                           "toolCall": {"title": "kubectl delete pod"}}})
                    self.permission_reply = await read()
                if self.unknown_request:
                    await send({"jsonrpc": "2.0", "id": 901, "method": "fs/write_text_file",
                                "params": {"path": "/etc/passwd", "content": "x"}})
                    self.unknown_reply = await read()
                await send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk",
                                                  "content": {"type": "text", "text": "healthy"}}}})
                await send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
        writer.close()


async def run_agent(agent, prompt="are pods ok?", **kwargs):
    server = await asyncio.start_server(agent.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        return await acp.prompt_agent("127.0.0.1", port, prompt, **kwargs)
    finally:
        server.close()
        await server.wait_closed()


class ACPTests(unittest.TestCase):
    def test_streams_text_and_advertises_no_capabilities(self):
        agent = FakeAgent()
        text, rejected = asyncio.run(run_agent(agent))
        self.assertEqual((text, rejected), ("pods healthy", 0))
        init = agent.received[0]["params"]["clientCapabilities"]
        self.assertEqual(init, {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False})
        self.assertEqual(agent.received[1]["params"]["mcpServers"], [])
        self.assertEqual(agent.received[2]["params"]["prompt"], [{"type": "text", "text": "are pods ok?"}])

    def test_permission_requests_are_rejected_never_allowed(self):
        options = [{"optionId": "allow", "kind": "allow_once", "name": "Allow"},
                   {"optionId": "always", "kind": "allow_always", "name": "Always"},
                   {"optionId": "deny", "kind": "reject_once", "name": "Reject"}]
        agent = FakeAgent(permission_options=options)
        text, rejected = asyncio.run(run_agent(agent))
        self.assertEqual(rejected, 1)
        self.assertEqual(agent.permission_reply["result"],
                         {"outcome": {"outcome": "selected", "optionId": "deny"}})
        agent = FakeAgent(permission_options=[{"optionId": "allow", "kind": "allow_once"}])
        asyncio.run(run_agent(agent))
        self.assertEqual(agent.permission_reply["result"], {"outcome": {"outcome": "cancelled"}})

    def test_client_side_methods_are_refused(self):
        agent = FakeAgent(unknown_request=True)
        asyncio.run(run_agent(agent))
        self.assertEqual(agent.unknown_reply["error"]["code"], -32601)

    def test_malformed_result_is_acp_error(self):
        async def scenario():
            async def handle(reader, writer):
                await reader.readline()
                writer.write(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
                await reader.readline()
                writer.write(b'{"jsonrpc":"2.0","id":2,"result":"x"}\n')
                await writer.drain()
                await reader.read()
                writer.close()
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            try:
                await acp.prompt_agent("127.0.0.1", server.sockets[0].getsockname()[1], "x", timeout=5)
            finally:
                server.close()
                await server.wait_closed()
        with self.assertRaisesRegex(acp.ACPError, "invalid result"):
            asyncio.run(scenario())

    def test_unreachable_and_timeout(self):
        with self.assertRaises(acp.ACPError):
            asyncio.run(acp.prompt_agent("127.0.0.1", 1, "x", timeout=5))

        async def stall():
            async def handle(reader, writer):
                try:
                    await asyncio.sleep(5)
                finally:
                    writer.close()
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            try:
                await acp.prompt_agent("127.0.0.1", server.sockets[0].getsockname()[1], "x", timeout=0.3)
            finally:
                server.close()
                await server.wait_closed()
        with self.assertRaisesRegex(acp.ACPError, "timed out"):
            asyncio.run(stall())

    def test_close_timeout_aborts_transport(self):
        class Reader:
            def __init__(self):
                self.lines = [
                    b'{"jsonrpc":"2.0","id":1,"result":{}}\n',
                    b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}\n',
                    b'{"jsonrpc":"2.0","id":3,"result":{}}\n',
                ]

            async def readline(self):
                return self.lines.pop(0)

        class Transport:
            aborted = False

            def abort(self):
                self.aborted = True

        class Writer:
            def __init__(self):
                self.transport = Transport()

            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

            async def wait_closed(self):
                await asyncio.Future()

        async def scenario():
            writer = Writer()
            with patch.object(acp.asyncio, "open_connection", return_value=(Reader(), writer)), \
                    patch.object(acp, "CLOSE_TIMEOUT", 0.01):
                result = await acp.prompt_agent("agent", 1, "question", timeout=1)
            return result, writer

        result, writer = asyncio.run(scenario())
        self.assertEqual(result, ("", 0))
        self.assertTrue(writer.transport.aborted)

    def test_cancelled_close_aborts_transport(self):
        class Reader:
            def __init__(self):
                self.lines = [
                    b'{"jsonrpc":"2.0","id":1,"result":{}}\n',
                    b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}\n',
                    b'{"jsonrpc":"2.0","id":3,"result":{}}\n',
                ]

            async def readline(self):
                return self.lines.pop(0)

        class Transport:
            aborted = False

            def abort(self):
                self.aborted = True

        class Writer:
            def __init__(self):
                self.transport = Transport()
                self.closing = asyncio.Event()

            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

            async def wait_closed(self):
                self.closing.set()
                await asyncio.Future()

        async def scenario():
            writer = Writer()
            with patch.object(acp.asyncio, "open_connection", return_value=(Reader(), writer)):
                task = asyncio.create_task(acp.prompt_agent("agent", 1, "question", timeout=5))
                await writer.closing.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            return writer

        writer = asyncio.run(scenario())
        self.assertTrue(writer.transport.aborted)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = json.loads((ROOT / "examples/conversation.example.json").read_text())

    def load(self, data):
        path = Path(self.temp.name) / "chat.json"
        path.write_text(json.dumps(data))
        return conversation.load_chat_config(path)

    def test_example_validates(self):
        self.assertIn("k3s", self.load(self.base)["agents"])
        self.assertEqual(main(["validate-chat", str(ROOT / "examples/conversation.example.json")]), 0)

    def test_rejections(self):
        cases = [
            ("channel_ids", ["general"]),
            ("model", {"url": "http://192.0.2.5:11434", "name": "qwen3:4b"}),
            ("model", {"url": "http://127.0.0.1:11434", "name": "gpt-oss:120b-cloud"}),
            ("home_assistant", dict(self.base["home_assistant"], url="http://ha.example.internal:8123")),
            ("home_assistant", dict(self.base["home_assistant"], url="https://user:pw@ha.example.internal")),
            ("home_assistant", dict(self.base["home_assistant"], entities=["../../api/services"])),
            ("agents", {"k3s": dict(self.base["agents"]["k3s"], cloud_egress_acknowledged=False)}),
            ("agents", {"k3s": dict(self.base["agents"]["k3s"], port=0)}),
            ("agents", {"Bad Name": self.base["agents"]["k3s"]}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(core.ConfigurationError):
                self.load(dict(self.base, **{key: value}))
        http_ok = dict(self.base["home_assistant"], url="http://ha.example.internal:8123", allow_insecure_http=True)
        self.load(dict(self.base, home_assistant=http_ok))


class HomeAndAskTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "examples/conversation.example.json").read_text())
        env = patch.dict(os.environ, {"HA_AUTHORIZATION": "Bearer test-not-a-real-token"})
        env.start()
        self.addCleanup(env.stop)

    def test_home_reads_only_allowlisted_entities_and_minimizes_fields(self):
        with self.assertRaises(core.ConfigurationError):
            conversation.home_states(self.config, ["lock.garage"])
        payload = {"entity_id": "lock.example_front_door", "state": "locked",
                   "attributes": {"friendly_name": "Front door", "access_token": "secret", "unit_of_measurement": ""},
                   "last_changed": "2024-01-01T00:00:00+00:00"}
        with patch.object(conversation, "request_json", return_value=payload) as request:
            states = conversation.home_states(self.config, ["lock.example_front_door"])
        self.assertEqual(request.call_args.args[0], "https://ha.example.internal/api/states/lock.example_front_door")
        self.assertNotIn("secret", json.dumps(states))
        self.assertEqual(states["lock.example_front_door"]["state"], "locked")

    def test_home_entity_failure_is_reported_not_raised(self):
        with patch.object(conversation, "request_json", side_effect=core.TransportError("HTTP 401")):
            states = conversation.home_states(self.config, ["sensor.example_battery"])
        self.assertEqual(states["sensor.example_battery"], {"state": "unavailable", "error": "HTTP 401"})

    def test_ask_local_verifies_model_and_bounds_answer(self):
        show = {"model_info": {"general.architecture": "qwen3"}}
        answer = {"message": {"content": "x" * 5000}}
        with patch.object(conversation, "request_json", return_value=answer) as chat, \
                patch("homelab_monitor.investigation.request_json", return_value=show):
            result = conversation.ask_local(self.config, "is the battery low?", [{"id": "ha"}], {})
        self.assertEqual(len(result), 1500)
        self.assertEqual(chat.call_args.args[0], "http://127.0.0.1:11434/api/chat")
        with patch("homelab_monitor.investigation.request_json", return_value={"remote_host": "ollama.com", "model_info": {"a": 1}}), \
                patch.object(conversation, "request_json") as chat:
            with self.assertRaises(core.TransportError):
                conversation.ask_local(self.config, "q", [], {})
            chat.assert_not_called()

    def test_ask_local_budgets_both_evidence_sources_before_serializing(self):
        rows = [{"id": f"check-{index}", "status": "failing", "evidence": "detail " * 1000}
                for index in range(20)]
        states = {f"sensor.example_{index}": {"state": "available " * 100}
                  for index in range(25)}
        show = {"model_info": {"general.architecture": "qwen3"}}
        answer = {"message": {"content": "answer"}}
        with patch.object(conversation, "request_json", return_value=answer) as chat, \
                patch("homelab_monitor.investigation.request_json", return_value=show):
            conversation.ask_local(self.config, "summarize the system", rows, states)

        encoded = chat.call_args.kwargs["body"]["messages"][1]["content"].removeprefix("Evidence:\n")
        self.assertLessEqual(len(encoded), conversation.MAX_EVIDENCE)
        evidence = json.loads(encoded)
        self.assertTrue(evidence["monitor_checks"])
        self.assertTrue(evidence["home"])
        self.assertGreater(evidence["omitted"]["monitor_checks"], 0)
        self.assertGreater(evidence["omitted"]["home"], 0)


class GatewayTests(unittest.TestCase):
    settings = {"allowed_user_ids": ["1"], "guild_id": "2", "channel_id": "3"}
    chat = {"channel_ids": ["4"], "agents": {}}

    def ctx(self, user="1", guild="2", channel="3"):
        return {"user_id": user, "guild_id": guild, "channel_id": channel}

    def test_channel_authorization(self):
        self.assertTrue(discord_bot.authorize(self.settings, self.chat, self.ctx()))
        self.assertFalse(discord_bot.authorize(self.settings, self.chat, self.ctx(channel="4")))
        for bad in (self.ctx(user="9", channel="4"), self.ctx(guild="9", channel="4"), self.ctx(channel="5")):
            with self.subTest(bad=bad), self.assertRaises(executor.Denied):
                discord_bot.authorize(self.settings, self.chat, bad)
        with self.assertRaises(executor.Denied):
            discord_bot.authorize(self.settings, None, self.ctx(channel="4"))

    def test_chat_channel_cannot_approve_in_executor(self):
        with self.assertRaises(executor.Denied):
            executor.discord_actor({"discord": self.settings}, self.ctx(channel="4"))

    def test_converse_routes(self):
        self.assertIsNone(asyncio.run(discord_bot.converse("!status", self.chat, "unused")))
        with self.assertRaises(core.ConfigurationError):
            asyncio.run(discord_bot.converse("!ask hi", None, "unused"))
        with self.assertRaises(core.ConfigurationError):
            asyncio.run(discord_bot.converse("!agent nope hello", self.chat, "unused"))
        self.assertIn("none configured", asyncio.run(discord_bot.converse("!agents", self.chat, "unused")))
        self.assertIn("!propose", asyncio.run(discord_bot.converse("!agent", self.chat, "unused")))

    def test_agent_route_sends_only_user_text(self):
        chat = {"channel_ids": ["4"], "agents": {"k3s": {"host": "h", "port": 3000}}}

        async def fake(host, port, prompt, **kwargs):
            self.assertEqual(prompt, "why is   longhorn degraded?")
            return "replica rebuilding", 2
        with patch.object(discord_bot, "prompt_agent", side_effect=fake):
            reply = asyncio.run(discord_bot.converse("!agent k3s why is   longhorn degraded?", chat, "unused"))
        self.assertIn("Cloud agent k3s", reply)
        self.assertIn("2 tool permission request(s) rejected", reply)

    def test_fit_response_truncates_with_marker_within_transport_budget(self):
        response = "x" * 16000
        fitted = discord_bot._fit_response(response)
        self.assertEqual(len(fitted), discord_bot.RESPONSE_LIMIT)
        self.assertTrue(fitted.endswith(discord_bot.RESPONSE_TRUNCATION_MARKER))
        self.assertEqual(discord_bot._fit_response("short response"), "short response")


if __name__ == "__main__":
    unittest.main()
