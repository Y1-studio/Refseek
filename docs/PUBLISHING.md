# Publish this repository and both Windows downloads

## Repository files

Create a repository named **Refseek**. Upload the contents of the prepared Refseek folder at the repository root, retaining the native/, python/, docs/ and third-party/ subfolders. README.md must be at the top level. Do not upload the sibling github-release folder as repository files.

## Release downloads

Create tag **v2.0.0**, title **Refseek 2.0 — Desktop and Browser**. Use the prepared RELEASE_NOTES.md as the description. Attach these four files from the sibling github-release folder:

- SemanticSearch-Native.exe
- SemanticSearch-v2.exe
- README.txt
- SHA256SUMS.txt

Both EXEs belong in Release Assets. They do not belong in Git history. The top-level README links to the latest release and the two fixed asset names. Preserve those filenames in later releases or update the links.

After publishing, check that both source folders appear, all five screenshots render, both application links download the intended EXEs, and downloaded SHA256 values match SHA256SUMS.txt. The automatic Source code ZIP is the source tree, not the ready-to-run app.

This prepared folder does not contain personal libraries, logs, credentials, training datasets or model binaries. Review any future additions before publishing. The source license has not been selected; this snapshot does not add one.
