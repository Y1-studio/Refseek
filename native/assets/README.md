# Application resources

Git stores `assets-manifest.json` here. Recover the model, runtime binaries, trained style adapters, probes and diagnostic images from a matching desktop release or assets folder.

From `native/`:

```powershell
python tooling/prepare-assets.py --from-exe "C:\Downloads\Refseek-Desktop.exe"
```

Or recover from a folder:

```powershell
python tooling/prepare-assets.py --from-directory "C:\ArtSearchBuild\assets"
```

The script checks every resource against `resource-lock.json` and copies it under `assets/`. Recovered files are ignored by Git. Add `--verify-only` to check the source without copying it.

See the [native build instructions](../../docs/NATIVE_BUILDING.md) for requirements, compilation and packaging.
