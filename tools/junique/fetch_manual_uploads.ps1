# Copies the full-size pictures listed in MANUAL_UPLOADS.csv from the
# Storage Box (S:) to a folder in Downloads, named by title, ready to upload
# to Junique by hand. Only reads the Storage Box; never changes it.
#
#   powershell -ExecutionPolicy Bypass -File tools\junique\fetch_manual_uploads.ps1
param(
    [string]$Out = (Join-Path $env:USERPROFILE "Downloads\Junique first designs"),
    [string]$Root = "S:\"
)
$ErrorActionPreference = "Stop"
$list = Join-Path $PSScriptRoot "MANUAL_UPLOADS.csv"

if (-not (Test-Path $Root)) {
    Write-Host "The Storage Box (S:) is not connected." -ForegroundColor Yellow
    Write-Host "Open S: in File Explorer, type your Storage Box password, then run this again."
    exit 1
}

New-Item -ItemType Directory -Force $Out | Out-Null
$copied = 0; $missing = 0
foreach ($row in Import-Csv $list) {
    $src = Join-Path $Root ($row.storage_path -replace "/", "\")
    $dst = Join-Path $Out ($row.title + ".jpg")
    if (-not (Test-Path -LiteralPath $src)) {
        Write-Host "MISSING  $($row.title)  ($src)" -ForegroundColor Red
        $missing++
        continue
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $mb = [math]::Round((Get-Item -LiteralPath $dst).Length / 1MB, 1)
    Write-Host "OK       $($row.title)  ($mb MB)"
    $copied++
}
Write-Host ""
Write-Host "$copied copied, $missing missing. Folder: $Out"
if ($copied -gt 0) { Start-Process explorer.exe $Out }
