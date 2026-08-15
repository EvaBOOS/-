# Import cookies.txt exported FROM the browser extension
# (Get cookies.txt LOCALLY / cookies.txt).
#
# Why: Edge/Chrome often block yt-dlp with "Failed to decrypt with DPAPI".
# Export inside the browser works because the browser itself decrypts cookies.
#
# Steps:
# 1) In Edge (or Opera) open https://www.youtube.com and stay logged in
# 2) Install extension: "Get cookies.txt LOCALLY"
#    https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
# 3) On youtube.com click the extension → Export → save as youtube_cookies.txt
# 4) Run this script and point it at that file (or drop file into the default path)

param(
    [string]$Source = ""
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $BackendRoot

$OutDir = Join-Path $BackendRoot "secrets"
$OutFile = Join-Path $OutDir "youtube_cookies.txt"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$downloads = Join-Path $env:USERPROFILE "Downloads"
$candidates = @()
if ($Source) { $candidates += $Source }
$candidates += @(
    (Join-Path $downloads "youtube_cookies.txt"),
    (Join-Path $downloads "www.youtube.com_cookies.txt"),
    (Join-Path $downloads "cookies.txt"),
    $OutFile
)

$found = $null
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c) -and (Get-Item $c).Length -gt 50) {
        $found = $c
        break
    }
}

if (-not $found) {
    Write-Host ""
    Write-Host "Файл cookies не найден." -ForegroundColor Red
    Write-Host ""
    Write-Host "Сделай так:" -ForegroundColor Cyan
    Write-Host "  1) Edge → youtube.com (будь залогинен)"
    Write-Host "  2) Установи расширение Get cookies.txt LOCALLY"
    Write-Host "  3) На странице YouTube нажми расширение → Export"
    Write-Host "  4) Сохрани файл в Загрузки как youtube_cookies.txt"
    Write-Host "  5) Снова: .\scripts\import_youtube_cookies.ps1"
    Write-Host ""
    Write-Host "Или укажи путь:" -ForegroundColor Yellow
    Write-Host "  .\scripts\import_youtube_cookies.ps1 -Source `"C:\Users\...\Downloads\cookies.txt`""
    exit 1
}

Copy-Item -Path $found -Destination $OutFile -Force
$yt = Select-String -Path $OutFile -Pattern "youtube\.com|google\.com" -ErrorAction SilentlyContinue
if (-not $yt) {
    Write-Host "В файле нет youtube/google cookies. Экспортируй именно со страницы youtube.com" -ForegroundColor Red
    exit 1
}

Write-Host "OK: скопировано → $OutFile ($((Get-Item $OutFile).Length) байт, строк: $($yt.Count))" -ForegroundColor Green
Write-Host "Источник: $found" -ForegroundColor DarkGray

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

Write-Host ""
Write-Host "Перезапусти API и снова нажми «Разобрать»." -ForegroundColor Cyan
Write-Host "Cookies в чат не отправляй." -ForegroundColor Yellow
