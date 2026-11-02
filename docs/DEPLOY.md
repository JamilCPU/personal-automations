# Deploying the Pi agent

Run from WSL (SSH to the Pis only works through WSL):

```bash
bash agent/deploy/deploy-agent.sh <ip> <device-id> [--usb-script /usr/local/bin/lidar-power] [--k3s] [--offline] [--show-token]
# e.g.
bash agent/deploy/deploy-agent.sh 192.168.0.103 pi3
bash agent/deploy/deploy-agent.sh 192.168.0.101 pi1 --k3s --offline
```

From Git Bash on Windows prefix with `MSYS_NO_PATHCONV=1 wsl.exe -e bash /mnt/f/Dev/personal-automations/agent/deploy/deploy-agent.sh ...`.

## Files

| File | Purpose |
|------|---------|
| `agent/deploy/deploy-agent.sh` | Builds the `pa-core`/`pa-agent` wheels, copies them over, creates the `pa-agent` user (groups `docker`, `systemd-journal`), venv in `/opt/personal-automations/venv`, runs `pa-agent init` once (never overwrites an existing `agent.toml`), installs the unit and sudoers, starts the service. |
| `agent/deploy/pa-agent.service` | systemd unit: `User=pa-agent`, `Restart=always`, `NoNewPrivileges=false` (sudo needs it), after `docker.service`. Listens on `:7701`. |
| `agent/deploy/personal-automations.sudoers` | `/etc/sudoers.d/personal-automations`: `systemctl start/stop/restart *`, `systemctl reboot`, `lidar-power on/off/status`. Checked with `visudo -cf` before it goes live. |
| `agent/deploy/k3s-rbac.yaml` | ServiceAccount `pa-agent` in `lab`, pod get/list/delete + `pods/log` in `lab`, cluster-wide pod get/list, and a token Secret. `--k3s` builds `/etc/personal-automations/kubeconfig` from that token (server `https://127.0.0.1:6443`). |

On each Pi: `/etc/personal-automations/{agent.toml,cert.pem,key.pem[,kubeconfig]}` (mode 600, owner `pa-agent`). The config has `hub_ip = 192.168.0.197` (jw, the hub host; override with `PA_HUB_IP`); only that address is served (IPv4-mapped IPv6 is treated as IPv4).

## Secrets

The script saves each agent's `cert.pem` and `token` outside the repo, in
`C:\Users\jamil\AppData\Local\personal-automations\agents\<device-id>\` (override with `PA_SECRETS_DIR`). The token is only generated on first init; the token is printed only with `--show-token`. Copy them to jw's `%LOCALAPPDATA%\personal-automations\agents\<device-id>\` (the hub runs there) and register them with `pa-hub add-agent` ([INSTALL.md](INSTALL.md) section 2).

## Notes

- Port 7701 must be free; the script aborts if something else holds it.
- `--offline` ships a wheelhouse (aarch64, Python 3.13) built on the PC. Pi1 needs it: its default route goes through Pi3 and it cannot reach PyPI.
- Verify: `curl --cacert <cert.pem> -H "Authorization: Bearer <token>" https://<ip>:7701/v1/programs` (401 without the header).

## Roll back

```bash
sudo systemctl disable --now pa-agent
sudo rm /etc/sudoers.d/personal-automations /etc/systemd/system/pa-agent.service
# optional full removal
sudo rm -rf /opt/personal-automations /etc/personal-automations && sudo userdel pa-agent
# k3s only
sudo k3s kubectl delete -f k3s-rbac.yaml
```
