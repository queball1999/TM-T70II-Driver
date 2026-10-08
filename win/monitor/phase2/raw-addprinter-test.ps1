<#
.SYNOPSIS
    Phase 2 probe: create a printer queue via the raw Win32 AddPrinter API,
    bypassing the CIM MSFT_Printer provider that Add-Printer/Get-PrinterPort
    choke on for our custom monitor's port.

.DESCRIPTION
    Throwaway diagnostic, not part of the shipped installer. Creates
    "TMT70 Raster Test" bound to port "TMT70:" (the TMT70 Monitor's fixed
    port) using whichever driver name you pass -- default "Microsoft IPP
    Class Driver". Run elevated.

.PARAMETER DriverName
    The installed driver to pair with our port. Try different candidates
    here without editing the script -- e.g. "Universal Print Class Driver",
    "Microsoft enhanced Point and Print compatibility driver".

.PARAMETER Remove
    Delete the test queue instead of creating it.

.EXAMPLE
    .\raw-addprinter-test.ps1
    .\raw-addprinter-test.ps1 -DriverName "Universal Print Class Driver"
    .\raw-addprinter-test.ps1 -Remove
#>
param(
    [string]$DriverName = "Microsoft IPP Class Driver",
    [switch]$Remove
)

$QueueName = "TMT70 Raster Test"
$PortName  = "TMT70:"

Add-Type -Namespace TMT70Raw -Name Spooler -MemberDefinition @"
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct PRINTER_INFO_2 {
    public string pServerName;
    public string pPrinterName;
    public string pShareName;
    public string pPortName;
    public string pDriverName;
    public string pComment;
    public string pLocation;
    public IntPtr pDevMode;
    public string pSepFile;
    public string pPrintProcessor;
    public string pDatatype;
    public string pParameters;
    public IntPtr pSecurityDescriptor;
    public uint Attributes;
    public uint Priority;
    public uint DefaultPriority;
    public uint StartTime;
    public uint UntilTime;
    public uint Status;
    public uint cJobs;
    public uint AveragePPM;
}

[DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern IntPtr AddPrinter(string pName, uint Level, ref PRINTER_INFO_2 pPrinter);

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct PRINTER_DEFAULTS {
    public string pDatatype;
    public IntPtr pDevMode;
    public uint DesiredAccess;
}

[DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool OpenPrinter(string pPrinterName, out IntPtr phPrinter, ref PRINTER_DEFAULTS pDefault);

[DllImport("winspool.drv", SetLastError = true)]
public static extern bool DeletePrinter(IntPtr hPrinter);

[DllImport("winspool.drv", SetLastError = true)]
public static extern bool ClosePrinter(IntPtr hPrinter);
"@

# STANDARD_RIGHTS_REQUIRED | PRINTER_ACCESS_ADMINISTER | PRINTER_ACCESS_USE
$PRINTER_ALL_ACCESS = 0x000F000C

if ($Remove) {
    $h = [IntPtr]::Zero
    $pd = New-Object TMT70Raw.Spooler+PRINTER_DEFAULTS
    $pd.DesiredAccess = $PRINTER_ALL_ACCESS
    if ([TMT70Raw.Spooler]::OpenPrinter($QueueName, [ref]$h, [ref]$pd)) {
        $deleted = [TMT70Raw.Spooler]::DeletePrinter($h)
        if (-not $deleted) {
            $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Write-Error "DeletePrinter failed: Win32 error $err"
        } else {
            Write-Host "Removed '$QueueName'."
        }
        [TMT70Raw.Spooler]::ClosePrinter($h) | Out-Null
    } else {
        Write-Host "'$QueueName' not found (nothing to remove)."
    }
    return
}

$pi = New-Object TMT70Raw.Spooler+PRINTER_INFO_2
$pi.pPrinterName    = $QueueName
$pi.pPortName       = $PortName
$pi.pDriverName     = $DriverName
$pi.pPrintProcessor = "winprint"
$pi.pDatatype       = "RAW"

Write-Host "Creating '$QueueName' on port '$PortName' with driver '$DriverName'..."
$handle = [TMT70Raw.Spooler]::AddPrinter($null, 2, [ref]$pi)
if ($handle -eq [IntPtr]::Zero) {
    $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Write-Error "AddPrinter failed: Win32 error $err"
} else {
    [TMT70Raw.Spooler]::ClosePrinter($handle) | Out-Null
    Write-Host "Created. Print something to '$QueueName' now, then check:"
    Write-Host "  C:\Windows\Temp\tmt70mon.log"
    Write-Host "  C:\Windows\Temp\tmt70mon-lastjob.log"
    Write-Host "  C:\Windows\Temp\tmt70-phase2-capture.bin  (the redirected 'device' output)"
}
