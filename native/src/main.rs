#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod app;
mod diagnostics;
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
    pub backend: String,
    pub device: Option<String>,
    pub gui_smoke: bool,
    pub smoke: bool,
    pub math: bool,
    pub report: PathBuf,
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
        if !under(&data, &home) {
            return Err("Native data must remain inside the application folder".into());
        }
        Ok(Self {
            report: value("--report")
                .map(PathBuf::from)
                .unwrap_or_else(|| home.join("verification/diagnostics.json")),
            assets: value("--assets").map(PathBuf::from),
            backend,
            device: value("--device"),
            gui_smoke: args.contains(&"--gui-smoke".into()),
            smoke: args.contains(&"--smoke".into()) || args.contains(&"--diagnose".into()),
            math: args.contains(&"--verify-math".into()),
            home,
            data,
        })
    }
}
fn diagnostic(o: &Options) -> Res<serde_json::Value> {
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
    let mut lib = store::Library::open(&o.data)?;
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
        let headless = args.iter().any(|a| ["--smoke", "--verify-math", "--diagnose"].contains(&a.as_str()));
        let opening = if headless { None } else { Some(opening::Opening::start()) };
        let o = Options::parse()?;
        let _lock = platform::lock(&o.home.join("data/.instance.lock"))?;
        if o.smoke || o.math {
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
            ["--smoke", "--verify-math", "--diagnose", "--gui-smoke"].contains(&a.as_str())
        }) {
            platform::message(&e);
        }
        eprintln!("{e}");
        std::process::exit(1);
    }
}
