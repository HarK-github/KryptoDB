from __future__ import annotations

import uuid
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class MemoryStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"

class MemoryType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    OBSERVATION = "observation"
    DECISION = "decision"
    HYPOTHESIS = "hypothesis"

class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    subject: str
    predicate: str
    object: str
    description: Optional[str] = None
    
    # Tenancy & Access Scope
    scope: str = "default"
    tenant_id: str = "default"
    
    # Lifecycle & Versioning
    status: MemoryStatus = MemoryStatus.ACTIVE
    version: int = 1
    previous_version_id: Optional[str] = None
    memory_type: MemoryType = MemoryType.FACT
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    trust_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    
    # Temporal Validity (Strict UTC)
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: Optional[datetime] = None
    
    # Provenance
    source_agent_id: str = "agent-1"
    source_doc: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
