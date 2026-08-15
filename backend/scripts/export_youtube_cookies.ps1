# Export YouTube cookies for yt-dlp (Windows).
# Usage (from backend/):
#   .\scripts\export_youtube_cookies.ps1
#   .\scripts\export_youtube_cookies.ps1 -Browser edge
#   .\scripts\export_youtube_cookies.ps1 -Browser edge -Profile "Profile 1"
#
# IMPORTANT:
# 1) Be logged into YouTube in THAT browser (same one you pass here).
# 2) Fully quit that browser before running (script can kill it with -KillBrowser).
# 3) Do NOT paste cookies into chat — the file stays on disk only.

param(
    [ValidateSet("edge", "chrome", "firefox", "brave", "opera", "operagx")]
    [string]$Browser = "edge",
    [string]$Profile = "",
    [switch]$KillBrowser,
    [switch]$SkipEnvUpdate
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $BackendRoot

$VenvPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Нет .venv. Сначала: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
}

$procMap = @{
    # Do NOT include msedgewebview2 — used by Cursor/Windows widgets, not the browser UI
    edge    = @("msedge")
    chrome  = @("chrome")
    firefox = @("firefox")
    brave   = @("brave")
    opera   = @("opera", "opera_browser")
    operagx = @("opera", "opera_browser")
}

$OutDir = Join-Path $BackendRoot "secrets"
$OutFile = Join-Path $OutDir "youtube_cookies.txt"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Get-BrowserProcs([string]$name) {
    $names = $procMap[$name]
    if (-not $names) { return @() }
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.Name })
}

Write-Host ""
Write-Host "=== Экспорт cookies YouTube ($Browser) ===" -ForegroundColor Cyan
Write-Host "1) Открой $Browser → youtube.com → войди в аккаунт"
Write-Host "2) ПОЛНОСТЬЮ закрой $Browser (иконка на панели задач тоже)"
Write-Host "3) Потом Enter здесь..."
Write-Host ""

$alive = Get-BrowserProcs $Browser
if ($alive.Count -gt 0) {
    Write-Host "Сейчас запущен Microsoft Edge (msedge): PID $($alive.Id -join ', ')" -ForegroundColor Yellow
    if ($KillBrowser) {
        Write-Host "Останавливаю msedge ..." -ForegroundColor Yellow
        $alive | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    } else {
        Write-Host "Закрой Edge сам ИЛИ запусти с флагом -KillBrowser" -ForegroundColor Yellow
        Write-Host "  .\scripts\export_youtube_cookies.ps1 -Browser $Browser -KillBrowser" -ForegroundColor Cyan
    }
} else {
    Write-Host "Окно Edge (msedge) не найдено — ок. Процессы msedgewebview2 можно игнорировать (это Cursor/Windows)." -ForegroundColor DarkGray
}

$null = Read-Host "Enter когда браузер закрыт и ты залогинен в YouTube"

$alive = Get-BrowserProcs $Browser
if ($alive.Count -gt 0) {
    Write-Host "Браузер всё ещё запущен — cookies не скопируются. Закрой и повтори." -ForegroundColor Red
    if (-not $KillBrowser) {
        Write-Host "Или: .\scripts\export_youtube_cookies.ps1 -Browser $Browser -KillBrowser" -ForegroundColor Cyan
    }
    exit 1
}

$browserArg = $Browser
if ($Profile) {
    $browserArg = "${Browser}:${Profile}"
}

Write-Host "Экспортирую в $OutFile ..." -ForegroundColor Yellow
if (Test-Path $OutFile) { Remove-Item $OutFile -Force }

& $VenvPython -m yt_dlp `
    --cookies-from-browser $browserArg `
    --cookies $OutFile `
    --skip-download `
    --no-warnings `
    "https://www.youtube.com/watch?v=jNQXAC9IVRw"

if (-not (Test-Path $OutFile) -or (Get-Item $OutFile).Length -lt 50) {
    Write-Host ""
    Write-Host "Не получилось через yt-dlp (часто DPAPI / шифрование Edge)." -ForegroundColor Red
    Write-Host ""
    Write-Host "Надёжный обход — экспорт ИЗ браузера:" -ForegroundColor Cyan
    Write-Host "  1) Edge → youtube.com (залогинен)"
    Write-Host "  2) Расширение Get cookies.txt LOCALLY → Export"
    Write-Host "  3) .\scripts\import_youtube_cookies.ps1"
    Write-Host ""
    Write-Host "Или Firefox: зайди в YouTube там и:" -ForegroundColor Yellow
    Write-Host "  .\scripts\export_youtube_cookies.ps1 -Browser firefox -KillBrowser"
    exit 1
}

$ytLines = Select-String -Path $OutFile -Pattern "youtube\.com|google\.com" -ErrorAction SilentlyContinue
if (-not $ytLines) {
    Write-Host "Файл создан, но youtube/google cookies не найдены. Залогинься на YouTube в $Browser и повтори." -ForegroundColor Red
    exit 1
}

Write-Host "OK: cookies сохранены ($((Get-Item $OutFile).Length) байт, youtube/google строк: $($ytLines.Count))" -ForegroundColor Green

if (-not $SkipEnvUpdate) {
    $envPath = Join-Path $BackendRoot ".env"
    if (Test-Path $envPath) {
        $raw = Get-Content $envPath -Raw -Encoding UTF8
        $cookieLine = "YTDLP_COOKIES_FILE=./secrets/youtube_cookies.txt"
        if ($raw -match "(?m)^YTDLP_COOKIES_FILE=.*$") {
            $raw = [regex]::Replace($raw, "(?m)^YTDLP_COOKIES_FILE=.*$", $cookieLine)
        } else {
            $raw = $raw.TrimEnd() + "`r`n`r`n$cookieLine`r`n"
        }
        if ($raw -match "(?m)^YTDLP_COOKIES_FROM_BROWSER=.*$") {
            $raw = [regex]::Replace($raw, "(?m)^YTDLP_COOKIES_FROM_BROWSER=.*$", "YTDLP_COOKIES_FROM_BROWSER=")
        }
        Set-Content -Path $envPath -Value $raw -Encoding UTF8 -NoNewline
        Write-Host "Обновлён .env → $cookieLine" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Дальше: перезапусти API и снова «Разобрать»." -ForegroundColor Cyan
Write-Host "Cookies в чат не отправляй." -ForegroundColor Yellow
Write-Host ""
