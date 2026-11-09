use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
pub type Res<T> = Result<T, String>;
pub fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
pub fn read_json<T: serde::de::DeserializeOwned>(p: &Path) -> Res<T> {
    let bytes = fs::read(p).map_err(|e| format!("Cannot read JSON file {}: {e}", display_path(p)))?;
    parse_json(p, &bytes)
}
pub fn parse_json<T: serde::de::DeserializeOwned>(p: &Path, bytes: &[u8]) -> Res<T> {
    serde_json::from_slice(bytes).map_err(|e| {
        let detail = std::str::from_utf8(bytes).err().map(|u| {
            let at = u.valid_up_to();
            let hex = bytes[at..(at + 8).min(bytes.len())].iter().map(|b|format!("{b:02x}")).collect::<Vec<_>>().join(" ");
            format!("; invalid UTF-8 at byte offset {at} (0-based), next bytes: {hex}")
        }).unwrap_or_default();
        format!("Cannot parse JSON file {}: {e}{detail}", display_path(p))
    })
}
pub fn hash(b: &[u8]) -> String {
    format!("{:x}", Sha256::digest(b))
}
pub fn file_hash(p: &Path) -> Res<String> {
    file_hash_inner(p,None)
}
pub fn file_hash_cancellable(p:&Path,cancel:&std::sync::atomic::AtomicBool)->Res<String>{
    file_hash_inner(p,Some(cancel))
}
fn file_hash_inner(p:&Path,cancel:Option<&std::sync::atomic::AtomicBool>)->Res<String>{
    let mut f = File::open(p).map_err(err)?;
    let mut h = Sha256::new();
    let mut b = [0u8; 131072];
    loop {
        if cancel.is_some_and(|c|c.load(std::sync::atomic::Ordering::Relaxed)) {return Err("File hashing cancelled".into());}
        let n = f.read(&mut b).map_err(err)?;
        if n == 0 {
            break;
        }
        h.update(&b[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}
pub fn stamp() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}
pub fn key(p: &Path) -> String {
    p.to_string_lossy()
        .trim_end_matches(['\\', '/'])
        .replace('/', "\\")
        .to_lowercase()
}
/// Friendly Windows presentation only. Canonical paths and index keys stay intact.
pub fn display_path(p: &Path) -> String {
    let text = p.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\") {
        if rest.get(..4).is_some_and(|s| s.eq_ignore_ascii_case(r"UNC\")) {
            return format!(r"\\{}", &rest[4..]);
        }
        let b = rest.as_bytes();
        if b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && b[2] == b'\\' {
            return rest.into();
        }
    }
    text.into_owned()
}
pub fn under(p: &Path, root: &Path) -> bool {
    let a = key(p);
    let b = key(root);
    a == b || a.starts_with(&(b + "\\"))
}
pub fn atomic(p: &Path, b: &[u8]) -> Res<()> {
    fs::create_dir_all(p.parent().ok_or("Missing parent")?).map_err(err)?;
    let t = p.with_extension(format!("tmp-{}", stamp()));
    let mut f = fs::OpenOptions::new().write(true).create_new(true).open(&t).map_err(err)?;
    let result = (|| {
        f.write_all(b).map_err(err)?;
        f.sync_all().map_err(err)?;
        drop(f);
        crate::platform::replace(&t, p)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&t);
    }
    result
}
pub fn save_json(p: &Path, v: &impl serde::Serialize) -> Res<()> {
    atomic(p, &serde_json::to_vec_pretty(v).map_err(err)?)
}
pub fn unit(x: &[f64]) -> Vec<f64> {
    let n = x.iter().map(|v| v * v).sum::<f64>().sqrt().max(1e-12);
    x.iter().map(|v| v / n).collect()
}
pub fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}
pub fn valid(x: &[f64]) -> bool {
    x.len() == 768 && x.iter().all(|v| v.is_finite()) && dot(x, x) > 1e-12
}
pub const INDEX_DIMENSIONS: [usize; 4] = [128, 256, 512, 768];
pub const DEFAULT_INDEX_DIMENSION: usize = 768;
pub fn vector_storage(rows: usize, dimension: usize) -> (u64, u64) {
    let bytes_per_dimension = (rows as u64).saturating_mul(4);
    let cache_dimensions = if dimension < 768 { 768 } else { 0 };
    (
        bytes_per_dimension.saturating_mul(dimension as u64),
        bytes_per_dimension.saturating_mul(cache_dimensions),
    )
}

pub fn storage_size(bytes: u64) -> String {
    match bytes {
        1_000_000_000.. => format!("{:.2} GB", bytes as f64 / 1_000_000_000.),
        1_000_000.. => format!("{:.2} MB", bytes as f64 / 1_000_000.),
        1_000.. => format!("{:.2} KB", bytes as f64 / 1_000.),
        _ => format!("{bytes} B"),
    }
}
pub fn native_dimension() -> usize { 768 }
pub fn mrl(x: &[f64], dimension: usize) -> Vec<f64> {
    unit(&x[..dimension])
}
pub fn canonical(p: &Path) -> Res<PathBuf> {
    fs::canonicalize(p).map_err(err)
}
