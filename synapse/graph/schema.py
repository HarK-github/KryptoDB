"""
Node/edge property split (plaintext vs. content).
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

class SynapseBase(BaseModel):
    id: str
    type: str
    permission_scope: str
    
class ContentFields(BaseModel):
    name_ct: str # Will be encrypted in Phase 3
    description_ct: str # Will be encrypted in Phase 3

class Node(SynapseBase, ContentFields):
    pass
    
class Edge(SynapseBase):
    predicate: str
    source_agent_id: str
    trust_weight: float = 0.0
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    unlock_at: Optional[datetime] = None
    derived_from: List[str] = Field(default_factory=list)
    superseded_by: Optional[str] = None
    subject_id: str
    object_id: str
    subject_tags: List[str] = Field(default_factory=list)
    object_tags: List[str] = Field(default_factory=list)
    is_suspicious: bool = False
