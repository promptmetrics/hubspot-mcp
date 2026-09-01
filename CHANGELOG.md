# Changelog

## Unreleased

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
