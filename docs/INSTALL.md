# Install guide

Everything runs on the home LAN (`192.0.2.0/24`). **jw** (the Windows 11 "MC Server PC", hostname `JW`, `192.0.2.197`) runs the hub (`192.0.2.197:7700`) and the MCP HTTP server (`:7702`); each Pi runs an agent (`:7701`); the Pixel app and Claude Code talk only to the hub. Warstation (`192.0.2.99`) is only where you build, deploy agents and run Claude Code; it is not a device in v1.

## 1. Hub (jw)

### Reach jw

From **Git Bash** on Warstation, with the Windows SSH key (not WSL):

```bash
ssh user@192.0.2.197
```

The remote shell is `cmd.exe`; run `powershell` for the PowerShell commands below. The elevated step (installing autostart) needs an Administrator PowerShell on jw's own desktop (or RDP).

### Prerequisites on jw

- Python 3.12.7 is installed at `C:\Users\jamil\AppData\Local\Programs\Python\Python312\python.exe` but is **not on PATH**. The code supports Python >= 3.11, so 3.12 is fine.
- `uv` is **not installed** on jw (checked 2026-11-01). Install it for the user (it lands in `%USERPROFILE%\.local\bin`, which the installer adds to the user PATH; open a new shell afterwards):

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- Git is installed (`C:\Program Files\Git\cmd\git.exe`). Clone the repo, for example to `C:\Users\jamil\personal-automations` (`gh auth login` or a deploy key; the repo is private).

### Configure

From the repo root on jw, pin uv to the existing interpreter so it does not download another one:

```powershell
uv sync --all-packages --python C:\Users\jamil\AppData\Local\Programs\Python\Python312\python.exe
uv run pa-hub init                       # --ip defaults to 192.0.2.197
```

`init` writes `hub.toml`, `hub.secrets.toml`, `hub\cert.pem`, `hub\key.pem` to `%LOCALAPPDATA%\personal-automations` on jw. The certificate's SANs are `192.0.2.197` and `127.0.0.1`. `hub.toml` has `bind = ["192.0.2.197", "127.0.0.1"]`: the hub listens on the LAN IP and on loopback (for local tools), not on every interface. `init` refuses to overwrite existing files and creates three client tokens: `phone`, `mcp-stdio` (Claude Code over stdio) and `mcp-http` (the MCP HTTP server task). Separate tokens let the history show which path an action came from; the MCP server also adds the agent's name (for example `mcp-http:claude-code`).

Register each agent once it is deployed (section 2). The deploy script runs on Warstation and saves `cert.pem` and `token` there, under `%LOCALAPPDATA%\personal-automations\agents\<device-id>\`. Copy that folder to the same path on jw (for example `scp -r` from Git Bash), then on jw:

```powershell
$a = "$env:LOCALAPPDATA\personal-automations\agents"
uv run pa-hub add-agent --device pi3 --cert $a\pi3\cert.pem --token (Get-Content $a\pi3\token)
```

Repeat for `pi1` and `robot-pi`. Then, in an **elevated** PowerShell on jw, install autostart (scheduled tasks for the hub and the MCP HTTP server, plus a LAN-only firewall rule for ports 7700 and 7702):

```powershell
uv run pa-mcp gen-token                 # prints a value; keep it
setx PA_MCP_HTTP_TOKEN <that value>     # user environment variable, read by the MCP task
.\hub\deploy\install-hub.ps1            # -Uninstall removes it
```

Log off and on (or start the tasks) so the new variable is picked up. The MCP task binds `192.0.2.197:7702`.

To run the hub by hand instead: `uv run pa-hub serve`.

### Moving the hub from Warstation to jw

Agents only answer their configured `hub_ip`. Agents deployed while the hub ran on Warstation have `hub_ip = "192.0.2.99"` in `/etc/personal-automations/agent.toml`, and re-running the deploy script keeps that file. On each Pi set `hub_ip = "192.0.2.197"` and `sudo systemctl restart pa-agent`. Then re-pair the phone (section 3) and re-register Claude Code (section 5), because the hub URL, certificate and tokens change. On Warstation, remove the old hub's autostart with `.\hub\deploy\install-hub.ps1 -Uninstall`.

## 2. Agents (Raspberry Pis)

Run from WSL on Warstation. Full details, files and rollback are in [DEPLOY.md](DEPLOY.md).

```bash
bash agent/deploy/deploy-agent.sh 192.0.2.103 pi3
bash agent/deploy/deploy-agent.sh 192.0.2.101 pi1 --k3s --offline
bash agent/deploy/deploy-agent.sh 192.0.2.102 robot-pi --usb-script /usr/local/bin/lidar-power
```

Re-running keeps each Pi's `agent.toml` and token and force-reinstalls the wheels, so it is also the upgrade path.

## 3. Pair the phone

On jw (an SSH session works; the QR code prints as text):

```powershell
uv run pa-hub pair --client phone
```

It prints a QR code and the pairing URI. Scan it with the Pixel camera and open the link. The app pins the hub certificate by fingerprint and stores the token.

## 4. Install the Android app

Build a signed release (needs the keystore, section 7):

```powershell
cd android
.\gradlew.bat testDebugUnitTest assembleRelease
# -> app\build\outputs\apk\release\app-release.apk
```

On the Pixel: Settings > System > Developer options > Wireless debugging > Pair device with pairing code. Then:

```powershell
adb pair <ip>:<pairing-port>        # enter the 6-digit code
adb connect <ip>:<connect-port>     # the port shown on the Wireless debugging screen
adb install -r android\app\build\outputs\apk\release\app-release.apk
```

Updates must be signed with the same keystore, otherwise `adb install -r` fails with a signature mismatch.

## 5. Claude Code MCP

```powershell
claude mcp add personal-automations -s user `
  -e PA_HUB_URL=https://192.0.2.197:7700 `
  -e PA_HUB_TOKEN=<mcp-stdio client token> `
  -e PA_HUB_CERT=<copy of jw's hub\cert.pem on this machine> `
  -- uv run --project F:\Dev\personal-automations pa-mcp
```

The `mcp-stdio` token is the key whose value is `"mcp-stdio"` in `hub.secrets.toml` (format `"<token>" = "<client>"`).

## 6. Rotate a client token

1. Edit `%LOCALAPPDATA%\personal-automations\hub.secrets.toml` and replace the token for `phone`, `mcp-stdio` and/or `mcp-http` with a new value (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Restart the hub (restart the "Personal Automations Hub" scheduled task, or `pa-hub serve`). Restart the "Personal Automations MCP" task too, since it reads the `mcp-http` token at launch.
3. Phone: run `pa-hub pair --client phone` again and scan the QR code.
4. Claude Code: `claude mcp remove personal-automations -s user`, then add it again with the new token (section 5).

Agent tokens live in each Pi's `/etc/personal-automations/agent.toml` and are mirrored on the PC under `agents\<device-id>\token`.

## 7. Keystore (back it up)

The release APK is signed with `%USERPROFILE%\.android\personal-automations.jks` (alias `pa`). The passwords are in `%USERPROFILE%\.gradle\gradle.properties` as `PA_STORE_FILE`, `PA_STORE_PASSWORD`, `PA_KEY_ALIAS`, `PA_KEY_PASSWORD`. Neither file is in the repo.

**Back up both the keystore and the passwords somewhere safe (a password manager).** If they are lost you cannot ship an update over the installed app: you would have to uninstall it (losing its data and pairing) and install a new one. Without the `PA_*` properties the build falls back to the debug key with a warning.
