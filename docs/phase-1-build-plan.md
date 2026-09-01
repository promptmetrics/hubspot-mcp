---
title: "HubSpot MCP — Phase 1 Build Plan"
status: phase-1-shipped
audience: dev team
created: 2026-07-07
owner: Izzy
spec: docs/architecture.md
---

# HubSpot MCP — Phase 1 Build Plan

> **Status update (2026-09-01): Task 10 is resolved.** The open question below
> — how to port the 44 agents, given they are Python modules rather than Claude
> Code sub-agent markdown — was settled by reading them: they are prompt
> *builders*, each returning an `AgentPrompt(agent_name, system_prompt,
> tool_names, domain_description)`. They are now registered as 44 MCP **prompts**
> plus a `hubspot_route` tool, which is option (B) below and works for every MCP
> client rather than Claude Code only. The rest of this document is the original
> Phase 1 plan, kept as the record of how the build was scoped.


## Context

`docs/architecture.md` specifies a phased repackage of the `iiizzzyyy/hubspot-claude` Claude Code **plugin** into a standalone **MCP server**. This document is the **Phase 1 build plan**: the concrete task breakdown, file mapping, and verification for the local-stdio, static-token, Claude-Code-CLI server.

**Scope of this plan:** Phase 1 only. Phase 2 (Cloud Run + Redis + bearer) and Phase 3 (Cowork OAuth) are out of scope, per the spec and the team's scoping decision.

**Mode:** Plan only. This is a documentation deliverable for dev-team review — no code is being written in this run. The tasks below are the proposed execution sequence for when the team approves and lifts research-only.

**Inputs that grounded this plan:**
- `docs/architecture.md` — the approved architecture spec (decisions D1–D8, §3 Phase 1 architecture, §5 cross-phase principles, §8 module structure).
- A read-only inspection of `iiizzzyyy/hubspot-claude@HEAD` (commit `353a044`) — actual file tree, function signatures, tool registry, agent format, test layout, auth scheme. **This inspection corrected several assumptions in the spec; those corrections are flagged below and must be reconciled before build.**

---

## ⚠️ Critical finding — the "44 sub-agents" are not Claude Code sub-agents

The spec (D5, §3.2, §8) assumes the plugin's 44 specialist agents are **Claude-Code-native sub-agents** (markdown files under `.claude-plugin/agents/`) that can be "reused unchanged" in a Cowork-compatible plugin. **They are not.**

What the inspection found:
- `.claude-plugin/plugin.json` is **minimal** — `name`, `version`, `description`, `author`, `homepage`, `license`. **No `commands`, `agents`, or `skills` keys.** No `marketplace.json` listing of commands/agents/skills.
- There is **no `.claude-plugin/agents/` directory, no `skills/`, no `commands/`** in the plugin. The skill surface lives in `SKILL.md`; the `/hubspot` command lives in the Python `cli.py` + `bin/hubspot` console-script.
- The 44 "agents" are **Python modules** under `src/hubspot_agent/agents/`. Each defines `_TOOL_NAMES`, `_DOMAIN`, `_STOP_WORDS`, a `get_<name>_agent_prompt(portal_config) -> AgentPrompt` builder, and registers preview/execute/reconcile handlers via `dispatch.register_*`. `AgentPrompt` = `(agent_name, system_prompt, tool_names, domain_description)`. **No `model` field.** The system prompt is assembled at runtime by `build_agent_prompt` from domain + tool list + portal context + fixed `SELF_CORRECTION`/`RESEARCH`/`REFLECTION` blocks.

**Impact on the spec:**
- **D5's "orchestration stays in the plugin, 44 sub-agents reused unchanged" is not a trivial port.** Converting 44 Python prompt-builder modules into 44 Claude Code sub-agent markdown files is real work (each needs its prompt inlined or generated, tool list mapped to `mcp__hubspot__<tool>` names, and the runtime prompt-assembly logic either dropped or replicated).
- The plugin is currently a **skill + SessionStart hook + console-script**, not a sub-agent/skill/command plugin. The `SessionStart` hook (`hooks/install.sh`) provisions the venv — which is exactly what Cowork can't fire (`#40495`). The spec already knew this; the new information is that the agent layer is also Python-internal, not markdown.

**Recommended Phase 1 posture:** ship the MCP server with **tools + safety state machine only**, and **defer the 44-agent routing port** to a dedicated sub-phase (see Task 10). Phase 1 verification uses **direct tool calls** (`mcp__hubspot__hubspot_search_objects`, `mcp__hubspot__create_write_plan`, `mcp__hubspot__approve_write_plan`) rather than the `/hubspot` natural-language router. This delivers the spec's core value (the differentiated tool layer + safety model on MCP) without blocking on the agent-port design decision.

**The team must decide** which of these three approaches owns the 44-agent routing before Phase 1 (or a Phase 1.5) completes:
- **(A) Convert to Claude Code sub-agent markdown.** Most faithful to D5; largest effort; makes the layer Cowork-compatible by construction.
- **(B) Keep routing server-side as a `hubspot_route` MCP tool.** Smallest effort; contradicts D5 (orchestration in the server); but the routing logic already exists in Python and would run unchanged.
- **(C) Defer entirely.** Ship tools+safety now; users compose tool calls directly or via their own sub-agents. Fastest; loses the specialist-routing UX until later.

This plan proceeds under **(C) for Phase 1**, with (A)/(B) as an explicit follow-on decision.

---

## Auth decision — OAuth (bring-your-own-app) for Phase 1

The spec left the Phase 1 auth mode contingent on verifying whether HubSpot's OAuth token endpoints accept **secretless PKCE** (no `client_secret`) — the shape a public CLI MCP client would need to avoid embedding a secret. An empirical PKCE spike (throwaway, `/tmp/hubspot_pkce_spike.py`) tested both endpoints against a real HubSpot app (EU data center, 158 configured scopes, PKCE S256, `client_secret` intentionally omitted):

| Endpoint | Exchange request (no secret) | Response | Verdict |
|---|---|---|---|
| `https://api.hubapi.com/oauth/2026-03/token` (new date-based) | `grant_type=authorization_code` + `client_id` + `code` + `redirect_uri` + `code_verifier` | `HTTP 400` — `BAD_CLIENT_SECRET` / `invalid_client` / "missing or invalid client secret" | `client_secret` required. |
| `https://api.hubapi.com/oauth/v3/token` (legacy) | same | `HTTP 400` — `BAD_CLIENT_SECRET` / `invalid_client` / "missing or invalid client secret" | `client_secret` required. |

**What the spike proved and didn't prove:** it proved `client_secret` is **mandatory** on every token exchange, on both the 2026-03 and legacy v3 endpoints — PKCE is a supplement, not a secret replacement, and there is **no secretless OAuth path**. It did **not** prove OAuth is impossible locally; the only thing the exchange rejected was the missing secret. Adding the user-supplied `client_secret` to the exchange is the fix.

**Why this is safe for a distributed local binary:** the original "don't leak a secret" concern was about embedding **one shared** `client_secret` in a binary we ship to everyone. **Bring-your-own-app** eliminates that: we ship **no secret**. Each user creates their own HubSpot developer app and supplies their own `client_id` + `client_secret` via env/config; their secret lives only on their own machine (like a PAT would), never in our code or binary.

**Decision (locked): Phase 1 defaults to OAuth via bring-your-own-app.** The flow runs entirely locally against the user's own app:

1. **One-time setup (user):** create a HubSpot developer app → in its Auth tab whitelist `http://localhost:3000/oauth/callback` → note the app's configured scope set → set `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`, and `HUBSPOT_SCOPES` (the app's exact configured scope list, space-separated) via env or `~/.claude/hubspot/<portal_id>.json`.
2. **First run (server):** `OAuthProvider` opens the browser to the **region-correct** authorize host (`app-eu1.hubspot.com` for EU apps, `app.hubspot.com` for US — derived from the app/portal region, not hardcoded) with PKCE S256 + the app's exact scope set; receives the code at `localhost:3000/oauth/callback`; exchanges `code + code_verifier + client_secret` for access + refresh tokens; stores them at `~/.claude/hubspot/<portal>/` (PKCE state under `oauth_states/`).
3. **Steady state:** `HubSpotClient` already does `get_valid_token` (refresh on expiry, 401 refresh-retry, 100/10s rate limit) — reused unchanged. Tokens are short-lived and refreshable; the user can revoke via their HubSpot app at any time.

**PAT is the optional fallback mode.** `--mode token|oauth` (principle 5) ships both from day 1: `oauth` (bring-your-own-app) is the default; `token` (`EnvTokenProvider` over a private-app token via `HUBSPOT_TOKEN_<portal_id>`) is for users who don't want to create a dev app. Both resolve to a `PortalConfig` and feed the same `HubSpotClient` — no tool-body branching.

**Reuse, don't rewrite:** the plugin already implements the entire OAuth flow in `auth.py` (`get_authorization_url` S256, `exchange_code_for_token`, `refresh_access_token`, `get_valid_token`, redirect `http://localhost:3000/oauth/callback`, `oauth_states/`) and holds app creds in `app_credentials.py`. `PortalConfig` already carries `auth_type`, `refresh_token`, `expires_at`. Phase 1 wires this into FastMCP context via a thin `OAuthProvider` adapter; it does **not** reimplement the flow.

**Two fixes the spike surfaced (must apply during the port):**
- **Region-aware authorize host.** The spike had to use `app-eu1.hubspot.com` (EU), not `app.hubspot.com`. If the plugin hardcodes `app.hubspot.com`, it must be made region-aware (detect from the app/portal, or accept `HUBSPOT_AUTHORIZE_URL` env) — otherwise EU users hit a broken authorize.
- **Exact scope-set matching.** HubSpot rejects any mismatch between the install URL `scope=` and the app's configured scopes. The authorize URL must send the user's app scope set verbatim (from `HUBSPOT_SCOPES` / config), not a curated default. The plugin's `scope_registry.py` / `HUBSPOT_SCOPES_<portal>` likely already supports this — confirm during Task 1.

**Confirming spike — PASSED 2026-07-07 (decision locked):** re-ran `/tmp/hubspot_pkce_spike.py` with `HUBSPOT_CLIENT_SECRET` set (user-supplied env, never read from disk by us) against **both** token endpoints. Each returned `HTTP 200`, `token_type=bearer`, `expires_in=1800` (30 min), `access_token` (747 chars) + `refresh_token` (36 chars). **PKCE + `client_secret` is confirmed viable on both the 2026-03 and legacy v3 token endpoints** — bring-your-own-app OAuth is locked as the Phase 1 default, PAT as fallback.

**Token endpoint for Phase 1 OAuth = 2026-03 date-based** (`https://api.hubapi.com/oauth/2026-03/token`) — the latest API, confirmed working with PKCE + `client_secret` on 2026-07-07. This puts the OAuth *exchange* on the same date-based versioning scheme as the CRM resource calls (`/<api>/2026-03/<resource>`), so the whole server is on the latest API. The legacy v3 endpoint (`https://api.hubapi.com/oauth/v3/token`) is the **configured fallback** — also confirmed working; the `OAuthProvider` should fall back to it if the 2026-03 token endpoint ever returns a 404/absence error (same 404-fallback pattern the spike used). The issued bearer token is valid for all HubSpot API calls regardless of which token endpoint issued it.

**Build impact:** Task 4 wires **both** providers: `EnvTokenProvider` (PAT fallback) and `OAuthProvider` (bring-your-own-app default) over the ported `auth.py` + `app_credentials.py` + `config.py`. The `--mode` flag selects which. Task 9 verification exercises the OAuth flow end-to-end (consent → exchange → authenticated call → refresh) plus the PAT fallback. Region-aware authorize + exact-scope-matching are added as explicit Task 1/4 sub-items.

**Open verification #2 (scope reach):** confirm the user's OAuth app (and the PAT fallback) can be granted the scopes needed for the differentiated endpoints (Conversations, audit/security history, sequences). With bring-your-own-app the user controls their app's scopes, so this is a documentation/setup-guidance matter, not a platform limit — verify during Task 1 with a max-scope app.

---

## Spec corrections from the inspection

These are facts the build must respect; where they differ from `architecture.md`, the spec should be amended in review.

| Spec claim | Actual (from inspection) | Action |
|---|---|---|
| "~75 tools" | **76** `@tool` functions across 18 modules | Use 76 in verification. |
| `TokenProvider` reads `HUBSPOT_PRIVATE_APP_TOKEN` | Plugin already has `config.load_portal_config` resolving `HUBSPOT_TOKEN_<portal_id>` (+ `HUBSPOT_TIER_`, `HUBSPOT_SCOPES_`), a `<cwd>/.hubspot-portal` selector, and `~/.claude/hubspot/<portal_id>.json` config files. `PortalConfig` dataclass carries `token`, `auth_type`, `refresh_token`, `expires_at`. | **Reuse `config.py` + `PortalConfig` as the TokenProvider impl.** Do not invent a new env-var scheme. `EnvTokenProvider` wraps `config.load_portal_config`. |
| `HubSpotClient` "takes a token + portal_id" | `HubSpotClient.__init__(portal: PortalConfig)` — takes a **`PortalConfig`**, not a bare token. Already has OAuth refresh, rate limiting (`100/10s`), 401 refresh-retry, error categorization. | Reuse `client.py` unchanged. The "thin interface taking token + portal_id" (principle 6) is satisfied by `PortalConfig`. |
| Safety model in `safety.py` | Split across `safety.py` (gate: `apply_write`), `persistence.py` (`pending_previews/`), `snapshot.py` (`undo_snapshots/`), `audit.py` (`audit.log`), `handlers.py` (`execute_pending_write`). State already lives at `~/.claude/hubspot/<portal_id>/`. | **Reuse all five modules as the FileStateStore backend.** The `StateStore` interface (principle 3) is a thin adapter over them, not a rewrite. |
| Plugin has `agents/`, `skills/`, `commands/` to reuse | Plugin has none of these as plugin assets (see critical finding above). | Address in Task 10 / spec amendment. |
| `.mcp.json` exists or is simple to mirror | **No `.mcp.json` in the repo.** | Create from scratch (Task 8). |
| ~840 pytest tests | **101 `test_*.py` files** + `conftest.py` + `routing_corpus.yaml`. Per-file `def test_` count not summed, but ~840 across 101 files is plausible. | Port the test suite verbatim; add FastMCP transport/auth/state tests. Confirm the real count during Task 1. |
| `pyproject.toml` deps include fastmcp | Deps are **only `httpx` + `pydantic`** (dev: `pytest`, `pytest-asyncio`, `pytest-httpx`, `respx`, `hypothesis`, `pyyaml`). **No `fastmcp`/`mcp`.** | Add `fastmcp` (and `cryptography` for P2 Fernet, optional in P1) to the new `hubspot-mcp` pyproject. |

---

## Porting strategy: reuse, don't rewrite

The plugin already has a clean internal architecture that maps almost 1:1 onto the spec's Phase 1 server. The build is a **FastMCP wrapper + adapter layer** over existing, tested code — not a reimplementation.

What we reuse verbatim (copy into `src/hubspot_mcp/`):
- `client.py`, `config.py`, `auth.py`, `app_credentials.py`, `errors.py`, `models.py`, `cache.py`, `redaction.py`, `scope_registry.py`, `validation.py` — HubSpot client + portal/auth/config layer.
- `safety.py`, `persistence.py`, `snapshot.py`, `audit.py`, `handlers.py` — preview/approve/undo/audit state machine (the `FileStateStore` backend).
- `tools/` (18 modules + `__init__.py` with the `@tool` registry and `invoke_tool`) — the 76 tools.
- `tests/` — the 101 test files, adjusted for the new package path.

What we write new:
- `server.py`, `__main__.py` — FastMCP app, `--transport stdio|http`, `--mode token|oauth` entrypoint (principle 5: mirror axonops dual-mode).
- `auth/base.py` (`TokenProvider` interface), `auth/env_provider.py` (`EnvTokenProvider` wrapping `config.load_portal_config` — PAT fallback), `auth/oauth_provider.py` (`OAuthProvider` — thin adapter over the ported `auth.py` + `app_credentials.py`; bring-your-own-app default), `auth/bearer_middleware.py` (P2 stub).
- `state/base.py` (`StateStore` interface), `state/file_store.py` (`FileStateStore` adapter over `persistence`/`snapshot`/`audit`).
- A **tool-registration layer** that iterates the `@tool` registry and emits one `@mcp.tool` per entry, resolving `HubSpotClient` + `portal_id` from FastMCP context and delegating to `invoke_tool`.
- **Safety-stateful tools**: `create_write_plan`, `approve_write_plan`, `reject_write_plan`, `list_pending_writes`, `undo_write` (if present), `list_recent_audit` — wrapping `handlers.execute_pending_write` et al.
- `.mcp.json`, `plugin.json`, `Dockerfile` (P2, stub), new transport/auth/state tests.

---

## Module mapping (plugin → server)

| `hubspot_agent/` source | `hubspot_mcp/` destination | Treatment |
|---|---|---|
| `client.py` | `client.py` | Copy verbatim. |
| `config.py`, `auth.py`, `app_credentials.py`, `errors.py`, `models.py`, `cache.py`, `redaction.py`, `scope_registry.py`, `validation.py` | `core/` (or flat) | Copy verbatim. |
| `safety.py`, `persistence.py`, `snapshot.py`, `audit.py`, `handlers.py` | `safety/` + `state/file_store.py` adapter | Copy verbatim; wrap behind `StateStore`. |
| `tools/__init__.py` + 18 modules | `tools/` | Copy verbatim. |
| (`tools/` registry → `@mcp.tool`) | `server.py` registration layer | New glue code. |
| (safety state machine → stateful tools) | `tools/safety_tools.py` | New thin wrappers over `handlers`. |
| — | `auth/base.py`, `auth/env_provider.py`, `auth/oauth_provider.py`, `auth/bearer_middleware.py` | New (P1: interface + PAT env impl + OAuth bring-your-own-app impl; bearer stub). |
| — | `state/base.py`, `state/file_store.py`, `state/redis_store.py` | New (P1: interface + file impl; redis stub). |
| — | `__main__.py`, `server.py` | New. |
| `agents/` (44 Python modules) | **deferred** (Task 10) | Not ported in Phase 1 under approach (C). |
| `cli.py`, `router.py`, `daemon.py`, `orchestrator.py`, `dispatch.py`, `loop_*`, `routing.py`, `planning.py`, `research.py`, `trace.py`, `progress.py`, `checkpoint.py`, `maintenance.py`, `setup.py`, `testing.py`, `capabilities.py`, `ledger.py`, `sequential_dispatch.py`, `agent_dispatch.py` | **not needed for P1** | CLI/daemon/orchestration/loop machinery is replaced by FastMCP + the plugin layer (later). Drop for now. |
| `tests/` | `tests/` | Copy; fix import paths. |

---

## Task breakdown (ordered)

Effort labels are rough dev-day fractions against the spec's ~1–2 dev-day Phase 1 budget **for the tools+safety scope** (approach C). The agent port (Task 10) is additional and unbudgeted.

| # | Task | Depends on | Effort | Notes |
|---|---|---|---|---|
| 0 | **Repo setup.** Clone `iiizzzyyy/hubspot-claude` as a read-only reference copy (e.g. `reference/hubspot-claude/`). Init `hubspot-mcp` pyproject: `fastmcp`, `httpx`, `pydantic`, `cryptography` (P2), dev deps from source. Create package layout per spec §8. `.gitignore` (never commit `~/.claude/hubspot/`, `.hubspot-portal`, token files). | — | 0.25d | Cloning is the first build-time action; **not done in this plan-only run.** |
| 1 | **Port core client + config.** Copy `client.py` + the 9 support modules. Confirm `HubSpotClient(PortalConfig)` imports clean and `config.load_portal_config` resolves env/config. Port their unit tests. Confirm real test count. | 0 | 0.25d | Reuse verbatim; fix import paths only. |
| 2 | **Port safety/state layer.** Copy `safety.py`, `persistence.py`, `snapshot.py`, `audit.py`, `handlers.py`. Run `test_safety_apply_write`, `test_audit_undo`, `test_destructive_gate`, `test_undo`, `test_snapshot`, `test_preview`, `test_persistence_concurrency`, `test_handlers`. | 1 | 0.25d | This is the `FileStateStore` backend. |
| 3 | **Port the 76 tools.** Copy `tools/` (18 modules + `__init__.py`). Verify `invoke_tool` dispatches all 76 via `test_tool_registry_populated`. Port per-tool tests. | 1 | 0.25d | Mechanical copy. |
| 4 | **FastMCP server skeleton + auth wiring.** `server.py` + `__main__.py` with `--transport stdio|http`, `--mode token\|oauth` (default `oauth`). `TokenProvider` interface + **two impls**: `EnvTokenProvider` (PAT fallback, wraps `config.load_portal_config`) and `OAuthProvider` (bring-your-own-app default, thin adapter over ported `auth.py` + `app_credentials.py`). OAuth exchange uses the **2026-03 token endpoint** (`https://api.hubapi.com/oauth/2026-03/token`, confirmed with PKCE + secret) with **legacy v3 as 404-fallback** (also confirmed). FastMCP context dependency resolves `PortalConfig` + builds `HubSpotClient` per request (or pools by portal). **Apply the two spike fixes here:** region-aware authorize host (`app-eu1.hubspot.com` vs `app.hubspot.com`, from app/portal region or `HUBSPOT_AUTHORIZE_URL` env) and exact-scope-set matching (send the user's `HUBSPOT_SCOPES` verbatim, not a curated default). | 1 | 0.35d | Principles 1, 2, 5 honored. Confirming spike PASSED 2026-07-07 on both endpoints — unblocked. |
| 5 | **Expose 76 tools as `@mcp.tool`.** Registration layer iterates the `@tool` registry; each `@mcp.tool` resolves client+portal from context and calls `invoke_tool(name, portal_id, **kwargs)`. Preserve tool `name` + `description` as the MCP tool name/description. | 3, 4 | 0.25d | The mechanical port. Add transport tests. |
| 6 | **Expose safety state machine as stateful `@mcp.tool`.** `create_write_plan` (write path → `safety.apply_write`), `approve_write_plan` (`handlers.execute_pending_write`), `reject_write_plan`, `list_pending_writes`, `list_recent_audit`, and `undo_write` if the source exposes an undo handler. | 2, 4, 5 | 0.25d | D5's "stateful tools for safety" boundary. |
| 7 | **`StateStore` interface + `FileStateStore` adapter.** Thin interface over `persistence`/`snapshot`/`audit` so P2 `RedisStateStore` swaps in without touching tool bodies. | 2 | 0.1d | Principle 3. |
| 8 | **`.mcp.json` + minimal plugin manifest.** `.mcp.json` declares `python -m hubspot_mcp --transport stdio`. Minimal `plugin.json` (mirror source). No agents/skills/commands yet. | 4 | 0.1d | |
| 9 | **Verification (spec §3.5).** `pip install -e .` → `python -m hubspot_mcp --transport stdio` responds to `tools/list` with 76 tools + safety tools. `claude mcp add hubspot --transport stdio -- python -m hubspot_mcp --transport stdio`. `/mcp` lists tools. **Auth verification (default `oauth`):** first run opens browser to the region-correct authorize URL → consent → `localhost:3000` callback → exchange `code+verifier+client_secret` → `access_token`+`refresh_token` stored at `~/.claude/hubspot/<portal>/` → one authenticated tool call succeeds → force a refresh (expire token) and confirm `get_valid_token` recovers. **PAT fallback (`--mode token`):** set `HUBSPOT_TOKEN_<portal_id>`, repeat one tool call. **Safety end-to-end (direct tool calls):** `hubspot_search_objects` → `hubspot_create_write_plan` (preview + `action_id`) → `hubspot_approve_write_plan` (execute) → confirm undo snapshot + audit entry at `~/.claude/hubspot/<portal>/`. `pytest -x` green. | 5, 6, 7, 8 | 0.3d | Direct-tool verification (approach C). |
| 10 | **(Deferred, decision required) 44-agent routing port.** Choose approach (A) markdown conversion / (B) server-side `hubspot_route` tool / (C) defer. Not in the Phase 1 budget. | 9 | +0.5–2d | See critical finding. |

**Estimated Phase 1 effort (approach C, tasks 0–9): ~1.65 dev-days** (auth wiring grew ~0.15d for the OAuth provider + two spike fixes), still within the spec's ~1–2 dev-day target. Task 10 is additive.

---

## How the six cross-phase principles are honored in Phase 1

1. **Transport-agnostic tools** — `@mcp.tool` wrappers contain no `stdin`/`stdout` access; `--transport stdio|http` is an `__main__.py` flag.
2. **Token via context, not deep env reads** — the active `TokenProvider` (`OAuthProvider` by default, `EnvTokenProvider` for the PAT fallback) resolves `PortalConfig` per request from FastMCP context; tool bodies receive a `HubSpotClient`, never `os.environ`. The `client_secret` lives in local config/env supplied by the user, never in code.
3. **`StateStore` interface** — `FileStateStore` adapter over the ported `persistence`/`snapshot`/`audit`; P2 swaps in `RedisStateStore`.
4. **Orchestration in the plugin, server exposes tools only** — Phase 1 ships tools-only (approach C). The agent-routing port (Task 10) is the place where D5 is realized; until then the server is purely tools + stateful safety tools.
5. **Mirror axonops dual-mode** — `__main__.py` exposes `--mode token|oauth` and `--transport stdio|http` from day 1. Phase 1 ships both modes live: `oauth` (bring-your-own-app, default) + `stdio`, with `token` (PAT) as the fallback.
6. **HubSpot client behind a thin interface** — `HubSpotClient(PortalConfig)` is that interface; `PortalConfig` carries token + portal_id + auth_type + refresh_token + expires_at, so the same client serves a static PAT and an OAuth-derived (refreshable) token identically. P1 exercises both; P2/P3 swap in a hosted-confidential-client OAuth provider behind the same interface.

---

## Verification (end-to-end, Phase 1)

1. `pip install -e .` then `python -m hubspot_mcp --transport stdio` — server starts, `tools/list` returns 76 HubSpot tools + the safety-stateful tools.
2. `claude mcp add hubspot --transport stdio -- python -m hubspot_mcp --transport stdio` (or via the plugin's `.mcp.json`).
3. `/mcp` lists `hubspot` and the tools.
4. **Direct-tool end-to-end (approach C):**
   - `mcp__hubspot__hubspot_search_objects` (objectType=companies, query="Acme") → returns matches.
   - `mcp__hubspot__hubspot_create_write_plan` (merge duplicates) → preview + `action_id` (no mutation yet).
   - `mcp__hubspot__hubspot_approve_write_plan` (action_id, expected_count=3) → merge executed; destructive-count gate re-checked.
   - Confirm `~/.claude/hubspot/<portal>/undo_snapshots/<action_id>.json` + one `audit.log` line written.
5. `pytest -x` — ported tests + new transport/auth/state tests green.

**Prerequisite (default `oauth` mode):** a HubSpot developer app you own, with `http://localhost:3000/oauth/callback` whitelisted in its Auth tab, and `HUBSPOT_CLIENT_ID` + `HUBSPOT_CLIENT_SECRET` + `HUBSPOT_SCOPES` (your app's exact configured scope list) set via env or `~/.claude/hubspot/<portal_id>.json`, plus the `<cwd>/.hubspot-portal` selector. **PAT fallback (`--mode token`):** `HUBSPOT_TOKEN_<portal_id>` instead. Never commit tokens, `client_secret`s, or `~/.claude/hubspot/` contents.

---

## Open questions for the dev team

1. **44-agent routing (Task 10):** approach (A), (B), or (C)? This is the single biggest decision left by this plan and the largest divergence from the spec as written.
2. **Spec amendment:** should `architecture.md` be updated to reflect (a) the corrections table above (76 tools, `PortalConfig`-based client, env-var scheme, agents-are-Python, no `.mcp.json`, no plugin agents/skills/commands) and (b) the **auth reversal** — spec §3's "local-stdio static-token" stance is now OAuth (bring-your-own-app) as default with PAT as fallback, per the empirically-verified auth decision above? Recommend yes before build.
3. **`min-instances` / P2 readiness:** P1 code should not hardcode file-state paths in tool bodies (use `StateStore`) so P2's `RedisStateStore` swap is clean. Confirm reviewers check this in PRs.
4. **Test count:** confirm the real `def test_` total during Task 1 (spec says ~840; 101 files verified).
5. **Dropping the daemon/CLI/loop modules** (`cli.py`, `router.py`, `daemon.py`, `orchestrator.py`, `loop_*`, `routing.py`, `planning.py`, `research.py`, `trace.py`, `progress.py`, `checkpoint.py`, `maintenance.py`, `setup.py`, `testing.py`, `capabilities.py`, `ledger.py`, `sequential_dispatch.py`, `agent_dispatch.py`) — these are not needed for a tools-only P1 server. Confirm the team is OK not porting them now (they'd be revisited if we keep routing server-side, approach B).
6. **OAuth onboarding UX:** bring-your-own-app requires each user to create a HubSpot dev app, whitelist the redirect URI, and supply `client_id` + `client_secret` + the app's exact scope list. Confirm the team accepts this setup cost as the Phase 1 default, vs. shipping PAT-first with OAuth as an opt-in mode. (Decision in this plan: OAuth default, PAT fallback — but the onboarding doc/guidance is a real deliverable for Task 8/9.)
7. **Confirming spike — RESOLVED (PASSED 2026-07-07):** `/tmp/hubspot_pkce_spike.py` with `HUBSPOT_CLIENT_SECRET` set returned `HTTP 200` + `access_token` + `refresh_token` against **both** the 2026-03 and legacy v3 token endpoints. `code + code_verifier + client_secret` is confirmed viable on both; **2026-03 is locked as the Phase 1 token endpoint**, v3 as fallback. Task 4 is unblocked.

---

## Out of scope for this plan

- Phase 2 (Cloud Run, `BearerAuth`, `RedisStateStore`, Docker, `/healthz`).
- Phase 3 (Cowork, `OAuthProxy`, `/.well-known/*`, OAuth token store, Path A/B spikes).
- The 44-agent routing port (Task 10 — deferred, decision required).
- Any actual code execution. This is a plan for review; research-only remains in effect until the team approves and explicitly lifts it.