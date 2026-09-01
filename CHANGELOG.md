# Changelog

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
