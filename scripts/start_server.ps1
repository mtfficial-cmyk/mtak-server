# ATAK_MI_Server — Local Full-Stack Startup
# Cloud relay runs independently on Render: https://atak-mi-server.onrender.com
$API_PORT   = 3001
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT_DIR   = Join-Path $SCRIPT_DIR '..'
$API_DIR    = Join-Path $ROOT_DIR 'api'

Write-Host '================================================================'
Write-Host '       ATAK_MI_Server  —  LOCAL FULL STACK'
Write-Host '================================================================'
Write-Host '  Cloud relay (always-on): https://atak-mi-server.onrender.com' -ForegroundColor Cyan
Write-Host '================================================================'

# ── 1. Check Docker ───────────────────────────────────────────────────────────
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] Docker not found. Install Docker Desktop first.' -ForegroundColor Red
    exit 1
}

# ── 2. Start PostgreSQL + MinIO via docker-compose ────────────────────────────
Write-Host '[1/3] Starting PostgreSQL + MinIO (Docker)...'
Set-Location $ROOT_DIR
docker compose up -d --remove-orphans 2>&1 | ForEach-Object { Write-Host "  $_" }

if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] docker compose failed.' -ForegroundColor Red
    exit 1
}

# Wait for PostgreSQL to be ready (max 30s)
Write-Host '  Waiting for PostgreSQL...'
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    $check = docker exec atak_postgres pg_isready -U postgres 2>&1
    if ($check -match 'accepting connections') { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if ($ready) {
    Write-Host '  PostgreSQL ready.' -ForegroundColor Green
} else {
    Write-Host '  WARNING: PostgreSQL did not respond in 30s — continuing anyway.' -ForegroundColor Yellow
}

# ── 3. Install Python dependencies ───────────────────────────────────────────
Write-Host '[2/3] Installing Python dependencies...'
Set-Location $API_DIR
python -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] pip install failed. Is Python installed?' -ForegroundColor Red
    exit 1
}
Write-Host '  OK' -ForegroundColor Green

# Kill any old process on API port
$oldProc = Get-NetTCPConnection -LocalPort $API_PORT -ErrorAction SilentlyContinue |
           Select-Object -ExpandProperty OwningProcess -First 1
if ($oldProc) {
    Stop-Process -Id $oldProc -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}

# ── 4. Print summary + start FastAPI ─────────────────────────────────────────
$LOCAL_IP = (Get-NetIPAddress -AddressFamily IPv4 |
             Where-Object { $_.InterfaceAlias -match 'Wi-Fi|Ethernet' } |
             Select-Object -First 1).IPAddress

Write-Host ''
Write-Host '[3/3] Starting FastAPI (local full-stack mode)...'
Write-Host ''
Write-Host '================================================================'
Write-Host '  LOCAL SERVICES LIVE!' -ForegroundColor Green
Write-Host '================================================================'
Write-Host '  Cloud relay:      https://atak-mi-server.onrender.com' -ForegroundColor Yellow
Write-Host ("  Local API (LAN):  http://{0}:{1}" -f $LOCAL_IP, $API_PORT)
Write-Host ("  Local (Emulator): http://10.0.2.2:{0}"           -f $API_PORT)
Write-Host "  MinIO Console:    http://localhost:9001"
Write-Host "  PostgreSQL:       localhost:5432  (db: atak_db)"
Write-Host '----------------------------------------------------------------'
Write-Host '  ATAK app uses cloud relay by default.'
Write-Host '  Switch to local IP above only for LAN-only testing.'
Write-Host '----------------------------------------------------------------'
Write-Host '  Logs below. Press Ctrl+C to stop (Docker keeps running).'
Write-Host '================================================================'
Write-Host ''

python -m uvicorn main:app --host 0.0.0.0 --port $API_PORT --reload
