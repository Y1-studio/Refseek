from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import EmbedClient, l2norm
from .account_http import AccountHTTP, allowed_file, guest_index
from .accounts import Accounts, read_env
from .config import Settings
from .indexer import Indexer
from .portable import config_file, env_path, shared_data_dir
from .scan import INDEX_KINDS
from .search import Searcher
from .store import Index

try:
    from PIL import Image as _PILImage
except Exception:
    _PILImage = None


def _thumbnail(data: bytes, max_side: int = 512):
    """Decode full-frame previews into a browser-supported image format."""
    if _PILImage is None:
        return None
    import io
    from PIL import ImageOps

    with _PILImage.open(io.BytesIO(data)) as image:
        image.draft('RGB', (max_side, max_side))
        img = ImageOps.exif_transpose(image)
        img.thumbnail((max_side, max_side), _PILImage.Resampling.LANCZOS)
        out = io.BytesIO()
        if 'A' in img.getbands() or 'transparency' in img.info:
            img.convert('RGBA').save(out, 'PNG')
            return out.getvalue(), 'image/png'
        img.convert('RGB').save(out, 'JPEG', quality=82)
        return out.getvalue(), 'image/jpeg'

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Refseek Browser</title>
<style>
  :root {
    color-scheme: dark;
    --bg:#0e1116; --surface:#161b22; --surface2:#161b22; --inset:#161b22;
    --border:#30363d; --border-soft:#21262d;
    --text:#e6edf3; --muted:#8b949e; --faint:#6e7681;
    --accent:#1f6feb; --accent-hi:#58a6ff; --accent-ink:#fff;
    --accent-soft:rgba(31,111,235,.15);
    --green:#7ee787; --red:#f85149;
    --header:rgba(14,17,22,.9); --ui-zoom:1; --card-width:230px; --thumb-height:155px;
    --sans:ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;
    --mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
  }
  :root[data-theme="light"] {
    color-scheme:light; --bg:#f2f5f9; --surface:#fff; --surface2:#edf1f6; --inset:#f7f9fc;
    --border:#9eabb9; --border-soft:#cbd3dd; --text:#182536; --muted:#425366; --faint:#526477;
    --accent:#185bc0; --accent-hi:#0d4eaa; --green:#12683a; --red:#ad2222; --header:rgba(242,245,249,.94);
  }
  * { box-sizing:border-box; }
  [hidden] { display:none!important; }
  :root[data-account="guest"] #method_report { display:none!important; }
  .aspectbox { border:1px solid var(--border); border-radius:12px; padding:14px 16px;
      margin-bottom:16px; background:var(--inset); }
  .aspectbox .bar { gap:7px; margin-bottom:8px; }
  .aspect { padding:8px 12px; font-size:0.78125rem; font-weight:600; }
  .aspect[aria-pressed=true] { background:var(--accent); border-color:var(--accent-hi); }
  .aspectbox label { color:var(--muted); font-size:0.75rem; }
  #focus_strength { flex:1; max-width:330px; accent-color:var(--accent-hi); }
  #focus_hint { color:var(--muted); font-size:0.75rem; margin:0; }
  #focus_amount { min-width:40px; font:0.75rem var(--mono); }
  .chip:disabled { opacity:.55; }
  .drop img { max-width:100%; object-fit:contain; }
  body { margin:0; font:0.9375rem/1.55 var(--sans); background:var(--bg); color:var(--text);
      min-height:100vh; }
  header { position:sticky; top:0; z-index:10; padding:16px 22px 13px;
      background:var(--header); backdrop-filter:blur(10px);
      border-bottom:1px solid var(--border-soft); }
  :root[data-large-ui] header { position:static; }
  header .wrap { max-width:1080px; margin:0 auto; }
  h1 { font-size:1.25rem; margin:0 0 3px; font-weight:650; letter-spacing:0; }
  .sub { color:var(--muted); font-size:0.78125rem; font-family:var(--mono); }
  main { padding:22px 22px 70px; max-width:1080px; margin:0 auto; }
  .panel { border:1px solid var(--border-soft); border-radius:18px; padding:20px 22px;
      margin-bottom:22px; background:var(--surface); }
  .panel.index { border-radius:12px; background:var(--inset); }
  header .wrap, .panel { zoom:var(--ui-zoom); }
  .navtabs { display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center; }
  .navtabs [role=tab] { background:transparent;border-color:transparent;color:var(--muted);padding:9px 18px; }
  .navtabs [aria-selected=true] { background:var(--accent-soft);border-color:var(--accent);color:var(--text); }
  .tabbadge { display:inline-block;font:0.6875rem var(--mono);margin-left:7px;color:var(--green); }
  .tools { margin-top:8px;font-size:0.75rem;color:var(--muted); }
  .tools summary,.refine summary { cursor:pointer; }
  .tools a { display:inline-block;color:var(--accent-hi);margin:7px 14px 0 0;text-decoration:none; }
  .refine { margin:10px 0 16px;border:1px solid var(--border-soft);border-radius:12px;padding:10px 14px;color:var(--muted); }
  .refine[open] summary { margin-bottom:12px; }
  .libraryintro { color:var(--muted);font-size:0.8125rem;margin-top:-5px; }
  .rootrow .rname { white-space:normal;word-break:break-word; }
  #dataloc, #index_backend, #indexreport, #idxstatus { overflow-wrap:anywhere; min-width:0; }
  .rootstorage { margin-top:4px;font:0.6875rem var(--mono);color:var(--faint); }
  .rootrow { flex-wrap:wrap; }
  .panel > h2 { font-size:0.9375rem; color:var(--text); margin:0 0 14px; font-weight:600; }
  .panel h3 { font-size:0.8125rem; color:var(--muted); margin:18px 0 8px; font-weight:600; }
  .panel h3:first-of-type { margin-top:0; }
  .hint { color:var(--faint); font-size:0.75rem; margin-top:7px; }
  .bar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
  .bar:last-child { margin-bottom:0; }
  input[type=search], input[type=text] { flex:1 1 340px; padding:12px 15px; border-radius:12px;
      border:1px solid var(--border); background:var(--inset); color:var(--text); font-size:0.9375rem;
      outline:none; transition:border-color .15s, box-shadow .15s; min-width:0; font-family:var(--sans); }
  #q { font-size:1.125rem; padding:15px 18px; border-radius:14px; }
  #q::placeholder { color:var(--faint); }
  input[type=search]:focus, input[type=text]:focus, input[type=number]:focus, select:focus {
      border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
  input[type=number] { width:76px; padding:11px 10px; border-radius:12px; font-family:var(--mono);
      border:1px solid var(--border); background:var(--inset); color:var(--text); outline:none; }
  input.want { flex:1 1 340px; padding:10px 14px; border-radius:12px;
      border:1px dashed var(--border); background:var(--inset); color:var(--text); font-size:0.875rem; outline:none; }
  select { padding:11px 12px; border-radius:12px; border:1px solid var(--border);
      background:var(--inset); color:var(--text); outline:none; font-family:var(--sans); }
  button { padding:11px 20px; border-radius:12px; border:1px solid #30363d;
      background:#238636; color:#fff; font-weight:700; font-size:0.875rem;
      cursor:pointer; transition:filter .12s, transform .05s; white-space:nowrap;
      font-family:var(--sans); }
  button:hover { filter:brightness(1.12); }
  button:active { transform:translateY(1px); }
  button.ghost { background:var(--surface2); color:var(--text); border-color:var(--border); font-weight:500; }
  button.ghost:hover { border-color:var(--faint); background:#1d2431; }
  button.danger { background:transparent; color:var(--red); border-color:rgba(248,81,73,.4); }
  button.danger:hover { background:rgba(248,81,73,.12); border-color:var(--red); }
  button:disabled { opacity:.4; cursor:default; transform:none; }
  button:focus-visible, input:focus-visible, select:focus-visible, .chip:focus-visible {
      outline:2px solid var(--accent); outline-offset:2px; }
  @media (prefers-reduced-motion:reduce) {
    * { animation:none !important; transition:none !important; }
  }
  .chips { display:flex; gap:7px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
  .chip { padding:5px 12px; border-radius:999px; border:1px solid var(--border);
      background:var(--surface2); color:var(--muted); font-size:0.78125rem; cursor:pointer;
      user-select:none; transition:color .12s, border-color .12s; font-family:var(--mono); }
  .chip:hover { border-color:var(--faint); color:var(--text); }
  .chip.on { background:var(--accent); border-color:var(--accent); color:#fff;
      font-weight:600; }
  .drop { border:1.5px dashed var(--border); border-radius:12px; padding:14px 16px;
      text-align:center; color:var(--muted); margin-bottom:16px; font-size:0.8125rem;
      display:flex; align-items:center; justify-content:center; gap:10px; flex-wrap:wrap;
      background:var(--inset); transition:border-color .15s; }
  .drop.over { border-color:var(--accent); color:var(--text); }
  .drop.small { padding:9px 12px; font-size:0.75rem; }
  .drop.small img { max-height:56px; }
  .drop img { max-height:90px; vertical-align:middle; border-radius:8px; }
  #rmimg { padding:4px 12px; margin-left:4px; }
  #results { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,var(--card-width)),1fr)); gap:12px; }
  .card { background:var(--surface); border:1px solid var(--border-soft); border-radius:10px;
      overflow:hidden; display:flex; flex-direction:column; text-decoration:none; color:inherit;
      transition:border-color .12s; }
  .card:hover { border-color:var(--accent); }
  .thumb { height:var(--thumb-height); background:var(--inset) center/cover no-repeat; display:flex;
      align-items:center; justify-content:center; font-size:2.25rem; color:var(--faint); }
  .thumb img { display:block; width:100%; height:100%; object-fit:cover; }
  .thumb-unavailable { padding:12px; text-align:center; font-size:0.8125rem; color:var(--muted); }
  .body { padding:11px 13px 12px; display:flex; flex-direction:column; gap:2px; }
  .kind { font-size:0.6875rem; color:var(--green); font-weight:700; font-family:var(--mono); }
  .name { font-weight:600; font-size:0.84375rem; word-break:break-all; }
  .result-path { display:none; overflow-wrap:anywhere; color:var(--muted); }
  .show-paths .result-path { display:block; }
  .hide-scores .result-score { display:none; }
  .compact-cards .body { padding:5px 8px; }
  .compact-cards .snip { display:none; }
  .settings-grid { display:grid; gap:.75rem; grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr)); }
  .settings-grid label { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; }
  .snip { color:var(--muted); font-size:0.75rem; max-height:57px; overflow:hidden; }
  .meta { display:flex; justify-content:space-between; align-items:center; color:var(--faint);
      font-size:0.6875rem; margin-top:8px; font-family:var(--mono); }
  .meta span:last-child { background:var(--surface2); border:1px solid var(--border);
      border-radius:999px; padding:1px 9px; color:var(--muted); }
  .status { color:var(--muted); font-size:0.78125rem; margin-bottom:12px; font-family:var(--mono); }
  .status:empty { display:none; }
  .err { color:var(--red); }
  .lab { font-size:0.78125rem; color:var(--muted); align-self:center; margin-right:2px; }
  label.check { display:inline-flex; align-items:center; gap:8px; font-size:0.8125rem; color:var(--muted);
      cursor:pointer; }
  label.check:hover { color:var(--text); }
  input[type=checkbox] { width:15px; height:15px; accent-color:var(--accent); cursor:pointer; }
  input[type=range] { flex:1 1 200px; accent-color:var(--accent); cursor:pointer; }
  .idxbar { display:flex; gap:8px; flex-wrap:wrap; }
  .idxbar input { flex:1 1 300px; padding:11px 14px; border-radius:12px;
      border:1px solid var(--border); background:var(--inset); color:var(--text); font-size:0.875rem;
      outline:none; min-width:0; }
  .idxrow { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px; }
  .idxrow .spacer { flex:1; }
  #idxstatus { margin:10px 0 0; }
  #idxstatus:empty { display:none; }
  .idxtrack { display:none; height:8px; background:#20262f; border-radius:5px; overflow:hidden;
      margin:12px 0 4px; }
  .idxfill { height:100%; width:0; background:var(--accent); border-radius:5px; transition:width .5s; }
  .idxtrack.indet .idxfill { width:35%; transition:none; animation:idxslide 1.1s infinite ease-in-out; }
  @keyframes idxslide { 0%{margin-left:-35%} 100%{margin-left:100%} }
  .idxfoot { display:flex; gap:10px; align-items:flex-start; margin-top:10px; }
  .idxfoot .status { flex:1; margin:0; }
  .pager { display:flex; gap:12px; align-items:center; justify-content:center; margin:18px 0 6px; }
  .pager[hidden] { display:none; }
  .pager button { padding:8px 18px; font-size:0.8125rem; }
  .rootrow { display:flex; gap:10px; align-items:center; padding:8px 12px;
      border:1px solid var(--border-soft); border-radius:10px; margin-bottom:8px;
      background:var(--inset); }
  .rootrow .rname { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
      white-space:nowrap; font-family:var(--mono); font-size:0.78125rem; }
  .rootrow .rcounts { color:var(--faint); font-size:0.71875rem; font-family:var(--mono);
      white-space:nowrap; }
  .rootrow button { padding:6px 12px; font-size:0.78125rem; }
  #foldertree { max-height:18rem; overflow:auto; }
  .folder-filter-row { display:flex; align-items:center; flex-wrap:wrap; gap:0.45rem; margin:0.2rem 0; }
  .folder-toggle { padding:0.15rem; width:2rem; flex:none; }
  .folder-label { min-width:0; overflow-wrap:anywhere; }
  html { scrollbar-gutter:stable; }
  #lightbox { position:fixed; inset:0; z-index:50; display:flex; align-items:center;
      justify-content:center; padding:24px; }
  #lightbox[hidden] { display:none; }
  #lb-back { position:absolute; inset:0; background:rgba(4,6,10,.82); }
  #lb-box { position:relative; margin:0; width:min(960px,calc(100vw - 48px)); height:min(760px,calc(100dvh - 48px));
      display:flex; flex-direction:column; background:var(--surface);
      border:1px solid var(--border); border-radius:14px; overflow:hidden; }
  #lb-stage { flex:1; min-height:0; overflow:hidden; display:flex; align-items:center; justify-content:center; background:#000; }
  #lb-img { display:block; width:100%; height:100%; object-fit:contain; }
  #lb-img[hidden] { display:none; }
  #lb-box figcaption { padding:12px 16px 14px; }
  #lb-box figcaption { max-height:58%; overflow:auto; }
  #lb-name { font-weight:650; font-size:0.875rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #lb-meta { color:var(--muted); font-size:0.75rem; font-family:var(--mono); margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #lb-nav { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:8px; }
  #lb-position { min-width:110px; text-align:center; font-size:0.75rem; }
  #lb-snip { color:var(--muted); font-size:0.78125rem; margin-top:8px; max-height:120px;
      overflow:auto; white-space:pre-wrap; }
  #lb-snip:empty { display:none; }
  .lbrow { display:flex; flex-wrap:wrap; gap:10px; margin-top:12px; }
  @media (max-width:640px) {
    header { padding:12px 14px 10px; }
    main { padding:14px 14px 60px; }
    .panel { padding:14px 14px; }
    #results { gap:10px; }
    .thumb { height:110px; }
  }
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>Refseek <span class="lab" id="appversion">Browser</span></h1>
    <div class="sub" id="sub">loading index…</div>
    <div class="bar" id="account_controls" hidden><a href="/users" id="manage_users" hidden>Manage users</a>
      <button id="sign_out" class="ghost">Sign out</button></div>
    <details class="tools"><summary>Tools & reports</summary>
      <a href="/match">Picture ↔ text match</a><a href="/focus">Compare image aspects</a>
      <a href="/style-report">Style adapter comparison</a><a href="/research">Aspect research</a>
    </details>
    <nav class="navtabs" role="tablist" aria-label="Workspace">
      <button id="search_tab" role="tab" aria-selected="true" aria-controls="search_panel">Search</button>
      <button id="library_tab" role="tab" aria-selected="false" aria-controls="library_panel" tabindex="-1">Library & indexing <span class="tabbadge" id="library_badge"></span></button>
      <button id="settings_tab" role="tab" aria-selected="false" aria-controls="settings_panel" tabindex="-1">Settings</button>
    </nav>
  </div>
</header>
<main>
  <section class="panel" id="search_panel" role="tabpanel" aria-labelledby="search_tab">
    <h2>Search</h2>
    <div class="bar">
      <input id="q" type="search" placeholder="Search images, PDFs, docs, code… (Enter)">
    </div>
    <div class="bar">
      <input id="k" type="number" value="24" min="1" max="200" title="results per page">
      <button id="go">Search</button>
      <button class="ghost" id="searchall" hidden>Search all folders</button>
      <button class="ghost" id="clear">Clear</button>
    </div>
    <div id="folderonly" class="hint" hidden></div>
    <details class="refine" id="refine_search"><summary id="refine_label">Refine search · extra terms & exclusions</summary>
    <div class="bar">
      <input id="nx" type="search" placeholder="Exclude… (optional)" title="things you do not want to see">
      <input id="nxw" type="number" value="0.5" min="0" max="2" step="0.1" title="exclude strength: how hard the Exclude term pushes results down">
    </div>
    <div id="wants"></div>
    <div class="bar">
      <button class="ghost" id="addwant" title="match another thing as well">+ want</button>
      <select id="combine" hidden title="how multiple wants combine">
        <option value="avg">match all</option>
        <option value="max">match any</option>
      </select>
    </div>
    <div class="drop small" id="dropneg">
      <img id="negpreview" hidden alt="">
      <span id="negdropmsg">Drop an image here to ignore its look (optional)</span>
      <button id="chooseneg" class="ghost">Choose ignore-image</button>
      <input id="negfile" type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/bmp" hidden>
      <input id="negw" type="number" value="1.0" min="0" max="2" step="0.1" title="ignore-image strength">
      <button id="rmneg" class="ghost" hidden title="remove ignore-image">×</button>
    </div>
    </details>
    <div class="chips" id="chips"></div>
    <details class="refine" id="folderfilters" hidden>
      <summary>Folders and subfolders</summary>
      <p class="hint">Open a folder with +. Include and Skip apply to its subfolders. Deeper rules take precedence. With no Include rules, Refseek searches every folder except those you skip.</p>
      <div class="bar"><button class="ghost" id="collapsefolders">Collapse all</button><button class="ghost" id="clearfolders">Clear folder filters</button><span class="hint" id="foldercount"></span></div>
      <div id="foldertree"></div>
    </details>
    <div class="drop" id="drop">
      <img id="preview" hidden alt="">
      <span id="dropmsg">Drop a full image here, or choose one to search visually</span>
      <button id="chooseimage" class="ghost">Choose image</button>
      <input id="imagefile" type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/bmp" hidden>
      <button id="rmimg" class="ghost" hidden title="remove image">×</button>
    </div>
    <div class="aspectbox" aria-label="Image aspect search">
      <div class="bar" id="focus_modes" role="group" aria-label="Match image by">
        <span class="lab">Match image by:</span>
        <button class="ghost aspect" data-focus="original" aria-pressed="true">Similarity</button>
        <button class="ghost aspect" data-focus="subject" aria-pressed="false">Subject</button>
        <button class="ghost aspect" data-focus="style" aria-pressed="false">Style</button>
        <button class="ghost aspect" data-focus="pose" aria-pressed="false">Pose</button>
        <button class="ghost aspect" data-focus="viewpoint" aria-pressed="false">Viewpoint</button>
        <button class="ghost aspect" data-focus="composition" aria-pressed="false">Composition</button>
        <button class="ghost aspect" data-focus="color" aria-pressed="false">Color palette</button>
        <button class="ghost aspect" data-focus="lighting" aria-pressed="false">Lighting</button>
      </div>
      <div class="bar" id="focus_slider" hidden>
        <label for="focus_strength">Aspect strength</label>
        <input id="focus_strength" type="range" min="0" max="100" value="100" step="5">
        <output id="focus_amount" for="focus_strength">100%</output>
      </div>
      <div class="bar" id="method_row" hidden>
        <label for="aspect_method">Search method</label>
        <select id="aspect_method">
          <option value="baseline">Existing method (v1.7B)</option>
        </select>
        <a id="method_report" href="/style-report" style="color:var(--accent-hi);font-size:12px">Compare style results</a>
      </div>
      <p id="focus_hint">Upload a full image, or open a result and choose “Use as reference”. No cropping needed.</p>
      <p class="hint" id="method_hint" hidden></p>
      <p class="hint" id="style_bundle_hint">Checking installed style adapters…</p>
    </div>
    <div class="bar" id="blendrow" hidden>
      <span class="lab">text</span>
      <input id="blend" type="range" min="0" max="100" value="50" step="5"
             title="balance between the image sample and the text query">
      <span class="lab">image</span>
      <span class="lab" id="blendinfo">image 50 · text 50</span>
    </div>
    <div class="status" id="status"></div>
    <div id="results"></div>
    <div class="pager" id="pager" hidden>
      <button class="ghost" id="prevb">← Prev</button>
      <span id="pageinfo" class="lab"></span>
      <button class="ghost" id="nextb">Next →</button>
    </div>
  </section>
  <section class="panel index" id="library_panel" role="tabpanel" aria-labelledby="library_tab" hidden>
    <h2>Library & indexing</h2>
    <p class="libraryintro">Each scanned folder has its own index and vectors. Update one folder here, or search across them in the Search tab.</p>
    <p class="hint" id="storage_summary"></p>
    <div class="hint" id="dataloc" style="margin:-6px 0 4px"></div>
    <h3>Library folders</h3>
    <div id="rootslist"></div>
    <div class="hint" id="norootsh" hidden>No folders indexed yet — add one below.</div>
    <h3>Add a folder</h3>
    <div class="idxbar">
      <input id="folder" type="text" autocomplete="off" spellcheck="false"
             placeholder="Folder to add & index, e.g. ~/Pictures or /home/dim/Documents">
      <button id="doidx" title="Index this folder (existing folders stay indexed)">Index</button>
    </div>
    <div class="idxrow">
      <label class="check" title="When off, only the folder above is scanned; the rest of the index is left untouched">
        <input type="checkbox" id="rescan"> update the other indexed folders too</label>
      <span class="spacer"></span>
      <button class="ghost" id="reidx" title="Rescan all indexed folders (incremental)">Update all folders</button>
    </div>
    <h3>File types to index</h3>
    <div class="chips" id="kchips" style="margin-bottom:6px"></div>
    <div class="hint">Only ticked types get indexed — update a folder or choose Update all folders to apply changes.
      This choice is saved for future runs.</div>
    <h3>Progress</h3>
    <p class="hint" id="index_backend"></p>
    <div class="idxtrack" id="idxtrack"><div class="idxfill" id="idxfill"></div></div>
    <div class="idxfoot">
      <div class="status" id="idxstatus"></div>
      <button class="ghost" id="cancelb" hidden title="Stop indexing now (embedded work stays saved)">Cancel</button>
    </div>
    <div class="bar">
      <button class="ghost" id="cleanupindex">Clean up old snapshots</button>
      <button class="ghost" id="indexdetails">Show latest indexing details</button>
    </div>
    <p class="hint" id="cleanupresult" aria-live="polite"></p>
    <div id="indexreport" hidden></div>
  </section>
  <section class="panel" id="settings_panel" role="tabpanel" aria-labelledby="settings_tab" hidden>
    <h2 id="settings_title">Settings</h2>
    <p class="hint" id="client_settings_hint" hidden>Appearance and result preferences are saved in this browser.</p>
    <h3>Appearance</h3>
    <div class="settings-grid">
      <label>UI scale <input id="ui_zoom" type="range" min="75" max="200" value="100" step="5"><output id="ui_zoom_value">100%</output></label>
      <label>Text size <input id="ui_text" type="range" min="12" max="24" value="16"><output id="ui_text_value">16</output></label>
      <label>Card width <input id="ui_cards" type="range" min="160" max="380" value="230" step="10"></label>
      <label>Thumbnail height <input id="ui_thumbs" type="range" min="90" max="300" value="155" step="5"></label>
      <label>Results per page <input id="ui_results" type="number" min="1" max="200" value="24"></label>
      <label>Theme <select id="ui_theme"><option value="dark">Dark</option><option value="light">Light</option></select></label>
      <label><input id="ui_compact" type="checkbox"> Compact cards</label>
      <label><input id="ui_scores" type="checkbox" checked> Show scores</label>
      <label id="ui_paths_option"><input id="ui_paths" type="checkbox"> Show full paths</label>
    </div>
    <div id="index-dim-controls"><h3>Indexing</h3>
      <label>CPU preparation workers <input id="prepareworkers" type="number" min="1" step="1" value="2"></label>
      <p class="hint">Defaults to two workers. Your count and file-type selections are remembered for future runs.</p>
      <h3>Library</h3>
      <label>Index dimensions <select id="indexdim"></select></label>
      <p class="hint" id="dimension_note" aria-live="polite"></p>
      <div id="dimension_sizes"></div></div>
  </section>
  <div id="lightbox" hidden>
    <div id="lb-back"></div>
    <figure id="lb-box">
      <div id="lb-stage"><img id="lb-img" alt=""></div>
      <figcaption>
        <div id="lb-nav"><button id="lb-prev" class="ghost">← Previous</button><span id="lb-position"></span><button id="lb-next" class="ghost">Next →</button></div>
        <div id="lb-name"></div>
        <div id="lb-meta"></div>
        <div id="lb-snip"></div>
        <div class="lbrow">
          <button id="lb-open">Open original</button>
          <button id="lb-reference" class="ghost" hidden>Use as reference</button>
          <button id="lb-negative" class="ghost" hidden>Use negative</button>
          <button id="lb-folder" class="ghost" hidden>Search this folder only</button>
          <button class="ghost" id="lb-close">Close</button>
        </div>
      </figcaption>
    </figure>
  </div>
</main>
<script>
const state = { kinds: new Set(['image']), query: "", image: null, negimage: null, rootsSel: new Set(), skipFolders: new Set(), onlyFolder: null,
  idxKinds: new Set(), jobTimer: null, wasRunning: false, lastJobSeen: null, page: 0, focus: "original" };
let searchSerial = 0, searchController = null;
let visibleResults = [], currentPageHasMore = false;
let selectedTab = "search";
const role = document.documentElement.dataset.account;
const guest = role === 'guest';
const tabs = guest ? ['search','settings'] : ['search','library','settings'];
document.getElementById('account_controls').hidden = !role;
document.getElementById('manage_users').hidden = role !== 'owner';
document.getElementById('library_tab').hidden = guest;
document.querySelector('.tools').hidden = guest;
document.getElementById('index-dim-controls').hidden = guest;
document.getElementById('ui_paths_option').hidden = guest;
if (guest) {
  document.getElementById('settings_tab').textContent = 'Client settings';
  document.getElementById('settings_title').textContent = 'Client settings';
  document.getElementById('client_settings_hint').hidden = false;
  document.getElementById('searchall').textContent = 'Search all shared folders';
}
document.getElementById('sign_out').onclick = async () => {
  const response = await fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if (response.ok) location.replace('/login');
};
function showTab(name, focusTab=false) {
  selectedTab = tabs.includes(name) ? name : 'search';
  for (const tab of tabs) {
    const button=document.getElementById(tab+"_tab");
    const active=tab === selectedTab;
    button.setAttribute("aria-selected",String(active));button.tabIndex=active ? 0 : -1;
    document.getElementById(tab+"_panel").hidden=!active;
    if(active && focusTab)button.focus();
  }
  history.replaceState(null,"",selectedTab === "search" ? location.pathname : '#' + selectedTab);
}
for (const [index, name] of tabs.entries()) {
  const button=document.getElementById(name+"_tab");
  button.onclick=()=>showTab(name);
  button.onkeydown=event=>{
    if(["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : (index + tabs.length + (event.key === 'ArrowLeft' ? -1 : 1)) % tabs.length;
      showTab(tabs[next], true);
    }
  };
}
showTab(location.hash.slice(1));
const preferenceIds = ['ui_zoom','ui_text','ui_cards','ui_thumbs','ui_results','ui_theme','ui_compact','ui_scores'];
if (!guest) preferenceIds.push('ui_paths');
function applyAppearance(save=false) {
  const read = id => document.getElementById(id);
  const root = document.documentElement;
  root.style.setProperty('--ui-zoom', Number(read('ui_zoom').value) / 100);
  root.toggleAttribute('data-large-ui', Number(read('ui_zoom').value) * Number(read('ui_text').value) >= 2400);
  root.style.fontSize = read('ui_text').value + 'px';
  root.style.setProperty('--card-width', read('ui_cards').value + 'px');
  root.style.setProperty('--thumb-height', read('ui_thumbs').value + 'px');
  root.dataset.theme = read('ui_theme').value === 'light' ? 'light' : 'dark';
  root.classList.toggle('compact-cards',read('ui_compact').checked);
  root.classList.toggle('hide-scores',!read('ui_scores').checked);
  root.classList.toggle('show-paths',!guest && read('ui_paths').checked);
  read('ui_zoom_value').value = read('ui_zoom').value + '%';
  read('ui_text_value').value = read('ui_text').value;
  read('k').value = read('ui_results').value;
  if (save) {
    const settings = Object.fromEntries(preferenceIds.map(id => [id, read(id).type === 'checkbox' ? read(id).checked : read(id).value]));
    try { localStorage.setItem(appearanceKey, JSON.stringify(settings)); } catch (_) {}
  }
}
const appearanceKey = guest ? 'refseek-guest-appearance' : 'refseek-browser-appearance';
try {
  const saved = JSON.parse(localStorage.getItem(appearanceKey) || '{}');
  for (const id of preferenceIds) {
    const input = document.getElementById(id);
    if (input.type === 'checkbox' && typeof saved[id] === 'boolean') input.checked = saved[id];
    if (input.type !== 'checkbox' && typeof saved[id] === 'string') input.value = saved[id];
  }
} catch (_) {}
for (const id of preferenceIds) document.getElementById(id).oninput = () => applyAppearance(true);
document.getElementById('k').onchange = event => {
  document.getElementById('ui_results').value = event.currentTarget.value;
  applyAppearance(true);
};
applyAppearance();

let indexSettings = null;
function renderDimension(settings) {
  if (!settings) return;
  indexSettings = settings;
  const workers = document.getElementById('prepareworkers');
  workers.max = settings.max_prepare_workers;
  workers.value = settings.prepare_workers;
  workers.disabled = state.wasRunning;
  const select = document.getElementById('indexdim');
  select.replaceChildren(...[...new Set([...settings.choices, settings.dimension])].map(dim => new Option(dim === 768 ? '768 (default)' : dim, dim)));
  select.value = settings.dimension;
  select.disabled = settings.locked || state.wasRunning;
  document.getElementById('dimension_note').textContent = settings.locked
    ? 'This library keeps its existing dimensions.' : 'Choose dimensions before indexing an empty library.';
  const table = document.getElementById('dimension_sizes');
  table.replaceChildren();
  for (const estimate of settings.estimates) {
    const row = document.createElement('div');
    row.textContent = `${estimate.dimension} dimensions · ${formatBytes(estimate.total_bytes)} per 10,000 vectors`
      + (estimate.native_bytes ? ` including ${formatBytes(estimate.native_bytes)} for Style` : '');
    table.append(row);
  }
  const note = document.createElement('p'); note.className = 'hint';
  note.textContent = 'Estimates cover vectors only. Metadata and the recovery copy take additional space.'; table.append(note);
}
document.getElementById('indexdim').onchange = async event => {
  const select = event.currentTarget, dimension = Number(select.value);
  select.disabled = true;
  try {
    const data = await (await fetch('/api/index_settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dimension})})).json();
    if (data.error) throw new Error(data.error);
    renderDimension(data);
  } catch (error) { renderDimension(indexSettings); document.getElementById('dimension_note').textContent = error.message; }
};
let indexPreferenceSave = Promise.resolve(), indexPreferenceError = null;
function saveIndexPreferences(changes) {
  indexPreferenceSave = indexPreferenceSave.then(async () => {
    const response = await fetch('/api/index_settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(changes)});
    const data = await response.json();
    if (data.error) throw new Error(data.error);
    indexPreferenceError = null;
    renderDimension(data);
  }).catch(error => {
    indexPreferenceError = error.message;
    if (indexSettings) renderDimension(indexSettings);
    idxSet('Could not save indexing preferences: ' + error.message, true);
  });
  return indexPreferenceSave;
}
document.getElementById('prepareworkers').onchange = event => saveIndexPreferences({prepare_workers:Number(event.target.value)});
let styleModels = {available: false, research_available: false};
// Explicit choices belong to their aspect; temporary loading fallbacks do not.
const aspectMethodChoices = Object.create(null);
const FOCUS_HINTS = {
  original: "Match the overall image, including subject, style and layout.",
  subject: "Match what is depicted while reducing photo, painting and drawing style differences.",
  style: "Match the visual treatment across different subjects, such as photos, paintings or sketches.",
  pose: "Match broad body poses and gestures across different people. Exact joint positions are not detected.",
  viewpoint: "Match the viewing angle, such as front, side, overhead or from below.",
  composition: "Match the arrangement and framing, such as centered subjects, close-ups or wide scenes.",
  color: "Match the overall color palette and tones across different subjects.",
  lighting: "Match lighting qualities, such as soft daylight, backlighting or dramatic shadows."
};
function imageOnly() { return !!state.image && (state.focus !== "original" || !!state.image.path); }
const ASPECT_METHODS = {
  baseline: "Existing method (v1.7B)",
  style_current: "Current style adapter",
  style_contrastive: "Contrastive style adapter",
  pisco: "Paired-image method — experimental",
  model_aware: "Model-specific method — experimental"
};
function syncFocus() {
  const focused = state.focus !== "original";
  const methodSelect = document.getElementById("aspect_method");
  const available=["baseline"];
  if(state.focus === "style" && styleModels.available) available.push("style_current","style_contrastive");
  if(!guest && styleModels.research_available !== false && ["subject","style"].includes(state.focus)) available.push("pisco");
  if(!guest && styleModels.research_available !== false && ["subject","style","pose"].includes(state.focus)) available.push("model_aware");
  const recommended=state.focus === "style" && styleModels.available
    ? (styleModels.default_method || "style_contrastive") : "baseline";
  const preferred=aspectMethodChoices[state.focus] || recommended;
  const selection=available.includes(preferred) ? preferred : "baseline";
  const optionsKey=available.join(",");
  if(methodSelect.dataset.optionsKey !== optionsKey) {
    methodSelect.replaceChildren(...available.map(value=>{
      const option=document.createElement("option");option.value=value;option.textContent=ASPECT_METHODS[value];return option;
    }));
    methodSelect.dataset.optionsKey=optionsKey;
  }
  methodSelect.value=selection;
  document.getElementById("method_row").hidden=available.length === 1;
  const methodReport=document.getElementById("method_report");
  methodReport.href=state.focus === "style" ? "/style-report" : "/research";
  methodReport.textContent=state.focus === "style" ? "Compare style results" : "Compare aspect results";
  document.getElementById("style_bundle_hint").hidden=state.focus !== "style";
  const methodHint = document.getElementById("method_hint");
  methodHint.hidden = methodSelect.value === "baseline";
  const notes = {
    "style_current/style": "Artist-reference style matching. Keeps the adapter’s saved blend with original similarity.",
    "style_contrastive/style": "Artist-reference style matching with a learned correction. Keeps its saved blend with original similarity.",
    "pisco/subject": "Paired-image experiment: subject AP improved by 1.7 points in the reused pilot, but top-result accuracy fell by 3.1 points. Fresh confirmation pending.",
    "pisco/style": "Paired-image experiment: synthetic appearance transformations did not improve artistic-style AP in the reused pilot (-0.3 points).",
    "model_aware/subject": "Model-specific experiment: subject AP fell by 0.3 points in the reused pilot.",
    "model_aware/style": "Model-specific experiment: style AP improved by 0.2 points in the reused pilot. Fresh confirmation pending.",
    "model_aware/pose": "Model-specific experiment: pose AP fell by 4.1 points and broad-pose AP by 4.6 points. The existing pose method remains recommended."
  };
  methodHint.textContent = notes[methodSelect.value + "/" + state.focus] || "";
  document.querySelectorAll("button[data-focus]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.focus === state.focus)));
  document.getElementById("focus_slider").hidden = !focused;
  document.getElementById("focus_amount").textContent = document.getElementById("focus_strength").value + "%";
  document.getElementById("focus_hint").textContent = FOCUS_HINTS[state.focus] +
    (!state.image ? " Choose a full reference image to use this mode." :
     imageOnly() ? " Image results only; text blend, exclusions and folder filters still apply." : "") +
    (focused ? " 0% restores overall similarity; 100% uses the selected method. Separation is approximate." : "");
  document.querySelectorAll("#chips [data-kind]").forEach(el => {
    const imageResults = imageOnly();
    const on = imageResults ? el.dataset.kind === 'image' : state.kinds.has(el.dataset.kind);
    el.disabled = imageResults;
    el.classList.toggle('on', on);
    el.setAttribute('aria-pressed', String(on));
  });
}
document.querySelectorAll("button[data-focus]").forEach(b => b.onclick = () => {
  state.focus = b.dataset.focus; state.page = 0; syncFocus();
  if (state.image) run();
});
document.getElementById("focus_strength").oninput = syncFocus;
document.getElementById("focus_strength").onchange = () => { state.page = 0; if (state.image) run(); };
document.getElementById("aspect_method").onchange = () => {
  aspectMethodChoices[state.focus] = document.getElementById("aspect_method").value;
  syncFocus(); state.page = 0; if (state.image) run();
};
async function loadStyleModels() {
  const hint = document.getElementById("style_bundle_hint");
  try {
    styleModels = await (await fetch("/api/style_models")).json();
    if (guest) {
      hint.textContent = styleModels.available ? 'Trained Style adapters available.' : 'Trained Style adapters unavailable.';
    } else hint.textContent = styleModels.available
      ? `Style adapters installed · ${styleModels.artists} artists · ${styleModels.images.toLocaleString()} images. Style defaults to the Contrastive adapter; you can choose another method.` + (styleModels.quantization_transfer ? " BF16-trained adapters transferred to Q8; retrieval quality needs fresh evaluation." : "")
      : "Trained style adapters unavailable: " + (styleModels.error || "missing model bundle");
  } catch (error) { hint.textContent = "Could not check the installed style adapters."; }
  const previousMethod = document.getElementById("aspect_method").value;
  syncFocus();
  if (state.image && state.focus === "style" &&
      document.getElementById("aspect_method").value !== previousMethod) {
    state.page = 0; run();
  }
}
loadStyleModels();
let folderChildren = new Map(), openFolders = new Set(), idxKindInit = false;
const IDX_ALL = ["image","pdf","docx","pptx","xlsx","epub","odt","text","code","audio","video"];
const KIND_LABEL = {image:"image",pdf:"pdf",docx:"doc",pptx:"slides",xlsx:"sheet",epub:"book",
  odt:"doc",text:"text",code:"code",audio:"audio",video:"video"};
const ICON = {pdf:"📄",docx:"📝",pptx:"📊",xlsx:"📈",epub:"📚",odt:"📝",text:"📃",code:"⌨️",
  audio:"🎵",video:"🎬",image:"🖼️"};

async function boot() {
  const response = await fetch("/api/status");
  if (response.status === 401 && role) { location.replace('/login'); return; }
  const s = await response.json();
  if (!response.ok) { document.getElementById("sub").textContent = s.error || 'Could not load the library.'; return; }
  document.getElementById('appversion').textContent = s.app_version ? `Browser · ${s.app_version}` : 'Browser';
  if (guest) {
    document.getElementById("sub").textContent = `${s.files.toLocaleString()} shared files`;
  } else {
    let base = `${s.vectors.toLocaleString()} vectors · ${s.files.toLocaleString()} files · ${s.index_settings?.dimension || s.dim || "?"}d · ` +
      `model ${s.model||"?"} via ${s.api_type||"?"} @ ${s.api_url||"?"}` +
      (s.app_version ? ` · app v${s.app_version}` : "");
    if (s.runtime) base += ` · ${s.runtime.backend.toUpperCase()} / ${s.runtime.device_name}`;
    if (s.index_error) base += " · INDEX PROBLEM: " + s.index_error;
    document.getElementById("sub").textContent = base;
    document.getElementById('index_backend').textContent = s.runtime
      ? `Embedding backend · ${s.runtime.backend.toUpperCase()} · ${s.runtime.device_name}` +
        (s.runtime.fallback ? ' · No GPU backend could start. See backend-attempts.json.' : '')
      : 'Embeddings use the configured server. File scanning and preparation use the CPU.';
    document.getElementById("dataloc").textContent = s.data_dir ? "index at " + s.data_dir : "";
    const storage=s.storage || {};
    document.getElementById("storage_summary").textContent = storage.format === "per-folder"
      ? `${storage.folders} independent folder indexes · unchanged folders keep their existing files · automatic checkpoints`
      : "Combined index; it will be split into folder indexes automatically when saved.";
    renderRoots(s.root_stats || []);
    renderCleanup(s.last_cleanup);
    renderDimension(s.index_settings);
    renderIdxKinds(s.kind_filter, s.files);
  }
  renderSearchKinds(s.kinds);
  folderChildren = new Map();
  for (const row of s.folder_tree || []) {
    if (!folderChildren.has(row.parent)) folderChildren.set(row.parent, []);
    folderChildren.get(row.parent).push(row);
  }
  renderFolders();
  syncFocus();
  if (!guest) pollJob();
}
function renderSearchKinds(counts = {}) {
  const box = document.getElementById("chips");
  box.innerHTML = "";
  const lab = document.createElement('span');
  lab.className = 'lab';
  lab.textContent = 'Search file types';
  box.appendChild(lab);
  for (const k of new Set(['image', 'text', ...IDX_ALL, ...Object.keys(counts)])) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'chip';
    el.textContent = (k === 'image' ? 'Images' : k === 'text' ? 'Text' : KIND_LABEL[k] || k) +
      (counts[k] ? ` · ${counts[k].toLocaleString()}` : '');
    el.onclick = () => {
      if (state.kinds.has(k)) state.kinds.delete(k); else state.kinds.add(k);
      syncFocus();
      state.page = 0;
      if (document.getElementById('status').textContent) run();
    };
    el.dataset.kind = k;
    box.appendChild(el);
  }
}

function folderFiltered() { return !!state.onlyFolder || !!state.rootsSel.size || !!state.skipFolders.size; }
function clearFolderFilters() {
  state.onlyFolder = null;
  state.rootsSel.clear();
  state.skipFolders.clear();
}
function syncFolderControls() {
  document.getElementById('searchall').hidden = !folderFiltered();
  document.getElementById('clearfolders').disabled = !folderFiltered();
  const only = document.getElementById('folderonly');
  only.hidden = !state.onlyFolder;
  const folder = state.onlyFolder;
  const label = guest && folder ? folder.split(/[\\/]/).filter(Boolean).pop() : folder;
  only.textContent = label ? `Folder only · ${label}` : '';
  document.getElementById('foldercount').textContent = `${state.rootsSel.size} included · ${state.skipFolders.size} skipped`;
}
function renderFolders(focusPath) {
  const box = document.getElementById('foldertree');
  document.getElementById('folderfilters').hidden = !folderChildren.has(null) && !folderFiltered();
  box.replaceChildren();
  function append(parent, depth) {
    for (const folder of folderChildren.get(parent) || []) {
      const folderLabel = guest || depth ? folder.label : folder.path;
      const row = document.createElement('div');
      row.className = 'folder-filter-row';
      row.style.paddingInlineStart = `min(25%, ${depth * 1.15}rem)`;
      const toggle = document.createElement('button');
      const open = openFolders.has(folder.path);
      toggle.type = 'button'; toggle.className = 'ghost folder-toggle';
      toggle.textContent = open ? '−' : '+';
      toggle.setAttribute('aria-label', `${open ? 'Collapse' : 'Expand'} ${folder.label}`);
      toggle.setAttribute('aria-expanded', String(open));
      if (!folderChildren.has(folder.path)) { toggle.disabled = true; toggle.style.visibility = 'hidden'; }
      toggle.onclick = () => {
        open ? openFolders.delete(folder.path) : openFolders.add(folder.path);
        renderFolders(folder.path);
      };
      row.append(toggle);
      for (const [label, set, other] of [['Include',state.rootsSel,state.skipFolders], ['Skip',state.skipFolders,state.rootsSel]]) {
        const tag = document.createElement('label'); tag.className = 'check';
        const input = document.createElement('input'); input.type = 'checkbox'; input.checked = set.has(folder.path);
        input.setAttribute('aria-label', `${label} ${guest ? folderLabel : folder.path}`);
        input.onchange = () => {
          state.onlyFolder = null;
          if (input.checked) {set.add(folder.path); other.delete(folder.path);} else set.delete(folder.path);
          if (input.checked) for (const checkbox of row.querySelectorAll('input')) if (checkbox !== input) checkbox.checked = false;
          state.page = 0; syncFolderControls();
        };
        tag.append(input, label); row.append(tag);
      }
      const label = document.createElement('span'); label.className = 'folder-label'; label.title = guest ? folderLabel : folder.path;
      label.textContent = `${folderLabel} · ${folder.files} ${folder.files === 1 ? 'file' : 'files'}`;
      row.append(label); box.append(row);
      if (folder.path === focusPath) toggle.focus();
      if (open) append(folder.path, depth + 1);
    }
  }
  append(null, 0);
  document.getElementById('collapsefolders').disabled = !openFolders.size;
  syncFolderControls();
}
function searchAllFolders() { clearFolderFilters(); renderFolders(); state.page = 0; run(); }
document.getElementById('searchall').onclick = searchAllFolders;
document.getElementById('clearfolders').onclick = searchAllFolders;
document.getElementById('collapsefolders').onclick = () => { openFolders.clear(); renderFolders(); };

function blendAlpha() {
  const v = parseInt(document.getElementById("blend").value || "50", 10);
  return Math.min(100, Math.max(0, isNaN(v) ? 50 : v)) / 100;
}
function syncBlendInfo() {
  const v = Math.round(blendAlpha() * 100);
  document.getElementById("blendinfo").textContent = `image ${v} · text ${100 - v}`;
}

function renderIdxKinds(filter, files) {
  if (!idxKindInit) {
    state.idxKinds = new Set(indexSettings?.kinds ?? filter ?? ['image']);
    idxKindInit = true;
  }
  const box = document.getElementById("kchips");
  box.innerHTML = "";
  const lab = document.createElement("span");
  lab.className = "lab";
  lab.textContent = "index types:";
  lab.title = "double-click to select all types";
  lab.ondblclick = () => {
    if (state.wasRunning) return;
    state.idxKinds = new Set(IDX_ALL);
    saveIndexPreferences({kinds:[...state.idxKinds]});
    box.querySelectorAll(".chip").forEach(c => c.classList.add("on"));
  };
  box.appendChild(lab);
  for (const k of IDX_ALL) {
    const el = document.createElement("span");
    el.className = "chip" + (state.idxKinds.has(k) ? " on" : "");
    el.textContent = KIND_LABEL[k] || k;
    el.title = k;
    el.onclick = () => {
      if (state.wasRunning) return;
      if (state.idxKinds.has(k)) state.idxKinds.delete(k); else state.idxKinds.add(k);
      el.classList.toggle("on");
      saveIndexPreferences({kinds:[...state.idxKinds]});
    };
    box.appendChild(el);
  }
}

async function run() {
  const q = document.getElementById("q").value.trim();
  const nx = document.getElementById("nx").value.trim();
  const wants = [...document.querySelectorAll("input.want")].map(el => el.value.trim()).filter(Boolean);
  const combine = document.getElementById("combine").value;
  const k = parseInt(document.getElementById("k").value || "24", 10);
  const nwRaw = parseFloat(document.getElementById("nxw").value);
  const nw = Math.min(2, Math.max(0, isNaN(nwRaw) ? 0.5 : nwRaw));
  const niwRaw = parseFloat(document.getElementById("negw").value);
  const niw = Math.min(2, Math.max(0, isNaN(niwRaw) ? 1.0 : niwRaw));
  const status = document.getElementById("status");
  const requestId = ++searchSerial;
  if (searchController) searchController.abort();
  const kinds = imageOnly() ? ['image'] : [...state.kinds];
  if (!kinds.length) {
    render([]);
    currentPageHasMore = false;
    renderPager(false);
    status.textContent = 'Select at least one search file type.';
    return;
  }
  searchController = new AbortController();
  const signal = searchController.signal;
  status.textContent = "searching…";
  let data;
  try {
    if (state.image || state.negimage) {
      const r = await fetch("/api/search_image", {method: "POST",
        signal,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(Object.assign({k, kind: kinds, not: nx ? [nx] : [],
          not_weight: nw, q: q, wants: wants, combine: combine, img_weight: blendAlpha(),
          focus: state.image ? state.focus : 'original', focus_strength: Number(document.getElementById("focus_strength").value) / 100,
          method: state.image ? document.getElementById("aspect_method").value : 'baseline',
          neg_image: state.negimage || undefined, neg_image_weight: niw,
          roots: [...state.rootsSel], excluded_roots: [...state.skipFolders], only_folder: state.onlyFolder,
          offset: state.page * k}, state.image))});
      data = await r.json();
    } else {
      const params = new URLSearchParams({k, kind: kinds.join(",")});
      if (state.page) params.append("o", state.page * k);
      params.append("not_weight", nw);
      if (q) params.append("q", q);
      for (const w of wants) params.append("q", w);
      if (wants.length) params.append("combine", combine);
      if (nx) params.append("not", nx);
      for (const r of state.rootsSel) params.append("roots", r);
      for (const r of state.skipFolders) params.append("excluded_roots", r);
      if (state.onlyFolder) params.append("only_folder", state.onlyFolder);
      const r = await fetch("/api/search?" + params, {signal});
      data = await r.json();
    }
  } catch (e) {
    if (requestId === searchSerial && e.name !== "AbortError") status.textContent = "Request failed: " + e;
    return;
  }
  if (requestId !== searchSerial) return;
  if (data.error) { status.textContent = data.error; return; }
  render(data.results || []);
  currentPageHasMore = !!data.more;
  renderPager(!!data.more);
  const desc = [q, ...wants].filter(Boolean).map(s => `“${s}”`).join(combine === "max" ? " or " : " + ");
  const hasText = !!(q.trim() || wants.length);
  status.textContent = `${(data.results||[]).length} result(s)` +
    (state.image
      ? (hasText ? ` for ${desc} + image @${Math.round(blendAlpha() * 100)}%` : " · searching by image")
      : desc ? ` for ${desc}` : "") +
    (nx ? ` · excluding “${nx}” @${nw}` : "") +
    (state.negimage ? ` · ignoring image @${niw}` : "") +
    (state.image && state.focus !== "original" ? ` · ${data.label || state.focus}` + (data.method === "style_original" ? "" : ` @${document.getElementById("focus_strength").value}%`) : "") +
    (data.seconds != null ? ` · ${data.seconds}s` : "") +
    (data.warning ? " · " + data.warning : "");
  if (data.method_note) document.getElementById("method_hint").textContent = data.method_note;
  return data;
}

document.getElementById("addwant").onclick = () => {
  const box = document.getElementById("wants");
  const row = document.createElement("div");
  row.className = "bar";
  const input = document.createElement("input");
  input.className = "want";
  input.type = "search";
  input.placeholder = "must also match… (Enter)";
  input.addEventListener("keydown", e => { if (e.key === "Enter") { state.page = 0; run(); } });
  const rm = document.createElement("button");
  rm.className = "ghost";
  rm.textContent = "−";
  rm.title = "remove this want";
  rm.onclick = () => { row.remove(); syncCombine(); };
  row.appendChild(input); row.appendChild(rm);
  box.appendChild(row);
  syncCombine();
  input.focus();
};
function syncCombine() {
  document.getElementById("combine").hidden =
    document.querySelectorAll("input.want").length === 0;
}

function render(items) {
  visibleResults = items;
  const box = document.getElementById("results");
  box.innerHTML = "";
  for (const r of items) {
    const a = document.createElement("a");
    a.className = "card"; a.href = r.open; a.target = "_blank"; a.rel = "noopener";
    a.onclick = e => { e.preventDefault(); openLightbox(r); };
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    if (r.kind === "image") {
      const preview = document.createElement('img');
      preview.alt = '';
      preview.loading = 'lazy';
      preview.decoding = 'async';
      let failures = 0;
      preview.onerror = () => {
        if (!preview.isConnected) return;
        if (failures++ === 0 && r.thumb) {
          const retry = new URL(r.thumb, location.href);
          retry.searchParams.set('_retry', '1');
          setTimeout(() => { if (preview.isConnected) preview.src = retry.href; }, 250);
        } else if (failures <= 2 && r.open) {
          preview.src = r.open;
        } else {
          preview.remove();
          thumb.classList.add('thumb-unavailable');
          thumb.textContent = 'Preview unavailable · Click to open';
        }
      };
      preview.src = r.thumb || r.open;
      thumb.appendChild(preview);
    } else { thumb.textContent = ICON[r.kind] || "📄"; }
    const body = document.createElement("div"); body.className = "body";
    const match = (r.pos != null ? r.pos : r.score) * 100;
    const pen = r.penalty
      ? ` ${r.penalty < 0 ? "+" : "−"}${Math.abs(r.penalty * 100).toFixed(1)}` : "";
    body.innerHTML = `<div class="kind">${KIND_LABEL[r.kind]||r.kind}</div>
      <div class="name">${escapeHtml(r.rel)}</div>
      ${guest ? '' : `<div class="result-path">${escapeHtml(r.path)}</div>`}
      <div class="snip">${escapeHtml(r.snippet || r.title || "")}</div>
      <div class="meta"><span>${r.chunks > 1 ? "chunk " + (r.chunk+1) + "/" + r.chunks : ""}</span>
      <span class="result-score" title="${r.penalty ? `match ${match.toFixed(1)}%, penalty ${pen.trim()}` : "match score"}">${match.toFixed(1)}%${pen}</span></div>`;
    a.appendChild(thumb); a.appendChild(body); box.appendChild(a);
  }
}
function escapeHtml(s) { return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

let lbUrl = null, lbRecord = null, lbScroll = null, lbPaging = false;
function syncLightboxNavigation() {
  const images=visibleResults.filter(r=>r.kind==='image');
  const at=images.findIndex(r=>r.path===lbRecord?.path);
  const image=lbRecord?.kind==='image';
  document.getElementById('lb-nav').hidden=!image;
  document.getElementById('lb-prev').disabled=lbPaging || (at<=0 && state.page===0);
  document.getElementById('lb-next').disabled=lbPaging || (at+1>=images.length && !currentPageHasMore);
  document.getElementById('lb-position').textContent=`Page ${state.page+1} · ${Math.max(0,at)+1} / ${images.length}`;
}
async function navigateLightbox(direction) {
  if(!lbRecord || lbRecord.kind!=='image' || lbPaging) return;
  const images=visibleResults.filter(r=>r.kind==='image');
  const at=images.findIndex(r=>r.path===lbRecord.path), next=at+direction;
  if(next>=0 && next<images.length) {openLightbox(images[next]);return;}
  if((direction>0 && !currentPageHasMore)||(direction<0 && state.page===0))return;
  lbPaging=true;syncLightboxNavigation();
  const originalPage=state.page, originalRecord=lbRecord;
  try {
    let data;
    do {
      state.page+=direction;
      data=await run();
      if(!data) {state.page=originalPage;return;}
      if(!lbRecord || lbRecord!==originalRecord)return;
      const pageImages=visibleResults.filter(r=>r.kind==='image');
      if(pageImages.length){openLightbox(direction>0?pageImages[0]:pageImages[pageImages.length-1]);return;}
    }while((direction>0 && data.more)||(direction<0 && state.page>0));
  }finally{lbPaging=false;if(lbRecord)syncLightboxNavigation();}
}
function openLightbox(r) {
  if(!lbScroll){lbScroll={x:window.scrollX,y:window.scrollY,overflow:document.documentElement.style.overflow};document.documentElement.style.overflow='hidden';}
  lbUrl = r.open;
  lbRecord = r;
  document.getElementById("lb-reference").hidden = r.kind !== "image";
  document.getElementById("lb-negative").hidden = r.kind !== "image";
  document.getElementById("lb-folder").hidden = r.kind !== "image" || !r.folder;
  const img = document.getElementById("lb-img");
  if (r.kind === "image") {
    img.src = r.open; img.hidden = false;
    document.getElementById("lb-snip").textContent = "";
  } else {
    img.hidden = true; img.removeAttribute("src");
    document.getElementById("lb-snip").textContent = r.snippet || r.title || "";
  }
  const name = r.rel || (!guest && r.path) || '';
  document.getElementById("lb-name").textContent = name;
  document.getElementById("lb-name").title = guest ? name : r.path || name;
  document.getElementById("lb-meta").textContent =
    `${r.kind || "?"} · ${((r.score || 0) * 100).toFixed(1)}%` +
    (r.chunks > 1 ? ` · chunk ${(r.chunk || 0) + 1}/${r.chunks}` : "") +
    (!guest && r.path ? ` · ${r.path}` : "");
  document.getElementById("lightbox").hidden = false;
  syncLightboxNavigation();
}
function closeLightbox() {
  document.getElementById("lightbox").hidden = true;
  const img = document.getElementById("lb-img");
  img.hidden = true; img.removeAttribute("src");
  lbUrl = null;
  lbRecord = null;
  if(lbScroll){const saved=lbScroll;lbScroll=null;document.documentElement.style.overflow=saved.overflow;window.scrollTo({left:saved.x,top:saved.y,behavior:'instant'});}
}
document.getElementById('lb-prev').onclick=()=>navigateLightbox(-1);
document.getElementById('lb-next').onclick=()=>navigateLightbox(1);
document.getElementById("lb-close").onclick = closeLightbox;
document.getElementById("lb-back").onclick = closeLightbox;
document.getElementById("lb-open").onclick = () => { if (lbUrl) window.open(lbUrl, "_blank", "noopener"); };
document.getElementById("lb-reference").onclick = () => {
  if (!lbRecord || lbRecord.kind !== "image") return;
  state.image = {path: lbRecord.path};
  const preview = document.getElementById("preview");
  preview.src = lbRecord.thumb; preview.hidden = false;
  preview.alt = "Reference: " + (lbRecord.rel || lbRecord.path);
  document.getElementById("dropmsg").textContent = "Reference: " + (lbRecord.rel || lbRecord.path);
  document.getElementById("rmimg").hidden = false;
  document.getElementById("blendrow").hidden = false;
  closeLightbox(); syncFocus(); syncBlendInfo(); state.page = 0;
  window.scrollTo({top: 0}); run();
};
document.getElementById('lb-negative').onclick = () => {
  if (lbRecord?.kind !== 'image') return;
  state.negimage = {path: lbRecord.path};
  const preview = document.getElementById('negpreview');
  preview.src = lbRecord.thumb; preview.hidden = false;
  document.getElementById('rmneg').hidden = false;
  document.getElementById('negdropmsg').textContent = `Ignoring: ${lbRecord.rel || lbRecord.path}`;
  syncNegativeLabel();
  closeLightbox();
};
document.getElementById('lb-folder').onclick = () => {
  if (lbRecord?.kind !== 'image' || !lbRecord.folder) return;
  clearFolderFilters(); state.onlyFolder = lbRecord.folder; state.page = 0;
  closeLightbox(); renderFolders(); window.scrollTo({top: 0}); run();
};
document.addEventListener("keydown", e => {
  if(document.getElementById('lightbox').hidden)return;
  if(['Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) {
    e.preventDefault();e.stopImmediatePropagation();
    if(e.key==='Escape')closeLightbox();
    else navigateLightbox(['ArrowRight','ArrowDown'].includes(e.key)?1:-1);
  }
},true);

function renderRoots(stats) {
  const box = document.getElementById("rootslist");
  box.innerHTML = "";
  const rows = stats || [];
  document.getElementById("norootsh").hidden = rows.length > 0;
  for (const s of rows) {
    const row = document.createElement("div");
    row.className = "rootrow";
    const nm = document.createElement("span");
    nm.className = "rname"; nm.textContent = s.root; nm.title = s.root;
    if(s.index_dir) {
      const label=document.createElement("div");label.textContent=s.root;nm.textContent="";nm.appendChild(label);
      const disk=document.createElement("div");disk.className="rootstorage";
      disk.textContent=`Saved index: ${formatBytes(s.index_bytes ?? s.size_bytes)} · Indexed originals: ${formatBytes(s.source_bytes)}`;
      disk.title=s.index_dir;nm.appendChild(disk);
    }
    const ct = document.createElement("span");
    ct.className = "rcounts";
    ct.textContent = `${(s.files ?? "?")} files · ${(s.vectors ?? "?")} vectors`;
    const update=document.createElement("button");update.className="ghost updateroot";update.textContent="Update";
    update.title="Scan and update only this folder";update.onclick=()=>{showTab("library");startIdx([s.root],false,true);};
    const verify=document.createElement('button');verify.className='ghost verifyroot';verify.textContent='Verify file contents';
    verify.title='Read and hash every file. Older records without hashes may be re-embedded on the first verification.';
    verify.onclick=()=>{showTab('library');startIdx([s.root],false,true,true);};
    const rm = document.createElement("button");
    rm.className = "danger rmroot"; rm.textContent = "Remove";
    rm.title = "Remove this folder's files from the index";
    rm.onclick = () => forgetRoot(s.root);
    row.appendChild(nm); row.appendChild(ct); row.appendChild(update); row.appendChild(verify); row.appendChild(rm);
    box.appendChild(row);
  }
}
async function forgetRoot(root) {
  if (!confirm("Remove " + root + " and its files from the index?")) return;
  try {
    const r = await fetch("/api/forget", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({folders: [root]})});
    const d = await r.json();
    if (d.error) { idxSet(d.error, true); return; }
    idxSet(`removed ${d.removed} vectors (${d.total} left)`, false);
    boot();
  } catch (e) { idxSet("request failed: " + e, true); }
}

function renderPager(more) {
  const pg = document.getElementById("pager");
  const show = state.page > 0 || more;
  pg.hidden = !show;
  document.getElementById("prevb").disabled = state.page === 0;
  document.getElementById("nextb").disabled = !more;
  document.getElementById("pageinfo").textContent = show ? `page ${state.page + 1}` : "";
}
document.getElementById("prevb").onclick = () => {
  if (state.page > 0) { state.page--; run(); window.scrollTo({top: 0}); }
};
document.getElementById("nextb").onclick = () => { state.page++; run(); window.scrollTo({top: 0}); };

document.getElementById("go").onclick = () => { state.page = 0; run(); };
document.getElementById("clear").onclick = () => { clearImage(); clearNegImage();
  state.page = 0;
  document.getElementById("focus_strength").value = "100"; syncFocus();
  document.getElementById("q").value = ""; document.getElementById("nx").value = "";
  document.getElementById("nxw").value = "0.5";
  document.getElementById("negw").value = "1.0";
  document.getElementById("wants").innerHTML = ""; syncCombine();
  renderPager(false);
  document.getElementById("results").innerHTML = ""; document.getElementById("status").textContent = ""; };
document.getElementById("q").addEventListener("keydown", e => { if (e.key === "Enter") { state.page = 0; run(); } });
document.getElementById("nx").addEventListener("keydown", e => { if (e.key === "Enter") { state.page = 0; run(); } });

function readAsDataURL(f) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("read failed"));
    r.readAsDataURL(f);
  });
}
function setImage(f, dataUrl) {
  const m = /^data:([^;,]+)?;base64,([\s\S]*)$/.exec(dataUrl || "");
  if (!m) {
    document.getElementById("status").innerHTML = '<span class="err">could not read image</span>';
    return false;
  }
  state.image = {base64: m[2], mime: f.type || m[1] || "image/png"};
  const prev = document.getElementById("preview");
  prev.src = dataUrl; prev.hidden = false;
  prev.alt = "Reference: " + f.name;
  document.getElementById("rmimg").hidden = false;
  document.getElementById("blendrow").hidden = false;
  syncBlendInfo();
  document.getElementById("dropmsg").textContent =
    `Reference: ${f.name} (${(f.size / 1048576).toFixed(1)} MB)`;
  syncFocus();
  return true;
}
function clearImage() {
  searchSerial++;
  if (searchController) searchController.abort();
  state.image = null;
  state.focus = "original";
  const prev = document.getElementById("preview");
  prev.hidden = true; prev.removeAttribute("src");
  document.getElementById("rmimg").hidden = true;
  document.getElementById("blendrow").hidden = true;
  document.getElementById("dropmsg").textContent = "Drop a full image here, or choose one to search visually";
  document.getElementById("imagefile").value = "";
  syncFocus();
}

const drop = document.getElementById("drop");
["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", async e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  const status = document.getElementById("status");
  if (f.type && !f.type.startsWith("image/")) {
    status.innerHTML = '<span class="err">not an image file</span>';
    return;
  }
  if (f.size > 25 * 1048576) {
    status.innerHTML = '<span class="err">image too large (max 25 MB)</span>';
    return;
  }
  let dataUrl;
  try {
    dataUrl = await readAsDataURL(f);
  } catch (err) {
    status.innerHTML = '<span class="err">could not read file</span>';
    return;
  }
  if (setImage(f, dataUrl)) { state.page = 0; run(); }
});
document.getElementById("rmimg").onclick = () => {
  clearImage(); state.page = 0;
  document.getElementById("results").innerHTML = "";
  renderPager(false); document.getElementById("status").textContent = "";
  if (document.getElementById("q").value.trim() || document.querySelectorAll("input.want").length) run();
};
document.getElementById("chooseimage").onclick = () => document.getElementById("imagefile").click();
document.getElementById("imagefile").onchange = async e => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 25 * 1048576) { document.getElementById("status").textContent = "Image too large (max 25 MB)"; return; }
  try {
    if (setImage(file, await readAsDataURL(file))) { state.page = 0; run(); }
  } catch (err) { document.getElementById("status").textContent = "Could not read image: " + err; }
  e.target.value = "";
};
document.getElementById("blend").oninput = syncBlendInfo;

function syncNegativeLabel() {
  document.getElementById('refine_label').textContent = state.negimage
    ? 'Refine search · negative image active' : 'Refine search · extra terms & exclusions';
}
function setNegImage(f, dataUrl) {
  const m = /^data:([^;,]+)?;base64,([\s\S]*)$/.exec(dataUrl || "");
  if (!m) {
    document.getElementById("status").innerHTML = '<span class="err">could not read image</span>';
    return false;
  }
  state.negimage = {base64: m[2], mime: f.type || m[1] || "image/png"};
  const prev = document.getElementById("negpreview");
  prev.src = dataUrl; prev.hidden = false;
  document.getElementById("rmneg").hidden = false;
  document.getElementById("negdropmsg").textContent =
    `ignoring: ${f.name} (${(f.size / 1048576).toFixed(1)} MB)`;
  syncNegativeLabel();
  return true;
}
function clearNegImage() {
  state.negimage = null;
  const prev = document.getElementById("negpreview");
  prev.hidden = true; prev.removeAttribute("src");
  document.getElementById("rmneg").hidden = true;
  document.getElementById("negdropmsg").textContent = "Drop an image here to ignore its look (optional)";
  syncNegativeLabel();
}
const dropneg = document.getElementById("dropneg");
["dragenter","dragover"].forEach(ev => dropneg.addEventListener(ev, e => {
  e.preventDefault(); dropneg.classList.add("over"); }));
["dragleave","drop"].forEach(ev => dropneg.addEventListener(ev, e => {
  e.preventDefault(); dropneg.classList.remove("over"); }));
dropneg.addEventListener("drop", async e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  const status = document.getElementById("status");
  if (f.type && !f.type.startsWith("image/")) {
    status.innerHTML = '<span class="err">not an image file</span>';
    return;
  }
  if (f.size > 25 * 1048576) {
    status.innerHTML = '<span class="err">image too large (max 25 MB)</span>';
    return;
  }
  let dataUrl;
  try {
    dataUrl = await readAsDataURL(f);
  } catch (err) {
    status.innerHTML = '<span class="err">could not read file</span>';
    return;
  }
  setNegImage(f, dataUrl);
});
document.getElementById("rmneg").onclick = () => { clearNegImage(); };
document.getElementById("chooseneg").onclick = () => document.getElementById("negfile").click();
document.getElementById("negfile").onchange = async e => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 25 * 1048576) { document.getElementById("status").textContent = "Ignore-image too large (max 25 MB)"; return; }
  try { setNegImage(file, await readAsDataURL(file)); }
  catch (err) { document.getElementById("status").textContent = "Could not read ignore-image: " + err; }
  e.target.value = "";
};
document.getElementById("blend").oninput = syncBlendInfo;

function idxSet(text, err) {
  const el = document.getElementById("idxstatus");
  el.innerHTML = err ? '<span class="err">' + escapeHtml(text) + "</span>" : escapeHtml(text);
}
function idxBusy(on) {
  for (const id of ["doidx", "reidx", "cleanupindex"]) document.getElementById(id).disabled = on;
  document.querySelectorAll(".rmroot,.updateroot,.verifyroot").forEach(b => { b.disabled = on; });
  document.getElementById("library_badge").textContent=on ? "Indexing…" : "";
  document.getElementById("cancelb").hidden = !on;
  document.getElementById('indexdim').disabled = on || !indexSettings || indexSettings.locked;
  document.getElementById('prepareworkers').disabled = on;
}
function formatBytes(value) {
  if (!Number.isFinite(value)) return 'unknown';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (value >= 1000 && i < units.length - 1) { value /= 1000; i++; }
  return `${value.toFixed(i ? 1 : 0)} ${units[i]}`;
}
function renderCleanup(report) {
  const line = document.getElementById('cleanupresult');
  if (!report) { line.textContent = 'Refseek keeps the active index and one recovery copy.'; return; }
  if (report.error) { line.textContent = `Cleanup failed: ${report.error}`; return; }
  line.textContent = report.removed || report.freed_bytes || report.failed
    ? `Last cleanup removed ${report.removed} old snapshots and freed ${formatBytes(report.freed_bytes)}.` +
      (report.failed ? ` ${report.failed} could not be fully removed.` : '')
    : 'Last cleanup found no old snapshots to remove.';
}
document.getElementById('cleanupindex').onclick = async event => {
  const button = event.currentTarget; button.disabled = true;
  document.getElementById('cleanupresult').textContent = 'Checking the active and recovery indexes…';
  try { renderCleanup(await (await fetch('/api/cleanup_snapshots',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json()); }
  catch (error) { renderCleanup({error:String(error)}); }
  finally { button.disabled = state.wasRunning; }
};
async function showIndexDetails() {
  const box = document.getElementById('indexreport');
  box.textContent = 'Loading indexing report…';
  try {
    const report = await (await fetch('/api/index_report')).json();
    if (report.error) { box.textContent = report.error; return; }
    box.textContent = `Last scan ${report.cancelled ? 'cancelled' : 'finished'} in ${fmtElapsed(report.seconds)} · ${report.indexed_files} indexed · ${report.unchanged} reused · ${report.dropped} removed from the index.`;
    if (report.warnings?.length) {
      const details = document.createElement('details'), heading = document.createElement('summary');
      heading.textContent = `Warnings (${report.warnings.length})`; details.append(heading);
      const list = document.createElement('ul');
      for (const [path, warning] of report.warnings) {
        const item = document.createElement('li'); item.textContent = `${path} · ${warning}`; list.append(item);
      }
      details.append(list); box.append(details);
    }
  } catch (error) { box.textContent = String(error); }
}
document.getElementById('indexdetails').onclick = event => {
  const box = document.getElementById('indexreport'); box.hidden = !box.hidden;
  event.currentTarget.textContent = box.hidden ? 'Show latest indexing details' : 'Hide latest indexing details';
  if (!box.hidden) showIndexDetails();
};
async function startIdx(folders, rescan, onlyThisFolder=false, verifyContents=false) {
  await indexPreferenceSave;
  if (indexPreferenceError) await saveIndexPreferences({kinds:[...state.idxKinds],prepare_workers:Number(document.getElementById('prepareworkers').value)});
  if (indexPreferenceError) {idxSet('Could not save indexing preferences: ' + indexPreferenceError,true);return;}
  if (!idxKindInit) {
    idxSet('Wait for the library to load before indexing.', true);
    return;
  }
  if (state.idxKinds.size === 0) {
    idxSet("tick at least one file type under 'index types' (double-click the label for all)", true);
    return;
  }
  const also = !onlyThisFolder && (rescan || document.getElementById("rescan").checked);
  try {
    const r = await fetch("/api/index", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({folders: folders, media: "auto", rescan: also,
        kinds: [...state.idxKinds], verify_contents:verifyContents})});
    const d = await r.json();
    if (d.error) { idxSet(d.error, true); return; }
    idxBusy(true);
    pollJob();
  } catch (e) { idxSet("request failed: " + e, true); }
}
function fmtElapsed(s) {
  s = Math.max(0, Math.round(s || 0));
  return s < 60 ? s + "s" : Math.floor(s / 60) + "m " + (s % 60) + "s";
}
async function pollJob() {
  let d;
  try { d = await (await fetch("/api/job")).json(); } catch (e) { return; }
  const track = document.getElementById("idxtrack");
  const fill = document.getElementById("idxfill");
  if (d.running) {
    state.wasRunning = true;
    idxBusy(true);
    const pct = (typeof d.pct === "number") ? d.pct : null;
    document.getElementById("library_badge").textContent=pct === null ? "Indexing…" : `${pct}%`;
    track.style.display = "block";
    track.classList.toggle("indet", pct === null);
    if (pct !== null) fill.style.width = pct + "%";
    const names = (d.folders || []).map(f => f.split(/[\\/]/).filter(Boolean).pop()).join(", ");
    let line = "indexing " + names;
    if (d.stage === "embedding" && d.progress) line += " — " + d.progress.trim();
    else if (d.stage === "scanning") line += " — scanning folders…";
    else if (d.stage === "preparing") line += " — reading files, building chunks…";
    else line += d.progress ? " — " + d.progress.trim() : " — starting…";
    if (d.started) {
      const elapsed = Date.now() / 1000 - d.started;
      line += ' · elapsed ' + fmtElapsed(elapsed);
      if (d.stage === 'embedding' && d.files_done > 0 && elapsed > 0) line += ` · ${(d.files_done / elapsed).toFixed(1)} files/s overall`;
    }
    idxSet(line, false);
    if (!state.jobTimer) state.jobTimer = setInterval(pollJob, 1200);
    return;
  }
  track.style.display = "none";
  const had = state.wasRunning;
  state.wasRunning = false;
  if (state.jobTimer) { clearInterval(state.jobTimer); state.jobTimer = null; }
  const seen = (d.finished != null) ? d.finished : 0;
  if (!had && state.lastJobSeen === seen) return;
  state.lastJobSeen = seen;
  idxBusy(false);
  if (d.error) idxSet("index failed: " + d.error, true);
  else if (d.result) {
    const r = d.result;
    let msg = (r.cancelled ? "cancelled, saved progress: " : "indexed: ") +
      `+${r.vectors_added} vectors · ${r.indexed_files} files · ` +
      `${r.unchanged} unchanged · ${r.dropped} removed · ${r.total} total in ${r.seconds}s`;
    if (r.vectors_added && r.seconds) msg += ` · ${(r.vectors_added / r.seconds).toFixed(1)} vec/s`;
    if (r.kinds_excluded) msg += ` · ${r.kinds_excluded} files skipped by type filter`;
    if (r.warnings?.length) msg += " · " + r.warnings.join("; ");
    if (r.skipped_reasons && r.skipped_reasons.length)
      msg += " · skipped: " + r.skipped_reasons.map(x => x.count + " x " + x.why).join(", ");
    if (r.api_errors && r.api_errors.length) msg += " · api errors: " + r.api_errors.length;
    idxSet(msg, false);
    if (!document.getElementById('indexreport').hidden) showIndexDetails();
  }
  boot();
}
document.getElementById("doidx").onclick = () => {
  const f = document.getElementById("folder").value.trim();
  if (!f) { idxSet("type a folder path first", true); return; }
  idxSet("starting index…", false);
  startIdx([f]);
};
document.getElementById("folder").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("doidx").click();
});
document.getElementById("reidx").onclick = () => {
  idxSet("starting re-index…", false);
  startIdx([], true);
};
document.getElementById("cancelb").onclick = async () => {
  try {
    const d = await (await fetch("/api/cancel", {method: "POST"})).json();
    if (d.error) { idxSet(d.error, true); return; }
    idxSet("cancelling — stopping queued work; completed files stay saved…", false);
  } catch (e) { idxSet("request failed: " + e, true); }
};

renderSearchKinds();
syncFocus();
boot();
</script>
</body>
</html>
"""

MATCH_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Picture ↔ text match</title>
<style>
  :root {
    color-scheme: light;
    --bg:#f4f1ea; --bg2:#ece7db;
    --card:#fffdf8; --ink:#23202a; --muted:#6f6a7d; --faint:#a09ab0;
    --line:#e7e1d3; --accent:#6d28d9; --accent2:#0d9488; --accent-ink:#fff;
    --gold:#b45309; --danger:#dc2626;
    --sans:ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;
    --mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.55 var(--sans); color:var(--ink);
    background:radial-gradient(1200px 500px at 15% -5%, #e9e2ff 0%, transparent 60%),
               radial-gradient(1000px 480px at 95% 0%, #cbf3ec 0%, transparent 55%),
               linear-gradient(var(--bg), var(--bg2));
    min-height:100vh; }
  header { padding:26px 22px 6px; }
  .wrap { max-width:980px; margin:0 auto; }
  .eyebrow { display:inline-block; font-size:12px; font-weight:700; letter-spacing:.12em;
    text-transform:uppercase; color:var(--accent); background:#ede6ff;
    border:1px solid #d9ccff; padding:4px 12px; border-radius:999px; }
  h1 { font-size:30px; margin:10px 0 2px; letter-spacing:-.02em; }
  .sub { color:var(--muted); font-size:13px; font-family:var(--mono); }
  .back { display:inline-block; margin-top:8px; font-size:13px; color:var(--accent);
    text-decoration:none; font-weight:600; }
  .back:hover { text-decoration:underline; }
  main { padding:16px 22px 70px; max-width:980px; margin:0 auto; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:20px;
    padding:22px; box-shadow:0 12px 32px rgba(60,40,120,.10); margin-bottom:18px; }
  .grid { display:grid; grid-template-columns:340px 1fr; gap:20px; }
  @media (max-width:760px) { .grid { grid-template-columns:1fr; } h1 { font-size:24px; } }
  .drop { border:2px dashed #c9c1e8; border-radius:16px; background:#faf8ff;
    min-height:280px; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:10px; padding:18px; text-align:center; color:var(--muted);
    cursor:pointer; transition:border-color .15s, background .15s; }
  .drop.over { border-color:var(--accent); background:#f1eaff; color:var(--ink); }
  .drop img { max-width:100%; max-height:260px; border-radius:12px;
    box-shadow:0 6px 18px rgba(60,40,120,.18); }
  .drop .big { font-size:40px; }
  .fname { font-size:12.5px; font-family:var(--mono); color:var(--muted); word-break:break-all; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  .btn { padding:12px 22px; border-radius:12px; border:1px solid var(--accent);
    background:var(--accent); color:var(--accent-ink); font-weight:700; font-size:14.5px;
    cursor:pointer; font-family:var(--sans); }
  .btn:hover { filter:brightness(1.08); }
  .btn:disabled { opacity:.45; cursor:default; }
  .btn.ghost { background:transparent; color:var(--ink); border-color:var(--line); font-weight:600; }
  .btn.ghost:hover { border-color:var(--faint); }
  .btn.file { padding:9px 16px; font-size:13.5px; }
  input[type=file] { display:none; }
  textarea { width:100%; min-height:190px; resize:vertical; padding:13px 15px;
    border-radius:14px; border:1px solid var(--line); background:#fff; color:var(--ink);
    font:14px/1.6 var(--sans); outline:none; }
  textarea:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(109,40,217,.15); }
  .hint { color:var(--muted); font-size:12.5px; margin-top:8px; }
  .count { font-family:var(--mono); font-size:12px; color:var(--muted); }
  .status { min-height:22px; font-size:13.5px; color:var(--muted); margin:12px 0 4px; }
  .status.err { color:var(--danger); font-weight:600; }
  .spinner { display:inline-block; width:14px; height:14px; border-radius:50%;
    border:2px solid #d9ccff; border-top-color:var(--accent); vertical-align:-2px;
    animation:spin 0.8s linear infinite; margin-right:8px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #results { display:flex; flex-direction:column; gap:12px; margin-top:14px; }
  #results:empty { display:none; }
  .result { border:1px solid var(--line); border-radius:14px; padding:13px 15px 14px;
    background:#fff; }
  .result.top { border-color:#f0c987; background:linear-gradient(#fffdf4,#fff); }
  .rhead { display:flex; gap:10px; align-items:baseline; }
  .rank { font:700 12px var(--mono); color:#fff; background:var(--accent2);
    border-radius:999px; padding:2px 10px; white-space:nowrap; }
  .result.top .rank { background:var(--gold); }
  .rtext { flex:1; font-weight:600; word-break:break-word; }
  .pct { font:700 22px var(--mono); white-space:nowrap; }
  .bar { height:10px; border-radius:6px; background:#efece4; margin-top:10px; overflow:hidden; }
  .fill { height:100%; border-radius:6px; width:0;
    background:linear-gradient(90deg, var(--accent2), var(--accent));
    transition:width .6s ease; }
  .result.top .fill { background:linear-gradient(90deg, #f59e0b, #ea580c); }
  .meta { display:flex; justify-content:space-between; gap:10px; margin-top:6px;
    font:11.5px var(--mono); color:var(--faint); }
  .foot { color:var(--faint); font-size:12px; text-align:center; margin-top:6px; }
  #pickmsg { font-size:13px; }
  .divider { border:0; border-top:1px solid var(--line); margin:20px 0 16px; }
  h2.sec { font-size:15px; margin:0 0 2px; }
  .drop2 { border:2px dashed #c9c1e8; border-radius:14px; background:#faf8ff;
    min-height:86px; display:flex; align-items:center; justify-content:center; gap:10px;
    padding:16px; text-align:center; color:var(--muted); font-size:13.5px; cursor:pointer;
    transition:border-color .15s, background .15s; margin-top:10px; }
  .drop2.over { border-color:var(--accent); background:#f1eaff; color:var(--ink); }
  .drop2 .big { font-size:26px; }
  .thumbs { display:flex; gap:12px; flex-wrap:wrap; margin-top:14px; }
  .thumbs:empty { display:none; }
  .thumb { position:relative; width:108px; }
  .thumb img { width:108px; height:108px; object-fit:cover; border-radius:12px; display:block;
    box-shadow:0 4px 14px rgba(60,40,120,.20); background:#eee; }
  .thumb .x { position:absolute; top:-9px; right:-9px; width:24px; height:24px; border-radius:50%;
    border:1px solid var(--line); background:#fff; color:var(--danger); font-weight:700;
    font-size:14px; line-height:1; cursor:pointer; padding:0; }
  .thumb .x:hover { background:#fee2e2; }
  .thumb .nm { font:10.5px var(--mono); color:var(--muted); margin-top:4px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .rthumb { width:64px; height:64px; object-fit:cover; border-radius:10px; flex:none;
    box-shadow:0 2px 8px rgba(60,40,120,.18); background:#eee; }
  .kbadge { font:700 11px var(--mono); color:var(--accent); background:#ede6ff;
    border-radius:999px; padding:2px 10px; white-space:nowrap; }
  .kbadge.pic { color:var(--accent2); background:#d9f5f0; }
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="eyebrow">second gui · direct check</span>
    <h1>Picture ↔ text match</h1>
    <div class="sub" id="sub">give it a picture, add words or more pictures, see how many % each one matches</div>
    <a class="back" href="/">← back to semantic search</a>
  </div>
</header>
<main>
  <div class="card">
    <div class="grid">
      <div>
        <div class="drop" id="drop" role="button" tabindex="0" title="click to pick a picture, or drop one here">
          <span class="big" id="dropicon">🖼️</span>
          <img id="preview" hidden alt="">
          <div id="pickmsg"><b>Drop a picture here</b><br>or click to choose one (max 25 MB)</div>
          <div class="fname" id="fname"></div>
        </div>
        <div class="row" style="margin-top:10px">
          <label class="btn ghost file" for="file">Choose picture…</label>
          <input type="file" id="file" accept="image/*">
          <button class="btn ghost file" id="rmimg" hidden>Remove</button>
        </div>
      </div>
      <div>
        <label for="texts" style="font-weight:700">What should the picture match?</label>
        <div class="count" id="count">0 lines</div>
        <textarea id="texts" placeholder="one idea per line, e.g.&#10;a sunset over the ocean&#10;a black cat on a sofa&#10;an invoice or receipt&#10;&#10;Enter = score"></textarea>
        <div class="row" style="margin-top:12px">
          <button class="btn" id="go">Check %</button>
          <button class="btn ghost" id="clear">Clear</button>
        </div>
        <div class="hint">Each non-empty line is scored separately against the same picture and ranked best-first, together with any pictures you add below. Nothing is added to your index.</div>
      </div>
    </div>
    <hr class="divider">
    <h2 class="sec">…or compare with other pictures</h2>
    <div class="count" id="ocount">0 pictures (max 10)</div>
    <div class="drop2" id="drop2" role="button" tabindex="0" title="click to add pictures, or drop them here">
      <span class="big">📷</span>
      <span><b>Drop other pictures here</b><br>or click to choose (each max 25 MB)</span>
    </div>
    <div class="row" style="margin-top:10px">
      <label class="btn ghost file" for="file2">Add pictures…</label>
      <input type="file" id="file2" accept="image/*" multiple>
      <button class="btn ghost file" id="rmothers" hidden>Remove all</button>
    </div>
    <div class="thumbs" id="thumbs"></div>
    <div class="status" id="status"></div>
    <div id="results" aria-live="polite"></div>
  </div>
  <div class="foot" id="foot">embeddings run locally · pictures are downscaled before scoring</div>
</main>
<script>
const state = { image: null, others: [] };
const $ = id => document.getElementById(id);

async function boot() {
  try {
    const s = await (await fetch("/api/status")).json();
    if (s && (s.model || s.api_url))
      $("foot").textContent =
        `model ${s.model || "?"} via ${s.api_type || "?"} @ ${s.api_url || "?"} · embeddings run locally · pictures are downscaled before scoring`;
  } catch (e) {}
}
function escapeHtml(s) { return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function lines() {
  return $("texts").value.split("\n").map(s => s.trim()).filter(Boolean);
}
function syncCount() {
  const n = lines().length;
  $("count").textContent = n + (n === 1 ? " line" : " lines") + (n > 20 ? " · only the first 20 are scored" : "");
}
function setStatus(html, isErr) {
  const el = $("status");
  el.className = "status" + (isErr ? " err" : "");
  el.innerHTML = html || "";
}
function readAsDataURL(f) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("read failed"));
    r.readAsDataURL(f);
  });
}
function setImage(f, dataUrl) {
  const m = /^data:([^;,]+)?;base64,([\s\S]*)$/.exec(dataUrl || "");
  if (!m) { setStatus("could not read that image", true); return false; }
  state.image = { base64: m[2], mime: f.type || m[1] || "image/png" };
  const prev = $("preview");
  prev.src = dataUrl; prev.hidden = false;
  $("dropicon").style.display = "none";
  $("pickmsg").innerHTML = "<b>looking good</b><br>drop another to replace it";
  $("fname").textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
  $("rmimg").hidden = false;
  setStatus("");
  return true;
}
function clearImage() {
  state.image = null;
  const prev = $("preview");
  prev.hidden = true; prev.removeAttribute("src");
  $("dropicon").style.display = "";
  $("pickmsg").innerHTML = "<b>Drop a picture here</b><br>or click to choose one (max 25 MB)";
  $("fname").textContent = "";
  $("rmimg").hidden = true;
  $("file").value = "";
}
async function takeFile(f) {
  if (!f) return;
  if (f.type && !f.type.startsWith("image/")) { setStatus("that file is not an image", true); return; }
  if (f.size > 25 * 1048576) { setStatus("image too large (max 25 MB)", true); return; }
  try {
    const url = await readAsDataURL(f);
    if (setImage(f, url)) run();
  } catch (e) { setStatus("could not read file", true); }
}
const drop = $("drop");
["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) takeFile(f);
});
drop.addEventListener("click", () => $("file").click());
drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") $("file").click(); });
$("file").addEventListener("change", e => {
  const f = e.target.files && e.target.files[0];
  if (f) takeFile(f);
});
$("rmimg").onclick = e => { e.stopPropagation(); clearImage(); };

function syncOCount() {
  const n = state.others.length;
  $("ocount").textContent = n + (n === 1 ? " picture" : " pictures") + " (max 10)" + (n >= 10 ? " · only the first 10 are scored" : "");
  $("rmothers").hidden = n === 0;
}
function renderOthers() {
  const box = $("thumbs");
  box.innerHTML = "";
  state.others.forEach((o, i) => {
    const el = document.createElement("div");
    el.className = "thumb";
    const im = document.createElement("img");
    im.src = o.dataUrl; im.alt = o.name;
    const nm = document.createElement("div");
    nm.className = "nm"; nm.textContent = o.name; nm.title = o.name;
    const x = document.createElement("button");
    x.className = "x"; x.textContent = "×"; x.title = "remove this picture";
    x.onclick = e => { e.stopPropagation(); state.others.splice(i, 1); syncOCount(); renderOthers(); };
    el.appendChild(im); el.appendChild(x); el.appendChild(nm);
    box.appendChild(el);
  });
  syncOCount();
}
async function takeOtherFiles(files) {
  const list = [...(files || [])];
  if (!list.length) return;
  let added = 0;
  for (const f of list) {
    if (state.others.length >= 10) { setStatus("max 10 other pictures — extra ones ignored", true); break; }
    if (f.type && !f.type.startsWith("image/")) { setStatus(`"${f.name}" is not an image — skipped`, true); continue; }
    if (f.size > 25 * 1048576) { setStatus(`"${f.name}" too large (max 25 MB) — skipped`, true); continue; }
    try {
      const dataUrl = await readAsDataURL(f);
      const m = /^data:([^;,]+)?;base64,([\s\S]*)$/.exec(dataUrl || "");
      if (!m) { setStatus(`could not read "${f.name}" — skipped`, true); continue; }
      state.others.push({ base64: m[2], mime: f.type || m[1] || "image/png", name: f.name || `picture ${state.others.length + 1}`, dataUrl });
      added++;
    } catch (e) { setStatus(`could not read "${f.name}" — skipped`, true); }
  }
  renderOthers();
  if (added) run();
}
const drop2 = $("drop2");
["dragenter","dragover"].forEach(ev => drop2.addEventListener(ev, e => { e.preventDefault(); drop2.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop2.addEventListener(ev, e => { e.preventDefault(); drop2.classList.remove("over"); }));
drop2.addEventListener("drop", e => { if (e.dataTransfer.files) takeOtherFiles(e.dataTransfer.files); });
drop2.addEventListener("click", () => $("file2").click());
drop2.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") $("file2").click(); });
$("file2").addEventListener("change", e => { takeOtherFiles(e.target.files); $("file2").value = ""; });
$("rmothers").onclick = () => { state.others = []; renderOthers(); };
$("texts").addEventListener("input", syncCount);
$("texts").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(); }
});
$("clear").onclick = () => {
  clearImage(); state.others = []; renderOthers(); $("texts").value = "";
  $("results").innerHTML = ""; setStatus(""); syncCount(); $("texts").focus();
};
$("go").onclick = () => run();

function bestLabel(it) {
  if (!it) return "";
  return (it.kind === "image" ? (it.name || "a picture") : (it.text || ""));
}

async function run() {
  const ts = lines().slice(0, 20);
  const imgs = state.others.slice(0, 10).map(o => ({ base64: o.base64, mime: o.mime, name: o.name }));
  if (!state.image) { setStatus("pick a picture first ☝️", true); return; }
  if (!ts.length && !imgs.length) { setStatus("write some text or add another picture first ✏️📷", true); $("texts").focus(); return; }
  const parts = [];
  if (ts.length) parts.push(ts.length + (ts.length === 1 ? " line" : " lines"));
  if (imgs.length) parts.push(imgs.length + (imgs.length === 1 ? " picture" : " pictures"));
  setStatus('<span class="spinner"></span>scoring ' + parts.join(" + ") + "…");
  $("results").innerHTML = "";
  $("go").disabled = true;
  try {
    const r = await fetch("/api/match", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: state.image, texts: ts, images: imgs }) });
    const data = await r.json();
    if (data.error) { setStatus(escapeHtml(data.error), true); return; }
    render(data.results || []);
    const n = (data.results || []).length;
    setStatus(n ? `done · best match <b>${escapeHtml(bestLabel(data.results[0]))}</b>` : "no scores came back", false);
  } catch (e) { setStatus("request failed: " + escapeHtml(String(e)), true); }
  finally { $("go").disabled = false; }
}

function render(items) {
  const box = $("results");
  box.innerHTML = "";
  items.forEach((it, i) => {
    const pct = Math.max(-100, Math.min(100, Number(it.pct) || 0));
    const shown = pct.toFixed(1) + "%";
    const isImg = it.kind === "image";
    const label = isImg ? (it.name || "picture") : (it.text || "");
    const el = document.createElement("div");
    el.className = "result" + (i === 0 ? " top" : "");
    const badge = `<span class="kbadge${isImg ? " pic" : ""}">${isImg ? "picture" : "text"}</span>`;
    const thumb = (isImg && state.others[it.idx] && state.others[it.idx].dataUrl)
      ? `<img class="rthumb" src="${state.others[it.idx].dataUrl}" alt="">` : "";
    el.innerHTML =
      `<div class="rhead"><span class="rank">${i === 0 ? "★ best" : "#" + (i + 1)}</span>` +
      thumb + `<span class="rtext">${escapeHtml(label)}</span>` + badge +
      `<span class="pct">${shown}</span></div>` +
      `<div class="bar"><div class="fill" style="width:0%"></div></div>` +
      `<div class="meta"><span>${i === 0 && items.length > 1 ? "closest to your picture" : "&nbsp;"}</span>` +
      `<span>cosine × 100</span></div>`;
    box.appendChild(el);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.querySelector(".fill").style.width = Math.max(0, Math.min(100, pct)) + "%";
    }));
  });
}

syncCount();
renderOthers();
boot();
</script>
</body>
</html>
"""

_FILE_RE = re.compile(r"indexing (\d+)/(\d+) files \((\d+)%\) · ([\d.]+) files/s · ETA (\S+)")
_EMB_RE = re.compile(r"embedding (\d+)/(\d+) items \((\d+)%\), (\d+)/(\d+) files · ([\d.]+) items/s · ETA (\S+)")


class _JobStream:
    """callable + file-like sink: routes indexer progress lines into the job dict."""

    def __init__(self, job, console=None):
        self.job = job
        self.console = console
        self._carry = ""
        self._last_emit = 0.0

    def _note(self, s):
        s = str(s).strip().strip("\r").strip()
        if not s:
            return
        job = self.job
        job["progress"] = s
        m = _EMB_RE.search(s)
        if m:
            job["stage"] = "embedding"
            job["done"], job["items_total"] = int(m.group(1)), int(m.group(2))
            job["pct"] = int(m.group(3))
            job["files_done"], job["files_total"] = int(m.group(4)), int(m.group(5))
            job["rate"] = float(m.group(6))
            job["eta"] = m.group(7)
        elif (files := _FILE_RE.search(s)):
            job.update(stage='embedding', done=int(files[1]), items_total=int(files[2]), pct=int(files[3]),
                       files_done=int(files[1]), files_total=int(files[2]), rate=float(files[4]), eta=files[5])
            m = files
        elif s.startswith("scanning"):
            job["stage"] = "scanning"
        elif s.startswith("preparing") or s.startswith("prepared"):
            job["stage"] = "preparing"
        self._console_emit(s, bool(m))

    def _console_emit(self, s, is_embedding):
        """Mirror job progress to the server console (the real stdout, not the
        swapped stream). Phase lines always; embedding lines at most ~every 10s."""
        if self.console is None or os.environ.get("EG_QUIET"):
            return
        now = time.time()
        if not is_embedding or now - self._last_emit >= 10:
            if is_embedding:
                self._last_emit = now
            try:
                print(f"[index] {s}", file=self.console, flush=True)
            except Exception:
                pass

    def __call__(self, s):
        self._note(s)
        return len(str(s))

    def write(self, s):
        data = self._carry + s
        parts = data.split("\n")
        self._carry = parts.pop()
        for part in parts:
            for seg in part.split("\r"):
                self._note(seg)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


_JOB = {}
_JOB_LOCK = threading.Lock()


def _job_view():
    view = {"running": False, "folders": [], "media": None, "kinds": None, "rescan": False,
            "cancel": False,
            "progress": None, "stage": None, "pct": None, "rate": None, "eta": None,
            "done": None, "items_total": None, "files_done": None, "files_total": None,
            "error": None, "result": None, "started": None, "finished": None}
    view.update(_JOB)
    return view


def _save_report(data_dir, name, report):
    from .store import _atomic_write
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write(root / name, json.dumps(report, ensure_ascii=False).encode('utf-8'))


def _read_report(data_dir, name, missing=None):
    try:
        return json.loads((Path(data_dir) / name).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return missing
    except (OSError, ValueError) as exc:
        return {'error': str(exc)}


def _index_worker(job, settings, client):
    orig = sys.stdout
    stream = _JobStream(job, console=orig)
    if not os.environ.get("EG_QUIET"):
        try:
            kinds = job.get("kinds")
            print(f"[index] started: {len(job['folders'])} folder(s)"
                  + (f" (types: {', '.join(kinds)})" if kinds else ""), file=orig, flush=True)
        except Exception:
            pass
    try:
        sys.stdout = stream
        job["stage"] = "scanning"
        res = Indexer(settings, client, log=stream).run(job["folders"], media=job["media"],
                                                        kinds=job.get("kinds"),
                                                        rescan=job.get("rescan", False),
                                                        cancel=lambda: bool(job.get("cancel")),
                                                        verify_contents=job.get("verify_contents",False))
        reasons = {}
        for _p, why in (res.get("skipped") or []):
            k = str(why)[:80]
            reasons[k] = reasons.get(k, 0) + 1
        job["result"] = {
            "cancelled": bool(res.get("cancelled", False)),
            "files_scanned": res.get("files_scanned", 0),
            "indexed_files": res.get("indexed_files", 0),
            "vectors_added": res.get("vectors_added", 0),
            "unchanged": res.get("unchanged", 0),
            "dropped": res.get("dropped", 0),
            "kinds_excluded": res.get("kinds_excluded", 0),
            "total": res.get("vectors_total", 0),
            "seconds": res.get("seconds", 0),
            "skipped_reasons": [{"count": n, "why": w} for w, n in
                                sorted(reasons.items(), key=lambda x: -x[1])[:5]],
            "api_errors": [str(e)[:200] for e in (res.get("errors") or [])[:3]],
            "warnings": list(res.get('warnings', [])),
            "preparation_workers": res.get('preparation_workers'),
            "embedding_requests": res.get('embedding_requests'),
            "checkpoints": res.get('checkpoints'),
            "verify_contents": res.get('verify_contents', False),
        }
        try:
            _save_report(settings.data_dir, 'last-index.json', dict(job['result'],
                         warnings=[(str(path), str(reason)) for path, reason in res.get('skipped', [])] + [('Recovery', str(w)) for w in res.get('warnings', [])]))
        except OSError as exc:
            job['result']['report_error'] = str(exc)
    except Exception as e:
        job["error"] = f"{type(e).__name__}: {e}"
        try:
            _save_report(settings.data_dir, 'last-index.json', {'error': job['error']})
        except OSError:
            pass
        if os.environ.get("EG_DEBUG"):
            traceback.print_exc()
    finally:
        stream.write("\n")
        sys.stdout = orig
        job["running"] = False
        job["finished"] = time.time()
        for key in ("stage", "pct", "rate", "eta", "done", "items_total",
                    "files_done", "files_total"):
            job[key] = None
        if not os.environ.get("EG_QUIET"):
            try:
                if job.get("error"):
                    print(f"[index] failed: {job['error']}", file=orig, flush=True)
                elif job.get("result"):
                    r = job["result"]
                    verb = "cancelled" if r.get("cancelled") else "done"
                    print(f"[index] {verb}: +{r.get('vectors_added', 0)} vectors · "
                          f"{r.get('indexed_files', 0)} files · {r.get('unchanged', 0)} unchanged · "
                          f"{r.get('dropped', 0)} removed · {r.get('total', 0)} total "
                          f"in {r.get('seconds', 0)}s", file=orig, flush=True)
            except Exception:
                pass


def _start_index_job(settings, client, folders, media, kinds=None, rescan=False, verify_contents=False):
    with _JOB_LOCK:
        if _JOB.get("running"):
            return None, "indexing already running — wait for the current one to finish"
        try:
            existing_index = Index.load(settings.data_dir)
        except Exception as exc:
            return None, 'Cannot load the library: ' + str(exc)
        from .index_preferences import load, save
        if kinds is None:
            kinds = load(settings.data_dir, fallback_kinds=existing_index.kind_filter or ['image'], fallback_workers=settings.threads)['kinds']
        kind_list = None
        if kinds is not None:
            if isinstance(kinds, str):
                parts = [x.strip().lower() for x in kinds.replace(";", ",").split(",") if x.strip()]
            elif isinstance(kinds, (list, tuple)):
                parts = [x.strip().lower() for p in kinds
                         for x in str(p).replace(";", ",").split(",") if x.strip()]
            else:
                return None, "kinds must be a list of file type names"
            if "all" in parts:
                kind_list = ["all"]
            else:
                unknown = sorted({p for p in parts if p not in INDEX_KINDS})
                if unknown:
                    return None, (f"unknown file type(s): {', '.join(unknown)} — "
                                  f"known: {', '.join(INDEX_KINDS)}")
                if not parts:
                    return None, "tick at least one file type (or use 'all')"
                kind_list = list(dict.fromkeys(parts))
        targets = []
        for f in folders:
            try:
                p = Path(f).expanduser().resolve()
            except Exception as e:
                return None, f"bad folder {f!r}: {type(e).__name__}: {e}"
            if not p.is_dir():
                return None, f"folder not found: {p}"
            if str(p) not in targets:
                targets.append(str(p))
        existing = existing_index.roots or []
        extra = [str(r) for r in existing] if rescan else []
        all_folders = list(dict.fromkeys(targets + extra))
        if not all_folders:
            if rescan and existing:
                all_folders = [str(r) for r in existing]
            else:
                return None, "no folders to index — type a folder path first"
        try:
            save(settings.data_dir, kinds=list(INDEX_KINDS) if kind_list==['all'] else kind_list,
                 prepare_workers=settings.threads)
        except (OSError, ValueError) as exc:
            return None, 'Could not save indexing preferences: ' + str(exc)
        job = {"running": True, "folders": all_folders, "media": media, "kinds": kind_list,
               "rescan": bool(rescan), "verify_contents":bool(verify_contents), "cancel": False,
               "progress": "", "error": None, "result": None, "started": time.time(), "finished": None}
        _JOB.clear()
        _JOB.update(job)
        threading.Thread(target=_index_worker, args=(_JOB, settings, client), daemon=True).start()
    return _JOB, None


def _norm_roots(raw):
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (tuple, list)):
        return None
    out = []
    for r in raw or []:
        if not isinstance(r, str):
            continue
        r = r.strip()
        if not r:
            continue
        try:
            out.append(str(Path(r).expanduser().resolve()))
        except Exception:
            out.append(r)
    return out or None


class Handler(AccountHTTP, BaseHTTPRequestHandler):
    server_version = "EmbeddingGemmaSearch/1.0"
    settings: Settings = None
    client: EmbedClient = None
    _cache = {"stamp": None, "index": None, "paths": set()}

    def index(self) -> Index:
        if self.guest:
            grants = self.account.folders
            with self._cache['guest_lock']:
                key = (Index.stamp(self.settings.data_dir), grants)
                views = self._cache.setdefault('guest_views', {})
                view = views.get(key)
                if view is None:
                    base = Index.load(self.settings.data_dir, roots=grants) if grants else Index(self.settings.data_dir)
                    view = guest_index(base, grants, self.settings_root)
                    if len(views) >= 4:
                        views.clear()
                    views[key] = view
                return view
        stamp = Index.stamp(self.settings.data_dir)
        if self._cache["index"] is None or stamp != self._cache["stamp"]:
            index = Index.load(self.settings.data_dir)
            self._cache.update(stamp=stamp, index=index, paths={r.get('path') for r in index.records}, views={})
        return self._cache["index"]

    def search_index(self,roots):
        if self.guest or not roots:return self.index()
        # Only requested shards are read for a scoped query. Keep a small set
        # of stable view objects so style projection caches remain reusable.
        stamp=Index.stamp(self.settings.data_dir)
        views=self._cache.setdefault('views',{})
        key=(stamp,tuple(sorted(roots)))
        value = views.get(key)
        if value is None:
            value=Index.load(self.settings.data_dir,roots=roots)
            if len(views)>=4:views.clear()
            views[key]=value
        return value

    def log_message(self, fmt, *args):
        if self.settings and os.environ.get("EG_QUIET"):
            return
        dt = (time.time() - getattr(self, "_t0", time.time())) * 1000
        super().log_message(fmt + " [%.1fms]", *args, dt)

    _QUIET_PATHS = ("/api/job", "/api/status")

    def log_request(self, code="-", size="-"):
        try:
            path = urllib.parse.urlparse(self.path).path
        except Exception:
            path = ""
        if path in self._QUIET_PATHS and str(code) == "200":
            return
        super().log_request(code, size)

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.accounts.enabled:
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8")

    def do_GET(self):
        self._t0 = time.time()
        url = urllib.parse.urlparse(self.path)
        path = url.path
        query = urllib.parse.parse_qs(url.query)
        try:
            if not self.authorize(path):
                return
            if self.accounts.enabled and path in ('/login', '/users', '/api/users'):
                return self.account_get(path)
            if path in ("/", "/index.html"):
                return self.html(PAGE)
            if path == "/match":
                return self.html(MATCH_PAGE)
            if path == "/focus":
                return self.html(Path(__file__).with_name("focus.html").read_text(encoding="utf-8"))
            if path == "/research":
                return self._send(200, Path(__file__).resolve().parent.parent.joinpath("research", "report.html").read_text(encoding="utf-8"),
                                  "text/html; charset=utf-8")
            if path == "/style-report":
                return self._send(200, Path(__file__).resolve().parent.parent.joinpath("style-models", "report.html").read_text(encoding="utf-8"),
                                  "text/html; charset=utf-8")
            if path == "/api/style_models":
                status = self.focus_engine.style_status()
                if self.guest:
                    status = {key: status[key] for key in ('available', 'default_method') if key in status}
                return self._json(status)
            if path == "/api/focus_examples":
                index = self.index()
                hits = Searcher(self.settings, self.client).search(
                    text=query.get("q", ["landscape painting"])[0][:300],
                    index=index, kind="image", k=8)
                return self._json({"results": [{"name": Path(r["path"]).name,
                    "path": r["path"], "thumb": "/thumb?path=" + urllib.parse.quote(r["path"])}
                    for r in hits]})
            if path == "/api/status":
                return self._json(self._status())
            if path == "/api/search":
                return self._search(query, None)
            if path == "/thumb":
                return self._send_file(query.get("path", [""])[0], inline=True, thumb=True)
            if path == "/file":
                return self._send_file(query.get("path", [""])[0], inline=True)
            if path == "/api/roots":
                index = self.index()
                return self._json({"roots": index.roots})
            if path == "/api/job":
                return self._json(_job_view())
            if path == "/api/index_report":
                return self._json(_read_report(self.settings.data_dir, 'last-index.json',
                                               {'error': 'No indexing report yet. Add or update a folder to create one.'}))
            return self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            if os.environ.get("EG_DEBUG"):
                traceback.print_exc()
            private_error = self.accounts.enabled and (self.account is None or self.guest)
            return self._json({"error": 'Refseek could not complete the request.' if private_error else f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        self._t0 = time.time()
        url = urllib.parse.urlparse(self.path)
        try:
            if not self.authorize(url.path, post=True):
                return
            if self.accounts.enabled and url.path in ('/api/login', '/api/logout', '/api/users', '/api/users/remove'):
                return self.account_post(url.path)
            if url.path == "/api/focus":
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 36 * 1048576:
                    return self._json({"error": "request too large or empty (max 36 MB)"}, 400)
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("expected an object")
                    return self._json(self.focus_engine.search(payload, self.index()))
                except (ValueError, TypeError) as e:
                    return self._json({"error": str(e)}, 400)
            if url.path == "/api/search_image":
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 72 * 1048576:
                    return self._json({"error": "request too large or empty (max 72 MB)"}, 400)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if not isinstance(payload, dict):
                    return self._json({"error": "expected an object"}, 400)
                return self._search({}, payload)
            if url.path == "/api/match":
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                return self._api_match(payload)
            if url.path in ("/api/index", "/api/forget"):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if url.path == "/api/index":
                    return self._start_index(payload)
                return self._forget(payload)
            if url.path == "/api/cancel":
                return self._cancel()
            if url.path in ("/api/cleanup_snapshots", "/api/index_settings") and self.headers.get_content_type() != 'application/json':
                return self._json({'error': 'Send an application/json request.'}, 415)
            if url.path == "/api/cleanup_snapshots":
                return self._cleanup_snapshots()
            if url.path == "/api/index_settings":
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 1024:
                    return self._json({'error': 'Invalid settings request.'}, 400)
                return self._set_index_settings(json.loads(self.rfile.read(length)))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            if os.environ.get("EG_DEBUG"):
                traceback.print_exc()
            private_error = self.accounts.enabled and (self.account is None or self.guest)
            return self._json({"error": 'Refseek could not complete the request.' if private_error else f"{type(e).__name__}: {e}"}, 500)

    def _status(self):
        from . import __version__
        from .store import CorruptIndexError

        if self.guest:
            index = self.index()
            info = index.stats()
            return {key: info[key] for key in ('files', 'kinds', 'roots')} | {'folder_tree': index.folder_tree}

        try:
            index = self.index()
        except CorruptIndexError as e:
            info = {"vectors": 0, "files": 0, "dim": None, "model": None,
                    "api_type": None, "roots": [], "kinds": {},
                    "data_dir": str(Path(self.settings.data_dir).expanduser().resolve()),
                    "root_stats": [],
                    "app_version": __version__, "index_error": str(e)}
            try:
                info.update({k: v for k, v in self.client.describe().items()})
            except Exception:
                pass
            return info
        info = {"vectors": len(index.records), "files": len({r.get("path") for r in index.records}),
                "dim": index.dim, "model": index.model, "api_type": index.api_type,
                "roots": index.roots, "kind_filter": index.kind_filter, "kinds": {},
                "data_dir": str(Path(self.settings.data_dir).expanduser().resolve()),
                "root_stats": self._root_stats(index),
                "folder_tree": index.folder_tree,
                "last_cleanup": _read_report(self.settings.data_dir, 'last-cleanup.json'),
                "index_settings": self._index_settings(index),
                "storage": index.storage_info(),
                "app_version": __version__}
        for r in index.records:
            info["kinds"][r.get("kind", "?")] = info["kinds"].get(r.get("kind", "?"), 0) + 1
        if getattr(index, "_problem", None):
            info["index_error"] = index._problem
        try:
            info.update({k: v for k, v in self.client.describe().items()})
        except Exception:
            pass
        info['runtime'] = getattr(self.settings,'runtime_info',None)
        return info

    def _index_settings(self, index):
        from .dimensions import DEFAULT_DIMENSION, DIMENSIONS, preferred_dimension

        dimension = (index.search_dim or index.dim) if index.records else preferred_dimension(self.settings.data_dir)
        from .index_preferences import load, cpu_limit
        saved = load(self.settings.data_dir, fallback_kinds=index.kind_filter or ['image'], fallback_workers=self.settings.threads)
        return dict(dimension=dimension or DEFAULT_DIMENSION, locked=bool(index.records), choices=DIMENSIONS,
                    kinds=saved['kinds'], prepare_workers=self.settings.threads, max_prepare_workers=cpu_limit(),
                    estimates=[dict(dimension=d, native_bytes=0 if d == DEFAULT_DIMENSION else 4 * 10000 * DEFAULT_DIMENSION,
                                    total_bytes=4 * 10000 * (d if d == DEFAULT_DIMENSION else d + DEFAULT_DIMENSION))
                               for d in DIMENSIONS])

    def _set_index_settings(self, payload):
        from .dimensions import DIMENSIONS
        from .index_preferences import save, cpu_limit
        if not isinstance(payload, dict) or not payload or set(payload)-{'dimension','kinds','prepare_workers'}:
            return self._json({'error':'Invalid indexing preferences.'}, 400)
        dimension = payload.get('dimension')
        if 'dimension' in payload and (type(dimension) is not int or dimension not in DIMENSIONS):
            return self._json({'error':'Choose 768, 512, 256 or 128 dimensions.'}, 400)
        kinds = payload.get('kinds')
        if 'kinds' in payload and (not isinstance(kinds,list) or any(not isinstance(k,str) or k not in INDEX_KINDS for k in kinds)):
            return self._json({'error':'Choose known file types.'}, 400)
        workers = payload.get('prepare_workers')
        if 'prepare_workers' in payload and (type(workers) is not int or not 1 <= workers <= cpu_limit()):
            return self._json({'error':f'Choose 1 through {cpu_limit()} CPU preparation workers.'}, 400)
        with _JOB_LOCK:
            if _JOB.get('running'):
                return self._json({'error':'Wait for indexing to finish.'}, 409)
            index = self.index()
            if dimension is not None and index.records:
                return self._json({'error':'This library keeps its existing dimensions.'}, 409)
            if 'kinds' in payload or 'prepare_workers' in payload:
                current = self._index_settings(index)
                save(self.settings.data_dir, kinds=kinds if 'kinds' in payload else current['kinds'],
                     prepare_workers=workers if workers is not None else self.settings.threads)
                if workers is not None:
                    self.settings.threads = workers
            if dimension is not None:
                _save_report(self.settings.data_dir,'search-settings.json',{'dimension':dimension})
            return self._json(self._index_settings(index))

    def _cleanup_snapshots(self):
        from .folder_store import cleanup
        from .store import CorruptIndexError

        with _JOB_LOCK:
            if _JOB.get('running'):
                return self._json({'error': 'Wait for indexing to finish before cleaning up.'}, 409)
            try:
                report = cleanup(self.settings.data_dir)
            except (CorruptIndexError, OSError, RuntimeError) as exc:
                report = {'error': str(exc)}
            _save_report(self.settings.data_dir, 'last-cleanup.json', report)
        return self._json(report, 409 if 'error' in report else 200)

    def _root_stats(self, index):
        import os as _os

        roots = index.roots or []
        files = [{} for _ in roots]
        vecs = [0] * len(roots)
        for r in index.records:
            p = r.get("path") or ""
            best = -1
            for i, root in enumerate(roots):
                if p == root or p.startswith(root.rstrip("/\\") + _os.sep):
                    if best < 0 or len(root) > len(roots[best]):
                        best = i
            if best >= 0:
                files[best][p] = r.get('size') or 0
                vecs[best] += 1
        storage={entry['root']:entry for entry in index.storage_info()['indexes']}
        return [dict(storage.get(root,{}),root=root,files=len(files[i]),vectors=vecs[i],source_bytes=sum(files[i].values()))
                for i, root in enumerate(roots)]

    def _indexed(self, path: str):
        if self.guest:
            view = self.index()
            return allowed_file(path, tuple(Path(p) for p in self.account.folders), self.settings_root) if path in view._allowed_paths else None
        try:
            target = str(Path(path).resolve())
        except Exception:
            return None
        self.index()
        if self.accounts.enabled and Path(target).is_relative_to(self.settings_root):
            return None
        return target if target in self._cache["paths"] else None

    def _send_file(self, path, inline=True, thumb=False):
        target = self._indexed(path)
        if not target or not Path(target).is_file():
            return self._json({"error": "file not indexed"}, 404)
        data = Path(target).read_bytes()
        ext = Path(target).suffix.lower()
        from .textio import IMAGE_EXTS, MIME_BY_EXT

        ctype = MIME_BY_EXT.get(ext)
        if not ctype:
            ctype = {
                ".html": "text/html", ".xml": "text/xml", ".txt": "text/plain",
                ".md": "text/markdown", ".py": "text/plain", ".json": "application/json",
                ".pdf": "application/pdf", ".epub": "application/epub+zip",
            }.get(ext, "application/octet-stream")
        if thumb and ext in IMAGE_EXTS:
            try:
                shrunk = _thumbnail(data)
            except Exception:
                shrunk = None
            if shrunk is not None:
                data, ctype = shrunk
        if self.accounts.enabled:
            safe_image = ctype in ('image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp', 'image/tiff')
            disp = 'inline' if inline and safe_image else 'attachment'
            name = urllib.parse.quote(Path(target).name, safe='')
            return self._send(200, data, ctype, {'Content-Disposition': f"{disp}; filename*=UTF-8''{name}",
                                               'Content-Security-Policy': "sandbox; default-src 'none'"})
        disp = "inline" if inline else "attachment"
        name = urllib.parse.quote(Path(target).name, safe='')
        return self._send(200, data, ctype, {'Content-Disposition': f"{disp}; filename*=UTF-8''{name}"})

    def _search(self, query, payload):
        from .store import CorruptIndexError
        from .folder_scope import FolderScope

        raw = query if payload is None else payload
        if self.guest and (payload or {}).get('method') not in (None, 'baseline', 'style_original', 'style_current', 'style_contrastive'):
            return self._json({'error': 'This method is available to the owner only.'}, 403)
        roots = _norm_roots(raw.get('roots'))
        excluded = _norm_roots(raw.get('excluded_roots'))
        exact = _norm_roots(raw.get('only_folder'))
        scope = FolderScope(roots, excluded, exact[0] if exact else None)
        neg = (payload or {}).get('neg_image') or {}
        try:
            needs_reference = (payload or {}).get('path') or (isinstance(neg, dict) and neg.get('path'))
            index = self.index() if needs_reference else self.search_index(scope.candidate_roots)
        except CorruptIndexError as e:
            if self.guest:
                return self._json({'error': 'The shared index is unavailable.'}, 503)
            return self._json({"error": f"index unreadable: {e} — run: python run.py doctor"})
        if not index.records:
            if self.guest or scope.candidate_roots:return self._json({"results":[],"more":False,"count":0})
            return self._json({"error": "index is empty — run: python run.py index <folder>"})
        searcher = Searcher(self.settings, self.client)
        raw_k = query.get("k") or (payload or {}).get("k") or [24]
        if isinstance(raw_k, (list, tuple)):
            raw_k = raw_k[0] if raw_k else 24
        k = max(1, min(200, int(raw_k)))
        raw_o = query.get("o", ["0"])[0] if not payload else (payload.get("offset") or 0)
        try:
            offset = max(0, int(raw_o))
        except (TypeError, ValueError):
            offset = 0
        kinds = query.get("kind", [""])[0] if not payload else ",".join(payload.get("kind") or [])
        kindstr = ",".join(x for x in kinds.split(",") if x.strip()) or None
        excludes = query.get("not", []) if not payload else (payload.get("not") or [])
        if isinstance(excludes, str):
            excludes = [excludes]
        raw_nw = query.get("not_weight", ["1.0"])[0] if not payload else payload.get("not_weight", 1.0)
        try:
            neg_weight = min(2.0, max(0.0, float(raw_nw)))
        except (TypeError, ValueError):
            neg_weight = 1.0
        combine = query.get("combine", ["avg"])[0] if not payload else (payload.get("combine") or "avg")
        if combine not in ("avg", "max"):
            combine = "avg"
        focus = (payload or {}).get("focus", "original")
        from .focus import FOCUS_GROUPS
        if focus not in ("original", "subject", *FOCUS_GROUPS):
            return self._json({"error": "unknown image aspect"}, 400)
        # Route uploaded 0%-strength queries through the original search path:
        # this preserves its exact float32 scores and tie order, even with negatives.
        use_focus = focus != "original" and (payload or {}).get("focus_strength", 1) != 0
        if payload and (use_focus or payload.get("path") or payload.get("method") == "style_original"):
            try:
                response = self.focus_engine.search_gallery(payload, index, scope=scope, k=k, offset=offset)
                for row in response['results']:
                    row['folder'] = str(Path(row['path']).parent)
                return self._json(response)
            except (TypeError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
        try:
            niw = min(2.0, max(0.0, float((payload or {}).get('neg_image_weight', 1.0))))
        except (TypeError, ValueError):
            niw = 1.0
        neg_vector = None
        if neg and niw:
            try:
                if isinstance(neg, dict) and neg.get('path'):
                    neg_vector = index.image_vector_at(neg['path'])
                    if neg_vector is None:
                        raise ValueError('negative image is no longer in this index')
                else:
                    neg_vector = self.focus_engine.image_vector(neg)
            except (TypeError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
        if payload and payload.get("base64"):
            import base64

            try:
                blob = base64.b64decode(payload["base64"], validate=True)
                if not blob or len(blob) > 25 * 1048576:
                    raise ValueError("choose an image smaller than 25 MB")
            except (ValueError, TypeError) as e:
                return self._json({"error": str(e)}, 400)
            mime = payload.get("mime") or "image/png"
            q = payload.get("q", "")
            if isinstance(q, str):
                q = [q]
            img_wants = [x.strip() for x in q if x and x.strip()]
            extra = [x.strip() for x in (payload.get("wants") or []) if x and x.strip()]
            try:
                iw = min(1.0, max(0.0, float(payload.get("img_weight", 0.5))))
            except (TypeError, ValueError):
                iw = 0.5
            results = searcher.search(k=k + 1, kind=kindstr, index=index, exclude=excludes,
                                      wants=img_wants + extra,
                                      combine=(payload.get("combine") or "avg"),
                                      image_bytes=blob, image_mime=mime, img_weight=iw,
                                      neg_image_vector=neg_vector,
                                      neg_image_weight=niw,
                                      neg_weight=neg_weight, scope=scope, offset=offset)
        else:
            q = query.get("q", []) if not payload else payload.get("q", "")
            if isinstance(q, str):
                q = [q]
            wants = [x.strip() for x in q if x and x.strip()]
            wants += [x.strip() for x in (payload or {}).get('wants', []) if x and x.strip()]
            if not wants:
                return self._json({"error": "empty query"})
            results = searcher.search(k=k + 1, kind=kindstr, index=index, exclude=excludes,
                                      wants=wants, combine=combine, scope=scope, offset=offset,
                                      neg_weight=neg_weight, neg_image_vector=neg_vector, neg_image_weight=niw)
        more = len(results) > k
        results = results[:k]
        for r in results:
            r["thumb"] = "/thumb?path=" + urllib.parse.quote(r.get("path", ""))
            r["open"] = "/file?path=" + urllib.parse.quote(r.get("path", ""))
            r["folder"] = str(Path(r["path"]).parent)
        response = {"results": results, "more": more}
        if focus != "original":
            response.update(focus=focus, label="Subject" if focus == "subject" else FOCUS_GROUPS[focus]["label"])
        return self._json(response)

    def _api_match(self, payload):
        """Direct similarity score: one main image vs N texts and/or M other
        images, cosine x 100, ranked best-first.

        Request: {image: {base64, mime}, texts: [...],
                  images: [{base64, mime, name}]}.
        Response: {results: [{kind: "text", text, pct} |
                             {kind: "image", name, idx, pct}], count}.
        `idx` is the position in the sent `images` list so the page can show
        the right thumbnail (the server never sees the preview dataURLs).

        No index involved. Pure-python dot product (no math.sumprod, so it
        works on python 3.10 without numpy).
        """
        import base64

        def _decode(b64, what):
            try:
                blob = base64.b64decode(b64 or "")
            except Exception:
                return None, f"unreadable {what}"
            if not blob:
                return None, f"unreadable {what}"
            if len(blob) > 25 * 1048576:
                return None, f"{what} too large (max 25 MB)"
            return blob, None

        payload = payload or {}
        img = payload.get("image") or {}
        if not isinstance(img, dict):
            img = {}
        b64 = img.get("base64") or payload.get("base64") or ""
        mime = img.get("mime") or payload.get("mime") or "image/png"
        raw_texts = payload.get("texts")
        if raw_texts is None:
            single = payload.get("text", payload.get("q", ""))
            raw_texts = [single] if isinstance(single, str) else []
        if isinstance(raw_texts, str):
            raw_texts = raw_texts.split("\n")
        texts = [str(t).strip() for t in (raw_texts or []) if str(t).strip()]
        texts = [t[:2000] for t in texts[:20]]
        raw_imgs = payload.get("images") or []
        if isinstance(raw_imgs, dict):
            raw_imgs = [raw_imgs]
        others = []
        for i, o in enumerate(list(raw_imgs)[:10]):
            if not isinstance(o, dict):
                continue
            blob, err = _decode(o.get("base64") or "", f"picture {i + 1}")
            if err:
                return self._json({"error": err}, 400)
            others.append({"blob": blob,
                           "mime": o.get("mime") or "image/png",
                           "name": str(o.get("name") or f"picture {i + 1}")[:120]})
        if not b64:
            return self._json({"error": "pick a picture first"}, 400)
        if not texts and not others:
            return self._json({"error": "write some text or add another picture first"}, 400)
        blob, err = _decode(b64, "image")
        if err:
            return self._json({"error": err}, 400)
        searcher = Searcher(self.settings, self.client)
        try:
            ivec = searcher.query_vector(image_bytes=blob, image_mime=mime)
        except Exception as e:
            return self._json({"error": f"could not embed image: {e}"}, 500)
        out = []
        if texts:
            try:
                raws = self.client.embed([self.settings.with_prefix_query(t) for t in texts])
                tvecs = [l2norm(v) for v in raws]
            except Exception as e:
                return self._json({"error": f"could not embed text: {e}"}, 500)
            for text, tv in zip(texts, tvecs):
                try:
                    cos = sum(a * b for a, b in zip(ivec, tv))
                except Exception:
                    cos = 0.0
                out.append({"kind": "text", "text": text,
                            "pct": round(float(cos) * 100.0, 1)})
        for idx, o in enumerate(others):
            try:
                # one-by-one: image items don't batch server-side, and a full
                # batch of big images can 500 the embedding server.
                ov = searcher.query_vector(image_bytes=o["blob"], image_mime=o["mime"])
            except Exception as e:
                return self._json({"error": f"could not embed {o['name']}: {e}"}, 500)
            try:
                cos = sum(a * b for a, b in zip(ivec, ov))
            except Exception:
                cos = 0.0
            out.append({"kind": "image", "name": o["name"], "idx": idx,
                        "pct": round(float(cos) * 100.0, 1)})
        out.sort(key=lambda r: r["pct"], reverse=True)
        return self._json({"results": out, "count": len(out)})

    def _start_index(self, payload):
        folders = payload.get("folders") or []
        if isinstance(folders, str):
            folders = [folders]
        media = payload.get("media") or "auto"
        if media not in ("auto", "on", "off"):
            media = "auto"
        job, error = _start_index_job(self.settings, self.client,
                                      [str(f) for f in folders if str(f).strip()], media,
                                      payload.get("kinds"), bool(payload.get("rescan")), bool(payload.get("verify_contents")))
        if error:
            return self._json({"error": error},
                              409 if "already running" in error else 400)
        return self._json({"started": True, "folders": job["folders"],
                           "kinds": job.get("kinds"), "rescan": job.get("rescan", False)})

    def _cancel(self):
        with _JOB_LOCK:
            if not _JOB.get("running"):
                return self._json({"error": "no index job running"}, 404)
            _JOB["cancel"] = True
            return self._json({"cancelling": True})

    def _forget(self, payload):
        from .store import CorruptIndexError

        if _JOB.get("running"):
            return self._json({"error": "indexing is running — wait for it to finish"}, 409)
        folders = payload.get("folders") or []
        if isinstance(folders, str):
            folders = [folders]
        targets = []
        for f in folders:
            f = str(f).strip()
            if not f:
                continue
            try:
                targets.append(str(Path(f).expanduser().resolve()))
            except Exception as e:
                return self._json({"error": f"bad folder {f!r}: {type(e).__name__}: {e}"}, 400)
        if not targets:
            return self._json({"error": "no folders given"}, 400)
        try:
            index = Index.load(self.settings.data_dir)
        except CorruptIndexError as e:
            return self._json({"error": f"index unreadable: {e}"}, 500)

        def under(path: str) -> bool:
            try:
                return any(Path(path).is_relative_to(t) for t in targets)
            except Exception:
                return False

        keep_r, keep_v = [], []
        for rec, vec in zip(index.records, index.vectors):
            if under(rec.get("path") or ""):
                continue
            keep_r.append(rec)
            keep_v.append(vec)
        removed = len(index.records) - len(keep_r)
        index.records, index.vectors = keep_r, keep_v
        index._matrix = None
        index.set_state(index.model, index.api_type,
                        [r for r in (index.roots or []) if r not in targets])
        index.save()
        return self._json({"removed": removed, "total": len(keep_r), "roots": index.roots})


class _LocalHTTPServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR can allow two apps to listen on one port.
    # A successful bind must mean our launch URL reaches our own library.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def build_server(settings: Settings, client: EmbedClient, port: int | None = None, *, accounts_mode: bool | None = None):
    from .accounts import TUNNEL_HOST, parse_tunnel_host
    from .folder_store import migrate
    from .focus import FocusEngine
    from .store import CorruptIndexError

    root = Path(settings.data_dir).expanduser().absolute()
    from .index_preferences import load as load_index_preferences, cpu_limit
    if 'EG_THREADS' not in os.environ:
        settings.threads = load_index_preferences(root, fallback_workers=settings.threads)['prepare_workers']
    settings.threads = max(1, min(settings.threads, cpu_limit()))
    accounts = Accounts(root, enabled=accounts_mode)
    config = read_env(env_path(root))
    network = config if accounts.enabled else {}
    host = network.get('REFSEEK_BIND_HOST', settings.web_host).strip()
    public_host = network.get('REFSEEK_PUBLIC_HOST', host).strip().lower()
    cert, key = network.get('REFSEEK_TLS_CERT', '').strip(), network.get('REFSEEK_TLS_KEY', '').strip()
    tunnel_host = parse_tunnel_host(network.get(TUNNEL_HOST, ''))
    local = host in ('127.0.0.1', 'localhost')
    tls = bool(cert or key)
    if tls and not (cert and key):
        raise ValueError('Configure both REFSEEK_TLS_CERT and REFSEEK_TLS_KEY.')
    if not local and not (accounts.enabled and tls):
        raise ValueError('Network access requires enabled accounts and an HTTPS certificate and key.')
    if tunnel_host and not local:
        raise ValueError('A Cloudflare Tunnel requires Refseek to listen on 127.0.0.1 or localhost.')
    if accounts.enabled and (not re.fullmatch(r'[a-z0-9][a-z0-9.\-]*', public_host) or public_host == '0.0.0.0'):
        raise ValueError('Set REFSEEK_PUBLIC_HOST to the server name or LAN address.')
    context = None
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config_file(root, cert), config_file(root, key))
    stamp = Index.stamp(settings.data_dir)
    try:
        index = migrate(settings.data_dir)
    except CorruptIndexError:
        index = None
    if index and index.records and index.dim:
        settings.dim = index.dim

    handler = type("BoundHandler", (Handler,), {"settings": settings, "client": client,
                                               'accounts': accounts, 'settings_root': shared_data_dir(root).resolve()})
    cached = index if stamp == Index.stamp(settings.data_dir) else None
    handler._cache = {"stamp": stamp, "index": cached, 'guest_lock': threading.Lock(),
                      "paths": {r.get('path') for r in cached.records} if cached else set()}
    handler.focus_engine = FocusEngine(settings, client)
    bind_port = int(config.get('REFSEEK_WEB_PORT', settings.web_port)) if port is None else port
    server = _LocalHTTPServer((host, bind_port), handler)
    server.scheme = 'https' if context else 'http'
    default_port = 443 if context else 80
    actual_port = server.server_port
    suffix = f':{actual_port}' if actual_port != default_port else ''
    names = {public_host} | ({'localhost', '127.0.0.1'} if local else set())
    server.account_hosts = {name + suffix for name in names}
    server.tunnel_host = tunnel_host
    server.access_url = f'{server.scheme}://{public_host}{suffix}/'
    if context:
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    server.daemon_threads = True
    return server, index


def serve(settings: Settings, client: EmbedClient):
    server, index = build_server(settings, client)
    if index is None:
        print("index: UNREADABLE — run: python run.py doctor")
    else:
        print(f"index: {len(index.records)} vectors, {index.dim or '?'}d, model {index.model or settings.model}")
    print(f"web UI: {server.access_url}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
