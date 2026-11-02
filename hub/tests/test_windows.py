import psutil
import pytest
from pa_core.policy import ActionNotAllowed
from pa_core.runner import RunResult
from pa_hub.windows import LocalWindowsDevice, normalise_binpath

HUB_PID = 4242
OLD = 1_000.0
NOW = 100_000.0
NO_DOCKER = ("docker", "info")
PS_ARGV = ("docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}")
GPU_ARGV = (
    "nvidia-smi",
    "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
    "--format=csv,noheader,nounits",
)


class FakeService:
    def __init__(self, name, display, status="running", binpath=r"C:\Tools\svc.exe"):
        self._n, self._d, self._s, self._b = name, display, status, binpath

    def name(self):
        return self._n

    def display_name(self):
        return self._d

    def status(self):
        return self._s

    def binpath(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


class FakeProc:
    def __init__(self, pid, name, cmdline=(), created=OLD, exe=None, ppid=0):
        self.info = {
            "pid": pid,
            "name": name,
            "exe": exe,
            "cmdline": list(cmdline),
            "create_time": created,
            "ppid": ppid,
        }
        self.terminated = False

    def create_time(self):
        return self.info["create_time"]

    def terminate(self):
        self.terminated = True


class FakeProcs:
    def __init__(self, procs):
        self.procs = {p.info["pid"]: p for p in procs}

    def process_iter(self, attrs=None):
        return iter(list(self.procs.values()))

    def Process(self, pid):
        if pid not in self.procs:
            raise psutil.NoSuchProcess(pid)
        return self.procs[pid]

    def cpu_percent(self, interval=None):
        return 12.5

    def virtual_memory(self):
        return type("M", (), {"percent": 40.0})()

    def disk_usage(self, path):
        assert path == "C:\\"
        return type("D", (), {"percent": 55.0})()

    def boot_time(self):
        return NOW - 3600


class Recorder:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = {NO_DOCKER: RunResult(1, "", "no daemon"), **(replies or {})}

    def __call__(self, argv):
        self.calls.append(list(argv))
        reply = self.replies.get(tuple(argv))
        if callable(reply):
            return reply()
        return reply or RunResult(0, "", "")


def make(procs=(), services=(), windowed=None, run=None):
    return LocalWindowsDevice(
        run=run or Recorder(),
        hub_pid=HUB_PID,
        procs=FakeProcs(procs),
        services=lambda: iter(services),
        windowed_pids=lambda: windowed or {},
        sleep=lambda _s: None,
        now=lambda: NOW,
    )


async def test_comfyui_named_from_folder():
    proc = FakeProc(100, "python.exe", ["python", "F:\\ComfyUI\\main.py"])
    progs = await make(procs=[proc]).programs()
    assert [p.id for p in progs] == ["process:ComfyUI#100@1000"]
    assert progs[0].kind == "process"
    assert progs[0].actions == ["stop"]
    assert progs[0].uptime_s == int(NOW - OLD)


async def test_young_python_and_windowless_ignored():
    procs = [
        FakeProc(1, "python.exe", ["python", "a.py"], created=NOW - 5),
        FakeProc(2, "svchost.exe"),
    ]
    assert await make(procs=procs).programs() == []


async def test_windowed_process_named_by_exe_stem():
    proc = FakeProc(7, "discord.exe", exe=r"C:\Users\j\AppData\Local\Discord\Discord.exe")
    title = "discordapp.com/app?_=1793580594810 - Discord"
    (prog,) = await make(procs=[proc], windowed={7: title}).programs()
    assert prog.id == "process:Discord#7@1000"
    assert prog.name == "Discord"
    assert prog.description == title
    assert prog.detail["window"] == title
    assert prog.pin_key == "process:Discord"
    assert prog.system is False


async def test_interpreter_keeps_script_name_and_pin_key():
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    (prog,) = await make(procs=[proc], windowed={100: "some title"}).programs()
    assert prog.name == "ComfyUI"
    assert prog.pin_key == "process:ComfyUI"


async def test_service_pin_key_is_id():
    (prog,) = await make(services=[FakeService("Tailscale", "Tailscale")]).programs()
    assert prog.pin_key == "windows-service:Tailscale"


WINAPPS = r"C:\Program Files\WindowsApps"


async def test_microsoft_apps_hidden_others_visible():
    procs = [
        FakeProc(
            1, "Notepad.exe", exe=WINAPPS + r"\Microsoft.WindowsNotepad_1_x64__8we\Notepad.exe"
        ),
        FakeProc(2, "WindowsTerminal.exe", exe=WINAPPS + r"\Microsoft.WindowsTerminal_1\WT.exe"),
        FakeProc(3, "msedge.exe", exe=r"C:\Program Files (x86)\Microsoft\Edge\msedge.exe"),
        FakeProc(4, "claude.exe", exe=WINAPPS + r"\Claude_1.4_x64__pzs\app\claude.exe"),
        FakeProc(5, "brave.exe", exe=r"C:\Program Files\BraveSoftware\brave.exe"),
        FakeProc(6, "SidebarDiagnostics.exe", exe=r"D:\Sidebar\SidebarDiagnostics.exe"),
    ]
    windowed = {i: f"title {i}" for i in range(1, 7)}
    progs = await make(procs=procs, windowed=windowed).programs()
    system = {p.name: p.system for p in progs}
    assert system == {
        "Notepad": True,
        "WindowsTerminal": True,
        "Msedge": True,
        "Claude": False,
        "Brave": False,
        "SidebarDiagnostics": False,
    }


async def test_interpreter_under_microsoft_jdk_stays_visible():
    java = r"C:\Program Files\Microsoft\jdk-21\bin\java.exe"
    proc = FakeProc(9, "java.exe", ["java", "@user_jvm_args.txt", "@win_args.txt"], exe=java)
    (prog,) = await make(procs=[proc]).programs()
    assert prog.system is False


async def test_stopped_and_helper_services_hidden():
    svcs = [
        FakeService("bravem", "Brave Update Service (bravem)", "stopped"),
        FakeService("NvContainerLocalSystem", "NVIDIA LocalSystem Container"),
        FakeService("Tailscale", "Tailscale", binpath=r"C:\Program Files\Tailscale\t.exe"),
    ]
    system = {p.detail["service"]: p.system for p in await make(services=svcs).programs()}
    assert system == {"bravem": True, "NvContainerLocalSystem": True, "Tailscale": False}


async def test_hub_process_locked():
    proc = FakeProc(HUB_PID, "python.exe", ["python", "-m", "pa_hub"])
    dev = make(procs=[proc])
    (prog,) = await dev.programs()
    assert prog.protection == "locked"
    assert prog.actions == []
    assert prog.system is True
    with pytest.raises(ActionNotAllowed):
        await dev.act(prog.id, "stop")
    assert not proc.terminated


async def test_process_stop_terminates_fresh_pid():
    proc = FakeProc(100, "python.exe", ["python", "F:\\ComfyUI\\main.py"])
    dev = make(procs=[proc])
    result = await dev.act("process:ComfyUI#100@1000", "stop")
    assert result.ok
    assert proc.terminated
    with pytest.raises(KeyError):
        await dev.act("process:Other#999", "stop")
    with pytest.raises(ActionNotAllowed):
        await dev.act("process:ComfyUI#100@1000", "restart")


async def test_service_listing_and_protection():
    svcs = [
        FakeService("sshd", "OpenSSH", binpath=r'"C:\Windows\System32\OpenSSH\sshd.exe"'),
        FakeService("Spooler", "Print Spooler", binpath=r"C:\Windows\System32\spoolsv.exe"),
        FakeService("MyApp", "My App", "stopped", r'"C:\Program Files\My\app.exe" --run'),
        FakeService("Busy", "Busy", "start_pending"),
        FakeService("Denied", "Denied", binpath=PermissionError("denied")),
    ]
    by_name = {p.name: p for p in await make(services=svcs).programs()}
    assert by_name["OpenSSH"].protection == "locked"
    assert by_name["OpenSSH"].actions == []
    assert by_name["Print Spooler"].system is True
    assert by_name["My App"].system is True  # stopped services are hidden
    assert by_name["Busy"].system is False
    assert by_name["My App"].status == "stopped"
    assert by_name["My App"].actions == ["start"]
    assert by_name["Busy"].status == "restarting"
    assert by_name["Denied"].system is True
    assert by_name["My App"].id == "windows-service:MyApp"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r'"C:\Windows\system32\svchost.exe" -k netsvcs -p', r"C:\Windows\system32\svchost.exe"),
        (r"C:\Program Files\App\a.exe -flag x", r"C:\Program Files\App\a.exe"),
        (r"%SystemRoot%\system32\foo.exe /x", r"C:\Windows\system32\foo.exe"),
        (r"%windir%\system32\foo.exe", r"C:\Windows\system32\foo.exe"),
        (r"\SystemRoot\System32\drivers\x.sys", r"C:\Windows\System32\drivers\x.sys"),
        (r"D:\bin\tool", r"D:\bin\tool"),
    ],
)
def test_normalise_binpath(raw, expected):
    assert normalise_binpath(raw) == expected


async def test_service_restart_sequence():
    states = iter(["RUNNING", "STOP_PENDING", "STOPPED"])
    run = Recorder(
        {("sc.exe", "query", "X"): lambda: RunResult(0, f"STATE : 4  {next(states)}", "")}
    )
    dev = make(services=[FakeService("X", "X svc")], run=run)
    result = await dev.act("windows-service:X", "restart")
    assert result.ok
    sc_calls = [c for c in run.calls if c[0] == "sc.exe"]
    assert sc_calls[0] == ["sc.exe", "stop", "X"]
    assert sc_calls[-1] == ["sc.exe", "start", "X"]
    assert all(c == ["sc.exe", "query", "X"] for c in sc_calls[1:-1])
    assert len(sc_calls) == 5


async def test_service_restart_times_out():
    ticks = iter(range(0, 1000, 5))
    run = Recorder({("sc.exe", "query", "X"): RunResult(0, "STATE : 4 RUNNING", "")})
    dev = LocalWindowsDevice(
        run=run,
        hub_pid=HUB_PID,
        procs=FakeProcs([]),
        services=lambda: iter([FakeService("X", "X")]),
        windowed_pids=dict,
        sleep=lambda _s: None,
        clock=lambda: float(next(ticks)),
        now=lambda: NOW,
    )
    result = await dev.act("windows-service:X", "restart")
    assert not result.ok
    assert ["sc.exe", "start", "X"] not in run.calls


async def test_service_start_and_disallowed():
    run = Recorder()
    dev = make(services=[FakeService("X", "X", "stopped")], run=run)
    assert (await dev.act("windows-service:X", "start")).ok
    assert ["sc.exe", "start", "X"] in run.calls
    with pytest.raises(ActionNotAllowed):
        await dev.act("windows-service:X", "stop")


async def test_gpu_parse_and_health():
    run = Recorder({GPU_ARGV: RunResult(0, "41, 3, 1200, 24576\n", "")})
    health = await make(run=run).health()
    assert health.gpu == {"temp_c": 41, "util_pct": 3, "mem_used_mb": 1200, "mem_total_mb": 24576}
    assert health.cpu_pct == 12.5
    assert health.mem_pct == 40.0
    assert health.disk_pct == 55.0
    assert health.uptime_s == 3600
    assert health.temp_c is None


async def test_gpu_missing_is_none():
    assert (await make().health()).gpu is None


async def test_no_docker_ok():
    procs = [FakeProc(100, "python.exe", ["python", "F:\\ComfyUI\\main.py"])]
    progs = await make(procs=procs, services=[FakeService("X", "X")]).programs()
    assert {p.kind for p in progs} == {"process", "windows-service"}


async def test_docker_listed_and_acted_on():
    ps_row = (
        '{"Names":"web","State":"running","Image":"nginx","RunningFor":"2 hours ago",'
        '"Labels":"","Status":"Up"}'
    )
    run = Recorder(
        {
            NO_DOCKER: RunResult(0, "ok", ""),
            PS_ARGV: RunResult(0, ps_row + "\n", ""),
            ("docker", "logs", "--tail", "5", "web"): RunResult(0, "a\nb\n", ""),
        }
    )
    dev = make(run=run)
    assert (await dev.program("docker:web")).kind == "docker"
    assert (await dev.act("docker:web", "restart")).ok
    assert ["docker", "restart", "web"] in run.calls
    assert await dev.logs("docker:web", 5) == ["a", "b"]


async def test_logs_and_unsupported():
    dev = make(services=[FakeService("X", "X")])
    assert await dev.logs("windows-service:X", 10) == []
    with pytest.raises(KeyError):
        await dev.program("windows-service:nope")
    assert dev.capabilities == set()
    for call in (dev.usb(), dev.reboot(), dev.set_usb(True)):
        with pytest.raises(ActionNotAllowed, match="not supported on jw PC"):
            await call


async def test_process_under_windows_dir_is_system():
    explorer = FakeProc(8, "explorer.exe", exe=r"C:\WINDOWS\explorer.exe")
    comfy = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"], exe=r"F:\py\python.exe")
    notepad = FakeProc(9, "notepad.exe", exe=r"D:\Tools\notepad.exe")
    windowed = {8: "Program Manager", 9: "n"}
    progs = await make(procs=[explorer, comfy, notepad], windowed=windowed).programs()
    system = {p.detail["pid"]: p.system for p in progs}
    assert system == {"8": True, "100": False, "9": False}


VENV_PY = r"F:\pa\.venv\Scripts\python.exe"
BASE_PY = r"C:\Py\python3.13.exe"


async def test_launcher_and_child_with_same_args_are_one_program():
    args = [r"F:\ComfyUI\main.py", "--listen"]
    launcher = FakeProc(100, "python.exe", [VENV_PY, *args], ppid=1)
    child = FakeProc(101, "python3.13.exe", [BASE_PY, *args], ppid=100)
    progs = await make(procs=[launcher, child]).programs()
    assert [p.id for p in progs] == ["process:ComfyUI#101@1000"]
    assert progs[0].actions == ["stop"]


async def test_hub_ancestor_chain_locked_and_collapsed():
    shell = FakeProc(50, "pa-hub.exe", [r"F:\pa\.venv\Scripts\pa-hub.exe", "serve"], ppid=1)
    venv = FakeProc(51, "python.exe", [VENV_PY, "-m", "pa_hub", "serve"], ppid=50)
    hub = FakeProc(HUB_PID, "python3.13.exe", [BASE_PY, "-m", "pa_hub", "serve"], ppid=51)
    dev = make(procs=[shell, venv, hub])
    (prog,) = await dev.programs()
    assert prog.detail["pid"] == str(HUB_PID)
    assert prog.protection == "locked"
    assert prog.actions == []
    locked = dev._locked_pids(dev._snapshot())
    assert {50, 51, HUB_PID} <= locked
    with pytest.raises(ActionNotAllowed):
        await dev.act(prog.id, "stop")
    assert not any(p.terminated for p in (shell, venv, hub))


async def test_mcp_server_tree_locked():
    mcp = FakeProc(60, "python3.exe", ["python3", r"F:\pa\.venv\Scripts\pa-mcp", "--http"])
    (prog,) = await make(procs=[mcp]).programs()
    assert prog.protection == "locked"
    assert prog.actions == []


@pytest.mark.parametrize("exe", ["python3.exe", "python3.13.exe", "python3.12.exe", "pythonw.exe"])
async def test_versioned_interpreters_recognised(exe):
    proc = FakeProc(100, exe, [exe, r"F:\tools\bot.py"])
    (prog,) = await make(procs=[proc]).programs()
    assert prog.name == "bot.py"


async def test_pid_reuse_refused():
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"], created=OLD + 50)
    dev = make(procs=[proc])
    with pytest.raises(KeyError):
        await dev.act("process:ComfyUI#100@1000", "stop")
    with pytest.raises(KeyError):
        await dev.act("process:ComfyUI#100", "stop")
    assert not proc.terminated


async def test_terminate_refused_when_live_process_changed():
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev = make(procs=[proc])
    program = await dev.program("process:ComfyUI#100@1000")
    proc.info["create_time"] = OLD + 99
    with pytest.raises(KeyError):
        dev._act(program, "stop")
    assert not proc.terminated


async def test_docker_group_restart():
    row = (
        '{{"Names":"{n}","State":"running","Image":"x","RunningFor":"2 hours ago",'
        '"Labels":"com.docker.compose.project=studio","Status":"Up"}}'
    )
    rows = "".join(row.format(n=n) + "\n" for n in ("api", "web"))
    run = Recorder({NO_DOCKER: RunResult(0, "ok", ""), PS_ARGV: RunResult(0, rows, "")})
    dev = make(run=run)
    assert (await dev.group_restart("studio")).ok
    assert ["docker", "restart", "api", "web"] in run.calls
    with pytest.raises(KeyError):
        await dev.group_restart("nope")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def cached_device(procs, clock, *, health=False):
    fake = FakeProcs(procs)
    counts = {"iter": 0}
    original = fake.process_iter

    def counting(attrs=None):
        counts["iter"] += 1
        return original(attrs)

    fake.process_iter = counting
    dev = LocalWindowsDevice(
        run=Recorder(),
        hub_pid=HUB_PID,
        procs=fake,
        services=lambda: iter([]),
        windowed_pids=dict,
        sleep=lambda _s: None,
        clock=clock,
        now=lambda: NOW,
    )
    return dev, counts


async def test_programs_listing_cached_within_ttl_and_expires():
    clock = Clock()
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev, counts = cached_device([proc], clock)
    first = await dev.programs()
    await dev.programs()
    assert counts["iter"] == 1
    clock.t = 3.5
    again = await dev.programs()
    assert counts["iter"] == 2
    assert again == first


async def test_concurrent_programs_share_one_enumeration():
    import asyncio

    clock = Clock()
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev, counts = cached_device([proc], clock)
    await asyncio.gather(dev.programs(), dev.programs(), dev.programs())
    assert counts["iter"] == 1


async def test_act_bypasses_listing_cache():
    clock = Clock()
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev, counts = cached_device([proc], clock)
    (prog,) = await dev.programs()
    assert counts["iter"] == 1
    await dev.act(prog.id, "stop")
    assert counts["iter"] == 2
    assert proc.terminated


async def test_health_cached_briefly():
    clock = Clock()
    dev, _ = cached_device([], clock)
    calls = {"n": 0}
    real = dev._health

    def counting():
        calls["n"] += 1
        return real()

    dev._health = counting
    await dev.health()
    await dev.health()
    assert calls["n"] == 1
    clock.t = 2.5
    await dev.health()
    assert calls["n"] == 2


async def test_programs_reenumerates_after_act():
    clock = Clock()
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev, counts = cached_device([proc], clock)
    (prog,) = await dev.programs()
    await dev.act(prog.id, "stop")
    before = counts["iter"]
    await dev.programs()
    assert counts["iter"] == before + 1


async def test_act_does_not_block_on_held_cache_lock():
    import asyncio
    import threading

    clock = Clock()
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev, _ = cached_device([proc], clock)
    (prog,) = await dev.programs()
    release = threading.Event()
    held = threading.Event()

    def hold():
        with dev._programs_lock, dev._health_lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    held.wait(5)
    try:
        await asyncio.wait_for(dev.act(prog.id, "stop"), timeout=2)
    finally:
        release.set()
        thread.join()


async def test_enumeration_started_before_act_is_not_cached():
    import threading

    clock = Clock()
    proc = FakeProc(100, "python.exe", ["python", r"F:\ComfyUI\main.py"])
    dev, counts = cached_device([proc], clock)
    in_enum = threading.Event()
    proceed = threading.Event()
    real = dev._all_programs

    def slow():
        result = real()
        in_enum.set()
        proceed.wait(5)
        return result

    dev._all_programs = slow
    thread = threading.Thread(target=dev._cached_programs)
    thread.start()
    in_enum.wait(5)
    dev._invalidate_caches()
    proceed.set()
    thread.join()
    dev._all_programs = real
    before = counts["iter"]
    dev._cached_programs()
    assert counts["iter"] == before + 1
