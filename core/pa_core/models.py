"""Shared data models for Personal Automations."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

Kind = Literal["docker", "systemd", "k3s", "windows-service", "process"]
Status = Literal["running", "stopped", "restarting", "failed", "unknown"]
ActionName = Literal["start", "stop", "restart"]
Protection = Literal["none", "confirm", "locked"]

T = TypeVar("T")


class Program(BaseModel):
    id: str
    name: str
    kind: Kind
    device: str
    status: Status
    uptime_s: int | None = None
    description: str = ""
    system: bool
    protection: Protection
    actions: list[ActionName]
    group: str | None = None
    detail: dict[str, str] = Field(default_factory=dict)


class Health(BaseModel):
    cpu_pct: float
    mem_pct: float
    disk_pct: float
    temp_c: float | None = None
    uptime_s: int
    gpu: dict[str, float] | None = None


class ActionResult(BaseModel):
    ok: bool
    message: str


class UsbState(BaseModel):
    on: bool
    detail: str


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None


def ok(data: T) -> ApiResponse[T]:
    return ApiResponse(success=True, data=data, error=None)


def fail(msg: str) -> ApiResponse[None]:
    return ApiResponse(success=False, data=None, error=msg)
