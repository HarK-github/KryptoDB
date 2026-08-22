from typing import Literal, Optional, Dict, Set, List
from pydantic import BaseModel, Field
from datetime import datetime
import asyncio
from uuid import uuid4

class GraphEvent(BaseModel):
    event_type: Literal["node_created", "edge_created", "edge_closed", 
                        "traversal_complete", "cascade_step", "access_denied"]
    permission_scope: str
    timestamp: datetime
    
    # Optional structural fields (No sensitive plaintext ever included)
    node_id: Optional[str] = None
    node_type: Optional[str] = None
    edge_id: Optional[str] = None
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    trust_weight: Optional[float] = None
    
    # Completed traversal animations are delivered atomically after the query.
    request_id: Optional[str] = None
    traversal_path: List[Dict[str, str]] = Field(default_factory=list)

class EventBus:
    def __init__(self):
        # A dictionary mapping client_id (UUID) to their asyncio.Queue
        self.clients: Dict[str, asyncio.Queue] = {}
        self.audit_log: list = []

    async def connect(self, client_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self.clients[client_id] = queue
        return queue

    def disconnect(self, client_id: str):
        if client_id in self.clients:
            del self.clients[client_id]

    async def publish(self, event: GraphEvent):
        """
        Fans out the raw event to all connected clients.
        Gate-filtering happens at the WebSocket consumption layer.
        """
        self.audit_log.insert(0, [
            event.timestamp.strftime("%H:%M:%S.%f")[:-3],
            "system", 
            event.event_type,
            event.permission_scope,
            "allowed"
        ])
        if len(self.audit_log) > 100:
            self.audit_log.pop()

        # Publish to all clients
        for queue in self.clients.values():
            await queue.put(event)

# Global singleton event bus
event_bus = EventBus()
