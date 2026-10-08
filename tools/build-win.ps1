<#
.SYNOPSIS
    Build the Windows package for epson-tmt70ii-escpos.

.DESCRIPTION
    The counterpart of tools/build.sh.  Stages the Windows front end together
    with the *unmodified* Linux sources it imports, then produces:

        build\win\...                        the staging tree
        epson-tmt70ii-escpos-win_<ver>.zip   portable, unzip and run
        tmt70print-setup-<ver>.exe           Inno Setup installer, if ISCC is present

    Zero .NET at build time and zero .NET at run time, with one exception:
    win\monitor\tmt70mon\tmt70mon.c (the native print-monitor DLL behind the
    optional "nativequeue" installer task) needs a C compiler on PATH --
    a genuinely new build-time dependency, not a .NET one and not something
    the shipped package itself depends on at runtime (the compiled DLL is
    staged like any other binary). MinGW-w64 works fine, e.g.:
        winget install BrechtSanders.WinLibs.POSIX.UCRT
    Pass -SkipMonitor to build without it. Inno Setup itself is native
    Pascal; the optional bundled runtime is CPython's embeddable distribution.

.PARAMETER Fetch
    Download the embeddable CPython runtime and libusb-1.0.dll into the staging
    tree so the result runs on a machine with no Python installed.
    Needs network access.  Without it the package uses the system Python and
    the system libusb-1.0.dll.

.PARAMETER SkipTests
    Do not run win\selftest.py first.  Not recommended.

.PARAMETER SkipMonitor
    Do not build win\monitor\tmt70mon\tmt70mon.dll (needs a C compiler on
    PATH). Without it, the installer's "nativequeue" task has nothing to
    install -- the CLI and its other transports are unaffected.

.EXAMPLE
    .\tools\build-win.ps1
    .\tools\build-win.ps1 -Fetch
#>

[CmdletBinding()]
param(
    [switch]$Fetch,
    [switch]$SkipTests,
    [switch]$SkipMonitor,
    [string]$PythonVersion = "3.12.7",
    [string]$LibUsbVersion = "1.0.27",
    [string]$OutDir
)

$ErrorActionPreference = "Stop"

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Info($m) { Write-Host "    $m" -ForegroundColor DarkGray }
function Write-Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

# --- paths ---------------------------------------------------------------- #
$Root = Split-Path -Parent $PSScriptRoot
$Pkg = "epson-tmt70ii-escpos"
$Version = "1.0.0"

# keep the version in step with debian/changelog
$changelog = Join-Path $Root "debian\changelog"
if (Test-Path $changelog) {
    $first = (Get-Content $changelog -TotalCount 1)
    if ($first -match '\(([^)]+)\)') {
        $Version = $Matches[1] -replace '-.*$', ''
        Write-Info "version $Version (from debian/changelog)"
    }
}

if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = $Root }
$Stage = Join-Path $Root "build\win"

# --- python --------------------------------------------------------------- #
$Py = $null
foreach ($cand in @("py", "python", "python3")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source; break }
}
if (-not $Py) { throw "No Python found on PATH. Install Python 3.8+ to build." }
Write-Info "python: $Py"

# --- tests ---------------------------------------------------------------- #
if (-not $SkipTests) {
    Write-Step "selftest"
    & $Py (Join-Path $Root "win\selftest.py")
    if ($LASTEXITCODE -ne 0) { throw "win\selftest.py failed (exit $LASTEXITCODE)" }
}

# --- stage ---------------------------------------------------------------- #
Write-Step "stage -> $Stage"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

# the Windows front end
Copy-Item (Join-Path $Root "win\tmt70print.py")  $Stage
Copy-Item (Join-Path $Root "win\tmt70print.cmd") $Stage
Copy-Item (Join-Path $Root "win\tmt70print.ini") $Stage
Copy-Item (Join-Path $Root "win\selftest.py")    $Stage
Copy-Item (Join-Path $Root "win\tmt70win") $Stage -Recurse

# the shared Linux sources, imported verbatim by tmt70win\core.py
foreach ($rel in @("src\filter\rastertoescpos", "src\backend\tmt70",
                   "tools\mkraster.py", "tools\escpos_dis.py")) {
    $dst = Join-Path $Stage $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item (Join-Path $Root $rel) $dst
}

# docs
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "docs") | Out-Null
foreach ($doc in @("docs\WINDOWS.md", "docs\README.md", "docs\PROTOCOL.md",
                   "debian\copyright")) {
    $src = Join-Path $Root $doc
    if (Test-Path $src) { Copy-Item $src (Join-Path $Stage "docs") }
}

Get-ChildItem -Recurse -Force -Path $Stage -Include "__pycache__" -Directory `
    | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# --- native print-monitor DLL (optional installer task: nativequeue) ------ #
if (-not $SkipMonitor) {
    Write-Step "compile tmt70mon.dll (native print monitor)"
    $gcc = Get-Command gcc -ErrorAction SilentlyContinue
    if (-not $gcc) {
        throw ("No C compiler (gcc) found on PATH -- needed to build " +
               "win\monitor\tmt70mon\tmt70mon.dll. Install a MinGW-w64 toolchain " +
               "(e.g. 'winget install BrechtSanders.WinLibs.POSIX.UCRT') or pass " +
               "-SkipMonitor to build without the native print queue feature.")
    }
    $monSrc = Join-Path $Root "win\monitor\tmt70mon\tmt70mon.c"
    $monOut = Join-Path $Stage "monitor\tmt70mon\tmt70mon.dll"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $monOut) | Out-Null
    & $gcc.Source -Wall -Wextra -shared -o $monOut $monSrc -lgdi32 -luser32 -ladvapi32
    if ($LASTEXITCODE -ne 0) { throw "gcc failed to build tmt70mon.dll (exit $LASTEXITCODE)" }
    Copy-Item (Join-Path $Root "win\monitor\register-monitor.ps1") (Join-Path $Stage "monitor")
    $monReadme = Join-Path $Root "win\monitor\README.md"
    if (Test-Path $monReadme) { Copy-Item $monReadme (Join-Path $Stage "monitor") }
    Write-Info "tmt70mon.dll -> monitor\tmt70mon\tmt70mon.dll"
} else {
    Write-Warn "skipped native print monitor build (-SkipMonitor)"
    Write-Warn "the installer's 'nativequeue' task will have nothing to install"
}

# --- optional bundled runtime --------------------------------------------- #
if ($Fetch) {
    Write-Step "fetch embeddable python $PythonVersion"
    $tmp = Join-Path $env:TEMP "tmt70-build"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $pyZip = Join-Path $tmp "python-embed.zip"
    $pyUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip -UseBasicParsing
    Expand-Archive -Path $pyZip -DestinationPath (Join-Path $Stage "python") -Force

    # the embeddable build ships a ._pth that disables site imports; add our dir
    Get-ChildItem (Join-Path $Stage "python") -Filter "*._pth" | ForEach-Object {
        Add-Content -Path $_.FullName -Value "..`nimport site"
    }
    Write-Info "bundled python -> python\python.exe"

    Write-Step "fetch libusb $LibUsbVersion"
    $luZip = Join-Path $tmp "libusb.7z"
    $luUrl = "https://github.com/libusb/libusb/releases/download/v$LibUsbVersion/libusb-$LibUsbVersion.7z"
    try {
        Invoke-WebRequest -Uri $luUrl -OutFile $luZip -UseBasicParsing
        $sevenZip = Get-Command 7z -ErrorAction SilentlyContinue
        if ($sevenZip) {
            $luOut = Join-Path $tmp "libusb"
            & $sevenZip.Source x -y "-o$luOut" $luZip | Out-Null
            # VS2022\MS64: the UCRT-linked build (VCRUNTIME140 + api-ms-win-crt-*,
            # present on Windows 10/11 out of the box). VS2013\MS64 also matches a
            # bare "MS64\dll" filter but needs the old MSVCR120 redistributable,
            # which most machines don't have -- do not relax this back to a
            # substring match without re-checking with objdump -p.
            $dll = Get-ChildItem $luOut -Recurse -Filter "libusb-1.0.dll" `
                | Where-Object { $_.FullName -match "VS2022\\MS64\\dll" } | Select-Object -First 1
            if ($dll) {
                New-Item -ItemType Directory -Force -Path (Join-Path $Stage "vendor") | Out-Null
                Copy-Item $dll.FullName (Join-Path $Stage "vendor")
                Write-Info "bundled libusb -> vendor\libusb-1.0.dll"
            }
        } else {
            Write-Warn "7z not found; place libusb-1.0.dll (MS64\dll) in win\vendor\ by hand"
        }
    } catch {
        Write-Warn "libusb download failed: $($_.Exception.Message)"
        Write-Warn "place libusb-1.0.dll in win\vendor\ by hand, or rely on a system copy"
    }
} else {
    # use a libusb the developer already dropped in win\vendor\
    $localDll = Join-Path $Root "win\vendor\libusb-1.0.dll"
    if (Test-Path $localDll) {
        New-Item -ItemType Directory -Force -Path (Join-Path $Stage "vendor") | Out-Null
        Copy-Item $localDll (Join-Path $Stage "vendor")
        Write-Info "bundled libusb from win\vendor\"
    } else {
        Write-Warn "no win\vendor\libusb-1.0.dll -- USB output needs a system libusb"
        Write-Warn "run with -Fetch to download one"
    }
}

# --- zip ------------------------------------------------------------------ #
Write-Step "zip"
$zip = Join-Path $OutDir "$Pkg-win_$Version.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $zip -CompressionLevel Optimal
Write-Info $zip

# --- installer ------------------------------------------------------------ #
$iss = Join-Path $Root "win\tmt70print.iss"
$iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if (-not $iscc) {
    foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                     "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $p) { $iscc = Get-Command $p; break }
    }
}
if ($iscc -and (Test-Path $iss)) {
    Write-Step "Inno Setup"
    & $iscc.Source "/DMyAppVersion=$Version" "/DStageDir=$Stage" "/O$OutDir" $iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }
    Write-Info (Join-Path $OutDir "tmt70print-setup-$Version.exe")
} else {
    Write-Warn "Inno Setup (ISCC.exe) not found - skipped the installer"
    Write-Warn "install it from https://jrsoftware.org/isdl.php, or ship the .zip"
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  portable: $zip"
Write-Host "  try it:   powershell -c `"Expand-Archive '$zip' C:\tmt70; C:\tmt70\tmt70print.cmd --list`""
