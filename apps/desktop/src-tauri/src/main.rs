//! TK 达人工作台桌面外壳。
//!
//! 职责只有四件事：
//! 1. 每次启动生成随机访问令牌，启动 Python 后端子进程（无黑窗口），从其标准输出读取端口；
//! 2. 通过 `backend_info` 命令把端口和令牌交给界面（等待后端就绪，最多 60 秒）；
//! 3. 单实例：再次双击时激活已有窗口，不启动第二个后端；
//! 4. 退出时结束后端（后端另有父进程看护，外壳崩溃时也会自行退出）。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const READY_PREFIX: &str = "TKWS_READY ";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Clone, Default)]
enum Status {
    #[default]
    Starting,
    Ready {
        port: u16,
    },
    Failed(String),
}

#[derive(Default)]
struct Backend {
    status: Mutex<Status>,
    changed: Condvar,
    child: Mutex<Option<Child>>,
}

impl Backend {
    fn set(&self, status: Status) {
        *self.status.lock().unwrap() = status;
        self.changed.notify_all();
    }
}

struct Shared {
    backend: Arc<Backend>,
    token: String,
}

#[derive(serde::Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[tauri::command]
async fn backend_info(shared: tauri::State<'_, Shared>) -> Result<BackendInfo, String> {
    let backend = shared.backend.clone();
    let token = shared.token.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let mut status = backend.status.lock().unwrap();
        loop {
            match &*status {
                Status::Ready { port } => return Ok(BackendInfo { port: *port, token }),
                Status::Failed(msg) => return Err(msg.clone()),
                Status::Starting => {}
            }
            let now = Instant::now();
            if now >= deadline {
                return Err("后台服务启动超时（60 秒）".to_string());
            }
            status = backend.changed.wait_timeout(status, deadline - now).unwrap().0;
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

fn new_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("系统随机数不可用");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn backend_command(app: &AppHandle) -> Result<Command, String> {
    if cfg!(debug_assertions) {
        // 开发构建：用 uv 直接运行后端源码
        let backend_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../backend");
        let mut cmd = Command::new("uv");
        cmd.args(["run", "python", "-m", "tk_workspace.desktop"]).current_dir(&backend_dir);
        cmd.env("TKWS_APP_ENV", "development");
        Ok(cmd)
    } else {
        let exe = app
            .path()
            .resource_dir()
            .map_err(|e| format!("无法定位安装目录：{e}"))?
            .join("tk-backend")
            .join("tk-backend.exe");
        if !exe.exists() {
            return Err(format!("找不到后台程序：{}", exe.display()));
        }
        let mut cmd = Command::new(&exe);
        if let Some(dir) = exe.parent() {
            cmd.current_dir(dir);
        }
        Ok(cmd)
    }
}

fn spawn_backend(app: &AppHandle, backend: Arc<Backend>, token: &str) {
    let mut cmd = match backend_command(app) {
        Ok(cmd) => cmd,
        Err(msg) => return backend.set(Status::Failed(msg)),
    };
    cmd.env("TKWS_LAUNCH_TOKEN", token)
        .env("TKWS_PARENT_PID", std::process::id().to_string())
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(if cfg!(debug_assertions) { Stdio::inherit() } else { Stdio::null() });
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(e) => return backend.set(Status::Failed(format!("无法启动后台服务：{e}"))),
    };
    let stdout = child.stdout.take().expect("stdout is piped");
    *backend.child.lock().unwrap() = Some(child);

    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if let Some(rest) = line.strip_prefix(READY_PREFIX) {
                let port = serde_json::from_str::<serde_json::Value>(rest)
                    .ok()
                    .and_then(|v| v.get("port").and_then(|p| p.as_u64()));
                if let Some(port) = port {
                    backend.set(Status::Ready { port: port as u16 });
                }
            }
        }
        // 标准输出关闭说明后端已退出
        let failed_before_ready = matches!(*backend.status.lock().unwrap(), Status::Starting);
        if failed_before_ready {
            backend.set(Status::Failed(
                "后台服务启动失败。日志：%LOCALAPPDATA%\\TKWorkspace\\logs\\backend.log".to_string(),
            ));
        }
    });
}

fn stop_backend(backend: &Backend) {
    if let Some(mut child) = backend.child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn main() {
    let backend = Arc::new(Backend::default());
    let token = new_token();

    let setup_backend = backend.clone();
    let setup_token = token.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(Shared { backend: backend.clone(), token })
        .invoke_handler(tauri::generate_handler![backend_info])
        .setup(move |app| {
            spawn_backend(app.handle(), setup_backend.clone(), &setup_token);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("启动桌面窗口失败");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            stop_backend(&backend);
        }
    });
}
