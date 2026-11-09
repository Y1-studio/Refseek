//! Opt-in incremental-update timing and preservation checks. Generated data only;
//! synthetic stored vectors isolate update overhead, not inference or relevance.
use crate::{rpc::Api, store::{Library, Record, Root, RootInfo}, util::*};
use std::{cell::Cell, collections::HashSet, fs, path::Path, sync::atomic::{AtomicBool, Ordering}, time::Instant};

pub fn run(home: &Path, count: usize, dimension: usize) -> Res<serde_json::Value> {
    let base = home.join("verification/folder-update").join(stamp().to_string());
    let source = base.join("source");
    let other = base.join("other");
    fs::create_dir_all(&source).map_err(err)?;
    fs::create_dir_all(&other).map_err(err)?;
    let source = canonical(&source)?;
    let other = canonical(&other)?;
    let bytes = vec![b'a'; 16 * 1024];
    let alternate = vec![b'b'; bytes.len()];
    let make = |path: std::path::PathBuf, content: &[u8], axis: usize| -> Res<Record> {
        fs::write(&path, content).map_err(err)?;
        let metadata = fs::metadata(&path).map_err(err)?;
        let mut vector = vec![0.; 768];
        vector[axis] = 1.;
        Ok(Record {path, hash: hash(content), size: metadata.len(),
            modified: metadata.modified().map_err(err)?.duration_since(std::time::UNIX_EPOCH).map_err(err)?.as_nanos(),
            kind: "image".into(), snippet: String::new(), chunk: 0,
            vector: vector.into(), search_vector: Default::default()})
    };
    let mut records = Vec::new();
    for i in 0..count { records.push(make(source.join(format!("{i:06}.jpg")), &bytes, 0)?); }
    let mut library = Library::open_with_dimension(&base.join("library"), dimension)?;
    library.roots = vec![Root {info: RootInfo {path: source.clone(), ..Default::default()}, records},
        Root {info: RootInfo {path: other.clone(), ..Default::default()}, records: vec![
            make(other.join("alternate.jpg"), &alternate, 1)?, make(other.join("original.jpg"), &bytes, 0)?]}];
    library.commit()?;
    let kinds: HashSet<String> = ["image".into()].into_iter().collect();
    let api = Api {port: 9}; // Every valid case must reuse a stored vector: no inference.
    let cancel = AtomicBool::new(false);
    let catalog = library.dir.join("catalog.json");
    let previous = library.dir.join("catalog.previous.json");
    let saved = fs::read(&catalog).map_err(err)?;
    let recovery = fs::read(&previous).ok();
    let other_snapshot = library.roots[1].info.snapshot.clone();
    let callbacks = Cell::new(0usize);
    let progress_messages = Cell::new(0usize);
    let started = Instant::now();
    let unchanged = library.update_live(&source, &kinds, &api, &cancel,
        &|_| progress_messages.set(progress_messages.get() + 1), &home.join("data"), &|_| {},
        &|_| callbacks.set(callbacks.get() + 1))?;
    let seconds = started.elapsed().as_secs_f64();
    if unchanged.reused != count || unchanged.embedded != 0 || unchanged.hashed_files != 0
        || unchanged.metadata_skipped != count || saved != fs::read(&catalog).map_err(err)?
        || recovery != fs::read(&previous).ok() { return Err("Unchanged update changed saved catalogs or did not reuse all files".into()); }
    // Fast update must notice a normal size/date change and hash only that file.
    let normal = source.join("000001.jpg");
    let normal_modified = fs::metadata(&normal).map_err(err)?.modified().map_err(err)?;
    fs::write(&normal, &alternate).map_err(err)?;
    fs::File::options().write(true).open(&normal).map_err(err)?
        .set_times(fs::FileTimes::new().set_modified(normal_modified + std::time::Duration::from_secs(2))).map_err(err)?;
    let normal_stats = library.update(&source, &kinds, &api, &cancel, &|_|{}, &home.join("data"))?;
    if normal_stats.hashed_files != 1 || normal_stats.metadata_skipped != count-1
        || library.roots[0].records.iter().find(|r|r.path==normal).ok_or("Missing changed file")?.vector[1] != 1. {
        return Err("Fast update missed a normal modification or read unchanged source files".into());
    }
    // A full verification remains available for edits preserving size AND date.
    let changed = source.join("000000.jpg");
    let modified = fs::metadata(&changed).map_err(err)?.modified().map_err(err)?;
    fs::write(&changed, &alternate).map_err(err)?;
    fs::File::options().write(true).open(&changed).map_err(err)?
        .set_times(fs::FileTimes::new().set_modified(modified)).map_err(err)?;
    let fast_preserved = library.update(&source, &kinds, &api, &cancel, &|_|{}, &home.join("data"))?;
    if fast_preserved.hashed_files != 0 || fast_preserved.metadata_skipped != count {
        return Err("Fast update unexpectedly read a file whose saved metadata matched".into());
    }
    let changed_stats = library.index(&source, &kinds, &api, &cancel, &|_| {}, &home.join("data"))?;
    let replacement = library.roots[0].records.iter().find(|r| r.path == changed).ok_or("Missing replacement")?;
    if changed_stats.embedded != 0 || replacement.hash != hash(&alternate) || replacement.vector[1] != 1.
        || library.roots[1].info.snapshot != other_snapshot { return Err("Same-timestamp content change or unrelated shard preservation failed".into()); }
    fs::write(&changed, b"broken image replacement").map_err(err)?;
    let failed = library.update(&source, &kinds, &api, &cancel, &|_| {}, &home.join("data"))?;
    if failed.failed != 1 || library.roots[0].records.iter().find(|r| r.path == changed).ok_or("Lost failed replacement")?.hash != hash(&alternate) {
        return Err("Failed replacement discarded its previous vector".into());
    }
    fs::rename(source.join("000001.jpg"), source.join("renamed.jpg")).map_err(err)?;
    fs::write(source.join("added.jpg"), &bytes).map_err(err)?;
    let additions = library.update(&source, &kinds, &api, &cancel, &|_| {}, &home.join("data"))?;
    if additions.removed != 1 || additions.embedded != 0 || library.roots[0].records.len() != count + 1 {
        return Err("Added/renamed/deleted file update failed".into());
    }
    cancel.store(true, Ordering::Relaxed);
    let cancelled = library.update(&source, &kinds, &api, &cancel, &|_| {}, &home.join("data"))?;
    let reload = Library::open(&library.dir)?;
    if !cancelled.cancelled || reload.roots[0].records.len() != count + 1 { return Err("Cancelled update lost records".into()); }
    Ok(serde_json::json!({"passed":true,"version":env!("CARGO_PKG_VERSION"),"files":count,"dimension":dimension,
        "unchanged_seconds":seconds,"unchanged":unchanged,"checkpoint_callbacks":callbacks.get(),
        "progress_messages":progress_messages.get(),"same_size_timestamp_content_change":changed_stats,
        "normal_metadata_change":normal_stats,"same_metadata_fast_update":fast_preserved,
        "failed_replacement":failed,"add_rename_delete":additions,"cancelled":cancelled,
        "unrelated_snapshot_unchanged":true,"catalogs_unchanged_on_noop":true,
        "note":"Synthetic saved vectors, real file reads/hashes/snapshot writes. No model or retrieval-quality measurement.",
        "fixture":display_path(&base)}))
}
