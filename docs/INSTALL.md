# Install guide

Everything runs on the home LAN (`192.168.0.0/24`). The PC runs the hub (`192.168.0.99:7700`); each Pi runs an agent (`:7701`); the Pixel app and Claude Code talk only to the hub.

## 1. Hub (Windows PC)

From the repo root:

```powershell
uv sync
uv run pa-hub init --ip 192.168.0.99        # writes hub.toml, hub.secrets.toml, hub\cert.pem, hub\key.pem
```

Files land in `%LOCALAPPDATA%\personal-automations`. `init` refuses to overwrite existing files and creates two client tokens: `phone` and `mcp`.

Register each agent once it is deployed (section 2). The deploy script saves `cert.pem` and `token` under `%LOCALAPPDATA%\personal-automations\agents\<device-id>\`:

```powershell
$a = "$env:LOCALAPPDATA\personal-automations\agents"
uv run pa-hub add-agent --device pi3 --cert $a\pi3\cert.pem --token (Get-Content $a\pi3\token)
```

Repeat for `pi1` and `robot-pi`. Then, in an **elevated** PowerShell, install autostart (scheduled tasks for the hub and the MCP HTTP server, plus a LAN-only firewall rule for ports 7700 and 7702):

```powershell
uv run pa-mcp gen-token                 # prints a value; keep it
setx PA_MCP_HTTP_TOKEN <that value>     # user environment variable, read by the MCP task
.\hub\deploy\install-hub.ps1            # -Uninstall removes it
```

Log off and on (or start the tasks) so the new variable is picked up.

To run the hub by hand instead: `uv run pa-hub serve`.

## 2. Agents (Raspberry Pis)

Run from WSL. Full details, files and rollback are in [DEPLOY.md](DEPLOY.md).

```bash
bash agent/deploy/deploy-agent.sh 192.168.0.103 pi3
bash agent/deploy/deploy-agent.sh 192.168.0.101 pi1 --k3s --offline
bash agent/deploy/deploy-agent.sh 192.168.0.102 robot-pi --usb-script /usr/local/bin/lidar-power
```

Re-running keeps each Pi's `agent.toml` and token and force-reinstalls the wheels, so it is also the upgrade path.

## 3. Pair the phone

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
  -e PA_HUB_URL=https://192.168.0.99:7700 `
  -e PA_HUB_TOKEN=<mcp client token> `
  -e PA_HUB_CERT=$env:LOCALAPPDATA\personal-automations\hub\cert.pem `
  -- uv run --project F:\Dev\personal-automations pa-mcp
```

The `mcp` token is the key whose value is `"mcp"` in `hub.secrets.toml` (format `"<token>" = "<client>"`).

## 6. Rotate a client token

1. Edit `%LOCALAPPDATA%\personal-automations\hub.secrets.toml` and replace the token for `phone` and/or `mcp` with a new value (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Restart the hub (restart the "Personal Automations Hub" scheduled task, or `pa-hub serve`). Restart the "Personal Automations MCP" task too, since it reads the `mcp` token at launch.
3. Phone: run `pa-hub pair --client phone` again and scan the QR code.
4. Claude Code: `claude mcp remove personal-automations -s user`, then add it again with the new token (section 5).

Agent tokens live in each Pi's `/etc/personal-automations/agent.toml` and are mirrored on the PC under `agents\<device-id>\token`.

## 7. Keystore (back it up)

The release APK is signed with `%USERPROFILE%\.android\personal-automations.jks` (alias `pa`). The passwords are in `%USERPROFILE%\.gradle\gradle.properties` as `PA_STORE_FILE`, `PA_STORE_PASSWORD`, `PA_KEY_ALIAS`, `PA_KEY_PASSWORD`. Neither file is in the repo.

**Back up both the keystore and the passwords somewhere safe (a password manager).** If they are lost you cannot ship an update over the installed app: you would have to uninstall it (losing its data and pairing) and install a new one. Without the `PA_*` properties the build falls back to the debug key with a warning.
