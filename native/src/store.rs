use crate::{extract, rpc::Api, util::*};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs::{self, File},
    io::{BufReader, Read, Write},
    os::windows::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    sync::{Arc, RwLock},
    time::Instant,
};
const FINGERPRINT: &str = "native-v1:embeddinggemma-2-q8:768:mean:fullframe1024-lanczos3-jpeg85";
// The search worker can read checkpoints while indexing. Do not retire files
// between reading a catalog and loading its immutable snapshots.
static SNAPSHOTS: RwLock<()> = RwLock::new(());
const SNAPSHOT_FILES: [&str; 3] = ["index.json", "vectors.f32", "native-vectors.f32"];
fn fingerprint(dimension: usize) -> String {
    if dimension == 768 { FINGERPRINT.into() }
    else { format!("{FINGERPRINT}:mrl-{dimension}:native-cache-v1") }
}
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
    pub vector: Arc<[f64]>,
    #[serde(skip)]
    pub search_vector: Arc<[f64]>,
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
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub native_vectors_sha: String,
    // Display-only totals derived from the active snapshot, including old indexes.
    #[serde(skip)]
    pub index_bytes: u64,
    #[serde(skip)]
    pub source_bytes: u64,
}

fn source_totals(records: &[Record]) -> (usize, u64) {
    let mut files = HashSet::new();
    let mut bytes = 0u64;
    for record in records {
        if files.insert(key(&record.path)) {
            bytes = bytes.saturating_add(record.size);
        }
    }
    (files.len(), bytes)
}
#[derive(Clone, Serialize, Deserialize)]
struct Catalog {
    version: u32,
    fingerprint: String,
    generation: u128,
    roots: Vec<RootInfo>,
    #[serde(default = "native_dimension")]
    dimension: usize,
}
#[derive(Serialize, Deserialize)]
struct Index {
    version: u32,
    fingerprint: String,
    records: Vec<Record>,
    #[serde(default = "native_dimension")]
    dimension: usize,
}
// Identical on-disk schema, without copying records to serialize a checkpoint.
#[derive(Serialize)]
struct IndexView<'a> {
    version: u32,
    fingerprint: String,
    records: &'a [Record],
    dimension: usize,
}

fn same_records(a: &[Record], b: &[Record]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(a, b)| a.path == b.path
        && a.hash == b.hash && a.size == b.size && a.modified == b.modified
        && a.kind == b.kind && a.snippet == b.snippet && a.chunk == b.chunk)
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
    pub dimension: usize,
    pub last_stats: Option<Stats>,
    pub prepare_workers: usize,
    commit_warning: Option<String>,
}
#[derive(Default, Deserialize, Serialize)]
pub struct Cleanup {
    pub candidates: usize,
    pub candidate_bytes: u64,
    pub removed: usize,
    pub freed_bytes: u64,
    pub failed: usize,
}

fn snapshot_name(path: &str) -> Res<(&str, &str)> {
    let mut parts = path.split('/');
    let prefix = parts.next();
    let shard = parts.next().unwrap_or_default();
    let stamp = parts.next().unwrap_or_default();
    if prefix != Some("folders") || parts.next().is_some() || shard.len() != 20
        || !shard.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || stamp.is_empty() || !stamp.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("Unrecognized snapshot path: {path}"));
    }
    Ok((shard, stamp))
}

fn plain_dir(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|m| m.is_dir() && m.file_attributes() & 0x400 == 0)
}

fn snapshot_files(path: &Path) -> Res<Option<Vec<(PathBuf, u64)>>> {
    let mut files = Vec::new();
    for entry in fs::read_dir(path).map_err(err)? {
        let entry = entry.map_err(err)?;
        let p = entry.path();
        let m = fs::symlink_metadata(&p).map_err(err)?;
        if !m.is_file() || m.file_attributes() & 0x400 != 0
            || !p.file_name().and_then(|s| s.to_str()).is_some_and(|name| SNAPSHOT_FILES.contains(&name)) {
            return Ok(None);
        }
        files.push((p, m.len()));
    }
    Ok(Some(files))
}

// Hash equality alone does not establish that an index can actually be opened.
// Keep vector verification bounded to one row rather than loading the library.
fn verify_snapshot_contents(base: &Path, dimension: usize, vectors: bool) -> Res<()> {
    let index_path = base.join("index.json");
    let index: Index = read_json(&index_path)?;
    if ![1, 2].contains(&index.version) || index.dimension != dimension
        || index.fingerprint != fingerprint(dimension) {
        return Err(format!("Invalid snapshot metadata: {}", display_path(&index_path)));
    }
    let count = index.records.len() as u64;
    drop(index);
    let search_path = base.join("vectors.f32");
    let file = File::open(&search_path).map_err(err)?;
    if file.metadata().map_err(err)?.len() != count.checked_mul((dimension * 4) as u64).ok_or("Vector size overflow")? {
        return Err(format!("Invalid vector length: {}", display_path(&search_path)));
    }
    let native_path = base.join("native-vectors.f32");
    let native = if dimension < 768 {
        let f = File::open(&native_path).map_err(err)?;
        if f.metadata().map_err(err)?.len() != count.checked_mul(768 * 4).ok_or("Vector size overflow")? {
            return Err(format!("Invalid vector length: {}", display_path(&native_path)));
        }
        Some(f)
    } else { None };
    if !vectors { return Ok(()); }
    let mut search = BufReader::new(file);
    let mut native = native.map(BufReader::new);
    let mut bytes = [0u8; 768 * 4];
    let mut search_row = [0.; 768];
    let mut native_row = [0.; 768];
    for row in 0..count {
        search.read_exact(&mut bytes[..dimension * 4]).map_err(err)?;
        for (value, b) in search_row[..dimension].iter_mut().zip(bytes[..dimension * 4].chunks_exact(4)) {
            *value = f32::from_le_bytes(b.try_into().unwrap()) as f64;
        }
        let v = &search_row[..dimension];
        if v.iter().any(|v| !v.is_finite()) || dot(v, v) <= 1e-12 {
            return Err(format!("Invalid vector row {row}: {}", display_path(&search_path)));
        }
        if let Some(native) = native.as_mut() {
            native.read_exact(&mut bytes).map_err(err)?;
            for (value, b) in native_row.iter_mut().zip(bytes.chunks_exact(4)) {
                *value = f32::from_le_bytes(b.try_into().unwrap()) as f64;
            }
            if !valid(&native_row) || mrl(&native_row, dimension).iter().zip(v).any(|(a, b)| (a - b).abs() > 1e-6) {
                return Err(format!("Invalid full-dimension cache row {row}: {}", display_path(&native_path)));
            }
        }
    }
    Ok(())
}

fn verify_saved_snapshot(dir: &Path, info: &RootInfo, dimension: usize, hashes: bool,
    verified: &mut HashSet<(PathBuf, String)>, validated: &mut HashSet<(String, usize)>) -> Res<()> {
    let (shard, stamp) = snapshot_name(&info.snapshot)?;
    let folders = dir.join("folders");
    let base = folders.join(shard).join(stamp);
    if !plain_dir(&folders) || !plain_dir(&folders.join(shard)) || !plain_dir(&base) {
        return Err(format!("Missing or linked protected snapshot {}", info.snapshot));
    }
    for (name, expected) in [("index.json", &info.index_sha), ("vectors.f32", &info.vectors_sha),
        ("native-vectors.f32", &info.native_vectors_sha)] {
        if name == "native-vectors.f32" && dimension == 768 { continue; }
        let file = base.join(name);
        let meta = fs::symlink_metadata(&file).map_err(err)?;
        if !meta.is_file() || meta.file_attributes() & 0x400 != 0 || expected.is_empty()
            || (hashes && verified.insert((file.clone(), expected.clone())) && file_hash(&file)? != *expected) {
            return Err(format!("Invalid protected file {}", display_path(&file)));
        }
    }
    if validated.insert((info.snapshot.clone(), dimension)) {
        verify_snapshot_contents(&base, dimension, hashes)?;
    }
    Ok(())
}

pub fn inspect_saved_catalog(dir: &Path, previous: bool) -> Res<serde_json::Value> {
    let _guard = SNAPSHOTS.read().map_err(err)?;
    let dir = canonical(dir)?;
    let path = dir.join(if previous { "catalog.previous.json" } else { "catalog.json" });
    let meta = fs::symlink_metadata(&path).map_err(err)?;
    if !meta.is_file() || meta.file_attributes() & 0x400 != 0 { return Err("Catalog is not a regular file".into()); }
    let catalog: Catalog = read_json(&path)?;
    if ![1, 2].contains(&catalog.version) || !INDEX_DIMENSIONS.contains(&catalog.dimension)
        || catalog.fingerprint != fingerprint(catalog.dimension) {
        return Err(format!("Invalid catalog {}", display_path(&path)));
    }
    let mut verified = HashSet::new();
    let mut validated = HashSet::new();
    for root in &catalog.roots {
        verify_saved_snapshot(&dir, root, catalog.dimension, true, &mut verified, &mut validated)?;
    }
    Ok(serde_json::json!({"passed":true,"catalog":display_path(&path),"dimension":catalog.dimension,
        "roots":catalog.roots.len(),"files":catalog.roots.iter().map(|r|r.files).sum::<usize>(),
        "vectors":catalog.roots.iter().map(|r|r.vectors).sum::<usize>(),"activated":false}))
}

// Caller holds the publication lock. Never traverse a reparse point and never
// recursively delete: an unexpected file makes the entire candidate ineligible.
fn prune_locked(dir: &Path, preview: bool, verify_hashes: bool) -> Res<Cleanup> {
    let dir = canonical(dir)?;
    let folders = dir.join("folders");
    let mut keep = HashSet::new();
    let mut verified = HashSet::new();
    let mut validated = HashSet::new();
    for name in ["catalog.json", "catalog.previous.json"] {
        let path = dir.join(name);
        let meta = match fs::symlink_metadata(&path) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && name != "catalog.json" => continue,
            Err(e) => return Err(err(e)),
        };
        if !meta.is_file() || meta.file_attributes() & 0x400 != 0 {
            return Err(format!("Cleanup refused: {name} is not a regular file"));
        }
        let catalog: Catalog = read_json(&path)?;
        if ![1, 2].contains(&catalog.version) || !INDEX_DIMENSIONS.contains(&catalog.dimension)
            || catalog.fingerprint != fingerprint(catalog.dimension) {
            return Err(format!("Cleanup refused: invalid {name}"));
        }
        for info in catalog.roots {
            verify_saved_snapshot(&dir, &info, catalog.dimension, verify_hashes, &mut verified, &mut validated)
                .map_err(|e|format!("Cleanup refused: {e}"))?;
            keep.insert(info.snapshot);
        }
    }
    let mut result = Cleanup::default();
    if !plain_dir(&folders) { return Ok(result); }
    for shard in fs::read_dir(&folders).map_err(err)? {
        let shard = shard.map_err(err)?;
        if !plain_dir(&shard.path()) { continue; }
        let name = shard.file_name();
        let Some(name) = name.to_str() else { continue; };
        if snapshot_name(&format!("folders/{name}/0")).is_err() { continue; }
        for snap in fs::read_dir(shard.path()).map_err(err)? {
            let snap = snap.map_err(err)?;
            let rel = format!("folders/{name}/{}", snap.file_name().to_string_lossy());
            if keep.contains(&rel) || snapshot_name(&rel).is_err() { continue; }
            let path = snap.path();
            if !plain_dir(&path) || canonical(&path)? != dir.join(&rel) { continue; }
            let Some(files) = snapshot_files(&path)? else { continue; };
            result.candidates += 1;
            result.candidate_bytes += files.iter().map(|(_, len)| len).sum::<u64>();
            if preview { continue; }
            for (file, len) in files {
                if fs::remove_file(file).is_ok() { result.freed_bytes += len; }
            }
            if fs::remove_dir(&path).is_ok() { result.removed += 1; }
            else { result.failed += 1; }
        }
    }
    Ok(result)
}

pub fn cleanup_snapshots(dir: &Path, preview: bool) -> Res<Cleanup> {
    let _guard = SNAPSHOTS.write().map_err(err)?;
    prune_locked(dir, preview, true)
}
#[derive(Clone, Serialize)]
pub struct SearchFolder {
    pub path: PathBuf,
    pub files: usize,
    pub depth: usize,
}
#[derive(Default, Deserialize, Serialize, Clone)]
#[serde(default)]
pub struct Stats {
    pub root: PathBuf,
    pub error: Option<String>,
    pub discovered: usize,
    pub embedded: usize,
    pub reused: usize,
    pub failed: usize,
    pub removed: usize,
    pub cancelled: bool,
    pub warnings: Vec<String>,
    pub elapsed_seconds: f64,
    pub processed: usize,
    pub index_dimension: usize,
    pub embedding_seconds: f64,
    pub snapshot_seconds: f64,
    pub hashed_files: usize,
    pub unchanged_files: usize,
    pub metadata_skipped: usize,
    pub preparation_workers: usize,
    pub preparation_seconds: f64,
    pub preparation_wait_seconds: f64,
    pub prepared_images: usize,
    pub checkpoints: usize,
}
#[derive(Clone, Default, Serialize)]
pub struct IndexProgress {
    pub phase: String,
    pub processed: usize,
    pub total: usize,
    pub embedded: usize,
    pub reused: usize,
    pub failed: usize,
    pub metadata_skipped: usize,
    pub elapsed_seconds: f64,
    pub files_per_second: f64,
    pub new_files_per_second: f64,
    pub eta_seconds: Option<f64>,
}
impl IndexProgress {
    fn current(phase: &str, stats: &Stats, started: Instant, processing: Instant) -> Self {
        let seconds = processing.elapsed().as_secs_f64().max(0.001);
        let skipped = if phase == "Indexing" {stats.metadata_skipped} else {0};
        let processed = stats.processed.saturating_sub(skipped);
        let total = stats.discovered.saturating_sub(skipped);
        let rate = processed as f64 / seconds;
        Self {
            phase: phase.into(),
            processed,
            total,
            embedded: stats.embedded,
            reused: stats.reused,
            failed: stats.failed,
            metadata_skipped: stats.metadata_skipped,
            elapsed_seconds: started.elapsed().as_secs_f64(),
            files_per_second: rate,
            new_files_per_second: stats.embedded as f64 / seconds,
            eta_seconds: if rate > 0. && phase == "Indexing" {
                Some(total.saturating_sub(processed) as f64 / rate)
            } else {
                None
            },
        }
    }
}
impl Library {
    pub fn dimension_on_disk(dir: &Path) -> Res<Option<usize>> {
        let path = dir.join("catalog.json");
        if !path.exists() { return Ok(None); }
        let c: Catalog = read_json(&path)?;
        if !INDEX_DIMENSIONS.contains(&c.dimension) || ![1, 2].contains(&c.version)
            || c.fingerprint != fingerprint(c.dimension) {
            return Err("Library belongs to a different embedding/preparation format".into());
        }
        Ok(Some(c.dimension))
    }
    pub fn open(dir: &Path) -> Res<Self> {
        Self::open_with_dimension(dir, DEFAULT_INDEX_DIMENSION)
    }
    pub fn open_with_dimension(dir: &Path, dimension: usize) -> Res<Self> {
        let _guard = SNAPSHOTS.read().map_err(err)?;
        if !INDEX_DIMENSIONS.contains(&dimension) { return Err("Unsupported index dimension".into()); }
        fs::create_dir_all(dir).map_err(err)?;
        if dir.join("index.json").exists() || dir.join("vectors.f32").exists() {
            return Err("Choose a new native data folder; legacy libraries are read-only.".into());
        }
        let p = dir.join("catalog.json");
        let mut lib = Self {
            dir: dir.to_owned(),
            roots: vec![],
            generation: 0,
            dimension,
            last_stats: None,
            prepare_workers: crate::image_pipeline::DEFAULT_WORKERS,
            commit_warning: None,
        };
        if !p.exists() {
            return Ok(lib);
        }
        let c: Catalog = read_json(&p)?;
        if ![1, 2].contains(&c.version) || !INDEX_DIMENSIONS.contains(&c.dimension)
            || c.fingerprint != fingerprint(c.dimension) {
            return Err("Library belongs to a different embedding/preparation format".into());
        }
        lib.dimension = c.dimension;
        for mut info in c.roots {
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
            let mut idx: Index = parse_json(&base.join("index.json"), &ib)?;
            if ![1, 2].contains(&idx.version)
                || idx.dimension != lib.dimension
                || idx.fingerprint != fingerprint(lib.dimension)
                || vb.len() != idx.records.len() * lib.dimension * 4
            {
                return Err("Invalid vector snapshot".into());
            }
            let native_bytes = if lib.dimension == 768 { None } else {
                let bytes = fs::read(base.join("native-vectors.f32")).map_err(err)?;
                if bytes.len() != idx.records.len() * 768 * 4 || info.native_vectors_sha.is_empty()
                    || hash(&bytes) != info.native_vectors_sha {
                    return Err("Invalid full-dimension style cache; active snapshots are preserved".into());
                }
                Some(bytes)
            };
            let native = native_bytes.as_deref().unwrap_or(&vb);
            for ((r, row), raw) in idx.records.iter_mut().zip(vb.chunks_exact(lib.dimension * 4))
                .zip(native.chunks_exact(768 * 4)) {
                r.search_vector = row
                    .chunks_exact(4)
                    .map(|x| f32::from_le_bytes(x.try_into().unwrap()) as f64)
                    .collect::<Vec<_>>().into();
                r.vector = if lib.dimension == 768 { r.search_vector.clone() } else {
                    raw.chunks_exact(4).map(|x| f32::from_le_bytes(x.try_into().unwrap()) as f64)
                        .collect::<Vec<_>>().into()
                };
                if !valid(&r.vector) || r.search_vector.iter().any(|v| !v.is_finite())
                    || dot(&r.search_vector, &r.search_vector) < 1e-12 {
                    return Err("Invalid library vector".into());
                }
                if lib.dimension < 768 && mrl(&r.vector, lib.dimension).iter().zip(r.search_vector.iter())
                    .any(|(a, b)| (a-b).abs() > 1e-6) {
                    return Err("Index and full-dimension cache disagree".into());
                }
            }
            info.index_bytes = (ib.len() + vb.len() + if lib.dimension < 768 { native.len() } else { 0 }) as u64;
            info.source_bytes = source_totals(&idx.records).1;
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
    pub fn set_dimension(&mut self, dimension: usize) -> Res<()> {
        if !INDEX_DIMENSIONS.contains(&dimension) { return Err("Unsupported index dimension".into()); }
        if dimension == self.dimension { return Ok(()); }
        if self.roots.iter().any(|root| !root.records.is_empty()) {
            return Err(format!("This library already uses {} dimensions. Its dimension is preserved; use a new library for a different dimension.", self.dimension));
        }
        self.dimension = dimension;
        self.commit()
    }
    pub fn search_folders(&self) -> Vec<SearchFolder> {
        let roots = self.roots.iter().map(|r| &r.info.path).collect::<Vec<_>>();
        let mut folders: BTreeMap<String, SearchFolder> = BTreeMap::new();
        for root in &roots {
            folders.entry(key(root)).or_insert(SearchFolder { path: (*root).clone(), files: 0, depth: 0 });
        }
        let mut files = HashSet::new();
        for record in self.roots.iter().flat_map(|r| &r.records) {
            if !files.insert(key(&record.path)) { continue; }
            let Some(root) = roots.iter().filter(|r| under(&record.path, r))
                .min_by_key(|r| key(r).len()) else { continue; };
            let mut parent = record.path.parent();
            while let Some(path) = parent {
                if !under(path, root) { break; }
                let depth = path.components().count().saturating_sub(root.components().count());
                let row = folders.entry(key(path)).or_insert(SearchFolder {path: path.into(), files: 0, depth});
                row.files += 1;
                parent = path.parent();
            }
        }
        folders.into_values().collect()
    }
    pub fn all(&self) -> Vec<Record> {
        self.roots
            .iter()
            .flat_map(|r| r.records.iter().cloned())
            .collect()
    }
    pub fn commit(&mut self) -> Res<()> {
        self.commit_warning = None;
        let _guard = SNAPSHOTS.write().map_err(err)?;
        let p = self.dir.join("catalog.json");
        let previous = if p.exists() { Some(read_json::<Catalog>(&p)?) } else { None };
        if previous.as_ref().map_or(0, |c| c.generation) != self.generation {
            return Err("Library changed; reload before committing".into());
        }
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
            let index = IndexView {
                version: if self.dimension == 768 {1} else {2},
                fingerprint: fingerprint(self.dimension),
                records: &records,
                dimension: self.dimension,
            };
            let ib = serde_json::to_vec(&index).map_err(err)?;
            serde_json::from_slice::<Index>(&ib).map_err(|e| format!(
                "Refusing to publish unreadable index JSON for {}: {e}", display_path(&root.info.path)))?;
            let mut vb = Vec::with_capacity(records.len() * self.dimension * 4);
            let mut nb = Vec::new();
            for r in &mut records {
                if !valid(&r.vector) {
                    return Err("Refusing to commit invalid vectors".into());
                }
                // Loaded vectors already have native float32 precision. Retain
                // their shared allocation instead of copying every cached row.
                if r.vector.iter().any(|v| (*v as f32) as f64 != *v) {
                    r.vector = r.vector.iter().map(|v| (*v as f32) as f64).collect::<Vec<_>>().into();
                }
                r.search_vector = if self.dimension == 768 {r.vector.clone()}
                    else {mrl(&r.vector, self.dimension).iter().map(|v| (*v as f32) as f64).collect::<Vec<_>>().into()};
                if !valid(&r.vector) || r.search_vector.iter().any(|v| !v.is_finite())
                    || dot(&r.search_vector, &r.search_vector) <= 1e-12 {
                    return Err("Refusing to publish invalid stored vectors".into());
                }
                for &x in r.search_vector.iter() {
                    vb.extend_from_slice(&(x as f32).to_le_bytes());
                }
                if self.dimension < 768 {
                    for &x in r.vector.iter() { nb.extend_from_slice(&(x as f32).to_le_bytes()); }
                }
            }
            let ih = hash(&ib);
            let vh = hash(&vb);
            let nh = if self.dimension < 768 {hash(&nb)} else {String::new()};
            if root.info.index_sha != ih
                || root.info.vectors_sha != vh
                || root.info.native_vectors_sha != nh
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
                if self.dimension < 768 { atomic(&dest.join("native-vectors.f32"), &nb)?; }
                if file_hash(&dest.join("index.json"))? != ih
                    || file_hash(&dest.join("vectors.f32"))? != vh
                    || (self.dimension < 768 && file_hash(&dest.join("native-vectors.f32"))? != nh)
                {
                    return Err("Snapshot verification failed; active catalog unchanged".into());
                }
                root.info.snapshot = snap;
                root.info.index_sha = ih;
                root.info.vectors_sha = vh;
                root.info.native_vectors_sha = nh;
                root.info.updated = stamp();
            }
            root.info.index_bytes = (ib.len() + vb.len() + nb.len()) as u64;
            (root.info.files, root.info.source_bytes) = source_totals(&records);
            root.info.vectors = records.len();
            root.records = records;
        }
        if previous.as_ref().is_some_and(|c| c.dimension == self.dimension
            && c.roots.len() == self.roots.len() && c.roots.iter().zip(&self.roots)
                .all(|(old, root)| old.path == root.info.path && old.snapshot == root.info.snapshot)) {
            return Ok(()); // A no-op must not replace the distinct recovery copy.
        }
        let generation = stamp();
        let c = Catalog {
            version: if self.dimension == 768 {1} else {2},
            fingerprint: fingerprint(self.dimension),
            generation,
            roots: self.infos(),
            dimension: self.dimension,
        };
        let predecessor = previous.as_ref().map(|_| fs::read(&p).map_err(err)).transpose()?;
        // Keep the distinct recovery catalog intact until publication succeeds.
        // A crash between these two atomic writes keeps the older recovery copy;
        // no snapshot pruning occurs until the new recovery reference is saved.
        save_json(&p, &c)?;
        self.generation = generation;
        if let Some(bytes) = predecessor {
            if let Err(error) = atomic(&self.dir.join("catalog.previous.json"), &bytes) {
                let warning = format!("Index saved, but the recovery catalog could not be updated: {error}. Older snapshots were retained.");
                let _ = save_json(&self.dir.join("recovery-catalog-error.json"),
                    &serde_json::json!({"error":&warning,"predecessor":previous}));
                self.commit_warning = Some(warning);
                return Ok(());
            }
        }
        let _ = fs::remove_file(self.dir.join("recovery-catalog-error.json"));
        // The publish succeeded even if cleanup cannot run (for example, an
        // antivirus temporarily holds an older file). Keep and report leftovers.
        match prune_locked(&self.dir, false, false) {
            Ok(r) if r.failed == 0 => {},
            Ok(r) => { let _ = save_json(&self.dir.join("snapshot-cleanup-error.json"), &r); },
            Err(e) => { let _ = save_json(&self.dir.join("snapshot-cleanup-error.json"), &serde_json::json!({"error":e})); },
        }
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
        committed: &dyn Fn(&Library),
    ) -> Res<Stats> {
        self.index_run(path, kinds, api, cancel, progress, excluded, live, committed, true)
    }
    pub fn update(
        &mut self, path:&Path, kinds:&HashSet<String>, api:&Api, cancel:&AtomicBool,
        progress:&dyn Fn(String), excluded:&Path,
    ) -> Res<Stats> {
        self.update_live(path, kinds, api, cancel, progress, excluded, &|_|{}, &|_|{})
    }
    pub fn update_live(
        &mut self, path:&Path, kinds:&HashSet<String>, api:&Api, cancel:&AtomicBool,
        progress:&dyn Fn(String), excluded:&Path, live:&dyn Fn(IndexProgress), committed:&dyn Fn(&Library),
    ) -> Res<Stats> {
        self.index_run(path, kinds, api, cancel, progress, excluded, live, committed, false)
    }
    fn index_run(
        &mut self, path:&Path, kinds:&HashSet<String>, api:&Api, cancel:&AtomicBool,
        progress:&dyn Fn(String), excluded:&Path, live:&dyn Fn(IndexProgress), committed:&dyn Fn(&Library),
        verify_contents:bool,
    ) -> Res<Stats> {
        let started = Instant::now();
        self.commit_warning = None;
        let mut stats = Stats {root: path.into(), index_dimension:self.dimension, ..Default::default()};
        let result = self.index_inner(path, kinds, api, cancel, progress, excluded, live, committed, started, &mut stats, verify_contents);
        stats.elapsed_seconds = started.elapsed().as_secs_f64();
        if let Err(error) = &result {
            stats.error = Some(error.clone());
            live(IndexProgress::current("Failed", &stats, started, started));
        }
        if let Some(warning) = self.commit_warning.take() { stats.warnings.insert(0, warning); }
        stats.warnings.truncate(200);
        self.last_stats = Some(stats.clone());
        result.map(|_| stats)
    }
    fn index_inner(
        &mut self, path:&Path, kinds:&HashSet<String>, api:&Api, cancel:&AtomicBool,
        progress:&dyn Fn(String), excluded:&Path, live:&dyn Fn(IndexProgress),
        committed:&dyn Fn(&Library), started:Instant, stats:&mut Stats, verify_contents:bool,
    ) -> Res<()> {
        let path = canonical(path)?;
        if !path.is_dir() {
            return Err("Index root is not a folder".into());
        }
        if under(&path, &self.dir) {
            return Err("The index's own working folder cannot be indexed as source content.".into());
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
        for r in self.roots.iter().flat_map(|root| &root.records) {
            if under(&r.path, &path) {
                old.entry(key(&r.path)).or_default().push(r.clone());
            }
        }
        let root_keys = self.roots.iter().map(|r| {
            let k = key(&r.info.path); let prefix = format!("{k}\\"); (k,prefix)
        }).collect::<Vec<_>>();
        let mut seen = HashSet::new();
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
                    total: stats.discovered,
                    processed: stats.processed,
                    reused: stats.reused,
                    metadata_skipped: stats.metadata_skipped,
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
                if scan_tick.elapsed().as_millis() >= 250 {
                    live(IndexProgress {phase:"Scanning".into(), total:stats.discovered, processed:stats.processed,
                        reused:stats.reused, metadata_skipped:stats.metadata_skipped,
                        elapsed_seconds:started.elapsed().as_secs_f64(), ..Default::default()});
                    scan_tick = Instant::now();
                }
                if cancel.load(Ordering::Relaxed) {
                    stats.cancelled = true;
                    complete = false;
                    break;
                }
                let e = match e {
                    Ok(e) => e,
                    Err(e) => {
                        complete = false;
                        stats.warnings.push(e.to_string());
                        continue;
                    }
                };
                let p = e.path();
                // Windows directory entries already contain size, date and
                // attributes. Reuse them without a separate per-file stat.
                // DirEntry::metadata does not follow symlinks.
                let m = match e.metadata() {
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
                        || under(&p, excluded) || under(&p, &self.dir)
                    {
                        continue;
                    }
                    stack.push(p);
                } else if m.is_file() {
                    if let Some(k) = extract::kind(&p) {
                        if kinds.contains(k) {
                            let pk = key(&p);
                            let owner = root_keys.iter().enumerate()
                                .filter(|(_,r)|pk==r.0 || pk.starts_with(&r.1))
                                .max_by_key(|(_,r)|r.0.len()).map(|(i,_)|i);
                            if owner == Some(at) {
                                stats.discovered += 1;
                                let modified = m.modified().ok().and_then(|t|t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d|d.as_nanos());
                                let unchanged = !verify_contents && old.get(&pk).is_some_and(|rs|
                                    !rs.is_empty() && !(matches!(k,"text" | "document") && rs.len()>=400)
                                    && rs.iter().all(|r|r.size==m.len() && Some(r.modified)==modified && r.kind==k));
                                if unchanged {
                                    // Reuse index.json metadata; do not open the source file,
                                    // sort it into the work queue, or rotate a snapshot.
                                    seen.insert(pk);
                                    stats.metadata_skipped += 1;
                                    stats.unchanged_files += 1;
                                    stats.reused += 1;
                                    stats.processed += 1;
                                    continue;
                                }
                                files.push((p, k.to_string()));
                            }
                        }
                    }
                }
            }
        }
        let mut needs_publish = self.roots[at].info.snapshot.is_empty();
        if files.is_empty() && !needs_publish && old.keys().all(|k|seen.contains(k)) {
            stats.cancelled |= cancel.load(Ordering::Relaxed);
            live(IndexProgress::current(if stats.cancelled {"Cancelled"} else {"Complete"}, stats, started, started));
            return Ok(());
        }
        files.sort_by(|a, b| key(&a.0).cmp(&key(&b.0)));
        // Content-addressed reuse is needed only if there is actual work.
        let mut cache: HashMap<(String, String), Vec<Record>> = HashMap::new();
        if !files.is_empty() {
            let mut cached_files: BTreeMap<String, Vec<Record>> = BTreeMap::new();
            for r in self.roots.iter().flat_map(|root| &root.records) {
                cached_files.entry(key(&r.path)).or_default().push(r.clone());
            }
            for records in cached_files.into_values() {
                if let Some(r) = records.first() {
                    cache.insert((r.hash.clone(), cache_kind(&r.path, &r.kind)), records);
                }
            }
        }
        let mut updated = old.clone();
        let mut checkpoint = Instant::now();
        let mut since = 0;
        let processing = Instant::now();
        let mut published = false;
        let mut progress_tick = Instant::now();
        live(IndexProgress::current(
            "Indexing", &stats, started, processing,
        ));
        let known=cache.keys().filter(|(_,kind)|kind=="image").map(|(h,_)|h.clone()).collect();
        let mut preparation=crate::image_pipeline::Pool::new(self.prepare_workers,files.len(),known)?;
        stats.preparation_workers=preparation.count;
        for (i,(p,k)) in files.iter().take(preparation.window).enumerate(){preparation.submit(i,p,k)?;}
        for (i, (p, k)) in files.iter().enumerate() {
            if cancel.load(Ordering::Relaxed) {
                stats.cancelled = true;
                complete = false;
                break;
            }
            let pk = key(p);
            seen.insert(pk.clone());
            let waiting=Instant::now();
            let ready=preparation.take(i,cancel);
            stats.preparation_wait_seconds+=waiting.elapsed().as_secs_f64();
            let next=i+preparation.window;
            if next<files.len() && !cancel.load(Ordering::Relaxed) {preparation.submit(next,&files[next].0,&files[next].1)?;}
            let result = (|| -> Res<Vec<Record>> {
                let outcome=ready?;
                stats.preparation_seconds+=outcome.seconds;
                stats.hashed_files+=usize::from(outcome.hashed);
                let prepared=outcome.value?;
                stats.prepared_images+=usize::from(prepared.image.is_some());
                let h=prepared.hash;
                let modified=prepared.modified;
                let size=prepared.size;
                let m=fs::metadata(p).map_err(err)?;
                let current_modified = m
                    .modified()
                    .map_err(err)?
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_err(err)?
                    .as_nanos();
                if m.len()!=size || current_modified!=modified {
                    return Err("File changed after preparation; previous vectors retained".into());
                }
                // Older builds could cache a silently truncated 400-chunk file.
                // Validate that limit before reusing such a document's vectors.
                if matches!(k.as_str(), "text" | "document")
                    && cache.get(&(h.clone(), cache_kind(p, k))).is_some_and(|rs|rs.len() >= 400) {
                    extract::chunks(&extract::text(p)?)?;
                }
                if let Some(existing) = old.get(&pk).filter(|rs| !rs.is_empty() && rs.iter().all(|r|
                    r.hash == h && r.size == size && r.modified == modified && r.kind == *k)) {
                    stats.reused += 1;
                    stats.unchanged_files += 1;
                    return Ok(existing.clone());
                }
                if let Some(existing) = cache.get(&(h.clone(), cache_kind(p, k))) {
                    stats.reused += 1;
                    return Ok(existing
                        .iter()
                        .map(|r| Record {
                            path: p.clone(),
                            modified,
                            size,
                            ..r.clone()
                        })
                        .collect());
                }
                let embedding_started = Instant::now();
                let parts = if k=="image" {
                    let (mime,bytes)=prepared.image.ok_or("Missing prepared image; previous vectors retained")?;
                    api.prepared_image_cancellable(&mime,&bytes,cancel).map(|v|vec![(String::new(),v)])
                } else {extract::embed_file(api, p, k, cancel)};
                stats.embedding_seconds += embedding_started.elapsed().as_secs_f64();
                let parts = parts?;
                if file_hash(p)? != h {
                    return Err("File changed during embedding; old vectors retained".into());
                }
                let records = parts
                    .into_iter()
                    .enumerate()
                    .map(|(chunk, (snippet, vector))| Record {
                        path: p.clone(),
                        hash: h.clone(),
                        size,
                        modified,
                        kind: k.clone(),
                        snippet,
                        chunk,
                        vector: vector.into(),
                        search_vector: Default::default(),
                    })
                    .collect::<Vec<_>>();
                cache.insert((h, cache_kind(p, k)), records.clone());
                if k=="image" {preparation.mark_cached(&records[0].hash);}
                stats.embedded += 1;
                Ok(records)
            })();
            if result.is_err() && cancel.load(Ordering::Relaxed) {
                stats.cancelled = true;
                complete = false;
                break;
            }
            match result {
                Ok(r) => {
                    if updated.get(&pk).is_none_or(|existing| !same_records(existing, &r)) {
                        updated.insert(pk, r);
                        since += 1;
                    }
                }
                Err(e) => {
                    stats.failed += 1;
                    stats.warnings.push(format!("{}: {e}", p.display()));
                }
            }
            stats.processed = stats.metadata_skipped + i + 1;
            // Fast cache-only scans must not flood the GUI event queue.
            if i == 0 || i + 1 == files.len() || progress_tick.elapsed().as_millis() >= 100 {
                progress(format!("{}/{} · {}", i + 1, files.len(),
                    p.file_name().unwrap_or_default().to_string_lossy()));
                live(IndexProgress::current("Indexing", &stats, started, processing));
                progress_tick = Instant::now();
            }
            if since > 0 && (!published || since >= 200 || checkpoint.elapsed().as_secs() >= 30) {
                self.roots[at].records = updated.values().flatten().cloned().collect();
                let writing = Instant::now();
                self.commit()?;
                stats.snapshot_seconds += writing.elapsed().as_secs_f64();
                stats.checkpoints += 1;
                committed(self);
                published = true;
                needs_publish = false;
                since = 0;
                checkpoint = Instant::now();
            }
        }
        drop(preparation);
        if cancel.load(Ordering::Relaxed) {
            stats.cancelled = true;
            complete = false;
        }
        if complete {
            updated.retain(|k, rs| {
                let remove = !seen.contains(k)
                    && rs
                        .first()
                        .is_some_and(|r| {
                            if !kinds.contains(&r.kind) { return false; }
                            match fs::symlink_metadata(&r.path) {
                                Err(e) if e.kind() == std::io::ErrorKind::NotFound => true,
                                Err(e) => { stats.warnings.push(format!("Cannot check {}; previous vectors retained: {e}", display_path(&r.path))); false },
                                Ok(_) => false,
                            }
                        });
                if remove {
                    stats.removed += 1;
                }
                !remove
            });
        }
        if updated.is_empty() && !old.is_empty() {
            return Err("Empty-scan guard: previous library preserved".into());
        }
        if since > 0 || needs_publish || stats.removed > 0 {
            self.roots[at].records = updated.into_values().flatten().collect();
            let writing = Instant::now();
            self.commit()?;
            stats.snapshot_seconds += writing.elapsed().as_secs_f64();
            stats.checkpoints += 1;
            committed(self);
        }
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
        Ok(())
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
