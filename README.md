# Personal Automations

A private LAN control plane for a small home lab. It shows which programs are running on each machine (systemd units, Docker containers, k3s pods, Windows services) and lets you start, stop, restart, read logs, switch USB power or reboot, either from an Android app or from Claude Code through an MCP server.

```
Android app ─┐                      ┌── agent on a Raspberry Pi
             ├──> hub (:7700) ──────├── agent on a Raspberry Pi (k3s)
Claude Code ─┘   (+ MCP server)     └── agent on a robot Pi
```

Agents run on each device and expose a small HTTPS API. The hub aggregates them, keeps an action history in SQLite, handles pairing and per-client bearer tokens, and is the only thing the phone app and the MCP server talk to. Transport is TLS with certificate pinning.

## Stack

- Python 3.11+ with FastAPI (agent and hub), a `uv` workspace, pytest and ruff
- MCP server (stdio, or streamable HTTP over TLS) built on the Python MCP SDK
- Kotlin and Jetpack Compose Android app (pairing, pinned-certificate TLS)
- Docker image for running the hub and MCP server on Linux; Windows scheduled-task installer for the hub
- GitHub Actions CI (Python tests on Windows, Linux image build, Android unit tests)

## Project layout

| Path | What |
|------|------|
| `core/` | shared models, command runner, auth, TLS helpers and program-visibility policy (`pa_core`) |
| `agent/` | per-device FastAPI agent plus deploy files (`agent/deploy/`) |
| `hub/` | hub: aggregates agents, action history, pairing, autostart (`hub/deploy/`) |
| `mcp/` | MCP server for Claude Code |
| `android/` | Kotlin / Jetpack Compose app |
| `docs/` | [INSTALL.md](docs/INSTALL.md), [DEPLOY.md](docs/DEPLOY.md), [DEPLOY-LINUX.md](docs/DEPLOY-LINUX.md), [API.md](docs/API.md), design spec and plan |

## Running

Full setup (hub, agents, phone pairing, Claude Code registration) is in [docs/INSTALL.md](docs/INSTALL.md). In short:

```
uv sync --all-packages
uv run pa-hub init          # writes config, secrets and a self-signed certificate
uv run pa-hub serve         # hub on :7700
uv run pa-mcp --help        # MCP server (stdio by default, --http for streamable HTTP)
```

Configuration is by file and environment variable; the addresses in the docs and `hub/hub.example.toml` are documentation placeholders (`192.0.2.x`), so replace them with your own LAN addresses.

- `PA_MCP_HTTP_TOKEN`: bearer token required by the MCP HTTP server (`uv run pa-mcp gen-token` makes one)
- `PA_MCP_BIND_HOST`: address the MCP HTTP server binds to (default `127.0.0.1`)
- `PA_MCP_TLS_CERT` / `PA_MCP_TLS_KEY`: certificate and key for the MCP HTTP server
- `PA_HUB_CERT`: hub certificate the MCP server pins

Tokens and certificates are generated locally by `pa-hub init` and are gitignored; none are in the repository.

## Development

```
uv run pytest -q
uv run ruff check .
cd android && ./gradlew testDebugUnitTest
```

## Status

Version 0.1. It is built around one specific home lab (a Windows hub PC and three Raspberry Pis), so device names and roles in `hub.example.toml` are examples to adapt.
