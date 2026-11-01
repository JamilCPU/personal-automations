<#
.SYNOPSIS
  Launches the Personal Automations MCP server over streamable HTTP (port 7702).
.DESCRIPTION
  Used by the "Personal Automations MCP" scheduled task. Reads the hub's `mcp` client token and
  certificate from %LOCALAPPDATA%\personal-automations, then runs `pa-mcp --http`.
  Requires the user environment variable PA_MCP_HTTP_TOKEN (generate one with
  `uv run pa-mcp gen-token`, then `setx PA_MCP_HTTP_TOKEN <token>`). Agents on other machines send
  it as `Authorization: Bearer <token>` to http://192.0.2.99:7702/mcp.
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$BindHost = '192.0.2.99',
    [int]$Port = 7702
)

$ErrorActionPreference = 'Stop'
$home_ = Join-Path $env:LOCALAPPDATA 'personal-automations'
$secrets = Get-Content (Join-Path $home_ 'hub.secrets.toml') -Raw
$match = [regex]::Match($secrets, '(?m)^\s*"([^"]+)"\s*=\s*"mcp"\s*$')
if (-not $match.Success) { throw 'No "mcp" client token found in hub.secrets.toml.' }
if (-not $env:PA_MCP_HTTP_TOKEN) {
    throw 'PA_MCP_HTTP_TOKEN is not set (uv run pa-mcp gen-token; setx PA_MCP_HTTP_TOKEN <token>).'
}

$env:PA_HUB_URL = "https://$($BindHost):7700"
$env:PA_HUB_TOKEN = $match.Groups[1].Value
$env:PA_HUB_CERT = Join-Path $home_ 'hub\cert.pem'
& uv run --project $ProjectDir pa-mcp --http --host $BindHost --port $Port
exit $LASTEXITCODE
