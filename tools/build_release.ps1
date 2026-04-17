param(
    [string]$OutputRoot,
    [string]$CompressonatorRoot
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repoRoot "dist"
}

$initPath = Join-Path $repoRoot "__init__.py"
$initContent = Get-Content -Raw $initPath
$versionMatch = [regex]::Match($initContent, '"version"\s*:\s*\(([^)]+)\)')
if (-not $versionMatch.Success) {
    throw "Could not read version from $initPath"
}

$version = ($versionMatch.Groups[1].Value -split ",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" } | ForEach-Object { $_ -replace '[^0-9]', '' }
$versionString = ($version -join ".")
$releaseName = "OOTP_ParkForge_$versionString"

$distRoot = Join-Path $OutputRoot $releaseName
$addonStage = Join-Path $distRoot "addon"
$docsStage = Join-Path $distRoot "docs"
$licensesStage = Join-Path $distRoot "licenses"
$runtimeStage = Join-Path $distRoot "runtime"
$addonZip = Join-Path $addonStage "ootp_parkforge_addon.zip"
$installZip = Join-Path $OutputRoot ($releaseName + ".zip")
$bundleZip = Join-Path $OutputRoot ($releaseName + "_distribution.zip")

if (Test-Path $distRoot) {
    Remove-Item -Recurse -Force $distRoot
}

New-Item -ItemType Directory -Force -Path $addonStage, $docsStage, $licensesStage, $runtimeStage | Out-Null

$tempZipRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("parkforge_addon_" + [guid]::NewGuid().ToString("N"))
$packageStage = Join-Path $tempZipRoot "io_scene_ootp_pod"
New-Item -ItemType Directory -Force -Path $packageStage | Out-Null

$excludeNames = @(
    "dist",
    "build",
    ".artifacts",
    "__pycache__"
)

Get-ChildItem -Force $repoRoot | Where-Object {
    $excludeNames -notcontains $_.Name
} | ForEach-Object {
    Copy-Item $_.FullName -Destination $packageStage -Recurse -Force
}

Compress-Archive -Path (Join-Path $tempZipRoot "io_scene_ootp_pod") -DestinationPath $addonZip -Force
if (Test-Path $installZip) {
    Remove-Item -Force $installZip
}
Compress-Archive -Path (Join-Path $tempZipRoot "io_scene_ootp_pod") -DestinationPath $installZip -Force

Copy-Item (Join-Path $repoRoot "README.md") (Join-Path $distRoot "README.md") -Force
Copy-Item (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") (Join-Path $licensesStage "THIRD_PARTY_NOTICES.md") -Force
Copy-Item (Join-Path $repoRoot "docs\\*") $docsStage -Recurse -Force

if (-not $CompressonatorRoot) {
    $CompressonatorRoot = $env:OOTP_POD_COMPRESSONATOR_ROOT
}

if ($CompressonatorRoot -and (Test-Path $CompressonatorRoot)) {
    $runtimeTarget = Join-Path $runtimeStage "compressonatorcli"
    New-Item -ItemType Directory -Force -Path $runtimeTarget | Out-Null
    Copy-Item $CompressonatorRoot $runtimeTarget -Recurse -Force
}

$hashLines = @()
Get-ChildItem -Recurse -File $distRoot | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    $relative = $_.FullName.Substring($distRoot.Length).TrimStart('\').Replace('\', '/')
    $hashLines += "$hash *$relative"
}
$hashLines | Set-Content (Join-Path $distRoot "SHA256SUMS.txt")

if (Test-Path $bundleZip) {
    Remove-Item -Force $bundleZip
}
Compress-Archive -Path $distRoot -DestinationPath $bundleZip -Force

Remove-Item -Recurse -Force $tempZipRoot

Write-Host "Built release folder: $distRoot"
Write-Host "Built install zip:    $installZip"
Write-Host "Built bundle zip:     $bundleZip"
