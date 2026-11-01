# Personal Automations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A wrench-icon Android app on Jamil's Pixel, plus an MCP server, that show every device and program in the home lab and can start/stop/restart programs, switch the Robot Pi's USB power, and reboot the Pis, all through a hub on the jw PC.

**Architecture:** A small Python **agent** runs on each Pi and exposes a narrow HTTPS API (health, programs, logs, a fixed action set). The **hub** on the jw PC is the only thing clients talk to. It aggregates the agents, acts as the PC's own "agent" in-process, and logs every action to SQLite. The **MCP server** and the **Android app** are both clients of the hub API. Each client has its own token, so history knows who did what.

**Tech Stack:** Python 3.13, uv workspace, FastAPI + uvicorn, pydantic v2, httpx, psutil, cryptography, qrcode, `mcp` (official Python SDK, FastMCP), pytest + pytest-asyncio + respx, ruff. Android: Kotlin, Jetpack Compose (Material 3, BOM `2024.12.01`), Navigation Compose, Ktor client 3.1.1 (OkHttp engine) + kotlinx.serialization 1.7.3, DataStore Preferences, JUnit4 + `kotlinx-coroutines-test` + Ktor `MockEngine`. These match `F:\Dev\AlarmAPI`, whose `app/build.gradle.kts` is the reference for versions and layout.

**Spec:** `docs/superpowers/specs/2026-11-01-personal-automations-design.md`

## Global Constraints

- Product name **Personal Automations**; app icon a **wrench**; applicationId `com.jamilcpu.personalautomations`.
- Device ids and display names: `jw-pc` "jw PC" (192.0.2.99), `robot-pi` "Robot Pi" (192.0.2.102), `pi1` "Pi1" (192.0.2.101), `pi3` "Pi3" (192.0.2.103). Never show "Pi2" anywhere in the UI.
- k3s node → device map: `raspberrypi-1`→`pi1`, `raspberrypi-2`→`robot-pi`, `raspberrypi-3`→`pi3`.
- **No shutdown** action anywhere. No Foxglove/Lichtblick button. No arbitrary shell; every subprocess goes through `pa_core.runner.run(argv)` with a list argv, never `shell=True`.
- USB power on the Robot Pi is all-ports only, through the existing `/usr/local/bin/lidar-power on|off|status`. The UI says "All USB ports · back on after reboot".
- Robot compose project: `sentinel-ros` (services `lidar`, `foxglove`, `rosbridge`).
- k3s workloads: view, logs and **restart** only. Program ids use the owner name plus node, so they stay stable across pod restarts.
- **Locked** (no stop/restart by anyone): SSH (`ssh.service`, `sshd.service`, Windows `sshd`), `pa-agent.service`, the hub process, `docker.service`, `containerd.service`, `k3s.service`, `k3s-agent.service`, `NetworkManager.service`, `systemd-networkd.service`. **Confirm** (app asks twice, MCP does not): `pihole-FTL.service`.
- MCP actions need no human approval (Jamil's decision). Every action from any client is written to history.
- LAN only: HTTPS on the LAN with self-signed certs made at install time and pinned by every client. No port forwarding. Ports: hub `7700`, agent `7701`, MCP HTTP `7702`. They must not collide with the Robot Pi's 8080/8765/9090/9091.
- Secrets (tokens, keys) are never committed. `*.secrets.toml`, `*.pem` and `.env` are gitignored.
- Do not modify `~/mission-control` or its k3s workload definition. Restarting its pod from the app is allowed.
- Deploying the agent to the **Robot Pi** changes system config: stop and ask Jamil before Task 8's Robot Pi step. Never reboot a Pi during development without asking.
- Repo: `JamilCPU/personal-automations`, **private** (Claude's call: it contains lab topology). Root: `F:\Dev\personal-automations`.

## Review Focus

1. **A device is offline or slow.** The hub still returns every other device and its programs within ~3 s and marks the dead one `online=false`. One timeout must not fail the whole list. Test in Task 12.
2. **A stale or unknown program id** (a container removed since the list loaded, a typo, `docker:x;rm -rf /`) gets a 404 envelope with a clear message. It is never a 500 and never reaches a subprocess. Tests in Task 7 and Task 12.
3. **USB on with the lidar unplugged.** `lidar-power on` exits 1 with "did not appear". The action returns `ok=false` with that message, and the hub records a failed action. Test in Task 6.
4. **Reboot returns before the machine goes down.** The agent replies `ok=true` and runs the reboot about 2 s later. The hub then shows the device offline, then back. Test in Task 6.
5. **Missing or wrong token on any route**, including logs and the USB GET, gets a 401 envelope with no detail leak. The compare is constant-time. Tests in Task 7 and Task 13.

---

## File Structure

```
pyproject.toml                 uv workspace root (members core, agent, hub, mcp); ruff + pytest config
core/pa_core/
  models.py                    Program, Health, ActionResult, UsbState, ApiResponse envelope
  runner.py                    run(argv) — the only subprocess entry point
  policy.py                    system/locked/confirm classification + allowed actions
  tls.py                       self-signed cert generation + SHA-256 fingerprint
  auth.py                      bearer-token FastAPI dependency (constant-time)
agent/pa_agent/
  config.py                    AgentConfig from /etc/personal-automations/agent.toml
  sources/docker.py            list/act/logs for Docker containers
  sources/systemd.py           list/act/logs for systemd services
  sources/k3s.py               list/restart/logs for k3s pods (Pi1 only)
  device.py                    LinuxDevice: merges sources, health, usb, reboot
  app.py                       FastAPI app + `pa-agent` CLI (init, serve)
agent/deploy/                  deploy-agent.sh, pa-agent.service, sudoers, k3s-rbac.yaml
hub/pa_hub/
  config.py                    HubConfig from hub.toml + hub.secrets.toml
  clients.py                   DeviceClient protocol + RemoteAgentClient
  windows.py                   LocalWindowsDevice (services, windowed processes, docker, GPU)
  history.py                   HistoryStore (SQLite)
  service.py                   HubService: aggregation, timeouts, dispatch, history
  app.py                       FastAPI app + `pa-hub` CLI (init, serve, pair)
hub/deploy/                    install-hub.ps1 (scheduled task + firewall rule)
mcp/pa_mcp/server.py           FastMCP tools over the hub API; stdio + HTTP
android/                       Gradle project (app module), mirrors AlarmAPI layout
.github/workflows/ci.yml       python tests + android unit tests + APK artifact
```

---

## Part A — Core and Agent

### Task 1: Repo, workspace, core models and runner

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `core/pyproject.toml`, `core/pa_core/__init__.py`, `core/pa_core/models.py`, `core/pa_core/runner.py`
- Test: `core/tests/test_models.py`, `core/tests/test_runner.py`

**Interfaces:**
- Produces:
  - `Kind = Literal["docker","systemd","k3s","windows-service","process"]`
  - `Status = Literal["running","stopped","restarting","failed","unknown"]`
  - `ActionName = Literal["start","stop","restart"]`
  - `Protection = Literal["none","confirm","locked"]`
  - `class Program(BaseModel)`: `id: str` (`"{kind}:{name}"`, k3s `"k3s:{ns}/{owner}@{node}"`), `name: str` (display), `kind: Kind`, `device: str`, `status: Status`, `uptime_s: int | None`, `description: str = ""`, `system: bool`, `protection: Protection`, `actions: list[ActionName]`, `group: str | None` (compose project), `detail: dict[str, str] = {}`
  - `class Health(BaseModel)`: `cpu_pct: float`, `mem_pct: float`, `disk_pct: float`, `temp_c: float | None`, `uptime_s: int`, `gpu: dict[str, float] | None = None`
  - `class ActionResult(BaseModel)`: `ok: bool`, `message: str`
  - `class UsbState(BaseModel)`: `on: bool`, `detail: str`
  - `class ApiResponse(BaseModel, Generic[T])`: `success: bool`, `data: T | None`, `error: str | None`; helpers `ok(data)` and `fail(msg)`
  - `run(argv: list[str], timeout: float = 30) -> RunResult` where `RunResult(returncode: int, stdout: str, stderr: str)`. It raises `TypeError` if `argv` is not a list of str. A timeout gives `returncode=124, stderr="timed out"`.
  - `Runner = Callable[[list[str]], RunResult]`. Every source takes a `Runner`, so tests inject fakes.

- [ ] **Step 1:** Create the GitHub repo and local checkout: `gh repo create JamilCPU/personal-automations --private --source F:\Dev\personal-automations --remote origin` (after `git init -b main`). Add the `.gitignore` entries from Global Constraints, plus `.venv/`, `__pycache__/`, `.superpowers/`, `android/build/`, `android/app/build/`, `.gradle/`, `local.properties`. The spec and plan already in `docs/` go in the first commit.
- [ ] **Step 2:** Write the uv workspace root (`[tool.uv.workspace] members = ["core","agent","hub","mcp"]`, dev deps pytest, pytest-asyncio, respx, ruff) and `core/pyproject.toml` (deps: pydantic>=2.9, cryptography>=43, fastapi>=0.115).
- [ ] **Step 3: Write the failing tests.** `test_program_roundtrip` (a k3s Program serializes and parses back unchanged). `test_envelope_ok_and_fail` (`ok(1).model_dump() == {"success": True, "data": 1, "error": None}`; `fail("x")` gives `success False, data None, error "x"`). `test_run_rejects_string_argv` (`run("ls")` raises TypeError). `test_run_captures_output` (`run([sys.executable, "-c", "print('hi')"]).stdout.strip() == "hi"`). `test_run_timeout` (a sleep longer than `timeout=0.5` gives returncode 124).
- [ ] **Step 4:** `uv run pytest core -q`. Expected: FAIL (module not found).
- [ ] **Step 5:** Implement `models.py` and `runner.py` (`subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)`).
- [ ] **Step 6:** `uv run pytest core -q && uv run ruff check .`. Expected: all pass, no lint errors.
- [ ] **Step 7:** Commit `feat: workspace, core models and safe runner` and push.

### Task 2: Policy (system filter, locked/confirm, allowed actions)

**Files:**
- Create: `core/pa_core/policy.py`
- Test: `core/tests/test_policy.py`

**Interfaces:**
- Consumes: `Kind`, `Status`, `ActionName`, `Protection` (Task 1)
- Produces:
  - `LOCKED: frozenset[str]` (the exact names from Global Constraints, including `"pa-agent.service"` and the Windows service `"sshd"`), `CONFIRM: frozenset[str] = {"pihole-FTL.service"}`
  - `protection_for(kind: Kind, name: str, extra_locked: frozenset[str] = frozenset()) -> Protection`
  - `is_system(kind: Kind, name: str, namespace: str | None = None) -> bool`
  - `allowed_actions(kind: Kind, status: Status, protection: Protection) -> list[ActionName]`
  - `class ActionNotAllowed(Exception)` and `check_action(program: Program, action: ActionName) -> None`, which raises if the action is not in `program.actions`

Matrix (copy exactly): locked gives `[]`. docker, systemd and windows-service give `["stop","restart"]` when running and `["start"]` otherwise. k3s gives `["restart"]`. process gives `["stop"]` when running and `[]` otherwise.

`is_system` rules:
- systemd: true for the Debian/systemd plumbing set. At minimum `dbus`, `getty@*`, `systemd-*`, `udev`/`systemd-udevd`, `polkit`, `cron`, `rsyslog`, `avahi-daemon`, `wpa_supplicant`, `ModemManager`, `NetworkManager`, `bluetooth`, `user@*`, `serial-getty@*`, `triggerhappy`, `rng-tools*`, `dphys-swapfile`, `alsa-*`, `rtkit-daemon`, `udisks2`, `accounts-daemon`, `colord`, `cups*`, `lightdm`, `upower`, `packagekit`, `unattended-upgrades`, `chrony`, `rpcbind`, `nfs-*`, `containerd`, `docker`, `ssh`/`sshd`, `k3s*`.
- k3s: true when namespace is `kube-system`.
- docker: never.
- windows-service: true unless the service's binary path is outside `C:\Windows\`.
- process: never (Task 10 already filters).

- [ ] **Step 1: Write the failing tests:**
  - `test_locked_has_no_actions` (`allowed_actions("systemd","running","locked") == []`)
  - `test_running_service_actions` (`== ["stop","restart"]`)
  - `test_stopped_container_actions` (`allowed_actions("docker","stopped","none") == ["start"]`)
  - `test_k3s_restart_only`
  - `test_process_stop_only`
  - `test_ssh_and_agent_locked` (`protection_for("systemd","ssh.service") == "locked"`; same for `pa-agent.service`)
  - `test_pihole_confirm`
  - `test_is_system_examples`: `getty@tty1.service` True; `ntopng.service`, `snapserver.service`, `code-server@jamil.service`, `redis-server.service`, `bluealsa-aplay.service`, `pihole-FTL.service` False; k3s kube-system True, lab False
  - `test_check_action_raises` (stop on a locked program raises `ActionNotAllowed`)
- [ ] **Step 2:** `uv run pytest core/tests/test_policy.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement `policy.py` (use `fnmatch` for the patterns).
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit `feat: program policy (system filter, locked, allowed actions)`.

### Task 3: Docker source

**Files:**
- Create: `agent/pyproject.toml` (deps: pa-core, fastapi, uvicorn[standard], psutil; script `pa-agent = "pa_agent.app:main"`), `agent/pa_agent/__init__.py`, `agent/pa_agent/sources/__init__.py`, `agent/pa_agent/sources/docker.py`
- Test: `agent/tests/test_docker.py`, `agent/tests/fixtures/docker_ps.jsonl`

**Interfaces:**
- Consumes: `Runner`, `Program`, `policy.*`
- Produces:
  - `list_docker(run: Runner, device: str) -> list[Program]`. Runs `docker ps -a --no-trunc --format "{{json .}}"`. The id is `docker:{Names}` and the name is the compose service label when present (`com.docker.compose.service`), otherwise `Names`. `group` comes from the `com.docker.compose.project` label. Status mapping: `State` running→running, exited/created→stopped, restarting→restarting, dead→failed. `uptime_s` is parsed from `RunningFor`/`Status` when running, `None` otherwise. If the docker CLI is missing or the daemon is down (non-zero exit), it returns `[]`.
  - `docker_action(run: Runner, container: str, action: ActionName) -> ActionResult` (`docker {action} {container}`)
  - `docker_group_restart(run: Runner, project: str, containers: list[str]) -> ActionResult` (`docker restart c1 c2 …`, the containers belonging to that project)
  - `docker_logs(run: Runner, container: str, lines: int) -> list[str]` (`docker logs --tail {lines} {container}`, stdout+stderr merged, split on lines)

- [ ] **Step 1:** Record a real fixture: `wsl.exe -e ssh user@192.0.2.102 'docker ps -a --no-trunc --format "{{json .}}"' > agent/tests/fixtures/docker_ps.jsonl`.
- [ ] **Step 2: Write failing tests:**
  - `test_lists_robot_containers` (with the fixture: names `{"lidar","foxglove","rosbridge"}`, all `group == "sentinel-ros"`, ids start with `docker:sentinel-ros-`)
  - `test_docker_down_returns_empty` (fake runner with returncode 1 gives `[]`)
  - `test_action_argv` (the fake runner records `["docker","restart","sentinel-ros-lidar-1"]`; a non-zero exit gives `ok=False` and the message is stderr)
  - `test_logs_tail` (argv includes `--tail`, `"50"`)
- [ ] **Step 3:** `uv run pytest agent/tests/test_docker.py -q`. Expected: FAIL.
- [ ] **Step 4:** Implement `docker.py`.
- [ ] **Step 5:** Run again. Expected: PASS.
- [ ] **Step 6:** Commit `feat(agent): docker source`.

### Task 4: systemd source

**Files:**
- Create: `agent/pa_agent/sources/systemd.py`
- Test: `agent/tests/test_systemd.py`, `agent/tests/fixtures/systemctl_units.json`

**Interfaces:**
- Produces:
  - `list_systemd(run: Runner, device: str) -> list[Program]`. Runs `systemctl list-units --type=service --all --output=json --no-pager` (systemd 257 on all Pis). It skips units whose `load` is `not-found`. Status mapping: `active/running`→running, `inactive`→stopped, `failed`→failed, `activating`/`reloading`→restarting. `uptime_s` is left `None` here; `device.py` fills it via `systemctl show -p ActiveEnterTimestampMonotonic` only when one program is requested. `description` comes from the unit's `description`.
  - `systemd_action(run: Runner, unit: str, action: ActionName) -> ActionResult` (`sudo -n systemctl {action} {unit}`)
  - `systemd_logs(run: Runner, unit: str, lines: int) -> list[str]` (`journalctl -u {unit} -n {lines} --no-pager -o short-iso`)

- [ ] **Step 1:** Record the fixture from Pi3: `wsl.exe -e ssh user@192.0.2.103 'systemctl list-units --type=service --all --output=json --no-pager' > agent/tests/fixtures/systemctl_units.json`.
- [ ] **Step 2: Write failing tests:**
  - `test_pi3_programs_visible` (non-system names include `ntopng.service`, `snapserver.service`, `code-server@jamil.service`, `redis-server.service`, `bluealsa-aplay.service`)
  - `test_ssh_locked_no_actions`
  - `test_failed_unit_status`
  - `test_action_uses_sudo_n` (argv `["sudo","-n","systemctl","restart","ntopng.service"]`)
- [ ] **Step 3:** Run. Expected: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run. Expected: PASS.
- [ ] **Step 6:** Commit `feat(agent): systemd source`.

### Task 5: k3s source

**Files:**
- Create: `agent/pa_agent/sources/k3s.py`
- Test: `agent/tests/test_k3s.py`, `agent/tests/fixtures/k3s_pods.json`

**Interfaces:**
- Produces:
  - `list_k3s(run: Runner, kubeconfig: str, node_devices: dict[str, str]) -> list[Program]`. Runs `k3s kubectl --kubeconfig {kubeconfig} get pods -A -o json`. It skips pods in phase `Succeeded` (finished helm jobs). The owner is `ownerReferences[0].name`, with the ReplicaSet hash suffix stripped for Deployments (`gitea-86668d4b65` → `gitea`). The id is `k3s:{ns}/{owner}@{nodeName}`, the name is `{owner}`, and `device = node_devices[nodeName]`. `detail` holds `{"pod": podName, "namespace": ns, "node": nodeName, "restarts": n}`.
  - `k3s_restart(run: Runner, kubeconfig: str, namespace: str, pod: str) -> ActionResult` (`k3s kubectl … delete pod -n {ns} {pod} --wait=false`)
  - `k3s_logs(run: Runner, kubeconfig: str, namespace: str, pod: str, lines: int) -> list[str]` (`… logs -n {ns} {pod} --tail {lines}`)

- [ ] **Step 1:** Record the fixture from Pi1: `wsl.exe -e ssh user@192.0.2.101 'sudo k3s kubectl get pods -A -o json' > agent/tests/fixtures/k3s_pods.json`.
- [ ] **Step 2: Write failing tests:**
  - `test_lab_workloads` (ids include `k3s:lab/gitea@raspberrypi-1`, `k3s:lab/ai-news@raspberrypi-1`, `k3s:lab/mission-control@raspberrypi-2` with `device == "robot-pi"`, and `k3s:lab/openclaw-gateway@raspberrypi-3`)
  - `test_kube_system_is_system`
  - `test_completed_jobs_skipped` (no `helm-install-*`)
  - `test_only_restart_allowed`
  - `test_restart_argv_uses_current_pod_name`
- [ ] **Step 3:** Run. Expected: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run. Expected: PASS.
- [ ] **Step 6:** Commit `feat(agent): k3s source`.

### Task 6: LinuxDevice (merge, health, USB, reboot)

**Files:**
- Create: `agent/pa_agent/config.py`, `agent/pa_agent/device.py`
- Test: `agent/tests/test_device.py`

**Interfaces:**
- Consumes: Tasks 3–5 sources, `policy.check_action`
- Produces:
  - `class AgentConfig(BaseModel)`: `device_id: str`, `token: str`, `bind: str = "0.0.0.0"`, `port: int = 7701`, `cert_file: str`, `key_file: str`, `usb_script: str | None = None`, `kubeconfig: str | None = None`, `node_devices: dict[str,str]`, `hub_ip: str | None = None` (when set, requests from other IPs get 403). `load_config(path: str) -> AgentConfig` reads TOML.
  - `class LinuxDevice`: `__init__(cfg: AgentConfig, run: Runner = run, reboot_delay_s: float = 2.0, scheduler: Callable[[float, Callable[[], None]], None] = threading_timer)` — `threading_timer(delay, fn)` is a module-level helper in `device.py` that starts a daemon `threading.Timer`
    - `programs() -> list[Program]` (docker + systemd + k3s when `kubeconfig` is set)
    - `program(pid: str) -> Program` (raises `KeyError` for an unknown id; looks up the id in a fresh `programs()` list only)
    - `logs(pid: str, lines: int = 100) -> list[str]` (`lines` clamped to 1..500)
    - `act(pid: str, action: ActionName) -> ActionResult` (`program()` → `check_action` → dispatch by kind)
    - `group_restart(project: str) -> ActionResult` (`KeyError` if there are no containers with that group)
    - `health() -> Health` (psutil; `temp_c` from `/sys/class/thermal/thermal_zone0/temp` / 1000)
    - `usb() -> UsbState` and `set_usb(on: bool) -> ActionResult`, both via `sudo -n {usb_script} status|on|off`. They raise `NotImplementedError` when `usb_script` is None. `on` is parsed from the status line `Lidar: on`.
    - `reboot() -> ActionResult` schedules `["sudo","-n","systemctl","reboot"]` after `reboot_delay_s` and returns `ok=True, "Rebooting in 2 s"` immediately.

- [ ] **Step 1: Write failing tests** (fake runner keyed by argv prefix, fake scheduler that records instead of waiting):
  - `test_unknown_program_keyerror` (`act("docker:x;rm -rf /","stop")` raises KeyError and the runner never sees that string)
  - `test_locked_action_refused` (`act("systemd:ssh.service","stop")` raises ActionNotAllowed)
  - `test_usb_on_failure_message` (the fake `lidar-power on` returns 1 with stderr `Error: /dev/ldlidar did not appear after power-on. Is the lidar plugged in?`, giving `ok=False` and a message containing "did not appear") [Review Focus 3]
  - `test_usb_status_parse`
  - `test_reboot_is_deferred` (after `reboot()` the runner has not been called with `systemctl reboot`, and the scheduler recorded delay 2.0 plus a callable that issues it) [Review Focus 4]
  - `test_group_restart_robot` (restarts exactly the three sentinel-ros containers)
  - `test_no_usb_script_not_implemented`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `config.py` and `device.py`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(agent): LinuxDevice with health, usb, reboot`.

### Task 7: Agent HTTP API, auth, TLS, CLI

**Files:**
- Create: `core/pa_core/tls.py`, `core/pa_core/auth.py`, `agent/pa_agent/app.py`
- Test: `core/tests/test_tls.py`, `agent/tests/test_app.py`

**Interfaces:**
- Produces:
  - `make_self_signed(common_name: str, ips: list[str], days: int = 3650) -> tuple[bytes, bytes]` (cert PEM, key PEM; SAN includes every IP), and `fingerprint_sha256(cert_pem: bytes) -> str` (lowercase hex, no colons)
  - `bearer_auth(tokens: dict[str, str]) -> Depends`. `tokens` maps token→client name. It uses `hmac.compare_digest` across all tokens and returns the client name. A failure gives HTTP 401 with body `fail("unauthorized")`.
  - `create_app(device: LinuxDevice, cfg: AgentConfig) -> FastAPI`. Routes (all under `/v1`, all return `ApiResponse`):
    - `GET /health` → Health
    - `GET /programs` → list[Program]
    - `GET /program?id=` → Program (404 `fail("unknown program: …")` on KeyError)
    - `GET /logs?id=&lines=` → list[str]
    - `POST /actions` body `{id, action}` → ActionResult (404 unknown id, 409 ActionNotAllowed)
    - `POST /groups/{project}/restart` → ActionResult
    - `GET /usb` → UsbState, `PUT /usb` body `{on}` → ActionResult (404 when not supported)
    - `POST /reboot` → ActionResult
    - Unhandled errors give 500 `fail("internal error")` with the traceback logged server-side only.
  - CLI `pa-agent init --device-id ID --ip IP [--usb-script PATH] [--kubeconfig PATH] --out DIR` writes `agent.toml`, `cert.pem`, `key.pem` and a random 32-byte urlsafe token, then prints the cert fingerprint and token once. `pa-agent serve --config PATH` runs uvicorn with TLS.

- [ ] **Step 1: Write failing tests** (FastAPI `TestClient`, `LinuxDevice` with a fake runner):
  - `test_tls_san_and_fingerprint` (the cert parsed with `cryptography` has the IP SAN; the fingerprint is 64 hex chars)
  - `test_every_route_requires_token`: parametrized over all routes; no header gives 401, a wrong token gives 401, and the body is exactly `{"success":false,"data":null,"error":"unauthorized"}` [Review Focus 5]
  - `test_unknown_program_404` (`POST /v1/actions {"id":"docker:x;rm -rf /","action":"stop"}` gives 404 and the runner received no docker call) [Review Focus 2]
  - `test_locked_409`
  - `test_programs_envelope`
  - `test_hub_ip_restriction` (`hub_ip` set and the request from a different client host gives 403)
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `tls.py`, `auth.py`, `app.py`.
- [ ] **Step 4:** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat(agent): HTTPS API with token auth and CLI`.

### Task 8: Agent deployment to the Pis

**Files:**
- Create: `agent/deploy/deploy-agent.sh`, `agent/deploy/pa-agent.service`, `agent/deploy/personal-automations.sudoers`, `agent/deploy/k3s-rbac.yaml`, `docs/DEPLOY.md`

**Interfaces:**
- Produces: on each Pi, a `pa-agent` system user (groups `docker`, `systemd-journal`), `/opt/personal-automations/venv`, `/etc/personal-automations/{agent.toml,cert.pem,key.pem[,kubeconfig]}` (mode 600, owner pa-agent), and `pa-agent.service` enabled. It listens on `:7701`. The hub (Task 9) reads each Pi's `cert.pem` and token.

Decisions:
- `deploy-agent.sh <ip> <device-id> [--usb-script /usr/local/bin/lidar-power] [--k3s]` runs from WSL. It does `uv build` for core and agent, scp's the wheels plus the deploy files, then over ssh: creates the user, the venv (`python3 -m venv`), `pip install` the wheels, runs `pa-agent init` (first time only, never overwriting an existing config), and installs the unit and sudoers (checked with `visudo -cf` before moving it into `/etc/sudoers.d/`). Finally it starts the unit and prints the fingerprint and token. It runs `sudo -n`, never interactively.
- Sudoers allows `pa-agent` exactly: `/usr/bin/systemctl start *`, `stop *`, `restart *`, `/usr/bin/systemctl reboot`, `/usr/local/bin/lidar-power on|off|status`. The agent's policy is what blocks locked units; sudo is the second fence for everything else.
- `k3s-rbac.yaml`: ServiceAccount `pa-agent` in `lab`; a Role in `lab` with pods get/list/delete and pods/log get; a ClusterRole `pa-agent-read` with pods get/list plus a ClusterRoleBinding (read across namespaces). A token Secret for the SA. `deploy-agent.sh --k3s` builds `/etc/personal-automations/kubeconfig` from that token.
- The unit runs with `User=pa-agent`, `Restart=always`, `NoNewPrivileges=false` (sudo needs it), and `After=network-online.target docker.service`.

- [ ] **Step 1:** Write the deploy files and `docs/DEPLOY.md` (what each file does, how to roll back: `systemctl disable --now pa-agent; rm /etc/sudoers.d/personal-automations`).
- [ ] **Step 2:** Deploy to **Pi3**: `bash agent/deploy/deploy-agent.sh 192.0.2.103 pi3`. Verify from the PC: `curl -sk -H "Authorization: Bearer <token>" https://192.0.2.103:7701/v1/programs` returns `success: true` and includes `ntopng.service`. Without the header it returns 401.
- [ ] **Step 3:** Deploy to **Pi1** with `--k3s`. Verify `/v1/programs` includes `k3s:lab/gitea@raspberrypi-1` and `k3s:lab/mission-control@raspberrypi-2`, and that `pihole-FTL.service` shows `"protection":"confirm"`. Confirm DNS still resolves (`nslookup github.com 192.0.2.101`).
- [ ] **Step 4: STOP — ask Jamil** before installing on the Robot Pi (it adds a user, a service and a sudoers file). Once he approves: `deploy-agent.sh 192.0.2.102 robot-pi --usb-script /usr/local/bin/lidar-power`. Verify `GET /v1/usb` gives `on: true` and the programs include lidar, foxglove and rosbridge. Do not test reboot or USB off without asking.
- [ ] **Step 5:** Commit `feat(agent): deployment scripts` (no secrets; check with `git status` that no `.pem`/token was staged).

---

## Part B — Hub (jw PC)

### Task 9: Hub config and remote agent client

**Files:**
- Create: `hub/pyproject.toml` (deps: pa-core, fastapi, uvicorn[standard], httpx, psutil, qrcode; script `pa-hub = "pa_hub.app:main"`), `hub/pa_hub/__init__.py`, `hub/pa_hub/config.py`, `hub/pa_hub/clients.py`, `hub/hub.example.toml`
- Test: `hub/tests/test_clients.py`

**Interfaces:**
- Produces:
  - `class DeviceEntry(BaseModel)`: `id`, `name`, `role: str` (subtitle: "Workstation · the hub", "Sentinel robot", "Network", "Gateway & audio"), `ip`, `kind: Literal["agent","local"]`, `url: str | None`, `cert_file: str | None`, `token: str | None`
  - `class HubConfig(BaseModel)`: `bind`, `port=7700`, `cert_file`, `key_file`, `clients: dict[str,str]` (token→client name, e.g. `phone`, `mcp`), `devices: list[DeviceEntry]`, `db_path: str` (default `%LOCALAPPDATA%\personal-automations\history.db`), `timeout_s: float = 3.0`. `load_hub_config(path, secrets_path) -> HubConfig` merges `hub.toml` with `hub.secrets.toml` (tokens).
  - `class DeviceClient(Protocol)` with async methods matching LinuxDevice: `health()`, `programs()`, `program(pid)`, `logs(pid, lines)`, `act(pid, action)`, `group_restart(project)`, `usb()`, `set_usb(on)`, `reboot()`. Plus the attribute `capabilities: set[str]` (`"usb"`, `"reboot"`).
  - `class RemoteAgentClient(DeviceClient)`: `__init__(entry: DeviceEntry, timeout_s: float)`. httpx.AsyncClient with `verify=ssl context loaded from entry.cert_file`, which pins the agent's own cert. It unwraps the envelope: HTTP 404 becomes `KeyError(msg)`, 409 becomes `ActionNotAllowed(msg)`, and timeouts/connection errors become `DeviceOffline(device_id)`. Capabilities come from the device entry (`usb` only for robot-pi; `reboot` for all agents).
  - `class DeviceOffline(Exception)`

- [ ] **Step 1: Write failing tests** with respx:
  - `test_unwraps_programs`
  - `test_404_to_keyerror`
  - `test_409_to_not_allowed`
  - `test_timeout_to_offline` (respx side_effect `httpx.ConnectTimeout`)
  - `test_sends_bearer`
  - `test_config_merges_secrets` (example toml plus a temp secrets file gives tokens filled)
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(hub): config and pinned agent client`.

### Task 10: LocalWindowsDevice

**Files:**
- Create: `hub/pa_hub/windows.py`
- Test: `hub/tests/test_windows.py`

**Interfaces:**
- Consumes: `Runner`, `policy`, `docker.list_docker`/`docker_action`/`docker_logs` (reuse from pa_agent: move `sources/docker.py` into `pa_core/docker.py` in this task and update the agent import, so the hub does not depend on pa_agent)
- Produces: `class LocalWindowsDevice(DeviceClient)`: `__init__(device_id="jw-pc", run=run, hub_pid=os.getpid(), procs=psutil, services=psutil.win_service_iter, windowed_pids=enum_windowed_pids)`. Capabilities: none (no USB, no reboot of the hub machine). Its `DeviceClient` methods are `async` and run the blocking psutil/`sc.exe` work via `asyncio.to_thread`.
  - Programs:
    - Windows services (`windows-service:{name}`, display name from `display_name()`, system per policy).
    - Docker containers if `docker info` succeeds.
    - Processes (`process:{name}#{pid}`) for PIDs with a visible top-level window (ctypes `EnumWindows`/`IsWindowVisible`/`GetWindowThreadProcessId`), or `python.exe`/`node.exe`/`java.exe` running ≥10 min. Process name: the window title, or for python/node the script path's basename. ComfyUI therefore shows as `main.py` → use the parent folder when the script is `main.py` (`ComfyUI`). The hub's own pid is locked.
  - Service actions: `sc.exe start|stop {name}`. Restart is stop, wait up to 15 s for STOPPED, then start.
  - Process stop: `psutil.Process(pid).terminate()`.
  - Logs: services give `[]` with message "Logs not available for Windows services". Processes give `[]`.
  - Health: psutil cpu/mem/disk(`C:\`), uptime from boot time, `gpu` from `nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits` (None if it fails).

- [ ] **Step 1: Write failing tests** with fakes for psutil, services and windowed_pids:
  - `test_comfyui_named_from_folder` (process `python.exe` with cmdline `["python","F:\\ComfyUI\\main.py"]` gives name `ComfyUI`)
  - `test_hub_process_locked`
  - `test_service_restart_sequence` (argv order: `sc.exe stop X`, `sc.exe query X` polls, `sc.exe start X`)
  - `test_gpu_parse` (`"41, 3, 1200, 24576"` gives `{"temp_c":41,"util_pct":3,"mem_used_mb":1200,"mem_total_mb":24576}`)
  - `test_no_docker_ok` (`docker info` failing still returns services/processes)
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement (plus the docker module move, with agent tests still green).
- [ ] **Step 4:** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(hub): local Windows device`.

### Task 11: HistoryStore

**Files:**
- Create: `hub/pa_hub/history.py`
- Test: `hub/tests/test_history.py`

**Interfaces:**
- Produces:
  - `class HistoryEntry(BaseModel)`: `ts: datetime` (UTC), `client: str`, `device: str`, `target: str` (program id, `"usb"`, `"reboot"`, or `"group:{project}"`), `action: str`, `ok: bool`, `message: str`
  - `class HistoryStore`: `__init__(db_path: str)` (creates the table and parent dir), `add(entry: HistoryEntry) -> None`, `recent(limit: int = 50, device: str | None = None, target: str | None = None) -> list[HistoryEntry]` (newest first, `limit` clamped to 1..500). Parameterized SQL only.

- [ ] **Step 1: Write failing tests:** `test_add_and_recent_order`, `test_filter_by_target`, `test_limit_clamped`, `test_survives_reopen` (tmp_path).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement with `sqlite3`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(hub): action history store`.

### Task 12: HubService

**Files:**
- Create: `hub/pa_hub/service.py`
- Test: `hub/tests/test_service.py`

**Interfaces:**
- Consumes: `DeviceClient`, `DeviceOffline`, `HistoryStore`, `HubConfig`
- Produces:
  - `class DeviceSummary(BaseModel)`: `id`, `name`, `role`, `ip`, `online: bool`, `health: Health | None`, `programs_running: int`, `programs_total: int` (non-system only), `capabilities: list[str]`, `usb: UsbState | None`
  - `class ProgramDetail(BaseModel)`: `program: Program`, `history: list[HistoryEntry]` (last 10 for that target)
  - `class HubService`: `__init__(devices: dict[str, DeviceClient], entries: dict[str, DeviceEntry], history: HistoryStore, timeout_s: float)`
    - `async devices() -> list[DeviceSummary]` (fans out with `asyncio.gather` and a per-device `asyncio.wait_for(timeout_s)`; offline gives `online=False`, zero counts)
    - `async device(device_id) -> DeviceSummary` (KeyError for an unknown id)
    - `async programs(device: str | None, include_system: bool) -> list[Program]` (offline devices contribute nothing; sorted by device order in the config, then name)
    - `async program(device_id, pid) -> ProgramDetail`
    - `async logs(device_id, pid, lines) -> list[str]`
    - `async act(client, device_id, pid, action) -> ActionResult`, `async group_restart(client, device_id, project)`, `async set_usb(client, device_id, on)`, `async usb(device_id)`, `async reboot(client, device_id)`. Every mutating call writes one HistoryEntry, ok or not (KeyError/ActionNotAllowed/DeviceOffline are recorded as `ok=False` and then re-raised). Reboot and USB raise `ActionNotAllowed` when the device lacks the capability.
    - `async recent_history(limit, device=None) -> list[HistoryEntry]`

- [ ] **Step 1: Write failing tests** with in-memory fake DeviceClients:
  - `test_one_offline_device_does_not_block` (one fake sleeps 10 s and timeout_s=0.2: `devices()` returns in <1 s, that device is `online=False`, the others are online) [Review Focus 1]
  - `test_programs_skip_offline`
  - `test_include_system_filter`
  - `test_action_recorded_with_client` (`act("phone",…)` gives a history entry with client `phone`)
  - `test_failed_action_recorded_and_raised` (KeyError gives `ok=False` entry, then raises) [Review Focus 2]
  - `test_usb_on_non_robot_not_allowed`
  - `test_counts_exclude_system`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(hub): aggregation service with history`.

### Task 13: Hub HTTP API, pairing, Windows install

**Files:**
- Create: `hub/pa_hub/app.py`, `hub/deploy/install-hub.ps1`, `docs/API.md`
- Test: `hub/tests/test_app.py`

**Interfaces:**
- Consumes: `HubService`, `bearer_auth`, `make_self_signed`
- Produces (all under `/v1`, envelope responses, `client = bearer_auth(cfg.clients)`). This is the contract the MCP server and Android app code against:
  - `GET /devices` → list[DeviceSummary]; `GET /devices/{id}` → DeviceSummary
  - `GET /programs?device=&include_system=false` → list[Program]
  - `GET /program?device=&id=` → ProgramDetail
  - `GET /logs?device=&id=&lines=100` → list[str]
  - `POST /actions` body `{device, id, action}` → ActionResult
  - `POST /devices/{id}/groups/{project}/restart` → ActionResult
  - `GET /devices/{id}/usb` → UsbState; `PUT /devices/{id}/usb` body `{on}` → ActionResult
  - `POST /devices/{id}/reboot` → ActionResult
  - `GET /history?limit=50&device=` → list[HistoryEntry]
  - Error mapping: KeyError → 404, ActionNotAllowed → 409, DeviceOffline → 503 `fail("{name} is offline")`, anything else → 500 `fail("internal error")`
- CLI:
  - `pa-hub init --ip 192.0.2.99 --out %LOCALAPPDATA%\personal-automations` writes `hub.toml` from `hub.example.toml` (4 devices from Global Constraints), `hub.secrets.toml` (new tokens for `phone` and `mcp`), `cert.pem`, `key.pem`. Each agent's token and cert are added with `pa-hub add-agent --device pi3 --cert path --token T`.
  - `pa-hub serve` starts the hub.
  - `pa-hub pair --client phone` prints, in the terminal only, a QR code for `pa://pair?url=https://192.0.2.99:7700&token={token}&fp={sha256}` plus the same text.
- `install-hub.ps1`:
  - registers a Scheduled Task "Personal Automations Hub": at logon of the current user, "Run with highest privileges", restart on failure, running `uv run --project F:\Dev\personal-automations pa-hub serve`
  - adds a firewall rule allowing TCP 7700 and 7702 inbound from `192.0.2.0/24` only, Private profile

- [ ] **Step 1: Write failing tests** (TestClient over a HubService with fakes):
  - `test_routes_require_token` (parametrized over all routes) [Review Focus 5]
  - `test_client_name_from_token` (an action with the mcp token gives a history entry with client `mcp`)
  - `test_offline_503_message` (`"Robot Pi is offline"`)
  - `test_unknown_device_404`
  - `test_pair_uri_format` (the function `pair_uri(url, token, fp)` returns exactly the scheme above, URL-encoded)
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `app.py` and `install-hub.ps1`. Write `docs/API.md` (routes, envelope, error codes).
- [ ] **Step 4:** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Live check.** `pa-hub init`, add the three agents, `pa-hub serve`. Then `curl -k -H "Authorization: Bearer <phone token>" https://192.0.2.99:7700/v1/devices` gives 4 devices, Robot Pi `capabilities` includes `usb`, and ComfyUI appears in `/v1/programs?device=jw-pc` when running. Run `install-hub.ps1`, sign out and back in, and the hub is running.
- [ ] **Step 6:** Commit `feat(hub): HTTPS API, pairing, Windows autostart`.

---

## Part C — MCP

### Task 14: MCP server

**Files:**
- Create: `mcp/pyproject.toml` (deps: mcp>=1.2, httpx; script `pa-mcp = "pa_mcp.server:main"`), `mcp/pa_mcp/__init__.py`, `mcp/pa_mcp/server.py`
- Test: `mcp/tests/test_server.py`

**Interfaces:**
- Consumes: Hub API (Task 13), via env `PA_HUB_URL`, `PA_HUB_TOKEN` (the `mcp` client token), `PA_HUB_CERT` (path to the hub cert.pem, pinned)
- Produces FastMCP tools. Every tool returns the hub's `data` or raises `ToolError(error)`.
  - `list_devices()`, `get_device(device)`
  - `list_programs(device: str | None = None, include_system: bool = False)`
  - `get_program(device, program_id)`, `get_program_logs(device, program_id, lines: int = 100)`
  - `program_action(device, program_id, action: Literal["start","stop","restart"])`
  - `restart_group(device, project)`
  - `get_usb_power(device)`, `set_usb_power(device, on: bool)`
  - `reboot_device(device)`
  - `recent_history(limit: int = 20)`

  Tool descriptions say plainly what each does, which devices exist (ids and names), and that USB power is all ports at once. No confirmation step (Jamil's decision).
  - Transports: `pa-mcp` gives stdio. `pa-mcp --http --host 192.0.2.99 --port 7702` gives streamable HTTP and requires `Authorization: Bearer {PA_MCP_HTTP_TOKEN}`.

- [ ] **Step 1: Write failing tests** (respx mocks the hub; call tools through FastMCP's in-memory client):
  - `test_list_devices_passthrough`
  - `test_hub_error_becomes_tool_error` (hub 503 `"Robot Pi is offline"` gives ToolError with that text)
  - `test_action_posts_body` (`{"device":"robot-pi","id":"docker:sentinel-ros-lidar-1","action":"restart"}`)
  - `test_http_transport_rejects_missing_token`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Register with Claude Code at user scope: `claude mcp add personal-automations -s user -e PA_HUB_URL=https://192.0.2.99:7700 -e PA_HUB_TOKEN=… -e PA_HUB_CERT=… -- uv run --project F:\Dev\personal-automations pa-mcp`. In a new Claude Code session, ask "what programs are running on Pi3?". Expected: the tool call returns ntopng etc. Add the HTTP transport to the scheduled task from Task 13 (a second action) so agents on other machines can reach `:7702`.
- [ ] **Step 6:** Commit `feat(mcp): MCP server over the hub API`.

---

## Part D — Android app

### Task 15: Android project, toolchain, CI

**Files:**
- Create: `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/gradle.properties`, `android/gradlew(.bat)` + wrapper, `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml`, `android/app/src/main/java/com/jamilcpu/personalautomations/MainActivity.kt`, `android/app/src/main/res/drawable/ic_launcher_foreground.xml` (wrench vector), `android/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a building app shell with the label "Personal Automations", the wrench adaptive icon, and a bottom bar with tabs **Devices** and **Programs** (empty screens). Config: `minSdk 26`, `targetSdk 35`, `compileSdk 35`, Java/Kotlin 17. Dependency versions are copied from `F:\Dev\AlarmAPI\app\build.gradle.kts` (minus media3/coil). Release signing uses Gradle properties `PA_STORE_FILE`, `PA_STORE_PASSWORD`, `PA_KEY_ALIAS`, `PA_KEY_PASSWORD`, falling back to the debug key, the same pattern as AlarmAPI.

- [ ] **Step 1:** The PC has no JDK or Android SDK on PATH. Install them: `winget install EclipseAdoptium.Temurin.17.JDK`, plus Android command-line tools into `F:\Android\Sdk` (`sdkmanager "platforms;android-35" "build-tools;35.0.0" "platform-tools"`). Set `ANDROID_HOME`, and write `android/local.properties` (gitignored).
- [ ] **Step 2:** Create the project files above, with a unit test `NavTabsTest` asserting the tab list is `["Devices","Programs"]`.
- [ ] **Step 3:** `cd android; .\gradlew.bat testDebugUnitTest assembleDebug`. Expected: BUILD SUCCESSFUL and the test passes.
- [ ] **Step 4:** Write `ci.yml` with two jobs: `python` (uv sync, pytest, ruff) and `android` (setup-java 17, `./gradlew testDebugUnitTest assembleRelease`, upload `personal-automations-<version>.apk`). Push, and `gh run watch` ends green.
- [ ] **Step 5:** Commit `feat(android): app shell with wrench icon and CI`.

### Task 16: Pairing, settings, pinned hub client

**Files:**
- Create: `android/app/src/main/java/com/jamilcpu/personalautomations/data/HubModels.kt`, `data/HubApi.kt`, `data/Pairing.kt`, `data/SettingsStore.kt`, `data/PinnedTls.kt`
- Modify: `AndroidManifest.xml` (deep-link intent filter for `pa://pair`, INTERNET permission)
- Test: `android/app/src/test/java/com/jamilcpu/personalautomations/data/PairingTest.kt`, `HubApiTest.kt`

**Interfaces:**
- Consumes: the Hub API (Task 13). The models mirror `Program`, `Health`, `UsbState`, `ActionResult`, `DeviceSummary`, `ProgramDetail`, `HistoryEntry` and `ApiResponse<T>` field-for-field (snake_case via `@SerialName`).
- Produces:
  - `data class HubSettings(val url: String, val token: String, val fingerprint: String)`
  - `fun parsePairUri(uri: String): HubSettings?` (null for any other scheme/host, or when a field is missing or the fingerprint is not 64 hex)
  - `class SettingsStore(context)`: `val settings: Flow<HubSettings?>`, `suspend fun save(s: HubSettings)`. DataStore; the token is encrypted with an Android Keystore AES key.
  - `fun pinnedOkHttp(fingerprintHex: String): OkHttpClient`. A trust manager that accepts only a server cert whose SHA-256 (over DER) equals the fingerprint, plus a hostname verifier that accepts the pinned cert for the hub IP.
  - `class HubApi(client: HttpClient, settings: HubSettings)` with suspend functions mirroring the routes:
    - `devices()`, `device(id)`, `programs(device: String?, includeSystem: Boolean)`, `program(device, id)`, `logs(device, id, lines)`
    - `act(device, id, action)`, `restartGroup(device, project)`
    - `usb(device)`, `setUsb(device, on)`, `reboot(device)`, `history(limit)`
    
    Each returns `HubResult<T>` = `Ok(T)` or `Err(message: String, code: Int)`. Network failure gives `Err("Can't reach the hub", 0)`.
  - `MainActivity` handles the `pa://pair` intent: parse, save, and toast "Paired with hub". With no settings, both tabs show "Not paired yet. Run `pa-hub pair` on the PC and scan the QR code with the Pixel camera."

- [ ] **Step 1: Write failing tests:**
  - `PairingTest`: a valid URI parses; wrong scheme gives null; a short fingerprint gives null; the URL-encoded token is decoded
  - `HubApiTest` with MockEngine: devices are decoded; a 503 envelope gives `Err("Robot Pi is offline", 503)`; the `Authorization: Bearer` header is sent; a connection failure gives `Err("Can't reach the hub", 0)`
- [ ] **Step 2:** `.\gradlew.bat testDebugUnitTest`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(android): pairing deep link and pinned hub client`.

### Task 17: Devices tab and device screen

**Files:**
- Create: `ui/devices/DevicesViewModel.kt`, `ui/devices/DevicesScreen.kt`, `ui/devices/DeviceScreen.kt`, `ui/common/Confirm.kt`, `ui/common/StatusDot.kt`
- Test: `android/app/src/test/.../ui/devices/DevicesViewModelTest.kt`

**Interfaces:**
- Consumes: `HubApi`
- Produces:
  - `DevicesViewModel(api)`: `state: StateFlow<DevicesUiState>` where `DevicesUiState(loading, devices: List<DeviceSummary>, error: String?, busy: Set<String>)`. It polls `devices()` every 5 s while visible. Functions `setUsb(deviceId, on)`, `reboot(deviceId)`, `restartGroup(deviceId, project)` refresh after success; on failure they put the hub's message in `error` (shown as a snackbar).
  - **DevicesScreen**: a card per device in config order. Card contents:
    - status dot (green online, grey offline)
    - name and role subtitle
    - health chips: temp, CPU %, programs `running/total`; for jw PC, GPU temp and uptime
    - "USB on/off" chip for the Robot Pi
    
    Tap opens DeviceScreen.
  - **DeviceScreen**: a health block, then:
    - if `"usb"` in capabilities, a "USB power (all ports)" switch with caption "All USB ports · back on after reboot". Turning it off asks for confirmation; turning it on does not.
    - if the device has programs with `group != null`, a "Restart all robot programs" button per group, with confirmation
    - "Reboot {name}…" (danger style) with a confirm dialog, "{name} will be offline for about a minute."
    - a link to that device's programs (opens the Programs tab with the filter preset)
    
    No shutdown button.

- [ ] **Step 1: Write failing tests:** `polls_and_exposes_devices`; `usb_failure_shows_hub_message` (Err "did not appear…" ends up in `state.error`); `reboot_marks_busy_then_clears`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement the ViewModel and screens.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(android): devices tab and device screen`.

### Task 18: Programs tab and program detail

**Files:**
- Create: `ui/programs/ProgramsViewModel.kt`, `ui/programs/ProgramsScreen.kt`, `ui/programs/ProgramDetailScreen.kt`, `ui/programs/ProgramDetailViewModel.kt`
- Test: `.../ui/programs/ProgramsViewModelTest.kt`, `ProgramDetailViewModelTest.kt`

**Interfaces:**
- Consumes: `HubApi`, `Confirm`, `StatusDot`
- Produces:
  - `ProgramsViewModel(api)`: `state: StateFlow<ProgramsUiState(loading, programs, deviceFilter: String?, hideSystem: Boolean = true, error)>`. Functions `setFilter(deviceId?)` and `setHideSystem(Boolean)`. The hide-system preference is kept in DataStore. It polls every 5 s.
  - **ProgramsScreen**: filter chips "All, jw PC, Robot Pi, Pi1, Pi3"; a "Hide system programs" toggle in the top bar menu; one card per program showing:
    - status dot
    - name
    - device tag chip (display name)
    - subtitle: description or kind + uptime ("up 2d 4h") or status ("stopped", "restarting")
  - `ProgramDetailViewModel(api, device, id)`: loads `program()` and `logs(lines=50)`. `act(action)` refreshes afterwards. `needsSecondConfirm = program.protection == "confirm"`.
  - **ProgramDetailScreen**:
    - header with the name and device tag
    - rows: Status, Uptime, Kind (plus group, e.g. "Docker · sentinel-ros"), and for k3s, Node and Restarts
    - a button per entry in `program.actions` (Start, Stop, Restart). Stop and Restart confirm; `confirm` protection adds a second dialog, "This is {name}. Your network's DNS depends on it — really restart?"; locked shows the caption "Protected — can't be stopped from here"
    - a "Recent log" monospace block (50 lines, refresh button)
    - a "History" list (client, action, result, relative time)
    - no "open in browser" or Foxglove button

- [ ] **Step 1: Write failing tests:**
  - `hide_system_default_true_and_filters`
  - `device_filter_applies`
  - `confirm_protection_requires_second_confirm`
  - `locked_program_has_no_buttons`
  - `action_error_surfaces_message` (Err 409 message shown)
  - `stale_program_404_shows_gone` (Err 404 gives state `gone = true` and the message "This program no longer exists — pull to refresh") [Review Focus 2]
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(android): programs tab and program detail`.

### Task 19: Release, install on the Pixel, end-to-end check, docs

**Files:**
- Create: `docs/INSTALL.md`
- Modify: `README.md`, and the Obsidian vault: `F:/Dev/obsidian-vault/Projects/PC/Personal Automations.md` (new, from `Templates/`), with links from `00-Index/Projects MOC.md` and the device notes

- [ ] **Step 1:** Create the keystore (`keytool -genkeypair -v -keystore %USERPROFILE%\.android\personal-automations.jks -alias pa -keyalg RSA -keysize 2048 -validity 10000`) and set the `PA_*` properties in `%USERPROFILE%\.gradle\gradle.properties`. Then `.\gradlew.bat testDebugUnitTest assembleRelease`. Expected: a signed APK.
- [ ] **Step 2:** Pair wireless ADB with the Pixel (`adb pair`, `adb connect`), then `adb install -r android\app\build\outputs\apk\release\app-release.apk`. Expected: "Success", and the launcher shows a wrench icon named "Personal Automations".
- [ ] **Step 3:** Run `pa-hub pair --client phone` and scan the QR with the Pixel camera. Expected: the app opens with "Paired with hub" and the Devices tab shows jw PC, Robot Pi, Pi1, Pi3 all online.
- [ ] **Step 4: End-to-end checks** (ask Jamil before each one marked ⚠):
  - The Programs tab shows lidar/foxglove/rosbridge (Robot Pi), Pi-hole (Pi1), ntopng/snapserver (Pi3), and gitea/mission-control with device tags. Toggling hide-system reveals getty etc.
  - Restart `snapserver` on Pi3. The status shows restarting, then running, and history shows "phone · restart · ok".
  - Program detail for `ssh.service` shows no buttons and the protected caption.
  - ⚠ USB off then on for the Robot Pi from the phone. lidar shows stopped, then running.
  - ⚠ Reboot Pi3 from the phone. It shows offline, then back online within ~2 min.
  - Turn off Wi-Fi on the phone. The app shows "Can't reach the hub", not a crash.
- [ ] **Step 5:** Write `docs/INSTALL.md` (hub install, agent deploy, pairing, MCP registration, how to rotate a token: edit `hub.secrets.toml` and re-pair). Update the README and the vault note.
- [ ] **Step 6:** Commit `docs: install guide and vault note` and push. CI is green.
