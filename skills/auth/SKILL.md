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
HS="${CLAUDE_PLUGIN_DATA}/venv/bin/hubspot-mcp"
"$HS" auth login \
  --portal "${user_config.hubspot_portal_id}" \
  --scopes $("$HS" auth scopes)
```

`auth scopes` derives the exact set from the tool registry, so it cannot drift
as tools are added. Do **not** paste a hardcoded list: an earlier version of
this skill requested five scopes, which 403s every ticket, list, workflow,
user, pipeline and engagement tool.

Two exclusions are deliberate, and both will look like omissions:

- **No `.delete` scopes.** Least privilege — destructive access is not
  requested by default. A portal that has already granted them keeps them.
- **No `crm.objects.{notes,calls,tasks,emails}.*`.** HubSpot documents these in
  403 bodies but does not offer them in the app scope picker, and requesting
  one makes HubSpot reject the *entire* authorize call.

Tell the user to complete the browser approval. After the command prints
`OAuth login succeeded for portal <id>`, the HubSpot MCP tools are live for
this session.

The user's HubSpot app must be permitted to request these scopes; if the
authorize page errors, the app's scope configuration is the place to look.

## If the command is not found

`${CLAUDE_PLUGIN_DATA}/venv/bin/hubspot-mcp` is provisioned when the MCP server
first launches. If it's missing (e.g. the MCP server failed to start because
`python3` is older than 3.12), tell the user to ensure `python3 >= 3.12` is on
PATH and run `/plugin` → reinstall `hubspot-mcp`, then retry.