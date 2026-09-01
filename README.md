# hubspot-mcp

A standalone [Model Context Protocol](https://modelcontextprotocol.io) server
for HubSpot CRM, distributable as a **Claude Code plugin**.

Speaks MCP protocol **`2026-07-28`** (and still serves handshake-era clients
back to `2024-11-05`, so nothing already installed breaks).

Exposes **79 domain tools** (contacts, companies, deals, tickets, pipelines,
owners, search, workflows, blueprints, docs, etc.), a **7-tool safety and
introspection layer**, and **44 specialist charters as MCP prompts**.

Every write is gated. Nothing reaches HubSpot until a human approves it, and
each approved write records an undo snapshot and an audit entry.

Auth is **bring-your-own-app OAuth** (default) — you supply your own HubSpot
private app's client credentials; tokens refresh automatically. A private-app
token (PAT) fallback is available via the CLI.

## Install as a Claude Code plugin

### 1. Create a HubSpot private app

1. Go to the [HubSpot developer portal](https://developers.hubspot.com) and
   create (or open) a private app.
2. In the app's **Auth** tab, copy the **Client ID** and **Client Secret**.
3. Add this **redirect URI**:
   ```
   http://localhost:3000/oauth/callback
   ```
4. Note your **portal (hub) ID** (shown in HubSpot under the gear icon →
   "Hub ID", or in the app URL).

### 2. Add the marketplace and install the plugin

In Claude Code:

```
/plugin marketplace add promptmetrics/hubspot-mcp
/plugin install hubspot-mcp@promptmetrics-hubspot-mcp
```

You'll be prompted for four values:

| Field | What to enter |
| --- | --- |
| `hubspot_client_id` | your app's Client ID |
| `hubspot_client_secret` | your app's Client Secret (stored in your OS keychain) |
| `hubspot_portal_id` | your portal (hub) ID |
| `hubspot_region` | `us` (default) or `eu` if your app was created in the EU portal |

### 3. Authenticate

The plugin's SessionStart hook writes your app credentials to
`~/.claude/hubspot/app_credentials.json` (chmod 600) on every session start.
To sign in to your portal, run:

```
/hubspot-mcp:auth
```

This opens a browser to HubSpot; approve the scopes. After it prints
`OAuth login succeeded`, the 86 HubSpot tools are live.

> **First run:** the MCP server provisions an isolated Python venv on first
> launch (this takes ~20–30s while it installs `mcp`, `httpx`, `pydantic`).
> If the tools don't appear right away, restart the session. Requires
> **Python 3.12+** on your `PATH`.

## Tools

- **79 domain tools**: CRUD + search across contacts, companies, deals,
  tickets, tasks, owners, pipelines, stages, properties, lists, engagements,
  workflows, workflow blueprints, official-docs search, and more.
- **7 safety and introspection tools**: `hubspot_approve_write`,
  `hubspot_reject_write`, `hubspot_list_pending_writes`,
  `hubspot_list_recent_audit`, `hubspot_undo_write`, `hubspot_status`
  (portal entitlements + request/error/cost aggregates), and `hubspot_route`
  (which specialist charter handles a request).
- **44 charters as MCP prompts** (`hubspot_objects`, `hubspot_workflows`, …):
  per-domain operating instructions naming the tools that domain may use, its
  self-correction rules, and a mandatory re-fetch-and-compare after every write.

### Approval tiers

Not every write deserves the same ceremony, so each is classified:

| Tier | When | What you do |
|---|---|---|
| `AUTO` | reversible, single-record, no sensitive field | applies immediately, reports an undo command |
| `CONFIRM` | sensitive property, large batch, or a side-effectful tool | one approval, no count |
| `FULL_GATE` | destructive, or reversibility not confirmed | approve with the **exact** record count |

Safety lists are configurable per portal in `approval_policy.json`, but an
override can only *add* a protection, never remove a shipped one.

On clients that support elicitation the approval happens **inline in one call**
(MCP Multi Round-Trip Requests); elsewhere it falls back to the
`hubspot_approve_write` flow, so cross-session approval keeps working.

### Portal-aware tool surface

Tools your portal is not entitled to (workflows, users, service automation) are
dropped from `tools/list` — but only when the entitlement probe is *conclusive*.
A transient failure leaves them advertised and explains any refusal at call
time, rather than silently hiding a dozen tools after one network blip.

Every mutating call goes through a **preview → approve** gate. Approved writes
record an undo snapshot and an audit-log entry under
`~/.claude/hubspot/<portal>/`.

## CLI (standalone, outside the plugin)

The package also installs a `hubspot-mcp` console script:

```sh
hubspot-mcp run --transport stdio          # run the MCP server
hubspot-mcp auth login --portal <id>       # OAuth flow
hubspot-mcp auth login --portal <id> --mode token   # PAT fallback
hubspot-mcp auth status --portal <id>      # show auth state
```

### Serving over HTTP

Every HTTP request must carry `Authorization: Bearer $HUBSPOT_MCP_SERVER_SECRET`
— protocol `2026-07-28` has no handshake, so there is no connection to
authenticate once and trust thereafter. `GET /healthz` is the one public path.

```sh
export HUBSPOT_MCP_SERVER_SECRET="$(openssl rand -base64 32)"
hubspot-mcp run --transport http --host 0.0.0.0 --port 8000
```

The server **refuses to start** on any non-loopback bind without that variable
set, or with a secret shorter than 32 characters. Binding to `127.0.0.1` without
one is allowed for local development and warns on stderr.

The token is a single shared secret for the whole deployment, so one portal per
process — per-user OAuth and multi-tenancy are Phase 3.

## Layout

```
plugin.json                       plugin manifest (userConfig, mcpServers, hook ref)
.claude-plugin/marketplace.json   marketplace catalog (source: "./")
bin/run-mcp.sh                    venv-provisioning MCP launcher
bin/session-start.sh              SessionStart hook: writes app_credentials.json
hooks/hooks.json                  SessionStart hook wiring
skills/auth/SKILL.md              /hubspot-mcp:auth slash command
src/hubspot_mcp/                  the MCP server (79 domain + 7 safety/introspection tools, 44 prompts)
```

## Troubleshooting

- **`/hubspot-mcp:auth` says command not found** — the venv didn't provision.
  Ensure `python3 --version` is 3.12+, then `/plugin` → reinstall
  `hubspot-mcp`.
- **MCP server fails to start** — check `python3 >= 3.12` and network access
  for the first `pip install`.
- **OAuth redirect mismatch** — confirm `http://localhost:3000/oauth/callback`
  is registered in your app's Auth tab.
- **EU portal errors ("Hub is unknown to this Hublet")** — set
  `hubspot_region` to `eu`.

## License

MIT.
