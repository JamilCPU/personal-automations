# Personal Automations

Private LAN control plane for the home lab. See and control programs (systemd units, Docker containers, k3s pods, Windows services) on jw (the MC Server PC, which hosts the hub) and the Raspberry Pis from a Pixel app or from Claude Code.

```
Pixel app ──┐                      ┌── agent on Pi3        (:7701)
            ├──> hub on jw (:7700) ├── agent on Pi1 (k3s)
Claude Code ┘   (MCP server)       └── agent on Robot Pi
```

## Layout

| Path | What |
|------|------|
| `core/` | shared models, command runner and program-visibility policy (`pa_core`) |
| `agent/` | per-device FastAPI agent plus deploy files (`agent/deploy/`) |
| `hub/` | hub (runs on jw, 192.0.2.197): aggregates agents, action history, pairing, Windows autostart (`hub/deploy/`) |
| `mcp/` | MCP server for Claude Code (stdio, or streamable HTTP on :7702) |
| `android/` | Kotlin / Jetpack Compose app |
| `docs/` | [INSTALL.md](docs/INSTALL.md), [DEPLOY.md](docs/DEPLOY.md), [API.md](docs/API.md) |

## Install

Follow [docs/INSTALL.md](docs/INSTALL.md).

## Develop

```
uv run pytest -q
uv run ruff check .
cd android && .\gradlew.bat testDebugUnitTest
```
