<#
.SYNOPSIS
    Capture the raw ESC/POS byte stream that the EPSON Advanced Printer Driver
    (APD4) produces for the TM-T70II, for reverse-engineering.

.DESCRIPTION
    Method "SwapPort" (default): temporarily repoints the REAL queue
    ("EPSON TM-T70II Receipt") at a FILE port, prints the test documents, then
    restores the original port.  Because it is the real queue, whatever you set
    in *Printer ▸ Printing preferences* (density, speed, paper width, 180 deg,
    paper reduction ...) is exactly what gets captured.

    Method "CloneQueue": prints through a throwaway queue on the same driver.
    Fast and non-invasive, but it uses the driver's DEFAULT settings only.

    The FILE port target lives in C:\Windows\Temp (writable by the LocalSystem
    spooler); finished captures are moved to .\capture\<tag>__<doc>.prn.

.EXAMPLE
    # 1. open  Settings ▸ Printers ▸ EPSON TM-T70II Receipt ▸ Printing preferences
    #    set Print Density = Dark, click OK
    # 2.
    .\tools\win-capture.ps1 -Tag density-dark
    # 3.
    python .\tools\escpos_dis.py .\capture\density-dark__blocks.prn

.EXAMPLE
    # if a previous run was killed and left the queue on the file port:
    .\tools\win-capture.ps1 -RestorePort
#>

[CmdletBinding()]
param(
    [string]$Tag = "baseline",
    [ValidateSet("SwapPort", "CloneQueue")]
    [string]$Method = "SwapPort",
    [string]$SourcePrinter = "EPSON TM-T70II Receipt",
    [string]$Driver = "EPSON TM-T70 ReceiptE4",
    [string]$OutDir,
    [string]$CapturePrinter = "TMT70CAP",
    [switch]$RestorePort,
    [switch]$NoCleanup
)

$ErrorActionPreference = "Stop"

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Info($m) { Write-Host "    $m" -ForegroundColor DarkGray }

# --- paths ---------------------------------------------------------------- #
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $root = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($root)) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if ([string]::IsNullOrWhiteSpace($root)) { $root = (Get-Location).Path }
    $OutDir = Join-Path $root "..\capture"
}
$OutDir   = [System.IO.Path]::GetFullPath($OutDir)
$PortRoot = Join-Path $env:WINDIR "Temp"          # LocalSystem-writable
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# stash the original port name next to the captures so -RestorePort works
$origPortStash = Join-Path $OutDir "_original_port.txt"

# --- helpers ------------------------------------------------------------- #
function Get-QueuePort($name) { (Get-Printer -Name $name -ErrorAction Stop).PortName }

function Restore-QueuePort {
    param($name, $port)
    if ([string]::IsNullOrWhiteSpace($port)) { return }
    try {
        Set-Printer -Name $name -PortName $port
        Write-Step "restored '$name' -> port '$port'"
        Remove-Item $origPortStash -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "COULD NOT restore the port automatically. Run:"
        Write-Warning "  Set-Printer -Name `"$name`" -PortName `"$port`""
    }
}

function Wait-Spool($printerName, $timeoutSec = 40) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    do {
        Start-Sleep -Milliseconds 500
        $jobs = @(Get-PrintJob -PrinterName $printerName -ErrorAction SilentlyContinue)
    } while ($jobs.Count -gt 0 -and (Get-Date) -lt $deadline)
    Start-Sleep -Milliseconds 900
}

function Ensure-FilePort($path) {
    if (Test-Path $path) { Remove-Item $path -Force -ErrorAction SilentlyContinue }
    if (-not (Get-PrinterPort -Name $path -ErrorAction SilentlyContinue)) {
        Add-PrinterPort -Name $path
    }
}

function Remove-FilePort($path) {
    Get-PrinterPort -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq $path } |
        ForEach-Object { Remove-PrinterPort -Name $_.Name -ErrorAction SilentlyContinue }
    if (Test-Path $path) { Remove-Item $path -Force -ErrorAction SilentlyContinue }
}

# --- -RestorePort short-circuit --------------------------------------- #
if ($RestorePort) {
    $p = $null
    if (Test-Path $origPortStash) { $p = (Get-Content $origPortStash -Raw).Trim() }
    if (-not $p) { $p = "ESDPRT001"; Write-Warning "no stash found; assuming ESDPRT001" }
    Restore-QueuePort -name $SourcePrinter -port $p
    return
}

# --- sanity ----------------------------------------------------------- #
if (-not (Get-Printer -Name $SourcePrinter -ErrorAction SilentlyContinue)) {
    throw "Source printer '$SourcePrinter' not found. Get-Printer | ft Name,DriverName"
}

# --- test documents (driver rasterises these to ESC/POS) -------------- #
$docDir = Join-Path $OutDir "_docs"
New-Item -ItemType Directory -Force -Path $docDir | Out-Null
$block = ([char]0x2588).ToString() * 40
$docs = [ordered]@{
    plain   = @("TM-T70II capture - plain text",
                "The quick brown fox jumps over the lazy dog 0123456789",
                "line spacing / feed / cut framing check.", "", "End.")
    columns = @("ITEM                    QTY      PRICE",
                "--------------------------------------",
                "Coffee, large             2       7.00",
                "Bagel                     1       3.25",
                "--------------------------------------",
                "TOTAL                            10.25")
    blocks  = @("RASTER / DENSITY PROBE", $block, $block, $block, $block,
                $block, $block, $block, $block, "half:", $block.Substring(0, 20),
                $block, $block, $block, $block)
    ruler   = @("0........1.........2.........3.........4.........5",
                "|123456789|123456789|123456789|123456789|123456789",
                "LEFT-MARGIN / PRINT-AREA-WIDTH probe", ("X" * 48))
}
foreach ($k in $docs.Keys) {
    Set-Content -Path (Join-Path $docDir "$k.txt") -Value $docs[$k] -Encoding ASCII
}

# record the queue's current (real) configuration
$cfgTxt = Join-Path $OutDir ("{0}__printconfig.txt" -f $Tag)
"### $SourcePrinter  ($(Get-Date -Format s))  method=$Method" | Set-Content $cfgTxt
try { Get-PrintConfiguration -PrinterName $SourcePrinter | Format-List * | Out-String | Add-Content $cfgTxt } catch {}

$portFile = Join-Path $PortRoot ("tmt70cap_{0}_{1}.prn" -f $Tag, $PID)
$results  = @()

# ===================================================================== #
if ($Method -eq "SwapPort") {

    $origPort = Get-QueuePort $SourcePrinter
    Set-Content -Path $origPortStash -Value $origPort -Encoding ASCII
    Write-Step "real queue port is '$origPort' (stashed)"

    try {
        Write-Step "creating FILE port $portFile"
        Ensure-FilePort $portFile

        Write-Step "repointing '$SourcePrinter' -> FILE port"
        Set-Printer -Name $SourcePrinter -PortName $portFile

        foreach ($k in $docs.Keys) {
            $src = Join-Path $docDir "$k.txt"
            $dst = Join-Path $OutDir ("{0}__{1}.prn" -f $Tag, $k)
            if (Test-Path $portFile) { Remove-Item $portFile -Force }

            Write-Step "printing '$k'"
            Get-Content -Path $src | Out-Printer -Name $SourcePrinter
            Wait-Spool $SourcePrinter

            if (Test-Path $portFile) {
                Move-Item $portFile $dst -Force
                $len = (Get-Item $dst).Length
                Write-Host ("    saved {0} ({1} bytes)" -f (Split-Path $dst -Leaf), $len) -ForegroundColor Green
                $results += [pscustomobject]@{ Doc = $k; Bytes = $len }
            } else {
                Write-Warning "no output for '$k' - the driver may have refused the FILE port."
                Write-Warning "Fall back to USBPcap:  choco install usbpcap wireshark"
            }
        }
    }
    finally {
        Restore-QueuePort -name $SourcePrinter -port $origPort
        if (-not $NoCleanup) { Remove-FilePort $portFile }
    }
}
# ===================================================================== #
else {   # CloneQueue  (driver DEFAULT settings only)

    Write-Warning "CloneQueue captures the driver's DEFAULT settings, not the"
    Write-Warning "queue's Printing-preferences.  Use -Method SwapPort for those."
    try {
        Ensure-FilePort $portFile
        if (Get-Printer -Name $CapturePrinter -ErrorAction SilentlyContinue) {
            Set-Printer -Name $CapturePrinter -PortName $portFile
        } else {
            Add-Printer -Name $CapturePrinter -DriverName $Driver -PortName $portFile
        }
        foreach ($k in $docs.Keys) {
            $dst = Join-Path $OutDir ("{0}__{1}.prn" -f $Tag, $k)
            if (Test-Path $portFile) { Remove-Item $portFile -Force }
            Get-Content -Path (Join-Path $docDir "$k.txt") | Out-Printer -Name $CapturePrinter
            Wait-Spool $CapturePrinter
            if (Test-Path $portFile) {
                Move-Item $portFile $dst -Force
                $results += [pscustomobject]@{ Doc = $k; Bytes = (Get-Item $dst).Length }
                Write-Host ("    saved {0}" -f (Split-Path $dst -Leaf)) -ForegroundColor Green
            }
        }
    }
    finally {
        if (-not $NoCleanup) {
            Remove-Printer -Name $CapturePrinter -ErrorAction SilentlyContinue
            Remove-FilePort $portFile
        }
    }
}

Write-Host ""
Write-Host "RESULTS ($Tag, $Method):" -ForegroundColor Yellow
$results | Format-Table -AutoSize
Write-Host "Decode:  python tools\escpos_dis.py `"$OutDir\$Tag`__blocks.prn`""
Write-Host "Compare: fc /b `"$OutDir\baseline__blocks.prn`" `"$OutDir\$Tag`__blocks.prn`""
