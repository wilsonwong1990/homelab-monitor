# One home entry point: Discord conversations

The Discord bot that already carries alerts and approvals is also the single
place to *ask* about the homelab and the house. Ask questions in a chat
channel. Alerts and approvals stay in their dedicated channel.

## Decision summary

| Question | Decision | Why |
|---|---|---|
| Discord or Slack? | **Discord only** for now | Discord already carries alerts and approvals, and one transport means one bot token, one identity allowlist and one audit trail. Slack would need a second app (Socket Mode), a second authorization layer and duplicated tests for no new capability. To add Slack later, add a transport that calls the same `converse()` router. |
| Move the ACP chat (Copilot on k3s) into Discord? | **Yes, as an opt-in route** (`!agent`) | You get one place to ask. The k3s ACP pods stay where they are; Discord becomes just another ACP client. |
| Keep ACP-chat-UI? | Keep it as an optional rich web UI, or retire it | Discord covers quick questions. The web UI is still better for long agentic sessions with tool output. See [ACP-chat-UI caveat](#acp-chat-ui-caveat). |
| Which model answers? | **Local by default** (`!ask`); cloud only when explicitly asked (`!agent NAME`) | Monitor and home data never leave the host unless you type them into an `!agent` message yourself. |
| Can chat change things? | **No** | Every route is read-only. Changes still go through `!propose`/`!approve` (allowlisted low-risk runbooks) or the GitHub lane. |

## Routes

| Command | Backend | Data that leaves the host |
|---|---|---|
| `!status`, `!read CHECK` | Monitor SQLite (read-only) | None (reply goes to Discord) |
| `!home [ENTITY ...]` | Home Assistant REST, allowlisted entities only | None beyond Discord |
| `!ask QUESTION` | Loopback Ollama, verified non-cloud via `/api/show`; context is the monitor snapshot plus allowlisted HA states | None beyond Discord |
| `!agents` | Config | None |
| `!agent NAME QUESTION` | ACP agent over TCP (for example `copilot --acp` from copilot-acp-server) | **Only the text you typed**, sent to the agent and its cloud model. Monitor and HA data are never attached. |
| `!propose`, `!approve` | Executor (approvals channel only) | Unchanged policy |

Every reply is labelled with where it came from: *Local model (not verified)* or
*Cloud agent NAME (read-only)*.

```mermaid
flowchart LR
  U[You on Discord] --> B[homelab-discord gateway]
  B -->|!status !read| DB[(monitor.db read-only)]
  B -->|!home| HA[Home Assistant allowlisted entities]
  B -->|!ask| O[Ollama loopback] 
  DB --> O
  HA --> O
  B -->|!agent k3s| ACP[copilot-acp-server on k3s]
  ACP -.->|cloud model| GH[GitHub Copilot]
  B -->|!propose !approve approvals channel| X[executor allowlisted runbooks]
```

## Read-only ACP client

`homelab_monitor/acp.py` implements just enough of the Agent Client Protocol
(NDJSON JSON-RPC 2.0 over TCP) to be a safe client:

- It advertises no filesystem or terminal capabilities and passes no MCP servers.
- Every `session/request_permission` is answered with the agent's `reject_once`
  option, or `cancelled` if there isn't one. **It never picks an allow option.**
  The reply reports how many permission requests were rejected.
- Any other client-side method (for example `fs/write_text_file`) gets a
  JSON-RPC "method not supported" error.
- Sessions are stateless (one ACP session per message), with a per-agent timeout
  of at most 600 s and caps on message and response size.
- Each user can have one request in flight at a time, on top of the existing
  3-second cooldown.

Rejecting permissions limits what the *agent's tools* can do. It does not limit
what credentials the agent pod holds. **Defence in depth on the agent side is
still required:**

1. Give the ACP pod a read-only kubeconfig/ServiceAccount (`get`/`list`/`watch`)
   and read-only Proxmox/HA tokens. Don't mount admin credentials into an image
   that a chat message can reach.
2. ACP over TCP has **no authentication**. Anyone who can reach the port can
   drive the agent and spend its Copilot PAT. Expose it as a ClusterIP only, add
   a NetworkPolicy that admits just the monitor host (or run the gateway in-cluster),
   and never publish it through ingress or a LoadBalancer.
3. Use a fine-grained PAT with only the Copilot scope for `COPILOT_GITHUB_TOKEN`.

### ACP-chat-UI caveat

ACP-chat-UI's client (`server/src/acp-client.ts`) answers
`requestPermission` by selecting `options[0]`, which in practice is usually
**allow**. Anyone logged into the web UI therefore implicitly approves every
tool call the agent makes. If you keep the web UI, change it to reject or prompt
the user, and replace the default `admin/admin` login. That's a separate change
in that repository.

## Configure

1. Create a dedicated Discord channel for conversation (for example `#home`) and
   keep the existing approvals channel for alerts and `!approve`.
2. Install the conversation config:

   ```bash
   sudo install -m 0640 -o root -g homelab-observe \
     /opt/homelab-monitor/examples/conversation.example.json /etc/homelab-monitor/conversation.json
   sudoedit /etc/homelab-monitor/conversation.json
   sudo -u homelab-discord /opt/homelab-monitor/.venv/bin/python -m homelab_monitor \
     validate-chat /etc/homelab-monitor/conversation.json
   ```

   - `channel_ids`: numeric IDs of the conversation channel(s). The existing user
     and guild allowlist in `discord-identity.json` still applies.
   - `home_assistant.entities`: the only entities that `!home` and `!ask` may read
     (at most 25). Use a dedicated HA user's long-lived token. HA tokens aren't
     scoped, so the allowlist is enforced here, and the gateway never calls
     HA services.
   - `agents`: remove the section to stay fully local. Each agent needs
     `"cloud_egress_acknowledged": true`.
3. Add the HA token for the bot (same variable name as the monitor):

   ```bash
   echo 'HA_AUTHORIZATION=Bearer REPLACE_LONG_LIVED_TOKEN' | sudo tee -a /etc/homelab-monitor/discord.env
   ```

4. Enable the routes with a systemd drop-in, so the base unit and upgrades stay
   untouched:

   ```bash
   sudo systemctl edit homelab-discord.service
   ```

   ```ini
   [Service]
   ExecStart=
   ExecStart=/opt/homelab-monitor/.venv/bin/python -m homelab_monitor --db /var/lib/homelab-monitor/monitor.db --socket /run/homelab-executor/control.sock discord --identity /etc/homelab-monitor/discord-identity.json --chat-config /etc/homelab-monitor/conversation.json
   ```

   ```bash
   sudo systemctl restart homelab-discord && journalctl -u homelab-discord -n 20
   ```

5. Try `!home`, `!ask is anything down?` and `!agent k3s summarise unhealthy pods`
   in the chat channel. `!approve` in the chat channel must be refused.

## Homelab vs production

- **Homelab way:** one bot process on the monitor host; a local model for
  anything that touches your data; cloud reasoning only when you ask for it.
- **Production way:** a chat-ops platform with SSO, per-command RBAC, an audit
  pipeline and a policy engine such as OPA in front of every tool call.
- **Learning value:** the same principles carry over. The transport isn't the
  authority, the model's output is never an instruction, and least privilege
  applies to credentials, not just to prompts.

## Not in this change

- Conversation memory or Discord threads. Each message is answered on its own;
  restate the context, or use ACP-chat-UI for long sessions.
- A Slack transport.
- Home Assistant service calls (turning devices on or off). If you want them,
  add them as executor runbooks with their own approval lane, not as a chat route.
