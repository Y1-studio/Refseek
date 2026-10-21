//! Lightweight Windows feedback while the graphics window initializes.
//! Uses only system controls; it does not load models or initialize a GPU.
use std::{
    sync::{Arc, atomic::{AtomicBool, Ordering}, mpsc},
    thread,
    time::{Duration, Instant},
};
use windows::{
    core::w,
    Win32::{
        Foundation::{HWND, LPARAM, LRESULT, WPARAM},
        Graphics::Gdi::{COLOR_WINDOW, DEFAULT_GUI_FONT, GetStockObject, HBRUSH, UpdateWindow},
        System::LibraryLoader::GetModuleHandleW,
        UI::{Controls::*, WindowsAndMessaging::*},
    },
};

pub struct Opening {
    stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
    pub shown: bool,
    pub error: Option<String>,
    pub since: Instant,
}
unsafe extern "system" fn window_proc(h: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    // The main application owns this short-lived window's lifetime.
    if msg == WM_CLOSE { return LRESULT(0); }
    unsafe { DefWindowProcW(h, msg, wp, lp) }
}
impl Opening {
    pub fn start() -> Self {
        let since = Instant::now();
        let stop = Arc::new(AtomicBool::new(false));
        let signal = stop.clone();
        let (tx, rx) = mpsc::channel();
        let thread = thread::spawn(move || unsafe {
            let result = (|| -> windows::core::Result<()> {
                let instance = GetModuleHandleW(None)?;
                let class = w!("SemanticSearchOpening");
                let wc = WNDCLASSW {
                    lpfnWndProc: Some(window_proc),
                    hInstance: instance.into(),
                    lpszClassName: class,
                    hbrBackground: HBRUSH((COLOR_WINDOW.0 as usize + 1) as *mut _),
                    hCursor: LoadCursorW(None, IDC_APPSTARTING)?,
                    ..Default::default()
                };
                if RegisterClassW(&wc) == 0 { return Err(windows::core::Error::from_thread()); }
                let width = 460;
                let height = 155;
                let x = (GetSystemMetrics(SM_CXSCREEN) - width) / 2;
                let y = (GetSystemMetrics(SM_CYSCREEN) - height) / 2;
                let window = CreateWindowExW(
                    WS_EX_DLGMODALFRAME, class, w!("Semantic Search · Opening…"),
                    WS_CAPTION | WS_POPUP, x, y, width, height,
                    None, None, Some(instance.into()), None,
                )?;
                // Destroy the window on this same UI thread even if a child fails.
                let children = (|| -> windows::core::Result<()> {
                    let label = CreateWindowExW(
                        WINDOW_EX_STYLE::default(), w!("STATIC"),
                        w!("Opening Semantic Search…\nPreparing the desktop window. Please wait."),
                        WS_CHILD | WS_VISIBLE, 22, 18, 410, 48,
                        Some(window), None, Some(instance.into()), None,
                    )?;
                    let font = GetStockObject(DEFAULT_GUI_FONT);
                    SendMessageW(label, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));
                    let controls = INITCOMMONCONTROLSEX {
                        dwSize: std::mem::size_of::<INITCOMMONCONTROLSEX>() as u32,
                        dwICC: ICC_PROGRESS_CLASS,
                    };
                    if !InitCommonControlsEx(&controls).as_bool() {
                        return Err(windows::core::Error::from_thread());
                    }
                    let bar = CreateWindowExW(
                        WINDOW_EX_STYLE::default(), w!("msctls_progress32"), w!(""),
                        WS_CHILD | WS_VISIBLE | WINDOW_STYLE(PBS_MARQUEE),
                        22, 75, 410, 14, Some(window), None, Some(instance.into()), None,
                    )?;
                    SendMessageW(bar, PBM_SETMARQUEE, Some(WPARAM(1)), Some(LPARAM(30)));
                    let _ = ShowWindow(window, SW_SHOWNORMAL);
                    // The first ShowWindow can inherit a hidden launcher startup
                    // flag. The explicit second call also covers that launch path.
                    let _ = ShowWindow(window, SW_SHOW);
                    let _ = UpdateWindow(window);
                    let _ = tx.send(Ok(IsWindowVisible(window).as_bool()));
                    let mut msg = MSG::default();
                    while !signal.load(Ordering::Relaxed) {
                        while PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE).as_bool() {
                            let _ = TranslateMessage(&msg);
                            DispatchMessageW(&msg);
                        }
                        thread::sleep(Duration::from_millis(20));
                    }
                    Ok(())
                })();
                let _ = DestroyWindow(window);
                let _ = UnregisterClassW(class, Some(instance.into()));
                children
            })();
            if let Err(e) = result { let _ = tx.send(Err(e.to_string())); }
        });
        // Window creation happens before graphics initialization starts.
        let (shown, error) = match rx.recv_timeout(Duration::from_secs(2)) {
            Ok(Ok(shown)) => (shown, None),
            Ok(Err(e)) => (false, Some(e)),
            Err(e) => (false, Some(e.to_string())),
        };
        Self { stop, thread: Some(thread), shown, error, since }
    }
}
impl Drop for Opening {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() { let _ = t.join(); }
    }
}
