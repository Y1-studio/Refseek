use crate::{
    Options, platform,
    preferences::GuiSettings,
    runtime,
    search::{Engine, Hit, Query},
    store::{Cleanup, IndexProgress, Library, RootInfo, SearchFolder, Stats},
    util::*,
};
use eframe::egui::{self, Color32, RichText, Vec2};
use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};
enum Command {
    Index(Vec<PathBuf>, HashSet<String>),
    Verify(Vec<PathBuf>, HashSet<String>),
    PreparationWorkers(usize),
    Remove(PathBuf),
    Dimension(usize),
    CleanupSnapshots,
    Shutdown,
}
enum Event {
    Status(String),
    Ready(String, PathBuf),
    Roots(Vec<RootInfo>),
    Folders(Vec<SearchFolder>),
    Dimension(usize, bool),
    Hits(Vec<Hit>),
    Done(String),
    Error(String),
    Stopped,
    IndexStatus(String),
    IndexProgress(IndexProgress),
    CleanupFinished(Res<Cleanup>),
    SearchStatus(String),
    SearchError(String),
}
fn library_state(tx: &mpsc::Sender<Event>, lib: &Library) {
    let _ = tx.send(Event::Roots(lib.infos()));
    let _ = tx.send(Event::Folders(lib.search_folders()));
    let _ = tx.send(Event::Dimension(lib.dimension, lib.roots.iter().any(|r| !r.records.is_empty())));
}
struct SearchWorker {
    handle: Option<thread::JoinHandle<()>>,
    closing: Arc<AtomicBool>,
    cancel: Arc<AtomicBool>,
}
impl Drop for SearchWorker {
    fn drop(&mut self) {
        self.closing.store(true, Ordering::Relaxed);
        self.cancel.store(true, Ordering::Relaxed);
        if let Some(t) = self.handle.take() {
            let _ = t.join();
        }
    }
}
fn worker(
    o: Options,
    rx: mpsc::Receiver<Command>,
    tx: mpsc::Sender<Event>,
    cancel: Arc<AtomicBool>,
    closing: Arc<AtomicBool>,
    searches: mpsc::Receiver<Query>,
    search_cancel: Arc<AtomicBool>,
) {
    let report = |s: String| {
        let _ = tx.send(Event::Status(s));
    };
    let mut library_loaded = false;
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Res<()> {
        let mut lib = Library::open_with_dimension(&o.data, o.dimension)?;
        lib.prepare_workers=o.prepare_workers;
        library_loaded = true;
        library_state(&tx, &lib);
        let assets = runtime::assets(&o.home, o.assets.as_deref(), &cancel, &report)?;
        report("Loading the frozen style adapters…".into());
        let mut engine = Engine::new(&assets, &o.home.join("data"))?;
        engine.styles.verify(&assets.join("math-reference.json"))?;
        let runtime = runtime::Runtime::start(
            &assets,
            &o.home.join("data"),
            &o.backend,
            o.device.as_deref(),
            cancel.clone(),
            &report,
        )?;
        save_json(
            &o.home.join("data/last-backend.json"),
            &serde_json::json!({"backend":runtime.backend,"device":runtime.device,"probes":runtime.probes,"attempts":runtime.attempts}),
        )?;
        let search_events = tx.clone();
        let search_api = runtime.api.clone();
        let search_dir = o.data.clone();
        let search_closing = closing.clone();
        let sc = search_cancel.clone();
        let search_handle = thread::spawn(move || {
            while !search_closing.load(Ordering::Relaxed) {
                let q = match searches.recv_timeout(Duration::from_millis(100)) {
                    Ok(q) => q,
                    Err(mpsc::RecvTimeoutError::Timeout) => continue,
                    Err(_) => break,
                };
                let status = |s| {
                    let _ = search_events.send(Event::SearchStatus(s));
                };
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    // The catalog is atomically published; its referenced files are immutable.
                    let snapshot = Library::open(&search_dir)?;
                    engine.search(&search_api, &snapshot, &q, &sc, &status)
                }));
                match result {
                    Ok(Ok(h)) => {
                        let _ = search_events.send(Event::Hits(h));
                    }
                    Ok(Err(e)) => {
                        let _ = search_events.send(Event::SearchError(e));
                    }
                    Err(_) => {
                        let _ = search_events.send(Event::SearchError(
                            "Search worker could not complete this query".into(),
                        ));
                    }
                }
            }
        });
        let _search_guard = SearchWorker {
            handle: Some(search_handle),
            closing: closing.clone(),
            cancel: search_cancel,
        };
        let _ = tx.send(Event::Ready(
            format!("{} · {}{}", runtime.backend.to_uppercase(), runtime.device,
                if runtime.backend == "cpu" && o.backend == "auto" {" · GPU unavailable; see backend-attempts.json"} else {""}),
            assets,
        ));
        while let Ok(command) = rx.recv() {
            if closing.load(Ordering::Relaxed) {
                break;
            }
            let verify_contents = matches!(&command, Command::Verify(..));
            let outcome = match command {
                Command::Shutdown => break,
                Command::CleanupSnapshots => {
                    let result = crate::store::cleanup_snapshots(&o.data, false);
                    let _ = save_json(&o.data.join("last-cleanup.json"), &result);
                    let _ = tx.send(Event::CleanupFinished(result));
                    continue;
                },
                Command::Dimension(d) => lib.set_dimension(d).map(|_| format!("New library dimension: {d}")),
                Command::PreparationWorkers(n)=>{lib.prepare_workers=n;Ok(format!("Image preparation: {} CPU workers",crate::image_pipeline::worker_count(n)))},
                Command::Remove(p) => lib.remove(&p).map(|_| {
                    "Folder removed from the native library. Original files are unchanged.".into()
                }),
                Command::Index(paths, kinds) | Command::Verify(paths, kinds) => {
                    let mut summaries = vec![];
                    let mut result = Ok(());
                    let mut pending_root = PathBuf::new();
                    for p in paths {
                        if cancel.load(Ordering::Relaxed) {
                            summaries.push("Indexing cancelled; remaining folders were not updated".into());
                            let _ = tx.send(Event::IndexProgress(IndexProgress {phase:"Cancelled".into(), ..Default::default()}));
                            break;
                        }
                        pending_root = p.clone();
                        lib.last_stats = None;
                        let source = match canonical(&p) {
                            Ok(path) => path,
                            Err(e) => {result = Err(e); break;}
                        };
                        if under(&source, &o.private_data) {
                            result = Err("Choose a picture folder outside Refseek's private data folder.".into());
                            break;
                        }
                        // A collection explicitly selected inside the app folder
                        // must still include its own subfolders. Protect working
                        // data, and exclude the whole app when scanning a parent.
                        let linked_data_inside_source = under(&o.private_data, &source) && !under(&o.private_data, &o.home);
                        let excluded = if under(&source, &o.home) || linked_data_inside_source {
                            o.private_data.clone()
                        } else { o.home.clone() };
                        let index_report = |s| {
                            let _ = tx.send(Event::IndexStatus(s));
                        };
                        let live = |p| {
                            let _ = tx.send(Event::IndexProgress(p));
                        };
                        let committed = |library: &Library| library_state(&tx, library);
                        let scan = if verify_contents { lib.index_live(
                            &source,
                            &kinds,
                            &runtime.api,
                            &cancel,
                            &index_report,
                            &excluded,
                            &live,
                            &committed,
                        ) } else { lib.update_live(&source, &kinds, &runtime.api, &cancel, &index_report, &excluded, &live, &committed) };
                        match scan {
                            Ok(stats) => {
                                let _ = save_json(&o.home.join("data/last-index.json"), &stats);
                                summaries.push(format!(
                                    "{} embedded · {} reused · {} skipped{}",
                                    stats.embedded,
                                    stats.reused,
                                    stats.failed,
                                    if stats.cancelled { " · cancelled" } else { "" }
                                ));
                            }
                            Err(e) => {
                                result = Err(e);
                                break;
                            }
                        }
                    }
                    if let Err(error) = &result {
                        let mut stats = lib.last_stats.clone().unwrap_or(Stats {
                            root:pending_root, index_dimension:lib.dimension, ..Default::default()
                        });
                        stats.error = Some(error.clone());
                        let _ = save_json(&o.home.join("data/last-index.json"), &stats);
                    }
                    result.map(|_| summaries.join("\n"))
                }
            };
            library_state(&tx, &lib);
            match outcome {
                Ok(s) => {
                    let _ = tx.send(Event::Done(s));
                }
                Err(e) => {
                    if let Ok(mut reload) = Library::open(&o.data) {
                        reload.prepare_workers=lib.prepare_workers;
                        lib = reload;
                        library_state(&tx, &lib);
                    }
                    let _ = tx.send(Event::Error(e));
                }
            }
        }
        Ok(())
    }));
    match result {
        Ok(Err(e)) => {
            let _ = tx.send(Event::Error(e.clone()));
            let recovery = if library_loaded { serde_json::Value::Null }
                else { crate::store::inspect_saved_catalog(&o.data, true)
                    .unwrap_or_else(|e|serde_json::json!({"passed":false,"error":e,"activated":false})) };
            let _ = save_json(&o.home.join("data/startup-error.json"),
                &serde_json::json!({"version":env!("CARGO_PKG_VERSION"),"library":display_path(&o.data),"error":&e,"recovery":recovery}));
        }
        Err(_) => {
            let _ = tx.send(Event::Error(
                "The model worker stopped unexpectedly. Existing snapshots are preserved.".into(),
            ));
        }
        _ => {}
    }
    let _ = tx.send(Event::Stopped);
}
struct Thumb {
    path: PathBuf,
    large: bool,
    image: Option<egui::ColorImage>,
}
#[derive(Default)]
struct FolderTree {
    folders: Vec<SearchFolder>,
    keys: Vec<String>,
    children: Vec<Vec<usize>>,
    roots: Vec<usize>,
}
impl FolderTree {
    fn new(folders: Vec<SearchFolder>) -> Self {
        let keys: Vec<_> = folders.iter().map(|folder| key(&folder.path)).collect();
        let by_key: HashMap<_, _> = keys.iter().enumerate().map(|(i, k)| (k.as_str(), i)).collect();
        let mut children = vec![vec![]; folders.len()];
        let mut roots = vec![];
        for (i, folder) in folders.iter().enumerate() {
            let parent = folder.path.ancestors().skip(1)
                .find_map(|ancestor| by_key.get(key(ancestor).as_str()).copied());
            if let Some(parent) = parent {
                children[parent].push(i);
            } else {
                roots.push(i);
            }
        }
        Self { folders, keys, children, roots }
    }
    fn visible_rows(&self, expanded: &HashSet<String>) -> Vec<(usize, usize)> {
        let mut rows = Vec::new();
        let mut stack: Vec<_> = self.roots.iter().rev().map(|&i| (i, 0)).collect();
        while let Some((i, depth)) = stack.pop() {
            rows.push((i, depth));
            if expanded.contains(&self.keys[i]) {
                stack.extend(self.children[i].iter().rev().map(|&child| (child, depth + 1)));
            }
        }
        rows
    }
}
fn navigation_button(ui: &mut egui::Ui, label: &str, next: bool, enabled: bool) -> egui::Response {
    ui.add_enabled_ui(enabled, |ui| {
        let id = ui.make_persistent_id(("navigation-chevron", label));
        let side = ui.text_style_height(&egui::TextStyle::Button);
        let button = egui::Button::new((egui::Atom::custom(id, Vec2::splat(side)), label)).atom_ui(ui);
        if let Some(rect) = button.rect(id) {
            let c = rect.center();
            let d = side * 0.24;
            let direction = if next { 1. } else { -1. };
            let color = ui.style().interact(&button.response).fg_stroke.color;
            ui.painter().add(egui::Shape::line(
                vec![c + Vec2::new(-direction*d, -d), c + Vec2::new(direction*d, 0.), c + Vec2::new(-direction*d, d)],
                egui::Stroke::new((side / 10.).max(1.5), color),
            ));
        }
        button.response
    }).inner
}
pub struct App {
    o: Options,
    tx: mpsc::Sender<Command>,
    rx: mpsc::Receiver<Event>,
    cancel: Arc<AtomicBool>,
    closing: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
    ready: bool,
    stopped: bool,
    busy: bool,
    status: String,
    backend: String,
    assets: Option<PathBuf>,
    tab: usize,
    q: Query,
    wants: Vec<String>,
    opening: Option<crate::opening::Opening>,
    opened_at: Instant,
    roots: Vec<RootInfo>,
    scope: HashSet<String>,
    excluded_scope: HashSet<String>,
    folder_tree: FolderTree,
    expanded_folders: HashSet<String>,
    settings_dimension: usize,
    dimension_locked: bool,
    hits: Vec<Hit>,
    page: usize,
    error: Option<String>,
    index_path: String,
    kinds: HashSet<String>,
    preview: Option<PathBuf>,
    thumb_tx: mpsc::Sender<(PathBuf, bool)>,
    thumb_rx: mpsc::Receiver<Thumb>,
    textures: HashMap<(PathBuf, bool), egui::TextureHandle>,
    requested: HashSet<(PathBuf, bool)>,
    failed_pictures: HashSet<(PathBuf, bool)>,
    settings_backend: String,
    prepare_workers: usize,
    smoke_stage: u8,
    smoke_deadline: Instant,
    screenshot_at: Option<Instant>,
    gui: GuiSettings,
    loaded_gui: GuiSettings,
    gui_save_at: Option<Instant>,
    search_tx: mpsc::Sender<Query>,
    search_cancel: Arc<AtomicBool>,
    searching: bool,
    index_status: String,
    index_progress: Option<IndexProgress>,
    index_details: Option<Res<Stats>>,
    cleanup_running: bool,
    last_cleanup: Option<Res<Cleanup>>,
    last_preview_rect: Option<egui::Rect>,
    diagnostic_key: Option<egui::Key>,
    diagnostic_drop: Option<PathBuf>,
    diagnostic_evidence: serde_json::Value,
    diagnostic_catalog_lock: Option<std::fs::File>,
    layout_checks: HashMap<String, serde_json::Value>,
}
impl App {
    fn new(cc: &eframe::CreationContext<'_>, o: Options, opening: Option<crate::opening::Opening>) -> Self {
        let graphics = if let Some(state) = &cc.wgpu_render_state {
            let info = state.adapter.get_info();
            serde_json::json!({"api":format!("{:?}",info.backend),"adapter":info.name,"device_type":format!("{:?}",info.device_type)})
        } else {
            serde_json::json!({"api":"OpenGL"})
        };
        let _ = save_json(&o.home.join("data/last-ui-renderer.json"), &graphics);
        let gui = GuiSettings::load(&o.home.join("data/settings.json"));
        gui.apply(&cc.egui_ctx);
        let kinds=crate::preferences::index_kinds(&o.home.join("data/settings.json"));
        let (tx, crx) = mpsc::channel();
        let (etx, rx) = mpsc::channel();
        let (search_tx, search_rx) = mpsc::channel();
        let search_cancel = Arc::new(AtomicBool::new(false));
        let cancel = Arc::new(AtomicBool::new(false));
        let closing = Arc::new(AtomicBool::new(false));
        let c = cancel.clone();
        let cl = closing.clone();
        let opts = o.clone();
        let sc = search_cancel.clone();
        let handle = thread::spawn(move || worker(opts, crx, etx, c, cl, search_rx, sc));
        let (ttx, trx) = mpsc::channel::<(PathBuf, bool)>();
        let (itx, irx) = mpsc::channel();
        let ctx = cc.egui_ctx.clone();
        thread::spawn(move || {
            while let Ok((path, large)) = trx.recv() {
                let image = crate::rpc::decoded(&path).ok().map(|i| {
                    let d = if large { 1400 } else { 300 };
                    let rgba = i
                        .resize(d, d, image::imageops::FilterType::Triangle)
                        .to_rgba8();
                    egui::ColorImage::from_rgba_unmultiplied(
                        [rgba.width() as usize, rgba.height() as usize],
                        &rgba,
                    )
                });
                if itx.send(Thumb { path, large, image }).is_err() {
                    break;
                }
                ctx.request_repaint();
            }
        });
        Self {
            last_cleanup: read_json(&o.data.join("last-cleanup.json")).ok(),
            settings_dimension: o.dimension,
            dimension_locked: false,
            settings_backend: o.backend.clone(),
            prepare_workers: o.prepare_workers,
            o,
            tx,
            rx,
            cancel,
            closing,
            thread: Some(handle),
            ready: false,
            stopped: false,
            busy: true,
            status: "Starting the native model…".into(),
            backend: "Starting…".into(),
            assets: None,
            tab: 0,
            q: Query::default(),
            wants: vec![],
            opened_at: opening.as_ref().map(|s| s.since).unwrap_or_else(Instant::now),
            opening,
            roots: vec![],
            scope: HashSet::new(),
            excluded_scope: HashSet::new(),
            folder_tree: FolderTree::default(),
            expanded_folders: HashSet::new(),
            hits: vec![],
            page: 0,
            error: None,
            index_path: String::new(),
            kinds,
            preview: None,
            thumb_tx: ttx,
            thumb_rx: irx,
            textures: HashMap::new(),
            requested: HashSet::new(),
            failed_pictures: HashSet::new(),
            smoke_stage: 0,
            smoke_deadline: Instant::now() + Duration::from_secs(600),
            screenshot_at: None,
            loaded_gui: gui.clone(),
            gui,
            gui_save_at: None,
            search_tx,
            search_cancel,
            searching: false,
            index_status: String::new(),
            index_progress: None,
            index_details: None,
            cleanup_running: false,
            last_preview_rect: None,
            diagnostic_key: None,
            diagnostic_drop: None,
            diagnostic_evidence: serde_json::json!({}),
            diagnostic_catalog_lock: None,
            layout_checks: HashMap::new(),
        }
    }
    fn send(&mut self, c: Command) {
        let cleanup = matches!(&c, Command::CleanupSnapshots);
        if matches!(&c, Command::Index(..) | Command::Verify(..)) {
            self.index_progress = Some(IndexProgress {
                phase: "Scanning".into(),
                ..Default::default()
            });
            self.index_status = if matches!(&c, Command::Verify(..)) {"Preparing full file-content verification…"}
                else {"Checking files against the saved index…"}.into();
        }
        self.error = None;
        self.busy = true;
        self.cancel.store(false, Ordering::Relaxed);
        if self.tx.send(c).is_err() {
            self.busy = false;
            self.error = Some("The model worker is not running. Restart the application.".into());
        } else if cleanup {
            self.cleanup_running = true;
        }
    }
    fn load_index_details(&self) -> Res<Stats> {
        let path = self.o.home.join("data/last-index.json");
        if !path.is_file() {
            return Err("No indexing report yet. Add or update a folder to create one.".into());
        }
        read_json(&path).map_err(|e| format!("Could not read the indexing report. {e}"))
    }
    fn toggle_index_details(&mut self) {
        self.index_details = if self.index_details.is_some() { None } else { Some(self.load_index_details()) };
    }
    fn has_folder_filters(&self) -> bool {
        self.q.only_folder.is_some() || !self.scope.is_empty() || !self.excluded_scope.is_empty()
    }
    fn clear_folder_filters(&mut self) {
        self.q.only_folder = None;
        self.scope.clear();
        self.excluded_scope.clear();
    }
    fn has_search_input(&self) -> bool {
        self.q.image.is_some() || !self.q.text.trim().is_empty() || self.wants.iter().any(|s| !s.trim().is_empty())
    }
    fn search_all_folders(&mut self) {
        self.clear_folder_filters();
        if self.has_search_input() {
            self.do_search();
        }
    }
    fn search_picture_folder(&mut self, folder: PathBuf) {
        self.clear_folder_filters();
        self.q.only_folder = Some(folder);
        self.preview = None;
        if self.has_search_input() {
            self.do_search();
        }
    }
    fn do_search(&mut self) {
        if !self.ready || self.searching {
            return;
        }
        // Keep selected rules even if a live index update removes a folder.
        // Dropping the last Include rule would silently broaden the search.
        self.q.roots = self.scope.iter().map(PathBuf::from).collect();
        self.q.excluded_roots = self.excluded_scope.iter().map(PathBuf::from).collect();
        self.page = 0;
        self.error = None;
        self.searching = true;
        self.search_cancel.store(false, Ordering::Relaxed);
        self.status = "Searching the latest saved library snapshot…".into();
        let mut query = self.q.clone();
        for want in self.wants.iter().map(|s| s.trim()).filter(|s| !s.is_empty()) {
            query.text.push('\n');
            query.text.push_str(want);
        }
        if self.o.gui_smoke {
            self.diagnostic_evidence["submitted_prompts"] = serde_json::json!(query.text.lines().map(str::trim).filter(|s| !s.is_empty()).collect::<Vec<_>>());
        }
        if self.search_tx.send(query).is_err() {
            self.searching = false;
            self.error = Some("Search worker is unavailable".into());
        }
    }
    fn stop(&mut self) {
        if !self.closing.swap(true, Ordering::Relaxed) {
            self.cancel.store(true, Ordering::Relaxed);
            self.search_cancel.store(true, Ordering::Relaxed);
            let _ = self.tx.send(Command::Shutdown);
            self.status = "Finishing the current operation and stopping the private model…".into();
        }
    }
    fn poll(&mut self, ctx: &egui::Context) {
        while let Ok(e) = self.rx.try_recv() {
            match e {
                Event::Status(s) => self.status = s,
                Event::Ready(b, a) => {
                    self.backend = b;
                    self.assets = Some(a);
                    self.ready = true;
                    self.busy = false;
                    self.status = "Ready".into();
                }
                Event::Roots(r) => self.roots = r,
                Event::Folders(f) => self.folder_tree = FolderTree::new(f),
                Event::Dimension(d, locked) => {
                    self.settings_dimension = d;
                    self.dimension_locked = locked;
                    let path = self.o.home.join("data/settings.json");
                    let saved: serde_json::Value = read_json(&path).unwrap_or_default();
                    if saved["index_dimension"].as_u64() != Some(d as u64) {
                        if let Err(e) = crate::preferences::merge(&path, "index_dimension", serde_json::json!(d)) {
                            self.error = Some(e);
                        }
                    }
                },
                Event::Hits(h) => {
                    self.hits = h;
                    self.page = 0;
                    self.searching = false;
                    self.status = "Search complete".into();
                }
                Event::Done(s) => {
                    self.status = s;
                    self.busy = false;
                    if self.index_details.is_some() {
                        self.index_details = Some(self.load_index_details());
                    }
                }
                Event::CleanupFinished(result) => {
                    self.status = if result.is_ok() { "Snapshot cleanup finished" } else { "Snapshot cleanup failed" }.into();
                    self.last_cleanup = Some(result);
                    self.cleanup_running = false;
                    self.busy = false;
                }
                Event::Error(e) => {
                    if let Some(p) = self.index_progress.as_mut() {
                        if matches!(p.phase.as_str(), "Scanning" | "Indexing") {
                            p.phase = "Failed".into();
                            p.eta_seconds = None;
                        }
                    }
                    self.error = Some(e.clone());
                    self.status = e;
                    self.busy = false;
                    if self.index_details.is_some() { self.index_details = Some(self.load_index_details()); }
                }
                Event::Stopped => {
                    self.stopped = true;
                    self.busy = false;
                    self.cleanup_running = false;
                    self.ready = false;
                    self.searching = false;
                }
                Event::IndexStatus(s) => self.index_status = s,
                Event::IndexProgress(p) => self.index_progress = Some(p),
                Event::SearchStatus(s) => self.status = s,
                Event::SearchError(s) => {
                    self.error = Some(s.clone());
                    self.status = s;
                    self.searching = false;
                }
            }
        }
        while let Ok(t) = self.thumb_rx.try_recv() {
            let key = (t.path.clone(), t.large);
            self.requested.remove(&key);
            if let Some(i) = t.image {
                self.failed_pictures.remove(&key);
                if self.textures.len() > 500 {
                    self.textures.clear();
                }
                let handle =
                    ctx.load_texture(t.path.to_string_lossy(), i, egui::TextureOptions::LINEAR);
                self.textures.insert((t.path, t.large), handle);
            } else {
                self.failed_pictures.insert(key);
            }
        }
    }
    fn request_picture(&mut self, path: &PathBuf, large: bool) {
        let key = (path.clone(), large);
        if self.failed_pictures.contains(&key) || !self.requested.insert(key.clone()) {
            return;
        }
        if self.thumb_tx.send(key.clone()).is_err() {
            self.requested.remove(&key);
            self.failed_pictures.insert(key);
        }
    }
    fn picture(
        &mut self,
        ui: &mut egui::Ui,
        path: &PathBuf,
        size: Vec2,
        large: bool,
    ) -> egui::Response {
        let k = (path.clone(), large);
        if let Some(t) = self.textures.get(&k) {
            let fit = t.size_vec2();
            let factor = (size.x / fit.x).min(size.y / fit.y).min(1.);
            ui.add(egui::Image::new((t.id(), fit * factor)).sense(egui::Sense::click()))
        } else {
            let failed = self.failed_pictures.contains(&k);
            self.request_picture(path, large);
            let response = ui.add_sized(
                size,
                egui::Label::new(RichText::new(if failed { "Preview unavailable · Click to retry" }
                    else { "Loading preview…" }).weak()).sense(egui::Sense::click()),
            );
            if failed && response.clicked() {
                self.failed_pictures.remove(&k);
                self.request_picture(path, large);
            }
            response
        }
    }
    fn reference(&mut self, ui: &mut egui::Ui) {
        egui::Frame::group(ui.style()).show(ui, |ui| {
            ui.vertical(|ui| {
                ui.set_width(ui.available_width().min(255.));
                ui.label(
                    RichText::new("REFERENCE IMAGE")
                        .small()
                        .strong()
                        .color(ui.visuals().hyperlink_color),
                );
                if let Some(p) = self.q.image.clone() {
                    self.picture(
                        ui,
                        &p,
                        Vec2::new(ui.available_width().min(240.), 150.),
                        false,
                    );
                    ui.add(
                        egui::Label::new(
                            RichText::new(p.file_name().unwrap_or_default().to_string_lossy())
                                .small(),
                        )
                        .truncate(),
                    )
                    .on_hover_text(display_path(&p));
                    ui.horizontal_wrapped(|ui| {
                        if ui.small_button("Change…").clicked() {
                            if let Some(p) = platform::choose(false) {
                                self.q.image = Some(p);
                            }
                        }
                        if ui.small_button("Clear").clicked() {
                            self.q.image = None;
                        }
                    });
                } else {
                    ui.add_space(20.);
                    ui.label("Match a whole image by subject, style, pose and more.");
                    ui.add_space(12.);
                    if ui.button("Choose an image…").clicked() {
                        if let Some(p) = platform::choose(false) {
                            self.q.image = Some(p);
                        }
                    }
                    ui.small("You can also drop an image here.");
                    ui.add_space(20.);
                }
            });
        });
    }
    fn wants_ui(&mut self, ui: &mut egui::Ui) {
        ui.add(
            egui::TextEdit::multiline(&mut self.q.text)
                .desired_rows(2)
                .desired_width(ui.available_width())
                .hint_text("Describe what you want to find"),
        );
        let mut remove = None;
        for (i, want) in self.wants.iter_mut().enumerate() {
            ui.push_id(i, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.label(format!("Want {}", i + 2));
                    if ui.small_button("Remove").clicked() { remove = Some(i); }
                });
                ui.add(
                    egui::TextEdit::multiline(want)
                        .desired_rows(1)
                        .desired_width(ui.available_width())
                        .hint_text("Another thing to search for…"),
                );
            });
        }
        if let Some(i) = remove { self.wants.remove(i); }
        ui.horizontal_wrapped(|ui| {
            if ui.add_enabled(self.wants.len() < 19, egui::Button::new("+ Want")).clicked() {
                self.wants.push(String::new());
            }
        });
        if !self.wants.is_empty() || self.q.text.lines().filter(|s| !s.trim().is_empty()).count() > 1 {
            if self.q.image.is_none() {
                egui::ComboBox::from_id_salt("combine-wants")
                    .width(ui.available_width().min(280.))
                    .truncate()
                    .selected_text(if self.q.combine_max { "Match any" } else { "Match all" })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.q.combine_max, false, "Match all");
                        ui.selectable_value(&mut self.q.combine_max, true, "Match any");
                    });
                ui.small(if self.q.combine_max {
                    "Match the strongest individual prompt."
                } else {
                    "Combine the prompts into one search."
                });
            } else {
                ui.small("With a reference image, all text prompts are combined before blending with the image.");
            }
        }
    }
    fn search_ui(&mut self, ui: &mut egui::Ui) {
        ui.add(egui::Label::new(RichText::new("Find what matters in an image").heading()).wrap());
        ui.label(RichText::new("Search your folders with text, a full image, or both.").weak());
        ui.add_space(10.);
        let narrow = ui.available_width()
            < (285. + ui.text_style_height(&egui::TextStyle::Body) * 25.).max(650.);
        if narrow {
            self.reference(ui);
        }
        ui.with_layout(
            if narrow {
                egui::Layout::top_down(egui::Align::Min)
            } else {
                egui::Layout::left_to_right(egui::Align::Min)
            },
            |ui| {
                if !narrow {
                    self.reference(ui);
                }
                ui.vertical(|ui| {
                    ui.set_width(ui.available_width());
                    ui.label("What are you looking for?");
                    self.wants_ui(ui);
                    if self.q.image.is_some() {
                        ui.label("Match this aspect");
                        ui.horizontal_wrapped(|ui| {
                            for (id, label) in [
                                ("similarity", "Similarity"),
                                ("subject", "Subject"),
                                ("style", "Style"),
                                ("pose", "Pose"),
                                ("viewpoint", "Viewpoint"),
                                ("composition", "Composition"),
                                ("color", "Color"),
                                ("lighting", "Lighting"),
                            ] {
                                ui.selectable_value(&mut self.q.aspect, id.into(), label);
                            }
                        });
                        if self.q.aspect == "style" {
                            egui::ComboBox::from_id_salt("style-method")
                                .width(ui.available_width().min(330.))
                                .truncate()
                                .selected_text(match self.q.method.as_str() {
                                    "current" => "Current style adapter",
                                    "baseline" => "Existing aspect method",
                                    _ => "Contrastive style adapter · default",
                                })
                                .show_ui(ui, |ui| {
                                    ui.selectable_value(
                                        &mut self.q.method,
                                        "contrastive".into(),
                                        "Contrastive style adapter · default",
                                    );
                                    ui.selectable_value(
                                        &mut self.q.method,
                                        "current".into(),
                                        "Current style adapter",
                                    );
                                    ui.selectable_value(
                                        &mut self.q.method,
                                        "baseline".into(),
                                        "Existing aspect method",
                                    );
                                });
                        }
                        if self.q.aspect != "similarity" {
                            ui.label("Aspect strength");
                            ui.add(
                                egui::Slider::new(&mut self.q.strength, 0.0..=1.0)
                                    .custom_formatter(|v, _| format!("{:.0}%", v * 100.)),
                            );
                        }
                        if !self.q.text.trim().is_empty() || self.wants.iter().any(|s| !s.trim().is_empty()) {
                            ui.label("Image weight");
                            ui.add(egui::Slider::new(&mut self.q.image_weight, 0.0..=1.0));
                        }
                    }
                    ui.add_space(5.);
                    ui.horizontal_wrapped(|ui| {
                        if ui
                            .add_enabled(
                                self.ready && !self.searching,
                                egui::Button::new(
                                    RichText::new("Search library")
                                        .strong()
                                        .color(Color32::WHITE),
                                )
                                .fill(Color32::from_rgb(35, 102, 155))
                                .min_size(Vec2::new(160., 38.)),
                            )
                            .clicked()
                        {
                            self.do_search();
                        }
                        if self.has_folder_filters() && ui.add_enabled(self.ready && !self.searching,
                            egui::Button::new("Search all folders")).on_hover_text("Clear folder filters and search the whole library").clicked()
                        {
                            self.search_all_folders();
                        }
                        if self.searching {
                            ui.spinner();
                            if self.ready && ui.small_button("Cancel").clicked() {
                                self.search_cancel.store(true, Ordering::Relaxed);
                            }
                        }
                    });
                    if let Some(folder) = &self.q.only_folder {
                        let path = display_path(folder);
                        ui.add(egui::Label::new(format!("Folder only · {path}")).truncate()).on_hover_text(path);
                    }
                });
            },
        );
        let refine = egui::CollapsingHeader::new(if self.q.ignore_image.is_some() {
            "Refine · negative image active"
        } else { "Refine results & search scope" })
            .id_salt("refine-results");
        let refine = if self.o.gui_smoke && (self.smoke_stage == 12
            || (std::env::args().any(|s| s == "--feature-gui-check") && matches!(self.smoke_stage, 5 | 8))) {
            refine.open(Some(true))
        } else { refine };
        refine.show(ui, |ui| {
            self.negative_image_ui(ui);
            ui.horizontal_wrapped(|ui| {
                for k in ["image", "text", "document", "audio", "video"] {
                    let mut on = self.q.kinds.contains(k);
                    if ui.checkbox(&mut on, k).changed() {
                        if on {
                            self.q.kinds.insert(k.into());
                        } else {
                            self.q.kinds.remove(k);
                        }
                    }
                }
            });
            ui.label("Ignore these concepts (one per line)");
            ui.add(
                egui::TextEdit::multiline(&mut self.q.negative)
                    .desired_rows(1)
                    .desired_width(ui.available_width()),
            );
            ui.label("Text exclusion weight");
            ui.add(egui::Slider::new(&mut self.q.negative_weight, 0.0..=2.0));
            self.folder_scope_ui(ui);
        });
        ui.separator();
        ui.horizontal_wrapped(|ui| {
            ui.label(RichText::new(format!("{} results", self.hits.len())).strong());
            ui.label(
                RichText::new("Similarity values, not confidence.")
                    .small()
                    .weak(),
            );
        });
        ui.horizontal_wrapped(|ui| {
            if navigation_button(ui, "Next", true,
                (self.page + 1) * self.gui.results_per_page < self.hits.len()).clicked()
            {
                self.page += 1;
            }
            ui.label(format!("Page {}", self.page + 1));
            if navigation_button(ui, "Previous", false, self.page > 0).clicked()
            {
                self.page -= 1;
            }
        });
        let hits = self
            .hits
            .iter()
            .skip(self.page * self.gui.results_per_page)
            .take(self.gui.results_per_page)
            .cloned()
            .collect::<Vec<_>>();
        if hits.is_empty() {
            ui.add_space(25.);
            ui.label(
                RichText::new(if self.roots.is_empty() {
                    "Add a folder in Library & indexing to start."
                } else {
                    "Choose a reference or enter text, then search."
                })
                .weak(),
            );
            return;
        }
        let columns = ((ui.available_width() / self.gui.card_width).floor() as usize).max(1);
        let card_width = (ui.available_width() - (columns - 1) as f32 * 10.) / columns as f32;
        ui.scope(|ui| {
            for row in hits.chunks(columns) {
                ui.horizontal_top(|ui| {
                    for h in row {
                        egui::Frame::group(ui.style())
                            .fill(ui.visuals().window_fill())
                            .show(ui, |ui| {
                                ui.vertical(|ui| {
                                    ui.set_width((card_width - 20.).max(100.));
                                    ui.set_min_height(self.gui.thumbnail_height + 76.);
                                    if h.kind == "image" {
                                        if self
                                            .picture(
                                                ui,
                                                &h.path,
                                                Vec2::new(
                                                    (card_width - 20.).max(100.),
                                                    self.gui.thumbnail_height,
                                                ),
                                                false,
                                            )
                                            .clicked()
                                        {
                                            self.preview = Some(h.path.clone());
                                        }
                                    } else {
                                        ui.add_space(20.);
                                        ui.label(
                                            RichText::new(h.kind.to_uppercase()).size(24.).weak(),
                                        );
                                        ui.label(h.snippet.chars().take(95).collect::<String>());
                                        ui.add_space(15.);
                                    }
                                    let title =
                                        h.path.file_name().unwrap_or_default().to_string_lossy();
                                    if self.gui.show_scores {
                                        ui.label(
                                            RichText::new(format!("{:.1}%", h.score * 100.))
                                                .strong()
                                                .color(if ui.visuals().dark_mode {
                                                    Color32::from_rgb(109, 195, 212)
                                                } else {
                                                    Color32::from_rgb(22, 92, 133)
                                                }),
                                        );
                                    }
                                    ui.add(egui::Label::new(title).truncate())
                                        .on_hover_text(display_path(&h.path));
                                    if self.gui.show_paths {
                                        ui.add(
                                            egui::Label::new(
                                                RichText::new(display_path(&h.path))
                                                    .small()
                                                    .weak(),
                                            )
                                            .truncate(),
                                        )
                                        .on_hover_text(display_path(&h.path));
                                    }
                                    ui.horizontal_wrapped(|ui| {
                                        if h.kind == "image"
                                            && ui
                                                .small_button("Use image")
                                                .on_hover_text("Use as reference and search again")
                                                .clicked()
                                        {
                                            self.q.image = Some(h.path.clone());
                                            self.do_search();
                                        }
                                        if ui.small_button("Show file").clicked() {
                                            platform::reveal(&h.path, true);
                                        }
                                    });
                                    if self.gui.show_scores && h.penalty.abs() > 0.00001 {
                                        ui.small(format!(
                                            "Match {:.1}% · penalty {:.1}%",
                                            h.positive * 100.,
                                            h.penalty * 100.
                                        ));
                                    }
                                });
                            });
                    }
                });
            }
        });
    }
    fn folder_scope_ui(&mut self, ui: &mut egui::Ui) {
        let heading = ui.label(RichText::new("Folders and subfolders").strong());
        if self.o.gui_smoke && matches!(self.smoke_stage, 5 | 8) && std::env::args().any(|s| s == "--feature-gui-check") {
            heading.scroll_to_me(Some(egui::Align::Min));
        }
        ui.small("Open a folder with +. Include and Skip apply to subfolders, and a deeper rule takes precedence. With no Include rules, Refseek searches all folders except those you skip.");
        ui.horizontal_wrapped(|ui| {
            if ui.add_enabled(!self.expanded_folders.is_empty(), egui::Button::new("Collapse all").small()).clicked() {
                self.expanded_folders.clear();
            }
            if ui.add_enabled(self.has_folder_filters() && !self.searching,
                egui::Button::new("Clear folder filters").small()).clicked() {
                self.search_all_folders();
            }
            ui.small(format!("{} included · {} skipped", self.scope.len(), self.excluded_scope.len()));
        });
        let folders = self.folder_tree.visible_rows(&self.expanded_folders);
        let height = ui.text_style_height(&egui::TextStyle::Body).max(ui.spacing().interact_size.y);
        let toggle_size = egui::vec2(height + 2. * ui.spacing().button_padding.x, height);
        egui::ScrollArea::vertical().id_salt("search-folder-tree").max_height(260.)
            .show_rows(ui, height, folders.len(), |ui, rows| {
                for i in rows {
                    let (i, depth) = folders[i];
                    let folder = &self.folder_tree.folders[i];
                    let k = &self.folder_tree.keys[i];
                    ui.push_id(k, |ui| {
                        ui.horizontal(|ui| {
                            ui.add_space((depth as f32 * 14.).min(ui.available_width() * 0.25));
                            if self.folder_tree.children[i].is_empty() {
                                ui.allocate_space(toggle_size);
                            } else {
                                let open = self.expanded_folders.contains(k);
                                let label = if open { "-" } else { "+" };
                                if ui.add(egui::Button::new(label).small().min_size(toggle_size))
                                    .on_hover_text(if open { "Collapse subfolders" } else { "Expand subfolders" }).clicked()
                                {
                                    if open { self.expanded_folders.remove(k); }
                                    else { self.expanded_folders.insert(k.clone()); }
                                }
                            }
                            let mut include = self.scope.contains(k);
                            let mut exclude = self.excluded_scope.contains(k);
                            if ui.checkbox(&mut include, "Include").changed() {
                                self.q.only_folder = None;
                                if include {self.scope.insert(k.clone()); self.excluded_scope.remove(k); exclude = false;}
                                else {self.scope.remove(k);}
                            }
                            if ui.checkbox(&mut exclude, "Skip").changed() {
                                self.q.only_folder = None;
                                if exclude {self.excluded_scope.insert(k.clone()); self.scope.remove(k);}
                                else {self.excluded_scope.remove(k);}
                            }
                            let label = if depth == 0 {display_path(&folder.path)}
                                else {folder.path.file_name().unwrap_or_default().to_string_lossy().into_owned()};
                            let unit = if folder.files == 1 { "file" } else { "files" };
                            ui.add(egui::Label::new(format!("{label} · {} {unit}", folder.files)).truncate())
                                .on_hover_text(display_path(&folder.path));
                        });
                    });
                }
            });
    }
    fn negative_image_ui(&mut self, ui: &mut egui::Ui) {
        egui::Frame::group(ui.style()).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.label(RichText::new("Negative image · optional").strong());
            ui.small(
                "Reduce matches that resemble this image. This works with text or image searches.",
            );
            ui.horizontal_wrapped(|ui| {
                if ui
                    .button(if self.q.ignore_image.is_some() {
                        "Change negative image…"
                    } else {
                        "Choose negative image…"
                    })
                    .clicked()
                {
                    if let Some(p) = platform::choose(false) {
                        self.q.ignore_image = Some(p);
                    }
                }
                if self.q.ignore_image.is_some() && ui.button("Clear negative image").clicked() {
                    self.q.ignore_image = None;
                }
            });
            if let Some(p) = self.q.ignore_image.clone() {
                self.picture(
                    ui,
                    &p,
                    Vec2::new(ui.available_width().min(160.), 100.),
                    false,
                );
                ui.add(
                    egui::Label::new(p.file_name().unwrap_or_default().to_string_lossy())
                        .truncate(),
                )
                .on_hover_text(display_path(&p));
                ui.label("Negative image weight");
                ui.add(egui::Slider::new(&mut self.q.ignore_weight, 0.0..=2.));
            }
        });
    }
    fn index_meter(&self, ui: &mut egui::Ui) {
        if let Some(p) = &self.index_progress {
            ui.label(format!("Embedding backend: {} · {}-dimension index", self.backend, self.settings_dimension));
            ui.small("Folder scanning and image preparation use the CPU; embeddings use the backend shown above.");
            let duration = |s: f64| {
                let n = s.max(0.) as u64;
                format!("{}:{:02}", n / 60, n % 60)
            };
            ui.label(
                RichText::new(format!("{} · {} / {} files", p.phase, p.processed, p.total))
                    .strong(),
            );
            if p.phase == "Scanning" {
                ui.spinner();
                ui.label(format!(
                    "{} files checked · {} unchanged · elapsed {}",
                    p.total,
                    p.metadata_skipped,
                    duration(p.elapsed_seconds)
                ));
            } else {
                if p.metadata_skipped > 0 {
                    ui.small(format!("{} unchanged files reused from the saved index without reading their contents.", p.metadata_skipped));
                }
                ui.add(
                    egui::ProgressBar::new(if p.total > 0 {
                        p.processed as f32 / p.total as f32
                    } else {
                        0.
                    })
                    .show_percentage(),
                );
                ui.horizontal_wrapped(|ui| {
                    ui.label(format!("{:.2} files/s processed", p.files_per_second));
                    ui.label(format!("{:.2} new files/s", p.new_files_per_second));
                    ui.label(format!(
                        "{} embedded · {} cached · {} skipped",
                        p.embedded, p.reused, p.failed
                    ));
                    ui.label(format!("Elapsed {}", duration(p.elapsed_seconds)));
                    if let Some(eta) = p.eta_seconds {
                        ui.label(format!("ETA {}", duration(eta)));
                    }
                });
            }
            ui.add(egui::Label::new(RichText::new(&self.index_status).small()).truncate())
                .on_hover_text(&self.index_status);
            if self.busy {
                ui.small("Search remains available using completed snapshots. Newly indexed files appear at checkpoints.");
                if p.phase == "Indexing" && p.total > 0 {
                    let (search, cache) = vector_storage(p.total, self.settings_dimension);
                    ui.small(format!("This scan: about {} of vectors for {} files, assuming one vector per file. Metadata is extra.",
                        storage_size(search.saturating_add(cache)), p.total));
                }
            }
        }
    }
    fn move_preview(&mut self, delta: isize) {
        let images = self
            .hits
            .iter()
            .filter(|h| h.kind == "image")
            .map(|h| h.path.clone())
            .collect::<Vec<_>>();
        if images.is_empty() {
            return;
        }
        let at = images
            .iter()
            .position(|p| Some(p) == self.preview.as_ref())
            .unwrap_or(0);
        let next = (at as isize + delta).clamp(0, images.len() as isize - 1) as usize;
        self.preview = Some(images[next].clone());
        if let Some(i) = self.hits.iter().position(|h| h.path == images[next]) {
            self.page = i / self.gui.results_per_page;
        }
    }
    fn preview_ui(&mut self, ctx: &egui::Context) {
        let Some(path) = self.preview.clone() else {
            return;
        };
        // fixed_size describes the content; reserve room for the outer frame/title too.
        let area = ctx.content_rect().shrink(32.);
        let size = Vec2::new(area.width().min(960.), area.height().min(760.));
        let rect = egui::Rect::from_center_size(area.center(), size);
        let mut open = true;
        let images = self
            .hits
            .iter()
            .filter(|h| h.kind == "image")
            .map(|h| h.path.clone())
            .collect::<Vec<_>>();
        let at = images.iter().position(|p| *p == path).unwrap_or(0);
        let shown = egui::Window::new("Image preview")
            .id(egui::Id::new("stable-image-preview"))
            .open(&mut open)
            .collapsible(false)
            .fixed_rect(rect)
            .order(egui::Order::Foreground)
            .show(ctx, |ui| {
                let canvas_height = (ui.available_height()
                    - ui.text_style_height(&egui::TextStyle::Body) * 6.
                    - 100.)
                    .max(40.);
                let (canvas, _) = ui.allocate_exact_size(
                    Vec2::new(ui.available_width(), canvas_height),
                    egui::Sense::hover(),
                );
                let key = (path.clone(), true);
                if let Some(texture) = self.textures.get(&key) {
                    let original = texture.size_vec2();
                    let ratio = (canvas.width() / original.x).min(canvas.height() / original.y);
                    let target = egui::Rect::from_center_size(canvas.center(), original * ratio);
                    ui.painter().image(
                        texture.id(),
                        target,
                        egui::Rect::from_min_max(egui::Pos2::ZERO, egui::pos2(1., 1.)),
                        Color32::WHITE,
                    );
                } else {
                    self.request_picture(&path, true);
                    ui.painter().text(
                        canvas.center(),
                        egui::Align2::CENTER_CENTER,
                        if self.failed_pictures.contains(&key) { "Preview unavailable. Use Retry image below." }
                            else { "Loading image…" },
                        egui::FontId::proportional(self.gui.text_size),
                        ui.visuals().text_color(),
                    );
                }
                ui.horizontal_wrapped(|ui| {
                    if self.failed_pictures.contains(&(path.clone(), true)) && ui.button("Retry image").clicked() {
                        self.failed_pictures.remove(&(path.clone(), true));
                        self.request_picture(&path, true);
                    }
                    if navigation_button(ui, "Previous", false, at > 0).clicked()
                    {
                        self.move_preview(-1);
                    }
                    ui.label(format!("{} / {}", at + 1, images.len()));
                    if navigation_button(ui, "Next", true, at + 1 < images.len()).clicked()
                    {
                        self.move_preview(1);
                    }
                });
                ui.add(
                    egui::Label::new(
                        RichText::new(path.file_name().unwrap_or_default().to_string_lossy())
                            .strong(),
                    )
                    .truncate(),
                )
                .on_hover_text(display_path(&path));
                ui.horizontal_wrapped(|ui| {
                    if ui
                        .add_enabled(!self.searching, egui::Button::new("Use reference"))
                        .clicked()
                    {
                        self.q.image = Some(path.clone());
                        self.preview = None;
                        self.do_search();
                    }
                    if ui.button("Use negative").clicked() {
                        self.q.ignore_image = Some(path.clone());
                        self.preview = None;
                    }
                    if let Some(folder) = path.parent() {
                        if ui.add_enabled(self.ready && !self.searching, egui::Button::new("Search this folder only"))
                            .on_hover_text(format!("Run the current search in {} without subfolders", display_path(folder)))
                            .clicked()
                        {
                            self.search_picture_folder(folder.to_path_buf());
                        }
                    }
                    if ui.button("Open original").clicked() {
                        platform::reveal(&path, false);
                    }
                });
                ui.small("Arrow keys: previous / next · Esc: close");
            });
        if let Some(r) = shown {
            self.last_preview_rect = Some(r.response.rect);
        }
        if !open {
            self.preview = None;
        }
    }
    fn library_ui(&mut self, ui: &mut egui::Ui) {
        ui.heading("Library & indexing");
        ui.label(RichText::new("Each folder has its own index and vectors. Unchanged images reuse their embeddings.").weak());
        ui.add_space(15.);
        ui.add(
            egui::TextEdit::singleline(&mut self.index_path)
                .desired_width(ui.available_width())
                .hint_text("Folder path"),
        );
        ui.horizontal_wrapped(|ui| {
            if ui.button("Browse…").clicked() {
                if let Some(p) = platform::choose(true) {
                    self.index_path = display_path(&p);
                }
            }
            if ui
                .add_enabled(
                    self.ready && !self.busy && !self.index_path.trim().is_empty() && !self.kinds.is_empty(),
                    egui::Button::new("Add & index"),
                )
                .clicked()
            {
                self.send(Command::Index(
                    vec![PathBuf::from(self.index_path.trim().trim_matches('"'))],
                    self.kinds.clone(),
                ));
            }
        });
        let before_kinds=self.kinds.clone();
        ui.horizontal_wrapped(|ui| {
            for k in crate::preferences::INDEX_KINDS {
                let mut on = self.kinds.contains(k);
                if ui.checkbox(&mut on, k).changed() {
                    if on {
                        self.kinds.insert(k.into());
                    } else {
                        self.kinds.remove(k);
                    }
                }
            }
            ui.small("Audio/video are experimental model inputs.");
        });
        if self.kinds!=before_kinds {
            if let Err(e)=crate::preferences::save_index_kinds(&self.o.home.join("data/settings.json"),&self.kinds){self.error=Some(e);}
        }
        if self.kinds.is_empty(){ui.small("Select at least one file type to index.");}
        ui.add_space(8.);
        ui.horizontal_wrapped(|ui| {
            if ui
                .add_enabled(
                    self.ready && !self.busy && !self.roots.is_empty() && !self.kinds.is_empty(),
                    egui::Button::new("Update all folders"),
                )
                .clicked()
            {
                self.send(Command::Index(
                    self.roots.iter().map(|r| r.path.clone()).collect(),
                    self.kinds.clone(),
                ));
            }
            if self.busy && !self.cleanup_running {
                ui.spinner();
                ui.label("Complete files are checkpointed.");
                if self.ready && ui.button("Cancel indexing").clicked() {
                    self.cancel.store(true, Ordering::Relaxed);
                }
            }
        });
        self.index_meter(ui);
        ui.separator();
        self.library_reports(ui);
        ui.separator();
        let roots = self.roots.clone();
        ui.scope(|ui| {
            for r in roots {
                egui::Frame::group(ui.style()).show(ui, |ui| {
                    ui.set_width(ui.available_width() - 20.);
                    ui.add(
                        egui::Label::new(RichText::new(display_path(&r.path)).strong())
                            .truncate(),
                    )
                    .on_hover_text(display_path(&r.path));
                    ui.label(format!(
                        "{} files · {} vectors · independent snapshot",
                        r.files, r.vectors
                    ));
                    ui.label(format!("Saved index: {} · Indexed originals: {}",
                        storage_size(r.index_bytes), storage_size(r.source_bytes)))
                        .on_hover_text("Saved index includes metadata, search vectors and any style cache for the active snapshot. Indexed originals are measured at the last scan; earlier snapshots and model files are separate.");
                    ui.horizontal_wrapped(|ui| {
                        if ui
                            .add_enabled(
                                self.ready && !self.busy && !self.kinds.is_empty(),
                                egui::Button::new("Update this folder"),
                            )
                            .on_hover_text("Fast update compares saved file size and modified date. Use Verify file contents to detect edits that preserve both.")
                            .clicked()
                        {
                            self.send(Command::Index(vec![r.path.clone()], self.kinds.clone()));
                        }
                        if ui.add_enabled(self.ready && !self.busy && !self.kinds.is_empty(), egui::Button::new("Verify file contents"))
                            .on_hover_text("Reads and hashes every file, then indexes any changed content. Use when files were edited while preserving their size and modified date.")
                            .clicked() {
                            self.send(Command::Verify(vec![r.path.clone()], self.kinds.clone()));
                        }
                        if ui.small_button("Open folder").clicked() {
                            platform::reveal(&r.path, false);
                        }
                        if ui
                            .add_enabled(
                                self.ready && !self.busy,
                                egui::Button::new("Remove from library"),
                            )
                            .clicked()
                        {
                            self.send(Command::Remove(r.path.clone()));
                        }
                    });
                    ui.add(
                        egui::Label::new(
                            RichText::new(display_path(&self.o.data.join(&r.snapshot)))
                                .small(),
                        )
                        .truncate(),
                    );
                });
            }
            if self.roots.is_empty() {
                ui.label("No folders indexed yet. Add a folder above.");
            }
        });
    }
    fn library_reports(&mut self, ui: &mut egui::Ui) {
        ui.horizontal_wrapped(|ui| {
            if ui.add_enabled(self.ready && !self.busy && self.o.data.join("catalog.json").is_file(),
                egui::Button::new("Clean up old snapshots")).clicked() {
                self.send(Command::CleanupSnapshots);
            }
            let label = if self.index_details.is_some() { "Hide latest indexing details" } else { "Show latest indexing details" };
            if ui.button(label).clicked() {
                self.toggle_index_details();
            }
        });
        if self.cleanup_running {
            ui.label("Checking the active and recovery indexes, then removing old snapshots…");
        } else if let Some(result) = &self.last_cleanup {
            match result {
                Ok(r) if r.candidates == 0 => {
                    ui.label("Last cleanup: no old snapshots to remove. The active index and any recovery copy are already kept.");
                }
                Ok(r) => {
                    ui.label(format!("Last cleanup: removed {} old snapshots and freed {}.", r.removed, storage_size(r.freed_bytes)));
                    if r.failed != 0 {
                        ui.label(format!("{} snapshots could not be fully removed. Try cleanup again.", r.failed));
                    }
                }
                Err(e) => { ui.colored_label(ui.visuals().error_fg_color, format!("Cleanup failed: {e}")); }
            }
        } else {
            ui.small("Refseek keeps the active index and one recovery copy. It removes older snapshots after each changed index is saved.");
        }
        if let Some(details) = &self.index_details {
            match details {
                Err(e) => { ui.label(e); }
                Ok(stats) => {
                    ui.label(format!("Last scan {} · {} of {} files processed in {:.1} seconds",
                        if stats.error.is_some() { "failed" } else if stats.cancelled { "cancelled" } else { "finished" }, stats.processed, stats.discovered, stats.elapsed_seconds));
                    if let Some(error) = &stats.error { ui.label(error); }
                    ui.label(format!("{} embedded · {} reused · {} skipped · {} removed from index",
                        stats.embedded, stats.reused, stats.failed, stats.removed));
                    if stats.index_dimension != 0 {
                        ui.small(format!("{} dimensions · {:.1} s embedding · {:.1} s saving snapshots",
                            stats.index_dimension, stats.embedding_seconds, stats.snapshot_seconds));
                    }
                    if stats.preparation_workers>0 {
                        ui.small(format!("{} CPU preparation workers · {:.1} s waiting for prepared files",stats.preparation_workers,stats.preparation_wait_seconds));
                    }
                    if !stats.warnings.is_empty() {
                        egui::CollapsingHeader::new(format!("Warnings ({})", stats.warnings.len())).show(ui, |ui| {
                            for warning in &stats.warnings {
                                ui.label(warning);
                            }
                        });
                    }
                }
            }
        }
    }
    fn settings_ui(&mut self, ui: &mut egui::Ui) {
        ui.heading("Settings");
        ui.label("Appearance changes apply immediately and are saved on this PC.");
        self.appearance_ui(ui);
        ui.add_space(12.);
        ui.separator();
        let model_heading = ui.heading("Local model");
        if self.o.gui_smoke && ((self.smoke_stage == 6 && std::env::args().any(|s| s == "--feature-gui-check"))
            || (self.smoke_stage == 14 && std::env::args().any(|s|s=="--interaction-smoke"))) {
            model_heading.scroll_to_me(Some(egui::Align::Min));
        }
        ui.add_space(12.);
        ui.label(RichText::new(&self.backend).strong().size(20.));
        ui.label("EmbeddingGemma 2 Q8 · native output 768 dimensions · llama.cpp b11741");
        ui.add_space(12.);
        ui.label(RichText::new("Image preparation workers").strong());
        let before_workers=self.prepare_workers;
        ui.add_enabled_ui(self.ready && !self.busy,|ui|{
            ui.horizontal_wrapped(|ui|{
                let mut automatic=self.prepare_workers==0;
                if ui.checkbox(&mut automatic,"Auto").changed(){
                    self.prepare_workers=if automatic {0}else{crate::image_pipeline::worker_count(crate::image_pipeline::DEFAULT_WORKERS)};
                }
                let mut custom=self.prepare_workers.max(1);
                if ui.add_enabled(!automatic,egui::DragValue::new(&mut custom)
                    .range(1..=crate::image_pipeline::maximum_workers()).speed(1.)
                    .update_while_editing(false).suffix(" CPU workers")).changed(){self.prepare_workers=custom;}
                if automatic {ui.label(format!("{} workers",crate::image_pipeline::worker_count(0)));}
            });
        });
        ui.small(format!("Default: 2 CPU workers. Enter any count from 1 to {}. Changes apply to the next indexing job and are remembered.",crate::image_pipeline::maximum_workers()));
        ui.small("Prepares full images on the CPU while the GPU embeds earlier images. A bounded queue limits memory use.");
        if before_workers!=self.prepare_workers {
            match crate::preferences::merge(&self.o.home.join("data/settings.json"),"prepare_workers",serde_json::json!(self.prepare_workers)) {
                Ok(())=>self.send(Command::PreparationWorkers(self.prepare_workers)),Err(e)=>self.error=Some(e),
            }
        }
        ui.add_space(12.);
        ui.label(RichText::new("Index dimensions").strong());
        let before_dimension = self.settings_dimension;
        ui.add_enabled_ui(self.ready && !self.busy && !self.searching && !self.dimension_locked, |ui| {
            egui::ComboBox::from_id_salt("index-dimension")
                .selected_text(format!("{} dimensions", self.settings_dimension))
                .show_ui(ui, |ui| {
                    for d in [768, 512, 256, 128] {
                        ui.selectable_value(&mut self.settings_dimension, d,
                            format!("{d}{}", if d == DEFAULT_INDEX_DIMENSION {" · default"} else {""}));
                    }
                });
        });
        if before_dimension != self.settings_dimension { self.send(Command::Dimension(self.settings_dimension)); }
        if self.dimension_locked {
            ui.small(format!("Detected existing {}-dimension library. Its dimension is kept; use a new library folder for a different size.", self.settings_dimension));
        } else { ui.small("Choose before indexing. New libraries default to 768; all folders in a library use the same dimension."); }
        ui.label(RichText::new("Estimated vector storage for 10,000 files").strong());
        for dimension in INDEX_DIMENSIONS {
            let (search, cache) = vector_storage(10_000, dimension);
            let label = if cache == 0 {
                format!("{dimension}: {} total (no separate style cache)", storage_size(search))
            } else {
                format!("{dimension}: {} total ({} search + {} style)",
                    storage_size(search.saturating_add(cache)), storage_size(search), storage_size(cache))
            };
            ui.small(label);
        }
        ui.small("Assumes one vector per file, as with images. Documents can use more. Metadata and the recovery copy are extra. Smaller dimensions keep a separate 768-dimension cache for trained Style; at 768, search and Style share the same vectors.");
        if self.settings_dimension == 128 { ui.colored_label(Color32::YELLOW, "128 dimensions reduces image retrieval quality; prefer 768 for artwork."); }
        ui.add_space(15.);
        ui.label("Backend for the next launch");
        let before = self.settings_backend.clone();
        egui::ComboBox::from_id_salt("backend")
            .selected_text(&self.settings_backend)
            .show_ui(ui, |ui| {
                for (k, l) in [
                    ("auto", "Auto (CUDA, Vulkan, CPU)"),
                    ("cuda", "CUDA"),
                    ("vulkan", "Vulkan"),
                    ("cpu", "CPU"),
                ] {
                    ui.selectable_value(&mut self.settings_backend, k.into(), l);
                }
            });
        if before != self.settings_backend {
            if let Err(e) = crate::preferences::merge(
                &self.o.home.join("data/settings.json"),
                "backend",
                serde_json::json!(self.settings_backend),
            ) {
                self.error = Some(e);
            }
        }
        ui.small(
            "Auto tries real text and image inference before accepting a device. Restart to apply.",
        );
        ui.add_space(18.);
        ui.label(RichText::new("Search defaults").strong());
        ui.label("Style: Contrastive style adapter, with its saved 50% blend.");
        ui.label("Subject, pose and other aspects: existing aspect projections.");
        ui.label("Similarity: original model embeddings.");
        ui.add_space(12.);
        ui.label("Style adapters retain the original 84-artist / 12,940-image full-data fit. Q8 probes and scoring parity do not establish retrieval quality on new images. Pose remains an experimental embedding projection.");
        ui.add_space(18.);
        ui.label(RichText::new("Everything stays beside the application").strong());
        ui.monospace(display_path(&self.o.home));
        ui.label(format!("Native library: {}", display_path(&self.o.data)));
        ui.horizontal_wrapped(|ui| {
            if ui.button("Show native data").clicked() {
                platform::reveal(&self.o.home.join("data"), false);
            }
            if ui.button("Show model logs").clicked() {
                platform::reveal(&self.o.home.join("data/logs"), false);
            }
        });
        ui.add_space(10.);
        ui.label("Rust / egui window. A private model process uses loopback. No browser or GUI web server. The native library is separate from earlier editions.");
    }
    fn appearance_ui(&mut self, ui: &mut egui::Ui) {
        ui.add_space(12.);
        ui.label(RichText::new("Display").strong());
        ui.label("UI scale · relative to Windows display scaling");
        ui.horizontal_wrapped(|ui| {
            ui.add(
                egui::Slider::new(&mut self.gui.scale, 0.75..=2.)
                    .step_by(0.05)
                    .custom_formatter(|v, _| format!("{:.0}%", v * 100.))
                    .custom_parser(|s| {
                        s.trim()
                            .trim_end_matches('%')
                            .parse::<f64>()
                            .ok()
                            .map(|v| v / 100.)
                    }),
            );
            if ui.small_button("100%").clicked() {
                self.gui.scale = 1.;
            }
        });
        ui.small("Ctrl + / Ctrl − to zoom · Ctrl 0 resets scaling");
        ui.label("Theme");
        ui.horizontal_wrapped(|ui| {
            for (key, label) in [
                ("dark", "Dark"),
                ("light", "Light"),
                ("system", "Follow Windows"),
            ] {
                ui.selectable_value(&mut self.gui.theme, key.into(), label);
            }
        });
        ui.label("Text size");
        ui.add(
            egui::Slider::new(&mut self.gui.text_size, 12.0..=22.0)
                .step_by(1.)
                .suffix(" pt"),
        );
        ui.checkbox(&mut self.gui.compact, "Compact spacing and buttons");
        ui.add_space(12.);
        ui.label(RichText::new("Search result layout").strong());
        ui.label("Preferred card width");
        ui.add(
            egui::Slider::new(&mut self.gui.card_width, 200.0..=400.0)
                .step_by(10.)
                .suffix(" pt"),
        );
        ui.label("Thumbnail height");
        ui.add(
            egui::Slider::new(&mut self.gui.thumbnail_height, 100.0..=280.0)
                .step_by(10.)
                .suffix(" pt"),
        );
        ui.small("Images keep their full frame and aspect ratio.");
        ui.label("Results per page");
        egui::ComboBox::from_id_salt("page-size")
            .selected_text(self.gui.results_per_page.to_string())
            .show_ui(ui, |ui| {
                for n in [12, 24, 48, 96] {
                    ui.selectable_value(&mut self.gui.results_per_page, n, n.to_string());
                }
            });
        ui.checkbox(
            &mut self.gui.show_scores,
            "Show similarity scores and penalties",
        );
        ui.checkbox(
            &mut self.gui.show_paths,
            "Show full file paths on result cards",
        );
        ui.add_space(8.);
        if ui.button("Reset appearance & layout").clicked() {
            self.gui = GuiSettings::default();
        }
        if self.gui_save_at.is_some() {
            ui.small("Saving preferences…");
        } else {
            ui.small("Preferences saved automatically.");
        }
    }
    fn flush_gui(&mut self) {
        if self.gui_save_at.is_some() {
            match self.gui.save(&self.o.home.join("data/settings.json")) {
                Ok(()) => self.gui_save_at = None,
                Err(e) => {
                    self.error = Some(format!("Could not save GUI preferences: {e}"));
                    self.gui_save_at = None;
                }
            }
        }
    }
    fn image_retry_check(&mut self) -> Res<()> {
        if self.closing.load(Ordering::Relaxed) {
            return Ok(());
        }
        if Instant::now() > self.smoke_deadline {
            return Err("Image retry verification timed out".into());
        }
        if !self.ready || self.busy {
            return Ok(());
        }
        let path = self.o.home.join("verification/retry-image.png");
        let small = (path.clone(), false);
        let large = (path.clone(), true);
        match self.smoke_stage {
            0 => {
                std::fs::create_dir_all(path.parent().unwrap()).map_err(err)?;
                if path.exists() { std::fs::remove_file(&path).map_err(err)?; }
                self.request_picture(&path, false);
                self.request_picture(&path, true);
                self.smoke_stage = 1;
            }
            1 if self.failed_pictures.contains(&small) && self.failed_pictures.contains(&large) => {
                if self.requested.contains(&small) || self.requested.contains(&large) {
                    return Err("Failed image requests remained pending".into());
                }
                crate::rpc::decoded(&self.assets.as_ref().unwrap().join("validation/b01-001.jpg"))?
                    .save(&path).map_err(err)?;
                self.failed_pictures.remove(&small);
                self.failed_pictures.remove(&large);
                self.request_picture(&path, false);
                self.request_picture(&path, true);
                self.smoke_stage = 2;
            }
            2 if self.textures.contains_key(&small) && self.textures.contains_key(&large) => {
                if self.requested.contains(&small) || self.requested.contains(&large)
                    || self.failed_pictures.contains(&small) || self.failed_pictures.contains(&large)
                {
                    return Err("Recovered images retained failed or pending state".into());
                }
                save_json(&self.o.report, &serde_json::json!({"small_recovered":true,"large_recovered":true,
                    "failed_requests_cleared":true,"backend":self.backend}))?;
                self.smoke_stage = 3;
                self.stop();
            }
            _ => {}
        }
        Ok(())
    }
    fn smoke(&mut self, ctx: &egui::Context) {
        let report_check = std::env::args().any(|s| s == "--library-report-check");
        if !self.o.gui_smoke {
            return;
        }
        if std::env::args().any(|s| s == "--image-retry-check") {
            if let Err(error) = self.image_retry_check() {
                let _ = save_json(&self.o.report, &serde_json::json!({"error":error}));
                self.stop();
            }
            return;
        }
        if std::env::args().any(|s| s == "--feature-gui-check") {
            self.feature_gui_check(ctx);
            return;
        }
        if std::env::args().any(|s| s == "--interaction-smoke") {
            self.interaction_smoke(ctx);
            return;
        }
        if Instant::now() > self.smoke_deadline || self.error.is_some() {
            let _ = save_json(
                &self.o.report,
                &serde_json::json!({"error":self.error,"stage":self.smoke_stage,"status":self.status}),
            );
            self.stop();
            return;
        }
        if self.ready && !self.busy && !self.searching {
            match self.smoke_stage {
                0 => {
                    if report_check {
                        self.diagnostic_evidence["existing_index_report"] = serde_json::to_value(self.load_index_details()).unwrap_or_default();
                    }
                    self.smoke_stage = 1;
                    let p = self.assets.as_ref().unwrap().join("validation");
                    self.send(Command::Index(
                        vec![p],
                        ["image"].into_iter().map(String::from).collect(),
                    ));
                }
                1 => {
                    self.smoke_stage = 2;
                    self.q.image =
                        Some(self.assets.as_ref().unwrap().join("validation/b01-001.jpg"));
                    self.q.aspect = "style".into();
                    self.do_search();
                }
                2 => {
                    if report_check {
                        self.smoke_stage = 6;
                        self.toggle_index_details();
                        self.send(Command::CleanupSnapshots);
                        return;
                    }
                    if std::env::args().any(|a| a == "--exercise-gui-preferences") {
                        self.gui = GuiSettings {
                            scale: 1.25,
                            theme: "light".into(),
                            text_size: 16.,
                            compact: true,
                            card_width: 280.,
                            thumbnail_height: 180.,
                            results_per_page: 48,
                            show_scores: false,
                            show_paths: true,
                        };
                        self.gui.apply(ctx);
                        self.gui_save_at = Some(Instant::now());
                        self.flush_gui();
                        // Exercise saving backend selection without losing GUI or unknown settings.
                        if let Err(e) = crate::preferences::merge(
                            &self.o.home.join("data/settings.json"),
                            "backend",
                            serde_json::json!("auto"),
                        ) {
                            self.error = Some(e);
                        }
                    }
                    self.smoke_stage = 3;
                    self.screenshot_at = Some(Instant::now() + Duration::from_secs(2));
                }
                6 if report_check => {
                    self.diagnostic_evidence["displayed_index_report"] = serde_json::to_value(&self.index_details).unwrap_or_default();
                    self.diagnostic_evidence["displayed_cleanup"] = serde_json::to_value(&self.last_cleanup).unwrap_or_default();
                    self.diagnostic_evidence["saved_cleanup"] = read_json(&self.o.data.join("last-cleanup.json")).unwrap_or_default();
                    self.smoke_stage = 4;
                    self.tab = 1;
                    self.screenshot_at = Some(Instant::now() + Duration::from_secs(1));
                }
                _ => {}
            }
        }
        if self.screenshot_at.is_some_and(|t| Instant::now() > t) {
            self.screenshot_at = None;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
        }
        for e in ctx.input(|i| i.events.clone()) {
            if let egui::Event::Screenshot { image, .. } = e {
                let raw = image
                    .pixels
                    .iter()
                    .flat_map(|p| p.to_array())
                    .collect::<Vec<_>>();
                let filename = match self.smoke_stage {
                    4 => "native-library.png",
                    5 => "native-settings.png",
                    _ => "native-ui.png",
                };
                let path = self.o.home.join("verification").join(filename);
                if let Some(p) = path.parent() {
                    let _ = std::fs::create_dir_all(p);
                }
                let saved = image::save_buffer(
                    &path,
                    &raw,
                    image.size[0] as u32,
                    image.size[1] as u32,
                    image::ColorType::Rgba8,
                )
                .map_err(err);
                let _ = save_json(
                    &self.o.report,
                    &serde_json::json!({"native_ui":saved,"screenshot":path,"backend":self.backend,"hits":self.hits,"browser_required":false,"library":self.roots,"gui":self.gui,"gui_loaded_on_start":self.loaded_gui,"gui_saved_matches":GuiSettings::load(&self.o.home.join("data/settings.json"))==self.gui,"settings_file":read_json::<serde_json::Value>(&self.o.home.join("data/settings.json")).ok(),"library_reports":self.diagnostic_evidence}),
                );
                match self.smoke_stage {
                    3 => {
                        self.smoke_stage = 4;
                        self.tab = 1;
                        self.screenshot_at = Some(Instant::now() + Duration::from_secs(1));
                    }
                    4 => {
                        self.smoke_stage = 5;
                        self.tab = 2;
                        self.screenshot_at = Some(Instant::now() + Duration::from_secs(1));
                    }
                    _ => self.stop(),
                }
            }
        }
    }
    fn feature_gui_check(&mut self, ctx: &egui::Context) {
        // Exercise a collection kept beside the application as well as the
        // normal external-collection setup. Never index Refseek's own data.
        let fixture_source = if std::env::args().any(|s| s == "--inside-app-source") {
            self.o.home.join("Pictures")
        } else {
            self.o.home.parent().unwrap().join(format!("{}-source",
                self.o.home.file_name().unwrap_or_default().to_string_lossy()))
        };
        let other_root = fixture_source.with_extension("other");
        let branch = fixture_source.join("skip");
        let nested_root = branch.join("keep");
        if self.error.is_some() || Instant::now() > self.smoke_deadline {
            let _ = save_json(&self.o.report,&serde_json::json!({"error":self.error,"stage":self.smoke_stage}));
            self.stop(); return;
        }
        if self.ready && !self.busy && !self.searching {
            match self.smoke_stage {
                0 => {
                    self.diagnostic_evidence["new_default_dimension"] = serde_json::json!(self.settings_dimension);
                    self.smoke_stage = 1; self.send(Command::Dimension(256));
                },
                1 => {
                    self.diagnostic_evidence["empty_library_dimension_change"] = serde_json::json!(self.settings_dimension == 256);
                    self.smoke_stage = 2; self.send(Command::Dimension(512));
                },
                2 => {
                    let root = fixture_source.clone();
                    let result = (|| -> Res<()> {
                        for (name, image) in [("one.jpg","b01-001.jpg"),("skip/two.jpg","b01-014.jpg"),
                            ("skip/keep/three.jpg","b01-021.jpg")] {
                            let path = root.join(name);
                            std::fs::create_dir_all(path.parent().unwrap()).map_err(err)?;
                            std::fs::copy(self.assets.as_ref().unwrap().join("validation").join(image),path).map_err(err)?;
                        }
                        std::fs::create_dir_all(&other_root).map_err(err)?;
                        std::fs::copy(self.assets.as_ref().unwrap().join("validation/b01-001.jpg"), other_root.join("four.jpg")).map_err(err)?;
                        Ok(())
                    })();
                    if let Err(e) = result {self.error = Some(e); return;}
                    self.smoke_stage = 3;
                    self.send(Command::Index(vec![root, nested_root.clone(), other_root.clone()],["image".into()].into_iter().collect()));
                },
                3 => {
                    let root = fixture_source.clone();
                    self.scope = [key(&root),key(&root.join("skip/keep"))].into_iter().collect();
                    self.excluded_scope = [key(&root.join("skip"))].into_iter().collect();
                    self.q.text = "animal".into(); self.do_search(); self.smoke_stage = 4;
                },
                4 => {
                    if self.hits.len() != 2 || self.hits.iter().any(|h| h.path.ends_with("two.jpg")) {
                        self.error = Some("GUI subfolder query did not preserve the selected rules".into()); return;
                    }
                    let visible = |expanded: &HashSet<String>| self.folder_tree.visible_rows(expanded).into_iter()
                        .map(|(i, depth)| (self.folder_tree.keys[i].clone(), depth)).collect::<Vec<_>>();
                    let collapsed = visible(&self.expanded_folders);
                    let opened = visible(&[key(&fixture_source)].into_iter().collect());
                    let fully_opened = visible(&[key(&fixture_source), key(&branch)].into_iter().collect());
                    if collapsed.len() != 2 || !collapsed.contains(&(key(&fixture_source), 0)) || !collapsed.contains(&(key(&other_root), 0))
                        || opened.len() != 3 || !opened.contains(&(key(&branch), 1))
                        || fully_opened.len() != 4 || !fully_opened.contains(&(key(&nested_root), 2)) {
                        self.error = Some("Folder tree did not group the nested index beneath its parent".into()); return;
                    }
                    self.diagnostic_evidence["folder_tree"] = serde_json::json!({"collapsed":collapsed,"root_opened":opened,"branch_opened":fully_opened});
                    self.diagnostic_evidence["gui_scope_query"] = serde_json::json!({"included":self.q.roots,
                        "excluded":self.q.excluded_roots,"hits":self.hits,"folders":self.folder_tree.folders});
                    self.diagnostic_evidence["folder_storage"] = serde_json::json!(self.roots.iter()
                        .map(|r| serde_json::json!({"snapshot": r.snapshot, "index_bytes": r.index_bytes, "source_bytes": r.source_bytes}))
                        .collect::<Vec<_>>());
                    self.diagnostic_evidence["vector_storage_per_10000"] = serde_json::json!(INDEX_DIMENSIONS.map(|d| (d, vector_storage(10_000, d))));
                    self.smoke_stage = 5; self.tab = 0;
                    self.screenshot_at = Some(Instant::now()+Duration::from_secs(1));
                },
                7 => {
                    if !self.hits.is_empty() {self.error = Some("A missing selected subfolder broadened the GUI search".into());return;}
                    self.diagnostic_evidence["missing_subfolder_does_not_broaden"] = serde_json::json!(true);
                    self.search_picture_folder(branch.clone());
                    self.smoke_stage = 9;
                },
                9 => {
                    if self.hits.len() != 1 || !self.hits[0].path.ends_with("two.jpg")
                        || !self.has_folder_filters() || !self.scope.is_empty() || !self.excluded_scope.is_empty() {
                        self.error = Some("Folder-only search did not restrict results to the picture's immediate folder".into());return;
                    }
                    self.diagnostic_evidence["folder_only_hits"] = serde_json::json!(self.hits);
                    self.smoke_stage = 10;
                    self.screenshot_at = Some(Instant::now() + Duration::from_secs(1));
                },
                11 => {
                    // The second root contains a copy of one.jpg; search deduplicates by content.
                    if self.hits.len() != 3 || !self.hits.iter().any(|h| h.path.ends_with("three.jpg")) || self.has_folder_filters() {
                        self.error = Some("Search all folders did not restore the full library".into());return;
                    }
                    self.diagnostic_evidence["all_folders_hit_count"] = serde_json::json!(self.hits.len());
                    let _ = save_json(&self.o.report,&self.diagnostic_evidence);
                    self.stop(); return;
                },
                _ => {},
            }
        }
        if self.screenshot_at.is_some_and(|t| Instant::now()>t) {
            self.screenshot_at = None;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
        }
        for event in ctx.input(|i| i.events.clone()) {
            if let egui::Event::Screenshot {image,..} = event {
                let filename = match self.smoke_stage {
                    5 => "folder-scope-collapsed.png",
                    8 => "folder-scope.png",
                    10 => "folder-only-search.png",
                    _ => "index-settings.png",
                };
                let raw = image.pixels.iter().flat_map(|p|p.to_array()).collect::<Vec<_>>();
                if let Err(e) = image::save_buffer(self.o.home.join("verification").join(filename),&raw,
                    image.size[0] as u32,image.size[1] as u32,image::ColorType::Rgba8) {
                    self.error = Some(e.to_string()); return;
                }
                if self.smoke_stage == 5 {
                    self.expanded_folders = [key(&fixture_source), key(&branch)].into_iter().collect();
                    self.smoke_stage = 8;
                    self.screenshot_at = Some(Instant::now()+Duration::from_secs(1));
                } else if self.smoke_stage == 8 {
                    self.smoke_stage = 6; self.tab = 2;
                    self.screenshot_at = Some(Instant::now()+Duration::from_secs(1));
                } else if self.smoke_stage == 10 {
                    self.search_all_folders();
                    self.smoke_stage = 11;
                } else {
                    self.diagnostic_evidence["dimension"] = serde_json::json!(self.settings_dimension);
                    self.diagnostic_evidence["dimension_locked_after_indexing"] = serde_json::json!(self.dimension_locked);
                    self.diagnostic_evidence["layout"] = serde_json::json!(self.layout_checks);
                    self.diagnostic_evidence["backend"] = serde_json::json!(self.backend);
                    self.diagnostic_evidence["settings"] = read_json(&self.o.home.join("data/settings.json")).unwrap_or_default();
                    self.scope = [key(&fixture_source.join("not-indexed"))].into_iter().collect();
                    self.excluded_scope.clear();
                    self.tab = 0;
                    self.do_search(); self.smoke_stage = 7;
                }
            }
        }
    }
    fn interaction_smoke(&mut self, ctx: &egui::Context) {
        let expected_save_error = matches!(self.smoke_stage, 17 | 18)
            && std::env::args().any(|a|a=="--index-error-check");
        if (self.error.is_some() && !expected_save_error) || Instant::now() > self.smoke_deadline {
            let _ = save_json(
                &self.o.report,
                &serde_json::json!({"error":self.error,"stage":self.smoke_stage,"evidence":self.diagnostic_evidence}),
            );
            self.stop();
            return;
        }
        match self.smoke_stage {
            0 if self.ready && !self.busy => {
                self.diagnostic_evidence["index_kinds_on_start"]=serde_json::json!(crate::preferences::INDEX_KINDS.into_iter().filter(|k|self.kinds.contains(*k)).collect::<Vec<_>>());
                self.diagnostic_evidence["preparation_workers_on_start"]=serde_json::json!(self.prepare_workers);
                self.smoke_stage = 1;
                self.send(Command::Index(
                    vec![self.assets.as_ref().unwrap().join("validation")],
                    ["image"].into_iter().map(String::from).collect(),
                ));
            }
            1 if !self.busy => {
                let dir = self.o.home.join("verification/concurrent-source");
                if let Err(e) = std::fs::create_dir_all(&dir) {
                    self.error = Some(e.to_string());
                    return;
                }
                for n in 0..40u32 {
                    let image =
                        image::RgbImage::from_fn(64 + n % 3 * 24, 96 - n % 3 * 16, |x, y| {
                            image::Rgb([
                                ((x * 3 + n * 13) % 256) as u8,
                                ((y * 5 + n * 7) % 256) as u8,
                                ((x + y + n * 31) % 256) as u8,
                            ])
                        });
                    if let Err(e) = image.save(dir.join(format!("generated-{n:02}.png"))) {
                        self.error = Some(e.to_string());
                        return;
                    }
                }
                self.send(Command::Index(
                    vec![dir],
                    ["image"].into_iter().map(String::from).collect(),
                ));
                self.q.image = Some(self.assets.as_ref().unwrap().join("validation/b01-001.jpg"));
                self.q.ignore_image =
                    Some(self.assets.as_ref().unwrap().join("validation/b01-014.jpg"));
                if std::env::args().any(|s| s == "--image-input-check") {
                    match crate::diagnostics::image_inputs(&self.o.home, self.assets.as_ref().unwrap()) {
                        Ok(files) => {
                            self.q.image = Some(files[0].clone());
                            self.q.ignore_image = Some(files[1].clone());
                            self.diagnostic_drop = Some(files[0].clone());
                        }
                        Err(e) => { self.error = Some(e); return; }
                    }
                }
                self.q.aspect = "style".into();
                // Force a fresh text inference request alongside the indexer's
                // image requests, rather than only exercising cached scoring.
                self.q.text = "a painted scene".into();
                self.wants = vec!["an animal outdoors".into(), "natural colors".into()];
                self.smoke_stage = 2;
            }
            2 if self.busy
                && self
                    .index_progress
                    .as_ref()
                    .is_some_and(|p| p.processed > 0) =>
            {
                self.do_search();
                self.smoke_stage = 3;
            }
            3 if !self.searching => {
                self.diagnostic_evidence["search_completed_while_indexing"] =
                    serde_json::json!(self.busy && !self.hits.is_empty());
                self.diagnostic_evidence["hits_during_indexing"] =
                    serde_json::json!(self.hits.len());
                self.diagnostic_evidence["live_speed"] =
                    serde_json::to_value(&self.index_progress).unwrap_or_default();
                self.cancel.store(true, Ordering::Relaxed);
                self.smoke_stage = 4;
            }
            4 if !self.busy => {
                self.gui.text_size = 22.;
                self.gui.apply(ctx);
                self.preview = self
                    .hits
                    .iter()
                    .find(|h| h.kind == "image")
                    .map(|h| h.path.clone());
                self.smoke_stage = 10;
                self.screenshot_at = Some(Instant::now() + Duration::from_secs(2));
            }
            17 if !self.busy && self.error.is_some() => {
                self.diagnostic_evidence["save_error_progress"] = serde_json::to_value(&self.index_progress).unwrap_or_default();
                let details = self.load_index_details();
                self.diagnostic_evidence["save_error_details"] = serde_json::to_value(&details).unwrap_or_default();
                self.diagnostic_evidence["save_error_terminal"] = serde_json::json!(
                    self.index_progress.as_ref().is_some_and(|p|p.phase=="Failed")
                    && details.as_ref().is_ok_and(|s|s.error.is_some() && s.processed>0));
                self.index_details = Some(details);
                self.diagnostic_catalog_lock = None;
                self.tab = 1;
                self.smoke_stage = 18;
                self.screenshot_at = Some(Instant::now() + Duration::from_secs(1));
            }
            _ => {}
        }
        if self.screenshot_at.is_some_and(|at| Instant::now() > at) && !self.searching {
            self.screenshot_at = None;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
        }
        for event in ctx.input(|i| i.events.clone()) {
            if let egui::Event::Screenshot { image, .. } = event {
                let name = format!("interaction-{}.png", self.smoke_stage);
                let path = self.o.home.join("verification").join(name);
                let rgba = image
                    .pixels
                    .iter()
                    .flat_map(|p| p.to_array())
                    .collect::<Vec<_>>();
                let _ = image::save_buffer(
                    path,
                    &rgba,
                    image.size[0] as u32,
                    image.size[1] as u32,
                    image::ColorType::Rgba8,
                );
                let rect =
                    |r: Option<egui::Rect>| r.map(|r| [r.min.x, r.min.y, r.width(), r.height()]);
                if self.smoke_stage == 18 {
                    let _ = save_json(&self.o.report, &self.diagnostic_evidence);
                    self.stop();
                    return;
                } else if self.smoke_stage == 10 {
                    self.diagnostic_evidence["viewer_before"] =
                        serde_json::json!(rect(self.last_preview_rect));
                    self.diagnostic_evidence["host_before"] =
                        serde_json::json!(rect(ctx.input(|i| i.viewport().outer_rect)));
                    self.diagnostic_evidence["image_before"] = serde_json::json!(self.preview);
                    self.diagnostic_key = Some(egui::Key::ArrowRight);
                    self.smoke_stage = 11;
                } else if self.smoke_stage == 11 {
                    self.diagnostic_evidence["viewer_after"] =
                        serde_json::json!(rect(self.last_preview_rect));
                    self.diagnostic_evidence["host_after"] =
                        serde_json::json!(rect(ctx.input(|i| i.viewport().outer_rect)));
                    self.diagnostic_evidence["image_after"] = serde_json::json!(self.preview);
                    self.diagnostic_evidence["viewer_stable"] = serde_json::json!(
                        self.diagnostic_evidence["viewer_before"]
                            == self.diagnostic_evidence["viewer_after"]
                    );
                    self.diagnostic_evidence["host_stable"] = serde_json::json!(
                        self.diagnostic_evidence["host_before"]
                            == self.diagnostic_evidence["host_after"]
                    );
                    self.diagnostic_evidence["arrow_changed_image"] = serde_json::json!(
                        self.diagnostic_evidence["image_before"]
                            != self.diagnostic_evidence["image_after"]
                    );
                    self.preview = None;
                    self.tab = 0;
                    self.smoke_stage = 12;
                } else if self.smoke_stage == 12 {
                    self.tab = 1;
                    self.smoke_stage = 13;
                } else if self.smoke_stage == 13 {
                    self.tab = 2;
                    self.smoke_stage = 14;
                } else if self.smoke_stage == 14 {
                    self.tab = 0;
                    self.q.image = None;
                    self.q.combine_max = false;
                    self.do_search();
                    self.smoke_stage = 15;
                } else if self.smoke_stage == 15 {
                    self.diagnostic_evidence["match_all"] = serde_json::json!(self.hits);
                    self.q.combine_max = true;
                    self.do_search();
                    self.smoke_stage = 16;
                } else {
                    self.diagnostic_evidence["match_any"] = serde_json::json!(self.hits);
                    self.diagnostic_evidence["layout"] =
                        serde_json::to_value(&self.layout_checks).unwrap_or_default();
                    self.diagnostic_evidence["negative_image_set"] =
                        serde_json::json!(self.q.ignore_image.is_some());
                    self.diagnostic_evidence["final_index_progress"] =
                        serde_json::to_value(&self.index_progress).unwrap_or_default();
                    if std::env::args().any(|a|a=="--index-error-check") {
                        let setup = (|| -> Res<PathBuf> {
                            use std::os::windows::fs::OpenOptionsExt;
                            self.diagnostic_catalog_lock = Some(std::fs::OpenOptions::new().read(true).share_mode(3)
                                .open(self.o.data.join("catalog.json")).map_err(err)?);
                            let source = self.o.home.join("verification/concurrent-source");
                            std::fs::copy(source.join("generated-00.png"), source.join("000-save-failure.png")).map_err(err)?;
                            Ok(source)
                        })();
                        match setup {
                            Ok(source) => {
                                self.tab = 1;
                                self.index_details = Some(self.load_index_details());
                                self.send(Command::Index(vec![source], ["image".into()].into_iter().collect()));
                                self.smoke_stage = 17;
                            },
                            Err(e) => {self.diagnostic_evidence["save_error_setup_error"]=serde_json::json!(e);
                                let _=save_json(&self.o.report,&self.diagnostic_evidence); self.stop();},
                        }
                        return;
                    }
                    let _ = save_json(&self.o.report, &self.diagnostic_evidence);
                    self.stop();
                    return;
                }
                self.screenshot_at = Some(Instant::now() + Duration::from_secs(1));
            }
        }
    }
}
impl eframe::App for App {
    fn raw_input_hook(&mut self, _: &egui::Context, input: &mut egui::RawInput) {
        if let Some(path) = self.diagnostic_drop.take() {
            input.dropped_files.push(egui::DroppedFile { path: Some(path), ..Default::default() });
        }
        // Diagnostic uses the same egui keyboard path as real input, without OS automation.
        if let Some(key) = self.diagnostic_key.take() {
            for pressed in [true, false] {
                input.events.push(egui::Event::Key {
                    key,
                    physical_key: Some(key),
                    pressed,
                    repeat: false,
                    modifiers: egui::Modifiers::NONE,
                });
            }
        }
    }
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        if let Some(opening) = self.opening.take() {
            if self.o.gui_smoke {
                let evidence = serde_json::json!({"native_indicator_shown":opening.shown,"indicator_error":opening.error,"first_frame_seconds":opening.since.elapsed().as_secs_f64()});
                self.diagnostic_evidence["opening"] = evidence.clone();
                let _ = save_json(&self.o.home.join("verification/opening.json"), &evidence);
            }
            drop(opening);
        }
        if self.preview.is_some() {
            let step = ctx.input_mut(|i| {
                let next = i.consume_key(egui::Modifiers::NONE, egui::Key::ArrowRight)
                    | i.consume_key(egui::Modifiers::NONE, egui::Key::ArrowDown);
                let prev = i.consume_key(egui::Modifiers::NONE, egui::Key::ArrowLeft)
                    | i.consume_key(egui::Modifiers::NONE, egui::Key::ArrowUp);
                if i.consume_key(egui::Modifiers::NONE, egui::Key::Escape) {
                    self.preview = None;
                }
                if next {
                    1
                } else if prev {
                    -1
                } else {
                    0
                }
            });
            if step != 0 {
                self.move_preview(step);
            }
            if self.preview.is_some() {
                ui.disable();
            }
        }
        let before_gui = self.gui.clone();
        ctx.input_mut(|i| {
            if i.consume_key(egui::Modifiers::CTRL, egui::Key::Plus)
                || i.consume_key(egui::Modifiers::CTRL, egui::Key::Equals)
            {
                self.gui.scale += 0.05;
            }
            if i.consume_key(egui::Modifiers::CTRL, egui::Key::Minus) {
                self.gui.scale -= 0.05;
            }
            if i.consume_key(egui::Modifiers::CTRL, egui::Key::Num0) {
                self.gui.scale = 1.;
            }
        });
        self.poll(&ctx);
        if ctx.input(|i| i.viewport().close_requested()) {
            self.stop();
            if !self.stopped {
                ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            }
        }
        if self.stopped && self.closing.load(Ordering::Relaxed) {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
        let dropped = ctx.input(|i| i.raw.dropped_files.clone());
        for d in dropped {
            if let Some(p) = d.path {
                if p.is_dir() {
                    self.index_path = display_path(&p);
                    self.tab = 1;
                } else if crate::extract::kind(&p) == Some("image") {
                    if self.o.gui_smoke {
                        self.diagnostic_evidence["dropped_image"] = serde_json::json!(display_path(&p));
                    }
                    self.q.image = Some(p);
                    self.tab = 0;
                }
            }
        }
        if ctx.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::Enter))
            && self.ready
            && !self.searching
            && self.preview.is_none()
        {
            self.do_search();
        }
        egui::Panel::bottom("status").show_inside(ui, |ui| {
            ui.horizontal(|ui| {
                if self.busy {
                    ui.spinner();
                } else {
                    let color = if self.ready { Color32::from_rgb(110, 205, 166) }
                        else { Color32::from_rgb(232, 165, 109) };
                    let (rect, _) = ui.allocate_exact_size(Vec2::splat(ui.text_style_height(&egui::TextStyle::Body)), egui::Sense::hover());
                    ui.painter().circle_filled(rect.center(), 3.5, color);
                }
                ui.add(egui::Label::new(&self.status).truncate())
                    .on_hover_text(&self.status);
            });
            if self.busy && self.ready {
                if let Some(p) = &self.index_progress {
                    ui.add(
                        egui::Label::new(format!(
                            "Index: {} · {}/{} · {:.2} files/s · {:.2} new files/s",
                            p.phase,
                            p.processed,
                            p.total,
                            p.files_per_second,
                            p.new_files_per_second
                        ))
                        .truncate(),
                    );
                }
            }
        });
        egui::Panel::left("navigation")
            .exact_size(188.)
            .resizable(false)
            .show_inside(ui, |ui| {
                ui.add_space(18.);
                ui.heading("Refseek");
                ui.label(
                    RichText::new(concat!("DESKTOP · ", env!("CARGO_PKG_VERSION")))
                        .small()
                        .color(ui.visuals().hyperlink_color),
                );
                ui.add_space(30.);
                for (i, l) in ["Search", "Library", "Settings"].iter().enumerate() {
                    ui.add_space(4.);
                    ui.selectable_value(&mut self.tab, i, *l);
                }
                ui.add_space(30.);
                ui.separator();
                ui.small(format!("{} folders", self.roots.len()));
                ui.small(format!(
                    "{} files",
                    self.roots.iter().map(|r| r.files).sum::<usize>()
                ));
                ui.small(format!(
                    "{} vectors",
                    self.roots.iter().map(|r| r.vectors).sum::<usize>()
                ));
                ui.add_space(16.);
                ui.small(&self.backend);
                ui.add_space(20.);
                ui.label(
                    RichText::new("Local. Full images.\nNo browser.")
                        .small()
                        .weak(),
                );
            });
        egui::CentralPanel::default().show_inside(ui, |ui| {
            egui::Frame::new().inner_margin(18.).show(ui, |ui| {
                if self.closing.load(Ordering::Relaxed) {
                    ui.heading("Closing safely…");
                    ui.label(&self.status);
                    ui.spinner();
                    return;
                }
                if let Some(e) = self.error.clone() {
                    egui::Frame::group(ui.style())
                        .fill(if ui.visuals().dark_mode {
                            Color32::from_rgb(69, 35, 38)
                        } else {
                            Color32::from_rgb(255, 230, 230)
                        })
                        .show(ui, |ui| {
                            ui.label(&e);
                            if ui.small_button("Dismiss").clicked() {
                                self.error = None;
                            }
                        });
                }
                if !self.ready && !self.stopped && !self.closing.load(Ordering::Relaxed) {
                    egui::ScrollArea::vertical().id_salt("opening-page").show(ui, |ui| {
                        ui.add_space(20.);
                        ui.heading("Opening Refseek…");
                        ui.add_space(12.);
                        ui.spinner();
                        ui.label(&self.status);
                        ui.small(format!("Elapsed {:.0}s", self.opened_at.elapsed().as_secs_f64()));
                        ui.label("The window is open. Your library and local model are being prepared.");
                    });
                    return;
                }
                match self.tab {
                    0 => {
                        egui::ScrollArea::vertical()
                            .id_salt("search-page")
                            .show(ui, |ui| {let max=ui.max_rect().max.x;self.search_ui(ui);self.layout_checks.insert("search".into(),serde_json::json!({"allowed_right":max,"content_right":ui.min_rect().max.x,"horizontal_overflow":ui.min_rect().max.x>max+1.}));});
                    }
                    1 => {
                        egui::ScrollArea::vertical()
                            .id_salt("library-page")
                            .show(ui, |ui| {let max=ui.max_rect().max.x;self.library_ui(ui);self.layout_checks.insert("library".into(),serde_json::json!({"allowed_right":max,"content_right":ui.min_rect().max.x,"horizontal_overflow":ui.min_rect().max.x>max+1.}));});
                    }
                    _ => {
                        egui::ScrollArea::vertical()
                            .id_salt("settings-scroll")
                            .show(ui, |ui| {let max=ui.max_rect().max.x;self.settings_ui(ui);self.layout_checks.insert("settings".into(),serde_json::json!({"allowed_right":max,"content_right":ui.min_rect().max.x,"horizontal_overflow":ui.min_rect().max.x>max+1.}));});
                    }
                }
            });
        });
        self.preview_ui(&ctx);
        if self.gui != before_gui {
            self.gui.sanitize();
            if self.gui.results_per_page != before_gui.results_per_page {
                self.page = 0;
            }
            self.gui.apply(&ctx);
            self.gui_save_at = Some(Instant::now() + Duration::from_millis(400));
        }
        if self.gui_save_at.is_some_and(|at| Instant::now() >= at) {
            self.flush_gui();
        }
        self.smoke(&ctx);
        ctx.request_repaint_after(Duration::from_millis(
            if self.busy || self.searching || self.o.gui_smoke {
                100
            } else {
                300
            },
        ));
    }
    fn on_exit(&mut self, _: Option<&eframe::glow::Context>) {
        self.flush_gui();
        self.stop();
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}
pub fn run(o: Options, opening: Option<crate::opening::Opening>) -> Res<()> {
    // Direct3D's software adapter keeps the window usable without a physical GPU.
    // This is separate from the inference backend selected in Settings.
    let software = std::env::args().any(|s| s == "--software-ui");
    let mut setup = eframe::egui_wgpu::WgpuSetupCreateNew::without_display_handle();
    setup.instance_descriptor.backends = eframe::wgpu::Backends::DX12;
    setup
        .instance_descriptor
        .backend_options
        .dx12
        .shader_compiler = eframe::wgpu::Dx12Compiler::Fxc;
    if software {
        setup.native_adapter_selector = Some(Arc::new(|adapters, surface| {
            adapters
                .iter()
                .find(|a| {
                    a.get_info().device_type == eframe::wgpu::DeviceType::Cpu
                        && surface.is_none_or(|s| a.is_surface_supported(s))
                })
                .cloned()
                .ok_or("No Direct3D software adapter is available".into())
        }));
    }
    let opts = eframe::NativeOptions {
        renderer: if std::env::args().any(|s| s == "--ui-gl") {
            eframe::Renderer::Glow
        } else {
            eframe::Renderer::Wgpu
        },
        wgpu_options: eframe::egui_wgpu::WgpuConfiguration {
            wgpu_setup: eframe::egui_wgpu::WgpuSetup::CreateNew(setup),
            ..Default::default()
        },
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1280., 900.])
            .with_min_inner_size([900., 650.]),
        ..Default::default()
    };
    eframe::run_native(
        "Refseek Desktop",
        opts,
        Box::new(move |cc| Ok(Box::new(App::new(cc, o, opening)))),
    )
    .map_err(err)
}
