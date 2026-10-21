# Refseek Browser

This edition opens a local search page in your default browser. Its small launcher window owns the model server: leave it open while searching, then use **Stop and exit**.

## Use the ready-made Windows app

Download **Refseek-Browser.exe** from this repository's Releases. You do not need to install Python or start a separate server. Put it in its own writable folder, double-click it, and wait for the search page to open. Choose **Library & indexing**, add an image folder, then search with words or a reference image. Style starts with the Contrastive adapter.

The EXE extracts its resources to a temporary directory each time it opens; allow several gigabytes of free space. It tries CUDA, Vulkan and CPU processing. Graphics drivers must already be installed.

New installations save settings, indexes and logs in **Refseek-Browser-data** beside the EXE. When an existing **SemanticSearch-v2-data** folder is present and the new folder is absent, Refseek reuses the existing folder automatically. Keep that folder when replacing the EXE. The native edition uses its own data folder; keep the two downloads in separate application folders.

Read the [browser guide](../docs/PYTHON_GUIDE.md) or [technical runtime notes](README.txt). The [native artist's guide](../docs/USER_GUIDE.md) illustrates shared search ideas, but its screenshots and exact control layout belong to the Rust edition.

## Run from source on Windows

Use Python **3.12 or later**, including Tcl/Tk. Run the following from this `python/` folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tooling/prepare-assets.py --from-exe "C:\Downloads\Refseek-Browser.exe"
.\.venv\Scripts\python.exe launcher.py
```

Resource recovery copies only the exact application resources listed in `resource-lock.json`; it never imports a user's library, settings or logs. Existing files with different hashes are preserved and reported as errors. The matching release contains the frozen style weights and compatibility probes, which model downloads alone do not provide. Alternatively recover from a matching unpacked v2 application folder using `--from-directory`.

The pinned model/runtime URLs and hashes are recorded in `assets-manifest.json`. `fetch_assets.py` downloads those public files, but does not supply the frozen application-specific style resources. It is not a substitute for recovering the complete bundle.

## Build the Windows EXE

After installing the build dependencies and recovering resources:

```powershell
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

The resulting application is `dist/Refseek-Browser.exe`. The source entry point `run.py` also provides the CLI and web server for developers who manage their own embedding API. Global options go before the subcommand; use `run.py --help` for details. The bundled desktop launcher and packaged release in this snapshot target Windows x64; no Linux AppImage is included.

## Check a build manually

Use a separate diagnostic data folder:

```powershell
.\dist\Refseek-Browser.exe --diagnose --data-dir .\verification\python-diagnostic
```

This checks model startup and embedding compatibility. It does not establish retrieval accuracy. Manually check indexing, text/image searches, Style, negative images, and fixed-frame arrow-key preview navigation before distributing a changed build.

Third-party notices are [here](../THIRD_PARTY_NOTICES.md). No project-wide source license has been selected.
