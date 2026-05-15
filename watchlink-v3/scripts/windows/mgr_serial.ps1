param(
    [Parameter(Mandatory=$true)]
    [string]$Port,

    [Parameter(Mandatory=$false)]
    [string]$Command,

    [int]$BaudRate = 115200,
    [int]$DataBits = 8,
    [ValidateSet('None','Odd','Even','Mark','Space')]
    [string]$Parity = 'None',
    [ValidateSet('One','Two','OnePointFive')]
    [string]$StopBits = 'One',

    [int]$ReadTimeoutMs = 400,
    [int]$WriteTimeoutMs = 1000,
    [int]$ReadWindowMs = 1200,

    [switch]$NoNewLine,
    [switch]$Listen,
    [int]$ListenSeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $sp = New-Object System.IO.Ports.SerialPort
}
catch {
    throw "SerialPort type is unavailable in current PowerShell runtime: $($_.Exception.Message)"
}
$sp.PortName = $Port
$sp.BaudRate = $BaudRate
$sp.DataBits = $DataBits
$sp.Parity = [System.IO.Ports.Parity]::$Parity
$sp.StopBits = [System.IO.Ports.StopBits]::$StopBits
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
        Write-Output "[INFO] Listening on $Port for $ListenSeconds seconds..."
        while ((Get-Date) -lt $deadline) {
            try {
                $line = $sp.ReadLine()
                if ($line -ne $null -and $line.Length -gt 0) {
                    Write-Output $line
                }
            }
            catch [System.TimeoutException] {
                # polling
            }
        }
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($Command)) {
        throw "Command is required unless -Listen is provided."
    }

    if ($NoNewLine) {
        $sp.Write($Command)
    }
    else {
        $sp.WriteLine($Command)
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lines = New-Object System.Collections.Generic.List[string]

    while ($sw.ElapsedMilliseconds -lt $ReadWindowMs) {
        try {
            $line = $sp.ReadLine()
            if ($line -ne $null -and $line.Length -gt 0) {
                $lines.Add($line)
            }
        }
        catch [System.TimeoutException] {
            # keep polling until read window ends
        }
    }

    if ($lines.Count -eq 0) {
        Write-Output "[WARN] No response within ${ReadWindowMs}ms"
    }
    else {
        $lines | ForEach-Object { Write-Output $_ }
    }
}
finally {
    if ($sp -and $sp.IsOpen) {
        $sp.Close()
    }
    if ($sp) {
        $sp.Dispose()
    }
}
