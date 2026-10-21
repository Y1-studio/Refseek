use crate::{
    rpc::{Api, media},
    util::*,
};
use std::{fs, io::Read, path::Path};
pub fn kind(p: &Path) -> Option<&'static str> {
    let e = p.extension()?.to_string_lossy().to_lowercase();
    if [
        "jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "ico", "ppm", "pgm",
    ]
    .contains(&e.as_str())
    {
        Some("image")
    } else if ["pdf", "docx", "pptx", "xlsx", "odt", "ods", "odp"].contains(&e.as_str()) {
        Some("document")
    } else if [
        "txt", "md", "csv", "json", "xml", "html", "htm", "rst", "log", "yaml", "yml", "toml",
        "py", "rs", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "java", "go", "sh", "sql", "css",
    ]
    .contains(&e.as_str())
    {
        Some("text")
    } else if ["wav", "mp3", "flac", "ogg", "m4a"].contains(&e.as_str()) {
        Some("audio")
    } else if ["mp4", "webm", "mkv", "mov", "avi"].contains(&e.as_str()) {
        Some("video")
    } else {
        None
    }
}
fn strip(s: &str) -> String {
    let mut out = String::new();
    let mut tag = false;
    for c in s.chars() {
        match c {
            '<' => {
                tag = true;
                out.push(' ');
            }
            '>' => tag = false,
            _ => {
                if !tag {
                    out.push(c)
                }
            }
        }
    }
    out.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
}
pub fn text(p: &Path) -> Res<String> {
    let e = p
        .extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_lowercase();
    if e == "pdf" {
        return pdf_extract::extract_text(p).map_err(err);
    }
    if kind(p) == Some("document") {
        let mut zip = zip::ZipArchive::new(fs::File::open(p).map_err(err)?).map_err(err)?;
        let mut parts = vec![];
        let mut total = 0u64;
        for i in 0..zip.len() {
            let mut f = zip.by_index(i).map_err(err)?;
            let name = f.name();
            if name.ends_with(".xml")
                && (name.starts_with("word/")
                    || name.starts_with("ppt/slides/")
                    || name.starts_with("xl/worksheets/")
                    || name == "xl/sharedStrings.xml"
                    || name == "content.xml")
            {
                total += f.size();
                if total > 20 * 1024 * 1024 {
                    return Err("Document text exceeds 20 MiB".into());
                }
                let mut s = String::new();
                f.read_to_string(&mut s).map_err(err)?;
                parts.push(strip(&s));
            }
        }
        return Ok(parts.join("\n"));
    }
    let b = fs::read(p).map_err(err)?;
    if b.len() > 20 * 1024 * 1024 {
        return Err("Text exceeds 20 MiB".into());
    }
    let s = if b.starts_with(&[0xff, 0xfe]) {
        String::from_utf16_lossy(
            &b[2..]
                .chunks_exact(2)
                .map(|v| u16::from_le_bytes([v[0], v[1]]))
                .collect::<Vec<_>>(),
        )
    } else {
        String::from_utf8_lossy(&b).into_owned()
    };
    Ok(if ["html", "htm", "xml"].contains(&e.as_str()) {
        strip(&s)
    } else {
        s
    })
}
pub fn chunks(s: &str) -> Vec<String> {
    let chars = s.chars().collect::<Vec<_>>();
    let mut out = vec![];
    let mut at = 0;
    while at < chars.len() && out.len() < 400 {
        let end = (at + 1600).min(chars.len());
        let v = chars[at..end].iter().collect::<String>();
        if !v.trim().is_empty() {
            out.push(v)
        }
        if end == chars.len() {
            break;
        }
        at = end - 200;
    }
    out
}
pub fn embed_file(
    api: &Api,
    p: &Path,
    k: &str,
    cancel: &std::sync::atomic::AtomicBool,
) -> Res<Vec<(String, Vec<f64>)>> {
    if k == "image" {
        return Ok(vec![(String::new(), api.image(p)?)]);
    }
    if k == "audio" || k == "video" {
        let b = fs::read(p).map_err(err)?;
        if b.len() > 64 * 1024 * 1024 {
            return Err("Media exceeds 64 MiB".into());
        }
        let ext = p.extension().unwrap_or_default().to_string_lossy();
        let mime = match ext.as_ref() {
            "mp3" => "audio/mpeg".into(),
            "m4a" => "audio/mp4".into(),
            "wav" => "audio/wav".into(),
            "mov" => "video/quicktime".into(),
            _ => format!("{k}/{ext}"),
        };
        return Ok(vec![(
            String::new(),
            api.embed(vec![media(&mime, &b)])?.remove(0),
        )]);
    }
    let s = text(p)?;
    let parts = chunks(&s);
    if parts.is_empty() {
        return Err("No readable text (scanned PDFs need OCR)".into());
    }
    let title = p.file_name().unwrap_or_default().to_string_lossy();
    let mut result = vec![];
    for batch in parts.chunks(4) {
        if cancel.load(std::sync::atomic::Ordering::Relaxed) {
            return Err("Indexing cancelled; previous file vectors retained".into());
        }
        let vectors = api.embed(
            batch
                .iter()
                .map(|s| serde_json::json!(format!("title: {title} | text: {s}")))
                .collect(),
        )?;
        for (s, v) in batch.iter().zip(vectors) {
            result.push((s.chars().take(500).collect(), v));
        }
    }
    Ok(result)
}
