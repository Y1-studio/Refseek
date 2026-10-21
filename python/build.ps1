param([string]$Python = "python")
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python tooling/prepare-assets.py --verify-local
    if ($LASTEXITCODE -ne 0) { throw 'Recover the matching resources first; see README.md.' }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build-work/pyinstaller-cache'
    & $Python -m PyInstaller --noconfirm --workpath build-work/pyinstaller --distpath dist SemanticSearch-v2.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'dist/SemanticSearch-v2.exe') -Algorithm SHA256 | Format-List
} finally { Pop-Location }
