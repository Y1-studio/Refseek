# Semantic Search Native

**Find the reference image you remember, even when you cannot remember its filename.**

A Windows desktop app for searching your own art and reference folders using words, an example image, or both. Search by overall resemblance, subject, style, pose, viewpoint, composition, colour, or lighting.

Search runs on your computer in a native Rust window. The downloadable app includes its model and processing tools; it opens without a browser or a separate Python installation.

[![Search by an example image and words in the native desktop app](../docs/screenshots/search.png)](../docs/screenshots/search.png)

*Search with an image, a description, and the feature you want to match. Click any screenshot to open it at full size.*

## Download and open

1. Open **Releases** on this repository's page.
2. Download **`SemanticSearch-Native.exe`** from the release's **Assets** section. The automatically generated “Source code” downloads are for developers.
3. Put the application in its own folder, such as `Documents\Art Search`.
4. Double-click it and wait for the opening/model-loading messages to finish.

An `.exe` is a Windows application file. Windows may show its name without the `.exe` ending. The first opening prepares the included resources and needs several gigabytes of free disk space.

**New to the app? Read the [artist's guide](../docs/USER_GUIDE.md).** It explains what the controls do and how to use them, step by step.

## Your first search

1. Open **Library**, click **Browse...**, and select a folder of pictures.
2. Select **image**, then click **Add & index**. This prepares a searchable catalogue.
3. Open **Search** and describe what you want, such as “delicate botanical ink drawings”.
4. Click **Search library** and click a result to preview it.

For visual searching, choose or drag in a reference image, select **Style**, **Subject**, or another matching feature, then search again. Your results are files already in your collection; the originals stay where you saved them.

## Features

- **Text and image search:** use a description, a whole reference image, or a mixture of both.
- **Aspect matching:** focus on subject, style, pose, viewpoint, composition, colour, or lighting. Style starts with the Contrastive style adapter.
- **Multiple ideas:** add **+ Want** fields; text-only searches offer **Match all** and **Match any**.
- **Refine:** use a negative image as a counter-example, exclude concepts, and restrict searches to selected folders or file types.
- **Browse comfortably:** use arrow keys or Previous/Next in a fixed-size image viewer; open originals or reveal them in their folders.
- **Index while searching:** see files/second and estimated remaining time while searching already saved work.
- **Separate folder indexes:** updates reuse unchanged files; removing a folder from the library leaves the original files in place.
- **Adjustable display:** interface scaling, text size, themes, thumbnail size, spacing and results per page.
- **Automatic processing choice:** tries compatible NVIDIA CUDA devices, then Vulkan graphics devices, then CPU processing.

Common image inputs include JPG/JPEG, PNG, WebP, GIF, BMP and TIFF. For layered artwork, export a PNG or JPEG copy. Text and document indexing is also available; audio/video support is experimental.

## Screenshots

| Refine and negative images | Library and indexing |
| --- | --- |
| [![Refine controls with a negative-image example](../docs/screenshots/refine.png)](../docs/screenshots/refine.png) | [![Library with live indexing speed and estimated time remaining](../docs/screenshots/library.png)](../docs/screenshots/library.png) |
| Choose what to avoid and which folders to search. | Add folders and watch progress while continuing to search. |

| Display settings | Image viewer |
| --- | --- |
| [![Settings for interface scaling, text size and result layout](../docs/screenshots/settings.png)](../docs/screenshots/settings.png) | [![Image preview with Previous and Next navigation](../docs/screenshots/viewer.png)](../docs/screenshots/viewer.png) |
| Adjust the app's size, theme and preview layout. | Browse pictures with the buttons or arrow keys. |

*The screenshots use [original demo illustrations](../docs/screenshots/README.md), not a personal collection. They show the interface, not an accuracy benchmark.*

## What to expect

These are visual suggestions, not exact guarantees. Style and pose matches can be imperfect, and the whole reference image influences a search. Pose uses an experimental embedding projection rather than a joint detector.

The frozen style adapters were fitted using 12,940 images across 84 artists. Earlier benchmark results apply to earlier evaluation fits; they do not establish accuracy for these full-data weights or the current Q8/native image preparation on new collections.

## Keeping your library

The app saves its settings and search catalogue in **`data` beside the application**. Keep that folder when updating or moving the app, and keep the original image folders available. After changing a source folder, use **Update this folder** in Library.

For updates: close the running app, replace the application file, keep `data`, then reopen it.

## Source and documentation

- [Artist's guide](../docs/USER_GUIDE.md) — setup, search examples, controls, settings and troubleshooting.
- [Build from source](../docs/NATIVE_BUILDING.md) — Windows GNU toolchain and recovery of the exact runtime resources.
- [Third-party components](../THIRD_PARTY_NOTICES.md) — model, runtime and dependency notices.
- [Publishing guide](../docs/PUBLISHING.md) — repository uploads and downloadable releases.

This source folder excludes personal libraries, model logs, dependency caches, training datasets, model weights, frozen adapter binaries and research images. Builders recover the matching application resources from an existing release or a matching local assets folder; see the build guide.

No project-wide source license has been selected for this snapshot. Third-party components retain their own license terms.
