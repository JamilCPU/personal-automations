<#
.SYNOPSIS
  Launches the Personal Automations MCP server over streamable HTTP (port 7702).
.DESCRIPTION
  Used by the "Personal Automations MCP" scheduled task. Reads the hub's `mcp-http` client token and
  certificate from %LOCALAPPDATA%\personal-automations, then runs `pa-mcp --http`.
  Requires the user environment variable PA_MCP_HTTP_TOKEN (generate one with
  `uv run pa-mcp gen-token`, then `setx PA_MCP_HTTP_TOKEN <token>`). Agents on other machines send
  it as `Authorization: Bearer <token>` to https://192.0.2.197:7702/mcp (jw, the hub host).
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$BindHost = '192.0.2.197',
    [int]$Port = 7702
)

$ErrorActionPreference = 'Stop'
$home_ = Join-Path $env:LOCALAPPDATA 'personal-automations'
$secrets = Get-Content (Join-Path $home_ 'hub.secrets.toml') -Raw
$match = [regex]::Match($secrets, '(?m)^\s*"([^"]+)"\s*=\s*"mcp-http"\s*$')
if (-not $match.Success) { throw 'No "mcp-http" client token found in hub.secrets.toml.' }
if (-not $env:PA_MCP_HTTP_TOKEN) {
    throw 'PA_MCP_HTTP_TOKEN is not set (uv run pa-mcp gen-token; setx PA_MCP_HTTP_TOKEN <token>).'
}

$env:PA_HUB_URL = "https://$($BindHost):7700"
$env:PA_HUB_TOKEN = $match.Groups[1].Value
$env:PA_HUB_CERT = Join-Path $home_ 'hub\cert.pem'
# Serve over TLS with the hub's certificate (SANs 192.0.2.197 and 127.0.0.1).
$env:PA_MCP_TLS_CERT = $env:PA_HUB_CERT
$env:PA_MCP_TLS_KEY = Join-Path $home_ 'hub\key.pem'
& uv run --project $ProjectDir pa-mcp --http --host $BindHost --port $Port
exit $LASTEXITCODE
