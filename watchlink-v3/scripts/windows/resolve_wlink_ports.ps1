function Convert-ToWlinkPairs([object[]]$Devices) {
    if (-not $Devices) { return @() }

    $pairs = foreach ($group in ($Devices | Group-Object DeviceId)) {
        $mgr = ($group.Group | Where-Object Role -eq 'MGR' | Select-Object -First 1).Port
        $uart = ($group.Group | Where-Object Role -eq 'UART' | Select-Object -First 1).Port
        [pscustomobject]@{
            DeviceId = $group.Name
            MGR = $mgr
            UART = $uart
        }
    }

    return @($pairs | Where-Object { $_.MGR -or $_.UART })
}

function Get-WlinkPortsFromPnpDevice {
    try {
        $devices = Get-PnpDevice -Class Ports -ErrorAction Stop |
            ForEach-Object {
                $name = $_.FriendlyName
                if ($_.Status -eq 'OK' -and $name -match '^WLINK\s+(?<DeviceId>[A-F0-9]+)\s+(?<Role>MGR|UART)\s+\((?<Port>COM\d+)\)$') {
                    [pscustomobject]@{
                        DeviceId = $Matches.DeviceId
                        Role = $Matches.Role
                        Port = $Matches.Port
                    }
                }
            } |
            Where-Object { $_ }
        return @(Convert-ToWlinkPairs $devices)
    } catch {
        return @()
    }
}

function Get-WlinkPortsFromPnPUtil {
    try {
        $raw = & pnputil /enum-devices /class Ports 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return @() }

        $blocks = (($raw -join "`n") -split "(?:\r?\n){2,}")
        $devices = foreach ($block in $blocks) {
            $nameMatch = [regex]::Match($block, 'Device Description:\s*(?<Name>.+)')
            $statusMatch = [regex]::Match($block, 'Status:\s*(?<Status>.+)')
            if ($nameMatch.Success -and $statusMatch.Success) {
                $name = $nameMatch.Groups['Name'].Value.Trim()
                $status = $statusMatch.Groups['Status'].Value.Trim()
                $wlinkMatch = [regex]::Match($name, '^WLINK\s+(?<DeviceId>[A-F0-9]+)\s+(?<Role>MGR|UART)\s+\((?<Port>COM\d+)\)$')
                if ($status -eq 'Started' -and $wlinkMatch.Success) {
                    [pscustomobject]@{
                        DeviceId = $wlinkMatch.Groups['DeviceId'].Value
                        Role = $wlinkMatch.Groups['Role'].Value
                        Port = $wlinkMatch.Groups['Port'].Value
                    }
                }
            }
        }

        return @(Convert-ToWlinkPairs ($devices | Where-Object { $_ }))
    } catch {
        return @()
    }
}

function Get-PreferredWlinkPorts {
    $pairs = @(Get-WlinkPortsFromPnpDevice)
    if ($pairs.Count -gt 0) { return $pairs }

    $pairs = @(Get-WlinkPortsFromPnPUtil)
    if ($pairs.Count -gt 0) { return $pairs }

    return @()
}

function Get-V3dlComOutput {
    try {
        $out = & v3dl com 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out -join "`n") }
    } catch {}

    try {
        $out = & wsl.exe bash -lc 'v3dl com' 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out -join "`n") }
    } catch {}

    return $null
}

function Get-WlinkRoleCandidates {
    param(
        [ValidateSet('UART','MGR')]
        [string]$Role
    )

    $preferredPairs = @(Get-PreferredWlinkPorts)
    if ($preferredPairs.Count -eq 1) {
        $preferredPort = if ($Role -eq 'UART') { $preferredPairs[0].UART } else { $preferredPairs[0].MGR }
        if (-not [string]::IsNullOrWhiteSpace($preferredPort)) {
            return @($preferredPort)
        }
    }

    $raw = Get-V3dlComOutput
    if ([string]::IsNullOrWhiteSpace($raw)) { return @() }

    $pattern = if ($Role -eq 'UART') { 'UART\s*\(COM(\d+)\)' } else { 'MGR\s*\(COM(\d+)\)' }
    $matches = [regex]::Matches($raw, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $set = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($m in $matches) { [void]$set.Add("COM$($m.Groups[1].Value)") }
    return @($set)
}

function Select-WlinkPortInteractive {
    param(
        [string]$Role,
        [string[]]$Ports
    )

    if ($Ports.Count -eq 0) {
        throw "No $Role port candidates available."
    }
    if ($Ports.Count -eq 1) {
        return $Ports[0]
    }
    if (-not [Environment]::UserInteractive) {
        throw "Multiple $Role ports detected ($($Ports -join ', ')). Please pass the port explicitly."
    }

    Write-Output "[INFO] Multiple $Role ports detected:"
    for ($i = 0; $i -lt $Ports.Count; $i++) {
        Write-Output ("  {0}) {1}" -f ($i + 1), $Ports[$i])
    }

    $selection = Read-Host "Select $Role port index"
    $index = 0
    if (-not [int]::TryParse($selection, [ref]$index) -or $index -lt 1 -or $index -gt $Ports.Count) {
        throw "Invalid $Role port selection: $selection"
    }
    return $Ports[$index - 1]
}

function Resolve-WlinkRolePort {
    param(
        [ValidateSet('UART','MGR')]
        [string]$Role,
        [string]$ExplicitPort,
        [string]$NoPortHint = ''
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPort)) { return $ExplicitPort }

    $ports = @(Get-WlinkRoleCandidates -Role $Role)
    if ($ports.Count -eq 0) {
        if ([string]::IsNullOrWhiteSpace($NoPortHint)) {
            throw "No $Role port detected."
        }
        throw "No $Role port detected. $NoPortHint"
    }

    return Select-WlinkPortInteractive -Role $Role -Ports $ports
}

function Resolve-WlinkPortPair {
    param(
        [string]$MgrPort,
        [string]$UartPort
    )

    $pairs = @(Get-PreferredWlinkPorts)

    if (-not [string]::IsNullOrWhiteSpace($MgrPort) -and -not [string]::IsNullOrWhiteSpace($UartPort)) {
        return [pscustomobject]@{
            DeviceId = 'manual'
            MGR = $MgrPort
            UART = $UartPort
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($MgrPort) -or -not [string]::IsNullOrWhiteSpace($UartPort)) {
        foreach ($pair in $pairs) {
            if ((-not [string]::IsNullOrWhiteSpace($MgrPort) -and $pair.MGR -eq $MgrPort) -or
                (-not [string]::IsNullOrWhiteSpace($UartPort) -and $pair.UART -eq $UartPort)) {
                return [pscustomobject]@{
                    DeviceId = $pair.DeviceId
                    MGR = if ($MgrPort) { $MgrPort } else { $pair.MGR }
                    UART = if ($UartPort) { $UartPort } else { $pair.UART }
                }
            }
        }
        throw 'Could not auto-complete missing port from started WLINK pairs. Pass both -MgrPort and -UartPort.'
    }

    if ($pairs.Count -eq 0) {
        throw 'No started WLINK MGR/UART pair found.'
    }
    if ($pairs.Count -eq 1) {
        return [pscustomobject]@{
            DeviceId = $pairs[0].DeviceId
            MGR = $pairs[0].MGR
            UART = $pairs[0].UART
        }
    }
    if (-not [Environment]::UserInteractive) {
        throw ("Multiple started WLINK pairs found: {0}. Pass -MgrPort and -UartPort explicitly." -f (($pairs | ForEach-Object { $_.DeviceId }) -join ', '))
    }

    Write-Output '[INFO] Multiple started WLINK pairs detected:'
    for ($i = 0; $i -lt $pairs.Count; $i++) {
        $p = $pairs[$i]
        Write-Output ("  {0}) DeviceId={1}, MGR={2}, UART={3}" -f ($i + 1), $p.DeviceId, $p.MGR, $p.UART)
    }

    $s = Read-Host 'Select device index'
    $idx = 0
    if (-not [int]::TryParse($s, [ref]$idx) -or $idx -lt 1 -or $idx -gt $pairs.Count) {
        throw "Invalid selection: $s"
    }

    return [pscustomobject]@{
        DeviceId = $pairs[$idx - 1].DeviceId
        MGR = $pairs[$idx - 1].MGR
        UART = $pairs[$idx - 1].UART
    }
}
