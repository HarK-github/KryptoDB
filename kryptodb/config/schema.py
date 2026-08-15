from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from datetime import datetime

class AgentConfig(BaseModel):
    trust_prior: float = 0.0
    allowed_scopes: List[str] = Field(default_factory=list)
    allowed_labels: List[str] = Field(default_factory=list)
    grant_expires_at: Optional[datetime] = None

class AgentsSchema(BaseModel):
    agents: Dict[str, AgentConfig]
