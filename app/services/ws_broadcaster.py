"""WebSocket Broadcaster for real-time pipeline visualization."""

import asyncio
from fastapi import WebSocket

class WSBroadcaster:
    """Manages connected websocket clients and broadcasts events."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, event: dict):
        """Send JSON event to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(event)
            except Exception:
                disconnected.append(connection)
        
        for failed in disconnected:
            self.disconnect(failed)

    def broadcast_sync(self, event: dict):
        """Fire and forget broadcast for synchronous contexts."""
        if not self.active_connections:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(event))
        except RuntimeError:
            pass # No loop, can't broadcast

# Singleton instance
broadcaster = WSBroadcaster()
