import os
import io
import uuid
import base64
import asyncio
import asyncpg
import logging
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from asyncpg.exceptions import UniqueViolationError as IntegrityError

from models import *
from websocket_manager import ConnectionManager
from udp_discovery import UDPDiscoveryService
from auth import hash_password, verify_password, create_access_token, decode_token
import lora_detector

load_dotenv()

# ── Deployment mode ───────────────────────────────────────────────────────────
#
# RELAY_MODE=true   → cloud deployment on Render (or similar).
#   • No PostgreSQL, MinIO, MQTT, or UDP Discovery.
#   • The server is a pure WebSocket relay: it forwards every message to all
#     connected room members without persisting anything.
#   • JWT validation still works (stateless — only needs the shared secret).
#   • Heartbeat / presence / LoRa registry work from in-memory state.
#
# RELAY_MODE=false  → local deployment with full Docker stack (default).
#   • Full DB persistence, MinIO media storage, UDP Discovery.
#   • MQTT disabled by default; set MQTT_ENABLED=true to re-enable.
#
RELAY_MODE   = os.getenv("RELAY_MODE",   "false").lower() == "true"
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "false").lower() == "true"

# Public URL returned by /api/config so clients can auto-configure.
# Set this to e.g. "https://atak-mi-server.onrender.com" on Render.
SERVER_URL = os.getenv("SERVER_URL", "")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
for _name in ("MQTTService", "uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_name).setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
PORT    = int(os.getenv("PORT", 3001))
PG_HOST = os.getenv("POSTGRES_HOST",     "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", 5432))
PG_USER = os.getenv("POSTGRES_USER",     "atak")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "atak_secret")
PG_DB   = os.getenv("POSTGRES_DB",       "atak_db")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT",   "localhost")
MINIO_PORT     = int(os.getenv("MINIO_PORT",   9000))
MINIO_SSL      = os.getenv("MINIO_USE_SSL",    "false").lower() == "true"
MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "atakadmin")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "atakadmin123")

# ── Services (all start as None; lifespan populates what's needed) ─────────────
manager   = ConnectionManager()
discovery = UDPDiscoveryService(port=8091)
mqtt_sub  = None
db_pool   = None
minio_client = None

BUCKETS = ["atak-images", "atak-videos", "atak-audio"]

# ── Presence (in-memory — works in both modes) ─────────────────────────────────
last_heartbeat:       dict = {}   # username → last-seen timestamp
_heartbeat_device_ids: dict = {}
_heartbeat_lora_names: dict = {}
_heartbeat_lora_ids:   dict = {}
ONLINE_TIMEOUT_SEC = 60

# ── Live Streaming State (in-memory, works in both relay and full mode) ───────
# stream_id (= streamer's username) → {"streamer": str, "started_at": str}
_active_streams:     dict = {}
# stream_id → set of subscriber WebSockets
_stream_subscribers: dict = {}

# HTML template for the in-app WebView viewer.
# "__STREAM_ID__" is replaced at request time; plain strings avoid f-string
# brace escaping across the embedded JavaScript.
_LIVE_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <title>Live Stream</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{background:#0A0A0E;color:#E2E8F0;font-family:monospace;height:100vh;
         display:flex;flex-direction:column;overflow:hidden}
    #hdr{background:rgba(0,0,0,.75);padding:8px 14px;display:flex;
         align-items:center;gap:10px;flex-shrink:0}
    #dot{width:8px;height:8px;border-radius:50%;background:#64748B;flex-shrink:0}
    #dot.live{background:#4ADE80;animation:blink 1.2s ease-in-out infinite}
    @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
    #lbl{font-size:12px;color:#00BAFF;flex:1;letter-spacing:.04em}
    #fps{font-size:11px;color:#4ADE80}
    #wrap{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden}
    img#frm{max-width:100%;max-height:100%;object-fit:contain;display:none}
    #idle{text-align:center;color:#4A5568;font-size:14px;line-height:1.9}
  </style>
</head>
<body>
  <div id="hdr">
    <div id="dot"></div>
    <span id="lbl">Connecting…</span>
    <span id="fps"></span>
  </div>
  <div id="wrap">
    <img id="frm" alt="">
    <div id="idle">📡<br>Waiting for stream…</div>
  </div>
<script>
(function(){
  var SID  = '__STREAM_ID__';
  var wpro = location.protocol==='https:'?'wss:':'ws:';
  var wurl = wpro+'//'+location.host+'/stream';
  var ws, frames=0, t0=Date.now(), prev=null;
  var dot=document.getElementById('dot');
  var lbl=document.getElementById('lbl');
  var fps=document.getElementById('fps');
  var frm=document.getElementById('frm');
  var idle=document.getElementById('idle');

  function setLive(on){
    if(on){dot.classList.add('live');frm.style.display='block';idle.style.display='none';}
    else  {dot.classList.remove('live');frm.style.display='none';idle.style.display='block';}
  }

  function connect(){
    ws=new WebSocket(wurl);
    ws.binaryType='arraybuffer';
    ws.onopen=function(){ws.send(JSON.stringify({action:'subscribe',stream_id:SID}));};
    ws.onmessage=function(e){
      if(typeof e.data==='string'){
        var m=JSON.parse(e.data);
        if(m.status==='ok'){lbl.textContent='📡 LIVE — '+SID;setLive(true);}
        else if(m.event==='publisher_offline'){lbl.textContent='Stream ended';fps.textContent='';setLive(false);}
        return;
      }
      if(prev)URL.revokeObjectURL(prev);
      var b=new Blob([e.data],{type:'image/jpeg'});
      prev=URL.createObjectURL(b);frm.src=prev;
      frames++;
      var n=Date.now();
      if(n-t0>=1000){fps.textContent=frames+' FPS';frames=0;t0=n;}
    };
    ws.onerror=function(){lbl.textContent='Connection error — retrying…';};
    ws.onclose=function(){setLive(false);lbl.textContent='Reconnecting…';setTimeout(connect,3000);};
  }
  connect();
})();
</script>
</body>
</html>"""


def _purge_stale_heartbeat_state(now: Optional[float] = None) -> set:
    now = now or time.time()
    stale = {u for u, t in list(last_heartbeat.items()) if (now - t) >= ONLINE_TIMEOUT_SEC}
    for u in stale:
        last_heartbeat.pop(u, None)
        _heartbeat_device_ids.pop(u, None)
        _heartbeat_lora_names.pop(u, None)
        _heartbeat_lora_ids.pop(u, None)
    return stale


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, mqtt_sub, minio_client

    if RELAY_MODE:
        logging.info("=" * 60)
        logging.info("  ATAK_MI_Server  —  RELAY MODE (cloud)")
        logging.info("  WebSocket relay only. No DB / MinIO / MQTT / UDP.")
        logging.info("=" * 60)
    else:
        logging.info("=" * 60)
        logging.info("  ATAK_MI_Server  —  FULL MODE (local)")
        logging.info("=" * 60)

        # 1. PostgreSQL
        db_pool = await asyncpg.create_pool(
            host=PG_HOST, port=PG_PORT,
            user=PG_USER, password=PG_PASS,
            database=PG_DB, min_size=2, max_size=10,
        )
        logging.info("[DB] PostgreSQL pool ready")

        # Schema migrations — safe on every restart (IF NOT EXISTS)
        try:
            await db_pool.execute(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS device_msg_id TEXT"
            )
            await db_pool.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_device_msg_id
                ON messages(device_msg_id)
                WHERE device_msg_id IS NOT NULL
                """
            )
            # Drawings table (Drawing Tools tool)
            await db_pool.execute("""
                CREATE TABLE IF NOT EXISTS drawings (
                    id           BIGSERIAL PRIMARY KEY,
                    drawing_uid  TEXT        NOT NULL,
                    device_id    TEXT        NOT NULL,
                    username     TEXT        NOT NULL,
                    room         TEXT        NOT NULL DEFAULT 'lobby',
                    drawing_type TEXT        NOT NULL DEFAULT 'freehand',
                    color        TEXT        NOT NULL DEFAULT '#FF0000',
                    label        TEXT,
                    geojson      JSONB       NOT NULL DEFAULT '{}',
                    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    synced_to_t2 BOOLEAN     NOT NULL DEFAULT FALSE,
                    UNIQUE(drawing_uid)
                )
            """)
            await db_pool.execute("CREATE INDEX IF NOT EXISTS idx_drawings_room ON drawings(room)")
            await db_pool.execute("CREATE INDEX IF NOT EXISTS idx_drawings_ts ON drawings(ts DESC)")
            # Device settings table (Night Vision, Grid, ADSB, Track History toggles)
            await db_pool.execute("""
                CREATE TABLE IF NOT EXISTS device_settings (
                    username   TEXT        NOT NULL PRIMARY KEY,
                    settings   JSONB       NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            logging.info("[DB] Schema migrations applied (device_msg_id, drawings, device_settings)")
        except Exception as _e:
            logging.warning(f"[DB] Migration warning (non-fatal): {_e}")

        # 2. MinIO
        from minio import Minio
        minio_client = Minio(
            f"{MINIO_ENDPOINT}:{MINIO_PORT}",
            access_key=MINIO_ACCESS,
            secret_key=MINIO_SECRET,
            secure=MINIO_SSL,
        )
        for bucket in BUCKETS:
            if not minio_client.bucket_exists(bucket):
                minio_client.make_bucket(bucket)
        logging.info("[MinIO] Buckets ready")

        # 3. UDP Discovery
        discovery.start()
        logging.info("[UDP] Discovery service started on port 8091")

        # 4. MQTT (optional even in full mode)
        if MQTT_ENABLED:
            from mqtt_service import MQTTSubscriber
            _host = os.getenv("MQTT_HOST", "localhost")
            _port = int(os.getenv("MQTT_PORT", 1883))
            logging.info(f"[MQTT] Connecting to {_host}:{_port} ...")
            mqtt_sub = MQTTSubscriber(broker=_host, port=_port, db_pool=db_pool, ws_manager=manager)
            mqtt_sub.start(loop=asyncio.get_event_loop())
            logging.info("[MQTT] Bridge started")
        else:
            logging.info("[MQTT] Disabled (set MQTT_ENABLED=true to enable)")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if not RELAY_MODE:
        discovery.stop()
        if mqtt_sub:
            mqtt_sub.stop()
        if db_pool:
            await db_pool.close()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="ATAK_MI_Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _require_db():
    """Raise 503 if the server is running in relay mode (no local DB)."""
    if RELAY_MODE or db_pool is None:
        raise HTTPException(503, detail="This endpoint requires the local server (RELAY_MODE is active).")


def _require_minio():
    if RELAY_MODE or minio_client is None:
        raise HTTPException(503, detail="Media storage is only available on the local server.")


# ── Auth ──────────────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(user: UserCreate):
    _require_db()
    hashed = hash_password(user.password)
    try:
        await db_pool.execute(
            "INSERT INTO users (username, password_hash, role) VALUES ($1, $2, $3)",
            user.username, hashed, user.role,
        )
        token = create_access_token({"sub": user.username})
        return {"token": token, "username": user.username, "role": user.role}
    except IntegrityError:
        raise HTTPException(400, "Username already exists")


@app.post("/api/auth/login")
async def login(user: UserLogin):
    _require_db()
    row = await db_pool.fetchrow(
        "SELECT password_hash, role FROM users WHERE username = $1", user.username
    )
    if not row or not verify_password(user.password, row["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token({"sub": user.username})
    return {"token": token, "username": user.username, "role": row["role"]}


@app.get("/api/auth/me")
async def get_me(request: Request):
    """JWT validation — stateless, works in both relay and full mode."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_token(auth[7:])
        if payload:
            username = payload.get("sub")
            if username:
                return {"username": username, "sub": username}
    raise HTTPException(401, "Unauthorized")


# ── Tactical REST (write + broadcast) ────────────────────────────────────────
# In relay mode the DB write is skipped; the broadcast still fires so connected
# clients receive the event in real time.

@app.post("/api/location", status_code=201)
async def post_location(body: LocationIn):
    if db_pool:
        await db_pool.execute(
            """INSERT INTO locations (device_id, username, room, latitude, longitude, accuracy_m, altitude_m, ts)
               VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8::timestamptz, NOW()))""",
            body.device_id, body.username, body.room,
            body.latitude, body.longitude, body.accuracy_m, body.altitude_m, body.ts,
        )
    await manager.broadcast(body.room, {
        "type": "location",
        "user": body.username,
        "room": body.room,
        "lat":  body.latitude,
        "lon":  body.longitude,
        "ts":   body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


@app.post("/api/message", status_code=201)
async def post_message(body: MessageIn):
    if db_pool:
        await db_pool.execute(
            """INSERT INTO messages (device_id, username, room, message_text, ts)
               VALUES ($1, $2, $3, $4, COALESCE($5::timestamptz, NOW()))""",
            body.device_id, body.username, body.room, body.message_text, body.ts,
        )
    await manager.broadcast(body.room, {
        "type": "message",
        "user": body.username,
        "room": body.room,
        "text": body.message_text,
        "ts":   body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


@app.post("/api/marker", status_code=201)
async def post_marker(body: MarkerIn):
    if db_pool:
        await db_pool.execute(
            """INSERT INTO markers
                 (marker_uid, device_id, username, room, marker_type, color, latitude, longitude, label, ts)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, COALESCE($10::timestamptz, NOW()))
               ON CONFLICT (marker_uid) DO UPDATE SET
                 latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude,
                 color=EXCLUDED.color, label=EXCLUDED.label""",
            body.marker_uid, body.device_id, body.username, body.room,
            body.marker_type, body.color, body.latitude, body.longitude, body.label, body.ts,
        )
    await manager.broadcast(body.room, {
        "type":       "marker",
        "user":       body.username,
        "room":       body.room,
        "id":         body.marker_uid,
        "markerType": body.marker_type,
        "color":      body.color,
        "lat":        body.latitude,
        "lon":        body.longitude,
        "ts":         body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


@app.post("/api/zone", status_code=201)
async def post_zone(body: ZoneIn):
    if db_pool:
        await db_pool.execute(
            """INSERT INTO zones
                 (zone_uid, device_id, username, room, status, latitude, longitude, radius_m, description, ts)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, COALESCE($10::timestamptz, NOW()))
               ON CONFLICT (zone_uid) DO UPDATE SET
                 status=EXCLUDED.status, radius_m=EXCLUDED.radius_m, description=EXCLUDED.description""",
            body.zone_uid, body.device_id, body.username, body.room,
            body.status, body.latitude, body.longitude, body.radius_m, body.description, body.ts,
        )
    await manager.broadcast(body.room, {
        "type":   "zone",
        "user":   body.username,
        "room":   body.room,
        "id":     body.zone_uid,
        "status": body.status,
        "lat":    body.latitude,
        "lon":    body.longitude,
        "radius": body.radius_m,
        "ts":     body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


@app.post("/api/alert", status_code=201)
async def post_alert(body: AlertIn):
    if db_pool:
        await db_pool.execute(
            """INSERT INTO alerts (device_id, username, room, alert_type, alert_text, ts)
               VALUES ($1, $2, $3, $4, $5, COALESCE($6::timestamptz, NOW()))""",
            body.device_id, body.username, body.room, body.alert_type, body.alert_text, body.ts,
        )
    await manager.broadcast(body.room, {
        "type":      "alert",
        "user":      body.username,
        "room":      body.room,
        "alertType": body.alert_type,
        "text":      body.alert_text,
        "ts":        body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


# ── Routes ───────────────────────────────────────────────────────────────────
@app.post("/api/route", status_code=201)
async def post_route(body: RouteIn):
    import json as _json
    if db_pool:
        waypoints_json = _json.dumps(body.waypoints) if not isinstance(body.waypoints, str) else body.waypoints
        await db_pool.execute(
            """INSERT INTO routes
                 (route_uid, device_id, username, room, route_name, waypoints, total_dist_m, ts)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,COALESCE($8::timestamptz, NOW()))
               ON CONFLICT (route_uid) DO UPDATE SET
                 route_name=EXCLUDED.route_name, waypoints=EXCLUDED.waypoints,
                 total_dist_m=EXCLUDED.total_dist_m""",
            body.route_uid, body.device_id, body.username, body.room,
            body.route_name, waypoints_json, body.total_dist_m, body.ts,
        )
    await manager.broadcast(body.room, {
        "type": "route", "user": body.username, "room": body.room,
        "id": body.route_uid, "name": body.route_name,
        "ts": body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


@app.get("/api/routes")
async def get_routes(room: str = Query(None), limit: int = Query(200)):
    if RELAY_MODE or not db_pool:
        return {"routes": []}
    if room:
        rows = await db_pool.fetch(
            "SELECT id,route_uid,device_id,username,room,route_name,waypoints,total_dist_m,ts FROM routes WHERE room=$1 ORDER BY ts DESC LIMIT $2",
            room, limit,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id,route_uid,device_id,username,room,route_name,waypoints,total_dist_m,ts FROM routes ORDER BY ts DESC LIMIT $1",
            limit,
        )
    def _s(v): return v.isoformat() if isinstance(v, datetime) else v
    return {"routes": [{k: _s(v) for k, v in dict(r).items()} for r in rows]}


# ── Measurements ──────────────────────────────────────────────────────────────
@app.post("/api/measurement", status_code=201)
async def post_measurement(body: MeasurementIn):
    if db_pool:
        await db_pool.execute(
            """INSERT INTO measurements
                 (measure_uid, device_id, username, room, start_lat, start_lon, end_lat, end_lon, distance_m, ts)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,COALESCE($10::timestamptz, NOW()))
               ON CONFLICT (measure_uid) DO UPDATE SET
                 start_lat=EXCLUDED.start_lat, start_lon=EXCLUDED.start_lon,
                 end_lat=EXCLUDED.end_lat, end_lon=EXCLUDED.end_lon,
                 distance_m=EXCLUDED.distance_m""",
            body.measure_uid, body.device_id, body.username, body.room,
            body.start_lat, body.start_lon, body.end_lat, body.end_lon,
            body.distance_m, body.ts,
        )
    await manager.broadcast(body.room, {
        "type": "measurement", "user": body.username, "room": body.room,
        "id": body.measure_uid, "distance_m": body.distance_m,
        "ts": body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


# ── Drawings ──────────────────────────────────────────────────────────────────
@app.post("/api/drawing", status_code=201)
async def post_drawing(body: DrawingIn):
    import json as _json
    if db_pool:
        geojson_str = _json.dumps(body.geojson) if not isinstance(body.geojson, str) else body.geojson
        await db_pool.execute(
            """INSERT INTO drawings
                 (drawing_uid, device_id, username, room, drawing_type, color, label, geojson, ts)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,COALESCE($9::timestamptz, NOW()))
               ON CONFLICT (drawing_uid) DO UPDATE SET
                 drawing_type=EXCLUDED.drawing_type, color=EXCLUDED.color,
                 label=EXCLUDED.label, geojson=EXCLUDED.geojson""",
            body.drawing_uid, body.device_id, body.username, body.room,
            body.drawing_type, body.color, body.label, geojson_str, body.ts,
        )
    await manager.broadcast(body.room, {
        "type": "drawing", "user": body.username, "room": body.room,
        "id": body.drawing_uid, "drawingType": body.drawing_type,
        "color": body.color, "ts": body.ts or datetime.now().isoformat(),
    })
    return {"success": True}


@app.delete("/api/drawing/{drawing_uid}", status_code=200)
async def delete_drawing(drawing_uid: str):
    if db_pool:
        await db_pool.execute("DELETE FROM drawings WHERE drawing_uid=$1", drawing_uid)
    return {"success": True, "drawing_uid": drawing_uid}


@app.get("/api/drawings")
async def get_drawings(room: str = Query(None), limit: int = Query(200)):
    if RELAY_MODE or not db_pool:
        return {"drawings": []}
    if room:
        rows = await db_pool.fetch(
            "SELECT id,drawing_uid,device_id,username,room,drawing_type,color,label,geojson,ts FROM drawings WHERE room=$1 ORDER BY ts DESC LIMIT $2",
            room, limit,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id,drawing_uid,device_id,username,room,drawing_type,color,label,geojson,ts FROM drawings ORDER BY ts DESC LIMIT $1",
            limit,
        )
    def _s(v): return v.isoformat() if isinstance(v, datetime) else v
    return {"drawings": [{k: _s(v) for k, v in dict(r).items()} for r in rows]}


# ── Device Settings (toggle state persistence) ────────────────────────────────
@app.get("/api/device-settings/{username}")
async def get_device_settings(username: str):
    if RELAY_MODE or not db_pool:
        return {"username": username, "settings": {}}
    row = await db_pool.fetchrow(
        "SELECT settings FROM device_settings WHERE LOWER(username)=LOWER($1)", username
    )
    return {"username": username, "settings": dict(row["settings"]) if row else {}}


@app.put("/api/device-settings/{username}", status_code=200)
async def put_device_settings(username: str, body: dict):
    if db_pool:
        import json as _json
        await db_pool.execute(
            """INSERT INTO device_settings (username, settings, updated_at)
               VALUES ($1, $2::jsonb, NOW())
               ON CONFLICT (username) DO UPDATE
                 SET settings=device_settings.settings || EXCLUDED.settings,
                     updated_at=NOW()""",
            username, _json.dumps(body),
        )
    return {"success": True, "username": username}


# ── Media (local only — requires MinIO) ──────────────────────────────────────
def _upload_to_minio(bucket: str, room: str, username: str, ext: str, data: bytes, mime: str) -> str:
    key = f"{room}/{username}/{uuid.uuid4()}.{ext}"
    minio_client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=mime)
    return key


@app.post("/api/media/{mtype}", status_code=201)
async def upload_media(
    mtype: str,
    device_id: str   = Form(...),
    username:  str   = Form(...),
    room:      str   = Form("lobby"),
    file:      UploadFile = File(...),
):
    _require_minio()
    if mtype not in ("image", "video", "audio"):
        raise HTTPException(400, "Invalid media type")
    bucket = f"atak-{mtype}s"
    data   = await file.read()
    ext    = (file.filename or "file").rsplit(".", 1)[-1].lower()
    mime   = file.content_type or "application/octet-stream"
    loop   = asyncio.get_event_loop()
    obj_key = await loop.run_in_executor(None, _upload_to_minio, bucket, room, username, ext, data, mime)
    await db_pool.execute(
        """INSERT INTO media (device_id, username, room, media_type, bucket_name, object_key, file_size_b, mime_type)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        device_id, username, room, mtype, bucket, obj_key, len(data), mime,
    )
    await manager.broadcast(room, {
        "type": mtype,
        "user": username,
        "room": room,
        "url":  f"/api/media/file?bucket={bucket}&key={obj_key}",
        "ts":   datetime.now().isoformat(),
    })
    return {"success": True, "object_key": obj_key}


@app.post("/api/media/upload-base64")
async def upload_media_base64(body: dict):
    _require_minio()
    data_b64   = body.get("data", "")
    media_type = body.get("mediaType", "image")
    username   = body.get("username", "unknown")
    room       = body.get("room", "lobby")
    if media_type not in ("image", "video", "audio"):
        raise HTTPException(400, "Invalid mediaType")
    try:
        data = base64.b64decode(data_b64)
    except Exception:
        raise HTTPException(400, "Invalid base64 data")
    ext_map  = {"image": "jpg",        "video": "mp4",       "audio": "m4a"}
    mime_map = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/m4a"}
    ext      = ext_map[media_type]
    mime     = mime_map[media_type]
    bucket   = f"atak-{media_type}s"
    media_id = str(uuid.uuid4())
    obj_key  = f"{room}/{username}/{media_id}.{ext}"
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: minio_client.put_object(bucket, obj_key, io.BytesIO(data), length=len(data), content_type=mime),
        )
    except Exception as e:
        raise HTTPException(500, f"MinIO upload failed: {e}")
    await db_pool.execute(
        """INSERT INTO media (device_id, username, room, media_type, bucket_name, object_key, file_size_b, mime_type)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        "APP-B64", username, room, media_type, bucket, obj_key, len(data), mime,
    )
    return {"mediaId": media_id, "object_key": obj_key,
            "url": f"/api/media/file?bucket={bucket}&key={obj_key}"}


@app.get("/api/media/file")
async def stream_file(bucket: str = Query(...), key: str = Query(...)):
    _require_minio()
    try:
        stat = minio_client.stat_object(bucket, key)
        obj  = minio_client.get_object(bucket, key)
        return StreamingResponse(obj, media_type=stat.content_type)
    except Exception:
        raise HTTPException(404, "File not found")


# ── Batch message sync (Android ChatSyncWorker) ───────────────────────────────
@app.post("/api/messages/batch")
async def batch_messages(body: dict):
    if RELAY_MODE or not db_pool:
        # In relay mode acknowledge everything so the Android worker stops retrying.
        messages = body.get("messages", [])
        return {"syncedIds": [m.get("msgId", str(uuid.uuid4())) for m in messages], "failedIds": []}

    messages   = body.get("messages", [])
    synced_ids = []
    for msg in messages:
        msg_id = msg.get("msgId") or str(uuid.uuid4())
        try:
            sender       = (msg.get("sender") or "").strip()
            room         = (msg.get("room")   or "lobby").strip()
            text         = (msg.get("text")   or "").strip()
            message_type = msg.get("messageType", "text")
            ts_ms        = msg.get("timestamp", 0)
            ts           = datetime.fromtimestamp(ts_ms / 1000.0) if ts_ms else datetime.now()
            if message_type == "text" and text:
                await db_pool.execute(
                    """INSERT INTO messages
                           (device_id, username, room, message_text, ts, device_msg_id)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (device_msg_id) WHERE device_msg_id IS NOT NULL
                       DO NOTHING""",
                    msg_id[:50], sender, room, text, ts, msg_id,
                )
        except Exception as e:
            logging.warning(f"[batch_messages] {msg_id}: {e}")
        synced_ids.append(msg_id)
    return {"syncedIds": synced_ids, "failedIds": []}


# ── Read endpoints ────────────────────────────────────────────────────────────
@app.get("/api/messages")
async def get_messages(room: str = Query(None), since: float = Query(0)):
    if RELAY_MODE or not db_pool:
        return {"messages": []}   # relay has no history
    since_sec = since / 1000.0
    if room:
        rows = await db_pool.fetch(
            """SELECT id, username, room, message_text, ts, device_msg_id
               FROM messages WHERE room=$1 AND ts > to_timestamp($2)
               ORDER BY ts ASC LIMIT 100""",
            room, since_sec,
        )
    else:
        rows = await db_pool.fetch(
            """SELECT id, username, room, message_text, ts, device_msg_id
               FROM messages WHERE ts > to_timestamp($1)
               ORDER BY ts ASC LIMIT 100""",
            since_sec,
        )
    return {"messages": [
        {"msgId": r["device_msg_id"] or str(r["id"]), "sender": r["username"], "room": r["room"],
         "text": r["message_text"], "messageType": "text",
         "timestamp": int(r["ts"].timestamp() * 1000)}
        for r in rows
    ]}


@app.post("/api/messages/dedup", status_code=200)
async def dedup_messages(room: str = Query(None)):
    """
    Remove duplicate messages from the messages table, keeping the earliest
    copy of each unique (username, room, message_text) fingerprint.
    Optionally scoped to a single room via ?room=lobby.
    Returns how many duplicate rows were deleted.
    """
    _require_db()
    try:
        if room:
            result = await db_pool.execute(
                """
                DELETE FROM messages
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM   messages
                    WHERE  room = $1
                    GROUP  BY username, room, message_text
                )
                AND room = $1
                """,
                room,
            )
        else:
            result = await db_pool.execute(
                """
                DELETE FROM messages
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM   messages
                    GROUP  BY username, room, message_text
                )
                """
            )
        # asyncpg returns "DELETE N" as a string
        deleted = int(result.split()[-1]) if result else 0
        return {"deleted": deleted, "message": f"Removed {deleted} duplicate message(s)"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/messages", status_code=200)
async def clear_messages(room: str = Query(None)):
    """
    Hard-delete all messages, or only messages in a specific room.
    Pass ?room=lobby to clear only that room; omit for a full wipe.
    Returns how many rows were deleted.
    """
    _require_db()
    try:
        if room:
            result = await db_pool.execute(
                "DELETE FROM messages WHERE room = $1", room
            )
        else:
            result = await db_pool.execute("DELETE FROM messages")
        deleted = int(result.split()[-1]) if result else 0
        return {"deleted": deleted, "message": f"Cleared {deleted} message(s)"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/users/all")
async def get_all_registered_users():
    ws_users   = set(manager.get_online_users())
    now        = time.time()
    hb_users   = {u for u, t in last_heartbeat.items() if now - t < ONLINE_TIMEOUT_SEC}
    online_set = ws_users | hb_users

    if RELAY_MODE or not db_pool:
        # Return only currently connected users; no full user table available.
        return {"users": [{"username": u, "online": True} for u in online_set], "count": len(online_set)}

    rows = await db_pool.fetch("SELECT username FROM users ORDER BY username")
    return {"users": [{"username": r["username"], "online": r["username"] in online_set} for r in rows],
            "count": len(rows)}


# ── Presence / Heartbeat ──────────────────────────────────────────────────────
async def _resolve_heartbeat_username(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_token(auth[7:])
        if payload:
            return payload.get("sub"), "", "", ""
    try:
        body = await request.json()
        return (body.get("username"), body.get("device_id", ""),
                body.get("lora_name", ""), body.get("lora_id", ""))
    except Exception:
        return None, "", "", ""


def _apply_heartbeat(username, device_id, lora_name, lora_id):
    if not username:
        return
    last_heartbeat[username] = time.time()
    if device_id:
        _heartbeat_device_ids[username] = device_id
    if device_id == "TRIDENT-SERVER":
        if lora_name:
            _heartbeat_lora_names[username] = lora_name
        else:
            _heartbeat_lora_names.pop(username, None)
        if lora_id:
            _heartbeat_lora_ids[username] = lora_id
        else:
            _heartbeat_lora_ids.pop(username, None)


@app.post("/api/auth/heartbeat",     status_code=200)
@app.post("/api/presence/heartbeat", status_code=200)
async def heartbeat(request: Request):
    username, device_id, lora_name, lora_id = await _resolve_heartbeat_username(request)
    _apply_heartbeat(username, device_id, lora_name, lora_id)
    if username:
        now = time.time()
        _purge_stale_heartbeat_state(now)
        hb_users  = {u for u, t in last_heartbeat.items() if now - t < ONLINE_TIMEOUT_SEC}
        all_users = list(set(manager.get_online_users()) | hb_users)
        await manager.broadcast_user_list_with_hb(all_users)
    return {"success": True}


@app.get("/api/presence/online")
async def get_online_users():
    ws_users = set(manager.get_online_users())
    now      = time.time()
    _purge_stale_heartbeat_state(now)
    hb_users = {u for u, t in last_heartbeat.items() if now - t < ONLINE_TIMEOUT_SEC}
    result   = []
    for username in list(ws_users | hb_users):
        last_seen_ts = last_heartbeat.get(username)
        result.append({
            "username":  username,
            "device_id": _heartbeat_device_ids.get(username, ""),
            "lora_name": _heartbeat_lora_names.get(username, ""),
            "room":      "",
            "last_seen": datetime.fromtimestamp(last_seen_ts).isoformat() if last_seen_ts else None,
            "online":    True,
        })
    return {"online_users": result, "count": len(result)}


# ── LoRa / Meshtastic ─────────────────────────────────────────────────────────
@app.get("/api/lora/status")
def lora_status():
    if RELAY_MODE:
        return {"connected": False, "port": None, "node_name": None}
    return lora_detector.get_status()


@app.get("/api/lora/registry")
async def get_lora_registry():
    now = time.time()
    _purge_stale_heartbeat_state(now)
    result = []
    for username, lora_id in _heartbeat_lora_ids.items():
        if not lora_id:
            continue
        last_seen_ts = last_heartbeat.get(username)
        online = bool(last_seen_ts and (now - last_seen_ts) < ONLINE_TIMEOUT_SEC)
        if not online:
            continue
        lora_name = _heartbeat_lora_names.get(username, "")
        name = username
        if lora_name and lora_name != username:
            name = f"{username} ({lora_name})"
        if lora_id:
            name = f"{name} ({lora_id})"
        result.append({
            "trident_name": username,
            "lora_id":      lora_id,
            "lora_name":    lora_name,
            "name":         name,
            "last_seen":    datetime.fromtimestamp(last_seen_ts).isoformat() if last_seen_ts else None,
            "online":       True,
        })
    result.sort(key=lambda x: (not x["online"], x["trident_name"].lower()))
    return {"registry": result, "count": len(result)}


# ── System ────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "ok", "relay_mode": RELAY_MODE}


@app.get("/api/config")
def get_config():
    """Returns the public server URL from SERVER_URL env var."""
    return {"server_url": SERVER_URL}


# ── Maritime sync export (local only) ─────────────────────────────────────────
_EXPORT_QUERIES = {
    "messages":     "SELECT id,device_id,username,room,message_text,ts FROM messages ORDER BY ts DESC LIMIT 500",
    "locations":    "SELECT id,device_id,username,room,latitude,longitude,accuracy_m,ts FROM locations ORDER BY ts DESC LIMIT 500",
    "alerts":       "SELECT id,device_id,username,room,alert_type,alert_text,ts FROM alerts ORDER BY ts DESC LIMIT 500",
    "markers":      "SELECT id,marker_uid,device_id,username,room,marker_type,color,latitude,longitude,label,ts FROM markers ORDER BY ts DESC LIMIT 500",
    "zones":        "SELECT id,zone_uid,device_id,username,room,status,latitude,longitude,radius_m,description,ts FROM zones ORDER BY ts DESC LIMIT 500",
    "media":        "SELECT id,device_id,username,room,media_type,bucket_name AS bucket,object_key,file_size_b AS file_size_bytes,mime_type,ts FROM media ORDER BY ts DESC LIMIT 100",
    "routes":       "SELECT id,route_uid,device_id,username,room,route_name,waypoints,total_dist_m,ts FROM routes ORDER BY ts DESC LIMIT 200",
    "measurements": "SELECT id,measure_uid,device_id,username,room,start_lat,start_lon,end_lat,end_lon,distance_m,ts FROM measurements ORDER BY ts DESC LIMIT 500",
    "drawings":     "SELECT id,drawing_uid,device_id,username,room,drawing_type,color,label,geojson,ts FROM drawings ORDER BY ts DESC LIMIT 200",
}


@app.get("/api/export/unsynced")
async def export_unsynced(table: str = Query(...)):
    _require_db()
    if table not in _EXPORT_QUERIES:
        raise HTTPException(400, f"Unknown table: {table}")
    try:
        rows = await db_pool.fetch(_EXPORT_QUERIES[table])
        def _s(v):
            return v.isoformat() if isinstance(v, datetime) else v
        return {"rows": [{k: _s(v) for k, v in dict(r).items()} for r in rows]}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/export/mark-synced")
async def mark_synced(body: dict):
    return {"success": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/chat")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(None)):
    await websocket.accept()

    username = "Guest"
    if token:
        payload = decode_token(token)
        if payload:
            username = payload.get("sub", "Guest")

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=25.0)
            except asyncio.TimeoutError:
                # Keepalive: push current user list
                now      = time.time()
                hb_users = {u for u, t in last_heartbeat.items() if now - t < ONLINE_TIMEOUT_SEC}
                all_users = list(set(manager.get_online_users()) | hb_users)
                await websocket.send_text(json.dumps({"type": "users", "users": all_users}))
                continue

            msg      = json.loads(data)
            msg_type = msg.get("type", "message")

            if msg_type == "pong":
                continue

            room = msg.get("room", "lobby")
            user = msg.get("user", username)

            # ── Join / subscribe ──────────────────────────────────────────────
            if msg_type in ("join", "subscribe"):
                await manager.connect(websocket, user, room)

                if db_pool:
                    # Send last 50 messages — use device_msg_id so Android
                    # Room DB dedup (IGNORE on msg_id) correctly suppresses
                    # messages the device already has locally.
                    msgs = await db_pool.fetch(
                        "SELECT id, username, message_text, ts, device_msg_id FROM messages WHERE room=$1 ORDER BY ts DESC LIMIT 50",
                        room,
                    )
                    for m in reversed(msgs):
                        await manager.send_personal_message({
                            "type":  "message",
                            "msgId": m["device_msg_id"] or f"srv-{m['id']}",
                            "user":  m["username"],
                            "room":  room,
                            "text":  m["message_text"],
                            "ts":    m["ts"].timestamp() * 1000,
                        }, websocket)

                    # Send active markers
                    marks = await db_pool.fetch(
                        "SELECT marker_uid,username,marker_type,color,latitude,longitude,label,ts "
                        "FROM markers WHERE room=$1",
                        room,
                    )
                    for m in marks:
                        await manager.send_personal_message({
                            "type":       "marker",
                            "user":       m["username"],
                            "room":       room,
                            "id":         m["marker_uid"],
                            "markerType": m["marker_type"],
                            "color":      m["color"],
                            "lat":        m["latitude"],
                            "lon":        m["longitude"],
                            "label":      m["label"],
                            "ts":         m["ts"].timestamp() * 1000,
                        }, websocket)

                await manager.broadcast(room, {"type": "join", "user": user, "room": room}, exclude=websocket)

            # ── Location ──────────────────────────────────────────────────────
            elif msg_type == "location":
                lat, lon   = msg.get("lat"), msg.get("lon")
                loc_msg_id = msg.get("msgId", "")
                if lat is not None and lon is not None:
                    if db_pool:
                        try:
                            await db_pool.execute(
                                """INSERT INTO locations (device_id, username, room, latitude, longitude)
                                   VALUES ($1,$2,$3,$4,$5)""",
                                loc_msg_id[:50] if loc_msg_id else "WS", user, room, float(lat), float(lon),
                            )
                        except Exception as e:
                            logging.warning(f"WS location DB error: {e}")
                    if mqtt_sub:
                        mqtt_sub.publish_location(room, user, float(lat), float(lon))
                    broadcast = {"type": "location", "user": user, "room": room,
                                 "lat": lat, "lon": lon, "ts": datetime.now().timestamp() * 1000}
                    if loc_msg_id:
                        broadcast["msgId"] = loc_msg_id
                    await manager.broadcast(room, broadcast, exclude=websocket)

            # ── Chat message ──────────────────────────────────────────────────
            elif msg_type == "message":
                text   = msg.get("text", "")[:150]
                msg_id = msg.get("msgId", "")
                if text:
                    if db_pool:
                        try:
                            await db_pool.execute(
                                """INSERT INTO messages
                                       (device_id, username, room, message_text, device_msg_id)
                                   VALUES ($1,$2,$3,$4,$5)
                                   ON CONFLICT (device_msg_id) WHERE device_msg_id IS NOT NULL
                                   DO NOTHING""",
                                msg_id[:50] if msg_id else "WS", user, room, text,
                                msg_id or None,
                            )
                        except Exception as e:
                            logging.warning(f"WS message DB error: {e}")
                    if mqtt_sub:
                        mqtt_sub.publish_chat(room, user, text)
                    broadcast = {"type": "message", "user": user, "room": room,
                                 "text": text, "ts": datetime.now().timestamp() * 1000}
                    if msg_id:
                        broadcast["msgId"] = msg_id
                    await manager.broadcast(room, broadcast, exclude=websocket)

            # ── Pass-through relay ────────────────────────────────────────────
            # Any unrecognised message type (ps_target, adsb, alert, custom events)
            # is forwarded as-is to all room members. This is essential for the
            # Point & Shoot target data to reach connected Trident operators.
            else:
                await manager.broadcast(room, msg, exclude=websocket)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logging.warning(f"WS error: {e}")
        manager.disconnect(websocket)


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE STREAMING  —  follows the ServerStream-main publish/subscribe protocol
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/live-streams")
async def list_live_streams():
    """Return the list of currently active live streams."""
    return {
        "streams": [
            {
                "user":       sid,
                "url":        f"/live/{sid}",
                "started_at": info["started_at"],
                "viewers":    len(_stream_subscribers.get(sid, set())),
            }
            for sid, info in _active_streams.items()
        ]
    }


@app.get("/live/{stream_id}")
async def stream_viewer_page(stream_id: str):
    """
    Serve the in-app WebView viewer page.
    The page opens a WebSocket subscriber connection to /stream internally.
    """
    html = _LIVE_VIEWER_HTML.replace("__STREAM_ID__", stream_id)
    return HTMLResponse(html)


@app.websocket("/stream")
async def stream_ws(websocket: WebSocket):
    """
    WebSocket streaming broker — mirrors the ServerStream-main protocol.

    Publisher  registration (first text frame):
        {"action": "publish",   "stream_id": "<username>", "token": "<jwt>"}
    Subscriber registration (first text frame):
        {"action": "subscribe", "stream_id": "<username>", "token": "<jwt>"}

    Server confirms:
        {"status": "ok", "role": "publisher|subscriber", "stream_id": "..."}

    Publisher then streams raw binary JPEG frames (no framing header).
    Server forwards every frame to all subscribers of that stream_id.
    On publisher disconnect all subscribers receive:
        {"event": "publisher_offline", "stream_id": "..."}
    """
    await websocket.accept()
    stream_id: str = ""
    role:      str = ""        # "pub" | "sub"
    username:  str = "unknown"

    try:
        # ── Handshake — first text frame must be a registration JSON ─────────
        raw      = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        payload  = json.loads(raw)
        action   = payload.get("action", "")
        stream_id = str(payload.get("stream_id", "")).strip()
        token    = payload.get("token",     "")

        if not stream_id or action not in ("publish", "subscribe"):
            await websocket.send_text(json.dumps({
                "status":  "error",
                "message": "Required fields: action (publish|subscribe), stream_id.",
            }))
            await websocket.close()
            return

        # Optional JWT auth — fall back to stream_id as username
        from auth import decode_token as _dec
        decoded  = _dec(token) if token else None
        username = decoded["sub"] if (decoded and "sub" in decoded) else stream_id

        # ── PUBLISHER path ───────────────────────────────────────────────────
        if action == "publish":
            role = "pub"
            _active_streams[stream_id] = {
                "streamer":   username,
                "started_at": datetime.utcnow().isoformat(),
            }
            _stream_subscribers.setdefault(stream_id, set())

            await websocket.send_text(json.dumps({
                "status":    "ok",
                "role":      "publisher",
                "stream_id": stream_id,
            }))

            # Notify all chat-connected ATAK clients so they see the LIVE banner
            await manager.broadcast("lobby", {
                "type":      "ps_stream",
                "user":      username,
                "stream_id": stream_id,
                "url":       f"/live/{stream_id}",
                "source":    "device_camera",
                "lat":       0.0,
                "lon":       0.0,
                "ts":        datetime.utcnow().isoformat(),
            })
            logging.info(f"[Stream] Publisher online: stream_id={stream_id} user={username}")

            # Frame relay loop — forward every binary frame to all subscribers
            while True:
                frame = await websocket.receive_bytes()
                subs  = list(_stream_subscribers.get(stream_id, set()))
                dead: list = []
                for sub_ws in subs:
                    try:
                        await sub_ws.send_bytes(frame)
                    except Exception:
                        dead.append(sub_ws)
                for d in dead:
                    _stream_subscribers[stream_id].discard(d)

        # ── SUBSCRIBER path ──────────────────────────────────────────────────
        else:
            role = "sub"
            _stream_subscribers.setdefault(stream_id, set())
            _stream_subscribers[stream_id].add(websocket)

            if stream_id in _active_streams:
                await websocket.send_text(json.dumps({
                    "status":    "ok",
                    "role":      "subscriber",
                    "stream_id": stream_id,
                }))
            else:
                await websocket.send_text(json.dumps({
                    "event":     "publisher_offline",
                    "stream_id": stream_id,
                }))
            logging.info(f"[Stream] Subscriber joined: stream_id={stream_id}")

            # Keep connection alive; server pings every 30 s
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        break

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        logging.warning(f"[Stream WS] Unexpected error: {exc}")
        try:
            await websocket.send_text(json.dumps({"status": "error", "message": str(exc)}))
        except Exception:
            pass

    finally:
        # ── Cleanup ──────────────────────────────────────────────────────────
        if role == "pub" and stream_id:
            _active_streams.pop(stream_id, None)
            subs = _stream_subscribers.pop(stream_id, set())
            offline = json.dumps({"event": "publisher_offline", "stream_id": stream_id})
            for sub_ws in subs:
                try:
                    await sub_ws.send_text(offline)
                except Exception:
                    pass
            logging.info(f"[Stream] Publisher offline: stream_id={stream_id}")

            # Notify chat clients so they can hide the LIVE banner
            await manager.broadcast("lobby", {
                "type":      "ps_stream_ended",
                "user":      username,
                "stream_id": stream_id,
                "ts":        datetime.utcnow().isoformat(),
            })

        elif role == "sub" and stream_id:
            if stream_id in _stream_subscribers:
                _stream_subscribers[stream_id].discard(websocket)
            logging.info(f"[Stream] Subscriber left: stream_id={stream_id}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
