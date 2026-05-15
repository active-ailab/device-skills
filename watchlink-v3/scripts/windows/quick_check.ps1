param(
    [switch]$Help,
    [string]$UartPort,
    [string]$MgrPort,
    [switch]$SmokeMonkey,
    [int]$IntervalMs = 1000,
    [ValidateSet('on','off')]
    [string]$Print = 'off',
    [ValidateSet('0','6')]
    [string]$Mem = '0'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Output @"
Usage:
  quick_check.ps1 [-UartPort COMx] [-MgrPort COMx] [-SmokeMonkey] [-IntervalMs N] [-Print on|off] [-Mem 0|6]

Behavior:
  - detect or validate UART/MGR ports
  - query WL+FWVER=? on MGR
  - query monkey status on UART
  - optionally run a monkey on/off smoke test and restore the original state
"@
    exit 0
}
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Wlctl = Join-Path $ScriptDir 'wlctl.ps1'
$PortResolver = Join-Path $ScriptDir 'resolve_wlink_ports.ps1'

if (-not (Test-Path $Wlctl)) {
    throw "wlctl.ps1 not found: $Wlctl"
}
if (-not (Test-Path $PortResolver)) {
    throw "resolve_wlink_ports.ps1 not found: $PortResolver"
}
. $PortResolver

function Resolve-Port([string]$Role, [string]$ExplicitPort) {
    return Resolve-WlinkRolePort -Role $Role -ExplicitPort $ExplicitPort -NoPortHint "Pass -$($Role.Substring(0,1).ToUpper() + $Role.Substring(1).ToLower())Port explicitly or make v3dl available."
}
function Invoke-Wlctl([string[]]$ScriptArgs) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Wlctl @ScriptArgs
}

function Write-Step([string]$Message) {
    Write-Output "[STEP] $Message"
}

$uart = Resolve-Port -Role 'UART' -ExplicitPort $UartPort
$mgr = Resolve-Port -Role 'MGR' -ExplicitPort $MgrPort

Write-Output "[INFO] UART port: $uart"
Write-Output "[INFO] MGR port: $mgr"

Write-Step 'Querying firmware version via MGR'
Invoke-Wlctl @('serial','WL+FWVER=?','-Role','MGR','-Port',$mgr)

Write-Step 'Querying monkey status via UART'
$statusOutput = @(Invoke-Wlctl @('monkey','status','-Port',$uart))
$statusOutput | ForEach-Object { $_ }

$initialState = 'unknown'
if (($statusOutput -join "`n") -match 'monkey status on') { $initialState = 'on' }
elseif (($statusOutput -join "`n") -match 'monkey status off') { $initialState = 'off' }

if ($initialState -eq 'unknown') {
    throw 'Monkey status check did not return a recognizable on/off state.'
}

if (-not $SmokeMonkey) {
    Write-Output "[PASS] Quick check completed without monkey state changes."
    exit 0
}

Write-Step 'Running monkey control smoke test'
Invoke-Wlctl @('monkey','off','-Port',$uart)
Invoke-Wlctl @('monkey','on','-Port',$uart,'-IntervalMs',"$IntervalMs",'-Print',$Print,'-Mem',$Mem)
$afterOn = @(Invoke-Wlctl @('monkey','status','-Port',$uart))
$afterOn | ForEach-Object { $_ }
if (($afterOn -join "`n") -notmatch 'monkey status on') {
    throw 'Monkey smoke test failed: status did not become on.'
}

if ($initialState -eq 'off') {
    Write-Step 'Restoring original monkey state to off'
    Invoke-Wlctl @('monkey','off','-Port',$uart)
} elseif ($initialState -eq 'on') {
    Write-Step 'Restoring original monkey state to on'
    Invoke-Wlctl @('monkey','on','-Port',$uart,'-IntervalMs',"$IntervalMs",'-Print',$Print,'-Mem',$Mem)
}

Write-Output "[PASS] Quick check completed, monkey control works on $uart."








