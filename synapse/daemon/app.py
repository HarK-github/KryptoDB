"""
FastAPI entrypoint, 127.0.0.1-only.
"""
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import logging
from synapse.daemon.lifecycle import startup_event, shutdown_event, check_health
from synapse.daemon.keyring import keyring

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_event()
    yield
    shutdown_event()

app = FastAPI(title="Synapse Daemon", version="0.1.0", lifespan=lifespan)

@app.get("/health")
def health_endpoint():
    status = check_health()
    if status["status"] == "down":
        raise HTTPException(status_code=503, detail=status)
    return status

@app.post("/session/start")
def start_session(active_partitions: list[str]):
    # In Phase 1, just accept and store them in the keyring
    keyring.set_active_partitions(set(active_partitions))
    return {"status": "ok", "active_partitions": list(keyring.get_active_partitions())}

@app.get("/session/show")
def show_session():
    return {"active_partitions": list(keyring.get_active_partitions())}

if __name__ == "__main__":
    import uvicorn
    # Bind to 127.0.0.1 only to ensure process boundary isolation
    uvicorn.run(app, host="127.0.0.1", port=8000)
