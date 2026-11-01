import sys

import pytest
from pa_core.runner import run


def test_run_rejects_string_argv():
    with pytest.raises(TypeError):
        run("ls")  # type: ignore[arg-type]


def test_run_rejects_non_str_items():
    with pytest.raises(TypeError):
        run([sys.executable, 1])  # type: ignore[list-item]


def test_run_captures_output():
    assert run([sys.executable, "-c", "print('hi')"]).stdout.strip() == "hi"


def test_run_returncode_and_stderr():
    r = run([sys.executable, "-c", "import sys; sys.stderr.write('e'); sys.exit(3)"])
    assert (r.returncode, r.stderr) == (3, "e")


def test_run_timeout():
    r = run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)
    assert r.returncode == 124
    assert r.stderr == "timed out"


def test_run_missing_executable_returns_127():
    r = run(["definitely-not-a-real-binary-xyz"])
    assert r == (127, "", "definitely-not-a-real-binary-xyz not found")


def test_run_decodes_invalid_utf8_with_replacement():
    r = run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([97, 255, 98]))"])
    assert r.returncode == 0
    assert r.stdout == "a�b"
