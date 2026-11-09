# Refseek 2.2.0

This release brings the Rust Desktop and Python Browser editions to the same version. It combines faster folder updates, bounded image preparation, remembered indexing choices, and fixes that protect saved indexing work.

## Faster indexing and updates

- **Update this folder** compares paths, file sizes and modification times with the saved index. Unchanged files skip content reads, resizing and embedding. A directory scan is still needed to find additions and removals. An unchanged update does not replace the active or recovery catalog.
- Images are selected by default. File-type choices and the number of CPU preparation workers are saved. Both editions default to **two preparation workers**, with a custom count up to the available CPU threads. Desktop also offers Auto.
- A bounded queue prepares images ahead of inference, overlapping file reads and resizing with model work. Desktop adds a cache-friendly Lanczos3 resize path with AVX2 where available and a portable fallback. Browser overlaps preparation with two independent embedding requests; text chunks retain their batching.
- **Verify file contents** hashes files to detect edits that preserve both size and modification time. It takes longer than a normal update; legacy Browser records without hashes can need embedding again on their first verification.

Performance depends on your images, processor, graphics device and disk. These improvements do not imply a fixed whole-library speed multiplier.

## Indexing and recovery fixes

- New snapshots are verified before the active catalog changes. Active and previous catalogs protect their referenced snapshots; cleanup verifies protected data before removing older copies.
- A changed file keeps its saved vectors until its complete replacement is available. Cancellation, embedding errors and incomplete scans retain previous work. Completed files are checkpointed for the next update.
- Missing, duplicate and out-of-range embedding response indices are rejected. Invalid vectors are rejected before publication.
- Cancel interrupts embedding waits and startup readiness probes. Queued preparation is stopped; an active image decode may finish before shutdown completes.
- Browser publication failures preserve the previous catalogs. A recovery-write failure retains older snapshots and reports a warning. Oversized documents are reported instead of silently truncating their index.
- More useful indexing details, saved cleanup results and startup diagnostics help explain skipped files and failures.

## Search, storage and Browser access

- Include / Skip rules filter folders and subfolders, with deeper rules taking precedence. Both editions provide a folder tree and a shortcut to search the previewed picture's folder.
- New libraries default to **768 dimensions**. Empty libraries can use 128, 256, 512 or 768; existing libraries keep their dimension. Smaller indexes retain the full vector cache for trained Style methods and do not reduce encoder work.
- Browser includes Normal mode and optional Account mode with administrator and guest folder access, local user management, and HTTPS / Cloudflare Tunnel configuration. The guide explains setup; remote access remains optional.
- Desktop uses `data/library`; Browser uses `data/browser`, with shared Browser configuration in `data/.env`. Their catalogs remain independent. Browser can move an older portable Browser data folder into this layout without re-embedding. A verified, one-time Desktop-to-Browser index import is available from Python source.

## Upgrade

1. Close the edition you are updating. Back up its existing data folder.
2. Replace its EXE in the same application folder and keep the saved data. Use **Refseek-Desktop.exe** or **Refseek-Browser.exe**; update shortcuts if needed.
3. Open the new version and use **Update this folder** for changed picture folders. Existing compatible libraries do not need a full rebuild.

If Browser finds `Refseek-Browser-data` or `SemanticSearch-v2-data` and no `data/browser`, it moves that folder into the shared layout and preserves its index and accounts. Conflicting configuration files or a move across drives stop with instructions instead of overwriting data. Desktop and Browser do not continually synchronize their indexes. Older releases cannot read the newer smaller-dimension snapshot format; retain your backup if you need to roll back.

## Verification and matching quality

Manual checks cover real text/image inference, indexing and unchanged updates, cancellation, failed saves, recovery retention, malformed responses, numerical reference comparisons, Browser account boundaries, and UI layouts. The 2.2 packages also receive startup/model and source/resource checks before publication.

The pinned Q8 model pair, llama.cpp b11741 runtimes, frozen trained adapters and default image preparation policy are unchanged. Full images are used without cropping; large images are resized to the existing 1024-pixel limit. The Desktop resize optimization was checked for matching pixels, and numerical compatibility checks passed. This release does not establish new retrieval-quality results. Style and pose remain approximate, and smaller search dimensions can change rankings.

Windows x64 only. Both EXEs include their model and runtime. Download them under **Assets**; GitHub's automatic source archives are for developers. See [the README](https://github.com/Y1-studio/Refseek#readme) for setup and guides. Each EXE's SHA256 is listed in **SHA256SUMS.txt**.
