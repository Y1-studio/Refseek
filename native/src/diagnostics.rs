//! Opt-in manual, real-model exercise of the new snapshot format. Uses only
//! generated copies inside verification; never touches the production library.
use crate::{rpc::Api, store::Library, util::*};
use std::{
    collections::HashSet,
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
};
/// Full-frame fixtures for a manual real-model reproduction of WebP inputs.
pub fn image_inputs(home: &Path, assets: &Path) -> Res<Vec<std::path::PathBuf>> {
    let dir = home.join("verification/image-inputs");
    std::fs::create_dir_all(&dir).map_err(err)?;
    let image = crate::rpc::decoded(&assets.join("validation/b01-001.jpg"))?
        .resize(512, 512, image::imageops::FilterType::Lanczos3);
    let webp = dir.join("small.webp");
    image.save_with_format(&webp, image::ImageFormat::WebP).map_err(err)?;
    let disguised = dir.join("webp-with-jpeg-extension.jpg");
    std::fs::copy(&webp, &disguised).map_err(err)?;
    let alpha = image::RgbaImage::from_fn(190, 270, |x, y| image::Rgba([
        (x % 256) as u8, (y % 256) as u8, ((x+y) % 256) as u8, (x % 256) as u8,
    ]));
    let transparent = dir.join("transparent.webp");
    alpha.save_with_format(&transparent, image::ImageFormat::WebP).map_err(err)?;
    Ok(vec![webp, disguised, transparent])
}
pub fn image_transport(
    home: &Path, assets: &Path, api: &Api,
    engine: &mut crate::search::Engine, gallery: &Library,
    progress: &dyn Fn(String),
) -> Res<serde_json::Value> {
    let files = image_inputs(home, assets)?;
    let cancel = AtomicBool::new(false);
    let mut cases = vec![];
    for path in &files {
        let (mime, bytes) = crate::rpc::prepared(path)?;
        let source = crate::rpc::decoded(path)?;
        let transport = image::load_from_memory(&bytes).map_err(err)?;
        let lossless = source.to_rgba8() == transport.to_rgba8();
        if mime != "image/png" || !lossless {
            return Err("Small WebP input did not preserve the full decoded frame".into());
        }
        let vector = api.image(path)?;
        if !valid(&vector) { return Err("Image input returned an invalid vector".into()); }
        let query = crate::search::Query {
            image: Some(path.clone()), ignore_image: Some(files[1].clone()),
            aspect: "style".into(), ..Default::default()
        };
        let hits = engine.search(api, gallery, &query, &cancel, progress)?;
        if hits.is_empty() { return Err("Unindexed WebP reference returned no results".into()); }
        cases.push(serde_json::json!({"source":display_path(path),"sent_mime":mime,
            "lossless_pixels":lossless,"dimensions":[transport.width(),transport.height()],
            "embedding_dimensions":vector.len(),"uncached_reference_and_negative_search_hits":hits.len()}));
    }
    // JPEG and PNG transport must remain byte-for-byte unchanged.
    let jpeg = assets.join("validation/b01-001.jpg");
    let source = crate::rpc::decoded(&jpeg)?.resize(512, 512, image::imageops::FilterType::Lanczos3);
    let png = home.join("verification/image-inputs/unchanged.png");
    source.save(&png).map_err(err)?;
    let unchanged_png = crate::rpc::prepared(&png)?.1 == std::fs::read(&png).map_err(err)?;
    let unchanged_jpeg = crate::rpc::prepared(&jpeg)?.1 == std::fs::read(&jpeg).map_err(err)?;
    let mut indexed = Library::open(&home.join("verification/image-input-library"))?;
    let kinds = ["image".into()].into_iter().collect();
    let stats = indexed.index(&home.join("verification/image-inputs"), &kinds, api, &cancel, progress, &home.join("data"))?;
    if stats.failed != 0 || !unchanged_jpeg || !unchanged_png {
        return Err("Image preparation/indexing verification failed".into());
    }
    Ok(serde_json::json!({"cases":cases,"jpeg_unchanged":unchanged_jpeg,"png_unchanged":unchanged_png,
        "index":stats,"display_drive":display_path(Path::new(r"\\?\H:\draw")),
        "display_unc":display_path(Path::new(r"\\?\UNC\server\share\folder"))}))
}
pub fn storage(
    home: &Path,
    assets: &Path,
    api: &Api,
    progress: &dyn Fn(String),
) -> Res<serde_json::Value> {
    let dir = home.join("verification/storage").join(stamp().to_string());
    let a = dir.join("source/a");
    let b = dir.join("source/b");
    let sub = a.join("nested");
    for p in [&a, &b, &sub] {
        std::fs::create_dir_all(p).map_err(err)?;
    }
    std::fs::copy(assets.join("validation/b01-001.jpg"), a.join("one.jpg")).map_err(err)?;
    std::fs::copy(assets.join("validation/b01-014.jpg"), sub.join("two.jpg")).map_err(err)?;
    // Same image, another root: should reuse an existing content-addressed vector.
    std::fs::copy(assets.join("validation/b01-001.jpg"), b.join("copy.jpg")).map_err(err)?;
    std::fs::write(
        a.join("notes.txt"),
        "A native Rust semantic folder search. This document describes a dog in a garden.",
    )
    .map_err(err)?;
    let mut lib = Library::open(&dir.join("library"))?;
    let kinds: HashSet<String> = ["image", "text"].into_iter().map(String::from).collect();
    let cancel = AtomicBool::new(false);
    let excluded = home.join("data");
    let first = lib.index(&a, &kinds, api, &cancel, progress, &excluded)?;
    let one_snapshot = lib.infos()[0].snapshot.clone();
    let rescan = lib.index(&a, &kinds, api, &cancel, progress, &excluded)?;
    if rescan.embedded != 0 || rescan.reused != 3 || lib.infos()[0].snapshot != one_snapshot {
        return Err("Unchanged root did not retain its immutable snapshot".into());
    }
    let cross = lib.index(&b, &kinds, api, &cancel, progress, &excluded)?;
    if cross.embedded != 0 || cross.reused != 1 {
        return Err("Cross-folder content cache reuse failed".into());
    }
    let split = lib.index(&sub, &kinds, api, &cancel, progress, &excluded)?;
    if lib.all().len() != 4
        || lib.roots.iter().any(|r| {
            r.records
                .iter()
                .any(|v| under(&v.path, &sub) && key(&r.info.path) != key(&sub))
        })
    {
        return Err("Nested-root ownership or record count failed".into());
    }
    let previous = lib
        .all()
        .iter()
        .find(|r| r.path.file_name().unwrap_or_default() == "one.jpg")
        .unwrap()
        .clone();
    std::fs::write(a.join("one.jpg"), b"incomplete image download").map_err(err)?;
    let failed = lib.index(&a, &kinds, api, &cancel, progress, &excluded)?;
    let retained = lib
        .all()
        .iter()
        .any(|r| r.path == previous.path && r.hash == previous.hash && r.vector == previous.vector);
    if failed.failed != 1 || !retained {
        return Err("Failed replacement discarded previous vectors".into());
    }
    cancel.store(true, Ordering::Relaxed);
    let cancelled = lib.index(&a, &kinds, api, &cancel, progress, &excluded)?;
    let reload = Library::open(&dir.join("library"))?;
    if !cancelled.cancelled || reload.all().len() != 4 {
        return Err("Cancelled scan lost records".into());
    }
    let cleanup = snapshot_retention(lib)?;
    Ok(
        serde_json::json!({"initial":first,"unchanged_rescan":rescan,"cross_folder_reuse":cross,"nested_root_split":split,"failed_replacement":failed,"previous_vector_preserved":retained,"cancelled_scan":cancelled,"reload_records":reload.all().len(),"roots":reload.infos(),"cleanup":cleanup,"passed":true}),
    )
}

fn snapshot_retention(mut lib: Library) -> Res<serde_json::Value> {
    use crate::store::cleanup_snapshots;
    let active = lib.dir.join("catalog.json");
    let previous = lib.dir.join("catalog.previous.json");
    let mut changed = Vec::new();
    for i in 0..4 {
        lib.roots[0].records[0].snippet = format!("checkpoint {i}");
        lib.commit()?;
        changed.push(lib.roots[0].info.snapshot.clone());
    }
    if changed[..2].iter().any(|p| lib.dir.join(p).exists())
        || changed[2..].iter().any(|p| !lib.dir.join(p).is_dir()) {
        return Err("Checkpoint retention did not keep exactly the two referenced generations".into());
    }
    let a = std::fs::read(&active).map_err(err)?;
    let b = std::fs::read(&previous).map_err(err)?;
    lib.commit()?;
    if std::fs::read(&previous).map_err(err)? != b || std::fs::read(&active).map_err(err)? != a {
        return Err("Unchanged commit rotated the catalogs".into());
    }
    let shard = lib.dir.join(&changed[3]).parent().unwrap().to_owned();
    let old = shard.join(stamp().to_string());
    std::fs::create_dir(&old).map_err(err)?;
    let bytes = std::fs::copy(lib.dir.join(&changed[3]).join("vectors.f32"), old.join("vectors.f32")).map_err(err)?;
    let unknown = shard.join("0");
    std::fs::create_dir(&unknown).map_err(err)?;
    std::fs::write(unknown.join("keep.txt"), "not a Refseek snapshot").map_err(err)?;
    let preview = cleanup_snapshots(&lib.dir, true)?;
    if preview.candidates != 1 || preview.candidate_bytes != bytes || !old.is_dir() {
        return Err("Cleanup preview did not identify exactly the old snapshot".into());
    }
    atomic(&previous, b"invalid catalog")?;
    let refused = cleanup_snapshots(&lib.dir, false).is_err() && old.is_dir();
    atomic(&previous, &b)?;
    if !refused { return Err("Cleanup did not fail closed on an invalid recovery catalog".into()); }
    let result = cleanup_snapshots(&lib.dir, false)?;
    if result.removed != 1 || result.freed_bytes != bytes || old.exists() || !unknown.join("keep.txt").is_file()
        || std::fs::read(&active).map_err(err)? != a || std::fs::read(&previous).map_err(err)? != b
        || Library::open(&lib.dir)?.all().len() != 4 {
        return Err("Cleanup did not preserve the active/recovery data and unrelated files".into());
    }
    Ok(serde_json::json!({"automatic_retention":true,"unchanged_commit_preserved_recovery":true,
        "invalid_recovery_refused":true,"preview":preview,"cleanup":result}))
}
