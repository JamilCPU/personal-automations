# Personal Automations

Private LAN control plane for the home lab: a per-device agent, a PC hub, an MCP server and an Android app.

Layout: `core/` (shared models, runner), `agent/`, `hub/`, `mcp/` (uv workspace members), `docs/`.

Develop: `uv run pytest core -q` and `uv run ruff check .`
