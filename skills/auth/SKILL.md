---
name: auth
description: Sign the HubSpot MCP plugin into a HubSpot portal via the OAuth bring-your-own-app flow. Run this once after installing the plugin, or whenever the session-start message says "not authenticated".
---

# HubSpot MCP — Authenticate

Run the OAuth login flow for the portal configured in this plugin's settings.
This opens a browser to HubSpot; after the user approves the scopes, the
callback is captured on localhost port 3000 and tokens are saved to
`~/.claude/hubspot/<portal>.json`.

## One-time prerequisite

In the HubSpot app's **Auth** tab, add this redirect URI:

```
http://localhost:3000/oauth/callback
```

## Run it

Use the Bash tool to run exactly this command (the portal ID is substituted
from the plugin config; the app client_id / client_secret are read from
`~/.claude/hubspot/app_credentials.json`, which the plugin's SessionStart hook
wrote from the plugin settings — they are NOT in this command):

```sh
"${CLAUDE_PLUGIN_DATA}/venv/bin/hubspot-mcp" auth login \
  --portal "${user_config.hubspot_portal_id}" \
  --scopes crm.objects.contacts.read crm.objects.contacts.write crm.objects.companies.read crm.objects.deals.read crm.objects.deals.write
```

Tell the user to complete the browser approval. After the command prints
`OAuth login succeeded for portal <id>`, the HubSpot MCP tools are live for
this session.

If the user needs a different scope set, change the `--scopes` list — the
granted scopes must match what their HubSpot app is permitted to request.

## If the command is not found

`${CLAUDE_PLUGIN_DATA}/venv/bin/hubspot-mcp` is provisioned when the MCP server
first launches. If it's missing (e.g. the MCP server failed to start because
`python3` is older than 3.12), tell the user to ensure `python3 >= 3.12` is on
PATH and run `/plugin` → reinstall `hubspot-mcp`, then retry.