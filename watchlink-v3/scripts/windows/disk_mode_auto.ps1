param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('mount-data','mount-log','unmount')]
    [string]$Mode,

    [string]$MgrPort,
    [int]$ReadMs = 2000,
    [int]$WaitSeconds = 25
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path $ScriptDir -Parent
$SerialPs1 = Join-Path $ScriptDir 'serial_cmd_auto.ps1'
$PortResolver = Join-Path $ScriptDir 'resolve_wlink_ports.ps1'
$DiskUtilsPy = if ($env:WLCTL_DISKUTILS_PY) { $env:WLCTL_DISKUTILS_PY } else { Join-Path $ScriptsRoot 'common\disk_utils.py' }

if (-not (Test-Path $SerialPs1)) {
    throw "serial_cmd_auto.ps1 not found: $SerialPs1"
}
if (-not (Test-Path $PortResolver)) {
    throw "resolve_wlink_ports.ps1 not found: $PortResolver"
}

. $PortResolver

$script:ResolvedMgrPort = Resolve-WlinkRolePort -Role 'MGR' -ExplicitPort $MgrPort -NoPortHint "Pass -MgrPort explicitly."
Write-Output ("[INFO] Using MGR port: {0}" -f $script:ResolvedMgrPort)

function Invoke-MgrCommand {
    param(
        [string]$Command,
        [switch]$AllowNoResponse
    )

    $args = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$SerialPs1,
        '-Role','MGR',
        '-Port',$script:ResolvedMgrPort,
        '-ReadWindowMs',"$ReadMs",
        '-Command',$Command
    )
    $out = & powershell @args 2>&1
    $exitCode = $LASTEXITCODE
    $text = [string]($out -join "`n")

    Write-Output ("[MGR] {0}" -f $Command)
    if (-not [string]::IsNullOrWhiteSpace($text)) {
        Write-Output $text
    }

    if ($exitCode -ne 0 -and -not $AllowNoResponse) {
        throw ("MGR command failed: {0} :: exit={1} :: {2}" -f $Command, $exitCode, $text)
    }
    if ($exitCode -ne 0 -and $AllowNoResponse) {
        Write-Output "[WARN] MGR sub-script exited with code $exitCode (tolerated via AllowNoResponse)"
    }
    if ($text -match '^\[ERROR\]') {
        throw ("MGR command failed: {0} :: {1}" -f $Command, $text)
    }
    if ((-not $AllowNoResponse) -and $text -match 'No response within') {
        throw ("MGR command no response: {0}" -f $Command)
    }

    return $text
}

function Get-ExternalDriveRoots {
    $drives = Get-PSDrive -PSProvider FileSystem |
        Where-Object { $_.Root -match '^[A-Z]:\\$' -and $_.Name -notin @('C','D') } |
        Sort-Object Name
    return @($drives | Select-Object -ExpandProperty Root)
}

function Resolve-PythonInvocation {
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
    return @()
}

function Invoke-DiskUtilsSelect {
    param(
        [ValidateSet('select-data-root','select-log-root')]
        [string]$Action,
        [string[]]$Roots
    )

    if (-not (Test-Path $DiskUtilsPy)) {
        return $null
    }

    $python = @(Resolve-PythonInvocation)
    if ($python.Count -eq 0) {
        return $null
    }

    $args = @($python + @($DiskUtilsPy, $Action, '--output', 'text'))
    foreach ($root in $Roots) {
        $args += @('--root', $root)
    }

    try {
        $output = & $args[0] $args[1..($args.Count - 1)] 2>$null
        $text = [string]($output -join "`n")
        $selected = $text.Trim()
        if (-not [string]::IsNullOrWhiteSpace($selected) -and (Test-Path $selected)) {
            return $selected
        }
    } catch {
        return $null
    }
    return $null
}

function Get-DriveVolumeLabel([string]$Root) {
    if ([string]::IsNullOrWhiteSpace($Root) -or $Root.Length -lt 2) { return '' }
    $letter = $Root.Substring(0, 1)
    try {
        $volume = Get-Volume -DriveLetter $letter -ErrorAction Stop
        return [string]$volume.FileSystemLabel
    } catch {
        return ''
    }
}

function Test-DataVolumeLabel([string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Label)) { return $false }
    $normalized = $Label.ToUpperInvariant()
    foreach ($token in @('SYSTEM','WLINK','IMAGE','IMG','BOOT')) {
        if ($normalized.Contains($token)) { return $false }
    }
    return $normalized.StartsWith('DATA')
}

function Test-WatchlinkVolumeLabel([string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Label)) { return $false }
    $normalized = $Label.ToUpperInvariant()
    if ($normalized.StartsWith('DATA')) { return $true }
    if ($normalized.StartsWith('SYSTEM')) { return $true }
    if ($normalized.Contains('WLINK')) { return $true }
    if ($normalized.Contains('IMAGE')) { return $true }
    if ($normalized -eq 'IMG') { return $true }
    return $false
}

function Get-WatchlinkDriveRoots {
    $roots = @(Get-ExternalDriveRoots)
    $selected = @()
    foreach ($root in $roots) {
        if (Test-WatchlinkVolumeLabel -Label (Get-DriveVolumeLabel -Root $root)) {
            $selected += $root
        }
    }
    return @($selected | Select-Object -Unique)
}

function Select-DataRoot([string[]]$Roots) {
    if ($Roots.Count -eq 0) { return $null }

    $pythonSelected = Invoke-DiskUtilsSelect -Action 'select-data-root' -Roots $Roots
    if ($pythonSelected) { return $pythonSelected }

    foreach ($root in $Roots) {
        if (Test-DataVolumeLabel -Label (Get-DriveVolumeLabel -Root $root)) { return $root }
    }

    return $null
}

function Select-LogRoot([string[]]$Roots) {
    if ($Roots.Count -eq 0) { return $null }

    $pythonSelected = Invoke-DiskUtilsSelect -Action 'select-log-root' -Roots $Roots
    if ($pythonSelected) { return $pythonSelected }

    if ($Roots.Count -eq 1) { return $Roots[0] }

    $imgLike = @()
    foreach ($root in $Roots) {
        $imgFiles = @(Get-ChildItem -LiteralPath $root -File -Filter *.img -ErrorAction SilentlyContinue)
        if ($imgFiles.Count -gt 0) { $imgLike += $root }
    }
    $imgLike = @($imgLike | Select-Object -Unique)
    if ($imgLike.Count -gt 0) { return $imgLike[0] }

    return $Roots[0]
}

function Wait-Root([scriptblock]$Selector) {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        $roots = @(Get-ExternalDriveRoots)
        $selected = & $Selector $roots
        if ($selected) { return $selected }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    return $null
}

function Unmount-Disk {
    $cleanupRoots = @()
    if ($env:WLCTL_UNMOUNT_EMIT_CLEANUP -eq '1') {
        $cleanupRoots = @(Get-WatchlinkDriveRoots)
    }

    # v3dl pattern: just WL+DISK=NULL.  After NULL, VBUS restores to pre-TEMP
    # state (which is typically OFF after a fresh power cycle or previous NULL).
    [void](Invoke-MgrCommand -Command 'WL+DISK=NULL' -AllowNoResponse)
    Start-Sleep -Seconds 2

    if ($env:WLCTL_UNMOUNT_EMIT_CLEANUP -eq '1') {
        foreach ($root in $cleanupRoots) {
            Write-Output ("[WLCTL_CLEANUP_ROOT] {0}" -f $root)
        }
    }
}

function Mount-DataDisk {
    # Follow v3dl pattern: single combined command with WL+VBUS=TEMP.
    # WL+VBUS=TEMP: temporary VBUS, auto-restores after WL+DISK=NULL.
    # WL+DISK=FS: mount SYSTEM/DATA partitions directly (no WL intermediate).
    # Ref: https://zepp.feishu.cn/docx/BdxDde1IeodCG1xZrmIcvovBnJe
    [void](Invoke-MgrCommand -Command 'WL+VBUS=TEMP WL+DISK=FS' -AllowNoResponse)
    Start-Sleep -Seconds 2

    $root = Wait-Root -Selector { param($roots) Select-DataRoot -Roots $roots }
    if ($root) {
        Write-Output '[INFO] DATA disk mode: FS'
        Write-Output $root
        return
    }

    throw 'No data disk root mounted after WL+VBUS=TEMP WL+DISK=FS.'
}

function Mount-LogDisk {
    [void](Invoke-MgrCommand -Command 'WL+VBUS=TEMP WL+DISK=IMG' -AllowNoResponse)
    Start-Sleep -Seconds 2

    $root = Wait-Root -Selector { param($roots) Select-LogRoot -Roots $roots }
    if (-not $root) {
        throw 'No log disk root mounted after WL+VBUS=TEMP WL+DISK=IMG.'
    }

    Write-Output '[INFO] LOG disk mode: IMG'
    Write-Output $root
}

switch ($Mode) {
    'mount-data' { Mount-DataDisk; break }
    'mount-log' { Mount-LogDisk; break }
    'unmount' {
        Unmount-Disk
        Write-Output 'UNMOUNTED'
        break
    }
}
