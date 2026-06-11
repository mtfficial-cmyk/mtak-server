# ATAK Project Chronology & System Evolution

This document explains, in simple words, how the ATAK mobile system and the `ATAK_MI_Server` backend evolved into the current integrated setup.

---

## 1. Mobile App Stabilization

One major part of the work was improving the Android tactical client so it could work reliably with the backend.

Key work included:

- resolving code integration issues in `MainActivity.java` and related classes
- fixing symbol and threading issues from merged tactical code
- reducing ANR and memory-related instability
- improving support for room-based communication and synchronized tactical data

This made the mobile side more stable when handling chat, map updates, and continuous background activity.

---

## 2. Creation of `ATAK_MI_Server`

The backend server was created as a merged solution instead of keeping separate systems.

The idea was:

- keep the fast tactical real-time behavior
- add proper backend storage and deployment structure

This server effectively combines:

### Tactical Realtime Layer

- WebSocket-based room communication
- instant relay of chat and tactical events
- UDP auto-discovery for local WiFi environments
- support for tactical message payloads used by the Android devices

### Enterprise / Persistent Layer

- PostgreSQL for structured data storage
- MinIO for storing media files
- JWT authentication
- Docker-based deployment
- support for cloud and local deployment paths

---

## 3. What The Server Supports Today

The current integrated backend supports:

- user registration and login
- JWT-based protected access
- real-time chat
- room-based communication
- GPS location sharing
- tactical markers and zones
- routes and measurements
- drawing synchronization
- alerts / SOS events
- presence heartbeat updates
- live stream support
- media upload support
- LoRa / Meshtastic-related server support

In simple words:

> the server acts as the central tactical coordination hub for all connected devices.

---

## 4. Local Full Stack Architecture

The local deployment is now designed around a proper service stack:

| Service | Technology | Purpose |
|---|---|---|
| Core API | FastAPI | Main backend and WebSocket server |
| Realtime Transport | WebSockets | Live chat and tactical synchronization |
| Discovery | UDP | Automatic server discovery on local WiFi |
| Database | PostgreSQL | Persistent storage for structured data |
| Object Storage | MinIO | Media storage |
| Optional Broker | Mosquitto MQTT | Tactical sensor / external integration support |

This gives a much more complete system than a simple relay-only chat backend.

---

## 5. Deployment Improvements

The project now supports multiple deployment styles:

### Local Full Mode

Used for:

- development
- testing
- field laptop / local network operations
- persistent data and media storage

### Cloud / Relay Mode

Used for:

- public cloud deployment
- lighter real-time relay behavior
- scenarios where persistence is not the main concern

This separation makes the server more flexible for different operational environments.

---

## 6. Documentation Cleanup and Clarification

Another important part of the work has been improving the project documentation so it matches the real running setup.

This included clarifying:

- what the server actually does
- how the local Docker setup works
- what ports are actually used
- the difference between local full mode and cloud relay mode
- how Android devices connect
- how the backend pieces fit together

Important clarification from the current repo state:

- API port is `3001`
- local PostgreSQL host port is `5433`
- MinIO uses `9000` and `9001`
- UDP discovery uses `8091`

Older notes that mention port `3000` do not describe the current local setup accurately.

---

## 7. Current Status

The server has grown from a simple tactical communication concept into a more complete integrated backend that supports:

- real-time tactical communication
- persistent backend storage
- media management
- local network discovery
- cloud deployment support
- future tactical integrations

The system is now much closer to a practical field backend than an early prototype.
