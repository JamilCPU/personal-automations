from pa_core.models import ApiResponse, Program, fail, ok


def test_program_roundtrip():
    p = Program(
        id="k3s:default/mission-control@raspberrypi-1",
        name="mission-control",
        kind="k3s",
        device="pi1",
        status="running",
        uptime_s=120,
        system=False,
        protection="none",
        actions=["restart"],
        group=None,
        detail={"node": "raspberrypi-1"},
    )
    assert Program.model_validate_json(p.model_dump_json()) == p
    assert Program.model_validate(p.model_dump()) == p


def test_envelope_ok_and_fail():
    assert ok(1).model_dump() == {"success": True, "data": 1, "error": None}
    bad = fail("x")
    assert (bad.success, bad.data, bad.error) == (False, None, "x")
    assert isinstance(bad, ApiResponse)


def test_program_pin_fields_default():
    p = Program(
        id="docker:web",
        name="web",
        kind="docker",
        device="pi1",
        status="running",
        system=False,
        protection="none",
        actions=["stop"],
    )
    assert (p.pin_key, p.pinned) == ("", False)
    pinned = Program.model_validate({**p.model_dump(), "pin_key": "docker:web", "pinned": True})
    assert (pinned.pin_key, pinned.pinned) == ("docker:web", True)
