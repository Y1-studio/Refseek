# Browser guide

Refseek Browser opens a search page on your computer in your usual browser. A separate launcher window loads the model and keeps it running.

## Open and index

Download [Refseek-Browser.exe](https://github.com/Y1-studio/Refseek/releases/latest/download/Refseek-Browser.exe), put it in its own folder, and double-click it. An `.exe` is a Windows application file; you don't need to install Python.

Choose your access mode and tunnel address in the launcher, then click **Start**. Refseek loads the model and opens the search page. Keep the launcher open while searching. The app extracts its included files on each launch, so leave several gigabytes free for temporary resources. It tries CUDA, Vulkan, then CPU using your installed graphics drivers.

Open **Library & indexing**. Under **Add a folder**, enter the picture folder's address, such as `D:\Pictures\References`, then click **Index**. To copy the address, open the folder in Windows File Explorer, click its address bar, and press **Ctrl + C**. Paste it into Refseek with **Ctrl + V**.

Indexing creates the searchable catalogue and leaves your originals in place. Start with a small folder, then return to **Search** after some work has been saved. Searches can continue during indexing.

A new library starts with images selected for indexing. Select any other types you want to index in **Library & indexing**; your choices are remembered. **Settings > CPU preparation workers** starts at **2** and accepts a saved custom value up to the available CPU threads.

## Search and refine

Enter a description, click **Choose image** or drop in a reference picture, or use both. Click **Search** to see results.

Search starts with **Images** selected. Use **Search file types** to include text, PDFs, or other indexed files, or turn **Images** off to search just the other types you selected.

With a reference, choose an aspect such as **Subject**, **Style**, or **Pose**. **Similarity** compares overall resemblance. Style starts with the **Contrastive style adapter**. The whole image affects matching; style and pose results aren't exact. Pose uses embeddings without detecting body joints.

When using words and an image, move the slider between **text** and **image** to balance their influence. The display shows their percentages. **Aspect strength** sets how much the selected feature influences matching.

Open **Refine search · extra terms & exclusions** and click **+ want** for more descriptions. For text-only queries, **match all** combines them, while **match any** uses the best individual match. With an image, the descriptions are combined before the text/image blend.

For a negative image, click **Choose ignore-image** under **Refine search** or **Use negative** in a result preview. It lowers the rank of pictures that resemble that example. The number beside the image sets the strength. Use the **×** beside its preview to remove it.

Enter unwanted concepts in **Exclude…** inside Refine. Open **Folders and subfolders** and click **+** beside an indexed root to see its children. Use **Include** and **Skip** to control which branches are searched. Deeper rules take precedence. **Search all folders** clears these rules and repeats the search.

## Preview and close

Click a result to preview it. Left/Up moves back; Right/Down moves forward, including across result pages. Escape closes the preview. The frame keeps its size as images change.

Click **Search this folder only** in the preview to repeat your current search in the picture's immediate folder. It excludes subfolders. Use **Search all folders** beside **Search** to reset the scope.

Use **Stop and exit** in the launcher when finished. Closing the browser tab leaves Refseek running. **Open search** in the launcher reopens the page.

## Saved data and updates

Browser uses `data/browser` beside the EXE. On first launch, it moves an existing `Refseek-Browser-data` or `SemanticSearch-v2-data` into `data/browser` if that destination does not exist. It preserves the index and moves `.env` up to `data/.env`. If the shared folder is on another drive, close Browser and move the old Browser folder to `data/browser` yourself before restarting.

Close Refseek before replacing its EXE and keep the data folder. Update old shortcuts when the filename changes. Copy the application and data folder together when moving it, and keep the original pictures available. After changing a picture folder, update its index.

You can keep the desktop and browser EXEs beside the same `data` folder. The desktop catalogue stays in `data/library`. Each edition keeps its own catalogue and settings.

To reuse a Desktop index before creating a Browser index, run this from the Python source directory:

```powershell
python -m egl.rust_import "D:\Refseek\data\library" "D:\Refseek\data\browser"
```

The import verifies and copies the saved vectors without embedding the files again. It keeps the Desktop index and Browser accounts in place. Changes made in either edition after the import stay in that edition's index.

**Settings** lets you change UI scale, text size, theme, card size, and which details appear in results. New libraries use 768 dimensions by default. You can choose a smaller dimension before the first scan. The trained Style adapters still use the full vectors. The estimates show storage for 10,000 vectors and exclude metadata and the recovery copy.

**Library & indexing** shows the size of each saved index and the originals it references. Use **Show latest indexing details** for skipped files. **Clean up old snapshots** removes unused copies and keeps the active index and one recovery copy.

## Share selected folders with users

Refseek opens in **Normal mode** by default, with full access on this computer and no sign-in. In the launcher, click **Use Account mode** to require sign-in. Choose an owner username and a password of at least 15 characters if prompted. Set the mode before clicking **Start** to use it immediately. Changes made after the server is running apply when you restart Refseek. Saved users and folder access stay available for the next time you enable Account mode.

Open **Manage users** in the local launcher. It opens directly and treats the person using this computer as the administrator. Click **New user**, enter a username and password, and select the indexed folders you want to share. Click **Allow selected folders**, then **Create user**. Guests can search and open files in those folders and their subfolders. Browser access in Account mode still requires signing in.

To give someone full access, select their account, check **Administrator**, and click **Save changes**. You can also check it when creating a new account. Administrators can use all indexed folders, indexing, settings, and browser user management. Clear the checkbox and choose folders to make the account a guest again. Changing a role signs that user out. The app keeps at least one administrator.

To change a guest's folders, select their name and click **Edit selected user**. Expand an indexed folder to choose a subfolder, then click **Allow selected folders**. Select entries in the allowed list and click **Remove selected access** to remove them. **Save changes** applies the new access. **Clear folder access** followed by **Save changes** leaves the account able to sign in with no access to library files. To change the password, enter a new one before saving; leave it blank to keep the current password. Folder and password changes sign the user out.

Select an existing guest to change their folders or reset their password. Leave the password blank to keep it. Click **Delete user** to remove an account. Changing a guest's password or folder access, or deleting the account, signs them out. You can also manage users from the owner's browser session.

Guests get **Search** and **Client settings**. Client settings control the appearance and results layout in their browser. Indexing, system details, library settings, and account management are owner-only.

The setup creates `REFSEEK_SESSION_KEY` in `data/.env`. Keep this file private. `data/browser/accounts.sqlite3` stores password hashes and folder access. Keep both files when moving the library. To set up from Python source, run `python run.py --data-dir "D:\Refseek\data\browser" accounts init --username owner`, then restart `web`. Use `accounts on` or `accounts off` to switch modes with saved users. The `accounts key` command generates the key alone while leaving the mode unchanged.

Refseek listens only on your computer by default. For other devices, set a LAN address and an HTTPS certificate trusted by those devices. Add these lines to `data/.env` alongside the generated key, then restart. Relative certificate paths start at `data`.

```dotenv
REFSEEK_BIND_HOST=0.0.0.0
REFSEEK_PUBLIC_HOST=refseek.example.lan
REFSEEK_WEB_PORT=8088
REFSEEK_TLS_CERT=certs/refseek.pem
REFSEEK_TLS_KEY=certs/refseek-key.pem
```

Replace `refseek.example.lan` with a hostname or LAN address that resolves to this computer and matches the certificate. Guests open the HTTPS address shown by the launcher. [OWASP guidance on HTTPS for sign-in](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)

### Share through Cloudflare Tunnel

Use **Account mode** and run `cloudflared` on the same computer as Refseek. For a Quick Tunnel, point it at the local address shown in the launcher, for example `cloudflared tunnel --url http://127.0.0.1:8088`. Keep Cloudflare's default Host header handling.

Click **Cloudflare tunnel…** in the Refseek launcher and paste the public HTTPS address Cloudflare prints, then click **Start**. Use that public address to sign in. If a Quick Tunnel gives you a different address, update it in the launcher. Changes made after Refseek has started require a restart. [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## Troubleshooting

- **The page hasn't opened:** wait for loading, then click **Open search**. Read any startup error in the launcher.
- **Startup takes a long time:** CPU processing and first-time graphics preparation take time. Check for free disk space for the extracted resources.
- **Results don't match what you wanted:** clear old references and exclusions, simplify the description, or change the selected aspect.
- **A file was skipped:** check the indexing details and logs. Export layered artwork as PNG or JPEG; incomplete or damaged files still need replacing.

The [desktop guide](USER_GUIDE.md) explains the shared search ideas in more detail. Its [screenshots](screenshots/README.md) show the Rust interface, whose layout differs from the browser app.

[Overview](../README.md) · [Python build instructions](../python/README.md)

## Update and verify a folder

Use a folder's update button after adding, editing or removing files. Normal updates compare saved paths, sizes and modification times and skip content reads for unchanged files. Refseek still scans directories for additions and removals. An unchanged update keeps both catalogs intact.

Use **Verify file contents** if changes may have preserved size and modification time. It reads and hashes every file; legacy records without hashes can need embedding again the first time. Cancellation saves completed files and keeps previous vectors for unfinished replacements. Incomplete scans do not publish removals. Documents that exceed the chunk limit appear in the indexing warnings instead of being silently truncated.
