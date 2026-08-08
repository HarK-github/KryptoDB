"""
Close-and-open supersession, lineage stamping.
"""
from datetime import datetime, timezone
from synapse.graph.schema import Edge

class WriteEngine:
    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
        
    def write_fact(self, edge: Edge, existing_edge: Edge | None = None) -> Edge:
        now = self._now()
        edge.valid_from = now
        edge.valid_to = None
        edge.ingested_at = now
        
        if existing_edge:
            existing_edge.valid_to = now
            existing_edge.superseded_by = edge.id
            
        return edge
        
    def assert_lineage(self, edge: Edge, is_derived: bool):
        if is_derived and not edge.derived_from:
            raise ValueError("Derived fact must have derived_from lineage")
        if not is_derived and edge.derived_from:
            raise ValueError("Primary fact cannot have derived_from lineage")
