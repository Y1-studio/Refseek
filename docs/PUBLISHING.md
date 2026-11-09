# Publish source and downloads

The repository is [Y1-studio/Refseek](https://github.com/Y1-studio/Refseek). The 2.2 release tag is **v2.2.0** and both editions report **2.2.0**.

## Repository contents

Keep `README.md`, `native/`, `python/`, `docs/`, `third-party/` and `THIRD_PARTY_NOTICES.md` at the repository root. Model/runtime binaries, personal libraries, credentials, build output and diagnostic logs stay outside Git. The source export's resource locks describe the matching release resources. Third-party notices apply to their named components; no project-wide source license has been selected.

## Release assets

Publish a tag at the reviewed source revision, then attach `Refseek-Desktop.exe`, `Refseek-Browser.exe`, `README.txt` and `SHA256SUMS.txt`. Use [RELEASE_NOTES.md](RELEASE_NOTES.md) for the description. Retain the EXE filenames because the README download links use them.

Before publishing, verify the packages' visible versions, exact source and resource contents, manual startup/indexing checks, documentation links and SHA256 hashes. Upload the EXEs as release assets. After publication, compare GitHub's asset sizes and SHA256 digests with the local files and check the README and guides at the release revision.
