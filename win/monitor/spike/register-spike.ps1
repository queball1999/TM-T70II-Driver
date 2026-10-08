<#
.SYNOPSIS
    Phase 0 spike: register/unregister the throwaway TMT70 Spike Monitor.

.DESCRIPTION
    NOT part of the shipped installer. Registers tmt70spike.dll as a Windows
    print monitor (AddMonitor), creates a test queue bound to the inbox
    "Generic / Text Only" driver and the monitor's one fixed port
    ("TMT70SPIKE:"), and restarts the Spooler service so it re-enumerates
    monitors. Every callback the monitor makes is logged to
    %ProgramData%\tmt70spike\writeport.log -- that log is the actual
    Phase 0 deliverable.

    Must be run elevated (Administrator).

.PARAMETER Unregister
    Tear everything down: delete the test queue, remove the monitor
    registration, delete the staged DLL, restart the spooler.

.EXAMPLE
    .\register-spike.ps1                # install + create test queue
    .\register-spike.ps1 -Unregister    # remove everything
#>
param(
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

$MonitorName = "TMT70 Spike Monitor"
$QueueName   = "TMT70 Spike Test"
$PortName    = "TMT70SPIKE:"
$DllName     = "tmt70spike.dll"
$DriverName  = "Generic / Text Only"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated (Administrator) PowerShell."
    }
}

function Add-MonitorNative {
    param([string]$Name, [string]$DllPath)
    # winspool.drv!AddMonitorW -- not exposed by any PowerShell cmdlet, so
    # P/Invoke it directly. This is the one step Inno Setup's Pascal script
    # can't do natively either; a real Phase 1 install needs an equivalent
    # helper (compiled or scripted) invoked from its [Run] section.
    Add-Type -Namespace TMT70 -Name Spooler -MemberDefinition @"
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct MONITOR_INFO_2 {
    public string pName;
    public string pEnvironment;
    public string pDLLName;
}

[DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool AddMonitor(string pName, uint Level, ref MONITOR_INFO_2 pMonitors);

[DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool DeleteMonitor(string pName, string pEnvironment, string pMonitorName);
"@

    $info = New-Object TMT70.Spooler+MONITOR_INFO_2
    $info.pName        = $Name    # the monitor's own name -- NOT the server name (that's AddMonitor's 1st arg, null=local)
    $info.pEnvironment = "Windows x64"
    $info.pDLLName     = [System.IO.Path]::GetFileName($DllPath)

    if (-not [TMT70.Spooler]::AddMonitor($null, 2, [ref]$info)) {
        $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "AddMonitor failed: Win32 error $err"
    }
}

function Remove-MonitorNative {
    param([string]$Name)
    Add-Type -Namespace TMT70 -Name Spooler2 -MemberDefinition @"
[DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool DeleteMonitor(string pName, string pEnvironment, string pMonitorName);
"@ -ErrorAction SilentlyContinue

    try {
        [TMT70.Spooler2]::DeleteMonitor($null, "Windows x64", $Name) | Out-Null
    } catch { }
}

Assert-Admin

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDll    = Join-Path $scriptDir $DllName
$system32  = Join-Path $env:WINDIR "System32"
$stagedDll = Join-Path $system32 $DllName

if ($Unregister) {
    Write-Host "Removing test queue '$QueueName'..."
    Remove-Printer -Name $QueueName -ErrorAction SilentlyContinue

    Write-Host "Removing monitor registration '$MonitorName'..."
    Remove-MonitorNative -Name $MonitorName
    Remove-Item -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName" `
        -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host "Restarting Spooler..."
    Restart-Service -Name Spooler -Force

    if (Test-Path $stagedDll) {
        Write-Host "Removing staged DLL from System32..."
        Remove-Item -Path $stagedDll -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Done. Log file (if any) left at %ProgramData%\tmt70spike\writeport.log for review."
    return
}

if (-not (Test-Path $srcDll)) {
    throw "Build tmt70spike.dll first (see the gcc command in NOTES.md), expected at $srcDll"
}

$alreadyRegistered = [bool](Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName" -ErrorAction SilentlyContinue)

Write-Host "Stopping Spooler so the DLL/registration can be replaced cleanly..."
Stop-Service -Name Spooler -Force

Write-Host "Staging $DllName to System32..."
Copy-Item -Path $srcDll -Destination $stagedDll -Force

if ($alreadyRegistered) {
    Write-Host "Monitor already registered -- DLL updated in place, skipping AddMonitor."
} else {
    Write-Host "Registering monitor '$MonitorName' via AddMonitor..."
    Add-MonitorNative -Name $MonitorName -DllPath $stagedDll
}

Write-Host "Starting Spooler so it (re)loads the monitor..."
Start-Service -Name Spooler
Start-Sleep -Seconds 1

Write-Host "Verifying the monitor is visible to the print system..."
$mon = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName" -ErrorAction SilentlyContinue
if (-not $mon) {
    throw "Monitor key not found after AddMonitor+restart -- registration did not take."
}
Write-Host "  Driver value: $($mon.Driver)"

if (-not (Get-Printer -Name $QueueName -ErrorAction SilentlyContinue)) {
    Write-Host "Creating test queue '$QueueName' on port '$PortName' with driver '$DriverName'..."
    if (-not (Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue)) {
        Add-PrinterDriver -Name $DriverName
    }
    # The port must come from this monitor's EnumPorts before Add-PrinterPort/
    # Add-Printer will accept it -- confirms EnumPorts is actually being called.
    Add-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
    Add-Printer -Name $QueueName -DriverName $DriverName -PortName $PortName
} else {
    Write-Host "Test queue '$QueueName' already exists, leaving it as-is."
}

Write-Host ""
Write-Host "Done. Now: print anything to '$QueueName' (e.g. Notepad -> File -> Print)"
Write-Host "and check: `$env:ProgramData\tmt70spike\writeport.log"
Write-Host ""
Write-Host "If nothing appears in that log, check Event Viewer under"
Write-Host "  Applications and Services Logs > Microsoft > Windows > PrintService > Admin"
Write-Host "  Applications and Services Logs > Microsoft > Windows > PrintService > Operational"
Write-Host "for load/signature failures, then record findings in NOTES.md."
Write-Host ""
Write-Host "Tear down with: .\register-spike.ps1 -Unregister"
