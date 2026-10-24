# Tải ffmpeg bản "full" của gyan.dev (có ddagrab, h264_nvenc, h264_qsv, h264_mf) vào tools\ffmpeg\
# Chạy:  powershell -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1  [-Force] [-Version 7.1]
#
# Mặc định ghim bản 7.1: đây là bản gyan mới nhất còn dùng NVENC API 12.2 (driver NVIDIA >= 551.76).
# Từ 7.1.1 trở đi gyan build với NVENC API 13.x -> cần driver >= 570 (bản 9.x cần >= 610); máy đang có
# driver 566.36 nên h264_nvenc báo "Driver does not support the required nvenc API version".
param([switch]$Force, [string]$Version = "7.1")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # thanh tiến trình làm Invoke-WebRequest chậm hẳn
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dest = Join-Path $PSScriptRoot "ffmpeg"
$bin = Join-Path $dest "bin"
$exe = Join-Path $bin "ffmpeg.exe"

function Test-Ffmpeg {
    if (-not (Test-Path $exe)) { return $false }
    $enc = (& $exe -hide_banner -encoders 2>$null) -join "`n"
    $flt = (& $exe -hide_banner -filters 2>$null) -join "`n"
    $ok = $true
    foreach ($name in "h264_nvenc", "h264_qsv", "h264_mf") {
        if ($enc -match "\b$name\b") { Write-Host "  [co] encoder $name" }
        else { Write-Host "  [THIEU] encoder $name"; $ok = $false }
    }
    if ($flt -match "\bddagrab\b") { Write-Host "  [co] filter ddagrab" }
    else { Write-Host "  [THIEU] filter ddagrab"; $ok = $false }
    # thử mở NVENC thật (phụ thuộc driver); hỏng thì video.py tự lùi về QSV/MF nên chỉ cảnh báo
    $ErrorActionPreference = "Continue"
    & $exe -hide_banner -loglevel quiet -f lavfi -i testsrc2=size=1280x720:rate=60 -frames:v 5 `
        -c:v h264_nvenc -preset p1 -tune ull -f null - 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  [chay duoc] h264_nvenc" }
    else { Write-Host "  [CANH BAO] h264_nvenc khong mo duoc (driver NVIDIA qua cu cho ban ffmpeg nay?)" }
    return $ok
}

if ((-not $Force) -and (Test-Path $exe)) {
    Write-Host "Da co $exe"
    $cur = (& $exe -hide_banner -version | Select-Object -First 1)
    Write-Host $cur
    if (($cur -match "ffmpeg version $([regex]::Escape($Version))-") -and (Test-Ffmpeg)) { exit 0 }
    Write-Host "Ban hien tai khong phai $Version hoac thieu tinh nang, tai lai..."
}

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("phonepad_ffmpeg_" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
    $extract = Join-Path $tmp "x"
    New-Item -ItemType Directory -Force $extract | Out-Null
    $done = $false

    # Cách 1: bản zip "full_build" trên GitHub GyanD/codexffmpeg (cùng tác giả với gyan.dev, tải nhanh hơn nhiều)
    try {
        Write-Host "Tim ban ffmpeg $Version full_build.zip tren GitHub GyanD/codexffmpeg ..."
        $rel = Invoke-RestMethod "https://api.github.com/repos/GyanD/codexffmpeg/releases/tags/$Version" -UseBasicParsing
        $asset = $rel.assets | Where-Object { $_.name -match "full_build\.zip$" -and $_.name -notmatch "shared" } | Select-Object -First 1
        if (-not $asset) { throw "Khong tim thay ban full_build.zip" }
        $arc = Join-Path $tmp $asset.name
        Write-Host "Tai $($asset.browser_download_url) ($([math]::Round($asset.size / 1MB)) MB) ..."
        Invoke-WebRequest $asset.browser_download_url -OutFile $arc -UseBasicParsing
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($arc, $extract)
        $done = $true
    } catch {
        Write-Host "  That bai: $($_.Exception.Message)"
        Get-ChildItem $extract | Remove-Item -Recurse -Force
    }

    # Cách 2: gói .7z trên gyan.dev (có thể rất chậm, ~50 KB/s khi thử), giải nén bằng tar.exe của Windows 11 (libarchive)
    if (-not $done) {
        $arc = Join-Path $tmp "ffmpeg.7z"
        $url = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$Version-full_build.7z"
        Write-Host "Tai $url ..."
        Invoke-WebRequest $url -OutFile $arc -UseBasicParsing
        & "$env:SystemRoot\System32\tar.exe" -xf $arc -C $extract
        if ($LASTEXITCODE -ne 0) { throw "tar.exe khong giai nen duoc .7z" }
    }

    $root = Get-ChildItem $extract -Directory | Select-Object -First 1
    if (-not (Test-Path (Join-Path $root.FullName "bin\ffmpeg.exe"))) { throw "Goi tai ve khong co bin\ffmpeg.exe" }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Move-Item $root.FullName $dest
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

& $exe -hide_banner -version | Select-Object -First 1
if (Test-Ffmpeg) { Write-Host "OK: $exe"; exit 0 }
Write-Host "Ban ffmpeg tai ve thieu tinh nang can thiet."
exit 1
