use crate::util::{Res, err};
use std::{
    fs::{File, OpenOptions},
    os::windows::{ffi::OsStrExt, fs::OpenOptionsExt, process::CommandExt},
    path::Path,
    process::{Child, Command},
};
use windows::{
    Win32::{
        Foundation::{CloseHandle, HANDLE},
        Storage::FileSystem::{MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW},
        System::{Com::*, JobObjects::*, Threading::CREATE_NO_WINDOW},
        UI::{Shell::*, WindowsAndMessaging::*},
    },
    core::{PCWSTR, PWSTR},
};
fn wide(p: &std::ffi::OsStr) -> Vec<u16> {
    p.encode_wide().chain(Some(0)).collect()
}
pub fn replace(from: &Path, to: &Path) -> Res<()> {
    unsafe {
        MoveFileExW(
            PCWSTR(wide(from.as_os_str()).as_ptr()),
            PCWSTR(wide(to.as_os_str()).as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
        .map_err(err)
    }
}
pub fn lock(p: &Path) -> Res<File> {
    OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .share_mode(0)
        .open(p)
        .map_err(|e| format!("This native library is already open, or is not writable: {e}"))
}
pub fn hidden(c: &mut Command) {
    c.creation_flags(CREATE_NO_WINDOW.0);
}
pub struct OwnedJob(HANDLE);
unsafe impl Send for OwnedJob {}
impl OwnedJob {
    pub fn attach(child: &Child) -> Res<Self> {
        use std::os::windows::io::AsRawHandle;
        unsafe {
            let h = CreateJobObjectW(None, None).map_err(err)?;
            let job = Self(h);
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of_val(&info) as u32,
            )
            .map_err(err)?;
            AssignProcessToJobObject(h, HANDLE(child.as_raw_handle())).map_err(err)?;
            Ok(job)
        }
    }
}
impl Drop for OwnedJob {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}
pub fn choose(folder: bool) -> Option<std::path::PathBuf> {
    unsafe {
        let init = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let result = (|| -> windows::core::Result<_> {
            let dialog: IFileOpenDialog =
                CoCreateInstance(&FileOpenDialog, None, CLSCTX_INPROC_SERVER)?;
            let mut flags = dialog.GetOptions()? | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST;
            if folder {
                flags |= FOS_PICKFOLDERS;
            } else {
                flags |= FOS_FILEMUSTEXIST;
            }
            dialog.SetOptions(flags)?;
            dialog.Show(None)?;
            let item = dialog.GetResult()?;
            let value: PWSTR = item.GetDisplayName(SIGDN_FILESYSPATH)?;
            let path = value.to_string()?;
            CoTaskMemFree(Some(value.0 as _));
            Ok(std::path::PathBuf::from(path))
        })();
        if init.is_ok() {
            CoUninitialize();
        }
        result.ok()
    }
}
pub fn reveal(p: &Path, select: bool) {
    let target = if select {
        std::path::PathBuf::from("explorer.exe")
    } else {
        p.to_owned()
    };
    let args = if select {
        format!("/select,\"{}\"", p.display())
    } else {
        String::new()
    };
    unsafe {
        let _ = ShellExecuteW(
            None,
            windows::core::w!("open"),
            PCWSTR(wide(target.as_os_str()).as_ptr()),
            PCWSTR(wide(std::ffi::OsStr::new(&args)).as_ptr()),
            None,
            SW_SHOWNORMAL,
        );
    }
}
pub fn message(s: &str) {
    unsafe {
        MessageBoxW(
            None,
            PCWSTR(wide(std::ffi::OsStr::new(s)).as_ptr()),
            windows::core::w!("Semantic Search · Rust"),
            MB_OK | MB_ICONERROR,
        );
    }
}
