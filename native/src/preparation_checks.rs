//! Manual CPU preparation timing and transport-compatibility check; isolated files only.
use crate::{image_pipeline::Pool,rpc,util::*};
use std::{collections::HashSet,fs,path::Path,sync::atomic::{AtomicBool,Ordering},time::Instant};

// Previous path-based preparation, kept only as an independent compatibility reference.
fn reference(path:&Path)->Res<(String,Vec<u8>)>{
    let img=rpc::decoded(path)?;
    if img.width()<=1024 && img.height()<=1024 {
        let bytes=fs::read(path).map_err(err)?;let format=image::guess_format(&bytes).map_err(err)?;
        if matches!(format,image::ImageFormat::Jpeg|image::ImageFormat::Png){return Ok((format.to_mime_type().into(),bytes));}
        if format==image::ImageFormat::WebP {
            let mut png=std::io::Cursor::new(Vec::new());img.write_to(&mut png,image::ImageFormat::Png).map_err(err)?;
            return Ok(("image/png".into(),png.into_inner()));
        }
    }
    let small=img.resize(1024,1024,image::imageops::FilterType::Lanczos3).to_rgb8();let mut bytes=vec![];
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut bytes,85).encode_image(&small).map_err(err)?;
    Ok(("image/jpeg".into(),bytes))
}
pub fn run(home:&Path,assets:&Path)->Res<serde_json::Value>{
    let base=home.join("verification/preparation").join(stamp().to_string());fs::create_dir_all(&base).map_err(err)?;
    let mut paths=vec![];
    for n in 0..12u32 {
        let image=image::RgbImage::from_fn(2200+n%3*16,1600+n%2*16,|x,y|image::Rgb([
            ((x*13+y*7+n*31)%256) as u8,((y*11+x/3+n*17)%256) as u8,((x+y*3+n*23)%256) as u8]));
        let path=base.join(format!("full-frame-{n:02}.png"));image.save(&path).map_err(err)?;paths.push(path);
    }
    let mut unique=HashSet::new();for path in &paths {if !unique.insert(file_hash(path)?){return Err("Preparation inputs are not distinct".into());}}
    let mut expected=vec![];
    for path in &paths {let (mime,bytes)=reference(path)?;expected.push((mime,hash(&bytes)));}
    let mut transport=vec![];
    let mut check_paths=paths.iter().take(2).cloned().collect::<Vec<_>>();
    check_paths.push(assets.join("validation/b01-001.jpg"));
    let webp=base.join("small.webp");image::RgbaImage::from_fn(64,96,|x,y|image::Rgba([x as u8,y as u8,40,(x+y) as u8]))
        .save_with_format(&webp,image::ImageFormat::WebP).map_err(err)?;check_paths.push(webp);
    for path in &check_paths {
        let old=reference(path)?;let new=rpc::prepared(path)?;
        if old!=new {return Err(format!("Preparation changed image transport: {}",display_path(path)));}
        transport.push(serde_json::json!({"path":display_path(path),"identical_bytes":true}));
    }
    let compatibility=resize_compatibility()?;
    let known_hash=file_hash(&paths[0])?;
    let mut known=HashSet::new();known.insert(known_hash.clone());
    let never_cancel=AtomicBool::new(false);
    let mut cached_pool=Pool::new(1,1,known)?;cached_pool.submit(0,&paths[0],"image")?;
    let cached=cached_pool.take(0,&never_cancel)?.value?;
    if cached.hash!=known_hash || cached.image.is_some(){return Err("Cached preparation changed digest or decoded needlessly".into());}
    drop(cached_pool);
    let cancel=AtomicBool::new(false);let mut rounds=vec![];
    for workers in [1,4,4,1] {
        let start=Instant::now();let mut pool=Pool::new(workers,paths.len(),HashSet::new())?;
        let actual=pool.count;let window=pool.window;
        for (i,path) in paths.iter().take(window).enumerate(){pool.submit(i,path,"image")?;}
        let mut total_worker_seconds=0.;
        for i in 0..paths.len(){
            let outcome=pool.take(i,&cancel)?;total_worker_seconds+=outcome.seconds;
            let value=outcome.value?;let (mime,bytes)=value.image.ok_or("Prepared image missing")?;
            if (mime,hash(&bytes))!=expected[i] {return Err("Parallel image order or bytes changed".into());}
            if i+window<paths.len(){pool.submit(i+window,&paths[i+window],"image")?;}
        }
        drop(pool);
        rounds.push(serde_json::json!({"requested_workers":workers,"actual_workers":actual,"files":paths.len(),
            "seconds":start.elapsed().as_secs_f64(),"summed_worker_seconds":total_worker_seconds,"queue_window":window}));
    }
    let high_bit=base.join("16bit.png");
    image::ImageBuffer::<image::Rgb<u16>,Vec<u16>>::from_fn(1200,1030,|x,y|image::Rgb([(x*23) as u16,(y*31) as u16,(x*y) as u16]))
        .save(&high_bit).map_err(err)?;
    let small_bmp=base.join("small.bmp");image::RgbImage::from_fn(35,51,|x,y|image::Rgb([x as u8,y as u8,100]))
        .save(&small_bmp).map_err(err)?;
    for path in [&high_bit,&small_bmp] {
        if reference(path)?!=rpc::prepared(path)? {return Err("Fallback image transport changed".into());}
        transport.push(serde_json::json!({"path":display_path(path),"identical_bytes":true}));
    }
    let bad=base.join("broken.jpg");fs::write(&bad,b"invalid image bytes").map_err(err)?;
    let mut pool=Pool::new(4,2,HashSet::new())?;pool.submit(0,&bad,"image")?;pool.submit(1,&paths[0],"image")?;
    let isolated=pool.take(0,&cancel)?.value.is_err() && pool.take(1,&cancel)?.value.is_ok();drop(pool);
    let mut pool=Pool::new(4,paths.len(),HashSet::new())?;
    for (i,path) in paths.iter().take(pool.window).enumerate(){pool.submit(i,path,"image")?;}
    cancel.store(true,Ordering::Relaxed);let start=Instant::now();let cancelled=pool.take(0,&cancel).is_err();drop(pool);
    let one=rounds.iter().filter(|r|r["requested_workers"]==1).map(|r|r["seconds"].as_f64().unwrap()).sum::<f64>()/2.;
    let four=rounds.iter().filter(|r|r["requested_workers"]==4).map(|r|r["seconds"].as_f64().unwrap()).sum::<f64>()/2.;
    Ok(serde_json::json!({"passed":isolated && cancelled,"files":paths.len(),"rounds":rounds,
        "one_worker_mean_seconds":one,"four_worker_mean_seconds":four,"preparation_speedup":one/four,
        "resize_compatibility":compatibility,"cached_digest_identical":true,
        "transport":transport,"bad_file_isolated":isolated,"cancelled_and_joined":cancelled,
        "cancellation_join_seconds":start.elapsed().as_secs_f64(),"fixture":display_path(&base),
        "note":"Freshly hashes, decodes and resizes 12 distinct full-frame generated images each round. CPU preparation only; no GPU inference or retrieval-quality claim."}))
}

// Opt-in manual numerical diagnostic, including the portable scalar path.
fn resize_compatibility()->Res<serde_json::Value>{
    let mut cases=vec![];
    for (w,h) in [(1031,1049),(2048,1),(1,2048),(63,3001),(3001,64),(1024,2048),(2500,2000)] {
        let rgba=image::RgbaImage::from_fn(w,h,|x,y|{let n=x.wrapping_mul(747796405).wrapping_add(y.wrapping_mul(2891336453));
            image::Rgba([n as u8,(n>>8) as u8,(n>>16) as u8,(n>>24) as u8])});
        let rgba=image::DynamicImage::ImageRgba8(rgba);
        for img in [rgba.clone(),image::DynamicImage::ImageRgb8(rgba.to_rgb8()),
            image::DynamicImage::ImageLuma8(rgba.to_luma8()),image::DynamicImage::ImageLumaA8(rgba.to_luma_alpha8())] {
            let reference=img.resize(1024,1024,image::imageops::FilterType::Lanczos3).to_rgb8();
            for simd in [false,true] {
                let actual=crate::fast_resize::resize(&img,1024,simd).ok_or("Fast resize missing")?;
                if actual!=reference {return Err(format!("Resize pixels changed for {:?} {w}x{h} SIMD={simd}",img.color()));}
            }
            cases.push(serde_json::json!({"width":w,"height":h,"color":format!("{:?}",img.color()),"scalar_and_simd_identical":true}));
        }
    }
    Ok(serde_json::json!({"passed":true,"cases":cases}))
}
