"""The only way Personal Automations starts subprocesses: list argv, never a shell."""

import subprocess
from collections.abc import Callable
from typing import NamedTuple

TIMEOUT_RETURNCODE = 124
NOT_FOUND_RETURNCODE = 127


class RunResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], RunResult]


def run(argv: list[str], timeout: float = 30) -> RunResult:
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a list of str")
    if not argv:
        raise TypeError("argv must not be empty")
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return RunResult(TIMEOUT_RETURNCODE, "", "timed out")
    except OSError:
        return RunResult(NOT_FOUND_RETURNCODE, "", f"{argv[0]} not found")
    return RunResult(proc.returncode, proc.stdout, proc.stderr)
