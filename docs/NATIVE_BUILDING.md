# Build from source

The application is a Windows x64 native Rust/egui app. Its only inference process is a privately owned, loopback-only llama.cpp server. Python is used to prepare/package resources at build time; the delivered app does not invoke Python.

## Requirements

- Windows 10/11 x64.
- Rust's GNU Windows toolchain, named `stable-x86_64-pc-windows-gnu`. The project requires Rust 1.92 or later; the exported source was built with Rust 1.96.
- MinGW-w64 GCC. The build script detects `gcc.exe` on PATH or the usual `C:\msys64\mingw64\bin` location. Use `-MinGwBin` for a different installation.
- Python 3.11+ for the standard-library-only resource and packaging scripts.
- The matching runtime resource bundle, described below.

Toolchains must already be available. The supplied scripts do not install system software. Cargo fetches the versions in `Cargo.lock` on the first build; `-Offline` is available after the local cache is populated.

## Recover matching resources

Large resources and artwork fixtures are not included in Git. The application requires the exact models, runtime files, frozen style adapters and compatibility probes. Recover these from the matching downloadable EXE:

```powershell
python tooling/prepare-assets.py --from-exe "C:\Downloads\SemanticSearch-Native.exe"
```

Alternatively, use an existing matching assets folder:

```powershell
python tooling/prepare-assets.py --from-directory "C:\ArtSearchBuild\assets"
```

Run all native build/resource commands from the `native/` folder. Resources are copied into `assets/`. The script checks every file against `resource-lock.json`, checks the embedded bundle manifest for EXE inputs, and refuses to replace a different existing resource. It never copies application libraries, settings or logs.

To check a source bundle without copying anything, add `--verify-only`.

The pinned public model/runtime download URLs, revisions and hashes are also recorded in `assets/assets-manifest.json`. Those public downloads alone do not include this application's frozen style adapters and probes. A complete matching resource bundle is still required; the scripts do not substitute or retrain it.

## Compile

```powershell
.\build.ps1
```

With a custom MinGW installation:

```powershell
.\build.ps1 -MinGwBin "C:\Tools\mingw64\bin"
```

Cargo downloads/cache and output stay in this project's `.cargo/` and `target/` directories. The checked-in `.cargo/config.toml` uses `gcc.exe` from PATH instead of a developer-specific absolute compiler path.

To run the development binary from the `native/` folder:

```powershell
$projectDir = (Get-Location).Path
& ".\target\x86_64-pc-windows-gnu\release\semantic-search-native.exe" --home "$projectDir" --assets "$projectDir\assets"
```

Its library is created under this project's `data/library`. Source folders retain their original files.

## Make the portable single EXE

```powershell
.\build.ps1 -Package
```

For a different Python command or full interpreter path, use `-Python`:

```powershell
.\build.ps1 -Package -Python "C:\Tools\Python312\python.exe"
```

`tooling/package.py` appends the verified resource bundle to the native executable and writes `SemanticSearch-Native.exe` plus `build-manifest.json` in the `native/` folder. Keep the recovered notices with the resources; the packager also collects available Rust dependency licenses from the local registry.

The portable EXE extracts resources next to itself on first opening and reuses them on later launches. Its processing fallback is CUDA, then Vulkan, then CPU; real text/image compatibility probes must pass before a device is accepted. Only the app's own inference process is stopped when it closes.

## Manual verification

This snapshot has manual real-model diagnostics rather than an automated test/CI suite. With the portable EXE:

```powershell
.\SemanticSearch-Native.exe --verify-math
.\SemanticSearch-Native.exe --smoke --image-input-check
.\SemanticSearch-Native.exe --gui-smoke --interaction-smoke --image-input-check
```

Use a separate writable `--home` directory and explicit `--assets` when running diagnostics alongside another instance. Diagnostics use a separate `verification/` library. They check adapter arithmetic, image transport, indexing, drag/drop, concurrent search, layout and viewer navigation. `--storage-check` adds snapshot/reuse/cancellation checks.

Small WebP inputs, including WebP data named `.jpg`, are decoded locally and sent as lossless full-frame PNG. The pinned standalone server cannot decode the direct WebP input without ffmpeg. Existing small JPEG/PNG transport bytes stay unchanged. No ffmpeg installation is required for these image searches.

These checks establish engineering compatibility, not fresh retrieval accuracy. Keep the original adapter weights, saved blends, probes and model pins intact.
