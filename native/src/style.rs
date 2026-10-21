use crate::util::*;
use std::{collections::HashMap, fs::File, io::Read, path::Path};
pub struct Array {
    pub shape: Vec<usize>,
    pub values: Vec<f64>,
    pub text: Option<String>,
}
pub fn npy(b: &[u8]) -> Res<Array> {
    if b.len() < 12 || &b[..6] != b"\x93NUMPY" {
        return Err("Invalid NumPy file".into());
    }
    let (start, len) = if b[6] == 1 {
        (
            10,
            u16::from_le_bytes(b[8..10].try_into().unwrap()) as usize,
        )
    } else {
        (
            12,
            u32::from_le_bytes(b[8..12].try_into().unwrap()) as usize,
        )
    };
    let h = std::str::from_utf8(b.get(start..start + len).ok_or("Invalid NumPy header")?)
        .map_err(err)?;
    let fortran = h.contains("True");
    let sh = h
        .split("'shape':")
        .nth(1)
        .ok_or("No array shape")?
        .split('(')
        .nth(1)
        .ok_or("No shape tuple")?
        .split(')')
        .next()
        .unwrap();
    let shape = sh
        .split(',')
        .filter_map(|x| x.trim().parse::<usize>().ok())
        .collect::<Vec<_>>();
    let n = shape.iter().product::<usize>();
    let body = &b[start + len..];
    let mut a = Array {
        shape,
        values: vec![],
        text: None,
    };
    if h.contains("<f8") {
        if body.len() != n * 8 {
            return Err("Invalid f64 array length".into());
        }
        a.values = body
            .chunks_exact(8)
            .map(|x| f64::from_le_bytes(x.try_into().unwrap()))
            .collect();
    } else if h.contains("<f4") {
        if body.len() != n * 4 {
            return Err("Invalid f32 array length".into());
        }
        a.values = body
            .chunks_exact(4)
            .map(|x| f32::from_le_bytes(x.try_into().unwrap()) as f64)
            .collect();
    } else if h.contains("<U") {
        a.text = Some(
            body.chunks_exact(4)
                .filter_map(|x| char::from_u32(u32::from_le_bytes(x.try_into().unwrap())))
                .filter(|c| *c != '\0')
                .collect(),
        );
    } else {
        return Err(format!("Unsupported array format {h}"));
    }
    if fortran && a.shape.len() == 2 {
        let rows = a.shape[0];
        let cols = a.shape[1];
        let old = a.values.clone();
        for i in 0..rows {
            for j in 0..cols {
                a.values[i * cols + j] = old[j * rows + i];
            }
        }
    }
    if a.values.iter().any(|x| !x.is_finite()) {
        return Err("Nonfinite adapter".into());
    }
    Ok(a)
}
fn arrays(p: &Path) -> Res<HashMap<String, Array>> {
    let mut z = zip::ZipArchive::new(File::open(p).map_err(err)?).map_err(err)?;
    let mut out = HashMap::new();
    for i in 0..z.len() {
        let mut f = z.by_index(i).map_err(err)?;
        if f.size() > 64 * 1024 * 1024 {
            return Err("Adapter entry too large".into());
        }
        let name = f.name().trim_end_matches(".npy").to_owned();
        let mut b = vec![];
        f.read_to_end(&mut b).map_err(err)?;
        out.insert(name, npy(&b)?);
    }
    Ok(out)
}
fn take(a: &mut HashMap<String, Array>, k: &str, shape: &[usize]) -> Res<Vec<f64>> {
    let x = a.remove(k).ok_or(format!("Adapter missing {k}"))?;
    if x.shape != shape {
        return Err(format!("Wrong {k} shape {:?}", x.shape));
    }
    Ok(x.values)
}
fn mul(a: &[f64], m: usize, k: usize, b: &[f64], n: usize) -> Vec<f64> {
    let mut c = vec![0.; m * n];
    unsafe {
        matrixmultiply::dgemm(
            m,
            k,
            n,
            1.,
            a.as_ptr(),
            k as isize,
            1,
            b.as_ptr(),
            n as isize,
            1,
            0.,
            c.as_mut_ptr(),
            n as isize,
            1,
        );
    }
    c
}
fn transform(
    axes: &[f64],
    rows: usize,
    scale: Option<&[f64]>,
    correction: Option<&[f64]>,
) -> Vec<f64> {
    let mut left = vec![0.; 768 * rows];
    for i in 0..768 {
        for r in 0..rows {
            left[i * rows + r] = axes[r * 768 + i] * scale.map(|s| s[r]).unwrap_or(1.);
        }
    }
    let mut w = mul(&left, 768, rows, correction.unwrap_or(axes), 768);
    for i in 0..768 {
        w[i * 768 + i] += 1.;
    }
    w
}
pub struct Metric {
    mean: Vec<f64>,
    w: Vec<f64>,
    warm: Option<(Vec<f64>, Vec<f64>)>,
    pub weight: f64,
}
impl Metric {
    pub fn load(p: &Path) -> Res<Self> {
        let mut a = arrays(p)?;
        let c = a
            .remove("config")
            .and_then(|v| v.text)
            .ok_or("Adapter has no config")?;
        let conf: serde_json::Value = serde_json::from_str(&c).map_err(err)?;
        let weight = conf["weight"].as_f64().ok_or("Missing style blend")?;
        if !(0.0..=1.0).contains(&weight) {
            return Err("Invalid style blend".into());
        }
        let mean = take(&mut a, "mean", &[768])?;
        let rows = *a
            .get("axes")
            .ok_or("No axes")?
            .shape
            .first()
            .ok_or("No axis shape")?;
        let axes = take(&mut a, "axes", &[rows, 768])?;
        let (w, warm) = if a.contains_key("warm_mean") {
            let wm = take(&mut a, "warm_mean", &[768])?;
            let n = a.get("warm_axes").ok_or("No warm axes")?.shape[0];
            let wa = take(&mut a, "warm_axes", &[n, 768])?;
            let ws = take(&mut a, "warm_scales", &[n])?;
            let correction = take(&mut a, "correction", &[rows, 768])?;
            (
                transform(&axes, rows, None, Some(&correction)),
                Some((wm, transform(&wa, n, Some(&ws), None))),
            )
        } else {
            let scales = take(&mut a, "scales", &[rows])?;
            (transform(&axes, rows, Some(&scales), None), None)
        };
        Ok(Self {
            mean,
            w,
            warm,
            weight,
        })
    }
    pub fn project(&self, x: &[f64]) -> Vec<f64> {
        self.project_batch(&[x.to_vec()]).remove(0)
    }
    pub fn project_batch(&self, x: &[Vec<f64>]) -> Vec<Vec<f64>> {
        if x.is_empty() {
            return vec![];
        }
        let mut a = x.iter().flat_map(|v| unit(v)).collect::<Vec<_>>();
        if let Some((mean, w)) = &self.warm {
            for row in a.chunks_mut(768) {
                for (i, v) in row.iter_mut().enumerate() {
                    *v -= mean[i];
                }
            }
            a = mul(&a, x.len(), 768, w, 768);
            for row in a.chunks_mut(768) {
                row.copy_from_slice(&unit(row));
            }
        }
        for row in a.chunks_mut(768) {
            for (i, v) in row.iter_mut().enumerate() {
                *v -= self.mean[i];
            }
        }
        mul(&a, x.len(), 768, &self.w, 768)
            .chunks(768)
            .map(unit)
            .collect()
    }
    pub fn score(&self, x: &[f64], y: &[f64]) -> f64 {
        (1. - self.weight) * dot(&unit(x), &unit(y))
            + self.weight * dot(&self.project(x), &self.project(y))
    }
}
pub struct Styles {
    pub current: Metric,
    pub contrastive: Metric,
}
impl Styles {
    pub fn load(p: &Path) -> Res<Self> {
        let freeze: HashMap<String, String> = read_json(&p.join("deployment-freeze.json"))?;
        for (name, h) in freeze {
            if file_hash(&p.join(name))? != h {
                return Err("Frozen style bundle failed SHA256 validation".into());
            }
        }
        Ok(Self {
            current: Metric::load(&p.join("artist-style-adapter.npz"))?,
            contrastive: Metric::load(&p.join("experimental-style-adapter.npz"))?,
        })
    }
    pub fn get(&self, name: &str) -> &Metric {
        if name == "current" {
            &self.current
        } else {
            &self.contrastive
        }
    }
    pub fn verify(&self, p: &Path) -> Res<serde_json::Value> {
        let v: serde_json::Value = read_json(p)?;
        let x: Vec<Vec<f64>> = serde_json::from_value(v["vectors"].clone()).map_err(err)?;
        let mut out = serde_json::Map::new();
        for name in ["current", "contrastive"] {
            let expected: Vec<Vec<f64>> =
                serde_json::from_value(v["scores"][name].clone()).map_err(err)?;
            let mut max: f64 = 0.;
            for i in 0..x.len() {
                for j in 0..x.len() {
                    max = max.max((self.get(name).score(&x[i], &x[j]) - expected[i][j]).abs());
                }
            }
            if max > 2e-8 {
                return Err(format!("{name} numerical mismatch {max}"));
            }
            out.insert(
                name.into(),
                serde_json::json!({"maximum_score_error":max,"passed":true}),
            );
        }
        Ok(out.into())
    }
}
