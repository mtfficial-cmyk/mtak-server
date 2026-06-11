# ATAK_MI_Server

`ATAK_MI_Server` is the backend server for the maritime / tactical Android client.

In simple words, this server is the shared hub for the whole team:

- phones connect to it
- chat messages go through it
- live GPS positions go through it
- tactical markers and drawings go through it
- media uploads go through it
- live video stream events go through it

If one user sends something, this server receives it and quickly shares it with the other users in the same room.

## What This Server Does

This server combines real-time tactical communication with normal backend services.

It can:

- relay real-time chat messages
- share live map positions
- sync markers, routes, drawings, measurements, and alerts
- manage user login with JWT authentication
- store data in PostgreSQL
- store media files in MinIO
- support UDP auto-discovery on local WiFi
- expose live stream endpoints for mobile camera publishing / viewing
- support device presence and online status tracking
- support LoRa / Meshtastic-related registry and detection endpoints

## Two Deployment Modes

The project is designed to run in two different ways.

### 1. Full Mode

Used mainly on a local PC / field laptop / local network.

Includes:

- FastAPI backend
- PostgreSQL database
- MinIO object storage
- optional MQTT support
- UDP auto-discovery

Use this when you want full persistence, media storage, offline or LAN-based operations, and proper backend services.

### 2. Relay Mode

Used mainly on cloud hosting such as Render.

Includes:

- FastAPI backend
- WebSocket relay
- authentication
- lightweight real-time communication

In relay mode, database-heavy and local-network services are not the focus. It is best for simple public relay behavior when the cloud server needs to stay lightweight.

## Main Features

### Real-Time Chat

Clients connect over WebSocket and exchange messages in real time.

The server supports:

- multiple rooms
- direct messages
- recent history sync for new joiners
- keepalive behavior to prevent silent disconnects
- forwarding of custom tactical message types

### Live GPS Sharing

Each device can send its location and the server forwards it to other connected users.

In full mode, location records can also be stored.

### Tactical Objects

The server supports live synchronization of:

- markers
- zones
- routes
- measurements
- drawings
- alerts

These updates are sent in real time so that every connected user sees the same map state.

### Media Uploads

When full mode is enabled, the server can store:

- images
- videos
- audio

using MinIO object storage.

### Live Video

One client can publish a stream and other clients can watch it.

The server exposes:

- WebSocket streaming endpoint
- live stream listing endpoint
- browser / WebView viewing page

### Presence

The server tracks which users are currently online using heartbeat updates.

### LoRa / Meshtastic Support

The server also includes support around LoRa-connected tactical devices:

- online registry endpoints
- optional USB serial hardware detection for compatible boards

## Current Folder Structure

```text
ATAK_MI_Server/
├── api/
│   ├── main.py
│   ├── auth.py
│   ├── models.py
│   ├── websocket_manager.py
│   ├── udp_discovery.py
│   ├── lora_detector.py
│   ├── mqtt_service.py
│   ├── requirements.txt
│   └── .env.example
├── atak/
├── docs/
│   ├── PROJECT_CHRONOLOGY.md
│   └── USER_GUIDE.md
├── mosquitto/
├── postgres/
│   └── schema.sql
├── scripts/
│   ├── start.bat
│   └── start_server.ps1
├── docker-compose.yml
├── Dockerfile
├── render.yaml
├── .dockerignore
└── .gitignore
```

## What We Have Done In This Project

This project has evolved from a basic tactical communication backend into a more complete integrated server.

The major work done here includes:

- built a unified FastAPI-based backend server
- added WebSocket chat and tactical room communication
- added GPS sharing and map object synchronization
- integrated PostgreSQL for persistent structured data
- integrated MinIO for media storage
- added JWT login and password hashing
- added local LAN auto-discovery over UDP
- added live stream support for mobile clients
- added presence / heartbeat tracking
- added LoRa / Meshtastic-related server-side support
- added Docker support for local full-stack deployment
- added Render deployment support for lightweight cloud relay mode
- documented the system more clearly so local deployment and field usage are easier to understand

## Actual Local Ports Used

These are the important ports used by the current setup:

| Service | Port | Notes |
|---|---:|---|
| ATAK API | `3001` | Main backend API and WebSocket server |
| PostgreSQL | `5433` | Host port mapped to container `5432` |
| MinIO API | `9000` | Object storage endpoint |
| MinIO Console | `9001` | MinIO browser console |
| UDP Discovery | `8091` | Local network auto-discovery |

## Docker Compose Setup

The local full stack is defined in `docker-compose.yml`.

It currently starts:

- `postgres`
- `minio`
- `atak-server`

### Important Current Compose Details

- backend port is `3001`
- PostgreSQL host port is `5433`
- the API container expects PostgreSQL at service name `postgres`
- the API container expects MinIO at service name `minio`
- `RELAY_MODE` is set to `false` in the local compose setup

## Dockerfile Purpose

The `Dockerfile` builds the server image for container-based deployment.

It:

- uses `python:3.11-slim`
- installs Python dependencies from `api/requirements.txt`
- copies the API source
- exposes port `3001`
- starts FastAPI with `uvicorn`

This is suitable for cloud deployment and also for manual container testing.

## Running Locally

### Option 1: With Docker Compose + Startup Script

Use the included script:

```powershell
.\scripts\start.bat
```

This is the easiest local flow if your environment is already prepared.

### Option 2: Manual Local Start

```powershell
docker compose up -d
pip install -r api/requirements.txt
cd api
python -m uvicorn main:app --host 0.0.0.0 --port 3001 --reload
```

## Cloud Deployment

The repo also includes `render.yaml` for Render deployment.

Typical idea:

1. push repo to GitHub
2. connect repo to Render
3. configure environment variables
4. deploy as relay mode or lightweight server mode depending on your environment design

## Environment Variables

Important variables include:

- `RELAY_MODE`
- `SERVER_URL`
- `PORT`
- `JWT_SECRET_KEY`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `MINIO_ENDPOINT`
- `MINIO_PORT`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MQTT_ENABLED`
- `MQTT_HOST`
- `MQTT_PORT`

Use `api/.env.example` as the template for local configuration.

## Simple Working Summary

If you want to understand the server in one line:

> This server is the shared tactical backend that lets multiple field devices chat, share positions, update the map, upload media, and stay synchronized in real time.

## Notes

- local documentation must match the real running port `3001`, not `3000`
- local PostgreSQL host port is `5433`
- MinIO is part of the full local stack
- cloud and local behavior are not identical, so deployment mode must be chosen correctly

## Related Docs

- [User Guide](docs/USER_GUIDE.md)
- [Project Chronology](docs/PROJECT_CHRONOLOGY.md)
