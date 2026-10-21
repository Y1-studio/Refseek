# Third-party components

This repository's source does not include the large runtime resource bundle. Matching application resources can be recovered as described in [BUILDING.md](docs/NATIVE_BUILDING.md); the recovery preserves the original notices and hashes.

- **llama.cpp / ggml b11741:** MIT. See [the retained license](third-party/llama.cpp-MIT.txt).
- **EmbeddingGemma 2 Q8_0 and vision projector:** the pinned Unsloth conversion declares Apache-2.0. See [the model card](third-party/model-README.md), [Apache-2.0](third-party/Apache-2.0.txt), and the exact revision/download hashes in `native/assets/assets-manifest.json`.
- **CUDA runtime / cuBLAS 12.4:** NVIDIA components from the pinned llama.cpp runtime distribution. Their distribution terms and notices accompany that runtime.
- **Vulkan / Microsoft app-local runtime components:** retain the original notices in the recovered resource bundle. Graphics drivers are supplied by their vendors.
- **Rust dependencies:** versions and registry checksums are in `native/Cargo.lock`. Their individual licenses accompany the packaged application; the packager collects license files from available registry sources.
- **Rust / MinGW toolchain components:** relevant notices are retained in the recovered bundle's `assets/licenses/` directories.
- **Frozen style adapters:** the application's existing exported profiles, including their deployment freeze, fitting provenance, numerical blends and compatibility probes. These resources are recovered separately; training images and caches are not part of this repository.

The original resource bundle also retains inherited build-time Python-related notices. The native application itself does not run Python; Python 3.11+ is used by the preparation/packaging scripts.

Research/sample images are not included in this source snapshot. Recovering an existing application's bundle preserves that bundle's original resources and notices; it does not change their ownership or terms.

The application's own project-wide source license has not been selected. The third-party license files here apply to the named components, not automatically to this entire project.

## Python/browser edition

The Python edition embeds CPython, NumPy, Pillow, Tcl/Tk and the PyInstaller bootloader. Their notices are retained in the downloadable Python application and the recoverable `python/licenses/` resources. Versions for a source build are specified in `python/requirements*.txt`. The Python app uses the same pinned model/runtime family; its complete resource lock is `python/resource-lock.json`. Neither source folder includes training images or personal libraries.
