# Browser edition: getting started

1. Download **SemanticSearch-v2.exe** from Releases and put it in a folder of its own.
2. Double-click it. A small launcher window prepares the model and opens a search page in your usual browser. Keep that launcher open.
3. Open **Library & indexing**, choose a folder of pictures and start indexing. Indexing prepares a searchable catalogue; your original pictures stay in place.
4. Return to **Search**. Enter a description, choose or drop in a reference picture, or use both. Choose the aspect you want to match, such as **Style** or **Subject**.
5. For Style, the default is the Contrastive style adapter. Other aspects use their existing methods. Similarity uses the overall image resemblance.
6. Use **+ Want** for additional descriptions. Text-only queries offer Match all and Match any. Use the negative image and exclusion controls to reduce unwanted matches; these influence ranking rather than guaranteeing a strict exclusion.
7. Click a result to preview it. Left/Up and Right/Down move through results, including across result pages; Escape closes the preview. The preview frame stays the same size.
8. When finished, use **Stop and exit** in the launcher. Closing only the browser tab leaves the application running. Use **Open search** to reopen its page.

## Keep your saved catalogue

The folder **SemanticSearch-v2-data** beside the application holds your catalogue and logs. Keep it during updates, and keep your original picture folders available. After changing a picture folder, update its index. Keep the Python and Rust applications in separate folders; their settings and catalogues are independent.

## If something goes wrong

- **The page does not open:** wait for the launcher to finish, then select Open search. Read any error shown in the launcher.
- **Startup is slow:** preparing the model and graphics backend takes time, especially on CPU. Allow several gigabytes of free disk space for extracted resources.
- **Results are poor:** try clearer wording, another reference, or a different matching aspect. Whole images influence matching; style and pose are approximate.
- **A file is skipped:** read the indexing details and logs. Layered artwork may need a PNG or JPEG export.

The [screenshots](screenshots/README.md) currently show the native Rust interface. The browser edition has a different layout. [Return to the two-version overview](../README.md).
