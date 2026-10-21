use crate::{extract, rpc::Api, util::*};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};
const FINGERPRINT: &str = "native-v1:embeddinggemma-2-q8:768:mean:fullframe1024-lanczos3-jpeg85";
#[derive(Clone, Serialize, Deserialize)]
pub struct Record {
    pub path: PathBuf,
    pub hash: String,
    pub size: u64,
    pub modified: u128,
    pub kind: String,
    pub snippet: String,
    pub chunk: usize,
    #[serde(skip)]
    pub vector: Vec<f64>,
}
#[derive(Clone, Default, Serialize, Deserialize)]
pub struct RootInfo {
    pub path: PathBuf,
    pub snapshot: String,
    pub files: usize,
    pub vectors: usize,
    pub updated: u128,
    pub index_sha: String,
    pub vectors_sha: String,
}
#[derive(Clone, Serialize, Deserialize)]
struct Catalog {
    version: u32,
    fingerprint: String,
    generation: u128,
    roots: Vec<RootInfo>,
}
#[derive(Serialize, Deserialize)]
struct Index {
    version: u32,
    fingerprint: String,
    records: Vec<Record>,
}
#[derive(Clone)]
pub struct Root {
    pub info: RootInfo,
    pub records: Vec<Record>,
}
pub struct Library {
    pub dir: PathBuf,
    pub roots: Vec<Root>,
    pub generation: u128,
}
#[derive(Default, Serialize, Clone)]
pub struct Stats {
    pub discovered: usize,
    pub embedded: usize,
    pub reused: usize,
    pub failed: usize,
    pub removed: usize,
    pub cancelled: bool,
    pub warnings: Vec<String>,
    pub elapsed_seconds: f64,
    pub processed: usize,
}
#[derive(Clone, Default, Serialize)]
pub struct IndexProgress {
    pub phase: String,
    pub processed: usize,
    pub total: usize,
    pub embedded: usize,
    pub reused: usize,
    pub failed: usize,
    pub elapsed_seconds: f64,
    pub files_per_second: f64,
    pub new_files_per_second: f64,
    pub eta_seconds: Option<f64>,
}
impl IndexProgress {
    fn current(phase: &str, stats: &Stats, started: Instant, processing: Instant) -> Self {
        let seconds = processing.elapsed().as_secs_f64().max(0.001);
        let rate = stats.processed as f64 / seconds;
        Self {
            phase: phase.into(),
            processed: stats.processed,
            total: stats.discovered,
            embedded: stats.embedded,
            reused: stats.reused,
            failed: stats.failed,
            elapsed_seconds: started.elapsed().as_secs_f64(),
            files_per_second: rate,
            new_files_per_second: stats.embedded as f64 / seconds,
            eta_seconds: if rate > 0. && phase == "Indexing" {
                Some(stats.discovered.saturating_sub(stats.processed) as f64 / rate)
            } else {
                None
            },
        }
    }
}
impl Library {
    pub fn open(dir: &Path) -> Res<Self> {
        fs::create_dir_all(dir).map_err(err)?;
        if dir.join("index.json").exists() || dir.join("vectors.f32").exists() {
            return Err("Choose a new native data folder; legacy libraries are read-only.".into());
        }
        let p = dir.join("catalog.json");
        let mut lib = Self {
            dir: dir.to_owned(),
            roots: vec![],
            generation: 0,
        };
        if !p.exists() {
            return Ok(lib);
        }
        let c: Catalog = read_json(&p)?;
        if c.version != 1 || c.fingerprint != FINGERPRINT {
            return Err("Library belongs to a different embedding/preparation format".into());
        }
        for info in c.roots {
            if Path::new(&info.snapshot)
                .components()
                .any(|c| !matches!(c, std::path::Component::Normal(_)))
            {
                return Err("Unsafe snapshot path".into());
            }
            let base = dir.join(&info.snapshot);
            if !under(&base, dir) {
                return Err("Unsafe snapshot path".into());
            }
            let ib = fs::read(base.join("index.json")).map_err(err)?;
            let vb = fs::read(base.join("vectors.f32")).map_err(err)?;
            if hash(&ib) != info.index_sha || hash(&vb) != info.vectors_sha {
                return Err(format!(
                    "Snapshot hash failed: {}. Previous catalog and snapshots are preserved.",
                    base.display()
                ));
            }
            let mut idx: Index = serde_json::from_slice(&ib).map_err(err)?;
            if idx.version != 1
                || idx.fingerprint != FINGERPRINT
                || vb.len() != idx.records.len() * 768 * 4
            {
                return Err("Invalid vector snapshot".into());
            }
            for (r, row) in idx.records.iter_mut().zip(vb.chunks_exact(768 * 4)) {
                r.vector = row
                    .chunks_exact(4)
                    .map(|x| f32::from_le_bytes(x.try_into().unwrap()) as f64)
                    .collect();
                if !valid(&r.vector) {
                    return Err("Invalid library vector".into());
                }
            }
            lib.roots.push(Root {
                info,
                records: idx.records,
            });
        }
        lib.generation = c.generation;
        Ok(lib)
    }
    pub fn infos(&self) -> Vec<RootInfo> {
        self.roots.iter().map(|r| r.info.clone()).collect()
    }
    pub fn all(&self) -> Vec<Record> {
        self.roots
            .iter()
            .flat_map(|r| r.records.iter().cloned())
            .collect()
    }
    pub fn commit(&mut self) -> Res<()> {
        let mut distributed = vec![vec![]; self.roots.len()];
        let mut seen = HashSet::new();
        // Prefer the actual owning shard before transferring records inherited
        // from a newly split parent. Otherwise a parent's older copy can win.
        let mut candidates = Vec::new();
        for (origin, root) in self.roots.iter().enumerate() {
            for r in &root.records {
                let owner = self
                    .roots
                    .iter()
                    .enumerate()
                    .filter(|(_, root)| under(&r.path, &root.info.path))
                    .max_by_key(|(_, root)| key(&root.info.path).len())
                    .map(|(i, _)| i);
                if let Some(i) = owner {
                    candidates.push((origin != i, i, r.clone()));
                }
            }
        }
        candidates.sort_by_key(|(transferred, _, _)| *transferred);
        let mut ownership = HashMap::new();
        for (_, owner, r) in candidates {
            let pk = key(&r.path);
            // A transferred stale file must not add trailing old chunks after
            // its owner has successfully replaced it with a shorter document.
            let signature = (r.hash.clone(), r.modified);
            if ownership.get(&pk).is_some_and(|s| *s != signature) {
                continue;
            }
            ownership.entry(pk.clone()).or_insert(signature);
            if seen.insert((pk, r.chunk)) {
                distributed[owner].push(r);
            }
        }
        for (root, mut records) in self.roots.iter_mut().zip(distributed) {
            records.sort_by(|a, b| key(&a.path).cmp(&key(&b.path)).then(a.chunk.cmp(&b.chunk)));
            let index = Index {
                version: 1,
                fingerprint: FINGERPRINT.into(),
                records: records.clone(),
            };
            let ib = serde_json::to_vec(&index).map_err(err)?;
            let mut vb = Vec::with_capacity(records.len() * 768 * 4);
            for r in &records {
                if !valid(&r.vector) {
                    return Err("Refusing to commit invalid vectors".into());
                }
                for &x in &r.vector {
                    vb.extend_from_slice(&(x as f32).to_le_bytes());
                }
            }
            let ih = hash(&ib);
            let vh = hash(&vb);
            if root.info.index_sha != ih
                || root.info.vectors_sha != vh
                || root.info.snapshot.is_empty()
            {
                let rid = hash(key(&root.info.path).as_bytes());
                let snap = format!("folders/{}/{}", &rid[..20], stamp());
                let dest = self.dir.join(&snap);
                fs::create_dir_all(&dest).map_err(err)?;
                for (name, b) in [("index.json", &ib), ("vectors.f32", &vb)] {
                    let mut f = File::create(dest.join(name)).map_err(err)?;
                    f.write_all(b).map_err(err)?;
                    f.sync_all().map_err(err)?;
                }
                if file_hash(&dest.join("index.json"))? != ih
                    || file_hash(&dest.join("vectors.f32"))? != vh
                {
                    return Err("Snapshot verification failed; active catalog unchanged".into());
                }
                root.info.snapshot = snap;
                root.info.index_sha = ih;
                root.info.vectors_sha = vh;
                root.info.updated = stamp();
            }
            root.info.files = records
                .iter()
                .map(|r| key(&r.path))
                .collect::<HashSet<_>>()
                .len();
            root.info.vectors = records.len();
            for r in &mut records {
                r.vector = r.vector.iter().map(|v| (*v as f32) as f64).collect();
            }
            root.records = records;
        }
        let p = self.dir.join("catalog.json");
        let disk = if p.exists() {
            read_json::<Catalog>(&p)?.generation
        } else {
            0
        };
        if disk != self.generation {
            return Err("Library changed; reload before committing".into());
        }
        let generation = stamp();
        let c = Catalog {
            version: 1,
            fingerprint: FINGERPRINT.into(),
            generation,
            roots: self.infos(),
        };
        if p.exists() {
            atomic(
                &self.dir.join("catalog.previous.json"),
                &fs::read(&p).map_err(err)?,
            )?;
        }
        save_json(&p, &c)?;
        self.generation = generation;
        Ok(())
    }
    pub fn remove(&mut self, path: &Path) -> Res<()> {
        self.roots.retain(|r| key(&r.info.path) != key(path));
        self.commit()
    }
    pub fn index(
        &mut self,
        path: &Path,
        kinds: &HashSet<String>,
        api: &Api,
        cancel: &AtomicBool,
        progress: &dyn Fn(String),
        excluded: &Path,
    ) -> Res<Stats> {
        self.index_live(
            path,
            kinds,
            api,
            cancel,
            progress,
            excluded,
            &|_| {},
            &|_| {},
        )
    }
    pub fn index_live(
        &mut self,
        path: &Path,
        kinds: &HashSet<String>,
        api: &Api,
        cancel: &AtomicBool,
        progress: &dyn Fn(String),
        excluded: &Path,
        live: &dyn Fn(IndexProgress),
        committed: &dyn Fn(Vec<RootInfo>),
    ) -> Res<Stats> {
        let started = Instant::now();
        let path = canonical(path)?;
        if !path.is_dir() {
            return Err("Index root is not a folder".into());
        }
        let at = match self
            .roots
            .iter()
            .position(|r| key(&r.info.path) == key(&path))
        {
            Some(i) => i,
            None => {
                self.roots.push(Root {
                    info: RootInfo {
                        path: path.clone(),
                        ..Default::default()
                    },
                    records: vec![],
                });
                self.roots.len() - 1
            }
        };
        let mut old: BTreeMap<String, Vec<Record>> = BTreeMap::new();
        for r in self.all() {
            if under(&r.path, &path) {
                old.entry(key(&r.path)).or_default().push(r);
            }
        }
        let mut cache: HashMap<(String, String), Vec<Record>> = HashMap::new();
        let mut cached_files: BTreeMap<String, Vec<Record>> = BTreeMap::new();
        for r in self.all() {
            cached_files.entry(key(&r.path)).or_default().push(r);
        }
        for records in cached_files.values() {
            if let Some(r) = records.first() {
                cache.insert(
                    (r.hash.clone(), cache_kind(&r.path, &r.kind)),
                    records.clone(),
                );
            }
        }
        let mut stats = Stats::default();
        let mut files = vec![];
        let mut stack = vec![path.clone()];
        let mut complete = true;
        let mut scan_tick = Instant::now();
        live(IndexProgress {
            phase: "Scanning".into(),
            ..Default::default()
        });
        while let Some(dir) = stack.pop() {
            if scan_tick.elapsed().as_millis() > 250 {
                live(IndexProgress {
                    phase: "Scanning".into(),
                    total: files.len(),
                    elapsed_seconds: started.elapsed().as_secs_f64(),
                    ..Default::default()
                });
                scan_tick = Instant::now();
            }
            if cancel.load(Ordering::Relaxed) {
                complete = false;
                stats.cancelled = true;
                break;
            }
            let entries = match fs::read_dir(&dir) {
                Ok(v) => v,
                Err(e) => {
                    complete = false;
                    stats.warnings.push(format!("{}: {e}", dir.display()));
                    continue;
                }
            };
            for e in entries {
                let e = match e {
                    Ok(e) => e,
                    Err(e) => {
                        complete = false;
                        stats.warnings.push(e.to_string());
                        continue;
                    }
                };
                let p = e.path();
                let m = match fs::symlink_metadata(&p) {
                    Ok(m) => m,
                    Err(e) => {
                        complete = false;
                        stats.warnings.push(e.to_string());
                        continue;
                    }
                };
                use std::os::windows::fs::MetadataExt;
                if m.file_attributes() & 0x400 != 0 {
                    continue;
                }
                if m.is_dir() {
                    let name = e.file_name().to_string_lossy().to_lowercase();
                    if [
                        ".git",
                        ".cargo",
                        "target",
                        "node_modules",
                        ".venv",
                        "venv",
                        "$recycle.bin",
                        "system volume information",
                    ]
                    .contains(&name.as_str())
                        || under(&p, excluded)
                    {
                        continue;
                    }
                    stack.push(p);
                } else if m.is_file() {
                    if let Some(k) = extract::kind(&p) {
                        if kinds.contains(k) {
                            let owner = self
                                .roots
                                .iter()
                                .filter(|r| under(&p, &r.info.path))
                                .max_by_key(|r| key(&r.info.path).len())
                                .map(|r| key(&r.info.path));
                            if owner.as_deref() == Some(&key(&path)) {
                                files.push((p, k.to_string()));
                            }
                        }
                    }
                }
            }
        }
        files.sort_by(|a, b| key(&a.0).cmp(&key(&b.0)));
        stats.discovered = files.len();
        let mut seen = HashSet::new();
        let mut updated = old.clone();
        let mut checkpoint = Instant::now();
        let mut since = 0;
        let processing = Instant::now();
        let mut published = false;
        live(IndexProgress::current(
            "Indexing", &stats, started, processing,
        ));
        for (i, (p, k)) in files.iter().enumerate() {
            if cancel.load(Ordering::Relaxed) {
                stats.cancelled = true;
                complete = false;
                break;
            }
            let pk = key(p);
            seen.insert(pk.clone());
            progress(format!(
                "{}/{} · {}",
                i + 1,
                files.len(),
                p.file_name().unwrap_or_default().to_string_lossy()
            ));
            let result = (|| -> Res<Vec<Record>> {
                let m = fs::metadata(p).map_err(err)?;
                if m.len() == 0 {
                    return Err("Empty file".into());
                }
                if m.len() > 256 * 1024 * 1024 {
                    return Err("File exceeds 256 MiB".into());
                }
                let h = file_hash(p)?;
                let modified = m
                    .modified()
                    .map_err(err)?
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_err(err)?
                    .as_nanos();
                if let Some(existing) = cache.get(&(h.clone(), cache_kind(p, k))) {
                    stats.reused += 1;
                    return Ok(existing
                        .iter()
                        .map(|r| Record {
                            path: p.clone(),
                            modified,
                            size: m.len(),
                            ..r.clone()
                        })
                        .collect());
                }
                let parts = extract::embed_file(api, p, k, cancel)?;
                if file_hash(p)? != h {
                    return Err("File changed during embedding; old vectors retained".into());
                }
                let records = parts
                    .into_iter()
                    .enumerate()
                    .map(|(chunk, (snippet, vector))| Record {
                        path: p.clone(),
                        hash: h.clone(),
                        size: m.len(),
                        modified,
                        kind: k.clone(),
                        snippet,
                        chunk,
                        vector,
                    })
                    .collect::<Vec<_>>();
                cache.insert((h, cache_kind(p, k)), records.clone());
                stats.embedded += 1;
                Ok(records)
            })();
            match result {
                Ok(r) => {
                    updated.insert(pk, r);
                    since += 1
                }
                Err(e) => {
                    stats.failed += 1;
                    stats.warnings.push(format!("{}: {e}", p.display()));
                }
            }
            stats.processed = i + 1;
            live(IndexProgress::current(
                "Indexing", &stats, started, processing,
            ));
            if since > 0 && (!published || since >= 200 || checkpoint.elapsed().as_secs() >= 30) {
                self.roots[at].records = updated.values().flatten().cloned().collect();
                self.commit()?;
                committed(self.infos());
                published = true;
                since = 0;
                checkpoint = Instant::now();
            }
        }
        if complete {
            updated.retain(|k, rs| {
                let remove = !seen.contains(k)
                    && rs
                        .first()
                        .is_some_and(|r| kinds.contains(&r.kind) && !r.path.exists());
                if remove {
                    stats.removed += 1;
                }
                !remove
            });
        }
        if updated.is_empty() && !old.is_empty() {
            return Err("Empty-scan guard: previous library preserved".into());
        }
        self.roots[at].records = updated.into_values().flatten().collect();
        self.commit()?;
        committed(self.infos());
        stats.elapsed_seconds = started.elapsed().as_secs_f64();
        live(IndexProgress::current(
            if stats.cancelled {
                "Cancelled"
            } else {
                "Complete"
            },
            &stats,
            started,
            processing,
        ));
        if stats.warnings.len() > 200 {
            stats.warnings.truncate(200);
        }
        Ok(stats)
    }
}

fn cache_kind(p: &Path, k: &str) -> String {
    if k == "image" || k == "audio" || k == "video" {
        k.into()
    } else {
        format!(
            "{}:{}",
            k,
            p.file_name().unwrap_or_default().to_string_lossy()
        )
    }
}
