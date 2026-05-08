import json
import logging
from datetime import datetime
from typing import Dict, Set, List
from fastapi import WebSocket

class ConnectionManager:
    """
    Manages WebSocket connections for ATAK clients.
    Supports room-based broadcasting and direct messaging.
    """
    def __init__(self):
        # room -> set of websockets
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # websocket -> metadata (user, device_id, room)
        self.metadata: Dict[WebSocket, dict] = {}
        self.logger = logging.getLogger("WebSocketManager")

    async def connect(self, websocket: WebSocket, username: str, room: str = "lobby"):
        if room not in self.active_connections:
            self.active_connections[room] = set()
        
        self.active_connections[room].add(websocket)
        self.metadata[websocket] = {
            "user": username,
            "room": room,
            "connected_at": datetime.now().isoformat()
        }
        self.logger.info(f"User {username} connected to room {room}")
        # Broadcast the updated user list to everyone
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket):
        meta = self.metadata.get(websocket)
        if meta:
            room = meta["room"]
            user = meta["user"]
            if room in self.active_connections:
                self.active_connections[room].remove(websocket)
                if not self.active_connections[room]:
                    del self.active_connections[room]
            del self.metadata[websocket]
            self.logger.info(f"User {user} disconnected from room {room}")
            
    async def broadcast_user_list(self):
        """
        Broadcast the list of all connected users to all clients (global).
        """
        users = self.get_online_users()
        payload = {"type": "users", "users": users}
        data = json.dumps(payload)
        
        for room in self.active_connections.values():
            for ws in room:
                try:
                    await ws.send_text(data)
                except:
                    pass

    async def broadcast(self, room: str, message: dict, exclude: WebSocket = None):
        """
        Send a JSON message to all clients in a specific room.
        For DM rooms that have no subscribers yet, fall back to broadcasting
        to ALL connected clients so the Android app can auto-join the room.
        """
        if "ts" not in message:
            message["ts"] = datetime.now().timestamp() * 1000  # Javascript ms

        if room not in self.active_connections:
            # DM room fallback: push to every connected client so Android's
            # handleServerMessage() auto-join logic can fire and subscribe.
            if room.startswith("dm-"):
                data = json.dumps(message)
                for ws_set in list(self.active_connections.values()):
                    for ws in list(ws_set):
                        if ws == exclude:
                            continue
                        try:
                            await ws.send_text(data)
                        except Exception:
                            pass
            return

        data = json.dumps(message)
        dead_connections = set()

        for connection in self.active_connections[room]:
            if connection == exclude:
                continue
            try:
                await connection.send_text(data)
            except Exception:
                dead_connections.add(connection)

        # Cleanup dead connections
        for dead in dead_connections:
            self.disconnect(dead)

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """
        Send a JSON message to a single client.
        """
        if "ts" not in message:
            message["ts"] = datetime.now().timestamp() * 1000
        try:
            await websocket.send_text(json.dumps(message))
        except Exception:
            self.disconnect(websocket)

    async def broadcast_user_list_with_hb(self, users: List[str]):
        """Broadcast a combined WS + heartbeat user list to all connected clients."""
        data = json.dumps({"type": "users", "users": users})
        for room in self.active_connections.values():
            for ws in room:
                try:
                    await ws.send_text(data)
                except Exception:
                    pass

    def get_online_users(self, room: str = None) -> List[str]:
        """
        Return a list of usernames currently online.
        """
        users = []
        if room:
            if room in self.active_connections:
                for ws in self.active_connections[room]:
                    users.append(self.metadata[ws]["user"])
        else:
            for meta in self.metadata.values():
                users.append(meta["user"])
        return list(set(users)) # Unique users
