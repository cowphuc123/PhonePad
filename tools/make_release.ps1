# Đóng gói 2 bản tải cho GitHub Releases vào dist\:
#   PhonePad-PC-v<ver>.zip       chương trình chạy trên máy tính (Python, không kèm ffmpeg)
#   PhonePad-Android-v<ver>.apk  app Android
# Chạy:  powershell -ExecutionPolicy Bypass -File tools\make_release.ps1 [-Version 0.1] [-SkipApkBuild]
param([string]$Version = "", [switch]$SkipApkBuild)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist"

if (-not $Version) {
    # lấy versionName trong android\app\build.gradle.kts
    $m = Select-String -Path (Join-Path $root "android\app\build.gradle.kts") -Pattern 'versionName\s*=\s*"([^"]+)"'
    $Version = $m.Matches[0].Groups[1].Value
}
Write-Host "Phien ban $Version"

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

# --- bản máy tính ---
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
$pcName = "PhonePad-PC-v$Version"
$stage = Join-Path $dist $pcName
New-Item -ItemType Directory -Force $stage, (Join-Path $stage "tools") | Out-Null

Get-ChildItem $root -File | Where-Object {
    $_.Extension -in ".py", ".bat", ".json", ".txt", ".md" -and $_.Name -ne ".gitignore"
} | Copy-Item -Destination $stage
Copy-Item (Join-Path $root "static") (Join-Path $stage "static") -Recurse
foreach ($f in "fetch_ffmpeg.ps1", "video_probe.py") {
    Copy-Item (Join-Path $root "tools\$f") (Join-Path $stage "tools")
}

$zip = Join-Path $dist "$pcName.zip"
Compress-Archive -Path $stage -DestinationPath $zip
Remove-Item $stage -Recurse -Force

$apk = Join-Path $dist "PhonePad-Android-v$Version.apk"
Copy-Item $apkOut $apk

Write-Host ""
Get-ChildItem $dist | ForEach-Object { "{0,-36} {1,8:N0} KB" -f $_.Name, ($_.Length / 1KB) }
