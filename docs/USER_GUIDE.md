# ATAK_MI_Server — User Guide

Welcome to your new integrated tactical backend. This server combines the realtime capabilities of the ATAK Tactical Bundle with the storage and scaling of your Internet-Facing Production system.

> [!NOTE]
> For a detailed history of how we merged the packages and updated the ATAK app, see the [Project Chronology](PROJECT_CHRONOLOGY.md).

## 1. Quick Start

### Prerequisites
- **Python 3.10+**: Run `python --version` to check.
- **Docker Desktop**: Required for the MinIO storage system.
- **PostgreSQL 17**: Ensure your Postgres service is running (pgAdmin).

### Initialization
1.  Open PowerShell in the `scripts` folder:
    ```powershell
    cd D:\Vijay_Psitech\ATAK_Psitech\Atak-main\Atak-main\ATAK_MI_Server\scripts
    ```
2.  Install dependencies:
    ```powershell
    pip install -r ../api/requirements.txt
    ```
3.  Ensure your `.env` in the `api` folder has the correct database credentials.

### Start the Server
Run the professional startup script:
```powershell
.\start_server.ps1
```
This script will start MinIO, launch the FastAPI backend, and begin the UDP Discovery service.

---

## 2. Connecting the ATAK App

### Auto-Discovery (Local WiFi)
If your phone and the server PC are on the same WiFi:
1.  Open the ATAK app.
2.  The app will broadcast a discovery request.
3.  The server's **UDP Discovery Service** will reply automatically.
4.  The Server URL should auto-fill for registration/login.

### Manual Connection
If auto-discovery fails:
- Enter: `http://<YOUR_PC_IP>:3000`
- Example: `http://192.168.1.5:3000`

---

## 3. Realtime Features

This server is "Live." You will notice the following:
- **Instant Messaging**: Chat messages are pushed via WebSockets immediately.
- **Live Location**: Map markers for your team update in realtime without needing to refresh.
- **Tactical Markers**: When one user places a marker (circle, triangle, etc.), it appears on everyone's map instantly.
- **Media Sync**: Photos and videos are uploaded to MinIO and shared with all participants.

---

## 4. Administration

### Database Management
Data is stored in your PostgreSQL `atak_db`. You can query it via pgAdmin:
- `users`: Managed accounts and roles.
- `messages`: Persistent chat history.
- `markers`: Shared tactical map data.

### Media Browser
Visit the MinIO console to browse shared photos/videos:
- **URL**: `http://localhost:9001`
- **User**: `atakadmin`
- **Pass**: `atakadmin123`

---

## 5. Troubleshooting

| Issue | Solution |
| :--- | :--- |
| **Server won't start** | Check if port `3000` or `8091` is in use by another app. |
| **App can't find server** | Ensure Windows Firewall allows ports `3000`, `8080`, and `8091`. |
| **Database error** | Verify `POSTGRES_PASSWORD` in your `.env` file matches pgAdmin. |
