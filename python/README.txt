REFSEEK BROWSER 2.0.1
Windows x64 runtime notes

For setup and search controls, read docs/PYTHON_GUIDE.md in the repository.
The source build instructions are in python/README.md. The ready-made EXE
includes Python, the model and its runtime files.

LAUNCH AND STORAGE

Double-click Refseek-Browser.exe. Keep the launcher open while using its
browser page. Stop and exit saves completed indexing work and stops the
app's model server. The server and browser interface bind to 127.0.0.1.

The EXE extracts bundled files into a temporary directory on each launch.
Allow several gigabytes of free space. The EXE is unsigned and has no
installer. Graphics drivers must already be installed.

New catalogues use Refseek-Browser-data beside the EXE. An existing
SemanticSearch-v2-data folder is reused when the new folder is absent.
Keep that folder when updating or moving the app. Keep source pictures
available too; after moving them, add and index their new location.
Each scanned root has a separate index and vector snapshot.

MODEL AND BACKENDS

The bundle contains llama.cpp b11741, CUDA 12.4 runtime files, matching
Vulkan/CPU builds, and the pinned Unsloth EmbeddingGemma 2 Q8_0 model and
projector. assets-manifest.json records download URLs, versions and hashes.

Auto tries compatible NVIDIA CUDA devices, then Vulkan, then CPU. Each
candidate must pass text and image embedding checks. Failed attempts are
recorded in the backend logs.

STYLE AND COMPATIBILITY

Contrastive is the default Style method. Current style adapter is also
available. Other aspects use their existing baseline. Manual choices stay
selected for their aspect while the page is open.

The style adapters were trained on BF16 embeddings. Startup verifies the
Q8 model/projector hashes and compares live probe embeddings to the frozen
BF16 vectors. The bundled runtime requires cosine agreement >=0.9995;
external servers retain >=0.9999. Recorded image agreement across four
backend/device combinations was 0.99979-0.99989.

Those probes check compatibility, not search accuracy. The full-data
adapters and their Q8 transfer need evaluation on new collections. Legacy
BF16 research projections are hidden for this Q8 runtime. Pose uses
embeddings without a joint detector.

COMMAND-LINE OPTIONS

  Refseek-Browser.exe --backend cpu
  Refseek-Browser.exe --backend vulkan
  Refseek-Browser.exe --backend cuda --device CUDA1
  Refseek-Browser.exe --data-dir "D:\SearchData"
  Refseek-Browser.exe --diagnose --backend auto

Diagnostics write diagnostics.json in the selected data directory and exit.
Backend logs record why a candidate failed.

BUILDING

Follow python/README.md to install build dependencies and recover the
complete resources from a matching release. Then run:

  .\build.ps1 -Python .\.venv\Scripts\python.exe

fetch_assets.py alone doesn't recover the trained adapter files or probes.
The build includes component licenses and model source notices in the EXE.
