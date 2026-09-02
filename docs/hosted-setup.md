---
title: "hubspot-mcp — hosted deployment setup"
status: stage 1 (PromptMetrics team)
audience: whoever owns the accounts
created: 2026-09-01
---

# Hosted deployment setup

What to create in HubSpot, WorkOS and Vercel to run `hubspot-mcp` as a hosted
server, and which values to bring back. Everything here needs account access, so
it cannot be scripted from this repo.

> **Read first:** `docs/architecture.md` **D12** — PromptMetrics deliberately
> hosts nothing for general distribution. This is **stage 1** of Phase 3: a
> deployment for you and the team, so HubSpot works in Claude Cowork. It is not
> a service to hand to community members; that is stage 3 and it is gated on a
> HubSpot marketplace listing.

Work through the three sections in order — Vercel needs values from the other
two. **Do not paste any secret into chat, a commit, or an issue.** Every one of
them belongs in the Vercel dashboard.

---

## 1. HubSpot public app

A *public* app (not a private app) is what lets someone click "Connect" and
authorise their own portal.

1. In the [HubSpot developer portal](https://developers.hubspot.com), create an
   app with the **marketplace** distribution type.
   - Not *private* distribution: as a standard app partner that caps at **10**
     customer installs, where marketplace-distribution allows **25** before a
     listing is approved.
2. **Auth tab → redirect URL.** This must match the deployment exactly,
   including scheme and no trailing slash:
   ```
   https://<your-vercel-domain>/connect/hubspot/callback
   ```
   You will not know the domain until section 3. Either set a custom domain
   first, or come back and fill this in — HubSpot rejects a mismatch outright.
3. **Auth tab → scopes.** Exactly these 27, and no others:
   ```
   automation
   crm.lists.read
   crm.lists.write
   crm.objects.appointments.read
   crm.objects.appointments.write
   crm.objects.companies.read
   crm.objects.companies.write
   crm.objects.contacts.read
   crm.objects.contacts.write
   crm.objects.deals.read
   crm.objects.deals.write
   crm.objects.tickets.read
   crm.objects.tickets.write
   crm.pipelines.orders.read
   crm.pipelines.orders.write
   crm.schemas.companies.read
   crm.schemas.companies.write
   crm.schemas.contacts.read
   crm.schemas.contacts.write
   crm.schemas.deals.read
   crm.schemas.deals.write
   crm.schemas.tickets.read
   crm.schemas.tickets.write
   sales-email-read
   settings.users.read
   settings.users.write
   tickets
   ```
   Two absences, for entirely different reasons — worth not confusing them:
   - **`.delete` scopes are our choice, not a HubSpot restriction.** They exist
     and are perfectly requestable; we do not ask for them so that an approved
     write can never quietly become a delete (R4, least privilege). Tick them if
     you want to; the server still never requests them.
   - **`crm.objects.notes.*`, `.calls.*`, `.tasks.*` and `.emails.*` are not
     offered by HubSpot.** They are absent from the public-app scope reference
     and the scope picker does not list them — HubSpot names engagement
     permissions differently, and the `crm.objects.*` form appears only in its
     own 403 error bodies. If you cannot find them in the picker, nothing is
     missing: the server never requests them either.

     `scope_registry` records the `crm.objects.*` form because that is what
     HubSpot's errors name, and excludes it from what we request. Upstream also
     found that requesting one made HubSpot reject the whole authorize call;
     that behaviour is undocumented, so treat it as one team's experience rather
     than a specified guarantee.

   Regenerate the list any time with `hubspot-mcp auth scopes`.
4. Copy the **Client ID** and **Client Secret** from the Auth tab.

**Bring back:** `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`.

> **Install ceiling.** This app is capped at 25 installs until a marketplace
> listing is approved. Applying for one needs **3 active installs from portals
> unaffiliated with PromptMetrics** — our own do not count — so stage 1 cannot
> start that clock. The listing is the long pole for stage 3 — 10 business days to first response and up to 60 days for the full cycle.

---

## 2. WorkOS AuthKit

WorkOS is the **authorization server**: it logs people in and issues the tokens
our server verifies. We never mint tokens ourselves, which is what keeps the
provider swappable.

> **Order note.** Step 4 needs the deployment URL. If you have a domain in mind
> (`hubspot-mcp.promptmetrics.dev`, say) use it now. Otherwise create the Vercel
> project first — §3 step 1, no deploy needed — to claim the `*.vercel.app`
> hostname, then come back.

1. Create a WorkOS account and a project. Use the **staging** environment first;
   it is free at any scale.
2. Enable **AuthKit** and turn on the sign-in methods you want. Email plus
   Google is enough for the team.
3. **Enable Dynamic Client Registration**: *Connect* → *Configuration*. WorkOS
   supports Client ID Metadata Documents natively; DCR is the fallback for MCP
   clients that do not yet speak CIMD, and we do not know which Claude uses.
4. **Add a Resource Indicator** under *Connect*. This is not a value WorkOS
   gives you — it is your server's URL, which you decide and then register:
   ```
   https://<your-domain>/mcp
   ```
   **Include the `/mcp` path.** That is the exact URL that goes into the client,
   so it is what the client names when requesting a token. WorkOS's own example
   shows a bare domain because theirs sits at a subdomain root; ours does not.
   The value has to match in three places — what AuthKit stamps as `aud`, what
   our server verifies, and what the client sends as `resource` — so use the
   form the client will actually send.

   Then open the **`...` menu** next to it and **Set as default**, which covers
   clients that omit the `resource` parameter.

   AuthKit stamps issued tokens with an `aud` matching this, which is what lets
   our server prove a token was minted *for us*. Without it a token the same
   authorization server issued for a **different** MCP server verifies here
   perfectly — same issuer, same signature — and an agent moves between many
   servers in one session. **Configure none and AuthKit falls back to the
   environment's client ID as the audience and ignores `resource` entirely**,
   which silently removes that protection.

   You can register several: staging and production, or a `.vercel.app` host now
   and a custom domain later.

5. Copy the **AuthKit domain** — the issuer, of the form
   `https://<something>.authkit.app`.

**Bring back:** the AuthKit issuer URL and the Resource Indicator you set. Both
are public identifiers — **no WorkOS API key or client secret is needed**, and
none should be shared. The server verifies tokens against the public JWKS at
`https://<authkit-domain>/oauth2/jwks`; it never calls WorkOS with a credential.

> Free to 1M monthly active users. The paid part is enterprise SSO connections
> at $125/month each, for connecting a customer's own Okta or Entra — nothing in
> stage 1 or 2 needs one.

---

## 3. Vercel

1. Create a project from this repository. Framework preset: **Other**. The
   entrypoint, function config and dependency install are already committed —
   `app.py`, `vercel.json` and `requirements.txt` — so there is nothing to
   configure in the build settings.
2. Add a **Redis** store from the Marketplace (Upstash or Redis Cloud — either
   works; the code speaks the Redis protocol and reads a single `REDIS_URL`).
   The integration injects `REDIS_URL` automatically.

   - **Storage is not the constraint.** Pending previews expire in 24h,
     snapshots in 7 days, the audit log is capped at 1000 entries per portal and
     the docs index is one key of roughly a megabyte. A free tier is ample.
   - **Region matters more than size.** `vercel.json` pins functions to `fra1`
     (Frankfurt), so create the store there. Vercel otherwise defaults to
     Washington DC, and an approve makes about six Redis round trips — an
     Atlantic crossing each costs roughly half a second per write. If you move
     the store, move `regions` in `vercel.json` with it.
   - On Redis Cloud the free plan only appears once **High Availability** is set
     to **None**; the form says so, quietly.
3. Generate the state encryption key locally and paste it in — it must never be
   generated in CI or committed:
   ```sh
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. Set these environment variables on the project:

| Variable | Value | Why |
|---|---|---|
| `HUBSPOT_CLIENT_ID` | from §1 | Identifies the HubSpot app |
| `HUBSPOT_CLIENT_SECRET` | from §1 | HubSpot OAuth is confidential-client only; there is no secretless option |
| `HUBSPOT_REGION` | `us` or `eu` | Only selects the authorize host; the token endpoint is global |
| `HUBSPOT_MCP_PUBLIC_URL` | `https://<your-domain>` | Builds the redirect URI. Must match §1 byte for byte |
| `HUBSPOT_MCP_STATE_KEY` | from step 3 | Encrypts pending previews, undo snapshots and refresh tokens at rest |
| `REDIS_URL` | injected by the integration | Selects the Redis backend automatically; no second variable to forget |
| `HUBSPOT_MCP_OAUTH_ISSUER` | the AuthKit issuer from §2 | Turns on per-request OAuth. **Setting this replaces the shared-secret bearer** rather than stacking with it |

**Do not set `HUBSPOT_PORTAL` or any `HUBSPOT_TOKEN_*`.** With per-request OAuth
the portal comes from each caller's token, so a process-wide portal is not a
fallback — it is another customer's CRM sitting behind any path that fails to
resolve the caller. The server refuses to start if either is present.
| ~~`HOME`~~ | — | Already set to `/tmp` in `vercel.json`; nothing to do |

The resource identifier the server verifies against is derived as
`HUBSPOT_MCP_PUBLIC_URL` + `/mcp`, so it matches the Resource Indicator
registered in §2 without a sixth variable to keep in sync.

5. **Set a spend limit.** Billing → Spend Management: set an amount and enable
   *pause production deployment*. Two caveats worth knowing before you rely on
   it: it does **not** cover Marketplace spend, so cap the Redis vendor
   separately, and it checks every few minutes rather than continuously, so set
   it below your true maximum.

**Bring back:** the deployment URL, so §1's redirect URL can be finalised.

---

## 3b. Values settled for staging

Recorded so they are not re-derived. All public identifiers; the two secrets
(HubSpot client secret, state key) live only in the Vercel dashboard.

| | |
|---|---|
| AuthKit issuer | `https://tolerant-climb-38-staging.authkit.app` — **no trailing slash**; RFC 9207 compares issuers exactly |
| Resource Indicator | `https://hubspot-mcp-promptmetrics.vercel.app/mcp` |
| `HUBSPOT_MCP_PUBLIC_URL` | `https://hubspot-mcp-promptmetrics.vercel.app` |
| HubSpot redirect URL | `https://hubspot-mcp-promptmetrics.vercel.app/connect/hubspot/callback` |

Confirmed live on the issuer's metadata: `client_id_metadata_document_supported`
is true and a registration endpoint is present, so a Claude client can register
itself by either mechanism with nothing for a user to paste.

---

## 4. Verify

In order — each step only makes sense once the one before passes.

```sh
curl https://<domain>/healthz
# → 200 {"status":"ok","version":"..."}

curl -i https://<domain>/mcp
# → 401, WWW-Authenticate carrying resource_metadata=...

curl https://<domain>/.well-known/oauth-protected-resource/mcp
# → names the AuthKit issuer, so a client can discover it with no configuration.
#   Note the /mcp suffix: RFC 9728 mounts the document under the resource path.

curl -i "https://<domain>/connect/hubspot?ticket=nope"
# → 400 with a page saying the link expired — never a traceback
```

Then in Cowork: *Customize → Connectors*, add `https://<domain>/mcp`.

**Watch what happens at the login step.** This is the open question in the plan:
if Cowork registers itself via DCR, onboarding scales. If it asks you to paste an
OAuth Client ID and Secret, stage 1 is unaffected but stage 3 needs a different
answer. Tell me which you see.

Then run the connect tool, authorise a sandbox portal, and confirm a read
(`hubspot_search_objects`), a gated write with approval, and an undo.

> Use a **sandbox portal**, not a production one, until the whole path is proven.

---

## If it does not connect

**Check for a WAF before debugging the server.** Anthropic connects from its own
cloud, not your machine, so a bot-blocking rule in front of the endpoint is the
most common cause of "cannot reach MCP server". Allowlist Anthropic's MCP egress
range `160.79.104.0/21` on `/mcp` and `/healthz`.

Otherwise, in order of likelihood: the redirect URL in §1 does not match
`HUBSPOT_MCP_PUBLIC_URL` exactly; a scope outside the list in §1 was added; or
`HUBSPOT_MCP_STATE_KEY` changed, which makes every stored connection
unreadable — the server says so explicitly rather than reporting people as
disconnected.
