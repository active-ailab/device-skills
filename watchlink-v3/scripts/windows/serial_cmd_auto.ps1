param(
    [ValidateSet('UART','MGR')]
    [string]$Role = 'MGR',

    [string]$Port,
    [string]$Command,

    [int]$BaudRate = 115200,
    [int]$ReadTimeoutMs = 400,
    [int]$WriteTimeoutMs = 1000,
    [int]$ReadWindowMs = 1200,

    [switch]$Listen,
    [int]$ListenSeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PortResolver = Join-Path $ScriptDir 'resolve_wlink_ports.ps1'
if (-not (Test-Path $PortResolver)) {
    throw "resolve_wlink_ports.ps1 not found: $PortResolver"
}
. $PortResolver

function Get-CandidatePorts([string]$role) {
    return @(Get-WlinkRoleCandidates -Role $role)
}

function Select-Port([string]$role, [string]$explicitPort) {
    return Resolve-WlinkRolePort -Role $role -ExplicitPort $explicitPort -NoPortHint "Please run 'v3dl com' and connect Watch-Link device."
}

$selectedPort = Select-Port -role $Role -explicitPort $Port

try {
    $sp = New-Object System.IO.Ports.SerialPort
} catch {
    throw "SerialPort type is unavailable in current PowerShell runtime: $($_.Exception.Message)"
}

$sp.PortName = $selectedPort
$sp.BaudRate = $BaudRate
$sp.DataBits = 8
$sp.Parity = [System.IO.Ports.Parity]::None
$sp.StopBits = [System.IO.Ports.StopBits]::One
$sp.ReadTimeout = $ReadTimeoutMs
$sp.WriteTimeout = $WriteTimeoutMs
$sp.NewLine = "`r`n"

try {
    $sp.Open()
    Start-Sleep -Milliseconds 120
    $sp.DiscardInBuffer()
    $sp.DiscardOutBuffer()

    if ($Listen) {
        $deadline = (Get-Date).AddSeconds($ListenSeconds)
        Write-Output "[INFO] Listening on $selectedPort ($Role) for $ListenSeconds seconds..."
        while ((Get-Date) -lt $deadline) {
            try {
                $line = $sp.ReadLine()
                if ($line) { Write-Output $line }
            } catch [System.TimeoutException] {}
        }
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($Command)) {
        throw "-Command is required unless -Listen is provided."
    }

    $sp.WriteLine($Command)

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lines = New-Object System.Collections.Generic.List[string]

    while ($sw.ElapsedMilliseconds -lt $ReadWindowMs) {
        try {
            $line = $sp.ReadLine()
            if ($line) { $lines.Add($line) }
        } catch [System.TimeoutException] {
            # Normal: no data within ReadTimeoutMs, keep waiting.
        } catch {
            # Port disappeared (USB re-enumeration during disk-mode transitions).
            # V3: serial + disk share the same physical USB — switching disk mode
            # tears down the serial port.  This is expected, not an error.
            Write-Output "[WARN] Serial port disconnected (expected during disk-mode / USB re-enumeration)"
            break
        }
    }

    if ($lines.Count -eq 0) {
        Write-Output "[WARN] No response within ${ReadWindowMs}ms"
    } else {
        $lines | ForEach-Object { Write-Output $_ }
    }
}
finally {
    if ($sp -and $sp.IsOpen) { $sp.Close() }
    if ($sp) { $sp.Dispose() }
}




