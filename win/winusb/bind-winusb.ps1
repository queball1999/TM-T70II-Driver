<#
.SYNOPSIS
    Bind WinUSB to the EPSON TM-T70II without a GUI (no Zadig needed).

.DESCRIPTION
    Automates exactly what Zadig's "Replace Driver" button does by hand:

      1. Get or create a self-signed code-signing certificate, scoped to this
         one purpose (subject "CN=tmt70print local WinUSB signer").
      2. Build a catalog for tmt70-winusb.inf (New-FileCatalog) and sign it
         with that certificate (Set-AuthenticodeSignature) -- this is the
         same "unsigned INF + locally-trusted catalog" technique libwdi (the
         library behind Zadig) uses; it works because step 3 makes *this
         machine* trust the signer, not because any CA vouches for it.
      3. Import the certificate's public half into the LocalMachine Root and
         TrustedPublisher stores, so Windows accepts the catalog from step 2.
      4. pnputil /add-driver ... /install -- stages the driver and rebinds
         any already-present matching device (USB\VID_04B8&PID_0202) to it.

    Must be run elevated (Administrator). Idempotent: safe to re-run: it
    reuses an existing valid certificate and pnputil no-ops on an
    already-installed identical driver.

.PARAMETER Unbind
    Remove the driver package this script installed (pnputil /delete-driver
    /uninstall) so Windows falls back to its next-best match for the device
    (typically none, i.e. "Unknown Device", same as after a plain Zadig
    "Replace Driver" -- undo -- reconnecting the printer or using Device
    Manager -> Update driver is still the way to restore a *specific* prior
    driver, e.g. the vendor TMUSB one).

.EXAMPLE
    .\bind-winusb.ps1
    .\bind-winusb.ps1 -Unbind
#>
param(
    [switch]$Unbind
)

$ErrorActionPreference = "Stop"

$VendorId    = "04B8"
$ProductId   = "0202"
$HardwareId  = "USB\VID_${VendorId}&PID_${ProductId}"
$CertSubject = "CN=tmt70print local WinUSB signer"
$InfName     = "tmt70-winusb.inf"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated (Administrator) PowerShell."
    }
}

function Find-PublishedDriver {
    # pnputil's own name for the copy of tmt70-winusb.inf it staged into the
    # driver store, e.g. "oem23.inf" -- looked up by original filename so
    # -Unbind works without us having to remember it between runs.
    $enum = & pnputil.exe /enum-drivers
    $current = $null
    foreach ($line in $enum) {
        if ($line -match '^Published Name:\s*(oem\d+\.inf)') { $current = $matches[1]; continue }
        if ($current -and $line -match '^Original Name:\s*(.+)$' -and $matches[1].Trim() -eq $InfName) {
            return $current
        }
    }
    return $null
}

Assert-Admin

if ($Unbind) {
    $oem = Find-PublishedDriver
    if (-not $oem) {
        Write-Host "No published '$InfName' driver found -- nothing to remove."
        exit 0
    }
    Write-Host "Removing driver package $oem ($InfName)..."
    & pnputil.exe /delete-driver $oem /uninstall /force
    Write-Host "Done. The device will fall back to Windows' next-best driver match" `
               "(commonly none -- use Device Manager -> Update driver to pick a specific one)."
    exit 0
}

Write-Host "Device:  $HardwareId"

# --- 1. certificate: reuse a still-valid one, else mint a fresh one -------- #
$cert = Get-ChildItem Cert:\LocalMachine\My |
    Where-Object { $_.Subject -eq $CertSubject -and $_.NotAfter -gt (Get-Date) } |
    Select-Object -First 1

if ($cert) {
    Write-Host "Reusing existing signing certificate (thumbprint $($cert.Thumbprint))."
} else {
    Write-Host "Creating local code-signing certificate '$CertSubject'..."
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $CertSubject `
        -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(20) `
        -KeyUsage DigitalSignature -KeyAlgorithm RSA -KeyLength 2048
}

# --- 2. stage the INF, build + sign a catalog for it ------------------------ #
$stage = Join-Path $env:TEMP "tmt70-winusb-stage"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item (Join-Path $ScriptDir $InfName) $stage

$catPath = Join-Path $stage "tmt70-winusb.cat"
Write-Host "Building driver catalog..."
New-FileCatalog -Path $stage -CatalogFilePath $catPath -CatalogVersion 2.0 | Out-Null

Write-Host "Signing catalog with the local certificate..."
$sig = Set-AuthenticodeSignature -FilePath $catPath -Certificate $cert
if ($sig.Status -ne "Valid") {
    throw "Catalog signature status is '$($sig.Status)' (expected Valid): $($sig.StatusMessage)"
}

# --- 3. trust that certificate on this machine ------------------------------ #
$certFile = Join-Path $stage "tmt70-winusb-signer.cer"
Export-Certificate -Cert $cert -FilePath $certFile | Out-Null
foreach ($storeName in "Root", "TrustedPublisher") {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
        $storeName, "LocalMachine")
    $store.Open("ReadWrite")
    try {
        $store.Add([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($certFile))
    } finally {
        $store.Close()
    }
}
Write-Host "Certificate trusted in LocalMachine\Root and \TrustedPublisher."

# --- 4. install: stage the driver and bind it to the present device -------- #
Write-Host "Installing via pnputil..."
$infPath = Join-Path $stage $InfName
$out = & pnputil.exe /add-driver $infPath /install 2>&1
$out | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) {
    throw "pnputil /add-driver failed (exit $LASTEXITCODE)"
}

# --- verify ------------------------------------------------------------------ #
$dev = Get-PnpDevice | Where-Object { $_.InstanceId -match [regex]::Escape($HardwareId) } |
    Select-Object -First 1
if (-not $dev) {
    Write-Host ""
    Write-Host "WARNING: no device matching $HardwareId is currently present -- the driver" `
               "is staged and will bind automatically next time the printer is plugged in."
    exit 0
}
$service = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_Service").Data
Write-Host ""
if ($service -eq "WinUSB") {
    Write-Host "Done. $($dev.FriendlyName) is now bound to WinUSB."
} else {
    throw "Installed, but the device's driver service is '$service', not WinUSB -- check pnputil output above."
}
