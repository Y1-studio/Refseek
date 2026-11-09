//! Opt-in manual failure injection. Every file lives in a generated diagnostic folder.
use crate::{rpc::Api, store::{Library, Record, Root, RootInfo}, util::*};
use std::{fs, io::{Read, Write}, net::TcpListener, os::windows::fs::OpenOptionsExt,
    path::Path, sync::{Arc, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};

fn seed(lib: &mut Library, source: &Path, files: &[(&str, &[u8])]) -> Res<()> {
    let mut records = vec![];
    for (name, content) in files {
        let path = source.join(name); fs::write(&path, content).map_err(err)?;
        let m = fs::metadata(&path).map_err(err)?;
        let mut v = vec![0.;768]; v[0] = 1.;
        records.push(Record {path, hash:hash(content), size:m.len(),
            modified:m.modified().map_err(err)?.duration_since(std::time::UNIX_EPOCH).map_err(err)?.as_nanos(),
            kind:"image".into(), snippet:String::new(), chunk:0, vector:v.into(), search_vector:Default::default()});
    }
    lib.roots = vec![Root {info:RootInfo {path:source.into(), ..Default::default()}, records}]; lib.commit()
}
fn setup(base: &Path, files: &[(&str, &[u8])]) -> Res<(Library, std::path::PathBuf)> {
    let source = base.join("source"); fs::create_dir_all(&source).map_err(err)?;
    let source = canonical(&source)?; let mut lib = Library::open(&base.join("library"))?;
    seed(&mut lib, &source, files)?; Ok((lib, source))
}
fn recovery(base: &Path) -> Res<serde_json::Value> {
    let (mut lib, _) = setup(base, &[("a.jpg",b"saved source")])?;
    let first_snapshot = lib.roots[0].info.snapshot.clone();
    lib.roots[0].records[0].snippet = "second saved state".into(); lib.commit()?;
    let active = lib.dir.join("catalog.json"); let previous = lib.dir.join("catalog.previous.json");
    let before_active = fs::read(&active).map_err(err)?; let before_previous = fs::read(&previous).map_err(err)?;
    // Allow reads and writes but deny deletion/replacement, like a held reader.
    let locked = fs::OpenOptions::new().read(true).share_mode(3).open(&active).map_err(err)?;
    lib.roots[0].records[0].snippet = "third unsaved state".into(); let failure = lib.commit().err(); drop(locked);
    let after_active = fs::read(&active).map_err(err)?; let after_previous = fs::read(&previous).map_err(err)?;
    Ok(serde_json::json!({"passed":failure.is_some() && before_active==after_active && before_previous==after_previous && after_previous!=after_active,
        "error":failure,"active_preserved":before_active==after_active,"distinct_recovery_overwritten":before_previous!=after_previous,
        "recovery_equals_active":after_previous==after_active,"old_snapshot_still_present":lib.dir.join(first_snapshot).is_dir()}))
}
fn cancellation(base: &Path) -> Res<serde_json::Value> {
    let (mut lib, source) = setup(base, &[("changed.png",b"old image"),("missing.png",b"old missing image")])?;
    fs::remove_file(source.join("missing.png")).map_err(err)?;
    image::RgbImage::from_pixel(2,2,image::Rgb([20,40,60])).save(source.join("changed.png")).map_err(err)?;
    let listener = TcpListener::bind("127.0.0.1:0").map_err(err)?; let port = listener.local_addr().map_err(err)?.port();
    let cancel = Arc::new(AtomicBool::new(false)); let worker_cancel = cancel.clone();
    let replied = Arc::new(AtomicBool::new(false)); let server_replied = replied.clone();
    let worker = std::thread::spawn(move || -> Res<()> {
        let (mut stream, _) = listener.accept().map_err(err)?; stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(err)?;
        let mut input = vec![]; let mut buffer = [0;4096];
        loop {
            let n = stream.read(&mut buffer).map_err(err)?; if n==0 {return Err("Closed request".into());} input.extend_from_slice(&buffer[..n]);
            if let Some(end) = input.windows(4).position(|w|w==b"\r\n\r\n") {
                let header = String::from_utf8_lossy(&input[..end]);
                let len = header.lines().find_map(|s|s.to_lowercase().strip_prefix("content-length:").map(|v|v.trim().parse::<usize>())).transpose().map_err(err)?.unwrap_or(0);
                if input.len()>=end+4+len {break;}
            }
        }
        std::thread::sleep(Duration::from_millis(300));
        worker_cancel.store(true,Ordering::Relaxed); std::thread::sleep(Duration::from_secs(2));
        server_replied.store(true, Ordering::Relaxed);
        let mut v=vec![0.;768]; v[1]=1.; let body=serde_json::json!({"data":[{"index":0,"embedding":v}]}).to_string();
        let _ = write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),body); Ok(())
    });
    let start = Instant::now();
    let stats=lib.index(&source,&["image".into()].into_iter().collect(),&Api{port},&cancel,&|_|{},&base.join("excluded"))?;
    let elapsed = start.elapsed().as_secs_f64(); let returned_before_reply = !replied.load(Ordering::Relaxed);
    worker.join().map_err(|_|"Inference stub failed")??;
    let reload=Library::open(&lib.dir)?; let retained=reload.all().iter().any(|r|r.path.ends_with("missing.png"));
    Ok(serde_json::json!({"passed":stats.cancelled && retained && returned_before_reply && stats.failed==0 && stats.removed==0,
        "returned_before_inference_reply":returned_before_reply,"cancel_seconds":elapsed,
        "cancel_requested":cancel.load(Ordering::Relaxed),"stats":stats,"missing_file_vector_retained":retained,
        "note":"Controlled local response isolates cancellation timing; no inference benchmark."}))
}
fn stale_progress(base: &Path) -> Res<serde_json::Value> {
    let (mut lib,source)=setup(base,&[("a.jpg",b"saved source")])?;
    let locked=fs::OpenOptions::new().read(true).share_mode(3).open(lib.dir.join("catalog.json")).map_err(err)?;
    fs::write(source.join("copied.jpg"),b"saved source").map_err(err)?;
    let phase=std::cell::RefCell::new(String::new());
    let result=lib.index_live(&source,&["image".into()].into_iter().collect(),&Api{port:9},&AtomicBool::new(false),&|_|{},
        &base.join("excluded"),&|p|{*phase.borrow_mut()=p.phase;},&|_|{}); drop(locked);
    let saved_error = lib.last_stats.as_ref().is_some_and(|s|s.error.is_some() && s.processed>0);
    Ok(serde_json::json!({"passed":result.is_err() && phase.borrow().as_str()=="Failed" && saved_error,
        "failed_stats_available":saved_error,
        "failed_stats":lib.last_stats,
        "error":result.err(),"last_progress_phase":phase.into_inner(),
        "note":"Failure emits a terminal phase and complete failure stats for the app's latest report."}))
}
fn recovery_write_failure(base:&Path)->Res<serde_json::Value>{
    let (mut lib,_) = setup(base,&[("a.jpg",b"saved source")])?;
    lib.roots[0].records[0].snippet="second state".into();lib.commit()?;
    let active=lib.dir.join("catalog.json");let previous=lib.dir.join("catalog.previous.json");
    let before_active=fs::read(&active).map_err(err)?;let before_previous=fs::read(&previous).map_err(err)?;
    let snapshots=lib.roots[0].info.snapshot.clone();
    let locked=fs::OpenOptions::new().read(true).share_mode(3).open(&previous).map_err(err)?;
    lib.roots[0].records[0].snippet="third saved state".into();let outcome=lib.commit();drop(locked);
    let preserved=before_previous==fs::read(&previous).map_err(err)?;
    let saved=before_active!=fs::read(&active).map_err(err)?;
    let reload=Library::open(&lib.dir)?;
    let warning=lib.dir.join("recovery-catalog-error.json").is_file();
    Ok(serde_json::json!({"passed":outcome.is_ok() && preserved && saved && warning && lib.dir.join(snapshots).is_dir()
        && reload.roots[0].records[0].snippet=="third saved state","active_saved":saved,"older_recovery_preserved":preserved,
        "warning_saved":warning,"publish_succeeded":outcome.is_ok()}))
}
fn cancellation_after_final_file(base:&Path)->Res<serde_json::Value>{
    let (mut lib,source)=setup(base,&[("a.jpg",b"saved source"),("missing.jpg",b"missing source")])?;
    fs::remove_file(source.join("missing.jpg")).map_err(err)?;
    let cancel=AtomicBool::new(false);
    let stats=lib.index(&source,&["image".into()].into_iter().collect(),&Api{port:9},&cancel,
        &|_|{cancel.store(true,Ordering::Relaxed);},&base.join("excluded"))?;
    let retained=Library::open(&lib.dir)?.all().len()==2;
    Ok(serde_json::json!({"passed":stats.cancelled && stats.removed==0 && retained,"stats":stats,"old_records_retained":retained}))
}
fn long_document(base:&Path)->Res<serde_json::Value>{
    let text="a".repeat(600_000)+" LAST_PAGE_MARKER";
    let (mut lib,source)=setup(base,&[("long.txt",text.as_bytes())])?;
    let mut template=lib.roots[0].records[0].clone();template.kind="text".into();
    lib.roots[0].records=(0..400).map(|chunk|Record{chunk,..template.clone()}).collect();lib.commit()?;
    let before=fs::read(lib.dir.join("catalog.json")).map_err(err)?;
    let stats=lib.index(&source,&["text".into()].into_iter().collect(),&Api{port:9},&AtomicBool::new(false),&|_|{},&base.join("excluded"))?;
    let reported=stats.failed==1 && stats.warnings.iter().any(|w|w.contains("400 text chunks"));
    let preserved=before==fs::read(lib.dir.join("catalog.json")).map_err(err)?;
    let boundary=crate::extract::chunks(&"a".repeat(560_200))?.len()==400;
    let overflow=crate::extract::chunks(&"a".repeat(560_201)).is_err();
    Ok(serde_json::json!({"passed":reported && preserved && boundary && overflow,"old_partial_cache_reported":reported,
        "previous_snapshot_preserved":preserved,"exact_boundary_allowed":boundary,"overflow_reported":overflow,"stats":stats}))
}
fn invalid_response_indices(base:&Path)->Res<serde_json::Value>{
    fn respond(body:serde_json::Value)->Res<(Api,std::thread::JoinHandle<Res<()>>)> {
        let listener=TcpListener::bind("127.0.0.1:0").map_err(err)?;
        let port=listener.local_addr().map_err(err)?.port();
        let worker=std::thread::spawn(move||->Res<()>{
            let (mut stream,_)=listener.accept().map_err(err)?;
            stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(err)?;
            let mut raw=vec![];let mut buf=[0u8;4096];
            loop {let n=stream.read(&mut buf).map_err(err)?;if n==0{break;}raw.extend_from_slice(&buf[..n]);
                if let Some(at)=raw.windows(4).position(|x|x==b"\r\n\r\n") {
                    let header=String::from_utf8_lossy(&raw[..at]);
                    let len=header.lines().find_map(|l|l.to_lowercase().strip_prefix("content-length:").and_then(|n|n.trim().parse::<usize>().ok())).unwrap_or(0);
                    if raw.len()>=at+4+len{break;}
                }
            }
            let body=body.to_string();
            write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),body).map_err(err)?;Ok(())
        });Ok((Api{port},worker))
    }
    let mut a=vec![0.;768];a[0]=1.;let mut b=vec![0.;768];b[1]=1.;
    let malformed=[serde_json::json!({"data":[{"embedding":a},{"index":1,"embedding":b}]}),
        serde_json::json!({"data":[{"index":0,"embedding":a},{"index":0,"embedding":b}]}),
        serde_json::json!({"data":[{"index":9,"embedding":a},{"index":1,"embedding":b}]})];
    let mut rejected=0;
    for body in malformed {let (api,worker)=respond(body)?;if api.embed(vec![serde_json::json!("first"),serde_json::json!("second")]).is_err(){rejected+=1;}worker.join().map_err(|_|"Response stub panicked")??;}
    let (api,worker)=respond(serde_json::json!({"data":[{"index":1,"embedding":b},{"index":0,"embedding":a}]}))?;
    let sorted=api.embed(vec![serde_json::json!("first"),serde_json::json!("second")])?;worker.join().map_err(|_|"Response stub panicked")??;
    let (mut lib,source)=setup(base,&[("changed.png",b"previous source")])?;
    let active=fs::read(lib.dir.join("catalog.json")).map_err(err)?;
    image::RgbImage::from_pixel(32,48,image::Rgb([1,2,3])).save(source.join("changed.png")).map_err(err)?;
    let (api,worker)=respond(serde_json::json!({"data":[{"index":7,"embedding":b}]}))?;
    let stats=lib.index(&source,&["image".into()].into_iter().collect(),&api,&AtomicBool::new(false),&|_|{},&base.join("excluded"))?;
    worker.join().map_err(|_|"Response stub panicked")??;
    let preserved=fs::read(lib.dir.join("catalog.json")).map_err(err)?==active && Library::open(&lib.dir)?.all()[0].vector[0]==1.;
    let reordered=sorted[0][0]==1. && sorted[1][1]==1.;
    Ok(serde_json::json!({"passed":rejected==3 && reordered && preserved && stats.failed==1,
        "malformed_batches_rejected":rejected,"valid_out_of_order_response_sorted":reordered,"previous_vectors_retained":preserved,"stats":stats}))
}

pub fn run(home:&Path)->Res<serde_json::Value>{
    let base=home.join("verification/failure-injection").join(stamp().to_string());fs::create_dir_all(&base).map_err(err)?;
    let cases=serde_json::json!({"invalid_response_indices":invalid_response_indices(&base.join("response-indices"))?,"recovery_on_failed_publish":recovery(&base.join("recovery"))?,
        "cancel_during_final_file":cancellation(&base.join("cancel"))?,"stale_progress_after_save_error":stale_progress(&base.join("progress"))?,
        "recovery_write_failure":recovery_write_failure(&base.join("recovery-write"))?,
        "cancel_after_final_file":cancellation_after_final_file(&base.join("cancel-after"))?,
        "long_document":long_document(&base.join("long-document"))?});
    let passed=cases.as_object().unwrap().values().all(|v|v["passed"]==true);
    Ok(serde_json::json!({"passed":passed,"cases":cases,"fixture":display_path(&base),
        "note":"Generated files and controlled local inference responses. No production library or inference-speed measurement."}))
}
