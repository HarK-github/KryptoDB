from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
from synapse.daemon.keyring import keyring
from synapse.integrity.partition_crypto import Encryptor

router = APIRouter()
encryptor = Encryptor()

class SessionStartRequest(BaseModel):
    active_partitions: List[str]

@router.post("/session")
def start_session(req: SessionStartRequest):
    keyring.clear()
    for p in req.active_partitions:
        key = encryptor._get_or_create_key(p)
        keyring.add_key(p, key)
    return {"active_partitions": req.active_partitions, "status": "active"}

@router.get("/session")
def get_session():
    return {"active_partitions": list(keyring._keys.keys())}
