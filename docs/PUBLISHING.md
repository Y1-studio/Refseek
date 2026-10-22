# Publish source and downloads

The repository is [Y1-studio/Refseek](https://github.com/Y1-studio/Refseek). The current Windows release is [v2.0.1](https://github.com/Y1-studio/Refseek/releases/tag/v2.0.1).

## Repository contents

Put `README.md`, `native/`, `python/`, `docs/`, `third-party/` and `THIRD_PARTY_NOTICES.md` at the repository root. Upload their contents with those paths preserved. Keep the sibling `github-release/` folder out of Git.

The source export excludes personal libraries, logs, credentials, training datasets and model binaries. Review additions before uploading. No project-wide source license has been selected; third-party notices apply to their named components.

## Release assets

For a new app version, create a release with its version tag and prepared release notes. Attach these files from `github-release/`:

- `Refseek-Desktop.exe`
- `Refseek-Browser.exe`
- `README.txt`
- `SHA256SUMS.txt`

Keep the EXEs in Release Assets, outside Git history. The main README links to the latest release using those asset names. Preserve the filenames or update the links.

After publishing, open the main README and confirm that the guides and five screenshots load. Download both EXEs and compare their SHA256 hashes with `SHA256SUMS.txt`. Check that each EXE's visible name and version match the release.

GitHub's automatic Source code archives contain the repository files. Users who want to run Refseek should download an EXE.

For documentation-only edits, update the repository and source ZIP. The existing EXEs remain the same unless their code or bundled content changes.
