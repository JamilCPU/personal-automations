"""The pa-hub command line: init, add-agent, serve, pair."""

import argparse
import logging
import secrets
import shutil
import sys
from pathlib import Path

import qrcode
import uvicorn
from pa_core.tls import fingerprint_sha256, make_self_signed

from pa_hub.app import Closer, create_app, pair_uri
from pa_hub.clients import DeviceClient, RemoteAgentClient, UnavailableDevice
from pa_hub.config import HubConfig, default_home, load_hub_config
from pa_hub.history import HistoryStore
from pa_hub.service import HubService
from pa_hub.windows import LocalWindowsDevice

CONFIG_NAME = "hub.toml"
SECRETS_NAME = "hub.secrets.toml"
EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "hub.example.toml"
EXAMPLE_HOME = r"%LOCALAPPDATA%\personal-automations"
EXAMPLE_IP = "192.168.0.99"
LOOPBACK = "127.0.0.1"
INIT_CLIENTS = ("phone", "mcp")

logger = logging.getLogger("pa_hub")


def build_devices(cfg: HubConfig) -> tuple[dict[str, DeviceClient], list[Closer]]:
    """Create one client per configured device; undeployed agents become offline stand-ins."""
    devices: dict[str, DeviceClient] = {}
    closers: list[Closer] = []
    for entry in cfg.devices:
        if entry.kind == "local":
            devices[entry.id] = LocalWindowsDevice(device_id=entry.id)
            continue
        try:
            remote = RemoteAgentClient(entry, cfg.timeout_s, cfg.action_timeout_s)
        except (ValueError, OSError) as exc:
            logger.warning("device %s unavailable: %s", entry.id, exc)
            devices[entry.id] = UnavailableDevice(entry)
        else:
            devices[entry.id] = remote
            closers.append(remote.aclose)
    return devices, closers


def build_service(cfg: HubConfig) -> tuple[HubService, list[Closer]]:
    devices, closers = build_devices(cfg)
    service = HubService(
        devices,
        {entry.id: entry for entry in cfg.devices},
        HistoryStore(cfg.db_path),
        cfg.timeout_s,
        action_timeout_s=cfg.action_timeout_s,
    )
    return service, closers


def _paths(config: str | None) -> tuple[Path, Path]:
    config_path = Path(config) if config else default_home() / CONFIG_NAME
    return config_path, config_path.with_name(SECRETS_NAME)


def render_hub_toml(out: Path, ip: str) -> str:
    text = EXAMPLE_PATH.read_text(encoding="utf-8")
    return text.replace(EXAMPLE_HOME, str(out)).replace(EXAMPLE_IP, ip)


def render_secrets_toml(tokens: dict[str, str]) -> str:
    lines = ["[clients]"]
    lines.extend(f'"{token}" = "{name}"' for name, token in tokens.items())
    return "\n".join(lines) + "\n"


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else default_home()
    cert_path, key_path = out / "hub" / "cert.pem", out / "hub" / "key.pem"
    targets = [out / CONFIG_NAME, out / SECRETS_NAME, cert_path, key_path]
    existing = [t for t in targets if t.exists()]
    if existing:
        print(f"refusing to overwrite existing file: {existing[0]}", file=sys.stderr)
        return 1
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_pem, key_pem = make_self_signed("pa-hub", [args.ip, LOOPBACK])
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    tokens = {name: secrets.token_urlsafe(32) for name in INIT_CLIENTS}
    (out / SECRETS_NAME).write_text(
        render_secrets_toml(tokens), encoding="utf-8"
    )
    (out / CONFIG_NAME).write_text(render_hub_toml(out, args.ip), encoding="utf-8")
    print(f"config:      {out / CONFIG_NAME}")
    print(f"secrets:     {out / SECRETS_NAME}  (clients: {', '.join(INIT_CLIENTS)})")
    print(f"fingerprint: {fingerprint_sha256(cert_pem)}")
    print("Pair a client with: pa-hub pair --client phone")
    return 0


def cmd_add_agent(args: argparse.Namespace) -> int:
    config_path, secrets_path = _paths(args.config)
    cfg = load_hub_config(config_path, secrets_path)
    entry = next((d for d in cfg.devices if d.id == args.device), None)
    if entry is None or entry.kind != "agent":
        print(f"unknown agent device: {args.device}", file=sys.stderr)
        return 1
    agent_dir = config_path.parent / "agents" / entry.id
    cert_dest, token_dest = agent_dir / "cert.pem", agent_dir / "token"
    if args.cert and not Path(args.cert).is_file():
        print(f"cert file not found: {args.cert}", file=sys.stderr)
        return 1
    has_cert = bool(args.cert) or cert_dest.is_file()
    has_token = bool(args.token and args.token.strip()) or token_dest.is_file()
    if not (has_cert and has_token):
        missing = [n for n, ok in (("cert.pem", has_cert), ("token", has_token)) if not ok]
        print(f"{entry.id}: missing {', '.join(missing)} (nothing written)", file=sys.stderr)
        return 1
    agent_dir.mkdir(parents=True, exist_ok=True)
    if args.cert:
        shutil.copyfile(args.cert, cert_dest)
    if args.token and args.token.strip():
        token_dest.write_text(args.token.strip(), encoding="utf-8")
    print(f"{entry.id}: cert and token present in {agent_dir}")
    return 0


def _print_qr(uri: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.make(fit=True)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    qr.print_ascii(out=sys.stdout, invert=True)


def cmd_pair(args: argparse.Namespace) -> int:
    config_path, secrets_path = _paths(args.config)
    cfg = load_hub_config(config_path, secrets_path)
    token = next((t for t, name in cfg.clients.items() if name == args.client), None)
    if token is None:
        print(f"unknown client: {args.client}", file=sys.stderr)
        return 1
    local = next((d for d in cfg.devices if d.kind == "local"), None)
    url = args.url or f"https://{local.ip if local else LOOPBACK}:{cfg.port}"
    fingerprint = fingerprint_sha256(Path(cfg.cert_file).read_bytes())
    uri = pair_uri(url, token, fingerprint)
    _print_qr(uri)
    print(uri)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO)
    config_path, secrets_path = _paths(args.config)
    cfg = load_hub_config(config_path, secrets_path)
    service, closers = build_service(cfg)
    names = {entry.id: entry.name for entry in cfg.devices}
    app = create_app(service, cfg.clients, names, closers)
    uvicorn.run(
        app,
        host=cfg.bind,
        port=cfg.port,
        ssl_certfile=cfg.cert_file,
        ssl_keyfile=cfg.key_file,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pa-hub")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="write hub.toml, secrets, certificate")
    init.add_argument("--ip", default=EXAMPLE_IP)
    init.add_argument("--out")
    init.set_defaults(func=cmd_init)
    add = sub.add_parser("add-agent", help="register an agent's cert and token")
    add.add_argument("--device", required=True)
    add.add_argument("--cert")
    add.add_argument("--token")
    add.add_argument("--config")
    add.set_defaults(func=cmd_add_agent)
    serve = sub.add_parser("serve", help="run the HTTPS API")
    serve.add_argument("--config")
    serve.set_defaults(func=cmd_serve)
    pair = sub.add_parser("pair", help="print a pairing QR code and URI")
    pair.add_argument("--client", required=True)
    pair.add_argument("--url")
    pair.add_argument("--config")
    pair.set_defaults(func=cmd_pair)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
