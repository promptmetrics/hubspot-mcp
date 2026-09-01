---
title: "HubSpot MCP — Architecture Spec (Phase 1 & 2)"
status: phase-1-shipped
audience: dev team
created: 2026-07-07
owner: Izzy
---

# HubSpot MCP — Architecture Spec (Phase 1 & 2)

## TL;DR

We are repackaging the existing `hubspot-claude` Claude Code **plugin** (44 sub-agents, ~75 tools, preview/approve/undo safety model) into a standalone **MCP server** so it can reach Claude Cowork and other hosted surfaces. We ship in phases:

- **Phase 1 — Claude Code (CLI), local stdio.** ✅ **Shipped.** Delivered on MCP protocol `2026-07-28` (see D9) with bring-your-own-app OAuth rather than the static token planned here, 79 domain tools, risk-tiered approval, pattern approval, capability gating, and the 44 charters as MCP prompts (Task 10, see D10). Static-token auth remains as the PAT fallback.
- **Phase 2 — Same server, deployed remote HTTP, static bearer.** ~2–3 dev-days, $0 on free tier. Burns down hosting/WAF/DB/cold-start risk before we touch OAuth.
- **Phase 3 (later, out of scope here) — Cowork:** add OAuth 2.1+PKCE provider bridge, flip transport to streamable-HTTP. ~5–10 dev-days.

The load-bearing principle: **build the tool layer transport-agnostic and token-injected now, so Phase 2 and Phase 3 are additive layers, not rewrites.**

---

## 1. Background & Goals

### 1.1 The problem

`iiizzzyyy/hubspot-claude` is a Claude Code **plugin** (v0.1.4, Python 3.12, `httpx` + `pydantic`, direct HubSpot REST API). It works well in Claude Code but **cannot run in Claude Cowork** because Cowork's sandboxed Linux VM does not fire `SessionStart` hooks (`anthropics/claude-code#40495`, `#47993`) — so the plugin's venv-provisioning step never runs and `bin/hubspot` fails on first use.

### 1.2 The goal

Make the HubSpot tooling usable by **non-technical users in Claude Cowork** with one-click install, without losing the plugin's differentiated safety model (preview → `action_id` → approve → execute → undo/audit) and specialist coverage.

### 1.3 Why an MCP server (and not the alternatives we evaluated)

| Option | Verdict | Why |
|---|---|---|
| Keep it a Claude Code plugin only | Rejected | Doesn't reach Cowork — the stated goal. |
| Wholesale clone of all 75 tools as a generic "HubSpot MCP" | Rejected | Saturated: 11+ community servers exist (shinzo-labs ~112 tools, axonops 76, peakmojo, etc.) plus HubSpot's official GA remote MCP (`mcp.hubspot.com`, April 2026). Commodity CRUD duplicates existing work. |
| Build on Composio's managed MCP | Rejected as primary | Composio has 232 HubSpot tools + managed OAuth, but: (a) `LOCAL_*` custom tools (our differentiation) **do not work through Composio's MCP URL** — SDK path only; (b) Cowork's custom-connector path is broken (`#412`/`#56122` closed not-planned) plus Composio's own server-side 401 bug (`#3485`); (c) data-intermediary posture + May 2026 security incident. Composio may be revisited as a *commodity layer* in a later hybrid. |
| **Self-hosted MCP server, phased** | **Chosen** | Owns the full surface, no intermediary, no per-call metering, full control of schemas and safety model, and a clean ramp from $0 local to hosted to Cowork. |

### 1.4 What's differentiated (and why it's worth building ourselves)

The official HubSpot MCP and most community servers miss exactly what our plugin covers:

- **Reviewable multi-step write plans** (preview → approve → execute → undo-as-batch). Only Daeda does this, commercially.
- **Sensitive-Data-mode engagement bypass** — the official MCP *blocks* all engagements (calls/notes/emails/tasks) when the portal has Sensitive Data on; the standard REST API we use does not.
- **Conversations inbox, audit logs, security history, deal splits, forecasts, sequences** — uncovered by the official server and unconfirmed/absent in community servers.

Commodity CRUD (contacts/companies/deals search & create) is NOT the differentiation. We keep it for completeness and because we already wrote it, but it is not the reason to choose this project over existing servers.

---

## 2. Decision Log

Each decision: rationale, alternatives rejected, evidence.

### D1 — Repackage the existing plugin's tool layer as a standalone MCP server
- **Rationale:** The tool layer (`src/hubspot_agent/tools/`, `client.py`, `handlers.py`, safety model) already exists and is tested (~840 pytest tests per the repo). MCP is the transport boundary that Cowork can reach. We are not cloning a third-party repo — we own this code.
- **Rejected:** Cloning a community server (would inherit their architecture, not our safety model); using Composio only (can't carry custom tools via MCP URL).
- **Evidence:** Repo inventory — 18 tool modules / 75 tools, `handlers.py` shared across daemon/in-process/CLI paths, `safety.py` preview/approve/undo.

### D2 — Start with Claude Code (CLI) users (Mode A), phase to Cowork later
- **Rationale:** Mode A is $0, ~1–2 dev-days, ships immediately, and lets us validate the tool layer + plugin orchestration without the OAuth-bridge build cost or Cowork's open OAuth bugs. Cowork's OAuth path has open bugs (`#62801` loopback redirect not received in sandbox, `#196` `McpAuthorizationError`) — de-risk those on *their* timeline, not on the critical path of shipping.
- **Rejected:** Building the full remote OAuth server first (5–10 dev-days + hosting + Cowork-OAuth risk before any user value).
- **Evidence:** Static-token auth is confirmed supported in Claude Code CLI (`claude mcp add --header "Authorization: Bearer …"`, fixed in v2.1.119 per `#47424`) and confirmed **not** supported in Cowork (requires OAuth 2.1+PKCE user consent).

### D3 — Use FastMCP as the server framework  ~~(superseded by D9)~~
- **Rationale:** FastMCP supports both stdio and streamable-HTTP transports and both static-token and OAuthProxy auth modes in one codebase. `axonops/hubspot-mcp` (Python, FastMCP, May 2026) already proves the dual-mode pattern for HubSpot specifically. This makes Phase 2 (transport flip) and Phase 3 (auth swap) config/layer changes, not rewrites.
- **Rejected:** Raw `stdin`/`stdout` handling (paints into a corner); TypeScript-only bridges like `CooperNiebuhr/mcp-server-bridge` (our codebase is Python).
- **Evidence:** FastMCP `OAuthProxy` docs; `axonops/hubspot-mcp` dual `token`/`oauth` mode.

### D9 — Migrate from FastMCP to the official `mcp` SDK (supersedes D3), 2026-08-31
- **Rationale:** D3 predates protocol revision `2026-07-28`, which removes the `initialize`/`notifications/initialized` handshake and the `Mcp-Session-Id` header, adds `server/discover`, requires `Mcp-Method`/`Mcp-Name` headers on Streamable HTTP POSTs, adds a required `resultType` on every result, and requires `ttlMs`/`cacheScope` on list results (SEP-2549). FastMCP 3.x depends on `mcp<2.0` and speaks the handshake era only; the official `mcp` SDK shipped 2.0.0 on the spec release date and is the reference implementation. Verified: this server negotiates `2026-07-28` and *also* still serves handshake-era clients down to `2024-11-05` via `initialize`, so no client is locked out.
- **Rejected:** FastMCP 4.0 (built on `mcp>=2.0`, so it does reach the new spec, but it is beta — 4.0.0b5 as of 2026-08-28 — and the FastMCP-specific auth/middleware surface is not needed here); staying on FastMCP 3.x (stable, but cannot reach `2026-07-28` at all).
- **Consequences:** `MCPServer` replaces `FastMCP`; the `_lifespan` compat shim is deleted; `transport="http"` becomes `"streamable-http"` (`"http"` kept as a CLI alias); the legacy HTTP+SSE transport is deprecated upstream and is not offered. `mcp` pulls `httpx2` for its own transport while `client.py` stays on `httpx` — deliberate, because `pytest-httpx` and `respx` target `httpx` and moving the HubSpot client would strand the test suite's HTTP mocking.
- **Two migration seams worth remembering:** (1) the SDK resolves the context parameter from `__annotations__` (via `typing.get_type_hints`) but builds the JSON schema from `__signature__`; `_make_domain_wrapper` synthesises both, and setting only the signature silently leaks `ctx` into all 76 domain schemas. (2) Tool errors returned as data reach the client as *successful* results — anticipated failures must raise `ToolError` so the protocol layer sets `is_error`. `tests/test_protocol_conformance.py` pins both.
- **Evidence:** `mcp` 2.1.1 on PyPI (2026-08-25); `mcp_types.version.LATEST_PROTOCOL_VERSION == "2026-07-28"`; spec changelog SEP-2549/2567/2575/2322.

### D4 — Static private-app token auth for Phase 1 & 2; OAuth 2.1+PKCE deferred to Phase 3
- **Rationale:** Private-app tokens (`pat-na1-…`) are single-user, no consent flow, no token store, no refresh logic — minimal. They work for the CLI audience immediately. OAuth is required only for Cowork's multi-user consent model.
- **Rejected:** Building OAuth now (premature; 5–7× the cost; blocked by Cowork bugs anyway).
- **Caveat:** Private-app tokens never expire unless revoked, but are single-portal. Multi-portal support (the plugin already has `hubspot portal switch`) must be preserved via a portal-id → token map (env or config file in P1/P2, KV in P3).

### D5 — Orchestration stays in a Claude Code plugin; the MCP server exposes *only tools*
- **Rationale:** MCP is a stateless tool protocol — it has no primitive for "spawn sub-agent" or "human approval gate." Those are client-side. The 44 specialist sub-agents, routing, `/hubspot` slash command, durable loops, and the approve/reject UX belong in the plugin (sub-agents + skills + slash commands), which is already Cowork-compatible. The server stays focused on: HubSpot API calls + the preview/approve/undo state machine (exposed as stateful tools: `create_write_plan`, `approve_write_plan`, etc.).
- **Rejected:** Trying to put sub-agents or the approval UX inside the MCP server (not an MCP concept).
- **Evidence:** Cowork plugin components confirmed: skills, slash commands, sub-agents, connectors. Sub-agents can use MCP connectors (`#43320`, `#58353` fixed).

### D6 — Token acquisition + approval/undo state behind interfaces
- **Rationale:** Phase 1 resolves tokens from env vars and stores approval/undo state in flat files. Phase 3 resolves tokens from an encrypted KV store (per-user) and stores state in KV. If both are behind interfaces, the tool layer never changes.
- **Rejected:** Reading `os.environ` deep inside tool functions; storing undo snapshots in hardcoded paths.

### D7 — Hosting: none in Phase 1; Google Cloud Run + Upstash Redis free tier in Phase 2
- **Rationale:** Cloud Run gives 2M req/mo free with real Python Docker and sub-second cold starts; Upstash gives 500k Redis commands/mo free with no daily cap. Both are $0 at low volume and scale predictably. Anthropic's MCP egress (`160.79.104.0/21`) reaches all major clouds.
- **Rejected alternatives:** Vercel Hobby (non-commercial only — unsafe for a work integration); Render free (60s cold starts); Fly.io (no free tier for new orgs); Hugging Face Spaces (ephemeral + sleeps — fragile for OAuth state later); Cloudflare Workers Python (10ms CPU too tight for httpx+pydantic).
- **Gotcha to document for ops:** If Cloudflare (or any WAF) is placed in front, allowlist `160.79.104.0/21` on `/mcp`, `/.well-known/*`, `/authorize`, `/token`, `/register`, `/auth/callback` — WAF bot-blocking is the single most common "couldn't reach MCP server" cause (`claude-ai-mcp#214`).

### D8 — Phase 3 will fork `axonops/hubspot-mcp` for the OAuth bridge; design P1/P2 to make that graft clean
- **Rationale:** `axonops/hubspot-mcp` already implements the HubSpot-specific FastMCP `OAuthProxy` config (endpoints, scopes, `forward_pkce=False`, `client_secret_basic`), `HubSpotTokenVerifier`, and `/.well-known/*` endpoints. Forking it collapses the OAuth bridge from weeks to ~5–10 dev-days. Our Phase 1/2 tool layer should be structured to drop into that server with minimal adaptation.
- **Note:** Phase 3 is out of scope for this spec but informs the interface boundaries here.

---

## 3. Phase 1 Architecture — Local stdio, static token (Claude Code CLI)

### 3.1 Shape

```
User (Claude Code CLI)
  └── loads plugin (marketplace or local)
        ├── .mcp.json  →  declares stdio MCP server: `python -m hubspot_mcp --transport stdio`
        ├── agents/    →  44 specialist sub-agents (markdown frontmatter, reused from plugin)
        ├── skills/    →  routing + safety + loop skills
        └── commands/  →  /hubspot slash command
                    │
                    ▼  (sub-agents call tools via stdio JSON-RPC)
        ┌──────────────────────────────────┐
        │ hubspot_mcp (FastMCP, stdio)     │
        │  ├── tools/*   (75 HubSpot tools)│
        │  ├── safety    (preview/approve/ │
        │  │             undo state machine)│
        │  ├── client    (httpx HubSpot)   │
        │  ├── TokenProvider  (env)        │
        │  └── StateStore     (file)       │
        └──────────────────────────────────┘
                    │
                    ▼  HTTPS
              api.hubapi.com
```

### 3.2 Components

- **`hubspot_mcp` server (new, Python 3.12, FastMCP).** Wraps the existing `hubspot_agent` tool/handler/client modules. Exposes tools via `@mcp.tool`. Transport = `stdio`.
- **TokenProvider interface.** P1 impl: `EnvTokenProvider` — reads `HUBSPOT_PRIVATE_APP_TOKEN` (and a `HUBSPOT_PORTAL_TOKENS` JSON map for multi-portal). Resolved per-request via FastMCP context dependency, never read deep in tool bodies.
- **StateStore interface.** P1 impl: `FileStateStore` — writes preview plans, undo snapshots, and audit entries under `~/.hubspot-mcp/<portal>/` (mirrors the plugin's existing `~/.claude/hubspot/<portal>/` layout). One user, one machine.
- **Safety model (preserved from plugin).** Writes return a preview + `action_id`; nothing mutates until `approve(action_id)`. Destructive ops require an expected count, re-checked at execute. Every approved create/update/delete writes an undo snapshot + audit entry.
- **Plugin (Claude Code / Cowork-compatible).** Reuses the plugin's 44 sub-agents, skills, and `/hubspot` command unchanged — they now call MCP tools (`mcp__hubspot__<tool>`) instead of shelling out to `bin/hubspot`. This is the only orchestration change from the existing plugin.

### 3.3 Auth & secrets

- Single private-app token per portal, in env or a local config file (gitignored). **Never committed.**
- Multi-portal: `portal_id → token` map. Working-dir `.hubspot-portal` file selects active portal (preserved from plugin).

### 3.4 What Phase 1 does NOT include

- No hosting, no HTTPS, no OAuth, no public endpoint.
- No multi-user concurrency (single user, local).
- No Cowork support (stdio doesn't run in Cowork sandbox).

### 3.5 Phase 1 verification

1. `pip install -e .` then `python -m hubspot_mcp --transport stdio` — server starts and responds to `tools/list`.
2. In Claude Code: `claude mcp add hubspot --transport stdio -- python -m hubspot_mcp --transport stdio` (or via plugin's `.mcp.json`).
3. `/mcp` lists `hubspot` and the 75 tools.
4. End-to-end: `/hubspot merge duplicates in companies named "Acme"` → sub-agent routes → calls `search_companies` → preview returned with `action_id` → `/hubspot approve <id> 3` → merge executed → undo snapshot + audit entry written.
5. `pytest -x` passes (port the existing ~840 tests; add FastMCP transport tests).

---

## 4. Phase 2 Architecture — Remote HTTP, static bearer (Claude Code CLI)

### 4.1 What changes from Phase 1

Only three things:
1. **Transport:** `stdio` → `streamable-http` (FastMCP config flag).
2. **Deployment:** server runs on Google Cloud Run (Python Docker image), public HTTPS endpoint.
3. **Auth middleware:** add an `Authorization: Bearer <token>` check on every request. The bearer is a **server-side static secret** the user sets and passes via `claude mcp add --header`. (This is *not* OAuth — it's a shared secret between the CLI user and the server. Single-tenant or small-team.)

Everything else — tool layer, safety model, plugin, sub-agents — is unchanged.

### 4.2 Shape

```
User (Claude Code CLI)
  └── claude mcp add hubspot --transport http \
        https://<run-url>/mcp \
        --header "Authorization: Bearer <SERVER_SECRET>"
        │
        ▼  HTTPS + Bearer
        ┌──────────────────────────────────┐
        │ Cloud Run  (Python Docker)       │
        │  hubspot_mcp (FastMCP, http)     │
        │   ├── BearerAuth middleware      │
        │   ├── tools/*  (same 75 tools)   │
        │   ├── safety   (same)            │
        │   ├── TokenProvider → env (HubSpot PAT) │
        │   └── StateStore  → Upstash Redis │
        └──────────────────────────────────┘
            │            │
            ▼            ▼
      api.hubapi.com   Upstash Redis
                      (preview/undo/audit state)
```

### 4.3 What's added

- **BearerAuth middleware** — constant-time compare of `Authorization: Bearer <SERVER_SECRET>`. `SERVER_SECRET` from Cloud Run env var. (This is the only new code of substance.)
- **StateStore impl: `RedisStateStore`** — preview plans / undo snapshots / audit entries move from flat files to Upstash Redis (encrypted Fernet blobs keyed by `action_id` / portal). This is required because Cloud Run instances are ephemeral; file state wouldn't survive. **This is why StateStore was an interface in P1.**
- **Docker image + Cloud Run service.** `min-instances=0` for free tier (accept sub-second cold starts) or `min-instances=1` (~$2–5/mo) to remove them.
- **`/healthz` endpoint** + WAF allowlist for `160.79.104.0/21` if Cloudflare is in front.

### 4.4 What Phase 2 still does NOT include

- No OAuth, no user consent, no per-user HubSpot tokens. Still single-tenant (one HubSpot PAT in the server's env) or small-team (shared `SERVER_SECRET`).
- No Cowork support (Cowork rejects static bearer — requires OAuth).

### 4.5 Why Phase 2 is worth doing (instead of jumping P1 → P3)

It burns down the **infrastructure** risk (hosting, HTTPS, cold starts, WAF, Redis state, egress) in isolation, *before* the **OAuth** risk is layered on. When Phase 3 adds OAuth, the only new variable is OAuth — debugging is tractable. Skipping P2 means debugging hosting + OAuth + Cowork bugs simultaneously.

### 4.6 Phase 2 verification

1. `docker build` + `gcloud run deploy` — service live at `https://<run-url>`.
2. `curl https://<run-url>/healthz` → 200. `curl /mcp` without bearer → 401.
3. In Claude Code (≥ v2.1.119 on macOS/Linux): `claude mcp add hubspot --transport http https://<run-url>/mcp --header "Authorization: Bearer <SERVER_SECRET>"`.
4. `/mcp` lists tools; same end-to-end merge-duplicates flow as P1 passes.
5. Confirm preview/undo state survives a cold-start cycle (instance recycled between `create_write_plan` and `approve_write_plan`) — proves Redis StateStore works.
6. Check Cloud Run logs for Anthropic egress (`160.79.104.0/21`) reaching the service; if 403s, check for a WAF in front.

---

## 5. Cross-Phase Architecture Principles (the day-1 choices that preserve the Cowork path)

These are the load-bearing interface decisions. Reviewers should push back here if any is wrong — everything else is mechanical.

1. **Transport-agnostic tools.** Tools are FastMCP `@mcp.tool` functions. No `stdin`/`stdout` access in tool bodies. `stdio` ↔ `streamable-http` is a startup flag.
2. **Token injection via context, not deep env reads.** Tools receive a `HubSpotClient` constructed by a `TokenProvider` resolved per-request from FastMCP context. P1: `EnvTokenProvider`. P3: `OAuthTokenProvider` (per-user, KV-backed). Tool bodies never know which.
3. **StateStore interface for all mutable state** (preview plans, undo snapshots, audit log, loop checkpoints). P1: `FileStateStore`. P2/P3: `RedisStateStore`. Same interface.
4. **Orchestration in the plugin, not the server.** Sub-agents, routing, approval UX, loops stay in the plugin (markdown + frontmatter). The server exposes tools only — including stateful ones (`create_write_plan`, `approve_write_plan`) that implement the safety state machine.
5. **Mirror `axonops/hubspot-mcp`'s dual-mode structure.** One entrypoint, `--mode token|oauth`, `--transport stdio|http`. P1/P2 use `token` mode; P3 adds `oauth` mode without forking the codebase.
6. **Keep the HubSpot client behind a thin interface** that takes a token + portal_id. Same client code serves a static PAT (P1/P2) and an OAuth-derived short-lived token (P3).

If we hold these six, Phase 3 is "add `OAuthTokenProvider` + `OAuthProxy` + `/.well-known/*` + flip transport" — additive, no tool-layer rewrite.

---

## 6. Roadmap to Phase 3 (Cowork) — for context, not in scope

Two paths from P2 to Cowork; P1/P2 architecture preserves **both**, so we decide later:

- **Path A — Remote OAuth server.** Fork `axonops/hubspot-mcp`, graft our tool layer in, add `OAuthProxy` (HubSpot endpoints, `forward_pkce=False`, `client_secret_basic`), externalize tokens to Upstash/DynamoDB, deploy streamable-HTTP. ~5–10 dev-days. Universal reach (claude.ai web, Cursor, etc.). **Risk:** Cowork OAuth bugs (`#62801` loopback redirect, `#196`) — must spike end-to-end on day 1. Workaround for `#62801`: use a public HTTPS callback URL, not `localhost` (unconfirmed).
- **Path B — Plugin-bundled local stdio MCP in Cowork sandbox.** If Cowork spawns a plugin-declared `.mcp.json` stdio server inside its sandbox, we get Cowork with **zero hosting, zero OAuth** — just repackage the P1 server into the Cowork plugin. **Status: unverified.** This is the single most valuable crux to de-risk, because a positive answer makes Path A unnecessary.

**Day-1 spike for Phase 3:** minimal Cowork plugin with a `.mcp.json` declaring one dummy stdio tool → does `/mcp` list it and can chat call it? (Decides Path B.) In parallel: minimal remote OAuth server → does a Cowork chat complete a tool call end-to-end? (Decides Path A.) Both are 1-day spikes.

---

## 7. Open Risks & Cruxes

| Risk | Status | Mitigation |
|---|---|---|
| Cowork ignores `SessionStart` hooks (`#40495`) | Confirmed open | Not relied upon in any phase. P1/P2 don't target Cowork. |
| Cowork custom-connector `Authorization` header drop (`#412`/`#56122`) | Closed not-planned | Avoided in P1/P2 (we don't use Cowork connectors yet). P3 Path A is exposed; P3 Path B sidesteps it. |
| Cowork OAuth loopback redirect (`#62801`) + `McpAuthorizationError` (`#196`) | Open | Day-1 spike in P3. Use public HTTPS callback, not loopback. |
| Plugin-bundled local MCP in Cowork sandbox (Path B crux) | **Unverified** | 1-day spike before P3 commitment. |
| Cloudflare/WAF blocks Anthropic egress | Common | Allowlist `160.79.104.0/21` on MCP + OAuth paths. |
| Cloud Run cold starts | Expected | `min-instances=1` (~$2–5/mo) if latency-sensitive; else accept sub-second. |
| Upstash free-tier exhaustion (500k cmd/mo) | At scale | Monitor; upgrade to paid (~$0.20/100k over) or move to DynamoDB (25GB/200M req free). |
| Composio as future commodity layer | Deferred | If we later want to retire commodity CRUD maintenance, Composio's 232 tools could replace our commodity tools while we keep only differentiated tools — but only via the SDK path, not the MCP URL. Revisit post-P3. |
| HubSpot rate limits (5 req/sec search, 100/10s burst) | Ongoing | Preserve the plugin's existing async rate-limited `httpx` client. Batch API v3 (100 records/call) for bulk ops. |

---

## 8. File / Module Structure (proposed for `hubspot-mcp` repo)

```
hubspot-mcp/
  pyproject.toml              # fastmcp, httpx, pydantic, cryptography (Fernet)
  src/hubspot_mcp/
    __main__.py               # entrypoint: --transport stdio|http, --mode token|oauth
    server.py                 # FastMCP app, tool registration, transport selection
    auth/
      base.py                 # TokenProvider interface
      env_provider.py         # P1/P2: EnvTokenProvider
      oauth_provider.py       # P3: OAuthTokenProvider (stub for now)
      bearer_middleware.py    # P2: static bearer check
    state/
      base.py                 # StateStore interface
      file_store.py           # P1: FileStateStore
      redis_store.py          # P2: RedisStateStore
    client.py                 # HubSpot httpx client (ported from plugin, token-injected)
    safety.py                 # preview/approve/undo state machine (ported)
    tools/                    # 75 tools (ported from plugin tools/, as @mcp.tool)
    agents/                   # 44 sub-agent definitions (moved to plugin package)
  plugin/
    .claude-plugin/plugin.json
    .mcp.json                 # declares hubspot_mcp stdio server
    agents/                   # 44 sub-agents
    skills/
    commands/                 # /hubspot
  Dockerfile                  # P2: Cloud Run image
  tests/                      # ported ~840 tests + transport/auth/state tests
  docs/architecture.md        # this file
```

---

## 9. Appendix: Key Research Findings (evidence for the decisions)

### 9.1 The existing plugin (`iiizzzyyy/hubspot-claude`)
- Claude Code **plugin** (not MCP), v0.1.4, Python 3.12, `httpx`+`pydantic`. 44 sub-agents, 18 tool modules / ~75 tools. Preview/approve/undo + audit safety model. OAuth or private-app token. Multi-portal. ~840 pytest tests. Single-author, MIT.
- **Cannot run in Cowork** — `SessionStart` hook provisions the venv; Cowork doesn't fire hooks (`anthropics/claude-code#40495`, `#47993`).

### 9.2 HubSpot MCP landscape (July 2026)
- **Official remote MCP** GA April 2026 (`mcp.hubspot.com`, OAuth 2.1+PKCE, ~12 generic tools). Gaps: no custom objects, Sensitive-Data mode blocks all engagements, no marketing writes, no Conversations, no multi-portal, no reviewable write plans.
- **Community servers:** shinzo-labs (~112 tools), axonops (76, custom objects + marketing + OAuthProxy), peakmojo (semantic search), mindstone (multi-account), phillipswdc (audit/rollback), Daeda (commercial, reviewable plans). 11+ total — saturated for commodity CRUD.
- **Composio:** 232 HubSpot tools, managed OAuth, brokered credentials (token never in LLM context). BUT `LOCAL_*` custom tools do **not** work via the MCP URL (SDK only); Cowork custom-connector path broken (`#412`/`#56122`); Composio's own `#3485` 401 bug; May 2026 security incident (ACE in tool-exec sandbox, 1 HubSpot connection compromised). Data intermediary.

### 9.3 Claude Cowork extension model
- Runs in hardened Linux VM sandbox inside Claude Desktop. Supports: **remote MCP connectors (OAuth 2.1+PKCE)** + **plugins (skills, slash commands, sub-agents, connectors)**. Does **not** support: local stdio MCP via user config, `SessionStart`/`PostToolUse` hooks.
- Sub-agents **can** use MCP connectors even when the main Routine runtime can't (`#43320` fixed, `#58353` fixed May 14 2026). Routines can't use custom connectors (`#52586` open).
- Static bearer tokens: **supported in Claude Code CLI** (`claude mcp add --header`, fixed v2.1.119), **not supported in Cowork** (OAuth-only).
- Anthropic MCP egress: `160.79.104.0/21` (IPv4), `2607:6bc0::/48` (IPv6).

### 9.4 Hosting (July 2026 free tiers)
- **Cloud Run:** 2M req/mo, 180k vCPU-sec, 360k GiB-sec free. Python Docker. Min-instances=1 ~$2–5/mo.
- **AWS Lambda + Function URLs:** 1M req/mo always-free; Function URLs = free HTTPS; SnapStart ~100–300ms Python cold start. Pair with DynamoDB (25GB/200M req free).
- **Modal:** $30/mo credits cover low traffic; best Python DX; sub-second cold starts.
- **Upstash Redis:** 500k commands/mo free, no daily cap — best for encrypted token + state storage.
- Avoid: Vercel Hobby (non-commercial), Render free (60s cold starts), Fly.io (no free tier new orgs), HF Spaces (ephemeral), Cloudflare Workers Python (10ms CPU), Deno Deploy (no Python), mcp.run (Wasm only), Glitch (dead).

### 9.5 OAuth bridge reference
- **`axonops/hubspot-mcp`** — Python FastMCP, dual `token`/`oauth` mode, `OAuthProxy` configured for HubSpot (authorize `https://app.hubspot.com/oauth/authorize`, token `https://api.hubapi.com/oauth/v3/token`, `forward_pkce=False`, `client_secret_basic`), `HubSpotTokenVerifier`, full `/.well-known/*` + `/authorize` + `/token` + `/register` + `/auth/callback`. In-memory token storage (swap for Upstash/DynamoDB). HubSpot `access_token` TTL 30 min (server-side refresh); `refresh_token` long-lived, one per install.
- **FastMCP `OAuthProxy`** — canonical building block: token-factory (HS256 JWT to client, JTI → Fernet-encrypted upstream token), dual-PKCE, confused-deputy mitigation.
- Effort: full OAuth bridge ~5–10 dev-days forking axonops; static-token stdio server ~1–2 dev-days.

---

## 10. Reviewer checklist

Reviewers, please focus on:
- [ ] **Section 5 (cross-phase principles).** Are the six interface boundaries correct? Anything that would force a rewrite in P3?
- [x] **D3 (FastMCP).** ~~Any reason to prefer a different framework?~~ Resolved by **D9** — migrated to the official `mcp` SDK for `2026-07-28` support.
- [ ] **D5 (orchestration in plugin, stateful tools for safety).** Is exposing the preview/approve state machine as MCP tools (`create_write_plan` / `approve_write_plan`) the right boundary, vs. some other pattern?
- [ ] **D7 (Cloud Run + Upstash).** Any org constraint (existing infra, AWS-only, data residency) that changes the host choice?
- [ ] **Section 6 Path B crux.** Should we de-risk the plugin-bundled-local-MCP-in-Cowork spike *now*, before P1, since it could make P3 unnecessary?
- [ ] **Section 8 structure.** Does the proposed module split match team conventions?