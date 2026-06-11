# ATAK\_MI\_Server

Backend server for the **MTAK** (Maritime Tactical Awareness Kit) Android app.

Think of it as a shared hub — every Android device connects here, and anything one device sends (a chat message, a GPS ping, a map marker, a live video frame) is instantly pushed to every other device in the same room.

---

## Two Deployment Modes

The server runs in two different ways depending on where you deploy it.

| | **Relay Mode** | **Full Mode** |
|---|---|---|
| **Where** | Cloud (Render.com) | Local PC / field laptop |
| **Database** | No | PostgreSQL |
| **File storage** | No | MinIO |
| **MQTT** | No | Optional |
| **UDP auto-discovery** | No | Yes |
| **WebSocket relay** | Yes | Yes |
| **JWT auth** | Yes | Yes |
| **Use when** | Always-on public relay | Full persistence, offline ops, LAN |

Switch between modes with the `RELAY_MODE` environment variable (`true` = relay, `false` = full).

---

## Features

### Real-Time Chat — WebSocket `/chat`

- Multiple rooms supported (default is `lobby`)
- Private/direct messages via `dm-<username>` rooms — automatically delivered even if the recipient isn't subscribed yet
- New joiners immediately receive the last 50 messages and all active map markers
- 25-second keepalive so connections do not drop silently
- Any unrecognised message type (`ps_target`, `adsb`, custom events) is forwarded as-is to all room members

### GPS Location Sharing

- Every device broadcasts its position; all other devices update on the map in real time
- In full mode, positions are also stored in the database (with optional altitude and accuracy)

### Tactical Map Objects

All of these are stored in the database (full mode) **and** broadcast instantly over WebSocket to every device in the room:

- **Markers** — shared pins (circle, triangle, custom types). Moving or recolouring a marker updates it in place via UPSERT.
- **Safety Zones** — circular geofences with a status (`safe`, `danger`, etc.)
- **Routes** — multi-point waypoint paths stored as GeoJSON
- **Distance Measurements** — point-to-point lines with distance in metres
- **Drawings** — freehand lines, polygons, circles, and annotated shapes (also GeoJSON). Individual drawings can be deleted.
- **Alerts** — SOS and tactical alerts broadcast instantly

### Live Video Streaming — WebSocket `/stream`

One device publishes its camera; any number of devices watch it live.

- Publisher sends raw binary JPEG frames; server forwards every frame to all subscribers
- When the publisher disconnects, all viewers receive a `publisher_offline` event
- A browser/WebView viewer page is served at `GET /live/<username>`
- Active streams listed at `GET /api/live-streams`
- A `ps_stream` event is sent to the chat room when a stream starts (shows LIVE banner on map)

### Media Upload — Full Mode Only

Images, videos, and audio files are uploaded to MinIO and shared with the room.

- Multipart upload: `POST /api/media/image|video|audio`
- Base64 JSON upload: `POST /api/media/upload-base64`
- Retrieve file: `GET /api/media/file?bucket=...&key=...`

### User Authentication

- Register and login with username + password
- Passwords stored as bcrypt hashes
- JWT tokens (HS256, 1-week expiry) used for all protected endpoints and WebSocket connections
- Token validation works in both modes — stateless, only needs the shared `JWT_SECRET_KEY`

### Presence & Heartbeat

- Devices ping `POST /api/presence/heartbeat` every ~30 seconds
- Any device not seen for 60 seconds is considered offline
- Online user list is pushed to all WebSocket clients on every heartbeat change

### LoRa / Meshtastic Support

- When a Trident device sends its LoRa node name and ID in the heartbeat, it appears in `GET /api/lora/registry`
- In full mode, `GET /api/lora/status` scans USB serial ports for connected LoRa hardware (Heltec, TTGO T-Beam, RAK, ESP32, etc.)

### Device Settings Sync

- Per-user toggle state (Night Vision, Grid overlay, ADS-B layer, Track History) saved server-side
- `PUT` endpoint uses JSON merge — updating one key never overwrites the others

### UDP Auto-Discovery — Full Mode Only

- Server listens on UDP port 8091
- When the Android app is on the same WiFi it broadcasts `ATAK_DISCOVER_CHAT`
- Server replies with the WebSocket URL — app fills in the server address automatically

### Batch Message Sync

- Android's `ChatSyncWorker` posts messages in bulk via `POST /api/messages/batch`
- Deduplication via `device_msg_id` — the same message can be submitted multiple times without creating duplicates
- In relay mode the endpoint acknowledges everything immediately (no DB to write to)

### Admin / Maintenance

- `POST /api/messages/dedup` — removes duplicate messages (optionally scoped to one room)
- `DELETE /api/messages` — hard-deletes all messages or one room's messages
- `GET /api/export/unsynced?table=<name>` — exports up to 500 rows from any table for cross-server sync

---

## Project Structure

```
ATAK_MI_Server/
├── api/
│   ├── main.py              # FastAPI app — all routes and WebSocket handlers
│   ├── auth.py              # JWT creation/validation, bcrypt password hashing
│   ├── models.py            # Pydantic request body models
│   ├── websocket_manager.py # Room-based WebSocket connection manager
│   ├── udp_discovery.py     # UDP auto-discovery service (full mode only)
│   ├── lora_detector.py     # USB serial port scan for LoRa hardware
│   ├── mqtt_service.py      # Optional MQTT bridge
│   ├── requirements.txt     # Python dependencies
│   ├── .env                 # Local secrets — never committed
│   └── .env.example         # Template for .env
├── postgres/
│   └── schema.sql           # Full database schema
├── mosquitto/
│   └── mosquitto.conf       # MQTT broker config
├── scripts/
│   ├── start.bat            # Double-click launcher (calls start_server.ps1)
│   └── start_server.ps1     # Starts Docker, waits for PG, runs FastAPI
├── docs/
│   ├── USER_GUIDE.md
│   └── PROJECT_CHRONOLOGY.md
├── Dockerfile               # Single-container image for cloud/Render
├── docker-compose.yml       # Local full stack: PostgreSQL + MinIO + API
├── render.yaml              # Render.com deployment config
├── .dockerignore
└── .gitignore
```

---

## Database Tables

All tables live in PostgreSQL (`atak_db`). The schema in `postgres/schema.sql` is applied automatically on first `docker compose up`.

| Table | What it stores |
|---|---|
| `users` | Registered accounts — username, bcrypt hash, role |
| `messages` | Chat history — room-scoped, deduplicated by `device_msg_id` |
| `locations` | GPS pings — lat/lon/altitude/accuracy per device |
| `markers` | Tactical map pins — type, colour, label — UPSERT on `marker_uid` |
| `zones` | Safety zone circles — radius, status — UPSERT on `zone_uid` |
| `routes` | Waypoint paths stored as JSONB — UPSERT on `route_uid` |
| `measurements` | Distance lines between two points |
| `drawings` | Freehand/polygon/circle shapes as GeoJSON — UPSERT on `drawing_uid` |
| `alerts` | SOS and tactical alerts |
| `media` | File references — bucket name + object key in MinIO |
| `adsb_snapshots` | ADS-B aircraft snapshots |
| `device_settings` | Per-user toggle state as JSONB — merge on conflict |
| `presence` | Last-seen presence records |

---

## Environment Variables

Copy `api/.env.example` to `api/.env` for local mode. For Render, set these in the dashboard.

| Variable | Default | Description |
|---|---|---|
| `RELAY_MODE` | `false` | Set `true` on Render. Disables DB / MinIO / MQTT / UDP. |
| `SERVER_URL` | *(empty)* | Public URL returned by `/api/config` (e.g. `https://atak-mi-server.onrender.com`) |
| `PORT` | `3001` | Port the API listens on |
| `JWT_SECRET_KEY` | `tactical-secret-change-me` | **Change this.** Must match on both local and cloud. |
| `POSTGRES_HOST` | `localhost` | Use `postgres` inside docker-compose |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_USER` | `atak` | |
| `POSTGRES_PASSWORD` | `atak_secret` | |
| `POSTGRES_DB` | `atak_db` | |
| `MINIO_ENDPOINT` | `localhost` | Use `minio` inside docker-compose |
| `MINIO_PORT` | `9000` | |
| `MINIO_ACCESS_KEY` | `atakadmin` | |
| `MINIO_SECRET_KEY` | `atakadmin123` | |
| `MQTT_ENABLED` | `false` | Set `true` to enable MQTT bridge |
| `MQTT_HOST` | `localhost` | |
| `MQTT_PORT` | `1883` | |

---

## Running Locally — Full Mode

**Requirements:** Docker Desktop, Python 3.11+

### Option A — One-click (Windows)

Double-click `scripts/start.bat`

This calls `start_server.ps1`, which:
1. Starts PostgreSQL + MinIO via `docker compose up -d`
2. Waits up to 30 s for PostgreSQL to be ready
3. Installs Python dependencies
4. Starts FastAPI on port `3001` with hot-reload

### Option B — Manual

```powershell
# Start backing services
docker compose up -d

# Install dependencies
pip install -r api/requirements.txt

# Run the server
cd api
python -m uvicorn main:app --host 0.0.0.0 --port 3001 --reload
```

### Ports used locally

| Service | Port | Notes |
|---|---|---|
| ATAK API | `3001` | Main API + WebSocket endpoint |
| MinIO API | `9000` | Object storage |
| MinIO Console | `9001` | `http://localhost:9001` — user: `atakadmin` / pass: `atakadmin123` |
| PostgreSQL | `5433` | Host port mapped from container's `5432` |
| UDP Discovery | `8091` | LAN auto-discovery |

### Connecting the Android app

- **Same WiFi** — UDP auto-discovery fills in the address automatically
- **Manual** — enter `http://<YOUR_PC_IP>:3001`
- **Android emulator** — use `http://10.0.2.2:3001`

---

## Deploying to Render — Relay Mode

`render.yaml` handles the deployment. Render builds the Docker image and runs the container.

1. Push this repo to GitHub
2. Connect the repo in the Render dashboard (`render.yaml` auto-configures the service)
3. In the Render **Environment** tab, set:
   - `SERVER_URL` → `https://<your-app>.onrender.com`
   - `JWT_SECRET_KEY` → same secret as your local server
4. Deploy

The free Render tier spins down after inactivity. `render.yaml` sets `healthCheckPath: /healthz` — configure UptimeRobot to ping that endpoint every 5 minutes to keep it awake.

---

## Building the Docker Image Manually

```bash
# Build
docker build -t atak-mi-server:latest .

# Run in relay mode
docker run -p 3001:3001 \
  -e RELAY_MODE=true \
  -e SERVER_URL=http://localhost:3001 \
  -e JWT_SECRET_KEY=your-secret \
  atak-mi-server:latest
```

---

## API Reference

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Create account. Returns JWT. |
| `POST` | `/api/auth/login` | Login. Returns JWT. |
| `GET` | `/api/auth/me` | Validate JWT, returns username. Works in both modes. |
| `POST` | `/api/auth/heartbeat` | Update presence (also accepts Bearer token). |
| `POST` | `/api/presence/heartbeat` | Same as above (alternate path). |

### Tactical Data — write + broadcast

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/location` | Publish GPS position |
| `POST` | `/api/message` | Send chat message (REST alternative to WebSocket) |
| `POST` | `/api/marker` | Create / update a map marker |
| `POST` | `/api/zone` | Create / update a safety zone |
| `POST` | `/api/alert` | Send an alert |
| `POST` | `/api/route` | Create / update a route |
| `POST` | `/api/measurement` | Create / update a distance measurement |
| `POST` | `/api/drawing` | Create / update a drawing |
| `DELETE` | `/api/drawing/{uid}` | Delete a drawing |

### Read Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/messages?room=lobby&since=0` | Fetch message history (full mode only) |
| `GET` | `/api/routes?room=lobby` | Fetch saved routes |
| `GET` | `/api/drawings?room=lobby` | Fetch drawings |
| `GET` | `/api/users/all` | All registered users with online status |
| `GET` | `/api/presence/online` | Currently online users with last-seen time |

### Media — full mode only

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/media/image` | Upload image (multipart) |
| `POST` | `/api/media/video` | Upload video (multipart) |
| `POST` | `/api/media/audio` | Upload audio (multipart) |
| `POST` | `/api/media/upload-base64` | Upload any media as base64 JSON |
| `GET` | `/api/media/file?bucket=...&key=...` | Stream a stored file |

### Batch Sync

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/messages/batch` | Bulk-insert messages with dedup |
| `POST` | `/api/messages/dedup?room=lobby` | Remove duplicate messages |
| `DELETE` | `/api/messages?room=lobby` | Clear messages (all or one room) |
| `GET` | `/api/export/unsynced?table=messages` | Export table rows |
| `POST` | `/api/export/mark-synced` | Acknowledge sync (no-op, returns success) |

### LoRa / Meshtastic

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/lora/status` | USB serial scan for LoRa hardware (full mode) |
| `GET` | `/api/lora/registry` | Online users with active LoRa radios |

### Live Streaming

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/live-streams` | List active streams |
| `GET` | `/live/{stream_id}` | Browser / WebView viewer page |
| `WS` | `/stream` | Publisher and subscriber endpoint |

### Device Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/device-settings/{username}` | Get saved toggle state |
| `PUT` | `/api/device-settings/{username}` | Merge-update toggle state |

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` or `/healthz` | Health check — `{"status":"ok","relay_mode":...}` |
| `GET` | `/api/config` | Returns `SERVER_URL` for app auto-configuration |

---

## WebSocket Protocol

Connect to `ws://<host>:3001/chat?token=<jwt>`

All messages are JSON. Always send a `join` first.

### Client → Server

```json
// Join a room (required first)
{ "type": "join", "user": "Alice", "room": "lobby" }

// Send a chat message
{ "type": "message", "user": "Alice", "room": "lobby", "text": "Hello", "msgId": "<uuid>" }

// Send GPS position
{ "type": "location", "user": "Alice", "room": "lobby", "lat": 28.6139, "lon": 77.2090 }

// Any other type is forwarded as-is to all room members
{ "type": "ps_target", "room": "lobby", ... }
```

### Server → Client

```json
// Incoming chat message from another user
{ "type": "message", "user": "Bob", "room": "lobby", "text": "Hi", "ts": 1717000000000 }

// Location update
{ "type": "location", "user": "Bob", "room": "lobby", "lat": 28.6, "lon": 77.2, "ts": ... }

// Marker placed or moved
{ "type": "marker", "user": "Bob", "room": "lobby", "id": "<uid>", "lat": ..., "lon": ..., "ts": ... }

// Another user joined
{ "type": "join", "user": "Bob", "room": "lobby" }

// Online user list — sent on every connect / disconnect / heartbeat
{ "type": "users", "users": ["Alice", "Bob"] }

// Live stream started (shown as LIVE banner on map)
{ "type": "ps_stream", "user": "Bob", "stream_id": "Bob", "url": "/live/Bob", "ts": ... }

// Live stream ended
{ "type": "ps_stream_ended", "user": "Bob", "stream_id": "Bob", "ts": ... }
```

### Live Stream WebSocket `/stream`

```json
// Publisher registers (first frame must be this JSON text)
{ "action": "publish", "stream_id": "Alice", "token": "<jwt>" }

// Subscriber registers
{ "action": "subscribe", "stream_id": "Alice", "token": "<jwt>" }

// Server confirms
{ "status": "ok", "role": "publisher", "stream_id": "Alice" }

// Publisher then sends raw binary JPEG frames continuously
// Server forwards each frame to all subscribers as binary

// When publisher disconnects, all subscribers receive:
{ "event": "publisher_offline", "stream_id": "Alice" }
```

---

## What We Built — Chronology

| What | Details |
|---|---|
| Unified FastAPI backend | Single server replaces multiple earlier prototypes |
| WebSocket chat + rooms | Real-time messaging with room isolation and DM support |
| GPS + map sync | Live location sharing and tactical object synchronisation |
| PostgreSQL integration | Persistent storage with asyncpg connection pool |
| MinIO media storage | Images/video/audio stored in object storage buckets |
| JWT authentication | Stateless auth that works in both relay and full mode |
| UDP auto-discovery | Phones find the local server on WiFi automatically |
| Live video streaming | JPEG frame relay over WebSocket, publisher/subscriber model |
| Presence & heartbeat | Online user tracking with 60-second timeout |
| LoRa / Meshtastic | Registry of online LoRa radios + USB serial hardware detection |
| Docker full stack | docker-compose with PostgreSQL + MinIO + API |
| Render relay deploy | render.yaml for lightweight always-on cloud relay |
| Batch sync + dedup | Android ChatSyncWorker support with `device_msg_id` dedup |
| Drawing tools | Freehand/polygon/circle shapes stored as GeoJSON |
| Device settings sync | Per-user toggle state (Night Vision, Grid, ADS-B, Track History) |
| Removed ngrok | ngrok dependency dropped; server URL comes from `SERVER_URL` env var |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 3001 already in use | Run `netstat -ano \| findstr :3001`, kill the process, restart. |
| App cannot find server on WiFi | Allow ports `3001` and `8091` through Windows Firewall. |
| PostgreSQL not starting | `docker logs atak_postgres` — check for port conflict on 5433. |
| MinIO not starting | `docker logs atak_minio` — check ports 9000 / 9001 are free. |
| JWT errors / login fails | Ensure `JWT_SECRET_KEY` is identical on local and cloud servers. |
| Duplicate messages in chat | `POST /api/messages/dedup?room=<room>` to clean up. |
| Cloud relay slow / sleeping | UptimeRobot → ping `/healthz` every 5 minutes on the Render URL. |
| LoRa device not detected | Connect USB, check Device Manager for a COM port, hit `/api/lora/status`. |

---

## Related Docs

- [User Guide](docs/USER_GUIDE.md)
- [Project Chronology](docs/PROJECT_CHRONOLOGY.md)
