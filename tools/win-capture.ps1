<#
.SYNOPSIS
    Capture the raw byte stream that the EPSON Advanced Printer Driver (APD4)
    emits for the TM-T70II, for reverse-engineering the ESC/POS it produces.

.DESCRIPTION
    Creates a throwaway printer queue that uses the same Epson driver but prints
    to a FILE port, mirrors the real queue's current driver preferences onto it,
    prints a set of test documents, and saves one .prn per document.

    Typical workflow for building the option->command table:

        # 1. baseline (whatever the queue is set to now)
        .\tools\win-capture.ps1 -Tag baseline

        # 2. open Preferences for "EPSON TM-T70II Receipt", change ONE setting
        #    (e.g. Print Density = Dark), click OK, then:
        .\tools\win-capture.ps1 -Tag density-dark

        # 3. ...repeat for each setting in the matrix (see docs/PROTOCOL.md)

    Then decode:  python tools\escpos_dis.py capture\density-dark__blocks.prn

.NOTES
    * Run in an elevated PowerShell (creating printer ports needs admin).
    * If the Epson driver refuses to spool to a plain FILE port, fall back to
      USBPcap + Wireshark (see docs/PROTOCOL.md).  Nothing here is installed via
      a manual download - use:  choco install usbpcap wireshark
#>

[CmdletBinding()]
param(
    [string]$Tag = "baseline",
    [string]$SourcePrinter = "EPSON TM-T70II Receipt",
    [string]$Driver = "EPSON TM-T70 ReceiptE4",
    [string]$OutDir,
    [string]$CapturePrinter = "TMT70CAP",
    [switch]$NoCleanup,
    [switch]$KeepDevmode
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("OutDir") -or [string]::IsNullOrWhiteSpace($OutDir)) {
    $root = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($root)) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if ([string]::IsNullOrWhiteSpace($root)) { $root = (Get-Location).Path }
    $OutDir = Join-Path $root "..\capture"
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Info($m) { Write-Host "    $m" -ForegroundColor DarkGray }

# --------------------------------------------------------------------------- #
# 0. sanity
# --------------------------------------------------------------------------- #
if (-not (Get-Printer -Name $SourcePrinter -ErrorAction SilentlyContinue)) {
    throw "Source printer '$SourcePrinter' not found. Run: Get-Printer | ft Name,DriverName"
}
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent() `
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Warning "Not elevated - port/printer creation may fail." }

# --------------------------------------------------------------------------- #
# 1. test documents  (plain text; the driver rasterises these to ESC/POS)
# --------------------------------------------------------------------------- #
$docDir = Join-Path $OutDir "_docs"
New-Item -ItemType Directory -Force -Path $docDir | Out-Null

$block = ([char]0x2588).ToString() * 40      # full-block glyph -> dense raster
$docs = @{
    "plain"   = @(
        "TM-T70II capture test - plain text",
        "The quick brown fox jumps over the lazy dog 0123456789",
        "Line spacing / feed / cut framing check.",
        "",
        "End of plain test."
    )
    "columns" = @(
        "ITEM                    QTY      PRICE",
        "--------------------------------------",
        "Coffee, large             2       7.00",
        "Bagel w/ cream cheese     1       3.25",
        "Orange juice              1       2.50",
        "--------------------------------------",
        "TOTAL                            12.75"
    )
    "blocks"  = @(
        "RASTER DENSITY / GRAPHICS FRAMING",
        $block, $block, $block, $block, $block, $block, $block, $block,
        "half:", ($block.Substring(0,20)),
        $block, $block, $block, $block
    )
    "ruler"   = @(
        "0........1.........2.........3.........4.........5",
        "|123456789|123456789|123456789|123456789|123456789",
        "LEFT-MARGIN / PRINT-AREA-WIDTH probe",
        ("X" * 48)
    )
}
foreach ($k in $docs.Keys) {
    $p = Join-Path $docDir "$k.txt"
    Set-Content -Path $p -Value $docs[$k] -Encoding ASCII
}
Write-Step "Test documents in $docDir"

# --------------------------------------------------------------------------- #
# 2. FILE port + capture queue
# --------------------------------------------------------------------------- #
$portFile = Join-Path $OutDir ("_port_{0}.prn" -f $Tag)
if (Test-Path $portFile) { Remove-Item $portFile -Force }

# a "port" whose name is a file path writes spooled data straight to that file
if (Get-PrinterPort -Name $portFile -ErrorAction SilentlyContinue) {
    Write-Info "Reusing port $portFile"
} else {
    Write-Step "Creating FILE port $portFile"
    Add-PrinterPort -Name $portFile
}

if (Get-Printer -Name $CapturePrinter -ErrorAction SilentlyContinue) {
    Write-Info "Reusing capture printer $CapturePrinter -> repointing port"
    Set-Printer -Name $CapturePrinter -PortName $portFile
} else {
    Write-Step "Creating capture printer $CapturePrinter (driver: $Driver)"
    Add-Printer -Name $CapturePrinter -DriverName $Driver -PortName $portFile
}

# --------------------------------------------------------------------------- #
# 3. mirror the real queue's current driver preferences onto the capture queue
#    (so whatever you set in the Preferences dialog is what gets captured)
# --------------------------------------------------------------------------- #
$dmDat = Join-Path $OutDir ("_devmode_{0}.dat" -f $Tag)
Write-Step "Mirroring driver preferences from '$SourcePrinter'"
& rundll32 printui.dll,PrintUIEntry /Ss /n "$SourcePrinter" /a "$dmDat" g d 2>&1 | Out-Null
Start-Sleep -Milliseconds 400
if (Test-Path $dmDat) {
    & rundll32 printui.dll,PrintUIEntry /Sr /n "$CapturePrinter" /a "$dmDat" g d 2>&1 | Out-Null
    Start-Sleep -Milliseconds 400
    Write-Info "DEVMODE mirrored ($((Get-Item $dmDat).Length) bytes)"
    if (-not $KeepDevmode) { Remove-Item $dmDat -Force }
} else {
    Write-Warning "printui /Ss produced no file - capture will use the queue's default DEVMODE"
}

# record the human-readable config alongside the captures
$cfgTxt = Join-Path $OutDir ("{0}__printconfig.txt" -f $Tag)
"### Get-PrintConfiguration '$SourcePrinter'  ($(Get-Date -Format s))" | Set-Content $cfgTxt
try { Get-PrintConfiguration -PrinterName $SourcePrinter | Format-List * | Out-String | Add-Content $cfgTxt } catch {}
"" | Add-Content $cfgTxt
"### Get-Printer" | Add-Content $cfgTxt
Get-Printer -Name $SourcePrinter | Format-List * | Out-String | Add-Content $cfgTxt

# --------------------------------------------------------------------------- #
# 4. print each document, harvest the .prn
# --------------------------------------------------------------------------- #
function Wait-Spool($printerName, $timeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    do {
        Start-Sleep -Milliseconds 500
        $jobs = @(Get-PrintJob -PrinterName $printerName -ErrorAction SilentlyContinue)
    } while ($jobs.Count -gt 0 -and (Get-Date) -lt $deadline)
    Start-Sleep -Milliseconds 800   # let the port finish flushing to disk
}

$results = @()
foreach ($k in ($docs.Keys | Sort-Object)) {
    $src = Join-Path $docDir "$k.txt"
    $dst = Join-Path $OutDir ("{0}__{1}.prn" -f $Tag, $k)
    if (Test-Path $portFile) { Remove-Item $portFile -Force }

    Write-Step "Printing '$k' -> $CapturePrinter"
    Get-Content -Path $src | Out-Printer -Name $CapturePrinter
    Wait-Spool $CapturePrinter

    if (Test-Path $portFile) {
        Move-Item $portFile $dst -Force
        $len = (Get-Item $dst).Length
        Write-Host ("    saved {0}  ({1} bytes)" -f (Split-Path $dst -Leaf), $len) -ForegroundColor Green
        $results += [pscustomobject]@{ Doc = $k; File = $dst; Bytes = $len }
    } else {
        Write-Warning "No output produced for '$k' - the Epson driver may refuse the FILE port. Use USBPcap fallback."
    }
}

# --------------------------------------------------------------------------- #
# 5. cleanup
# --------------------------------------------------------------------------- #
if (-not $NoCleanup) {
    Write-Step "Cleanup"
    Remove-Printer -Name $CapturePrinter -ErrorAction SilentlyContinue
    Remove-PrinterPort -Name $portFile -ErrorAction SilentlyContinue
    if (Test-Path $portFile) { Remove-Item $portFile -Force -ErrorAction SilentlyContinue }
} else {
    Write-Info "Left $CapturePrinter / port in place (-NoCleanup)"
}

Write-Host ""
Write-Host "RESULTS ($Tag):" -ForegroundColor Yellow
$results | Format-Table -AutoSize
Write-Host "Decode with:  python tools\escpos_dis.py `"$OutDir\$Tag`__blocks.prn`""
