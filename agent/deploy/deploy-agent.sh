#!/usr/bin/env bash
# Deploy the Personal Automations agent to one Pi. Run from WSL:
#   bash agent/deploy/deploy-agent.sh <ip> <device-id> [--usb-script PATH] [--k3s] [--offline] [--show-token]
# --offline: the Pi has no internet (e.g. pi1); ship a wheelhouse built on this PC (aarch64, py3.13).
# Never interactive: ssh BatchMode, sudo -n. Never overwrites an existing agent.toml.
# Secrets (cert.pem, token) are saved outside the repo in $PA_SECRETS_DIR/<device-id>/.
set -euo pipefail

HUB_IP="${PA_HUB_IP:-192.168.0.99}"
SSH_USER="${PA_SSH_USER:-jamil}"
SECRETS_DIR="${PA_SECRETS_DIR:-/mnt/c/Users/jamil/AppData/Local/personal-automations/agents}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

usage() { echo "usage: $0 <ip> <device-id> [--usb-script PATH] [--k3s] [--offline] [--show-token]" >&2; exit 2; }
[ $# -ge 2 ] || usage
IP="$1"; DEVICE="$2"; shift 2
USB_SCRIPT=""; K3S=0; OFFLINE=0; SHOW_TOKEN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --usb-script) [ $# -ge 2 ] || usage; USB_SCRIPT="$2"; shift 2 ;;
    --k3s) K3S=1; shift ;;
    --offline) OFFLINE=1; shift ;;
    --show-token) SHOW_TOKEN=1; shift ;;
    *) usage ;;
  esac
done
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad ip: $IP" >&2; exit 2; }
[[ "$DEVICE" =~ ^[a-z0-9-]+$ ]] || { echo "bad device-id: $DEVICE" >&2; exit 2; }
[[ -z "$USB_SCRIPT" || "$USB_SCRIPT" =~ ^/[A-Za-z0-9/_.-]+$ ]] || { echo "bad usb script path" >&2; exit 2; }

TARGET="$SSH_USER@$IP"
SSH=(ssh -o BatchMode=yes "$TARGET")
STAGE="/tmp/pa-deploy"

# 1. Build wheels (uv in WSL, else the Windows uv.exe via interop, else reuse dist/).
cd "$ROOT"
UV="$(command -v uv || command -v uv.exe || true)"
if [ -n "$UV" ]; then
  rm -rf dist
  "$UV" build --package pa-core --out-dir dist
  "$UV" build --package pa-agent --out-dir dist
else
  echo "warning: uv not found, using existing dist/" >&2
fi
ls dist/pa_core-*.whl dist/pa_agent-*.whl >/dev/null
if [ "$OFFLINE" = 1 ]; then
  PIP_DL=(pip download -q -d dist/wheelhouse --platform manylinux_2_28_aarch64
    --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 --python-version 3.13
    --implementation cp --only-binary=:all: dist/pa_core-*.whl dist/pa_agent-*.whl
    uvloop httptools watchfiles websockets pyyaml python-dotenv)  # uvicorn[standard] extras: markers are evaluated for the host OS, not the Pi
  UVX="$(command -v uvx || command -v uvx.exe)"
  rm -rf dist/wheelhouse
  "$UVX" "${PIP_DL[@]}"
fi

# 2. Port 7701 must be free unless our own unit already owns it.
if "${SSH[@]}" 'ss -ltn | grep -q ":7701 "' && ! "${SSH[@]}" 'systemctl is-active --quiet pa-agent'; then
  echo "port 7701 is in use by something else on $IP; aborting" >&2
  exit 1
fi

# 3. Copy wheels and deploy files.
"${SSH[@]}" "rm -rf $STAGE && mkdir -p $STAGE"
scp -o BatchMode=yes dist/pa_core-*.whl dist/pa_agent-*.whl \
  "$HERE/pa-agent.service" "$HERE/personal-automations.sudoers" "$HERE/k3s-rbac.yaml" \
  "$TARGET:$STAGE/"
if [ "$OFFLINE" = 1 ]; then scp -o BatchMode=yes -r dist/wheelhouse "$TARGET:$STAGE/wheelhouse"; fi

# 4. Install on the Pi (root via sudo -n). Prints "INIT|fp|token" on first init, always "CERT|b64".
read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -euo pipefail
DEVICE="$1"; IP="$2"; HUB_IP="$3"; USB_SCRIPT="$4"; K3S="$5"; STAGE="$6"; OFFLINE="$7"
[ "$USB_SCRIPT" != "-" ] || USB_SCRIPT=""   # ssh drops empty args, so "-" means none
CONF=/etc/personal-automations
OPT=/opt/personal-automations

id pa-agent >/dev/null 2>&1 || useradd --system --home-dir "$OPT" --shell /usr/sbin/nologin pa-agent
usermod -aG docker,systemd-journal pa-agent

mkdir -p "$OPT" "$CONF"
chown pa-agent:pa-agent "$CONF"
chmod 700 "$CONF"
[ -x "$OPT/venv/bin/python" ] || python3 -m venv "$OPT/venv"
PIP_OPTS=(--quiet)
[ "$OFFLINE" != 1 ] || PIP_OPTS+=(--no-index --find-links "$STAGE/wheelhouse")
"$OPT/venv/bin/pip" install "${PIP_OPTS[@]}" "$STAGE"/pa_core-*.whl "$STAGE"/pa_agent-*.whl
"$OPT/venv/bin/pip" install --quiet --no-deps --force-reinstall "$STAGE"/pa_core-*.whl "$STAGE"/pa_agent-*.whl

KUBE_ARGS=()
if [ "$K3S" = 1 ]; then
  k3s kubectl apply -f "$STAGE/k3s-rbac.yaml" >&2
  TOKEN=""
  for _ in $(seq 1 30); do
    TOKEN="$(k3s kubectl -n lab get secret pa-agent-token -o jsonpath='{.data.token}' | base64 -d)"
    [ -n "$TOKEN" ] && break
    sleep 1
  done
  [ -n "$TOKEN" ] || { echo "SA token not populated" >&2; exit 1; }
  CA="$(k3s kubectl -n lab get secret pa-agent-token -o jsonpath='{.data.ca\.crt}')"
  (
    umask 077
    cat > "$CONF/kubeconfig" <<KUBE
apiVersion: v1
kind: Config
clusters:
- name: k3s
  cluster:
    server: https://127.0.0.1:6443
    certificate-authority-data: $CA
users:
- name: pa-agent
  user:
    token: $TOKEN
contexts:
- name: pa-agent
  context: {cluster: k3s, user: pa-agent, namespace: lab}
current-context: pa-agent
KUBE
  )
  chown pa-agent:pa-agent "$CONF/kubeconfig"
  chmod 600 "$CONF/kubeconfig"
  KUBE_ARGS=(--kubeconfig "$CONF/kubeconfig")
fi

if [ ! -e "$CONF/agent.toml" ]; then
  USB_ARGS=()
  [ -z "$USB_SCRIPT" ] || USB_ARGS=(--usb-script "$USB_SCRIPT")
  INIT_OUT="$(sudo -u pa-agent "$OPT/venv/bin/pa-agent" init --device-id "$DEVICE" --ip "$IP" \
    --hub-ip "$HUB_IP" --out "$CONF" "${USB_ARGS[@]}" "${KUBE_ARGS[@]}")"
  FP="$(printf '%s\n' "$INIT_OUT" | sed -n 's/^fingerprint: //p')"
  TK="$(printf '%s\n' "$INIT_OUT" | sed -n 's/^token: *//p')"
  echo "INIT|$FP|$TK"
fi
chmod 600 "$CONF"/*.pem "$CONF/agent.toml"

# Sudoers: validate before it goes live.
TMP=/etc/sudoers.d/.personal-automations.tmp
install -m 440 -o root -g root "$STAGE/personal-automations.sudoers" "$TMP"
if ! visudo -cf "$TMP" >&2; then
  rm -f "$TMP"
  echo "sudoers invalid" >&2
  exit 1
fi
mv "$TMP" /etc/sudoers.d/personal-automations

install -m 644 "$STAGE/pa-agent.service" /etc/systemd/system/pa-agent.service
systemctl daemon-reload
systemctl enable pa-agent >&2
systemctl restart pa-agent
sleep 2
systemctl is-active pa-agent >&2
echo "CERT|$(base64 -w0 "$CONF/cert.pem")"
REMOTE

REMOTE_OUT="$(printf '%s\n' "$REMOTE_SCRIPT" | "${SSH[@]}" sudo -n bash -s -- \
  "$DEVICE" "$IP" "$HUB_IP" "${USB_SCRIPT:--}" "$K3S" "$STAGE" "$OFFLINE")"

# 5. Save secrets outside the repo and report.
DEST="$SECRETS_DIR/$DEVICE"
mkdir -p "$DEST"
printf '%s\n' "$REMOTE_OUT" | sed -n 's/^CERT|//p' | base64 -d > "$DEST/cert.pem"
INIT_LINE="$(printf '%s\n' "$REMOTE_OUT" | grep '^INIT|' || true)"
if [ -n "$INIT_LINE" ]; then
  IFS='|' read -r _ FP TOKEN <<<"$INIT_LINE"
  (umask 077; printf '%s\n' "$TOKEN" > "$DEST/token")
  echo "fingerprint: $FP"
  if [ "$SHOW_TOKEN" = 1 ]; then echo "token:       $TOKEN"; fi
  echo "token saved: $DEST/token (generated once; the Pi keeps it in agent.toml)"
else
  echo "existing agent.toml kept; token unchanged (see $DEST/token if saved earlier)"
fi
echo "cert saved:  $DEST/cert.pem"
"${SSH[@]}" "rm -rf $STAGE"
echo "deployed $DEVICE at https://$IP:7701"
