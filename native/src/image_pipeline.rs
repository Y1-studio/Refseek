//! Bounded CPU preparation ahead of serial inference and catalog publication.
use crate::{rpc, util::*};
use sha2::{Digest,Sha256};
use std::io::Read;
use std::{collections::{BTreeMap,HashSet}, fs, path::{Path,PathBuf},
    sync::{Arc,Mutex,RwLock,atomic::{AtomicBool,Ordering},mpsc::{self,Receiver,SyncSender}},
    thread::{self,JoinHandle},time::{Duration,Instant}};

pub const DEFAULT_WORKERS:usize=2;
pub fn maximum_workers()->usize {thread::available_parallelism().map_or(1,usize::from)}
pub fn worker_count(requested:usize)->usize {
    let cores=maximum_workers();
    if requested==0 {(cores/2).clamp(1,4)} else {requested.clamp(1,cores)}
}
pub struct Prepared {
    pub size:u64,
    pub modified:u128,
    pub hash:String,
    pub image:Option<(String,Vec<u8>)>,
}
pub struct Outcome {pub value:Res<Prepared>,pub hashed:bool,pub seconds:f64}
struct Job {path:PathBuf,kind:String,reply:SyncSender<Outcome>}
pub struct Pool {
    jobs:Option<SyncSender<Job>>,
    results:BTreeMap<usize,Receiver<Outcome>>,
    workers:Vec<JoinHandle<()>>,
    stop:Arc<AtomicBool>,
    known:Arc<RwLock<HashSet<String>>>,
    pub window:usize,
    pub count:usize,
}
impl Pool {
    pub fn new(requested:usize, files:usize, known:HashSet<String>)->Res<Self>{
        let count=if files==0 {0}else{worker_count(requested).min(files)};
        let (jobs,receiver)=mpsc::sync_channel::<Job>(count.max(1));
        let receiver=Arc::new(Mutex::new(receiver));
        let stop=Arc::new(AtomicBool::new(false));
        let known=Arc::new(RwLock::new(known));
        let mut pool=Self {jobs:Some(jobs),results:BTreeMap::new(),workers:vec![],
            stop:stop.clone(),known:known.clone(),window:count+1,count};
        for i in 0..count {
            let receiver=receiver.clone();let stop=stop.clone();let known=known.clone();
            let worker=thread::Builder::new().name(format!("image-preparation-{i}")).spawn(move ||{
                while !stop.load(Ordering::Relaxed) {
                    let job=match receiver.lock() {Ok(r)=>r.recv(),Err(_)=>break};
                    let Ok(job)=job else {break};
                    if stop.load(Ordering::Relaxed) {break;}
                    let start=Instant::now();let mut hashed=false;
                    let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||prepare(&job.path,&job.kind,&known,&stop,&mut hashed)))
                        .unwrap_or_else(|_|Err("Image preparation worker failed; previous vectors retained".into()));
                    let _=job.reply.send(Outcome{value:result,hashed,seconds:start.elapsed().as_secs_f64()});
                }
            }).map_err(err)?;
            pool.workers.push(worker);
        }
        Ok(pool)
    }
    pub fn submit(&mut self,index:usize,path:&Path,kind:&str)->Res<()> {
        let (reply,result)=mpsc::sync_channel(1);
        self.jobs.as_ref().ok_or("Preparation pool stopped")?
            .send(Job{path:path.into(),kind:kind.into(),reply}).map_err(err)?;
        self.results.insert(index,result);
        Ok(())
    }
    pub fn take(&mut self,index:usize,cancel:&AtomicBool)->Res<Outcome>{
        let result=self.results.remove(&index).ok_or("Missing preparation result")?;
        loop {
            if cancel.load(Ordering::Relaxed) {
                self.stop.store(true,Ordering::Relaxed);
                return Err("Indexing cancelled; previous file vectors retained".into());
            }
            match result.recv_timeout(Duration::from_millis(50)) {
                Ok(value)=>return Ok(value),
                Err(mpsc::RecvTimeoutError::Timeout)=>{},
                Err(e)=>return Err(format!("Image preparation stopped: {e}")),
            }
        }
    }
    pub fn mark_cached(&self,hash:&str){if let Ok(mut known)=self.known.write(){known.insert(hash.into());}}
}
impl Drop for Pool {
    fn drop(&mut self){
        self.stop.store(true,Ordering::Relaxed);
        self.jobs.take();
        self.results.clear();
        for worker in self.workers.drain(..){let _=worker.join();}
    }
}
fn prepare(path:&Path,kind:&str,known:&RwLock<HashSet<String>>,stop:&AtomicBool,hashed:&mut bool)->Res<Prepared>{
    let metadata=fs::metadata(path).map_err(err)?;
    if metadata.len()==0 {return Err("Empty file".into());}
    if metadata.len()>256*1024*1024 {return Err("File exceeds 256 MiB".into());}
    if stop.load(Ordering::Relaxed){return Err("Preparation cancelled".into());}
    // Hash the same owned bytes we decode. Keep the later source validation before
    // publishing vectors; this removes only the duplicate preparation read/hash.
    let (digest,bytes)=if kind=="image" && metadata.len()<=64*1024*1024 {
        let (digest,bytes)=read_hashed_image(path,metadata.len(),stop)?;
        (digest,Some(bytes))
    } else {(file_hash_cancellable(path,stop)?,None)};
    *hashed=true;
    let modified=metadata.modified().map_err(err)?.duration_since(std::time::UNIX_EPOCH).map_err(err)?.as_nanos();
    let cached=kind=="image" && known.read().map_err(err)?.contains(&digest);
    let image=if kind=="image" && !cached {
        if metadata.len()>64*1024*1024 {return Err("Image exceeds 64 MiB".into());}
        if stop.load(Ordering::Relaxed){return Err("Preparation cancelled".into());}
        Some(rpc::prepared_bytes(bytes.as_deref().ok_or("Image bytes missing")?)?)
    } else {None};
    if stop.load(Ordering::Relaxed){return Err("Preparation cancelled".into());}
    let after=fs::metadata(path).map_err(err)?;
    if after.len()!=metadata.len() || after.modified().map_err(err)?!=metadata.modified().map_err(err)? {
        return Err("File changed during preparation; previous vectors retained".into());
    }
    Ok(Prepared{size:metadata.len(),modified,hash:digest,image})
}

fn read_hashed_image(path:&Path,size:u64,stop:&AtomicBool)->Res<(String,Vec<u8>)>{
    let mut file=fs::File::open(path).map_err(err)?;
    let mut bytes=Vec::with_capacity(size as usize);
    let mut digest=Sha256::new();let mut buffer=[0u8;131072];
    loop {
        if stop.load(Ordering::Relaxed){return Err("Preparation cancelled".into());}
        let count=file.read(&mut buffer).map_err(err)?;
        if count==0 {break;}
        if bytes.len()+count>64*1024*1024 {return Err("Image exceeds 64 MiB".into());}
        digest.update(&buffer[..count]);bytes.extend_from_slice(&buffer[..count]);
    }
    if bytes.len() as u64!=size {return Err("File changed during preparation; previous vectors retained".into());}
    Ok((format!("{:x}",digest.finalize()),bytes))
}
