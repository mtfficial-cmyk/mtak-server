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
from fastapi.responses import StreamingResponse, JSONResponse
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
                    """INSERT INTO messages (device_id, username, room, message_text, ts)
                       VALUES ($1,$2,$3,$4,$5)""",
                    msg_id[:50], sender, room, text, ts,
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
            """SELECT id, username, room, message_text, ts
               FROM messages WHERE room=$1 AND ts > to_timestamp($2)
               ORDER BY ts ASC LIMIT 100""",
            room, since_sec,
        )
    else:
        rows = await db_pool.fetch(
            """SELECT id, username, room, message_text, ts
               FROM messages WHERE ts > to_timestamp($1)
               ORDER BY ts ASC LIMIT 100""",
            since_sec,
        )
    return {"messages": [
        {"msgId": str(r["id"]), "sender": r["username"], "room": r["room"],
         "text": r["message_text"], "messageType": "text",
         "timestamp": int(r["ts"].timestamp() * 1000)}
        for r in rows
    ]}


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
    """
    Returns the public server URL.
    In relay mode (Render): SERVER_URL env var.
    In local mode: queries ngrok if running, falls back to SERVER_URL.
    """
    if SERVER_URL:
        return {"server_url": SERVER_URL, "ngrok": False}
    if not RELAY_MODE:
        try:
            import urllib.request, json as _json
            with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as r:
                data = _json.loads(r.read())
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("proto") == "https":
                        return {"server_url": tunnel["public_url"], "ngrok": True}
        except Exception:
            pass
    return {"server_url": None, "ngrok": False}


# ── Maritime sync export (local only) ─────────────────────────────────────────
_EXPORT_QUERIES = {
    "messages":  "SELECT id,device_id,username,room,message_text,ts FROM messages ORDER BY ts DESC LIMIT 500",
    "locations": "SELECT id,device_id,username,room,latitude,longitude,accuracy_m,ts FROM locations ORDER BY ts DESC LIMIT 500",
    "alerts":    "SELECT id,device_id,username,room,alert_type,alert_text,ts FROM alerts ORDER BY ts DESC LIMIT 500",
    "markers":   "SELECT id,marker_uid,device_id,username,room,marker_type,color,latitude,longitude,label,ts FROM markers ORDER BY ts DESC LIMIT 500",
    "zones":     "SELECT id,zone_uid,device_id,username,room,status,latitude,longitude,radius_m,description,ts FROM zones ORDER BY ts DESC LIMIT 500",
    "media":     "SELECT id,device_id,username,room,media_type,bucket_name AS bucket,object_key,file_size_b AS file_size_bytes,mime_type,ts FROM media ORDER BY ts DESC LIMIT 100",
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
                    # Send last 50 messages
                    msgs = await db_pool.fetch(
                        "SELECT id, username, message_text, ts FROM messages WHERE room=$1 ORDER BY ts DESC LIMIT 50",
                        room,
                    )
                    for m in reversed(msgs):
                        await manager.send_personal_message({
                            "type":  "message",
                            "msgId": f"srv-{m['id']}",
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
                                """INSERT INTO messages (device_id, username, room, message_text)
                                   VALUES ($1,$2,$3,$4)""",
                                msg_id[:50] if msg_id else "WS", user, room, text,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
