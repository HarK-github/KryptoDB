import hashlib
import json
from synapse.graph.schema import Edge, Node

def hash_entity(node: Node) -> str:
    data = {
        "id": node.id,
        "type": node.type,
        "permission_scope": node.permission_scope,
        "name_ct": node.name_ct,
        "description_ct": node.description_ct
    }
    canonical = json.dumps(data, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()

def hash_fact(edge: Edge) -> str:
    data = {
        "id": edge.id,
        "type": edge.type,
        "subject_id": edge.subject_id,
        "object_id": edge.object_id,
        "permission_scope": edge.permission_scope
    }
    canonical = json.dumps(data, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
