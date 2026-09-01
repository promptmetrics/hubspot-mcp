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
   Two constraints that are easy to get wrong and produce confusing failures:
   - **No `.delete` scopes.** Deletes go through the write gate and are
     performed with write scopes; requesting delete scopes at authorize time
     asks users for permission we do not need.
   - **No `crm.objects.notes.*`, `.calls.*`, `.tasks.*` or `.emails.*`.**
     HubSpot rejects the *entire* authorize call if these are requested, with an
     error that does not name them.

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

1. Create a WorkOS account and a project. Use the **staging** environment first
   — it is free at any scale.
2. Enable **AuthKit**, and turn on the sign-in methods you want (email +
   Google is enough for the team).
3. **Enable Dynamic Client Registration**: dashboard → *Connect* →
   *Configuration*. Without it, an MCP client has no way to register itself and
   you would be pasting client IDs by hand.
4. Copy the **AuthKit domain** — the issuer URL, of the form
   `https://<something>.authkit.app`.

**Bring back:** the AuthKit issuer URL.

> Free to 1M monthly active users. The paid part is enterprise SSO connections
> at $125/month each, for connecting a customer's own Okta or Entra — nothing
> in stage 1 or 2 needs one.

---

## 3. Vercel

1. Create a project from this repository. Framework preset: **Other**; the ASGI
   entrypoint is `hubspot_mcp.server.build_http_app()`.
2. Add a **Redis** store from the Marketplace (Upstash or Redis Cloud — either
   works; the code speaks the Redis protocol and reads a single `REDIS_URL`).
   The integration injects `REDIS_URL` automatically.
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

5. **Set a spend limit.** Billing → Spend Management: set an amount and enable
   *pause production deployment*. Two caveats worth knowing before you rely on
   it: it does **not** cover Marketplace spend, so cap the Redis vendor
   separately, and it checks every few minutes rather than continuously, so set
   it below your true maximum.

**Bring back:** the deployment URL, so §1's redirect URL can be finalised.

---

## 4. Verify

In order — each step only makes sense once the one before passes.

```sh
curl https://<domain>/healthz
# → 200 {"status":"ok","version":"..."}

curl -i https://<domain>/mcp
# → 401 with a WWW-Authenticate challenge naming the WorkOS issuer

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
