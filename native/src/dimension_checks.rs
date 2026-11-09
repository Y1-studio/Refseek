//! Opt-in real-model diagnostics and balanced indexing timings. All writes go
//! to the explicitly selected diagnostic home, never to an existing library.
use crate::{Options, runtime::Runtime, search::{Engine, Query}, store::Library, util::*};
use std::{collections::HashSet, path::Path, sync::{Arc, atomic::AtomicBool}, time::Instant};

pub fn run(o: &Options, assets: &Path, runtime: &Runtime, cancel: &Arc<AtomicBool>,
    progress: &dyn Fn(String)) -> Res<serde_json::Value> {
    let root = o.home.join("verification/dimension-checks").join(stamp().to_string());
    let source = root.join("pictures");
    for (name, image) in [("one.jpg", "b01-001.jpg"), ("skip/two.jpg", "b01-014.jpg"),
        ("skip/keep/three.jpg", "b01-021.jpg")] {
        let path = source.join(name);
        std::fs::create_dir_all(path.parent().unwrap()).map_err(err)?;
        std::fs::copy(assets.join("validation").join(image), path).map_err(err)?;
    }
    let kinds: HashSet<String> = ["image".into()].into_iter().collect();
    let mut legacy = Library::open_with_dimension(&root.join("legacy"), 768)?;
    legacy.index(&source, &kinds, &runtime.api, cancel, progress, &o.home.join("data"))?;
    // Remove the new optional fields to exercise the actual old on-disk schema.
    let catalog_path = legacy.dir.join("catalog.json");
    let mut catalog: serde_json::Value = read_json(&catalog_path)?;
    catalog.as_object_mut().unwrap().remove("dimension");
    for info in catalog["roots"].as_array_mut().unwrap() {
        let path = legacy.dir.join(info["snapshot"].as_str().unwrap()).join("index.json");
        let mut index: serde_json::Value = read_json(&path)?;
        index.as_object_mut().unwrap().remove("dimension");
        save_json(&path, &index)?;
        info["index_sha"] = serde_json::json!(file_hash(&path)?);
    }
    save_json(&catalog_path, &catalog)?;
    let legacy = Library::open_with_dimension(&legacy.dir, 512)?;
    if legacy.dimension != 768 { return Err("Existing legacy dimension was not detected".into()); }
    let before = file_hash(&catalog_path)?;
    let _ = Library::open(&legacy.dir)?;
    if before != file_hash(&catalog_path)? { return Err("Opening a legacy index changed it".into()); }
    let mut checks = vec![];
    let mut reference = std::collections::HashMap::new();
    for dimension in [768, 512, 256, 128] {
        let mut lib = Library::open_with_dimension(&root.join(format!("dim-{dimension}")), dimension)?;
        lib.roots = legacy.roots.clone();
        for root in &mut lib.roots { root.info.snapshot.clear(); }
        lib.commit()?;
        let reload = Library::open_with_dimension(&lib.dir, 512)?;
        if reload.dimension != dimension || reload.all().len() != 3 { return Err("Dimension reload failed".into()); }
        for record in reload.all() {
            if record.search_vector.len() != dimension || !valid(&record.vector)
                || (dot(&record.search_vector, &record.search_vector) - 1.).abs() > 1e-6 {
                return Err("Invalid normalized MRL/index cache pair".into());
            }
        }
        let info = reload.infos()[0].clone();
        if std::fs::metadata(reload.dir.join(&info.snapshot).join("vectors.f32")).map_err(err)?.len()
            != (3 * dimension * 4) as u64 { return Err("Primary vector file is the wrong dimension".into()); }
        let stats = lib.index(&source, &kinds, &runtime.api, cancel, progress, &o.home.join("data"))?;
        if stats.embedded != 0 || stats.reused != 3 || lib.infos()[0].snapshot != info.snapshot {
            return Err("Dimension rescan did not reuse unchanged snapshots".into());
        }
        let locked = lib.set_dimension(if dimension == 512 {768} else {512}).is_err();
        if !locked { return Err("Nonempty library allowed a dimension change".into()); }
        let folders = reload.search_folders();
        if !folders.iter().any(|f| f.path.ends_with("skip/keep") && f.files == 1) {
            return Err("Indexed subfolders not discovered".into());
        }
        let mut engine = Engine::new(assets, &root.join(format!("engine-{dimension}")))?;
        let mut style_parity = serde_json::Map::new();
        for method in ["current", "contrastive"] {
            let q = Query {image: Some(source.join("one.jpg")), aspect:"style".into(), method:method.into(), ..Default::default()};
            let hits = engine.search(&runtime.api, &reload, &q, cancel, progress)?;
            let scores = hits.iter().map(|h| (key(&h.path),h.score)).collect::<Vec<_>>();
            if dimension == 768 {reference.insert(method.to_string(),scores.clone());}
            let expected = reference.get(method).unwrap();
            let same = scores.len() == expected.len() && scores.iter().zip(expected)
                .all(|(a,b)| a.0 == b.0 && (a.1-b.1).abs() < 1e-12);
            if !same {return Err("Frozen trained-style scores changed with index dimension".into());}
            style_parity.insert(method.into(),serde_json::json!(same));
        }
        let mut q = Query {image:Some(source.join("one.jpg")), aspect:"style".into(), strength:0., ..Default::default()};
        let zero = engine.search(&runtime.api,&reload,&q,cancel,progress)?;
        q.strength = 1e-8;
        let near_zero = engine.search(&runtime.api,&reload,&q,cancel,progress)?;
        if zero.iter().any(|a| near_zero.iter().find(|b|b.path == a.path)
            .is_none_or(|b| (a.score-b.score).abs() > 1e-7)) {
            return Err("Style strength is discontinuous at zero".into());
        }
        let q = Query {text:"a drawing of an animal".into(), roots:vec![source.clone(),source.join("skip/keep")],
            excluded_roots:vec![source.join("skip")], ..Default::default()};
        let hits = engine.search(&runtime.api, &reload, &q, cancel, progress)?;
        if hits.len() != 2 || hits.iter().any(|h| h.path.ends_with("two.jpg")) {
            return Err("Hierarchical subfolder scope failed".into());
        }
        let q = Query {text:"animal".into(), excluded_roots:vec![source.join("skip")], ..Default::default()};
        if engine.search(&runtime.api, &reload, &q, cancel, progress)?.len() != 1 {
            return Err("Exclude-only scope failed".into());
        }
        let q = Query {text:"animal".into(), roots:vec![source.join("skip")], ..Default::default()};
        if engine.search(&runtime.api, &reload, &q, cancel, progress)?.len() != 2 {
            return Err("Subfolder-only scope failed".into());
        }
        let q = Query {image:Some(source.join("one.jpg")), ignore_image:Some(source.join("skip/two.jpg")),
            aspect:"subject".into(), method:"baseline".into(), ..Default::default()};
        if engine.search(&runtime.api, &reload, &q, cancel, progress)?.iter().any(|h| !h.score.is_finite()) {
            return Err("Reduced-dimension aspect/negative search failed".into());
        }
        checks.push(serde_json::json!({"dimension":dimension,"cached_rescan":stats,"dimension_locked":locked,
            "style_scores_unchanged":style_parity,"hierarchical_scopes":true,"folders":folders}));
    }
    Ok(serde_json::json!({"legacy_768_detected":true,"legacy_open_unchanged":true,"checks":checks}))
}

pub fn benchmark(o: &Options, runtime: &Runtime, cancel: &Arc<AtomicBool>,
    progress: &dyn Fn(String)) -> Res<serde_json::Value> {
    let args = std::env::args().collect::<Vec<_>>();
    let input = args.iter().position(|s| s == "--benchmark-images").and_then(|i|args.get(i+1))
        .ok_or("Use --benchmark-images with a folder containing distinct readable full images")?;
    let source = canonical(Path::new(input))?;
    let root = o.home.join("verification/dimension-benchmark").join(stamp().to_string());
    let files = std::fs::read_dir(&source).map_err(err)?.filter_map(Result::ok)
        .map(|e|e.path()).filter(|p|p.is_file() && crate::extract::kind(p) == Some("image")).collect::<Vec<_>>();
    if files.len() < 12 {return Err("Use at least 12 distinct images for the benchmark".into());}
    let mut hashes = HashSet::new();
    for p in &files {if !hashes.insert(file_hash(p)?) {return Err("Duplicate images in benchmark input".into());}}
    progress("Warming up image inference before balanced timing rounds…".into());
    for p in files.iter().take(3) {runtime.api.image(p)?;}
    let mut rounds = vec![];
    let kinds = ["image".into()].into_iter().collect();
    for (round, order) in [[768,512,256,128],[256,768,128,512],[128,256,512,768]].into_iter().enumerate() {
        for dimension in order {
            progress(format!("Benchmark round {} / 3 · {dimension} dimensions · {} images",round+1,files.len()));
            let mut lib = Library::open_with_dimension(&root.join(format!("round-{round}-{dimension}")),dimension)?;
            let start = Instant::now();
            let stats = lib.index(&source,&kinds,&runtime.api,cancel,&|_|{},&o.home.join("data"))?;
            let elapsed = start.elapsed().as_secs_f64();
            if stats.embedded != files.len() || stats.reused != 0 || stats.failed != 0 {return Err("Benchmark did not freshly embed every input".into());}
            let mut vector_bytes = 0;
            let mut cache_bytes = 0;
            for info in lib.infos() {
                let base = lib.dir.join(info.snapshot);
                vector_bytes += std::fs::metadata(base.join("vectors.f32")).map_err(err)?.len();
                cache_bytes += std::fs::metadata(base.join("native-vectors.f32")).map(|m|m.len()).unwrap_or(0);
            }
            rounds.push(serde_json::json!({"round":round+1,"dimension":dimension,"images":files.len(),
                "seconds":elapsed,"images_per_second":files.len() as f64/elapsed,
                "embedding_seconds":stats.embedding_seconds,"snapshot_seconds":stats.snapshot_seconds,
                "primary_vector_bytes":vector_bytes,"full_style_cache_bytes":cache_bytes}));
            save_json(&o.report,&serde_json::json!({"in_progress":true,"rounds":rounds}))?;
        }
    }
    let result = serde_json::json!({"backend":runtime.backend,"device":runtime.device,"attempts":runtime.attempts,
        "images":files.len(),"source":source,"rounds":rounds,"in_progress":false,
        "notes":"Each round freshly indexes the same distinct full images with no reused vectors. Model inference remains 768d; leading dimensions are normalized for the primary index. The full cache preserves trained style scores. This measures speed, not retrieval quality; other applications can affect timings."});
    save_json(&o.report,&result)?;
    Ok(result)
}
