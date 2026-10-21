use std::{env, path::PathBuf, process::Command};

fn main() {
    println!("cargo:rerun-if-changed=resources/Refseek.rc");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo output directory"))
        .join("refseek-version.o");
    let compiler = env::var_os("WINDRES").unwrap_or_else(|| "windres.exe".into());
    let status = Command::new(compiler)
        .args(["-i", "resources/Refseek.rc", "-O", "coff", "-o"])
        .arg(&output)
        .status()
        .expect("MinGW windres is required to embed Refseek's Windows application information");
    assert!(status.success(), "Windows application resource compilation failed");
    println!("cargo:rustc-link-arg={}", output.display());
}
