# Hub API

HTTPS on port 7700 (self-signed certificate; clients pin the SHA-256 fingerprint). All routes live under `/v1` and need `Authorization: Bearer <token>`. Each token identifies a client (`phone`, `mcp-stdio`, `mcp-http`), which is recorded in the action history. A client may add the header `X-PA-Agent: <name>` (matching `^[A-Za-z0-9._-]{1,40}$`); the history then records `<client>:<name>`, for example `mcp-http:claude-code`. An invalid or missing header records the token's client only. The MCP server sends its connected agent's `clientInfo.name` this way.

## Envelope

Every response, success or failure:

```json
{"success": true, "data": {...}, "error": null}
{"success": false, "data": null, "error": "Robot Pi is offline"}
```

## Errors

| Status | Meaning | `error` |
|--------|---------|---------|
| 401 | missing or wrong token | `unauthorized` |
| 404 | unknown device / program / group | `unknown device: x` |
| 409 | action not allowed (protection, or device lacks the capability) | reason |
| 422 | invalid request body or parameters | `invalid request` |
| 502 | the device agent answered with an error | `<Device name>: <agent error>` |
| 503 | device unreachable or timed out | `<Device name> is offline` |
| 500 | anything else | `internal error` |

Reads time out after 3 s (`timeout_s`); mutating calls after 30 s (`action_timeout_s`).

## Routes

| Method and path | Body | `data` |
|-----------------|------|--------|
| `GET /devices` | | `DeviceSummary[]` (id, name, role, ip, online, health, programs_running, programs_total, capabilities, usb) |
| `GET /devices/{id}` | | `DeviceSummary` |
| `GET /programs?device=&include_system=false` | | `Program[]` (k3s pods appear under the device they run on; pinned programs first) |
| `GET /program?device=&id=` | | `{program, history}` |
| `GET /logs?device=&id=&lines=100` | | `string[]` |
| `POST /actions` | `{device, id, action}` (`start`, `stop`, `restart`) | `{ok, message}` |
| `POST /devices/{id}/groups/{project}/restart` | | `{ok, message}` |
| `GET /devices/{id}/usb` | | `{on, detail}` (devices with the `usb` capability) |
| `PUT /devices/{id}/usb` | `{on}` | `{ok, message}` |
| `POST /devices/{id}/reboot` | | `{ok, message}` (devices with the `reboot` capability) |
| `PUT /pins` | `{device, id, pinned}` | `{ok, message}` (recorded in history as `pin` / `unpin`) |
| `GET /history?limit=50&device=` | | `HistoryEntry[]` (ts, client, device, target, action, ok, message) |

Program ids that start with `k3s:` are routed to the k3s source device (Pi1) whichever `device` is given.

### System programs and pinning

`system: true` programs are hidden unless `include_system=true`. Besides Linux plumbing and `kube-system` pods, on Windows that means: services that are stopped, live under `C:\Windows`, are Microsoft-shipped (`Program Files*\Microsoft\`, `ProgramData\Microsoft\`, `WindowsApps\Microsoft.*`, `Program Files\WSL`) or look like background helpers (`update`, `elevation`, `crash`, `telemetry`, `container` in the service or display name); windowed processes with a Microsoft-shipped executable; and the hub's own process tree. Windowed processes are named after their executable (`Discord`); the window title is the `description`.

Every `Program` carries `pin_key` (stable across restarts: the id, or `process:<name>` for processes) and `pinned`. `PUT /pins` resolves `id` on the device and stores `(device, pin_key)`; unknown device or program is a 404. Pinned programs are always listed, even when `system`, and sort first. A pinned program that is not running on an online device is listed as a placeholder: `id` `pinned:<pin_key>`, `status` `stopped`, `description` `Not running`, no actions. `GET /program` on a placeholder id returns 404; `PUT /pins` with `pinned: false` on it unpins. Device program counts include visible pinned programs.

## Pairing

`pa-hub pair --client phone` prints a QR code and this URI:

```
pa://pair?url=<url>&token=<token>&fp=<sha256>
```

Every value is percent-encoded (for example `url=https%3A%2F%2F192.0.2.197%3A7700`). `fp` is the lowercase hex SHA-256 of the hub certificate (DER). The app must trust only a certificate with that fingerprint.

## CLI

- `pa-hub init [--ip 192.0.2.197] [--out DIR]` writes `hub.toml`, `hub.secrets.toml` (tokens for `phone`, `mcp-stdio`, `mcp-http`), and `hub/cert.pem` + `hub/key.pem` (SANs: the IP and 127.0.0.1). `hub.toml` gets `bind = ["<ip>", "127.0.0.1"]`: the hub listens on the LAN IP and on loopback for local tools, not on every interface. `bind` also accepts a single string (for example `"0.0.0.0"`, then rely on the firewall rule). Default `DIR` is `%LOCALAPPDATA%\personal-automations`. Never overwrites.
- `pa-hub add-agent --device ID [--cert PATH --token T]` copies the agent's cert and token into `agents/<id>/`, or verifies they are already there.
- `pa-hub serve [--config hub.toml]`.
- `pa-hub pair --client NAME [--url URL]`.

Agents that are not deployed yet (no token or cert on disk) are reported as offline.

## Windows autostart

From an elevated PowerShell: `hub\deploy\install-hub.ps1` (task at logon, firewall rule for TCP 7700 and 7702 from 192.0.2.0/24, Private profile). `-Uninstall` removes both.

## MCP server

`pa-mcp` (package `mcp/`) exposes the same actions as MCP tools: `list_devices`, `get_device`, `list_programs`, `get_program`, `get_program_logs`, `program_action`, `restart_group`, `get_usb_power`, `set_usb_power`, `reboot_device`, `recent_history`, `pin_program`. Each returns the hub's `data` or raises a tool error carrying the hub's `error` text. There is no confirmation step.

Environment: `PA_HUB_URL` (for example `https://192.0.2.197:7700`), `PA_HUB_TOKEN` (the `mcp-stdio` client token for stdio, `mcp-http` for the HTTP server), `PA_HUB_CERT` (path to the hub `cert.pem`, pinned).

- stdio (local agents): `uv run --project <repo> pa-mcp`. Registered for Claude Code with `claude mcp add personal-automations -s user -e ... -- uv run --project <repo> pa-mcp`.
- Streamable HTTP (agents on other machines): `pa-mcp --http [--host 192.0.2.197] [--port 7702]` (the host defaults to the hub's LAN IP) serves `/mcp` over TLS and requires `Authorization: Bearer $PA_MCP_HTTP_TOKEN`; it refuses to start without that variable. Make a token with `pa-mcp gen-token`, then `setx PA_MCP_HTTP_TOKEN <token>`. `install-hub.ps1` also installs a "Personal Automations MCP" scheduled task for this (`run-mcp-http.ps1`).

The HTTP transport uses TLS: `PA_MCP_TLS_CERT` / `PA_MCP_TLS_KEY` default to the hub's `%LOCALAPPDATA%\personal-automations\hub\cert.pem` / `key.pem` (SANs 192.0.2.197 and 127.0.0.1), and `--http` refuses to start if either is missing. Clients connect to `https://192.0.2.197:7702/mcp` and pin that certificate.
