#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod app;
mod diagnostics;
mod dimension_checks;
mod extract;
mod opening;
mod platform;
mod preferences;
mod rpc;
mod runtime;
mod search;
mod store;
mod style;
mod util;
mod update_checks;
mod indexing_checks;
mod image_pipeline;
mod fast_resize;
mod preparation_checks;
use std::{
    collections::HashSet,
    path::PathBuf,
    sync::{Arc, atomic::AtomicBool},
};
use util::*;
#[derive(Clone)]
pub struct Options {
    pub home: PathBuf,
    pub assets: Option<PathBuf>,
    pub data: PathBuf,
    pub private_data: PathBuf,
    pub backend: String,
    pub device: Option<String>,
    pub dimension: usize,
    pub gui_smoke: bool,
    pub smoke: bool,
    pub math: bool,
    pub report: PathBuf,
    pub dimension_check: bool,
    pub dimension_benchmark: bool,
    pub library_check: bool,
    pub prepare_workers: usize,
}
impl Options {
    fn parse() -> Res<Self> {
        let args = std::env::args().collect::<Vec<_>>();
        let value = |flag: &str| -> Option<String> {
            args.iter()
                .position(|a| a == flag)
                .and_then(|i| args.get(i + 1))
                .cloned()
        };
        let home = value("--home").map(PathBuf::from).unwrap_or(
            std::env::current_exe()
                .map_err(err)?
                .parent()
                .unwrap()
                .to_owned(),
        );
        std::fs::create_dir_all(home.join("data")).map_err(err)?;
        let home = canonical(&home)?;
        let private_data = canonical(&home.join("data"))?;
        let settings: serde_json::Value =
            read_json(&home.join("data/settings.json")).unwrap_or_default();
        let backend = value("--backend")
            .or_else(|| settings["backend"].as_str().map(str::to_string))
            .unwrap_or("auto".into());
        if !["auto", "cuda", "vulkan", "cpu"].contains(&backend.as_str()) {
            return Err("--backend must be auto, cuda, vulkan or cpu".into());
        }
        let data = value("--data-dir").map(PathBuf::from).unwrap_or_else(|| {
            if args
                .iter()
                .any(|s| ["--smoke", "--gui-smoke", "--diagnose"].contains(&s.as_str()))
            {
                home.join("verification/native-library")
            } else {
                home.join("data/library")
            }
        });
        std::fs::create_dir_all(&data).map_err(err)?;
        let data = canonical(&data)?;
        if !under(&data, &home) && !under(&data, &private_data) {
            return Err("Native data must remain inside the application folder or its shared data folder".into());
        }
        let requested = value("--dimension").map(|s| s.parse::<usize>().map_err(err)).transpose()?
            .or_else(|| settings["index_dimension"].as_u64().map(|n| n as usize)).unwrap_or(DEFAULT_INDEX_DIMENSION);
        if !INDEX_DIMENSIONS.contains(&requested) { return Err("--dimension must be 128, 256, 512 or 768".into()); }
        let library_check = args.contains(&"--check-library".into());
        let prepare_workers=value("--prepare-workers").map(|s|s.parse::<usize>().map_err(err)).transpose()?
            .unwrap_or_else(||preferences::preparation_workers(&settings));
        if prepare_workers>image_pipeline::maximum_workers() {return Err(format!("--prepare-workers must be 0 (automatic) through {} (available CPU threads)",image_pipeline::maximum_workers()));}
        let dimension = if library_check { requested }
            else { store::Library::dimension_on_disk(&data)?.unwrap_or(requested) };
        Ok(Self {
            report: value("--report")
                .map(PathBuf::from)
                .unwrap_or_else(|| if library_check { home.join("library-check.json") }
                    else { home.join("verification/diagnostics.json") }),
            assets: value("--assets").map(PathBuf::from),
            backend,
            device: value("--device"),
            dimension,
            dimension_check: args.contains(&"--dimension-check".into()),
            dimension_benchmark: args.contains(&"--benchmark-dimensions".into()),
            library_check,
            prepare_workers,
            gui_smoke: args.contains(&"--gui-smoke".into()),
            smoke: args.contains(&"--smoke".into()) || args.contains(&"--diagnose".into()),
            math: args.contains(&"--verify-math".into()),
            home,
            data,
            private_data,
        })
    }
}
fn diagnostic(o: &Options) -> Res<serde_json::Value> {
    if o.library_check {
        let lib = store::Library::open_with_dimension(&o.data, o.dimension)?;
        return Ok(serde_json::json!({"version":env!("CARGO_PKG_VERSION"),"passed":true,
            "library":display_path(&o.data),"dimension":lib.dimension,"roots":lib.roots.len(),
            "files":lib.roots.iter().map(|r| r.info.files).sum::<usize>(),
            "vectors":lib.roots.iter().map(|r| r.records.len()).sum::<usize>(),
            "note":"Read and verified existing catalog, snapshots, and vectors; no model startup or reindexing."}));
    }
    let cancel = Arc::new(AtomicBool::new(false));
    let progress = |s: String| {
        let _ = atomic(&o.home.join("verification/progress.txt"), s.as_bytes());
    };
    let assets = runtime::assets(&o.home, o.assets.as_deref(), &cancel, &progress)?;
    let styles = style::Styles::load(&assets.join("style-models"))?;
    let math = styles.verify(&assets.join("math-reference.json"))?;
    if o.math {
        return Ok(serde_json::json!({"math":math}));
    }
    let run = runtime::Runtime::start(
        &assets,
        &o.home.join("data"),
        &o.backend,
        o.device.as_deref(),
        cancel.clone(),
        &progress,
    )?;
    if o.dimension_benchmark {return dimension_checks::benchmark(o, &run, &cancel, &progress);}
    if o.dimension_check {return dimension_checks::run(o, &assets, &run, &cancel, &progress);}
    let mut lib = store::Library::open_with_dimension(&o.data, o.dimension)?;
    lib.prepare_workers=o.prepare_workers;
    let kinds: HashSet<String> = ["image"].into_iter().map(String::from).collect();
    let root = assets.join("validation");
    let first = lib.index(
        &root,
        &kinds,
        &run.api,
        &cancel,
        &progress,
        &o.home.join("data"),
    )?;
    let second = lib.index(
        &root,
        &kinds,
        &run.api,
        &cancel,
        &progress,
        &o.home.join("data"),
    )?;
    let reloaded = store::Library::open(&o.data)?;
    let mut engine = search::Engine::new(&assets, &o.home.join("data"))?;
    let mut rankings = serde_json::Map::new();
    for method in ["similarity", "current", "contrastive"] {
        let q = search::Query {
            image: Some(root.join("b01-001.jpg")),
            aspect: if method == "similarity" {
                "similarity"
            } else {
                "style"
            }
            .into(),
            method: method.into(),
            ..Default::default()
        };
        rankings.insert(
            method.into(),
            serde_json::to_value(engine.search(&run.api, &reloaded, &q, &cancel, &progress)?)
                .map_err(err)?,
        );
    }
    let tq = search::Query {
        text: "a drawing of an animal".into(),
        ..Default::default()
    };
    let text = engine.search(&run.api, &reloaded, &tq, &cancel, &progress)?;
    let image_inputs = if std::env::args().any(|s| s == "--image-input-check") {
        diagnostics::image_transport(&o.home, &assets, &run.api, &mut engine, &reloaded, &progress)?
    } else { serde_json::Value::Null };
    let storage = if std::env::args().any(|s| s == "--storage-check") {
        diagnostics::storage(&o.home, &assets, &run.api, &progress)?
    } else {
        serde_json::Value::Null
    };
    let mut aspects = serde_json::Map::new();
    if std::env::args().any(|s| s == "--aspects") {
        for aspect in [
            "subject",
            "style",
            "pose",
            "viewpoint",
            "composition",
            "color",
            "lighting",
        ] {
            let q = search::Query {
                image: Some(root.join("b01-001.jpg")),
                aspect: aspect.into(),
                method: "baseline".into(),
                ..Default::default()
            };
            aspects.insert(
                aspect.into(),
                serde_json::to_value(engine.search(&run.api, &reloaded, &q, &cancel, &progress)?)
                    .map_err(err)?,
            );
        }
    }
    if second.embedded != 0 || second.reused != 3 || reloaded.all().len() != 3 {
        return Err("Rescan/reload verification did not preserve three cached images".into());
    }
    Ok(
        serde_json::json!({"backend":run.backend,"device":run.device,"probes":run.probes,"math":math,"first_index":first,"cached_rescan":second,"storage":storage,"image_inputs":image_inputs,"folders":reloaded.infos(),"rankings":rankings,"aspects":aspects,"text_query":text,"note":"Real inference and numerical compatibility checks; not a fresh retrieval quality benchmark."}),
    )
}
fn main() {
    let result = (|| -> Res<()> {
        let args = std::env::args().collect::<Vec<_>>();
        let cleanup = args.iter().any(|a| a == "--clean-index-snapshots");
        let preview = args.iter().any(|a| a == "--preview-index-cleanup");
        let update_check = args.iter().any(|a| a == "--update-check");
        let indexing_check = args.iter().any(|a| a == "--indexing-check");
        let preparation_check=args.iter().any(|a|a=="--preparation-check");
        let headless = preparation_check || indexing_check || update_check || cleanup || preview || args.iter().any(|a| ["--smoke", "--verify-math", "--diagnose", "--dimension-check", "--benchmark-dimensions", "--check-library"].contains(&a.as_str()));
        let opening = if headless { None } else { Some(opening::Opening::start()) };
        let o = Options::parse()?;
        if preparation_check {
            let cancel=AtomicBool::new(false);
            let assets=runtime::assets(&o.home,o.assets.as_deref(),&cancel,&|_|{})?;
            let result=preparation_checks::run(&o.home,&assets);
            save_json(&o.report,&result.as_ref().map_err(|e|e.as_str()))?;
            if result?["passed"]!=true {return Err("Preparation check failed; see report".into());}
            return Ok(());
        }
        if indexing_check {
            let result = indexing_checks::run(&o.home);
            save_json(&o.report, &result.as_ref().map_err(|e|e.as_str()))?;
            let report = result?;
            if report["passed"] != true { return Err("Indexing failure-injection check failed; see report".into()); }
            return Ok(());
        }
        if update_check {
            let count = args.iter().position(|a| a == "--update-files").and_then(|i| args.get(i + 1))
                .map(|s| s.parse::<usize>().map_err(err)).transpose()?.unwrap_or(6000);
            if !(2..=100_000).contains(&count) { return Err("--update-files must be 2 through 100000".into()); }
            let result = update_checks::run(&o.home, count, o.dimension);
            save_json(&o.report, &result.as_ref().map_err(|e| e.as_str()))?;
            return result.map(|_| ());
        }
        if o.library_check {
            let result = (|| {
                let _lock = platform::lock(&o.home.join("data/.instance.lock"))?;
                let _library_lock = platform::lock(&o.data.join(".library.lock"))?;
                diagnostic(&o)
            })();
            match &result {
                Ok(report) => save_json(&o.report, report)?,
                Err(error) => save_json(&o.report, &serde_json::json!({"version":env!("CARGO_PKG_VERSION"),
                    "passed":false,"library":display_path(&o.data),"error":error,
                    "recovery":store::inspect_saved_catalog(&o.data, true)
                        .unwrap_or_else(|e|serde_json::json!({"passed":false,"error":e,"activated":false}))}))?,
            }
            return result.map(|_| ());
        }
        let _lock = platform::lock(&o.home.join("data/.instance.lock"))?;
        let _library_lock = platform::lock(&o.data.join(".library.lock"))?;
        if cleanup || preview {
            let result = store::cleanup_snapshots(&o.data, preview);
            match &result {
                Ok(report) => save_json(&o.report, report)?,
                Err(e) => save_json(&o.report, &serde_json::json!({"error": e}))?,
            }
            return result.map(|_| ());
        }
        if o.smoke || o.math || o.dimension_check || o.dimension_benchmark {
            let r = diagnostic(&o);
            match &r {
                Ok(v) => save_json(&o.report, v)?,
                Err(e) => {
                    let _ = save_json(&o.report, &serde_json::json!({"error":e}));
                }
            }
            r?;
            return Ok(());
        }
        app::run(o, opening)
    })();
    if let Err(e) = result {
        let args = std::env::args().collect::<Vec<_>>();
        if !args.iter().any(|a| {
            ["--smoke", "--verify-math", "--diagnose", "--gui-smoke", "--dimension-check", "--benchmark-dimensions", "--clean-index-snapshots", "--preview-index-cleanup", "--check-library", "--update-check", "--indexing-check", "--preparation-check"].contains(&a.as_str())
        }) {
            platform::message(&e);
        }
        eprintln!("{e}");
        std::process::exit(1);
    }
}
