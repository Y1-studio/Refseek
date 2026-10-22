# Refseek Browser source

This folder contains the Python edition. Its launcher starts the bundled model server and opens a local page in the default browser. Closing the launcher with **Stop and exit** saves completed indexing work and stops its model process.

For the Windows download, use [Refseek-Browser.exe](https://github.com/Y1-studio/Refseek/releases/latest/download/Refseek-Browser.exe) and follow the [browser guide](../docs/PYTHON_GUIDE.md). It includes Python and the model.

## Run from source on Windows

Use Python 3.12 or later with Tcl/Tk. From this `python/` folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tooling/prepare-assets.py --from-exe "C:\Downloads\Refseek-Browser.exe"
.\.venv\Scripts\python.exe launcher.py
```

Resource recovery checks files against `resource-lock.json`. It copies application resources, including trained style weights and compatibility probes, and excludes user libraries, settings and logs. It reports an error instead of replacing an existing file with a different hash. Add `--verify-only` to check the source bundle without copying it.

To recover from a matching unpacked application folder, use `--from-directory` instead of `--from-exe`. `assets-manifest.json` records the public model/runtime URLs and hashes. `fetch_assets.py` downloads those public files only; the complete release bundle is needed for the application-specific style resources.

## Build the EXE

After installing dependencies and recovering the resources:

```powershell
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

The output is `dist/Refseek-Browser.exe`. The source spec retains its older filename, `SemanticSearch-v2.spec`, but produces the Refseek-named EXE. This launcher and package target Windows x64; this release has no Linux AppImage.

The separate `run.py` entry point provides a CLI and web server for developers managing their own embedding API. Global options go before the subcommand. Use `run.py --help` for the available commands.

## Check a build manually

Use a separate data folder:

```powershell
.\dist\Refseek-Browser.exe --diagnose --data-dir .\verification\python-diagnostic
```

This checks model startup and text/image embedding compatibility, then writes `diagnostics.json` and exits. Before distributing a changed build, check indexing, text and image search, Style, negative images, and arrow-key preview browsing in the app. Startup probes don't measure retrieval accuracy.

[Runtime flags and model notes](README.txt) · [Third-party notices](../THIRD_PARTY_NOTICES.md)

No project-wide source license has been selected.
