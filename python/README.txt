SEMANTIC SEARCH v2 — Windows x64 portable application

Double-click SemanticSearch-v2.exe. Python and a separate model server are not
needed. Everything runs locally. The launcher opens the search page in your
default browser. Keep the launcher open; Stop and exit saves completed indexing
work and stops its own model server.
In an image preview, Left/Up and Right/Down move between images, including
across result pages. The viewer keeps a fixed frame; image proportions do not
resize it or scroll the background page. Esc closes the preview.

Automatic selection: NVIDIA CUDA, then Vulkan graphics, then CPU. Each candidate
must load the model and return valid text AND image embeddings before it is used.
The exact pinned b11741 CUDA 12.4 build, its CUDA runtime, matching Vulkan/CPU
builds, and the requested Unsloth Q8_0 model/projector are bundled.
Graphics drivers must already be installed. The application does not install
drivers or system packages. Driver/model failures fall back to another backend.

The single EXE extracts its bundled files into a temporary directory on launch;
allow several GB of free space. First GPU startup can take longer for shader or
kernel preparation. The EXE is unsigned. No installer is required.

Your indexes, folder catalog and logs are stored in SemanticSearch-v2-data beside
the EXE. Put it in a writable folder. Copy that data folder too when moving your
library. Original images stay where they are; moving them requires a rescan.
Open Library & indexing to select folders. Each scanned folder has its own index
and vectors. v1.9 and its library are left intact. v2 starts with a fresh library
because it uses the requested Q8 model instead of the previous BF16 model.

Style search includes the existing current and contrastive adapters. Contrastive
is the default, the strongest performer in the prior artist benchmarks.
Other aspects use the existing baseline. A manual method selection is remembered
for its aspect while the page stays open. Saved adapter blends are unchanged.
The adapter weights were trained on BF16 embeddings. The exact Q8 model/projector hashes are
verified at startup. Behavioral probes compare against the training BF16 vectors
with an explicit Q8 transfer tolerance (cosine >=0.9995; external servers keep
>=0.9999). Measured image agreement was 0.99979-0.99989 across four backend/device
combinations. This compatibility check does not prove unchanged retrieval quality. Fresh evaluation of the quantized transfer remains necessary. Legacy
BF16 research projection methods are hidden for this bundled Q8 runtime.
Subject, pose, composition, viewpoint, color and lighting use current-model
calibration. Pose remains embedding-based, without a joint detector.

Useful command-line overrides:
  SemanticSearch-v2.exe --backend cpu
  SemanticSearch-v2.exe --backend vulkan
  SemanticSearch-v2.exe --backend cuda --device CUDA1
  SemanticSearch-v2.exe --data-dir "D:\SearchData"
  SemanticSearch-v2.exe --diagnose --backend auto
Diagnostics write diagnostics.json beside the indexes and exit. Backend logs
explain failed candidates. The model server and GUI bind only to 127.0.0.1.

Rebuild from source (Python 3.12+, NumPy, Pillow, PyInstaller):
  python fetch_assets.py
  python -m PyInstaller --noconfirm --clean --workpath build-work/pyinstaller --distpath dist SemanticSearch-v2.spec
All external download URLs, versions, sizes and SHA256 hashes are pinned in
assets-manifest.json. Licenses and model source notices are embedded in the EXE.
