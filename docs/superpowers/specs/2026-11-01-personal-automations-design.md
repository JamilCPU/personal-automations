# Personal Automations — Design

**Date:** 2026-11-01 · **Status:** draft, awaiting approval · **Owner:** Jamil

## 1. Intent

Jamil wants a **home control center on his Google Pixel**: one place to see every
machine in the home lab, see every program running on them, and switch things on
and off — starting with the Robot Pi's USB power and robot programs. It should also be
drivable by Claude (an MCP server), so "is the lidar on?" works from a chat.

This is the start of a broader **Personal Automations** system. The **jw PC** (Windows
workstation, 192.168.0.99, basically always on) is the home and host for Jamil's
automations generally; the Pis are devices it manages. This app is not part of the
home-studio / AlarmAPI app — that one is for media and his YouTube algorithm.

## 2. Naming and identity

- Product name: **Personal Automations**. App icon: a **wrench**.
- Its own GitHub repo: `JamilCPU/personal-automations` (monorepo — see §6).
- Device names as shown to the user: **jw PC**, **Robot Pi** (192.168.0.102 — never
  "Pi2" in the UI), **Pi1** (192.168.0.101), **Pi3** (192.168.0.103).

## 3. Goals

1. **Devices tab** — a card per machine (jw PC, Robot Pi, Pi1, Pi3) with online/offline
   and a health line (temperature, CPU, disk/uptime; GPU for the PC). Tap → device
   screen with device-level controls.
2. **Programs tab** — **every** program on every device, one card each, showing the
   program name and a tag for the device it runs on. Filter chips per device. A
   "hide system programs" toggle (on by default) hides OS/background noise.
3. **Program detail screen** — tap a program → status, uptime, kind (Docker container,
   system service, …), controls (start/stop/restart where allowed), recent log lines,
   and a history of actions taken through the app ("restarted from phone, yesterday 21:14").
4. **Robot Pi controls**
   - USB power on/off/status — *all four ports together* (Pi 5 hardware: 5 V only
     drops when every port is off; per-port control is not possible). Reuse the existing
     `lidar-power on|off|status` script (`/usr/local/bin/lidar-power`, symlinked into
     `~/SENTINEL-SENTRY/robot`), which also stops/starts the `lidar` container and waits
     for `/dev/ldlidar`. Ports always come back on after a reboot; the UI should say so.
   - Restart **all** robot programs (compose project `sentinel-ros`: `lidar`,
     `foxglove`, `rosbridge`) in one tap, or start/stop/restart any single one.
   - **Reboot** the Robot Pi, behind a confirmation.
5. **Reboot** for Pi1 and Pi3 as well, behind a confirmation (same mechanism).
6. **MCP server** exposing the same actions/reads to Claude.
7. Usable on home Wi-Fi from the Pixel.

## 4. Non-goals (v1)

- **No shutdown** for any device (a shut-down Pi can't be turned back on remotely).
- No "Open Foxglove"/robot-viewer button; no hosted Lichtblick. (Generic "open web page"
  links per program are fine to leave out of v1 too.)
- No access from outside the home network (Tailscale/WireGuard is a later step).
- No smart plugs, no physical PC power control, no Wake-on-LAN — noted as future ideas.
- No per-port USB control (hardware can't).
- No arbitrary shell / "run any command" anywhere.
- Not a replacement for Cockpit (still on Robot Pi :9090) or Mission Control
  (`~/mission-control`, an agent-ops dashboard — **do not modify it**).

## 5. Anti-goals (would make it a failure even if it "works")

- Anything reachable from the internet, or any endpoint usable without the secret key.
- An action that can lock Jamil out: stopping SSH, networking, Docker/containerd, k3s,
  the agent itself, or Pi-hole's DNS without an explicit, loud confirmation. These are
  **protected** (see §8).
- A cluttered Programs tab where the useful programs are buried in system noise.
- Calling the Robot Pi "Pi2" in the UI.

## 6. Architecture

```
 Pixel app ──HTTPS/HTTP + token──▶  Hub (jw PC)  ◀── MCP server (Claude)
                                     │
                     token, LAN only │
             ┌───────────────┬───────┴───────┬───────────────┐
         Agent: Robot Pi   Agent: Pi1      Agent: Pi3     Local: jw PC
```

- **Hub (jw PC).** The only thing the phone and MCP talk to. Holds the device list,
  aggregates status from agents, forwards allowed actions, records action history.
  Starts automatically with Windows. Also reports on the PC itself (it is the PC's agent).
- **Agent (one per Pi).** A tiny always-on service exposing a narrow API: health,
  list programs, logs, and the allowed actions. Accepts requests only from the hub
  (shared token; optionally also restrict to the hub's IP). Runs as a non-root user
  with narrow `sudo` rules for exactly what it needs (e.g. `uhubctl` via `lidar-power`,
  `systemctl restart/start/stop`, `systemctl reboot`).
  *Why agents instead of SSH from the hub:* SSH from Windows to the Pis only works via
  WSL today, and a narrow purpose-built agent is a smaller blast radius than handing the
  hub a full shell.
- **Android app (Pixel).** Native app, Kotlin + Jetpack Compose (matches the existing
  AlarmAPI app), two bottom tabs (Devices, Programs) plus device and program detail screens.
- **MCP server.** Thin wrapper over the hub's API — same token, same actions. Runs on
  the PC. **Jamil's decision:** it should be reachable by AI agents generally (Claude Code,
  Atlas and other agents on the lab network); the specific client doesn't matter. To keep
  the LAN-only rule, v1 offers it over the local network (stdio for local clients plus a
  LAN HTTP transport for agents on other lab machines); reaching it from the phone's
  Claude app waits for Tailscale. *(Transport split is Claude's call.)*
  **Jamil's decision:** MCP actions need **no human approval** — agents may reboot, cut
  USB power, stop/restart programs freely "for now". The only hard blocks that remain are
  the lockout protections in §8 (SSH and the agent/hub itself can't be stopped by anyone,
  app or agent), because those aren't permissions — they keep the system reachable.
  Every MCP action is still written to the history log, marked with which agent did it.
- **Repo layout (monorepo):** `hub/`, `agent/`, `mcp/`, `android/`, `docs/`.

## 7. What "programs" means per device

| Device | Listed | Controls |
|---|---|---|
| Pis | Docker containers (with compose project) + running systemd services | start / stop / restart / logs |
| jw PC | Docker containers (if Docker is running) + Windows services + running apps (processes with a window or notable long-running ones) | services/containers: start/stop/restart; plain processes: view + stop |

**k3s workloads.** All three Pis are in one k3s cluster (Pi1 = control plane; Robot Pi
and Pi3 = workers). Real programs run there as pods in the `lab` namespace: `ai-news`
and `gitea` (Pi1), `mission-control` (a DaemonSet — one pod on each Pi),
`openclaw-gateway` (one per Pi, host networking). These must appear as programs, tagged
with the node they run on. `kube-system` pods (coredns, traefik, metrics-server, …) count
as system programs. Controls for k3s workloads in v1: view, logs, **restart** (delete the
pod / rollout restart) only — no stop, since DaemonSets can't be scaled to zero and
stopping cluster workloads is easy to get wrong. *(Claude's call.)* k3s queries go through
the Pi1 agent (the control plane), which needs read + pod-delete rights scoped to the
`lab` namespace — not cluster-admin.

"System programs" (hidden by default) = OS plumbing such as dbus, getty, udev,
NetworkManager, Windows built-ins. The exact filter is the builder's call; it must
make Robot Pi show `lidar`, `foxglove`, `rosbridge`; Pi1 show Pi-hole; Pi3 show
ntopng, snapserver, code-server, redis, bluealsa; PC show things like ComfyUI.

## 8. Security

- **Secret key (token)** on every request: phone→hub, MCP→hub, hub→agent. Stored in
  config files / Android secure storage, never in the repo. A separate token for
  hub→agent from the phone/MCP one.
- **HTTPS everywhere** with a self-signed certificate made at install time, pinned in the
  Android app and the MCP client — so other devices on the Wi-Fi can't read the token.
  *(Claude's call.)*
- **LAN only.** Bind to the LAN interface; no port forwarding. Windows firewall rule
  scoped to the local subnet.
- **Fixed action list.** Actions are a closed set (status, list, logs, start, stop,
  restart, usb on/off, reboot). Program names are validated against what the agent
  itself discovered — never passed through to a shell.
- **Protected programs** (SSH, networking, Docker/containerd, k3s, the agent/hub
  itself, Pi-hole on Pi1): visible, but stop/restart is either blocked or requires a
  second explicit confirmation. Builder decides which, but SSH and the agent itself
  must not be stoppable from the app.
- **Confirmations** in the app for reboot, USB off, stopping anything.
- **History log** of every action (who: phone/MCP, what, when, result).

## 9. Constraints

- Robot Pi: Raspberry Pi 5, Debian 13 (trixie), Python 3.13; runs ROS 2 Jazzy stack in
  Docker; Cockpit on :9090, rosbridge on :9091, foxglove on :8765, some node app on
  :8080 — pick agent ports that don't collide. Installing the agent on the Robot Pi
  (a new service + sudoers rule) changes system config: **ask Jamil before deploying.**
  Don't reboot it during development without asking.
- Pi1 runs Pi-hole (the lab's DNS) — don't disrupt it while installing.
- SSH from the PC to Pis goes through WSL (`wsl.exe -e ssh jamil@<ip>`), user `jamil`.
- Pixel phone; app may be sideloaded (no Play Store requirement).

## 10. Decisions made by Claude (not explicitly chosen by Jamil)

- Hub + agent split (vs. hub using SSH).
- Native Kotlin/Compose app (vs. a web page pinned to the home screen).
- Hub/agent/MCP language: Python (FastAPI) — common across the lab's projects.
- Monorepo layout above.
- "Hide system programs" toggle, on by default.
- Reboot also offered for Pi1/Pi3.
- History log of actions.
- k3s workloads: view/logs/restart only (§7).
- HTTPS with a pinned self-signed certificate (§8).
- Hub starts at Windows logon as a scheduled task "run with highest privileges" in
  Jamil's session — so it can both see windowed apps (e.g. ComfyUI) and control Windows
  services. Not a SYSTEM service. (Jamil's PC is always on and logged in.)

## 11. Left to the builder

Ports, exact API shape, how health is sampled, the system-program filter, how the app
is themed, refresh/polling interval, how many
log lines to show, test strategy.

## 12. Future ideas (explicitly out of v1)

Tailscale for away-from-home use; Wake-on-LAN for the PC from a Pi; smart plugs to
power-cycle/monitor the PC; "open web page" links per program (Pi-hole admin, ntopng,
Cockpit); hosted Lichtblick robot viewer; home-screen widget / Quick Settings tile for
USB power; scheduled automations hosted on the hub.
