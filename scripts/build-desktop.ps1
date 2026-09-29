# 构建 Windows 安装包：后端（PyInstaller 目录模式）→ 前端 → Tauri NSIS 安装包。
# 用法（仓库根目录，PowerShell）：  .\scripts\build-desktop.ps1
# 产物：apps\desktop\src-tauri\target\release\bundle\nsis\TK达人工作台_<版本>_x64-setup.exe
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Step($name, $dir, [scriptblock]$body) {
    Write-Host "==> $name" -ForegroundColor Cyan
    Push-Location (Join-Path $root $dir)
    try {
        & $body
        if ($LASTEXITCODE -ne 0) { throw "$name 失败（退出码 $LASTEXITCODE）" }
    } finally { Pop-Location }
}

Step "后端依赖" "backend" { uv sync --group build }
Step "后端打包" "backend" {
    uv run --group build pyinstaller packaging/tk-backend.spec --noconfirm --distpath dist --workpath build/pyinstaller
}
Step "后端冒烟" "backend" {
    # 打包后的后端必须能独立启动并打印就绪行
    $env:TKWS_DATA_DIR = Join-Path ([IO.Path]::GetTempPath()) ("tkws-smoke-" + [guid]::NewGuid())
    $env:TKWS_LAUNCH_TOKEN = "smoke"
    $p = Start-Process -FilePath "dist\tk-backend\tk-backend.exe" -NoNewWindow -PassThru `
        -RedirectStandardOutput "build\smoke.out" -RedirectStandardError "build\smoke.err"
    $ok = $false
    for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
        Start-Sleep -Milliseconds 500
        if ((Test-Path "build\smoke.out") -and (Select-String -Path "build\smoke.out" -Pattern "TKWS_READY" -Quiet)) { $ok = $true }
    }
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Remove-Item Env:TKWS_DATA_DIR, Env:TKWS_LAUNCH_TOKEN
    if (-not $ok) { Get-Content "build\smoke.err" -Tail 30; throw "打包后的后端未能启动" }
    $global:LASTEXITCODE = 0
}
Step "前端依赖" "apps/web" { pnpm install --frozen-lockfile }
Step "桌面外壳依赖" "apps/desktop" { pnpm install --frozen-lockfile }
Step "生成安装包" "apps/desktop" { pnpm build }

Get-ChildItem (Join-Path $root "apps/desktop/src-tauri/target/release/bundle/nsis/*.exe") |
    ForEach-Object { Write-Host "安装包：$($_.FullName)  ($([math]::Round($_.Length / 1MB, 1)) MB)" -ForegroundColor Green }
