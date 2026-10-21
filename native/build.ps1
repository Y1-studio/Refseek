param(
    [switch]$Package,
    [switch]$Offline,
    [string]$MinGwBin,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $MinGwBin) {
        $compiler = Get-Command gcc.exe -ErrorAction SilentlyContinue
        if ($compiler) { $MinGwBin = Split-Path -Parent $compiler.Source }
        elseif (Test-Path -LiteralPath 'C:\msys64\mingw64\bin\gcc.exe') {
            $MinGwBin = 'C:\msys64\mingw64\bin'
        }
    }
    if (-not $MinGwBin -or -not (Test-Path -LiteralPath (Join-Path $MinGwBin 'gcc.exe'))) {
        throw 'MinGW-w64 GCC is required. Run with -MinGwBin pointing to its bin folder. See docs/BUILDING.md.'
    }
    $env:CARGO_HOME = Join-Path $PSScriptRoot '.cargo'
    $env:CARGO_TARGET_DIR = Join-Path $PSScriptRoot 'target'
    $env:Path = $MinGwBin + ';' + $env:Path
    $cargoArgs = @('+stable-x86_64-pc-windows-gnu', 'build', '--release', '--locked')
    if ($Offline) { $cargoArgs += '--offline' }
    & cargo @cargoArgs
    if ($LASTEXITCODE -ne 0) { throw 'Rust build failed' }
    if ($Package) {
        & $Python tooling/package.py
        if ($LASTEXITCODE -ne 0) { throw 'Packaging failed; prepare the matching assets first.' }
    }
}
finally { Pop-Location }
