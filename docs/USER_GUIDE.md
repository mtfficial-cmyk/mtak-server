# ATAK_MI_Server User Guide

This guide explains how to run and use `ATAK_MI_Server` in simple words.

## What This Server Is

This server is the backend for the maritime / tactical Android system.

It helps connected users:

- chat in real time
- share live GPS locations
- place and update tactical map markers
- share routes, drawings, alerts, and measurements
- upload media files
- use live stream features

In short:

> the server keeps all connected devices synchronized.

## How It Works

When a mobile device connects:

1. the user logs in
2. the app connects to the API / WebSocket server
3. the app joins a room
4. the server relays chat and tactical updates to everyone else in that room
5. in full mode, the server also stores data in PostgreSQL and media in MinIO

## Two Ways To Run It

### Full Mode

Use this on a local machine or field laptop when you want the full stack.

Includes:

- FastAPI backend
- PostgreSQL
- MinIO
- UDP auto-discovery
- optional MQTT support

### Relay Mode

Use this mainly for cloud deployments when you want a simpler relay server.

This is useful when you only need real-time communication and lighter infrastructure.

## Local Ports

The current setup uses:

- `3001` for the ATAK API and WebSocket server
- `5433` for PostgreSQL on the host machine
- `9000` for MinIO API
- `9001` for MinIO Console
- `8091` for UDP auto-discovery

## Quick Local Start

### Easy Method

Run:

```powershell
.\scripts\start.bat
```

This is the easiest local launch method if the environment is prepared.

### Manual Method

```powershell
docker compose up -d
pip install -r api/requirements.txt
cd api
python -m uvicorn main:app --host 0.0.0.0 --port 3001 --reload
```

## Docker Services In Local Full Mode

The local `docker-compose.yml` starts:

- PostgreSQL
- MinIO
- the ATAK backend server

## Android Client Connection

### Auto-Discovery

If the Android device and server PC are on the same WiFi network:

1. the app sends a UDP discovery request
2. the server listens on port `8091`
3. the server replies with its address
4. the app can fill in the server automatically

### Manual Connection

If discovery does not work, use:

```text
http://<YOUR_PC_IP>:3001
```

Example:

```text
http://192.168.1.5:3001
```

For Android emulator use:

```text
http://10.0.2.2:3001
```

## Main Features Available To Users

### Chat

- instant room-based chat
- direct messages
- recent message sync

### Map Synchronization

- live location updates
- tactical markers
- zones
- routes
- drawings
- distance measurements
- alerts

### Media

- image upload
- video upload
- audio upload

### Live Stream

- one user can publish a stream
- other users can subscribe and watch

### Presence

- online / offline user tracking using heartbeat updates

## Data Storage

In full mode:

- PostgreSQL stores structured tactical data
- MinIO stores media files

Examples of stored data:

- users
- messages
- locations
- markers
- routes
- drawings
- alerts
- device settings
- presence

## Environment Setup

Use `api/.env.example` as the template for your local `.env`.

Important values you should check:

- `PORT`
- `SERVER_URL`
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

## Troubleshooting

| Problem | What to check |
|---|---|
| Server not reachable | Make sure the backend is really running on `3001` |
| Auto-discovery fails | Check Windows Firewall and UDP port `8091` |
| Database errors | Check PostgreSQL container and credentials |
| Media upload fails | Check MinIO is running on `9000` / `9001` |
| JWT problems | Make sure the same `JWT_SECRET_KEY` is used where required |
| Android app cannot connect | Make sure phone and PC are on the same network, or enter the server IP manually |

## Important Clarification

Some older notes may still mention port `3000`.

For the current setup in this repo, the important backend port is:

```text
3001
```
