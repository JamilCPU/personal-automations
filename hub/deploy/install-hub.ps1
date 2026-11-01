<#
.SYNOPSIS
  Installs (or removes) the Personal Automations hub autostart on Windows.
.DESCRIPTION
  Run from an elevated PowerShell. Idempotent: re-running replaces the task and rule.
    - Scheduled task "Personal Automations Hub": at logon of the current user, highest
      privileges, restarts on failure, runs `uv run --project <repo> pa-hub serve`.
    - Firewall rule "Personal Automations Hub": inbound TCP 7700,7702 from 192.0.2.0/24,
      Private profile only.
  -Uninstall removes both.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Personal Automations Hub'
$RuleName = 'Personal Automations Hub'
$Ports = '7700,7702'
$LanSubnet = '192.0.2.0/24'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated (Administrator) PowerShell.'
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "Scheduled task '$TaskName' not present."
    }
    if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -DisplayName $RuleName
        Write-Host "Removed firewall rule '$RuleName'."
    } else {
        Write-Host "Firewall rule '$RuleName' not present."
    }
    return
}

$uv = (Get-Command uv -ErrorAction Stop).Source
$action = New-ScheduledTaskAction -Execute $uv `
    -Argument "run --project `"$ProjectDir`" pa-hub serve" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

$existed = [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host ("{0} scheduled task '{1}' (at logon of {2}, highest privileges, restart on failure)." -f `
    ($(if ($existed) { 'Updated' } else { 'Created' }), $TaskName, $identity.Name))

$ruleExisted = [bool](Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)
if ($ruleExisted) { Remove-NetFirewallRule -DisplayName $RuleName }
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort $Ports.Split(',') -RemoteAddress $LanSubnet -Profile Private | Out-Null
Write-Host ("{0} firewall rule '{1}' (TCP {2} from {3}, Private profile)." -f `
    ($(if ($ruleExisted) { 'Replaced' } else { 'Created' }), $RuleName, $Ports, $LanSubnet))

Write-Host "Start now with: Start-ScheduledTask -TaskName '$TaskName' (or sign out and back in)."
