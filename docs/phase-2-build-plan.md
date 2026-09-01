---
title: "HubSpot MCP — Phase 2 Build Plan"
status: closed — tasks 1–5 shipped, 6–7 dropped (D12)
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
| 1 | ✅ **Done.** **Route persistence through `StateStore`.** Introduce a module-level `get_store()` returning the configured implementation; convert the ~28 direct call sites in `handlers.py`, `snapshot.py`, `safety.py`, `server.py`. Behaviour-identical — `FileStateStore` stays the default and the 628 tests must pass untouched. | — | 0.75d |
| 2 | ✅ **Done** (as 2a + 2b). **`RedisStateStore`.** Implement all 11 methods against Upstash (or Vercel's Redis integration). Pending previews and snapshots are Fernet-encrypted blobs keyed by `portal_id:action_id`; audit is an append-only list. Port `FileStateStore`'s tests against it via a shared conformance suite so both implementations are held to one contract. | 1 | 0.75d |
| 3 | ✅ **Done** (with a correction, below). **Per-request bearer auth.** `SERVER_SECRET` from env, constant-time compare, wired through the SDK's `token_verifier` rather than middleware — there is no handshake to authenticate in. `auth/bearer_middleware.py` already documents this; replace the stub. | — | 0.25d |
| 4 | ✅ **Done** (2 of 3 caches — see below). **Move the remaining local-disk state.** Schema cache, capability cache and docs index are per-instance today; on Vercel each cold instance rebuilds them (the docs index costs ~40 fetches). Move to Redis with the same TTLs. | 1, 2 | 0.35d |
| 5 | ✅ **Done.** **Single-tenant guard.** Refuse to start when the deployment cannot resolve exactly one portal; add a startup assertion and a test. Document the boundary in the README. | — | 0.15d |
| 6 | ❌ **Dropped (D12).** **Vercel deployment.** `vercel.json` with the FastAPI entrypoint and `maxDuration`; `streamable_http_app()` mounted; env vars for `HUBSPOT_MCP_SERVER_SECRET`, HubSpot credentials, Redis URL. (`/healthz` landed with Task 3.) Preview deployment per PR. | 2, 3 | 0.4d |
| 7 | ❌ **Dropped (D12).** **Ops documentation.** Update `docs/architecture.md` §4 and D7→D11; document the WAF gotcha — if anything sits in front of the endpoint, allowlist Anthropic's MCP egress `160.79.104.0/21` on `/mcp` and `/healthz`, since WAF bot-blocking is the most common "cannot reach MCP server" cause. | 6 | 0.2d |

**Estimated: ~2.85 dev-days**, against the spec's ~2–3. The estimate holds only
because Task 1 is bounded mechanical work; if the call sites resist a clean
`get_store()` seam, it grows.

### Task 1 outcome

Landed as expected — `get_store()` / `set_store()` in `hubspot_mcp.state`, all
28 call sites converted, the 628 existing tests unchanged and green. Two things
the interface got wrong showed up only once something called it, and both are
now fixed:

- **`save_undo_snapshot` was missing.** The pattern-write executor captures its
  own originals after the batch runs, so it cannot use
  `save_undo_snapshot_for_action` (which derives them from a preview *before*
  the write). Added as a twelfth method.
- **`list_pending` returned `list[Path]`.** Unimplementable against Redis, and
  it leaked the server's home directory into the `hubspot_list_pending_writes`
  result. Now returns action ids — which is what approve and reject take anyway.

`tests/test_state_store_seam.py` (9 tests) holds the seam: nothing outside
`state/` imports a storage module, every interface method is reached from
`src/`, no `Path` appears in an interface signature, and — the decisive one — a
full preview → approve cycle against an in-memory store creates no files, with
the state directory pointed at a path asserted never to come into existence.

---

### Task 3 outcome — and a correction to this plan

The task row above said to wire auth "through the SDK's `token_verifier` rather
than middleware". That conflates two different things called middleware, and the
conclusion it draws is wrong:

- **MCP-protocol middleware** (`Middleware.on_initialize`) genuinely cannot be
  used — the handshake it keys off no longer runs. That is the real §1 finding.
- **ASGI middleware** runs per request by construction, one call per HTTP
  request. It was never connection-scoped, so the objection does not apply.

And `token_verifier` turns out to be the wrong seam for a *shared static
secret*: the SDK requires `AuthSettings` alongside it, which declares the server
an OAuth 2.1 protected resource — publishing
`/.well-known/oauth-protected-resource` and pointing 401s at an issuer. Phase 2
has no authorization server to point at, so a client following that metadata
would chase a discovery flow that does not exist. `token_verifier` becomes the
right seam in Phase 3, when per-user OAuth gives it something true to say.

Shipped instead: `auth/bearer_middleware.BearerAuthMiddleware`, a bare ASGI
wrapper (not Starlette's `BaseHTTPMiddleware`, which buffers responses and would
break the transport's streaming). Three things worth noting:

- **`server.build_http_app()` is where the wrapper lives**, not the `run()`
  branch — Vercel imports the ASGI app rather than calling `run()`, so guarding
  only the uvicorn path would have shipped the hosted deployment unguarded.
- **It fails closed.** A bind to anything other than loopback with no secret, or
  a secret under 32 characters, refuses to start. A guessable secret on a public
  endpoint is worse than none, because it looks protected.
- **The env var is `HUBSPOT_MCP_SERVER_SECRET`**, not the plan's bare
  `SERVER_SECRET`.

Comparison is against a SHA-256 digest rather than the raw token, so
`compare_digest` cannot leak the secret's length; missing, malformed and wrong
tokens all return one identical 401.

---

### Task 2 outcome — split in two, and a third plan correction

The row above estimated `RedisStateStore` at "implement all 11 methods". It was
12 (see the Task 1 outcome), and before any of them could be written the
interface had to become **asynchronous** — which the plan does not mention.

All 17 call sites are inside `async def` handlers, and `execute_pending_write`
alone makes up to six store calls per approve. A synchronous interface would
have stalled the event loop for six sequential network round trips on every
approve. So the task shipped as two PRs: **2a** converts the interface (and
fixes an existing inconsistency — Phase 1 offloaded three of those calls to a
thread and ran fourteen inline, including snapshot writes, despite `persistence`
taking a directory `flock` and `fsync`ing), and **2b** adds the store.

Two things the plan did not call for and should have:

- **Shared decisions, not duplicated ones.** `build_undo_snapshot` is now a pure
  function in `snapshot.py` and `is_valid_action_id` is public in
  `persistence.py`. Without that, the Redis store would have carried its own
  copy of "is this undoable?" and "is this id safe?" — the second being a path
  traversal on disk and a key injection in Redis. This is the same invariant
  Phase 0 adopted for write classification.
- **Encryption is not optional.** Previews and snapshots carry contact names,
  emails and deal amounts into a third party's database. The store refuses to
  start without `HUBSPOT_MCP_STATE_KEY`.

---

### Task 4 outcome — two caches moved, one deliberately not

The row asked for schema cache, capability cache and docs index. Two moved; the
**schema cache did not**, and that is a decision rather than an omission.

Every writer of `SchemaCache` is async, but its *readers* are not:
`validation.py`, `tools/objects.py`, `agent_routing.py` and the agent prompt
builders all read it from synchronous code, and the prompt builders are called
synchronously from `prompts/list`. Making it async means rewriting the
validation and prompt layers — which §5 of this plan names as the signal that a
seam is in the wrong place.

The cost of leaving it per-instance is bounded: a cold instance re-warms the
standard schemas in `app_lifespan`, the same work a fresh stdio session already
does. If it ever matters, hydrate from the shared cache in the lifespan and push
back on write, keeping the synchronous reader interface. Compare the two that
did move:

- **Capability matrix** — not primarily about the five probe calls.
  `_unadvertise_unavailable_tools` removes tools from `tools/list` based on this
  matrix, so two instances that probed independently (one cleanly, one through a
  transient 5xx) would advertise **different tool lists for the same portal**.
- **Docs index** — ~40 outbound fetches and ~5.5s on a cold build, and it is
  HubSpot's public documentation, identical for every portal. Now built once per
  deployment.

The task also added a **second interface rather than four more `StateStore`
methods**, because the two need opposite failure behaviour: a state backend
failure must surface, a cache backend failure must read as a miss. Encoding that
in the type is what stops the next person getting it wrong.

---

### Phase 2 closed: tasks 6–7 dropped

**Decision, 2026-09-01 (D12 in `docs/architecture.md`): PromptMetrics hosts nothing.**

Open Question 3 below asked whether Phase 2 earned its place. Working through the abuse and
cost surface answered it, and not in the way the question anticipated. The problem is not that
hosting is risky — it is that *this* deployment shape cannot do the job anyone wanted it for:

- **It serves the operator's portal.** One deployment, one portal, one shared secret (§5). Every
  user of a hosted instance reads and writes *our* HubSpot with *our* credentials. There is no
  per-user revocation and no per-user audit trail, because there are no users — there is one
  secret.
- **The operator pays for every invocation, including the rejected ones.** Bearer verification
  runs inside the function, so a 401 still bills. Vercel's WAF can reject at the edge, and spend
  management can cap and pause, but note the cap covers metered Vercel resources only — a
  Marketplace Redis sits outside it.

Neither is an implementation defect; both follow from a single-tenant shared-secret design.
Meanwhile the shipped plugin already gives every user their own portal, their own credentials
and their own machine, at zero infrastructure cost — which is what was actually wanted.

**What stays, and why it was still worth building:**

| Shipped | Still earns its place because |
|---|---|
| `StateStore` seam + async interface (Tasks 1, 2a) | Removed 14 blocking filesystem calls from the event loop on the local path, and fixed a false undo promise the interface had been hiding |
| `RedisStateStore` (Task 2b) | Prerequisite for Phase 3, and its conformance suite now holds the file store to a written contract |
| `CacheStore` (Task 4) | The capability matrix and docs index are cleaner for it locally too |
| Bearer auth + single-tenant guard (Tasks 3, 5) | Small, self-contained, and they let a *user* self-host safely if they choose to |

Nothing is reverted. Hosting is revisited only when there is a paid product to attach it to, and
the answer then is Phase 3 (per-user OAuth) — never a shared secret.

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

1. ~~**Redis provider**~~ — **decided 2026-09-01: choose at provisioning time.**
   A previous revision of this document said "Vercel's own Redis". That was
   wrong: **there is no first-party Vercel Redis.** Vercel KV was retired and
   migrated to Upstash in December 2024, and `vercel.com/docs/redis` states that
   Redis is available only through Marketplace integrations — Redis Cloud (Redis
   Inc.), Upstash, and others. Marketplace *native* integrations bill through the
   Vercel account, so the "second billing relationship" objection to Upstash was
   overstated too.

   It is not an architectural decision: every candidate speaks the Redis
   protocol, so `RedisStateStore` uses `redis-py` against a single `REDIS_URL`
   and works with any of them. Upstash's serverless advantage (per-request HTTP,
   no connection pool) would require its vendor-specific client; that trade —
   lock-in for a few milliseconds next to a 200ms HubSpot call — was declined.
2. **Token storage.** Phase 1 keeps the HubSpot refresh token in a 0600 file. In
   Phase 2 single-tenant it can be an env var, but that puts a long-lived
   refresh token in the deployment config. Encrypted in Redis is better hygiene
   and is on the Phase 3 path anyway — decide now or knowingly defer.
3. ~~**Does Phase 2 earn its place?**~~ — **answered 2026-09-01: partly.** Its
   groundwork did; its deployment did not. See "Phase 2 closed" above and D12.
