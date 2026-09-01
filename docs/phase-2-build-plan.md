---
title: "HubSpot MCP — Phase 2 Build Plan"
status: draft-for-review
audience: dev team
created: 2026-09-01
owner: Izzy
spec: docs/architecture.md §4
---

# HubSpot MCP — Phase 2 Build Plan

Phase 1 shipped at **v0.2.0**: 79 domain tools, 7 safety/introspection tools, 44
charters as MCP prompts, on protocol `2026-07-28`, with 628 tests behind CI.
Everything runs locally over stdio, one process per user.

Phase 2 makes the same server reachable over HTTPS.

`docs/architecture.md` §4 already specifies Phase 2. This plan **corrects it in
four places** before executing it — the spec was written on 2026-07-07, before
the protocol migration and before the safety features existed, and its central
claim ("only three things change") is no longer true.

---

## 1. Spec corrections

| §4 says | Reality | Consequence |
|---|---|---|
| "**StateStore** was an interface in P1 … `RedisStateStore` is what's added" | The interface exists and `FileStateStore` implements all 11 methods, but **nothing outside `state/` references it**. `handlers.py` (18 call sites), `snapshot.py` (5), `server.py` (3) and `safety.py` (2) import the module-level functions directly. | Redis is not a drop-in. The real work is routing ~28 call sites through the interface first. This is the single largest task and it is not in the spec's estimate. |
| "**BearerAuth middleware** — the only new code of substance" | `2026-07-28` removed the `initialize` handshake, so `Middleware.on_initialize` never runs. Auth must verify **per request**. | Anything connection-scoped would authorise every later request on that connection for free. Use the SDK's `token_verifier`, which runs per request by construction. |
| "**Cloud Run + Upstash**" (D7) | We are already paying for a Vercel team account; Vercel supports FastAPI as a first-class backend framework with `maxDuration` to 1800s and Fluid compute keeping instances warm. | Supersede D7 with **D11 (Vercel)**. The deciding factor is operational, not technical — see §2. |
| "Everything else is unchanged" | Three things added after the spec assume **one portal per process**, and one mutates global server state. | Phase 2 stays single-tenant, and that boundary now has to be stated and enforced rather than assumed. See §3. |

---

## 2. D11 — Hosting: Vercel, superseding D7 (Cloud Run)

- **Rationale.** The state migration in §4 is required on *any* multi-instance
  host, so "a container keeps things warm between requests" — D7's main
  technical argument — mostly evaporates once pending previews, snapshots,
  audit and caches are external. What remains is operational: we already pay
  for a Vercel team account, so marginal cost is ~zero, while Cloud Run adds a
  GCP project, a second billing relationship, separate IAM, separate secrets
  management, a separate deploy pipeline, and a second place to look during an
  incident. Vercel also gives a preview deployment per PR, which is worth more
  now that `main` is protected and every change goes through one.
- **Verified before deciding:** FastAPI is a supported Vercel backend framework;
  `maxDuration` is configurable to 1800s (MCP calls are seconds); Fluid compute
  preserves the warm `httpx` client pool across requests.
- **Rejected:** Cloud Run (a free tier we do not need, at the cost of a second
  platform to operate); Cloudflare Workers Python (CPU ceiling too tight for
  `httpx` + `pydantic`); Vercel Hobby (non-commercial).
- **Still open:** the docs-index build makes ~40 outbound fetches in 5.5s on a
  cold cache. Confirm that is acceptable on Vercel's egress model, and keep the
  index in Redis so it is built once per deployment rather than once per
  instance (§4, Task 4).

---

## 3. The single-tenant boundary, and why it must be enforced

Phase 1 resolves exactly one portal per process — from `--portal`,
`HUBSPOT_PORTAL`, or a `.hubspot-portal` file — and three things now depend on
that:

1. `app_lifespan` builds **one** `HubSpotClient` and probes **one** capability
   matrix.
2. `_unadvertise_unavailable_tools` calls `mcp.remove_tool(...)`, mutating the
   **module-level server object**.
3. `client._LAST_RATE_STATE` is a module-level per-portal rate snapshot.

(1) and (3) are merely wrong-ish when shared. **(2) is a correctness bug the
moment one deployment serves two portals:** portal A lacking Workflows would
unadvertise the workflow tools for portal B on the same instance, permanently,
until the instance recycles.

The spec's Phase 2 is single-tenant (one HubSpot PAT in the server env), so this
is *in scope to enforce, not to fix*: the server must refuse to start multi-tenant
rather than silently mis-serve. Multi-tenancy is Phase 3, where per-user OAuth
makes the portal a per-request property — at which point (1)–(3) all become
request-scoped, and capability gating must move from `remove_tool` to a
per-request `tools/list` filter.

---

## 4. Tasks

| # | Task | Depends on | Est. |
|---|---|---|---|
| 1 | **Route persistence through `StateStore`.** Introduce a module-level `get_store()` returning the configured implementation; convert the ~28 direct call sites in `handlers.py`, `snapshot.py`, `safety.py`, `server.py`. Behaviour-identical — `FileStateStore` stays the default and the 628 tests must pass untouched. | — | 0.75d |
| 2 | **`RedisStateStore`.** Implement all 11 methods against Upstash (or Vercel's Redis integration). Pending previews and snapshots are Fernet-encrypted blobs keyed by `portal_id:action_id`; audit is an append-only list. Port `FileStateStore`'s tests against it via a shared conformance suite so both implementations are held to one contract. | 1 | 0.75d |
| 3 | **Per-request bearer auth.** `SERVER_SECRET` from env, constant-time compare, wired through the SDK's `token_verifier` rather than middleware — there is no handshake to authenticate in. `auth/bearer_middleware.py` already documents this; replace the stub. | — | 0.25d |
| 4 | **Move the remaining local-disk state.** Schema cache, capability cache and docs index are per-instance today; on Vercel each cold instance rebuilds them (the docs index costs ~40 fetches). Move to Redis with the same TTLs. | 1, 2 | 0.35d |
| 5 | **Single-tenant guard.** Refuse to start when the deployment cannot resolve exactly one portal; add a startup assertion and a test. Document the boundary in the README. | — | 0.15d |
| 6 | **Vercel deployment.** `vercel.json` with the FastAPI entrypoint and `maxDuration`; `streamable_http_app()` mounted; env vars for `SERVER_SECRET`, HubSpot credentials, Redis URL. `/healthz`. Preview deployment per PR. | 2, 3 | 0.4d |
| 7 | **Ops documentation.** Update `docs/architecture.md` §4 and D7→D11; document the WAF gotcha — if anything sits in front of the endpoint, allowlist Anthropic's MCP egress `160.79.104.0/21` on `/mcp` and `/healthz`, since WAF bot-blocking is the most common "cannot reach MCP server" cause. | 6 | 0.2d |

**Estimated: ~2.85 dev-days**, against the spec's ~2–3. The estimate holds only
because Task 1 is bounded mechanical work; if the call sites resist a clean
`get_store()` seam, it grows.

---

## 5. What Phase 2 still does NOT include

- **No OAuth, no per-user tokens, no multi-tenancy.** One portal, one shared
  `SERVER_SECRET`. Cowork rejects static bearer, so Cowork remains Phase 3.
- **No change to the tool layer, safety model, prompts or plugin.** If a task
  requires touching `tools/`, that is a signal the seam is in the wrong place.
- **No stdio regression.** The local plugin path stays the default and must keep
  passing the full suite; Vercel is an additional deployment target, not a
  replacement.

---

## 6. Verification

1. `pytest` green with `FileStateStore` (628 tests, unchanged) **and** green
   against `RedisStateStore` via the shared conformance suite.
2. `curl https://<url>/healthz` → 200; `curl https://<url>/mcp` with no bearer →
   401; with a wrong bearer → 401.
3. `claude mcp add hubspot --transport http https://<url>/mcp --header "Authorization: Bearer <SERVER_SECRET>"`,
   then `/mcp` lists 86 tools and 44 prompts.
4. **The one that proves Phase 2 works:** mint a write preview, force the
   instance to recycle, then approve the same `action_id` from a cold instance.
   This is the same statelessness property `test_protocol_conformance` pins
   locally, now across real infrastructure.
5. Undo and the audit log survive the same recycle.
6. A capability-gated tool behaves identically to local: unadvertised when the
   probe is conclusive, refused with an explanation when it is not.
7. Confirm Anthropic egress reaches the service in the logs; if 403s, look for a
   WAF before debugging the server.

---

## 7. Open questions

1. **Redis provider** — Upstash via Vercel's marketplace integration, or Vercel's
   own Redis? Upstash was chosen in D7 for the free tier; on a paid team the
   integration story may matter more than the tier.
2. **Token storage.** Phase 1 keeps the HubSpot refresh token in a 0600 file. In
   Phase 2 single-tenant it can be an env var, but that puts a long-lived
   refresh token in the deployment config. Encrypted in Redis is better hygiene
   and is on the Phase 3 path anyway — decide now or knowingly defer.
3. **Does Phase 2 earn its place?** Its value is burning down infrastructure risk
   *before* OAuth risk (spec §4.5). If Cowork is not actually near-term, the
   honest alternative is to stay on stdio and skip to Phase 3 when it is.
