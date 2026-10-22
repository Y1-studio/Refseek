# Build the desktop app

The desktop edition uses Rust and egui on Windows x64. It starts its own llama.cpp process on a loopback address. Python prepares and packages resources during the build; the delivered app doesn't run Python.

## Requirements

- Windows 10 or 11, x64.
- Rust 1.92 or later with the `stable-x86_64-pc-windows-gnu` toolchain. The exported source was built with Rust 1.96.
- MinGW-w64 GCC and windres. The build script checks PATH and `C:\msys64\mingw64\bin`. Use `-MinGwBin` for another location.
- Python 3.11 or later for the standard-library resource and packaging scripts.
- A matching resource bundle from the desktop release.

Install the toolchains before building. The supplied scripts don't install system software. Cargo downloads the dependencies in `Cargo.lock`; use `-Offline` once the local cache is populated.

Run the commands below from `native/`.

## Recover resources

Download the matching [desktop EXE](https://github.com/Y1-studio/Refseek/releases/tag/v2.0.1), then recover its resources:

```powershell
python tooling/prepare-assets.py --from-exe "C:\Downloads\Refseek-Desktop.exe"
```

Or use an existing matching assets folder:

```powershell
python tooling/prepare-assets.py --from-directory "C:\ArtSearchBuild\assets"
```

The script copies resources into `assets/`, checks every file against `resource-lock.json`, and checks the embedded manifest for EXE inputs. It refuses to replace a different existing resource and excludes user libraries, settings and logs. Add `--verify-only` to check the source without copying it.

`assets/assets-manifest.json` records the public model/runtime URLs and hashes. Those downloads don't contain Refseek's trained adapters and probes, so they aren't a substitute for the complete matching bundle.

## Compile and run

```powershell
.\build.ps1
```

For another MinGW location:

```powershell
.\build.ps1 -MinGwBin "C:\Tools\mingw64\bin"
```

Cargo's cache and build output stay in `.cargo/` and `target/`. The checked-in Cargo configuration uses `gcc.exe` from PATH.

Run the development binary with explicit resource and home directories:

```powershell
$projectDir = (Get-Location).Path
& ".\target\x86_64-pc-windows-gnu\release\semantic-search-native.exe" --home "$projectDir" --assets "$projectDir\assets"
```

The internal Cargo binary keeps its original name. Its library lives under `data/library` in the selected home directory.

## Package one EXE

```powershell
.\build.ps1 -Package
```

To select another Python interpreter:

```powershell
.\build.ps1 -Package -Python "C:\Tools\Python312\python.exe"
```

`tooling/package.py` appends the verified resources to the native binary and writes `Refseek-Desktop.exe` and `build-manifest.json` here. Keep component notices with the recovered resources. The packager also collects available Rust dependency licenses from the local registry.

On first launch the EXE extracts resources beside itself and reuses them on later launches. It tries CUDA, Vulkan, then CPU, accepting a device only after text and image compatibility checks. On closing, it stops its own model process.

## Manual checks

Use a separate writable `--home` and explicit `--assets` when another instance is running. These diagnostics use a `verification/` library:

```powershell
.\Refseek-Desktop.exe --verify-math
.\Refseek-Desktop.exe --smoke --image-input-check
.\Refseek-Desktop.exe --gui-smoke --interaction-smoke --image-input-check
```

The checks cover adapter arithmetic, image transport, indexing, drag/drop, search during indexing, layout and preview browsing. `--storage-check` adds snapshot, reuse and cancellation checks. This snapshot uses manual diagnostics rather than an automated test or CI suite.

Small WebP files, including WebP bytes named `.jpg`, are decoded locally and sent as full-frame lossless PNG. The pinned server can't decode direct WebP without ffmpeg. Existing small JPEG/PNG transport stays unchanged; these image searches don't need an ffmpeg installation.

These checks measure engineering compatibility, not retrieval accuracy. Preserve model pins, trained weights, saved blends and probes when making unrelated changes.
