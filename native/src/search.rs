use crate::{
    rpc::Api,
    store::{Library, Record},
    style::Styles,
    util::*,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};
#[derive(Clone, Serialize, Deserialize)]
pub struct Query {
    pub text: String,
    pub image: Option<PathBuf>,
    pub ignore_image: Option<PathBuf>,
    pub negative: String,
    pub aspect: String,
    pub method: String,
    pub strength: f64,
    pub image_weight: f64,
    pub negative_weight: f64,
    pub ignore_weight: f64,
    pub roots: Vec<PathBuf>,
    pub kinds: HashSet<String>,
    pub combine_max: bool,
}
impl Default for Query {
    fn default() -> Self {
        Self {
            text: String::new(),
            image: None,
            ignore_image: None,
            negative: String::new(),
            aspect: "similarity".into(),
            method: "contrastive".into(),
            strength: 1.,
            image_weight: 0.5,
            negative_weight: 0.5,
            ignore_weight: 1.,
            roots: vec![],
            kinds: ["image", "text", "document", "audio", "video"]
                .into_iter()
                .map(String::from)
                .collect(),
            combine_max: false,
        }
    }
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Hit {
    pub path: PathBuf,
    pub kind: String,
    pub snippet: String,
    pub score: f64,
    pub positive: f64,
    pub penalty: f64,
}
#[derive(Deserialize)]
struct Group {
    context_count: usize,
    prompts: Vec<String>,
}
pub struct Engine {
    pub styles: Styles,
    assets: PathBuf,
    data: PathBuf,
    bases: HashMap<String, Vec<Vec<f64>>>,
    text_cache: HashMap<String, Vec<f64>>,
    gallery: HashMap<String, (u128, Vec<Vec<f64>>)>,
    pub style_compatible: bool,
}
fn mean(v: &[Vec<f64>]) -> Vec<f64> {
    let mut out = vec![0.; 768];
    for x in v {
        for (i, y) in x.iter().enumerate() {
            out[i] += y / v.len() as f64;
        }
    }
    out
}
fn channel(v: &[f64], basis: &[Vec<f64>], residual: bool) -> Vec<f64> {
    let coords = basis.iter().map(|a| dot(v, a)).collect::<Vec<_>>();
    if residual {
        let mut r = v.to_vec();
        for (a, c) in basis.iter().zip(coords) {
            for (i, y) in a.iter().enumerate() {
                r[i] -= c * y;
            }
        }
        unit(&r)
    } else {
        unit(&coords)
    }
}
impl Engine {
    pub fn new(assets: &Path, data: &Path) -> Res<Self> {
        Ok(Self {
            styles: Styles::load(&assets.join("style-models"))?,
            assets: assets.to_owned(),
            data: data.to_owned(),
            bases: HashMap::new(),
            text_cache: HashMap::new(),
            gallery: HashMap::new(),
            style_compatible: true,
        })
    }
    fn text(&mut self, api: &Api, s: &str) -> Res<Vec<f64>> {
        if let Some(v) = self.text_cache.get(s) {
            return Ok(v.clone());
        }
        let v = api.text(s)?;
        if self.text_cache.len() > 10000 {
            self.text_cache.clear();
        }
        self.text_cache.insert(s.into(), v.clone());
        Ok(v)
    }
    fn basis(
        &mut self,
        api: &Api,
        name: &str,
        cancel: &AtomicBool,
        progress: &dyn Fn(String),
    ) -> Res<Vec<Vec<f64>>> {
        if let Some(v) = self.bases.get(name) {
            return Ok(v.clone());
        }
        let all: HashMap<String, Group> = read_json(&self.assets.join("aspects.json"))?;
        let g = all.get(name).ok_or("Unknown aspect")?;
        let fingerprint = hash(
            format!(
                "native-q8-focus-v1:{}",
                serde_json::to_string(&g.prompts).map_err(err)?
            )
            .as_bytes(),
        );
        let cache = self
            .data
            .join("calibration")
            .join(format!("{name}-{fingerprint}.json"));
        if let Ok(b) = read_json::<Vec<Vec<f64>>>(&cache) {
            if !b.is_empty() && b.iter().all(|v| valid(v) && (dot(v, v) - 1.).abs() < 1e-6) {
                self.bases.insert(name.into(), b.clone());
                return Ok(b);
            }
        }
        let mut embedded = vec![];
        for (i, batch) in g.prompts.chunks(4).enumerate() {
            if cancel.load(Ordering::Relaxed) {
                return Err("Calibration cancelled".into());
            }
            progress(format!(
                "Calibrating {name} · {}/{} descriptions",
                (i * 4).min(g.prompts.len()),
                g.prompts.len()
            ));
            embedded.extend(api.embed(batch.iter().map(|s| serde_json::json!(s)).collect())?);
        }
        let means = embedded
            .chunks(g.context_count)
            .map(mean)
            .collect::<Vec<_>>();
        let mut axes: Vec<Vec<f64>> = vec![];
        for m in means.iter().skip(1) {
            let mut a = m
                .iter()
                .zip(&means[0])
                .map(|(x, y)| x - y)
                .collect::<Vec<_>>();
            for _ in 0..2 {
                for b in &axes {
                    let c = dot(&a, b);
                    for (i, y) in b.iter().enumerate() {
                        a[i] -= c * y;
                    }
                }
            }
            if dot(&a, &a) > 1e-10 {
                axes.push(unit(&a));
            }
        }
        if axes.is_empty() {
            return Err("Could not calibrate aspect".into());
        }
        save_json(&cache, &axes)?;
        self.bases.insert(name.into(), axes.clone());
        Ok(axes)
    }
    fn image(&self, api: &Api, p: &Path, records: &[Record]) -> Res<(Vec<f64>, String)> {
        let h = file_hash(p)?;
        if let Some(r) = records.iter().find(|r| r.kind == "image" && r.hash == h) {
            return Ok((unit(&r.vector), h));
        }
        Ok((api.image(p)?, h))
    }
    pub fn search(
        &mut self,
        api: &Api,
        lib: &Library,
        q: &Query,
        cancel: &AtomicBool,
        progress: &dyn Fn(String),
    ) -> Res<Vec<Hit>> {
        let records = lib.all();
        if records.is_empty() {
            return Err("The native library is empty. Add a folder in Library & indexing.".into());
        }
        let wants = q
            .text
            .lines()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .take(20)
            .collect::<Vec<_>>();
        if wants.is_empty() && q.image.is_none() {
            return Err("Enter text or choose a reference image".into());
        }
        let mut texts = vec![];
        for s in wants {
            if cancel.load(Ordering::Relaxed) {
                return Err("Search cancelled".into());
            }
            texts.push(self.text(api, s)?);
        }
        let text = if texts.is_empty() {
            None
        } else {
            Some(unit(&mean(&texts)))
        };
        let (image, image_hash) = if let Some(p) = &q.image {
            let (v, h) = self.image(api, p, &records)?;
            (Some(v), Some(h))
        } else {
            (None, None)
        };
        let ignore = if let Some(p) = &q.ignore_image {
            Some(self.image(api, p, &records)?.0)
        } else {
            None
        };
        let mut negatives = vec![];
        for s in q
            .negative
            .lines()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .take(20)
        {
            negatives.push(self.text(api, s)?);
        }
        let negative = if negatives.is_empty() {
            None
        } else {
            Some(mean(&negatives))
        };
        let alpha = if image.is_none() {
            0.
        } else if text.is_none() {
            1.
        } else {
            q.image_weight
        };
        let mut blend = vec![0.; 768];
        if let Some(v) = &image {
            for (i, x) in v.iter().enumerate() {
                blend[i] += alpha * x;
            }
        }
        if let Some(v) = &text {
            for (i, x) in v.iter().enumerate() {
                blend[i] += (1. - alpha) * x;
            }
        }
        let norm = dot(&blend, &blend).sqrt().max(1e-12);
        let focused = image.is_some() && q.aspect != "similarity" && q.strength > 0.;
        let trained = focused && q.aspect == "style" && q.method != "baseline";
        if trained && !self.style_compatible {
            return Err(
                "Trained styles are unavailable because the live model probes did not pass".into(),
            );
        }
        let basis = if focused && !trained {
            Some(self.basis(
                api,
                if q.aspect == "subject" {
                    "style"
                } else {
                    &q.aspect
                },
                cancel,
                progress,
            )?)
        } else {
            None
        };
        let tag = if trained {
            format!("style:{}", q.method)
        } else if focused {
            format!("focus:{}", q.aspect)
        } else {
            String::new()
        };
        let feature_query = if trained {
            Some(self.styles.get(&q.method).project(image.as_ref().unwrap()))
        } else if let Some(b) = &basis {
            Some(channel(image.as_ref().unwrap(), b, q.aspect == "subject"))
        } else {
            None
        };
        if focused
            && self
                .gallery
                .get(&tag)
                .is_none_or(|(g, _)| *g != lib.generation)
        {
            progress(format!("Preparing {} library features…", q.aspect));
            let features = if trained {
                self.styles
                    .get(&q.method)
                    .project_batch(&records.iter().map(|r| r.vector.clone()).collect::<Vec<_>>())
            } else {
                records
                    .iter()
                    .map(|r| channel(&r.vector, basis.as_ref().unwrap(), q.aspect == "subject"))
                    .collect()
            };
            if self.gallery.len() > 3 {
                self.gallery.clear();
            }
            self.gallery.insert(tag.clone(), (lib.generation, features));
        }
        let features = self.gallery.get(&tag).map(|(_, v)| v);
        let mut hits = vec![];
        for (i, r) in records.iter().enumerate() {
            if i % 500 == 0 && cancel.load(Ordering::Relaxed) {
                return Err("Search cancelled".into());
            }
            if !q.kinds.contains(&r.kind)
                || (!q.roots.is_empty() && !q.roots.iter().any(|root| under(&r.path, root)))
            {
                continue;
            }
            if image.is_some() && q.aspect != "similarity" && r.kind != "image" {
                continue;
            }
            if image_hash.as_ref().is_some_and(|h| *h == r.hash) {
                continue;
            }
            let ic = image.as_ref().map(|v| dot(v, &r.vector)).unwrap_or(0.);
            let tc = text.as_ref().map(|v| dot(v, &r.vector)).unwrap_or(0.);
            let ordinary = if image.is_none() && q.combine_max {
                texts.iter().map(|v| dot(v, &r.vector)).fold(-1., f64::max)
            } else {
                (alpha * ic + (1. - alpha) * tc) / norm
            };
            let positive = if focused {
                let mut aspect = dot(feature_query.as_ref().unwrap(), &features.unwrap()[i]);
                if trained {
                    let w = self.styles.get(&q.method).weight;
                    aspect = (1. - w) * ic + w * aspect;
                }
                let target = alpha * aspect + (1. - alpha) * tc;
                (1. - q.strength) * ordinary + q.strength * target
            } else {
                ordinary
            };
            let penalty = negative
                .as_ref()
                .map(|v| q.negative_weight * dot(v, &r.vector))
                .unwrap_or(0.)
                + ignore
                    .as_ref()
                    .map(|v| q.ignore_weight * dot(v, &r.vector))
                    .unwrap_or(0.);
            hits.push((
                r.hash.clone(),
                Hit {
                    path: r.path.clone(),
                    kind: r.kind.clone(),
                    snippet: r.snippet.clone(),
                    score: positive - penalty,
                    positive,
                    penalty,
                },
            ));
        }
        hits.sort_by(|a, b| b.1.score.total_cmp(&a.1.score));
        let mut paths = HashSet::new();
        let mut hashes = HashSet::new();
        Ok(hits
            .into_iter()
            .filter(|(h, r)| paths.insert(key(&r.path)) && hashes.insert(h.clone()))
            .map(|(_, r)| r)
            .collect())
    }
}
