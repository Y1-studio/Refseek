# Browser guide

Refseek Browser opens a search page on your computer in your usual browser. A separate launcher window loads the model and keeps it running.

## Open and index

Download [Refseek-Browser.exe](https://github.com/Y1-studio/Refseek/releases/latest/download/Refseek-Browser.exe), put it in its own folder, and double-click it. An `.exe` is a Windows application file; you don't need to install Python.

Wait for the launcher to open the search page. Keep the launcher open while searching. The app extracts its included files on each launch, so leave several gigabytes free for temporary resources. It tries CUDA, Vulkan, then CPU using your installed graphics drivers.

Open **Library & indexing**. Under **Add a folder**, enter the picture folder's address, such as `D:\Pictures\References`, then click **Index**. To copy the address, open the folder in Windows File Explorer, click its address bar, and press **Ctrl + C**. Paste it into Refseek with **Ctrl + V**.

Indexing creates the searchable catalogue and leaves your originals in place. Start with a small folder, then return to **Search** after some work has been saved. Searches can continue during indexing.

## Search and refine

Enter a description, click **Choose image** or drop in a reference picture, or use both. Click **Search** to see results.

With a reference, choose an aspect such as **Subject**, **Style**, or **Pose**. **Similarity** compares overall resemblance. Style starts with the **Contrastive style adapter**. The whole image affects matching; style and pose results aren't exact. Pose uses embeddings without detecting body joints.

When using words and an image, move the slider between **text** and **image** to balance their influence. The display shows their percentages. **Aspect strength** sets how much the selected feature influences matching.

Open **Refine search · extra terms & exclusions** and click **+ want** for more descriptions. For text-only queries, **match all** combines them, while **match any** uses the best individual match. With an image, the descriptions are combined before the text/image blend.

For a negative image, click **Choose ignore-image** below the reference-image controls. It lowers the rank of pictures that resemble that example; the number beside it sets the strength. Use the **×** beside its preview to remove it.

Enter unwanted concepts in **Exclude…** inside Refine. Select the folder checkboxes to restrict where you search; with none selected, all library folders are searched. Exclusions adjust ranking rather than guaranteeing removal. Clear previous inputs when they no longer belong to the search.

## Preview and close

Click a result to preview it. Left/Up moves back; Right/Down moves forward, including across result pages. Escape closes the preview. The frame keeps its size as images change.

Use **Stop and exit** in the launcher when finished. Closing the browser tab leaves Refseek running. **Open search** in the launcher reopens the page.

## Saved data and updates

New installations use `Refseek-Browser-data` beside the EXE for the catalogue and logs. If `SemanticSearch-v2-data` already exists and the new folder doesn't, Refseek reuses that older folder.

Close Refseek before replacing its EXE and keep the data folder. Update old shortcuts when the filename changes. Copy the application and data folder together when moving it, and keep the original pictures available. After changing a picture folder, update its index.

Keep the desktop and browser editions in separate application folders. Their catalogues and settings are independent.

## Troubleshooting

- **The page hasn't opened:** wait for loading, then click **Open search**. Read any startup error in the launcher.
- **Startup takes a long time:** CPU processing and first-time graphics preparation take time. Check for free disk space for the extracted resources.
- **Results don't match what you wanted:** clear old references and exclusions, simplify the description, or change the selected aspect.
- **A file was skipped:** check the indexing details and logs. Export layered artwork as PNG or JPEG; incomplete or damaged files still need replacing.

The [desktop guide](USER_GUIDE.md) explains the shared search ideas in more detail. Its [screenshots](screenshots/README.md) show the Rust interface, whose layout differs from the browser app.

[Overview](../README.md) · [Python build instructions](../python/README.md)
