# Refseek Desktop

This is the Rust source for the Windows desktop app. It searches local folders with text and reference images and displays results in its own window. The delivered app runs without Python or a browser.

To use the app, download [Refseek-Desktop.exe](https://github.com/Y1-studio/Refseek/releases/latest/download/Refseek-Desktop.exe) and follow the [artist's guide](../docs/USER_GUIDE.md). The [main README](../README.md) compares the desktop and browser editions and shows screenshots.

## Build from source

Follow the [native build instructions](../docs/NATIVE_BUILDING.md). You need the GNU Windows Rust toolchain, MinGW-w64, Python for packaging, and the matching resource bundle recovered from a release. The build writes `Refseek-Desktop.exe` in this folder.

The source excludes model weights, trained adapter files, dependency caches and personal libraries. `Cargo.lock` records Rust dependency versions; `resource-lock.json` records the required application resources.

## Runtime and data

The app starts its own llama.cpp process on a loopback address. It tries CUDA, Vulkan, then CPU and checks text and image embeddings before accepting a device. Closing Refseek stops that process.

Settings and indexes live in `data` beside the EXE. Each scanned root has a separate index and vector snapshot. Keep `data` during updates, and use **Update this folder** in Library after changing a source folder.

Style defaults to the Contrastive style adapter. The saved weights and blends are fixed; switching search methods doesn't train a model or rebuild the library. Pose uses an embedding projection without a joint detector. See the [overview](../README.md#start-searching) for the limits of the style and pose methods.

[Third-party notices](../THIRD_PARTY_NOTICES.md) describe the bundled components. The project's own source license hasn't been selected.
