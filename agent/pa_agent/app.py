"""Agent HTTPS API and the pa-agent command line."""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pa_core.auth import bearer_auth, install_error_handlers
from pa_core.models import ActionName, ActionResult, ApiResponse, Health, Program, UsbState, ok
from pa_core.policy import ActionNotAllowed
from pa_core.tls import fingerprint_sha256, make_self_signed
from pydantic import BaseModel

from pa_agent.config import AgentConfig, load_config
from pa_agent.device import LinuxDevice

DEFAULT_LOG_LINES = 100
DEFAULT_NODE_DEVICES = {
    "raspberrypi-1": "pi1",
    "raspberrypi-2": "robot-pi",
    "raspberrypi-3": "pi3",
}
CONFIG_NAME = "agent.toml"
CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
HUB_CLIENT = "hub"
PRIVATE_FILE_MODE = 0o600


class ActionRequest(BaseModel):
    id: str
    action: ActionName


class UsbRequest(BaseModel):
    on: bool


def _unknown_program(pid: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"unknown program: {pid}")


def _not_supported() -> HTTPException:
    return HTTPException(status_code=404, detail="not supported on this device")


def create_app(device: LinuxDevice, cfg: AgentConfig) -> FastAPI:
    app = FastAPI(title="pa-agent", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    authenticate = bearer_auth({cfg.token: HUB_CLIENT})

    def guard(request: Request) -> str:
        # IP first, so a stranger gets 403 without ever exercising the token check.
        if cfg.hub_ip is not None:
            host = request.client.host if request.client else None
            if host != cfg.hub_ip:
                raise HTTPException(status_code=403, detail="forbidden")
        return authenticate(request)

    router = APIRouter(prefix="/v1", dependencies=[Depends(guard)])

    @router.get("/health")
    def health() -> ApiResponse[Health]:
        return ok(device.health())

    @router.get("/programs")
    def programs() -> ApiResponse[list[Program]]:
        return ok(device.programs())

    @router.get("/program")
    def program(id: str) -> ApiResponse[Program]:
        try:
            return ok(device.program(id))
        except KeyError:
            raise _unknown_program(id) from None

    @router.get("/logs")
    def logs(id: str, lines: int = DEFAULT_LOG_LINES) -> ApiResponse[list[str]]:
        try:
            return ok(device.logs(id, lines))
        except KeyError:
            raise _unknown_program(id) from None

    @router.post("/actions")
    def actions(body: ActionRequest) -> ApiResponse[ActionResult]:
        try:
            return ok(device.act(body.id, body.action))
        except KeyError:
            raise _unknown_program(body.id) from None
        except ActionNotAllowed as exc:
            raise HTTPException(status_code=409, detail=str(exc) or "action not allowed") from None

    @router.post("/groups/{project}/restart")
    def group_restart(project: str) -> ApiResponse[ActionResult]:
        try:
            return ok(device.group_restart(project))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown group: {project}") from None

    @router.get("/usb")
    def usb() -> ApiResponse[UsbState]:
        try:
            return ok(device.usb())
        except NotImplementedError:
            raise _not_supported() from None

    @router.put("/usb")
    def set_usb(body: UsbRequest) -> ApiResponse[ActionResult]:
        try:
            return ok(device.set_usb(body.on))
        except NotImplementedError:
            raise _not_supported() from None

    @router.post("/reboot")
    def reboot() -> ApiResponse[ActionResult]:
        return ok(device.reboot())

    app.include_router(router)
    return app


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def render_config(cfg: AgentConfig) -> str:
    lines = [
        f"device_id = {_toml_str(cfg.device_id)}",
        f"token = {_toml_str(cfg.token)}",
        f"bind = {_toml_str(cfg.bind)}",
        f"port = {cfg.port}",
        f"cert_file = {_toml_str(cfg.cert_file)}",
        f"key_file = {_toml_str(cfg.key_file)}",
    ]
    if cfg.usb_script:
        lines.append(f"usb_script = {_toml_str(cfg.usb_script)}")
    if cfg.kubeconfig:
        lines.append(f"kubeconfig = {_toml_str(cfg.kubeconfig)}")
    if cfg.hub_ip:
        lines.append(f"hub_ip = {_toml_str(cfg.hub_ip)}")
    pairs = ", ".join(f"{_toml_str(k)} = {_toml_str(v)}" for k, v in cfg.node_devices.items())
    lines.append(f"node_devices = {{ {pairs} }}")
    return "\n".join(lines) + "\n"


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    path.chmod(PRIVATE_FILE_MODE)


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.out)
    targets = [out / CONFIG_NAME, out / CERT_NAME, out / KEY_NAME]
    existing = [t for t in targets if t.exists()]
    if existing:
        print(f"refusing to overwrite existing file: {existing[0]}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    cert_pem, key_pem = make_self_signed(args.device_id, [args.ip])
    cfg = AgentConfig(
        device_id=args.device_id,
        token=secrets.token_urlsafe(32),
        cert_file=str(out / CERT_NAME),
        key_file=str(out / KEY_NAME),
        usb_script=args.usb_script,
        kubeconfig=args.kubeconfig,
        node_devices=dict(DEFAULT_NODE_DEVICES),
    )
    (out / CERT_NAME).write_bytes(cert_pem)
    _write_private(out / KEY_NAME, key_pem)
    _write_private(out / CONFIG_NAME, render_config(cfg).encode("utf-8"))
    print(f"config:      {out / CONFIG_NAME}")
    print(f"fingerprint: {fingerprint_sha256(cert_pem)}")
    print(f"token:       {cfg.token}")
    print("Store the token now; it is not shown again.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    app = create_app(LinuxDevice(cfg), cfg)
    uvicorn.run(
        app, host=cfg.bind, port=cfg.port, ssl_certfile=cfg.cert_file, ssl_keyfile=cfg.key_file
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pa-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="write config, certificate and token")
    init.add_argument("--device-id", required=True)
    init.add_argument("--ip", required=True)
    init.add_argument("--usb-script")
    init.add_argument("--kubeconfig")
    init.add_argument("--out", required=True)
    init.set_defaults(func=cmd_init)
    serve = sub.add_parser("serve", help="run the HTTPS API")
    serve.add_argument("--config", required=True)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
