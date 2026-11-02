import asyncio
import time
from pathlib import Path

import pytest
from pa_core.models import ActionName, ActionResult, Health, Program, UsbState
from pa_core.policy import ActionNotAllowed
from pa_hub.clients import DeviceOffline, HubAgentError
from pa_hub.config import DeviceEntry
from pa_hub.history import HistoryStore
from pa_hub.pins import PinStore
from pa_hub.service import HubService

K3S_POD = "k3s:lab/mission-control@raspberrypi-2"


def prog(
    pid: str,
    device: str,
    *,
    name: str | None = None,
    system: bool = False,
    status: str = "running",
    pin_key: str = "",
    kind: str = "docker",
) -> Program:
    return Program.model_validate(
        {
            "id": pid,
            "name": name or pid,
            "kind": kind,
            "device": device,
            "status": status,
            "system": system,
            "protection": "none",
            "actions": ["restart"],
            "pin_key": pin_key,
        }
    )


class FakeClient:
    def __init__(
        self,
        programs: list[Program] | None = None,
        *,
        caps: set[str] | None = None,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.capabilities = caps or set()
        self._programs = programs or []
        self._delay = delay
        self._error = error
        self.calls: list[tuple] = []

    async def _gate(self) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error

    async def health(self) -> Health:
        await self._gate()
        return Health(cpu_pct=1, mem_pct=2, disk_pct=3, uptime_s=4)

    async def programs(self) -> list[Program]:
        await self._gate()
        return list(self._programs)

    async def program(self, pid: str) -> Program:
        await self._gate()
        for p in self._programs:
            if p.id == pid:
                return p
        raise KeyError(pid)

    async def logs(self, pid: str, lines: int) -> list[str]:
        self.calls.append(("logs", pid, lines))
        return ["line"]

    async def act(self, pid: str, action: ActionName) -> ActionResult:
        self.calls.append(("act", pid, action))
        await self.program(pid)
        return ActionResult(ok=True, message=f"{action} {pid}")

    async def group_restart(self, project: str) -> ActionResult:
        self.calls.append(("group", project))
        return ActionResult(ok=True, message="grp")

    async def usb(self) -> UsbState:
        return UsbState(on=True, detail="up")

    async def set_usb(self, on: bool) -> ActionResult:
        self.calls.append(("usb", on))
        return ActionResult(ok=True, message="usb")

    async def reboot(self) -> ActionResult:
        self.calls.append(("reboot",))
        return ActionResult(ok=False, message="refused")


def entry(device_id: str, caps: list[str] | None = None, *, k3s: bool = False) -> DeviceEntry:
    return DeviceEntry(
        id=device_id,
        name=device_id.upper(),
        role="r",
        ip="1.1.1.1",
        kind="agent",
        capabilities=caps or [],
        k3s_source=k3s,
    )


def standard(
    tmp_path: Path, timeout_s: float = 1.0, action_timeout_s: float = 30.0, **overrides: FakeClient
) -> tuple[HubService, dict[str, FakeClient]]:
    clients = {
        "jw-pc": FakeClient(
            [
                prog("d:b", "jw-pc", name="Beta"),
                prog("d:a", "jw-pc", name="alpha"),
                prog("d:sys", "jw-pc", system=True),
            ]
        ),
        "pi1": FakeClient(
            [
                prog("s:x", "pi1", name="x"),
                prog(K3S_POD, "robot-pi", name="mission-control"),
                prog("k3s:lab/other@pi3", "pi3", name="other", status="stopped"),
            ]
        ),
        "robot-pi": FakeClient([prog("s:r", "robot-pi", name="r")], caps={"usb", "reboot"}),
        "pi3": FakeClient([], caps={"reboot"}),
    }
    clients.update(overrides)
    caps = {"robot-pi": ["usb", "reboot"], "pi3": ["reboot"]}
    entries = {i: entry(i, caps.get(i), k3s=i == "pi1") for i in clients}
    service = HubService(
        clients,  # type: ignore[arg-type]
        entries,
        HistoryStore(str(tmp_path / "h.db")),
        timeout_s,
        action_timeout_s=action_timeout_s,
        pins=PinStore(str(tmp_path / "h.db")),
    )
    return service, clients


async def test_one_offline_device_does_not_block(tmp_path: Path) -> None:
    service, _ = standard(tmp_path, timeout_s=0.2, pi3=FakeClient(delay=10))
    start = time.monotonic()
    result = await service.devices()
    assert time.monotonic() - start < 1
    by_id = {d.id: d for d in result}
    assert by_id["pi3"].online is False
    assert by_id["pi3"].programs_total == 0
    assert all(by_id[i].online for i in ("jw-pc", "pi1", "robot-pi"))
    assert [d.id for d in result] == ["jw-pc", "pi1", "robot-pi", "pi3"]


async def test_programs_skip_offline(tmp_path: Path) -> None:
    service, _ = standard(tmp_path, pi1=FakeClient(error=DeviceOffline("pi1")))
    ids = {p.id for p in await service.programs(None, include_system=True)}
    assert "s:x" not in ids
    assert "d:a" in ids


async def test_agent_error_treated_as_offline_in_listing(tmp_path: Path) -> None:
    service, _ = standard(tmp_path, pi3=FakeClient(error=HubAgentError("pi3", 401, "bad")))
    by_id = {d.id: d for d in await service.devices()}
    assert by_id["pi3"].online is False


async def test_include_system_filter(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    without = await service.programs("jw-pc", include_system=False)
    with_sys = await service.programs("jw-pc", include_system=True)
    assert [p.id for p in without] == ["d:a", "d:b"]
    assert {p.id for p in with_sys} == {"d:a", "d:b", "d:sys"}


async def test_counts_exclude_system(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    summary = await service.device("jw-pc")
    assert (summary.programs_running, summary.programs_total) == (2, 2)


async def test_robot_pi_programs_include_k3s_pods(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    ids = [p.id for p in await service.programs("robot-pi", include_system=False)]
    assert ids == [K3S_POD, "s:r"]
    robot = await service.device("robot-pi")
    assert (robot.programs_running, robot.programs_total) == (2, 2)
    pi1 = await service.device("pi1")
    assert (pi1.programs_running, pi1.programs_total) == (1, 1)
    pi3 = await service.device("pi3")
    assert (pi3.programs_running, pi3.programs_total) == (0, 1)


async def test_devices_usb_only_with_capability(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    by_id = {d.id: d for d in await service.devices()}
    assert by_id["robot-pi"].usb is not None
    assert by_id["robot-pi"].usb.on
    assert by_id["pi1"].usb is None


async def test_unknown_device(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    with pytest.raises(KeyError, match="unknown device: nope"):
        await service.device("nope")


async def test_action_recorded_with_client(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    result = await service.act("phone", "jw-pc", "d:a", "restart")
    assert result.ok
    [h] = await service.recent_history(10)
    assert (h.client, h.device, h.target, h.action, h.ok) == (
        "phone",
        "jw-pc",
        "d:a",
        "restart",
        True,
    )


async def test_failed_action_recorded_and_raised(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    with pytest.raises(KeyError):
        await service.act("phone", "jw-pc", "missing", "stop")
    [h] = await service.recent_history(10)
    assert h.ok is False
    assert "missing" in h.message


async def test_action_result_not_ok_recorded_not_raised(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    result = await service.reboot("phone", "robot-pi")
    assert result.ok is False
    [h] = await service.recent_history(10)
    assert (h.target, h.action, h.ok, h.message) == ("reboot", "reboot", False, "refused")


async def test_k3s_program_routed_to_source(tmp_path: Path) -> None:
    service, clients = standard(tmp_path)
    await service.act("phone", "robot-pi", K3S_POD, "restart")
    assert ("act", K3S_POD, "restart") in clients["pi1"].calls
    assert clients["robot-pi"].calls == []
    [h] = await service.recent_history(10)
    assert h.device == "robot-pi"
    assert await service.logs("robot-pi", K3S_POD, 5) == ["line"]
    detail = await service.program("robot-pi", K3S_POD)
    assert detail.program.id == K3S_POD
    assert len(detail.history) == 1


async def test_k3s_unknown_device_still_rejected(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    with pytest.raises(KeyError, match="unknown device"):
        await service.logs("nope", K3S_POD, 5)


async def test_usb_on_non_robot_not_allowed(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    with pytest.raises(ActionNotAllowed):
        await service.set_usb("phone", "pi1", True)
    with pytest.raises(ActionNotAllowed):
        await service.reboot("phone", "jw-pc")
    with pytest.raises(ActionNotAllowed):
        await service.usb("pi1")
    hist = await service.recent_history(10)
    assert len(hist) == 2
    assert not any(h.ok for h in hist)


async def test_usb_and_group_history(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    await service.set_usb("phone", "robot-pi", False)
    await service.group_restart("phone", "pi1", "proj")
    hist = {h.target: h for h in await service.recent_history(10)}
    assert hist["usb"].action == "off"
    assert hist["group:proj"].action == "restart"


async def test_offline_action_recorded(tmp_path: Path) -> None:
    service, _ = standard(tmp_path, timeout_s=0.1, action_timeout_s=0.1, pi3=FakeClient(delay=10))
    # reboot on pi3 hits the fake immediately; use act which awaits program() (delayed)
    with pytest.raises(DeviceOffline):
        await service.act("phone", "pi3", "x", "restart")
    [h] = await service.recent_history(10)
    assert h.ok is False


async def test_action_timeout_is_longer_than_read_timeout(tmp_path: Path) -> None:
    class SlowAct(FakeClient):
        async def act(self, pid: str, action: ActionName) -> ActionResult:
            await asyncio.sleep(0.4)
            return await super().act(pid, action)

    slow = SlowAct([prog("s:r", "robot-pi")], caps={"reboot"})
    service, _ = standard(tmp_path, timeout_s=0.1, action_timeout_s=2.0, **{"robot-pi": slow})
    result = await service.act("phone", "robot-pi", "s:r", "restart")
    assert result.ok
    service._action_timeout_s = 0.1
    with pytest.raises(DeviceOffline):
        await service.act("phone", "robot-pi", "s:r", "restart")


class GarbageClient(FakeClient):
    async def programs(self) -> list[Program]:
        return [Program.model_validate({"bogus": 1})]


@pytest.mark.parametrize(
    "bad",
    [
        FakeClient(error=ValueError("bad json")),
        FakeClient(error=RuntimeError("boom")),
        GarbageClient(),
    ],
)
async def test_misbehaving_client_is_offline_not_500(tmp_path: Path, bad: FakeClient) -> None:
    service, _ = standard(tmp_path, pi3=bad)
    by_id = {d.id: d for d in await service.devices()}
    assert by_id["pi3"].online is False
    assert all(by_id[i].online for i in ("jw-pc", "pi1", "robot-pi"))
    ids = {p.id for p in await service.programs(None, include_system=True)}
    assert "d:a" in ids


class CountingClient(FakeClient):
    async def health(self) -> Health:
        self.calls.append(("health",))
        return await super().health()

    async def programs(self) -> list[Program]:
        self.calls.append(("programs",))
        return await super().programs()


async def test_device_filtered_programs_skip_unrelated_clients(tmp_path: Path) -> None:
    jw = CountingClient([prog("d:a", "jw-pc")])
    pi1 = CountingClient([prog("s:x", "pi1"), prog("k3s:lab/other@pi3", "pi3", name="other")])
    pi3 = CountingClient([prog("s:p", "pi3", name="p")])
    service, _ = standard(tmp_path, **{"jw-pc": jw, "pi1": pi1, "pi3": pi3})
    ids = {p.id for p in await service.programs("pi3", include_system=True)}
    assert ids == {"s:p", "k3s:lab/other@pi3"}
    assert jw.calls == []
    assert ("health",) not in pi1.calls + pi3.calls


async def test_local_device_filter_skips_k3s_source(tmp_path: Path) -> None:
    pi1 = CountingClient([prog("s:x", "pi1")])
    service, _ = standard(tmp_path, pi1=pi1)
    service._entries["jw-pc"] = entry("jw-pc").model_copy(update={"kind": "local"})
    assert {p.id for p in await service.programs("jw-pc", include_system=True)} == {
        "d:a",
        "d:b",
        "d:sys",
    }
    assert pi1.calls == []


async def test_unfiltered_programs_skip_health(tmp_path: Path) -> None:
    clients = {i: CountingClient([prog(f"p:{i}", i)]) for i in ("jw-pc", "pi1", "robot-pi", "pi3")}
    service, _ = standard(tmp_path, **clients)
    assert len(await service.programs(None, include_system=True)) == 4
    assert all(c.calls == [("programs",)] for c in clients.values())


# ---- pinning ---------------------------------------------------------------


async def test_pin_marks_program_and_sorts_first(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    result = await service.set_pinned("phone", "jw-pc", "d:b", True)
    assert result.ok
    progs = await service.programs("jw-pc", include_system=False)
    assert [(p.id, p.pinned) for p in progs] == [("d:b", True), ("d:a", False)]
    assert progs[0].pin_key == "d:b"  # older agents send no pin_key: the hub uses the id
    [h] = await service.recent_history(10)
    assert (h.client, h.device, h.target, h.action, h.ok) == ("phone", "jw-pc", "d:b", "pin", True)


async def test_pinned_system_program_always_listed(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    await service.set_pinned("phone", "jw-pc", "d:sys", True)
    progs = await service.programs("jw-pc", include_system=False)
    assert [p.id for p in progs] == ["d:sys", "d:a", "d:b"]
    assert progs[0].system is True and progs[0].pinned is True
    summary = await service.device("jw-pc")
    assert (summary.programs_running, summary.programs_total) == (3, 3)


async def test_pin_key_survives_restart_of_process(tmp_path: Path) -> None:
    before = prog("process:Discord#1@10", "jw-pc", name="Discord", pin_key="process:Discord")
    after = prog("process:Discord#2@20", "jw-pc", name="Discord", pin_key="process:Discord")
    service, clients = standard(tmp_path, **{"jw-pc": FakeClient([before])})
    await service.set_pinned("phone", "jw-pc", before.id, True)
    clients["jw-pc"]._programs = [after]
    (only,) = await service.programs("jw-pc", include_system=False)
    assert (only.id, only.pinned) == (after.id, True)


async def test_placeholder_for_pinned_program_not_running(tmp_path: Path) -> None:
    gone = prog(
        "process:Discord#1@10", "jw-pc", name="Discord", pin_key="process:Discord", kind="process"
    )
    service, clients = standard(tmp_path, **{"jw-pc": FakeClient([gone])})
    await service.set_pinned("phone", "jw-pc", gone.id, True)
    clients["jw-pc"]._programs = []
    (placeholder,) = await service.programs("jw-pc", include_system=False)
    assert (
        placeholder.model_dump()
        == Program(
            id="pinned:process:Discord",
            name="Discord",
            kind="process",
            device="jw-pc",
            status="stopped",
            description="Not running",
            system=False,
            protection="none",
            actions=[],
            pin_key="process:Discord",
            pinned=True,
        ).model_dump()
    )
    summary = await service.device("jw-pc")
    assert (summary.programs_running, summary.programs_total) == (0, 1)
    every = await service.programs(None, include_system=False)
    assert every[0].id == "pinned:process:Discord"


async def test_no_placeholder_for_offline_device(tmp_path: Path) -> None:
    service, clients = standard(tmp_path)
    await service.set_pinned("phone", "robot-pi", "s:r", True)
    clients["robot-pi"]._error = DeviceOffline("robot-pi")
    # Only the k3s pod remains (it comes from pi1); no placeholder for the offline service.
    ids = [p.id for p in await service.programs("robot-pi", include_system=False)]
    assert ids == [K3S_POD]
    ids = [p.id for p in await service.programs(None, include_system=False)]
    assert not any(i.startswith("pinned:") for i in ids)


async def test_no_k3s_placeholder_when_k3s_source_offline(tmp_path: Path) -> None:
    service, clients = standard(tmp_path)
    await service.set_pinned("phone", "robot-pi", K3S_POD, True)
    clients["pi1"]._error = DeviceOffline("pi1")
    assert [p.id for p in await service.programs("robot-pi", include_system=False)] == ["s:r"]


async def test_unpin_live_and_placeholder(tmp_path: Path) -> None:
    service, clients = standard(tmp_path)
    await service.set_pinned("phone", "jw-pc", "d:a", True)
    result = await service.set_pinned("mcp", "jw-pc", "d:a", False)
    assert result.ok
    assert not any(p.pinned for p in await service.programs("jw-pc", include_system=False))
    await service.set_pinned("phone", "jw-pc", "d:a", True)
    clients["jw-pc"]._programs = []
    (placeholder,) = await service.programs("jw-pc", include_system=False)
    await service.set_pinned("phone", "jw-pc", placeholder.id, False)
    assert await service.programs("jw-pc", include_system=False) == []
    actions = [h.action for h in await service.recent_history(10)]
    assert actions == ["unpin", "pin", "unpin", "pin"]


async def test_pin_k3s_program_routed_to_source(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    await service.set_pinned("phone", "robot-pi", K3S_POD, True)
    progs = await service.programs("robot-pi", include_system=False)
    assert [(p.id, p.pinned) for p in progs] == [(K3S_POD, True), ("s:r", False)]


async def test_pin_unknown_device_or_program(tmp_path: Path) -> None:
    service, _ = standard(tmp_path)
    with pytest.raises(KeyError, match="unknown device"):
        await service.set_pinned("phone", "nope", "d:a", True)
    with pytest.raises(KeyError):
        await service.set_pinned("phone", "jw-pc", "missing", True)
    with pytest.raises(KeyError):
        await service.set_pinned("phone", "jw-pc", "pinned:never", False)
    hist = await service.recent_history(10)
    assert len(hist) == 3
    assert not any(h.ok for h in hist)
