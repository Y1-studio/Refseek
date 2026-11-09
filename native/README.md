# Refseek Desktop 2.2.0

This is the Rust source for the Windows desktop app. It searches local folders with text and reference images and displays results in its own window. The delivered app runs without Python or a browser.

To use the app, download [Refseek-Desktop.exe](https://github.com/Y1-studio/Refseek/releases/latest/download/Refseek-Desktop.exe) and follow the [artist's guide](../docs/USER_GUIDE.md). The [main README](../README.md) compares the desktop and browser editions and shows screenshots.

## Build from source

Follow the [native build instructions](../docs/NATIVE_BUILDING.md). You need the GNU Windows Rust toolchain, MinGW-w64, Python for packaging, and the matching resource bundle recovered from a release. The build writes `Refseek-Desktop.exe` in this folder.

The source excludes model weights, trained adapter files, dependency caches and personal libraries. `Cargo.lock` records Rust dependency versions; `resource-lock.json` records the required application resources.

## Runtime and data

The app starts its own llama.cpp process on a loopback address. It tries CUDA, Vulkan, then CPU and checks text and image embeddings before accepting a device. Closing Refseek stops that process.

Settings and working resources live in `data` beside the EXE; the index lives in `data/library`. Each scanned root has a separate index and vector snapshot. Keep `data` during updates, and use **Update this folder** in Library after changing a source folder.

Desktop 2.2.0 defaults new libraries to 768-dimensional indexes. Settings offers 128, 256, 512 and 768 before a library contains indexed files; existing libraries keep the dimension recorded on disk. Smaller indexes also retain a full 768-dimensional vector cache for the trained Style methods. **Include / Skip** controls in Refine filter folders and subfolders without changing the index. Use **Library > Clean up old snapshots** to remove old checkpoints that neither the active nor recovery catalog references.

Style defaults to the Contrastive style adapter. The saved weights and blends are fixed; switching search methods doesn't train a model or rebuild the library. Pose uses an embedding projection without a joint detector. See the [overview](../README.md#start-searching) for the limits of the style and pose methods.

[Third-party notices](../THIRD_PARTY_NOTICES.md) describe the bundled components. The project's own source license hasn't been selected.

If startup reports a JSON error, the message names the file or inference endpoint. Errors shown in the main window are saved to `data/startup-error.json`. Close Refseek and run `Refseek-Desktop.exe --check-library` to verify the saved library without model startup or reindexing; the result is written to `library-check.json` beside the EXE.

When the active library cannot load, the report also checks the previous catalog without activating it. Cleanup refuses to delete files when a protected index has unreadable JSON or fails validation.

## Indexing in 2.2

Normal updates reuse saved file metadata before opening image contents. **Verify file contents** also hashes files and detects edits hidden by unchanged sizes and timestamps. Indexing defaults to images and two CPU preparation workers; file types and custom worker counts are remembered. Choose the worker count under **Settings > Local model**. The queue is bounded, and image preparation overlaps ordered embedding work.

Failed or cancelled changed files retain their previous vectors until a whole replacement succeeds. Unchanged updates preserve both catalogs. See the [release notes](../docs/RELEASE_NOTES.md) for the recovery fixes and manual verification coverage.
