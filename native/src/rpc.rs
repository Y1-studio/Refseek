use crate::util::*;
use base64::Engine;
use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    net::TcpStream,
    path::Path,
    time::Duration,
};
#[derive(Clone)]
pub struct Api {
    pub port: u16,
}
impl Api {
    pub fn request(&self, path: &str, body: Option<&Value>, timeout: u64) -> Res<Value> {
        let mut stream = TcpStream::connect_timeout(
            &format!("127.0.0.1:{}", self.port).parse().map_err(err)?,
            Duration::from_secs(3),
        )
        .map_err(err)?;
        stream
            .set_read_timeout(Some(Duration::from_secs(timeout)))
            .map_err(err)?;
        stream
            .set_write_timeout(Some(Duration::from_secs(timeout)))
            .map_err(err)?;
        let payload = body.map(|v| v.to_string()).unwrap_or_default();
        write!(stream,"{} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",if body.is_some(){"POST"}else{"GET"},self.port,payload.len(),payload).map_err(err)?;
        let mut raw = vec![];
        stream
            .take(32 * 1024 * 1024)
            .read_to_end(&mut raw)
            .map_err(err)?;
        let split = raw
            .windows(4)
            .position(|b| b == b"\r\n\r\n")
            .ok_or("Malformed inference response")?;
        let header = String::from_utf8_lossy(&raw[..split]);
        let status = header.split_whitespace().nth(1).unwrap_or("");
        let mut data = raw[split + 4..].to_vec();
        if header.to_lowercase().contains("transfer-encoding: chunked") {
            let mut out = vec![];
            let mut at = 0;
            loop {
                let line = data[at..]
                    .windows(2)
                    .position(|x| x == b"\r\n")
                    .ok_or("Bad HTTP chunk")?
                    + at;
                let n = usize::from_str_radix(
                    std::str::from_utf8(&data[at..line])
                        .map_err(err)?
                        .split(';')
                        .next()
                        .unwrap(),
                    16,
                )
                .map_err(err)?;
                if n == 0 {
                    break;
                }
                at = line + 2;
                out.extend_from_slice(data.get(at..at + n).ok_or("Truncated HTTP chunk")?);
                at += n + 2;
            }
            data = out;
        }
        if status != "200" {
            return Err(format!(
                "Inference HTTP {status}: {}",
                String::from_utf8_lossy(&data)
                    .chars()
                    .take(600)
                    .collect::<String>()
            ));
        }
        serde_json::from_slice(&data).map_err(err)
    }
    pub fn embed(&self, input: Vec<Value>) -> Res<Vec<Vec<f64>>> {
        let n = input.len();
        let data=self.request("/v1/embeddings",Some(&json!({"model":"embeddinggemma-2-q8","input":input,"dimensions":768,"encoding_format":"float"})),900)?;
        let mut rows = data["data"].as_array().ok_or("Missing embeddings")?.clone();
        rows.sort_by_key(|r| r["index"].as_u64());
        let out = rows
            .iter()
            .map(|r| serde_json::from_value::<Vec<f64>>(r["embedding"].clone()).map_err(err))
            .collect::<Res<Vec<_>>>()?;
        if out.len() != n || out.iter().any(|v| !valid(v)) {
            return Err("Expected finite, nonzero 768-dimensional vectors".into());
        }
        Ok(out.iter().map(|v| unit(v)).collect())
    }
    pub fn text(&self, s: &str) -> Res<Vec<f64>> {
        Ok(self
            .embed(vec![json!(format!("task: search result | query: {s}"))])?
            .remove(0))
    }
    pub fn image(&self, p: &Path) -> Res<Vec<f64>> {
        let result = (|| {
            let (m, b) = prepared(p)?;
            Ok(self.embed(vec![media(&m, &b)])?.remove(0))
        })();
        result.map_err(|e: String| format!("Could not embed image {}: {e}", display_path(p)))
    }
}
pub fn media(mime: &str, b: &[u8]) -> Value {
    let url = format!(
        "data:{mime};base64,{}",
        base64::engine::general_purpose::STANDARD.encode(b)
    );
    if mime.starts_with("audio/") {
        json!({"content":[{"type":"input_audio","input_audio":{"url":url,"data":url}}]})
    } else if mime.starts_with("video/") {
        json!({"content":[{"type":"input_video","input_video":{"url":url,"data":url}}]})
    } else {
        json!({"content":[{"type":"image_url","image_url":{"url":url,"data":url}}]})
    }
}
pub fn decoded(p: &Path) -> Res<image::DynamicImage> {
    let mut r = image::ImageReader::open(p)
        .map_err(err)?
        .with_guessed_format()
        .map_err(err)?;
    let mut limits = image::Limits::default();
    limits.max_alloc = Some(256 * 1024 * 1024);
    limits.max_image_width = Some(30000);
    limits.max_image_height = Some(30000);
    r.limits(limits);
    r.decode().map_err(err)
}
pub fn prepared(p: &Path) -> Res<(String, Vec<u8>)> {
    if std::fs::metadata(p).map_err(err)?.len() > 64 * 1024 * 1024 {
        return Err("Image exceeds 64 MiB".into());
    }
    let img = decoded(p)?;
    if img.width() <= 1024 && img.height() <= 1024 {
        let b = std::fs::read(p).map_err(err)?;
        let fmt = image::guess_format(&b).map_err(err)?;
        if matches!(fmt, image::ImageFormat::Jpeg | image::ImageFormat::Png) {
            return Ok((fmt.to_mime_type().into(), b));
        }
        if fmt == image::ImageFormat::WebP {
            // b11741's standalone runtime cannot decode WebP without ffmpeg.
            // Lossless PNG preserves the decoded full frame, including alpha.
            // Inspect the contents: a .jpg file can actually contain WebP.
            let mut png = std::io::Cursor::new(Vec::new());
            img.write_to(&mut png, image::ImageFormat::Png).map_err(err)?;
            return Ok(("image/png".into(), png.into_inner()));
        }
    }
    let small = img
        .resize(1024, 1024, image::imageops::FilterType::Lanczos3)
        .to_rgb8();
    let mut b = vec![];
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut b, 85)
        .encode_image(&small)
        .map_err(err)?;
    Ok(("image/jpeg".into(), b))
}
