param(
    [string] $StageRoot = "",
    [string] $PackageDir = "",
    [switch] $PlanOnly
)

# PyInstaller and ISCC can write progress to stderr, so check their exit codes.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found at $py" }
$versionMatch = Select-String -Path "app\constants.py" -Pattern 'APP_VERSION\s*=\s*"([^"]+)"'
if (-not $versionMatch) { throw "APP_VERSION was not found" }
$version = $versionMatch.Matches[0].Groups[1].Value
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $root "tmp\release-$version"))
if (-not $StageRoot) { $StageRoot = Join-Path $releaseRoot "windows-build" }
if (-not [IO.Path]::IsPathRooted($StageRoot)) { $StageRoot = Join-Path $root $StageRoot }
$stage = [IO.Path]::GetFullPath($StageRoot)
if (-not $stage.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "StageRoot must be a child of $releaseRoot"
}
if (-not $PackageDir) { $PackageDir = Join-Path $stage "packages" }
if (-not [IO.Path]::IsPathRooted($PackageDir)) { $PackageDir = Join-Path $root $PackageDir }
$packageOut = [IO.Path]::GetFullPath($PackageDir)
$installerExe = Join-Path $packageOut "BulkSeqStudio-Setup-$version.exe"
$portableZip = Join-Path $packageOut "BulkSeqStudio-Portable-$version.zip"
if (Test-Path -LiteralPath $stage) { throw "Stage already exists; choose a new StageRoot: $stage" }
foreach ($path in @($installerExe, $portableZip)) {
    if (Test-Path -LiteralPath $path) { throw "Package already exists; preserving it: $path" }
}

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")) {
        if (Test-Path -LiteralPath $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) { throw "ISCC.exe (Inno Setup 6) not found" }
& $py -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is unavailable in .venv" }
if ($PlanOnly) {
    Write-Host "Version: $version"
    Write-Host "Stage: $stage"
    Write-Host "Packages: $packageOut"
    Write-Host "Inno: $iscc"
    return
}

New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Path $packageOut -Force -ErrorAction Stop | Out-Null
$distPath = Join-Path $stage "dist"
$workPath = Join-Path $stage "build"
Write-Host "[1/4] Building executable in isolated staging..."
$basePython = (& $py -c 'import sys; print(sys.base_prefix)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $basePython)) { throw "Python base prefix is unavailable" }
$buildPath = (@((Split-Path -Parent $py), $basePython, "$env:SystemRoot\System32",
                $env:SystemRoot, "$env:SystemRoot\System32\Wbem") |
              Where-Object { Test-Path -LiteralPath $_ }) -join ';'
$priorPath = $env:PATH
try {
    # Keep unrelated native toolchains on the host PATH out of the frozen DLL search.
    $env:PATH = $buildPath
    Push-Location $stage
    & $py -m PyInstaller --distpath $distPath --workpath $workPath (Join-Path $root "packaging\BulkSeqStudio.spec") --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
} finally {
    if ((Get-Location).Path -eq $stage) { Pop-Location }
    $env:PATH = $priorPath
}

Write-Host "[2/4] Running the frozen QtWebEngine self-test..."
$onedir = Join-Path $distPath "BulkSeq Studio"
$bundleCheck = Join-Path $root "packaging\verify_bundle.py"
& $py $bundleCheck $onedir
if ($LASTEXITCODE -ne 0) { throw "Bundled runtime script inventory is invalid" }
$frozenExe = Join-Path $onedir "BulkSeqStudio.exe"
if (-not (Test-Path -LiteralPath $frozenExe)) { throw "Frozen executable is missing: $frozenExe" }
$selftestOut = Join-Path $stage "selftest.json"
$vendorMatch = Select-String -Path "app\assets\web\ppi\VENDORED.md" -Pattern '^\| cytoscape\.min\.js \| cytoscape \| ([0-9.]+) \|$'
if (-not $vendorMatch) { throw "Vendored Cytoscape version was not found" }
$vendorVersion = $vendorMatch.Matches[0].Groups[1].Value
$priorSelfTest = $env:BULKSEQ_SELFTEST
$priorSkipDialog = $env:BULKSEQ_SKIP_READINESS_DIALOG
$priorSelfTestOut = $env:BULKSEQ_SELFTEST_OUT
$env:BULKSEQ_SELFTEST = "1"
$env:BULKSEQ_SKIP_READINESS_DIALOG = "1"
$env:BULKSEQ_SELFTEST_OUT = $selftestOut
try {
    $selftest = Start-Process -FilePath $frozenExe -PassThru -WindowStyle Hidden
    if (-not $selftest.WaitForExit(90000)) {
        Stop-Process -Id $selftest.Id -Force -ErrorAction SilentlyContinue
        throw "Frozen self-test timed out after 90 seconds"
    }
    $selftest.Refresh()
    if ($selftest.ExitCode -ne 0) { throw "Frozen self-test exited $($selftest.ExitCode)" }
    if (-not (Test-Path $selftestOut)) { throw "Frozen self-test did not write $selftestOut" }
    $selftestResult = Get-Content -Raw -LiteralPath $selftestOut | ConvertFrom-Json
    if (-not $selftestResult.pass -or -not $selftestResult.webengine -or $selftestResult.nodes -ne 3 -or
        $selftestResult.version -ne $vendorVersion) {
        throw "Frozen self-test failed: $(Get-Content -Raw -LiteralPath $selftestOut)"
    }
} finally {
    $env:BULKSEQ_SELFTEST = $priorSelfTest
    $env:BULKSEQ_SKIP_READINESS_DIALOG = $priorSkipDialog
    $env:BULKSEQ_SELFTEST_OUT = $priorSelfTestOut
}

Write-Host "[3/4] Building installer with Inno Setup..."
& $iscc "/DMyAppVersion=$version" "/DBuildSource=$onedir" "/DPackageDir=$packageOut" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
if (-not (Test-Path -LiteralPath $installerExe)) { throw "Installer not produced at $installerExe" }

Write-Host "[4/4] Creating portable ZIP..."
Start-Sleep -Seconds 8
$zipped = $false
foreach ($attempt in 1..8) {
    $candidate = Join-Path $stage "portable-attempt-$attempt.zip"
    try {
        Compress-Archive -Path $onedir -DestinationPath $candidate -CompressionLevel Optimal -ErrorAction Stop
        Move-Item -LiteralPath $candidate -Destination $portableZip -ErrorAction Stop
        $zipped = $true
        break
    } catch {
        if (Test-Path -LiteralPath $portableZip) { throw "Portable ZIP already exists; review it before retrying" }
        Start-Sleep -Seconds 10
    }
}
if (-not $zipped) { throw "Portable ZIP creation failed after retries (a dist/ file stayed locked)." }

Write-Host ""
Write-Host "Done."
Write-Host "  Executable:   $frozenExe"
Write-Host "  Installer:    $installerExe"
Write-Host "  Portable ZIP: $portableZip"
