# Changelog

## Unreleased

### Phase 3 — stage 1: Vercel deployment

- **`app.py`, `vercel.json` and `requirements.txt`.** Vercel imports the ASGI
  app rather than calling `run`, which is why every guard lives in
  `build_http_app`. `requirements.txt` installs `.[hosted]`, so the dependency
  set stays defined once in `pyproject.toml`.
- **The single-tenant guard inverts under hosted OAuth.** Serving many portals
  is the point, so `enforce_no_ambient_portal` replaces it and refuses to start
  when `HUBSPOT_PORTAL` or any `HUBSPOT_TOKEN_*` is set. On a multi-tenant
  deployment a process-wide portal is not a fallback — it is another customer's
  CRM behind any path that fails to resolve the caller.
- `HOME=/tmp` is set in `vercel.json`: the schema cache and trace log still
  write to local disk and only `/tmp` is writable on a serverless host.
- `reference/**` is excluded from the function bundle, alongside tests and docs.

### Phase 3 — stage 1: per-caller session resolution

The hosted path now resolves the HubSpot session **per request** from the
verified access token, so all 86 tools act on the portal the *caller*
authorised. The stdio path is untouched.

- **Clients are pooled per subject, not built per request.** `HubSpotClient`
  opens an `httpx.AsyncClient` in its constructor and must be closed; one per
  request leaks a connection pool every time — invisible in testing, a slow
  degradation in production. The pool is LRU-bounded at 32, evicts by closing,
  and the lifespan closes the rest at shutdown.
- **Pooled by subject rather than portal**, because two people may authorise the
  same portal and a shared client would refresh one person's grant into the
  other's record.
- **A missing connection is guidance, not an error.** Someone authenticated but
  not yet connected gets a connect link through the same `auth_error` channel
  stdio uses for an unauthenticated portal — no new branch in any tool body. A
  *transient* HubSpot failure deliberately gets no link: re-authorising fixes
  nothing when HubSpot is merely having a bad minute.
- **The hosted lifespan builds nothing portal-specific.** Its only job is owning
  the pool's lifetime; if anything bypasses the resolver it gets a session that
  cannot act, rather than a working client for whatever portal was configured.
- Per-request resolution deliberately does **not** call `warm_standard_schemas`,
  which makes several HubSpot calls. The schema cache warms on demand.

### Phase 3 — stage 1: pluggable token refresh

- **`HubSpotClient` takes an optional `token_refresher`.** Its refresh
  previously wrote the local portal file, which is right for stdio and wrong for
  a hosted deployment twice over: that disk does not survive the instance, and a
  hosted token belongs to a *user* in the connection store rather than to a
  portal. The default is unchanged, so stdio behaves exactly as before.
- **`update_credentials()`** lets a pooled client adopt a freshly resolved
  token without being rebuilt, so it keeps its connection pool. Prerequisite
  for serving many portals from one process without a socket leak per request.

### Phase 3 — stage 1: the server as an OAuth resource server

- **Setting `HUBSPOT_MCP_OAUTH_ISSUER` turns on per-request OAuth.** The server
  then publishes RFC 9728 protected-resource metadata, so a client discovers
  where to authenticate with nothing configured, and rejects unverified requests
  with the 401 the spec requires.
- **OAuth replaces the shared-secret bearer rather than stacking with it.**
  Requiring both would mean every user needed a secret nobody should be sharing.
  Unset the issuer and the shared-secret path is unchanged, so self-hosting
  still works.
- **The resource identifier is `HUBSPOT_MCP_PUBLIC_URL` + the mount path**, from
  one constant. That string has to match in three places — what the
  authorization server stamps as `aud`, what we verify, and what the client
  sends as `resource` — so it is derived rather than configured a fourth time.
- An issuer set without a public URL **refuses to start**. Serving anyway would
  401 every request with nothing visibly wrong.

### Phase 3 — stage 1: verifying MCP access tokens

- **`JWTVerifier`** makes the hosted server an OAuth 2.1 resource server. It
  verifies signature against the issuer's JWKS, the issuer as an exact string
  (RFC 9207 comparison is exact, so a trailing slash is a different issuer), and
  **the audience** — the check whose absence is invisible in testing and fatal
  in production, because without it a token the same authorization server minted
  for a *different* MCP server is accepted here.
- **Asymmetric algorithms only.** Accepting an HMAC algorithm is the classic JWT
  confusion attack: the issuer's public key is published, so an attacker could
  sign their own token with it and have it verify. `none` likewise.
- The JWKS is fetched with `httpx`, not `jwt.PyJWKClient`, which uses a blocking
  `urllib` call — wrong on an event loop, and it bypasses the HTTP layer the
  rest of the codebase tests against. An unknown `kid` refetches once, so issuer
  key rotation is a non-event rather than an outage.
- Every failure returns `None` rather than raising: the SDK turns that into the
  401 the spec requires, and a JWKS outage becomes a 401 rather than a 500.
- The `redis` extra is now `hosted`, and includes `pyjwt[crypto]`.

### Phase 3 — stage 1: app credentials from the environment

- **`HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET` and `HUBSPOT_REGION` are now
  read from the environment**, falling back to
  `~/.claude/hubspot/app_credentials.json`. A hosted deployment has no writable
  home directory, so the file was the only source and there was no way to
  configure one at all. Environment wins: a deployment sets these deliberately,
  where the file is written by the plugin's SessionStart hook and may belong to
  a different app. A blank variable counts as unset, and values are trimmed
  because both `vercel env pull` and dashboard paste add newlines.
- **`docs/hosted-setup.md`** — what to create in HubSpot, WorkOS and Vercel, and
  which values to bring back.
### Phase 3 — stage 1: connecting a HubSpot account

- **`ConnectFlow`** bridges the awkward gap in hosted OAuth: authorising HubSpot
  is a *browser* journey, but the person's identity comes from an MCP access
  token a browser never carries. A tool mints a one-time **ticket** bound to the
  caller's subject and returns a link; opening it is what proves who is
  connecting. `GET /connect/hubspot` redeems it, `GET /connect/hubspot/callback`
  exchanges the code and stores the connection.
- **The ticket and the OAuth `state` are credentials in a URL**, and are treated
  as such: single-use, 10-minute TTL, and stored under a digest of themselves so
  a dump of the backing store yields nothing replayable. A forged, replayed and
  expired `state` all return the *same* message — telling an attacker which they
  hit is free help.
- **The redirect URI is server-configured, never taken from the request** — a
  caller-supplied one is how an OAuth flow becomes an open redirect.
- **The portal comes from HubSpot, not the caller.** A public app cannot know
  the portal in advance because the user picks the account on HubSpot's consent
  screen; it arrives as `hub_id` on the token response, with the token-info
  endpoint as the documented fallback.
- The callback page escapes everything it renders, including HubSpot's own
  `error_description`, and an unexpected failure shows a generic message while
  the detail goes to stderr for the operator.

### Phase 3 — stage 1: resolving a caller's HubSpot session

- **`HostedOAuthProvider`** answers "which portal did *this caller* authorise?",
  looking the subject up in the connection store and refreshing their access
  token when it nears expiry. Deliberately **not** a `TokenProvider`: that
  interface resolves by `portal_id`, which on a hosted deployment is an output
  of authentication, not an input — taking one from the caller would let anyone
  name someone else's portal.
- **A failed refresh is classified conclusive or transient**, the same
  distinction the capability prober draws. A 4xx is HubSpot saying the grant is
  gone and the user must reconnect; a 5xx, 429 or network error is HubSpot
  having a bad minute and must not send anyone round an OAuth flow — doing so
  wastes their time and rotates a credential that still worked. A failed refresh
  never discards the connection record either.
- **One refresh per subject at a time.** Two concurrent tool calls whose token
  has just aged out would otherwise both refresh, and if HubSpot rotates the
  refresh token the loser writes back a dead one. In-process only; the
  cross-instance race is documented rather than solved.
- `oauth_flow.refresh_tokens_only` splits the network half of the refresh from
  persistence, so the local path (portal file) and the hosted path (connection
  store) share one endpoint, one payload and one 404-fallback rule.

### Phase 3 — stage 1: per-user HubSpot connections

- **`ConnectionStore`** joins an identity subject to the HubSpot portal that
  person authorised — the bridge between Phase 3's two OAuth relationships.
  `FileConnectionStore` (0600, for local and self-hosting) and
  `RedisConnectionStore` (encrypted, for the hosted path), selected by the same
  `REDIS_URL` / `HUBSPOT_MCP_STATE_BACKEND` rule as the other stores.
- **Unreadable is not the same as unconnected.** A corrupt record or a rotated
  encryption key raises `ConnectionUnreadable` rather than returning `None` —
  reporting "not connected" would send a user round the OAuth flow to fix a
  key-management problem. This is `StateStore` semantics, not `CacheStore`.
- **Subjects are hashed into storage keys, not validated.** Identity providers
  mint subjects in formats we do not control (`auth0|abc`, an email, an opaque
  id), so a validating regex risks locking out a real user, while the raw value
  in a Redis key or file path invites injection and traversal. A digest is
  injection-proof for any input and keeps the subject out of the keyspace.
- **`HubSpotConnection.__repr__` redacts both tokens.** An unredacted dataclass
  repr puts a live CRM credential in every traceback that touches the object.
- The three Redis stores now share an `_EncryptedRedis` base for the connection
  and cipher. Encryption is common to all of them; *decryption* is not, because
  the right answer to an unreadable value differs per store — each keeps its own.

### Phase 3 — stage 1: the per-request session seam

Groundwork for per-user OAuth. No behaviour change on any existing path.

- **`server._session(ctx)` now decides whose portal a request acts on.** Every one
  of the 86 tools, the 7-tool safety layer and the 44 charters reach their HubSpot
  client, schema cache and `PortalConfig` through it, so a hosted deployment can
  resolve the caller's portal from their access token without a single tool body
  changing — the same shape as `state.get_store()`.
- `set_session_resolver()` installs that resolver. With none installed the server
  keeps exactly its single-portal behaviour, and `_lifespan` remains the
  single-portal implementation rather than something tools call directly. A test
  fails the build if anything reaches past the seam to it.

### Phase 2 — Task 1: the `StateStore` seam

Groundwork for serving over HTTPS. No user-facing behaviour change on stdio.

- **Routed all persistence through `StateStore`.** `handlers`, `safety` and
  `server` called the `persistence` / `snapshot` / `audit` modules directly, so
  the `RedisStateStore` planned in `docs/architecture.md` §4 would not have been
  a drop-in — it had ~28 call sites to convert first. They now resolve the store
  through `hubspot_mcp.state.get_store()`, and `set_store()` installs a
  different implementation. `FileStateStore` remains the default.
- **Added `StateStore.save_undo_snapshot`.** The pattern-write executor captures
  its own originals (it only learns which records applied after the batch runs)
  and had no interface method to write them — an omission invisible while
  nothing called the interface.
- **`hubspot_list_pending_writes` now returns action ids, not file paths.** A
  path cannot be honoured by a remote store, and the previous shape leaked the
  server's home directory into the tool result. Action ids are also what
  `hubspot_approve_write` / `hubspot_reject_write` actually take.
### Phase 2 closed — no hosted instance (D12)

`hubspot-mcp` is distributed as a Claude Code plugin that each user runs locally against their
own portal. PromptMetrics hosts nothing.

The remote-HTTP work stopped after its groundwork. A single-tenant shared-secret deployment
serves *the operator's* portal on *the operator's* bill — including the rejected requests, since
bearer verification runs inside the function — so it could never be a way to distribute this to
users, and the local plugin already does that better at zero infrastructure cost.

Nothing is reverted. The `StateStore` / `CacheStore` seams, the async state interface and the
shared undo/action-id decisions all improve the local path and are prerequisites for per-user
OAuth. `BearerAuthMiddleware` and the single-tenant guard stay so that a *user* can self-host
safely if they want to. Hosting is revisited only alongside per-user OAuth.

### Phase 2 — Task 5: single-tenant guard

- **An HTTP deployment now refuses to start unless it resolves exactly one
  portal.** Fatal on a non-loopback bind: no `HUBSPOT_PORTAL`, a portal read
  from a `.hubspot-portal` file in the working directory (which on a hosted
  deployment is a build artifact, so a stray committed file would decide which
  CRM this writes to), or `HUBSPOT_TOKEN_*` variables for more than one portal.
- Enforced rather than assumed because `_unadvertise_unavailable_tools` calls
  `mcp.remove_tool(...)` on the module-level server: a second portal lacking
  Workflows would unadvertise the workflow tools for the first one too, until
  the instance recycled. That is a correctness bug, not an inefficiency.
- Loopback binds only warn, matching the bearer-auth policy — local development
  stays permissive, anything internet-reachable is strict.
- Every problem is reported at once, so a misconfigured deployment does not
  teach you its requirements one redeploy at a time.

### Phase 2 — Task 4: shared caches

- **New `CacheStore` interface**, separate from `StateStore` because the two
  need opposite failure behaviour. Losing a pending preview breaks an approve,
  so `StateStore` lets backend errors surface. Losing a cached capability matrix
  costs a refetch, so a `CacheStore` backend failure reads as a **miss** — a
  Redis blip must not fail tool calls that could have gone to HubSpot instead.
- **The capability matrix is now shared.** Not just to save five probe calls per
  cold start: `_unadvertise_unavailable_tools` removes tools from `tools/list`
  based on this matrix, so two instances that probed independently — one
  cleanly, one through a transient 5xx — would advertise different tool lists
  for the same portal.
- **The docs index is now shared and global.** A cold build is ~40 outbound
  fetches taking ~5.5s; it is now built once per deployment rather than once per
  instance. It is HubSpot's public documentation, identical for every portal, so
  it is cached unscoped.
- **The schema cache deliberately stays on local disk.** Its readers are
  synchronous — `validation`, `tools/objects`, `agent_routing` and the agent
  prompt builders — so moving it means rewriting the validation and prompt
  layers. A cold instance simply re-warms standard schemas in the lifespan,
  which is what a fresh stdio session already does.
- The file backend keeps the exact paths it used before
  (`CONFIG_DIR/<portal>/capabilities.json`, `CONFIG_DIR/docs_index.json`), so no
  local cache is orphaned on upgrade. Expiry moved from a field inside each
  value to the store, which is what lets Redis use a native TTL.

### Phase 2 — Task 2: `RedisStateStore`

The write-safety state machine can now live outside the process, which is what
makes the server usable on a host where consecutive requests hit different
instances. Nothing changes for the stdio plugin.

- **`RedisStateStore`, provider-agnostic.** Speaks the Redis protocol via
  `redis-py` and reads a single `REDIS_URL` — what every Vercel Marketplace
  Redis integration injects. There is no first-party Vercel Redis, so picking a
  vendor must not be a code change.
- **Everything is encrypted before it leaves the process.** Pending previews and
  undo snapshots carry HubSpot record properties — names, emails, deal
  amounts — and are about to sit in a third party's database. `RedisStateStore`
  refuses to start without `HUBSPOT_MCP_STATE_KEY`. A value written under a
  rotated key reads as not-found rather than crashing the approve path.
- **`REDIS_URL` alone selects the backend**, so the hosted deployment configures
  itself; `HUBSPOT_MCP_STATE_BACKEND` forces `file` or `redis` when the
  inference is wrong. The `redis` and `cryptography` dependencies are the
  `[redis]` extra and the stdio path never imports them.
- **The confirm-count gate is transactional.** Recording a confirmation is a
  read-modify-write under `WATCH`, so two concurrent approves cannot both see an
  unconfirmed preview.
- TTLs: pending previews 24h (matching the file store's reaper), undo snapshots
  7 days, audit capped at 1000 entries per portal.
- **`build_undo_snapshot` extracted** as a pure function in `snapshot.py`, and
  `persistence.is_valid_action_id` made public. Both stores now share one
  decision about what is undoable and one about which action ids are safe —
  on disk a crafted id is a path traversal, in Redis a key injection.

`tests/test_state_store_conformance.py` runs one contract against both stores.

### Phase 2 — Task 2a: `StateStore` is now asynchronous

Prerequisite for a network-backed store. No user-facing behaviour change.

- **Every `StateStore` method is a coroutine.** All 17 call sites already sit
  inside `async def` handlers, so a synchronous interface would have put a Redis
  round trip on the event loop — `execute_pending_write` alone makes up to six
  store calls per approve.
- **`FileStateStore` now keeps filesystem work off the loop too.** The ported
  `persistence` module takes a directory `flock` and `fsync`s on write; Phase 1
  offloaded two of those calls by hand and left the other fifteen inline. All of
  them now run through `asyncio.to_thread`, and the hand-rolled offloads in
  `handlers.py` and `safety.py` are gone — the store owns that now.

### Phase 2 — Task 3: per-request bearer auth for the HTTP transport

Groundwork for serving over HTTPS. No change to the stdio path, which is still
the default and still needs no authentication — the transport is a pipe to a
process you started.

- **`Authorization: Bearer <secret>` is now required on every HTTP request.**
  Protocol `2026-07-28` removed the handshake, so there is no connection setup
  to authenticate in and no session to carry a decision forward; anything
  checked once per connection would authorise the rest of it for free.
- **The server fails closed.** Binding to anything but loopback without
  `HUBSPOT_MCP_SERVER_SECRET` set, or with a secret under 32 characters, refuses
  to start. A guessable secret on a public endpoint is worse than no secret,
  because it looks protected. A loopback bind without one still works for local
  development, with a warning.
- **Added `GET /healthz`** — public by design, reports version and nothing about
  the portal, and deliberately touches neither HubSpot nor the state store so a
  third party having a bad minute cannot take the deployment down.
- Missing, malformed and wrong tokens return one identical 401 with
  `WWW-Authenticate: Bearer`. The comparison is against a SHA-256 digest, so it
  leaks neither content nor the secret's length.

## 0.2.0 — 2026-09-01

Protocol migration and parity with `promptmetrics/hubspot-claude` v0.2.14.
Tests 11 → 628, now gated in CI.

### Security and safety

- **Closed a write-gate bypass.** Thirteen tools — `hubspot_raw_api` on any
  mutating verb, all five workflow writes, refunds, imports, exports, forms,
  reports, dashboards and scheduled emails — reached HubSpot with no preview,
  no approval, no undo snapshot and no audit entry, because writes were
  classified only by `.write`/`.delete` scope suffix and those tools carry
  neither.
- **Closed two path traversals**, both reachable from tool arguments: a crafted
  `action_id` escaped the pending-preview directory, and an OAuth `state` of
  `../<portal_id>` *deleted the portal's stored token file*.
- **Credential writes no longer have a world-readable window** — tokens, client
  secrets and PKCE state are created 0600 and moved into place atomically.
- **Undo stopped lying.** An update whose preview captured no original values
  was marked undoable, so an operator approved believing it could be rolled
  back. Now fail-closed, and the preview warns at approval time.

### Protocol `2026-07-28`

- Migrated from FastMCP to the official `mcp` SDK. Handshake-era clients back
  to `2024-11-05` are still served from the same deployment.
- Write approval can complete **in one call** via Multi Round-Trip Requests,
  falling back to the two-call `hubspot_approve_write` flow where the client
  does not support elicitation.
- `tools/list` and `prompts/list` carry `ttlMs`/`cacheScope: private`, are
  deterministically ordered, and failed calls now set `is_error`.

### Added

- **Risk-tiered approval** (`AUTO`/`CONFIRM`/`FULL_GATE`) and **pattern
  approval** — approve one rule, scale it with per-record compare-and-set that
  skips drifted records.
- **44 specialist charters as MCP prompts** plus `hubspot_route`.
- **Portal capability gating**: unavailable tools are unadvertised, but only on
  a conclusive entitlement probe.
- **`hubspot_status`**: portal tier and request/latency/error/cost aggregates.
- **Working docs search** over HubSpot's official documentation — keyless and
  server-side, so it does not depend on the client having web search.
- Workflow blueprints move to JSON data with the extract → parameterize →
  promote → create loop.

### Fixed

- Reads retry on 429 honouring `Retry-After`; writes never auto-retry.
- `find_duplicates` paginates — past the first 100 records every large portal
  previously looked duplicate-free.
- Bulk updates reject mis-shaped records, which HubSpot silently accepts with a
  200 while changing nothing.
- The auth skill requested 5 OAuth scopes where the tool registry needs 27;
  the set is now derived and cannot drift.
- Plugin manifests were failing `claude plugin validate` on missing
  `userConfig` titles.

## 0.1.1 — 2026-07-07

Initial release: MCP server with 76 domain tools and the preview → approve
write gate, distributed as a Claude Code plugin.
