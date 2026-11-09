REFSEEK BROWSER 2.2.0 — Windows x64 portable application

Double-click Refseek-Browser.exe. Choose your access and tunnel settings, then
click Start. Python and a separate model server are not
needed. Everything runs locally. The launcher opens the search page in your
default browser. Keep the launcher open; Stop and exit saves completed indexing
work and stops its own model server.
2.2.0 indexing updates: new libraries start with images selected and two CPU
preparation workers. File-type choices and the worker count are remembered.
Settings lets you enter any worker count up to the available CPU threads.
A bounded preparation queue overlaps two independent embedding requests instead
of preparing the entire library in memory. Text chunks retain their batch size.
Unchanged updates reuse saved metadata and do not rewrite unchanged catalogs.
Verify file contents reads and hashes every file; the first verification of
legacy records without hashes can re-embed them. Changed or failed files keep
their previous vectors until a full replacement is available. Incomplete scans
cannot publish removals. Cancel interrupts network waits and stops queued work;
an active decode may finish first. Documents exceeding the chunk limit are
reported instead of silently truncated. Failed saves preserve recovery, and a
recovery-write failure keeps older snapshots and reports a warning.
Cancel and Stop also interrupt the model-readiness probes during startup.
The frozen model pair, adapter weights and Python image resizing are unchanged.

In an image preview, Left/Up and Right/Down move between images, including
across result pages. The viewer keeps a fixed frame; image proportions do not
resize it or scroll the background page. Esc closes the preview.
Use negative in the preview to down-rank similar images. Search this folder
only reruns your search in the picture's immediate folder. Search all folders
clears the scope and runs it across the library. Expand indexed roots in the
folder tree for Include and Skip rules on their subfolders.

Automatic selection: NVIDIA CUDA, then Vulkan graphics, then CPU. Each candidate
must load the model and return valid text AND image embeddings before it is used.
The exact pinned b11741 CUDA 12.4 build, its CUDA runtime, matching Vulkan/CPU
builds, and the requested Unsloth Q8_0 model/projector are bundled.
Graphics drivers must already be installed. The application does not install
drivers or system packages. Driver/model failures fall back to another backend.

The single EXE extracts its bundled files into a temporary directory on launch;
allow several GB of free space. First GPU startup can take longer for shader or
kernel preparation. The EXE is unsigned. No installer is required.

Put Refseek-Browser.exe and Refseek-Desktop.exe next to the same data folder.
Browser stores its library and accounts in data/browser. Desktop keeps its own
index in data/library. The catalogues remain independent.
With the old Browser closed, the new launcher moves Refseek-Browser-data or
SemanticSearch-v2-data to data/browser on first run if that location is empty.
It moves the Browser .env to data/.env. This keeps the existing index.
Open Library & indexing to select folders. Each scanned folder has its own index
and vectors. Library shows the stored index size, indexed originals, and the
latest scan report. Clean up old snapshots preserves the active index and one
recovery copy. Changed indexes also prune older snapshots automatically.

Settings offers UI scale, text size, theme, card size, and result display
options. A new library defaults to 768 dimensions. You can choose a smaller
search dimension before adding files. Existing libraries keep their dimension.
Smaller search dimensions retain full model vectors for the trained Style
methods. Settings estimates vector storage for 10,000 vectors.

Normal mode gives full access on this computer without signing in. To share
specific indexed folders, click Use Account mode in the launcher, set up an
owner password if prompted, then click Start. Open Manage users to add or delete
guests, change passwords, and choose their folders. The local launcher opens
account management directly; it treats the person using this computer as the
administrator. Browser access in Account mode still requires signing in.
Select a user, check Administrator, and click Save changes to give full access
to all folders, indexing, settings, and browser user management. Clear it to
restore guest access to selected folders. Role changes sign the user out.
Keep at least one administrator; the app prevents deleting or demoting the last.
To edit an existing guest, select their name in Manage users and click Edit
selected user. Add indexed folders with Allow selected folders, or choose a
folder in the allowed list and click Remove selected access. Save changes applies
the edits. Clear folder access removes all grants; after saving, that guest can
sign in but cannot search or open library files. Leave Password blank to keep it,
or enter a new password before saving. Access and password changes sign them out.
Choose Normal mode before Start to turn browser sign-in off again. Changes
made after the server is running apply when you restart Refseek. Guests can search and
open files in their assigned folders and subfolders, and adjust their browser
appearance and result preferences. Indexing, server details, library settings,
and account management are owner-only.
The shared data/.env holds the private REFSEEK_SESSION_KEY. Password hashes and
folder grants live in data/browser/accounts.sqlite3. Keep both files when moving.
Saved accounts are kept in Normal mode. Network sharing requires Account mode and HTTPS. The browser
guide lists the .env settings for a LAN address and certificate.

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
  Refseek-Browser.exe --start
  Refseek-Browser.exe --backend cpu
  Refseek-Browser.exe --backend vulkan
  Refseek-Browser.exe --backend cuda --device CUDA1
  Refseek-Browser.exe --data-dir "D:\SearchData"
  Refseek-Browser.exe --diagnose --backend auto
Diagnostics write diagnostics.json beside the indexes and exit. Backend logs
explain failed candidates. The model server and GUI bind only to 127.0.0.1.

Rebuild from source (Python 3.12+, NumPy, Pillow, PyInstaller):
  python fetch_assets.py
  python -m PyInstaller --noconfirm --clean --workpath build-work/pyinstaller --distpath dist SemanticSearch-v2.spec
All external download URLs, versions, sizes and SHA256 hashes are pinned in
assets-manifest.json. Licenses and model source notices are embedded in the EXE.
