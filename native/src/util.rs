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
    serde_json::from_slice(&fs::read(p).map_err(err)?).map_err(err)
}
pub fn hash(b: &[u8]) -> String {
    format!("{:x}", Sha256::digest(b))
}
pub fn file_hash(p: &Path) -> Res<String> {
    let mut f = File::open(p).map_err(err)?;
    let mut h = Sha256::new();
    let mut b = [0u8; 131072];
    loop {
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
    let mut f = File::create(&t).map_err(err)?;
    f.write_all(b).map_err(err)?;
    f.sync_all().map_err(err)?;
    drop(f);
    crate::platform::replace(&t, p)
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
pub fn canonical(p: &Path) -> Res<PathBuf> {
    fs::canonicalize(p).map_err(err)
}
