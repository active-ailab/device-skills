param(
    [Parameter(Position=0)]
    [ValidateSet('help','serial','listen','monkey','disk','fs','vbus','ready','awake','diagnose')]
    [string]$Action,

    [Parameter(Position=1)]
    [string]$Arg1,

    [string]$Role,
    [string]$Port,
    [string]$Command,
    [string]$Message,
    [string]$MessageFile,
    [ValidateSet('data','log')]
    [string]$Disk,
    [string]$FsPath,
    [string]$FromPath,
    [string]$ToPath,
    [Alias('Output')]
    [ValidateSet('json','text')]
    [string]$OutputFormat = 'text',
    [int]$ReadMs = 1500,
    [int]$Seconds = 10,
    [int]$Timeout = 60,
    [string]$ProbeCommand = 'monkey -g',
    [string]$Encoding = 'utf-8',
    [switch]$Recursive,
    [switch]$MissingOk,

    [int]$IntervalMs = 1000,
    [ValidateSet('on','off')]
    [string]$Print = 'off',
    [ValidateSet('0','6')]
    [string]$Mem = '0',

    [Alias('h')]
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:LastChildExitCode = 0

trap {
    Write-Error $_
    exit ([Math]::Max(1, $script:LastChildExitCode))
}

function Show-Usage {
    Write-Output @"
wlctl - stable entrypoint for device-watchlink-v3

Usage:
  wlctl.ps1 serial -Role UART|MGR [-Port COMx] -Command "COMMAND" [-ReadMs N]
  wlctl.ps1 listen [-Role UART|MGR] [-Port COMx] [-Seconds N]
  wlctl.ps1 monkey on [-Port COMx] [-IntervalMs N] [-Print on|off] [-Mem 0|6]
  wlctl.ps1 monkey off [-Port COMx]
  wlctl.ps1 monkey status [-Port COMx]
  wlctl.ps1 disk mount-data|mount-log|unmount [-Port COMx]
  wlctl.ps1 fs ls|read|pull|push|rm -Disk data|log [fs args...]
  wlctl.ps1 vbus on|off|status [-Port COMx] [-Output json|text]
  wlctl.ps1 ready [-Port COMx] [-Timeout N] [-ProbeCommand CMD] [-Output json|text]
  wlctl.ps1 awake [-Port COMx] [-Timeout N] [-ProbeCommand CMD] [-Output json|text]
  wlctl.ps1 diagnose [-Message TEXT] [-MessageFile PATH] [-Output json|text]

Examples:
  wlctl.ps1 serial -Role MGR -Command "WL+FWVER=?"
  wlctl.ps1 serial -Role UART -Command "monkey -g"
  wlctl.ps1 monkey on -IntervalMs 1000 -Print off -Mem 0
  wlctl.ps1 monkey status
  wlctl.ps1 disk mount-data
  wlctl.ps1 fs pull -Disk log -FromPath "**/*.log" -ToPath .\img_logs
  wlctl.ps1 vbus status
  wlctl.ps1 ready -Timeout 60
  wlctl.ps1 awake -Timeout 15
  wlctl.ps1 diagnose -Message "cmd.exe timed out" -Output json
"@
}

function Write-JsonPayload([hashtable]$Payload) {
    $filtered = @{}
    foreach ($entry in $Payload.GetEnumerator()) {
        if ($null -ne $entry.Value -and $entry.Value -ne '') {
            $filtered[$entry.Key] = $entry.Value
        }
    }
    $filtered | ConvertTo-Json -Compress
}

if ($Help -or $Action -eq 'help') {
    Show-Usage
    exit 0
}
if ([string]::IsNullOrWhiteSpace($Action)) {
    Show-Usage
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path $ScriptDir -Parent
$SerialPs1 = if ($env:WLCTL_SERIAL_PS1) { $env:WLCTL_SERIAL_PS1 } else { Join-Path $ScriptDir 'serial_cmd_auto.ps1' }
$DiskPs1 = if ($env:WLCTL_DISK_PS1) { $env:WLCTL_DISK_PS1 } else { Join-Path $ScriptDir 'disk_mode_auto.ps1' }
$DiagnosePy = if ($env:WLCTL_DIAGNOSE_PY) { $env:WLCTL_DIAGNOSE_PY } else { Join-Path $ScriptsRoot 'common\diagnose_utils.py' }
$FsPy = if ($env:WLCTL_FS_PY) { $env:WLCTL_FS_PY } else { Join-Path $ScriptsRoot 'common\fs_utils.py' }

if (-not (Test-Path $SerialPs1)) {
    throw "serial_cmd_auto.ps1 not found: $SerialPs1"
}
if (-not (Test-Path $DiskPs1)) {
    throw "disk_mode_auto.ps1 not found: $DiskPs1"
}
if (-not (Test-Path $DiagnosePy)) {
    throw "diagnose_utils.py not found: $DiagnosePy"
}
if (-not (Test-Path $FsPy)) {
    throw "fs_utils.py not found: $FsPy"
}

function Invoke-Serial([string]$role, [string]$port, [string]$cmd, [int]$readMs) {
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$SerialPs1,'-Role',$role,'-ReadWindowMs',"$readMs")
    if (-not [string]::IsNullOrWhiteSpace($port)) { $args += @('-Port',$port) }
    if (-not [string]::IsNullOrWhiteSpace($cmd)) { $args += @('-Command',$cmd) }
    & powershell @args
    if ($LASTEXITCODE -ne 0) {
        $script:LastChildExitCode = $LASTEXITCODE
        throw ("Invoke-Serial sub-script failed with exit code $LASTEXITCODE (role=$role port=$port)")
    }
}

function Invoke-Listen([string]$role, [string]$port, [int]$seconds) {
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$SerialPs1,'-Role',$role,'-Listen','-ListenSeconds',"$seconds")
    if (-not [string]::IsNullOrWhiteSpace($port)) { $args += @('-Port',$port) }
    & powershell @args
    if ($LASTEXITCODE -ne 0) {
        $script:LastChildExitCode = $LASTEXITCODE
        throw ("Invoke-Listen sub-script failed with exit code $LASTEXITCODE (role=$role port=$port)")
    }
}

function Invoke-Disk([string]$mode, [string]$mgrPort, [int]$readMs) {
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$DiskPs1,'-Mode',$mode,'-ReadMs',"$readMs")
    if (-not [string]::IsNullOrWhiteSpace($mgrPort)) { $args += @('-MgrPort',$mgrPort) }
    & powershell @args
    if ($LASTEXITCODE -ne 0) {
        $script:LastChildExitCode = $LASTEXITCODE
        throw ("Invoke-Disk sub-script failed with exit code $LASTEXITCODE (mode=$mode port=$mgrPort)")
    }
}

function Normalize-VbusState([string]$text) {
    foreach ($line in ($text -split "\r?\n")) {
        $normalized = $line.Trim().ToLowerInvariant()
        switch ($normalized) {
            '1' { return 'on' }
            '1.0' { return 'on' }
            'on' { return 'on' }
            'vbus=on' { return 'on' }
            'wl+vbus=on' { return 'on' }
            '0' { return 'off' }
            '0.0' { return 'off' }
            'off' { return 'off' }
            'vbus=off' { return 'off' }
            'wl+vbus=off' { return 'off' }
        }
    }
    return $null
}

function Get-VbusState([string]$mgrPort) {
    $output = Invoke-Serial -role 'MGR' -port $mgrPort -cmd 'WL+VBUS=?' -readMs $ReadMs
    $state = Normalize-VbusState -text ($output | Out-String)
    if ([string]::IsNullOrWhiteSpace($state)) {
        throw "Unable to determine VBUS status from output: $output"
    }
    return $state
}

function Set-VbusState([string]$target, [string]$mgrPort) {
    $target = $target.ToLowerInvariant()
    $desiredCmd = if ($target -eq 'off') { 'WL+VBUS=OFF' } else { 'WL+VBUS=ON' }

    try {
        if ((Get-VbusState -mgrPort $mgrPort) -eq $target) {
            Write-Output $target
            return
        }
    } catch {
        # Query failure should not block us from attempting to set the state.
    }

    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        Invoke-Serial -role 'MGR' -port $mgrPort -cmd $desiredCmd -readMs $ReadMs | Out-Null
        try {
            if ((Get-VbusState -mgrPort $mgrPort) -eq $target) {
                Write-Output $target
                return
            }
        } catch {
            if ($attempt -ge 2) {
                throw
            }
        }
        if ($attempt -lt 2) {
            Start-Sleep -Seconds 1
        }
    }

    throw "Failed to switch VBUS to $target."
}

function Test-ContainsMarker([string]$text, [string[]]$markers) {
    foreach ($marker in $markers) {
        if (-not [string]::IsNullOrWhiteSpace($marker) -and $text.Contains($marker)) {
            return $true
        }
    }
    return $false
}

function Test-UartProbeSucceeded([string]$output, [string]$probeCmd, [string]$probeToken = 'monkey status') {
    if ([string]::IsNullOrWhiteSpace($output)) {
        return $false
    }
    if (-not [string]::IsNullOrWhiteSpace($probeToken) -and $output.Contains($probeToken)) {
        return $true
    }
    if ($output -match '(?m)(^|\r?\n)(\$ |# )') {
        return $true
    }
    if ($probeCmd.Trim().ToLowerInvariant() -eq 'monkey -g' -and $output -match '(?i)monkey\s+status|monkey\s*=|status\s*=|monkey\s+is\s+(?:on|off)') {
        return $true
    }
    return $false
}

function Wait-DeviceReady([string]$uartPort, [int]$timeout, [string]$probeCmd, [string[]]$readyMarkers) {
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $timeout))
    $probeIndex = 0
    while ((Get-Date) -lt $deadline) {
        $remaining = [Math]::Max(1, [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds))
        $chunkSeconds = [Math]::Min(5, $remaining)
        $probeIndex += 1
        $probeReadMs = [Math]::Min(6000, [Math]::Max(1800, $chunkSeconds * 900))

        try {
            $probe = Invoke-Serial -role 'UART' -port $uartPort -cmd $probeCmd -readMs $probeReadMs
            if (-not [string]::IsNullOrWhiteSpace($probe)) {
                Write-Output $probe
            }
            if (Test-UartProbeSucceeded -output ($probe | Out-String) -probeCmd $probeCmd) {
                Write-Output "[INFO] UART shell probe #$probeIndex succeeded"
                return
            }
        } catch {
            Write-Output "[WARN] UART shell probe #$probeIndex failed: $($_.Exception.Message)"
        }

        try {
            $chunk = Invoke-Listen -role 'UART' -port $uartPort -seconds $chunkSeconds
            if (-not [string]::IsNullOrWhiteSpace($chunk)) {
                Write-Output $chunk
            }
            if (Test-ContainsMarker -text ($chunk | Out-String) -markers $readyMarkers) {
                return
            }
        } catch {
            Write-Output "[WARN] UART listen failed while waiting for ready screen logs: $($_.Exception.Message)"
        }
    }

    throw "Device did not reach launcher-ready or shell-ready state within timeout. probe=$probeCmd"
}

function Prepare-UartShellForLongLog([string]$uartPort, [int]$timeout, [string]$keepAwakeCmd, [string]$probeCmd) {
    $keepAwakeReadMs = 1200
    $probeReadMs = 2500

    if (-not [string]::IsNullOrWhiteSpace($keepAwakeCmd)) {
        Write-Output "===== UART $keepAwakeCmd ====="
        try {
            $keepAwakeOutput = Invoke-Serial -role 'UART' -port $uartPort -cmd $keepAwakeCmd -readMs $keepAwakeReadMs
            if (-not [string]::IsNullOrWhiteSpace($keepAwakeOutput)) {
                Write-Output $keepAwakeOutput
            }
            if (Test-UartProbeSucceeded -output ($keepAwakeOutput | Out-String) -probeCmd $probeCmd) {
                Write-Output "[INFO] UART keep-awake command returned shell prompt"
                return
            }
        } catch {
            Write-Output "[WARN] $keepAwakeCmd failed: $($_.Exception.Message)"
        }
    }

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $timeout))
    $probeIndex = 0
    while ((Get-Date) -lt $deadline) {
        $probeIndex += 1
        Write-Output "===== UART shell probe #$probeIndex ====="
        try {
            $probe = Invoke-Serial -role 'UART' -port $uartPort -cmd $probeCmd -readMs $probeReadMs
            if (-not [string]::IsNullOrWhiteSpace($probe)) {
                Write-Output $probe
            }
            if (Test-UartProbeSucceeded -output ($probe | Out-String) -probeCmd $probeCmd) {
                Write-Output "[INFO] UART shell probe #$probeIndex succeeded"
                return
            }
        } catch {
            Write-Output "[WARN] UART shell probe #$probeIndex failed: $($_.Exception.Message)"
        }

        $remaining = [Math]::Max(0, [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds))
        if ($remaining -le 0) {
            break
        }
        $chunkSeconds = [Math]::Min(2, [Math]::Max(1, $remaining))
        try {
            $chunk = Invoke-Listen -role 'UART' -port $uartPort -seconds $chunkSeconds
            if (-not [string]::IsNullOrWhiteSpace($chunk)) {
                Write-Output $chunk
            }
        } catch {
            Write-Output "[WARN] UART stabilize listen #$probeIndex failed: $($_.Exception.Message)"
        }
    }

    throw "UART shell did not stabilize after ready. keep_awake_cmd=$keepAwakeCmd, probe=$probeCmd"
}

function Resolve-PythonInvocation() {
    if (-not [string]::IsNullOrWhiteSpace($env:WLCTL_PYTHON_BIN)) {
        return @($env:WLCTL_PYTHON_BIN)
    }
    foreach ($candidate in @('python', 'python3', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            if ($candidate -eq 'py') {
                return @($cmd.Source, '-3')
            }
            return @($cmd.Source)
        }
    }
    throw "python/python3/py not found; wlctl diagnose requires Python."
}

function Invoke-Diagnose([string]$message, [string]$messageFile, [string]$format) {
    $python = @(Resolve-PythonInvocation)
    $args = @($python + @($DiagnosePy, '--output', $format))
    if (-not [string]::IsNullOrWhiteSpace($message)) {
        $args += @('--message', $message)
    }
    if (-not [string]::IsNullOrWhiteSpace($messageFile)) {
        $args += @('--message-file', $messageFile)
    }
    & $args[0] $args[1..($args.Count - 1)]
}

function Invoke-Fs([string]$subAction) {
    if ([string]::IsNullOrWhiteSpace($Disk)) {
        throw "fs requires -Disk data|log"
    }
    $python = @(Resolve-PythonInvocation)
    $args = @($python + @($FsPy, $subAction, '--disk', $Disk, '--output', $OutputFormat))
    if (-not [string]::IsNullOrWhiteSpace($Port)) {
        $args += @('--mgr-port', $Port)
    }
    if ($Timeout -gt 0) {
        $args += @('--wait-seconds', "$Timeout")
    }
    if (-not [string]::IsNullOrWhiteSpace($FsPath)) {
        $args += @('--path', $FsPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($FromPath)) {
        $args += @('--from-path', $FromPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($ToPath)) {
        $args += @('--to-path', $ToPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($Encoding)) {
        $args += @('--encoding', $Encoding)
    }
    if ($Recursive) {
        $args += '--recursive'
    }
    if ($MissingOk) {
        $args += '--missing-ok'
    }
    & $args[0] $args[1..($args.Count - 1)]
}

switch ($Action) {
    'serial' {
        $roleToUse = if ($Role) { $Role } else { 'MGR' }
        $cmdToUse = if ($Command) { $Command } else { $Arg1 }
        if ([string]::IsNullOrWhiteSpace($cmdToUse)) {
            throw "serial requires command. Use -Command or positional Arg1."
        }
        if ($cmdToUse.TrimStart().StartsWith('monkey') -and $roleToUse -eq 'MGR') {
            Write-Output "[WARN] Detected monkey command on MGR, auto-switch role to UART."
            $roleToUse = 'UART'
        }
        if ($cmdToUse.TrimStart().StartsWith('WL+') -and $roleToUse -eq 'UART') {
            Write-Output "[WARN] Detected WL+ command on UART, auto-switch role to MGR."
            $roleToUse = 'MGR'
        }
        Invoke-Serial -role $roleToUse -port $Port -cmd $cmdToUse -readMs $ReadMs
    }
    'listen' {
        $roleToUse = if ($Role) { $Role } else { 'UART' }
        Invoke-Listen -role $roleToUse -port $Port -seconds $Seconds
    }
    'monkey' {
        $sub = if ($Arg1) { $Arg1.ToLowerInvariant() } else { '' }
        switch ($sub) {
            'on' {
                # This firmware expects each monkey setting as a separate command.
                Invoke-Serial -role 'UART' -port $Port -cmd "monkey -i $IntervalMs" -readMs $ReadMs
                Invoke-Serial -role 'UART' -port $Port -cmd "monkey -p $Print" -readMs $ReadMs
                Invoke-Serial -role 'UART' -port $Port -cmd "monkey -m $Mem" -readMs $ReadMs
                Invoke-Serial -role 'UART' -port $Port -cmd 'monkey -s on' -readMs $ReadMs
            }
            'off' {
                Invoke-Serial -role 'UART' -port $Port -cmd 'monkey -s off' -readMs $ReadMs
            }
            'status' {
                Invoke-Serial -role 'UART' -port $Port -cmd 'monkey -g' -readMs $ReadMs
            }
            default {
                throw "monkey requires sub-action: on|off|status"
            }
        }
    }
    'disk' {
        $sub = if ($Arg1) { $Arg1.ToLowerInvariant() } else { 'mount-data' }
        if ($sub -notin @('mount-data','mount-log','unmount')) {
            throw "disk requires sub-action: mount-data|mount-log|unmount"
        }
        Invoke-Disk -mode $sub -mgrPort $Port -readMs $ReadMs
    }
    'fs' {
        $sub = if ($Arg1) { $Arg1.ToLowerInvariant() } else { '' }
        if ($sub -notin @('ls','read','pull','push','rm')) {
            throw "fs requires sub-action: ls|read|pull|push|rm"
        }
        Invoke-Fs -subAction $sub
    }
    'vbus' {
        $sub = if ($Arg1) { $Arg1.ToLowerInvariant() } else { 'status' }
        switch ($sub) {
            'status' {
                $state = Get-VbusState -mgrPort $Port
                if ($OutputFormat -eq 'json') {
                    Write-JsonPayload @{
                        command = 'vbus'
                        action = 'status'
                        status = 'ok'
                        port = $Port
                        state = $state
                    }
                } else {
                    Write-Output $state
                }
            }
            'on' {
                $state = Set-VbusState -target 'on' -mgrPort $Port
                if ($OutputFormat -eq 'json') {
                    Write-JsonPayload @{
                        command = 'vbus'
                        action = 'on'
                        status = 'ok'
                        port = $Port
                        state = $state
                    }
                } else {
                    Write-Output $state
                }
            }
            'off' {
                $state = Set-VbusState -target 'off' -mgrPort $Port
                if ($OutputFormat -eq 'json') {
                    Write-JsonPayload @{
                        command = 'vbus'
                        action = 'off'
                        status = 'ok'
                        port = $Port
                        state = $state
                    }
                } else {
                    Write-Output $state
                }
            }
            default {
                throw "vbus requires sub-action: on|off|status"
            }
        }
    }
    'ready' {
        $markers = @(
            'gotoScreen:LauncherScreen',
            'gotoScreen:ChargeScreen',
            'goto screen LauncherScreen',
            '_setCurrentScreenInterval LauncherScreen'
        )
        $lines = @(Wait-DeviceReady -uartPort $Port -timeout $Timeout -probeCmd $ProbeCommand -readyMarkers $markers)
        if ($OutputFormat -eq 'json') {
            Write-JsonPayload @{
                command = 'ready'
                status = 'ok'
                port = $Port
                timeout = $Timeout
                probe_command = $ProbeCommand
                output = ($lines -join "`n")
            }
        } else {
            $lines | ForEach-Object { $_ }
        }
    }
    'awake' {
        $lines = @(Prepare-UartShellForLongLog -uartPort $Port -timeout $Timeout -keepAwakeCmd 'power -s on' -probeCmd $ProbeCommand)
        if ($OutputFormat -eq 'json') {
            Write-JsonPayload @{
                command = 'awake'
                status = 'ok'
                port = $Port
                timeout = $Timeout
                probe_command = $ProbeCommand
                output = ($lines -join "`n")
            }
        } else {
            $lines | ForEach-Object { $_ }
        }
    }
    'diagnose' {
        Invoke-Diagnose -message $Message -messageFile $MessageFile -format $OutputFormat
    }
}
