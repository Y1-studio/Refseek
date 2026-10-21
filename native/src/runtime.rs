use crate::{
    platform,
    rpc::{Api, media},
    style::npy,
    util::*,
};
use std::{
    fs::{self, File},
    io::Read,
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};
pub struct Runtime {
    pub api: Api,
    pub backend: String,
    pub device: String,
    pub probes: serde_json::Value,
    child: Child,
    _job: platform::OwnedJob,
}
fn verify_backend(assets: &Path, kind: &str) -> Res<()> {
    let manifest: serde_json::Value = read_json(&assets.join("assets-manifest.json"))?;
    let files = manifest["files"]
        .as_object()
        .ok_or("No pinned runtime hashes")?;
    let prefix = format!("runtimes/{kind}/");
    let mut count = 0;
    for (name, record) in files {
        if name.starts_with(&prefix) {
            count += 1;
            if file_hash(&assets.join(name))? != record["sha256"].as_str().unwrap_or("") {
                return Err(format!("Pinned runtime hash mismatch: {name}"));
            }
        }
    }
    if count == 0 {
        return Err("No pinned backend files".into());
    }
    Ok(())
}
impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
pub fn assets(
    home: &Path,
    explicit: Option<&Path>,
    cancel: &AtomicBool,
    progress: &dyn Fn(String),
) -> Res<PathBuf> {
    if let Some(p) = explicit {
        return canonical(p);
    }
    let local = home.join("assets");
    if local.join("models/embeddinggemma-2-Q8_0.gguf").exists() {
        return Ok(local);
    }
    let exe = std::env::current_exe().map_err(err)?;
    let mut z=zip::ZipArchive::new(File::open(&exe).map_err(err)?).map_err(|_|"Missing bundled assets; keep the assets folder beside this development executable or use the packaged EXE".to_string())?;
    let mut metadata = String::new();
    z.by_name("bundle-manifest.json")
        .map_err(err)?
        .read_to_string(&mut metadata)
        .map_err(err)?;
    let id = hash(metadata.as_bytes());
    let dest = home.join("data/runtime").join(&id[..16]);
    let marker = dest.join("ready.json");
    if marker.exists() {
        return Ok(dest.join("assets"));
    }
    let manifest: std::collections::BTreeMap<String, String> =
        serde_json::from_str(&metadata).map_err(err)?;
    let staging = home
        .join("data/runtime")
        .join(format!("extract-{}", stamp()));
    fs::create_dir_all(&staging).map_err(err)?;
    for (i, (name, expected)) in manifest.iter().enumerate() {
        if cancel.load(Ordering::Relaxed) {
            return Err("Startup cancelled".into());
        }
        progress(format!(
            "Unpacking bundled assets · {}/{}",
            i + 1,
            manifest.len()
        ));
        let mut entry = z.by_name(name).map_err(err)?;
        let safe = entry.enclosed_name().ok_or("Unsafe bundle path")?;
        if !safe.starts_with("assets") {
            return Err("Unexpected bundle path".into());
        }
        let target = staging.join(safe);
        fs::create_dir_all(target.parent().unwrap()).map_err(err)?;
        let mut file = File::create(&target).map_err(err)?;
        std::io::copy(&mut entry, &mut file).map_err(err)?;
        file.sync_all().map_err(err)?;
        drop(file);
        if file_hash(&target)? != *expected {
            return Err(format!("Bundle hash mismatch: {name}"));
        }
    }
    save_json(
        &staging.join("ready.json"),
        &serde_json::json!({"bundle":id}),
    )?;
    if dest.exists() {
        return Err(format!(
            "Incomplete extraction exists at {}; preserve it and rename that folder before restarting",
            dest.display()
        ));
    }
    fs::rename(&staging, &dest).map_err(err)?;
    Ok(dest.join("assets"))
}
fn output(exe: &Path, args: &[&str]) -> Res<String> {
    let mut c = Command::new(exe);
    platform::hidden(&mut c);
    let mut child = c
        .args(args)
        .current_dir(exe.parent().unwrap())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(err)?;
    let job = match platform::OwnedJob::attach(&child) {
        Ok(job) => job,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
    };
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let a = thread::spawn(move || {
        let mut s = String::new();
        let _ = std::io::BufReader::new(stdout)
            .take(1024 * 1024)
            .read_to_string(&mut s);
        s
    });
    let b = thread::spawn(move || {
        let mut s = String::new();
        let _ = std::io::BufReader::new(stderr)
            .take(1024 * 1024)
            .read_to_string(&mut s);
        s
    });
    let end = Instant::now() + Duration::from_secs(30);
    loop {
        if child.try_wait().map_err(err)?.is_some() {
            break;
        }
        if Instant::now() > end {
            let _ = child.kill();
            let _ = child.wait();
            break;
        }
        thread::sleep(Duration::from_millis(100));
    }
    drop(job);
    Ok(format!(
        "{}\n{}",
        a.join().unwrap_or_default(),
        b.join().unwrap_or_default()
    ))
}
impl Runtime {
    pub fn start(
        assets: &Path,
        data: &Path,
        backend: &str,
        device: Option<&str>,
        cancel: Arc<AtomicBool>,
        progress: &dyn Fn(String),
    ) -> Res<Self> {
        fs::create_dir_all(data.join("logs")).map_err(err)?;
        progress("Verifying the bundled Q8 model pair…".into());
        for (name, h) in [
            (
                "embeddinggemma-2-Q8_0.gguf",
                "6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc",
            ),
            (
                "mmproj-Q8_0.gguf",
                "90e7b0238009e2954f856f2081dcf7f35af026b64c765e98f4777053e1754460",
            ),
        ] {
            if file_hash(&assets.join("models").join(name))? != h {
                return Err(format!("Model SHA256 mismatch: {name}"));
            }
        }
        let mut candidates = vec![];
        for kind in ["cuda", "vulkan"] {
            if backend != "auto" && backend != kind {
                continue;
            }
            if cancel.load(Ordering::Relaxed) {
                return Err("Cancelled".into());
            }
            progress(format!("Verifying and detecting {kind} devices…"));
            if let Err(e) = verify_backend(assets, kind) {
                progress(format!("{kind} unavailable: {e}"));
                let _ = atomic(&data.join(format!("logs/devices-{kind}.txt")), e.as_bytes());
                continue;
            }
            let exe = assets.join(format!("runtimes/{kind}/llama-server.exe"));
            let raw = match output(&exe, &["--list-devices"]) {
                Ok(s) => s,
                Err(e) => {
                    progress(format!("{kind} detection unavailable: {e}"));
                    continue;
                }
            };
            atomic(
                &data.join(format!("logs/devices-{kind}.txt")),
                raw.as_bytes(),
            )?;
            for line in raw.lines() {
                let s = line.trim();
                if let Some((id, label)) = s.split_once(':') {
                    if (id.starts_with("CUDA") || id.starts_with("Vulkan"))
                        && id.chars().last().is_some_and(|c| c.is_ascii_digit())
                        && !['[', ']', ' '].iter().any(|c| id.contains(*c))
                    {
                        let low = label.to_lowercase();
                        if ["llvmpipe", "lavapipe", "swiftshader", "software renderer"]
                            .iter()
                            .any(|x| low.contains(x))
                        {
                            continue;
                        }
                        if device.is_none_or(|d| id == d) {
                            candidates.push((
                                kind.to_string(),
                                id.to_string(),
                                label.trim().to_string(),
                            ));
                        }
                    }
                }
            }
        }
        if backend == "auto" || backend == "cpu" {
            verify_backend(assets, "cpu")?;
            candidates.push(("cpu".into(), "none".into(), "CPU".into()));
        }
        if candidates.is_empty() {
            return Err(format!(
                "No usable {backend} device was detected. Choose Auto or CPU."
            ));
        }
        let mut errors = vec![];
        for (kind, id, label) in candidates {
            if cancel.load(Ordering::Relaxed) {
                return Err("Cancelled".into());
            }
            progress(format!("Starting {kind} · {label}…"));
            let port = TcpListener::bind("127.0.0.1:0")
                .map_err(err)?
                .local_addr()
                .map_err(err)?
                .port();
            let log = data.join(format!("logs/server-{}-{kind}-{}.log", stamp(), id));
            let result = (|| -> Res<Self> {
                let exe = assets.join(format!("runtimes/{kind}/llama-server.exe"));
                let mut c = Command::new(&exe);
                platform::hidden(&mut c);
                c.current_dir(exe.parent().unwrap())
                    .args(["-m"])
                    .arg(assets.join("models/embeddinggemma-2-Q8_0.gguf"))
                    .arg("--mmproj")
                    .arg(assets.join("models/mmproj-Q8_0.gguf"))
                    .args([
                        "--embeddings",
                        "--pooling",
                        "mean",
                        "--alias",
                        "embeddinggemma-2-q8",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        &port.to_string(),
                        "-np",
                        "2",
                        "-c",
                        "8192",
                        "-b",
                        "8192",
                        "-ub",
                        "8192",
                        "-fa",
                        "auto",
                        "--device",
                        &id,
                        "-ngl",
                        if kind == "cpu" { "0" } else { "all" },
                    ]);
                if kind == "cpu" {
                    c.arg("--no-mmproj-offload");
                } else {
                    c.args(["--mmproj-device", &id]);
                }
                let f = File::create(&log).map_err(err)?;
                c.stdout(f.try_clone().map_err(err)?).stderr(f);
                let mut child = c.spawn().map_err(err)?;
                let job = match platform::OwnedJob::attach(&child) {
                    Ok(j) => j,
                    Err(e) => {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(e);
                    }
                };
                let mut runtime = Self {
                    api: Api { port },
                    backend: kind.clone(),
                    device: label.clone(),
                    probes: serde_json::Value::Null,
                    child,
                    _job: job,
                };
                let end = Instant::now() + Duration::from_secs(180);
                loop {
                    if cancel.load(Ordering::Relaxed) {
                        return Err("Cancelled".into());
                    }
                    if runtime.child.try_wait().map_err(err)?.is_some() {
                        return Err(format!("Model process exited; see {}", log.display()));
                    }
                    if runtime.api.request("/health", None, 2).is_ok() {
                        break;
                    }
                    if Instant::now() > end {
                        return Err(format!("Model startup timed out; see {}", log.display()));
                    }
                    thread::sleep(Duration::from_millis(300));
                }
                progress(format!(
                    "Checking real text and image embeddings on {kind}…"
                ));
                let base = assets.join("style-models");
                let p: serde_json::Value = read_json(&base.join("model-probes.json"))?;
                let img = fs::read(base.join("probe-image.bin")).map_err(err)?;
                if hash(&img) != p["image_sha256"].as_str().unwrap_or("") {
                    return Err("Image probe hash mismatch".into());
                }
                let text = runtime.api.embed(vec![p["text"].clone()])?.remove(0);
                let image = runtime
                    .api
                    .embed(vec![media(
                        p["mime"].as_str().unwrap_or("image/jpeg"),
                        &img,
                    )])?
                    .remove(0);
                let t = npy(&fs::read(base.join("probe-text.npy")).map_err(err)?)?;
                let i = npy(&fs::read(base.join("probe-image.npy")).map_err(err)?)?;
                let tc = dot(&text, &unit(&t.values));
                let ic = dot(&image, &unit(&i.values));
                if tc < 0.9995 || ic < 0.9995 {
                    return Err(format!(
                        "Style compatibility failed: text {tc:.7}, image {ic:.7}"
                    ));
                }
                runtime.probes = serde_json::json!({"text_cosine":tc,"image_cosine":ic,"minimum":0.9995,"note":"Compatibility only; Q8/native preprocessing retrieval quality is not independently evaluated."});
                Ok(runtime)
            })();
            match result {
                Ok(r) => return Ok(r),
                Err(e) => {
                    progress(format!("{kind} failed; trying the next backend…"));
                    errors.push(format!("{kind}/{id}: {e}"));
                }
            }
        }
        Err(errors.join("\n"))
    }
}
