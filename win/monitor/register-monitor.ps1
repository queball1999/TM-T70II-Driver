<#
.SYNOPSIS
    Register the TMT70 Monitor and create the native "EPSON TM-T70II
    (tmt70print)" print queue.

.DESCRIPTION
    Installs tmt70mon.dll as a Windows print monitor (AddMonitor), writes its
    device/interpreter configuration to the registry, and creates a queue
    bound to the inbox "Generic / Text Only" driver on the monitor's one
    fixed port ("TMT70:"). Any app's File -> Print -> that queue then flows:
    GDI/EMF -> Generic/Text-Only rendering -> our WritePort (accumulates to a
    temp file, stripping page-eject bytes) -> EndDocPort spawns tmt70print
    against that file with --kind text --device <Device>.

    v1 scope: ONE queue, text-only (see win/monitor/README.md and
    docs/WINDOWS.md for the image/graphics limitation). Config lives under
    HKLM\SYSTEM\CurrentControlSet\Control\Print\Monitors\TMT70 Monitor\Config
    and is read fresh by the monitor on every job -- editing it and
    restarting the Spooler service is enough to change target device/options,
    no reinstall needed.

    Must be run elevated (Administrator).

.PARAMETER Device
    tmt70print --device URI (default: tmt70://04b8:0202, i.e. WinUSB).

.PARAMETER ExtraArgs
    Extra tmt70print arguments appended verbatim, e.g. "--density dark --cut full".
    Defaults to "--reflow": "Generic / Text Only" always pre-wraps at its own
    width and prepends a page margin as literal blank lines/spaces, both of
    which --reflow corrects (see docs/WINDOWS.md, option D). Pass -ExtraArgs ""
    explicitly to disable it.

.PARAMETER Unregister
    Remove the queue, monitor, and staged DLL; restart the spooler.

.EXAMPLE
    .\register-monitor.ps1 -Device "printer://EPSON TM-T70II Receipt"
    .\register-monitor.ps1 -Device "socket://192.168.1.50:9100" -ExtraArgs "--reflow --density dark --cut full"
    .\register-monitor.ps1 -Unregister
#>
param(
    [string]$Device = "tmt70://04b8:0202",
    [string]$ExtraArgs = "--reflow",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

$MonitorName = "TMT70 Monitor"
$QueueName   = "EPSON TM-T70II (tmt70print)"
$PortName    = "TMT70:"
$DllName     = "tmt70mon.dll"
$DriverName  = "Generic / Text Only"
$ConfigKey   = "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName\Config"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated (Administrator) PowerShell."
    }
}

function Add-MonitorNative {
    param([string]$Name, [string]$DllPath)
    Add-Type -Namespace TMT70Prod -Name Spooler -MemberDefinition @"
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

    $info = New-Object TMT70Prod.Spooler+MONITOR_INFO_2
    $info.pName        = $Name    # the monitor's own name -- NOT AddMonitor's server-name arg (null=local)
    $info.pEnvironment = "Windows x64"
    $info.pDLLName     = [System.IO.Path]::GetFileName($DllPath)

    if (-not [TMT70Prod.Spooler]::AddMonitor($null, 2, [ref]$info)) {
        $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "AddMonitor failed: Win32 error $err"
    }
}

function Remove-MonitorNative {
    param([string]$Name)
    Add-Type -Namespace TMT70Prod -Name Spooler2 -MemberDefinition @"
[DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool DeleteMonitor(string pName, string pEnvironment, string pMonitorName);
"@ -ErrorAction SilentlyContinue

    try { [TMT70Prod.Spooler2]::DeleteMonitor($null, "Windows x64", $Name) | Out-Null } catch { }
}

Assert-Admin

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$winDir    = Split-Path -Parent $scriptDir              # win/
$srcDll    = Join-Path $scriptDir "tmt70mon\$DllName"
$system32  = Join-Path $env:WINDIR "System32"
$stagedDll = Join-Path $system32 $DllName

if ($Unregister) {
    Write-Host "Removing queue '$QueueName'..."
    Remove-Printer -Name $QueueName -ErrorAction SilentlyContinue

    Write-Host "Stopping Spooler..."
    Stop-Service -Name Spooler -Force

    Write-Host "Removing monitor registration '$MonitorName'..."
    Remove-MonitorNative -Name $MonitorName
    Remove-Item -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName" `
        -Recurse -Force -ErrorAction SilentlyContinue

    if (Test-Path $stagedDll) {
        Remove-Item -Path $stagedDll -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Starting Spooler..."
    Start-Service -Name Spooler

    Write-Host "Done."
    return
}

if (-not (Test-Path $srcDll)) {
    throw "Build tmt70mon.dll first (gcc -Wall -Wextra -shared -o tmt70mon.dll tmt70mon.c -lgdi32 -luser32 -ladvapi32), expected at $srcDll"
}

# Resolve the Python interpreter and tmt70print.py the monitor should invoke.
# Prefer a bundled embeddable runtime (win\python\python.exe, as staged by
# tools\build-win.ps1 -Fetch) over whatever's on PATH -- the monitor runs in
# a service context where PATH resolution is not something to depend on
# (see spike/NOTES.md bug #2 for why relying on ambient environment state
# bit us once already).
$bundledPython = Join-Path $winDir "python\python.exe"
if (Test-Path $bundledPython) {
    $pythonExe = $bundledPython
} else {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $cmd) { throw "No Python found (bundled or on PATH). Install Python 3.8+ or build with -Fetch." }
    $pythonExe = $cmd.Source
}
$scriptPath = Join-Path $winDir "tmt70print.py"
if (-not (Test-Path $scriptPath)) { throw "tmt70print.py not found at $scriptPath" }

Write-Host "Python:  $pythonExe"
Write-Host "Script:  $scriptPath"
Write-Host "Device:  $Device"

$alreadyRegistered = [bool](Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName" -ErrorAction SilentlyContinue)

if ($alreadyRegistered) {
    # Updating the DLL binary in place: the spooler must be stopped first to
    # release its lock on the currently-loaded file (Windows won't let you
    # overwrite a mapped DLL). No AddMonitor call needed -- already registered.
    Write-Host "Monitor already registered -- stopping Spooler to replace the DLL..."
    Stop-Service -Name Spooler -Force
    Write-Host "Staging $DllName to System32..."
    Copy-Item -Path $srcDll -Destination $stagedDll -Force
    Write-Host "Starting Spooler so it reloads the updated DLL..."
    Start-Service -Name Spooler
} else {
    # Fresh registration: AddMonitor is itself an RPC call *to* the spooler
    # service, so the spooler must be left running for this call (stopping
    # it first, as the DLL-replace branch above does, makes AddMonitor fail
    # with ERROR_RPC_S_SERVER_UNAVAILABLE/1722). A restart afterward is what
    # makes the already-running spooler actually load the new monitor.
    Write-Host "Staging $DllName to System32..."
    Copy-Item -Path $srcDll -Destination $stagedDll -Force
    Write-Host "Registering monitor '$MonitorName' via AddMonitor..."
    Add-MonitorNative -Name $MonitorName -DllPath $stagedDll
    Write-Host "Restarting Spooler so it loads the new monitor..."
    Restart-Service -Name Spooler -Force
}
Start-Sleep -Seconds 1

$mon = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$MonitorName" -ErrorAction SilentlyContinue
if (-not $mon) { throw "Monitor key not found after AddMonitor+restart -- registration did not take." }
Write-Host "  Driver value: $($mon.Driver)"

Write-Host "Writing config to $ConfigKey ..."
New-Item -Path $ConfigKey -Force | Out-Null
Set-ItemProperty -Path $ConfigKey -Name "PythonExe"  -Value $pythonExe
Set-ItemProperty -Path $ConfigKey -Name "ScriptPath" -Value $scriptPath
Set-ItemProperty -Path $ConfigKey -Name "Device"     -Value $Device
Set-ItemProperty -Path $ConfigKey -Name "ExtraArgs"  -Value $ExtraArgs

if (-not (Get-Printer -Name $QueueName -ErrorAction SilentlyContinue)) {
    Write-Host "Creating queue '$QueueName' on port '$PortName' with driver '$DriverName'..."
    if (-not (Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue)) {
        Add-PrinterDriver -Name $DriverName
    }
    Add-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
    Add-Printer -Name $QueueName -DriverName $DriverName -PortName $PortName
} else {
    Write-Host "Queue '$QueueName' already exists, leaving it as-is."
}

Write-Host ""
Write-Host "Done. Print to '$QueueName' from any app (Notepad -> File -> Print is easiest)."
Write-Host "Diagnostics:"
Write-Host "  Monitor log:        C:\Windows\Temp\tmt70mon.log"
Write-Host "  Last job's stdout:  C:\Windows\Temp\tmt70mon-lastjob.log"
Write-Host ""
Write-Host "To change the target device/options later:"
Write-Host "  Set-ItemProperty '$ConfigKey' -Name Device -Value '<uri>'"
Write-Host "  Restart-Service Spooler"
Write-Host ""
Write-Host "Tear down with: .\register-monitor.ps1 -Unregister"
