# Đóng gói 2 bản tải cho GitHub Releases vào dist\:
#   PhonePad-PC-v<ver>.exe       chương trình máy tính (một file exe, PyInstaller)
#   PhonePad-Android-v<ver>.apk  app Android
# Chạy:  powershell -ExecutionPolicy Bypass -File tools\make_release.ps1 [-Version 0.2] [-SkipApkBuild]
# Cần:   pip install pyinstaller aiohttp vgamepad psutil pillow ; Android SDK để build APK
param([string]$Version = "", [switch]$SkipApkBuild)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist"
$build = Join-Path $root "build"

if (-not $Version) {
    # lấy versionName trong android\app\build.gradle.kts (2 bản luôn cùng số phiên bản)
    $m = Select-String -Path (Join-Path $root "android\app\build.gradle.kts") -Pattern 'versionName\s*=\s*"([^"]+)"'
    $Version = $m.Matches[0].Groups[1].Value
}
Write-Host "Phien ban $Version"
# exe tự cập nhật so tag GitHub với version.py, app so với versionName: hai số phải trùng nhau
$py = Select-String -Path (Join-Path $root "version.py") -Pattern 'VERSION\s*=\s*"([^"]+)"'
if ($py.Matches[0].Groups[1].Value -ne $Version) {
    throw "version.py ($($py.Matches[0].Groups[1].Value)) khac versionName Android ($Version)"
}

# --- APK ---
$apkOut = Join-Path $root "android\app\build\outputs\apk\debug\app-debug.apk"
if (-not $SkipApkBuild) {
    Write-Host "Build APK ..."
    Push-Location (Join-Path $root "android")
    try {
        & .\gradlew.bat assembleDebug --console=plain
        if ($LASTEXITCODE -ne 0) { throw "gradlew assembleDebug that bai" }
    } finally { Pop-Location }
}
if (-not (Test-Path $apkOut)) { throw "Khong thay $apkOut" }

# --- exe máy tính ---
New-Item -ItemType Directory -Force $build | Out-Null
Push-Location $root
try {
    & python tools\make_icon.py | Out-Null
    # bố cục mẫu = bản đã commit (không lấy bố cục đang sửa dở trên máy này)
    $default = Join-Path $build "layouts.default.json"
    # (qua cmd để giữ nguyên byte UTF-8; ">" của PowerShell 5.1 ghi ra UTF-16)
    cmd /c "git show HEAD:layouts.json > `"$default`""
    if ($LASTEXITCODE -ne 0) { throw "git show layouts.json that bai" }
    $exclude = "numpy", "PIL", "pydantic", "pydantic_core", "yaml", "chardet", "charset_normalizer",
               "setuptools", "pkg_resources", "IPython", "jedi", "matplotlib", "pandas", "scipy",
               "cryptography", "lxml" | ForEach-Object { "--exclude-module"; $_ }
    & python -m PyInstaller --noconfirm --onefile --windowed --name PhonePad `
        --icon "$root\phonepad.ico" `
        --add-data "$root\static\index.html;static" `
        --add-data "$default;." `
        --add-data "$root\phonepad.ico;." `
        --collect-all vgamepad @exclude `
        --distpath "$build\dist" --workpath "$build\pyi" --specpath $build `
        server.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller that bai" }
} finally { Pop-Location }

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force $dist | Out-Null
Copy-Item "$build\dist\PhonePad.exe" (Join-Path $dist "PhonePad-PC-v$Version.exe")
Copy-Item $apkOut (Join-Path $dist "PhonePad-Android-v$Version.apk")

Write-Host ""
Get-ChildItem $dist | ForEach-Object { "{0,-36} {1,8:N0} KB" -f $_.Name, ($_.Length / 1KB) }
