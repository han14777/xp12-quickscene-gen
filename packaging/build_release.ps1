# Build the Windows release zip: dist/QuickScene-<version>-windows-x64.zip
# Usage: powershell -ExecutionPolicy Bypass -File packaging/build_release.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
python -m PyInstaller --noconfirm --clean --distpath dist --workpath packaging/build packaging/quickscene.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = "dist/QuickScene"
Copy-Item README.md, README.zh-CN.md, LICENSE $app
Copy-Item packaging/START_HERE.txt $app

# Zipped with Python: Compress-Archive in Windows PowerShell 5.1 writes backslash paths that some unzip tools reject
$zip = "dist/QuickScene-$version-windows-x64.zip"
python -c "import shutil; shutil.make_archive('dist/QuickScene-$version-windows-x64', 'zip', 'dist', 'QuickScene')"
if ($LASTEXITCODE -ne 0) { throw "zip failed" }
Write-Host "Built $zip"
