# Application resources

This source folder includes the pinned public model/runtime manifest. The actual models, runtime binaries, frozen style adapters, compatibility probes and diagnostic images are recovered from a matching native release or existing assets folder.

From the `native/` folder:

```powershell
python tooling/prepare-assets.py --from-exe "C:\Downloads\SemanticSearch-Native.exe"
```

Alternatively:

```powershell
python tooling/prepare-assets.py --from-directory "C:\ArtSearchBuild\assets"
```

The recovery checks every resource against `resource-lock.json` and keeps all resources inside this project's `assets/` directory. Add `--verify-only` to validate the source without copying files.

Only this README and `assets-manifest.json` are tracked here. Recovered resources are ignored by Git. See [the build guide](../../docs/NATIVE_BUILDING.md) for full instructions.
