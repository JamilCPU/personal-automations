# Hub API

HTTPS on port 7700 (self-signed certificate; clients pin the SHA-256 fingerprint). All routes live under `/v1` and need `Authorization: Bearer <token>`. Each token identifies a client (`phone`, `mcp`), which is recorded in the action history.

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
| `GET /programs?device=&include_system=false` | | `Program[]` (k3s pods appear under the device they run on) |
| `GET /program?device=&id=` | | `{program, history}` |
| `GET /logs?device=&id=&lines=100` | | `string[]` |
| `POST /actions` | `{device, id, action}` (`start`, `stop`, `restart`) | `{ok, message}` |
| `POST /devices/{id}/groups/{project}/restart` | | `{ok, message}` |
| `GET /devices/{id}/usb` | | `{on, detail}` (devices with the `usb` capability) |
| `PUT /devices/{id}/usb` | `{on}` | `{ok, message}` |
| `POST /devices/{id}/reboot` | | `{ok, message}` (devices with the `reboot` capability) |
| `GET /history?limit=50&device=` | | `HistoryEntry[]` (ts, client, device, target, action, ok, message) |

Program ids that start with `k3s:` are routed to the k3s source device (Pi1) whichever `device` is given.

## Pairing

`pa-hub pair --client phone` prints a QR code and this URI:

```
pa://pair?url=<url>&token=<token>&fp=<sha256>
```

Every value is percent-encoded (for example `url=https%3A%2F%2F192.0.2.99%3A7700`). `fp` is the lowercase hex SHA-256 of the hub certificate (DER). The app must trust only a certificate with that fingerprint.

## CLI

- `pa-hub init [--ip 192.0.2.99] [--out DIR]` writes `hub.toml`, `hub.secrets.toml` (tokens for `phone`, `mcp`), and `hub/cert.pem` + `hub/key.pem` (SANs: the IP and 127.0.0.1). Default `DIR` is `%LOCALAPPDATA%\personal-automations`. Never overwrites.
- `pa-hub add-agent --device ID [--cert PATH --token T]` copies the agent's cert and token into `agents/<id>/`, or verifies they are already there.
- `pa-hub serve [--config hub.toml]`.
- `pa-hub pair --client NAME [--url URL]`.

Agents that are not deployed yet (no token or cert on disk) are reported as offline.

## Windows autostart

From an elevated PowerShell: `hub\deploy\install-hub.ps1` (task at logon, firewall rule for TCP 7700 and 7702 from 192.0.2.0/24, Private profile). `-Uninstall` removes both.
