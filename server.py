"""
ViMax Interactive Pipeline – FastAPI backend.

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import os
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipelines.interactive_pipeline import InteractivePipeline

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="ViMax Interactive Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

pipeline_instance: Optional[InteractivePipeline] = None
pipeline_task: Optional[asyncio.Task] = None
connected_ws: Set[WebSocket] = set()

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class StartRequest(BaseModel):
    idea: str
    user_requirement: str = ""
    style: str = ""
    config_path: str = "configs/idea2video.yaml"


class DecisionRequest(BaseModel):
    action: str          # "approve" | "regenerate" | "save"
    feedback: str = ""
    content: str = ""


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------


async def broadcast(msg: dict):
    """Send a JSON message to all connected WebSocket clients."""
    dead: Set[WebSocket] = set()
    text = json.dumps(msg)
    for ws in list(connected_ws):
        try:
            await ws.send_text(text)
        except Exception:
            dead.add(ws)
    connected_ws.difference_update(dead)


async def _drain_log_queue(pipeline: InteractivePipeline):
    """Background task: pull log messages from the pipeline and broadcast them."""
    while True:
        try:
            message = await asyncio.wait_for(pipeline.log_queue.get(), timeout=1.0)
            await broadcast({"type": "log", "message": message})
        except asyncio.TimeoutError:
            # Check if the pipeline task is done and the queue is empty
            if pipeline_task is not None and pipeline_task.done() and pipeline.log_queue.empty():
                break
        except Exception:
            break


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_ws.add(websocket)
    try:
        while True:
            # Keep the connection alive; we only push from the server side
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        connected_ws.discard(websocket)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@app.post("/api/start")
async def start_pipeline(body: StartRequest):
    global pipeline_instance, pipeline_task

    # Cancel any existing run
    if pipeline_task is not None and not pipeline_task.done():
        pipeline_task.cancel()
        try:
            await pipeline_task
        except (asyncio.CancelledError, Exception):
            pass

    config_path = body.config_path
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.getcwd(), config_path)

    pipeline_instance = InteractivePipeline(config_path=config_path)
    pipeline_instance.set_broadcast(broadcast)

    async def _run():
        # Start the log drainer alongside the pipeline
        drain_task = asyncio.create_task(_drain_log_queue(pipeline_instance))
        try:
            await pipeline_instance.run(
                idea=body.idea,
                user_requirement=body.user_requirement,
                style=body.style,
            )
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):
                pass

    pipeline_task = asyncio.create_task(_run())

    return {"status": "started"}


@app.post("/api/decision")
async def submit_decision(body: DecisionRequest):
    global pipeline_instance
    if pipeline_instance is None:
        return {"status": "error", "detail": "No pipeline running"}

    await pipeline_instance.submit_decision(
        action=body.action,
        feedback=body.feedback,
        content=body.content,
    )
    return {"status": "ok"}


@app.get("/api/media/{file_path:path}")
async def serve_media(file_path: str):
    """
    Serve arbitrary image/video files by absolute path.
    Security: only paths within the pipeline's working_dir are served.
    """
    # Normalise to absolute path
    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    file_path = os.path.realpath(file_path)

    # Guard: must be within cwd
    cwd = os.path.realpath(os.getcwd())
    if not file_path.startswith(cwd + os.sep) and file_path != cwd:
        # Allow if pipeline working_dir is set
        if pipeline_instance is not None and pipeline_instance._idea2video is not None:
            allowed_root = os.path.realpath(pipeline_instance._idea2video.working_dir)
            if not file_path.startswith(allowed_root):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(file_path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path)


# ---------------------------------------------------------------------------
# Serve React build (must come last so it doesn't shadow API routes)
# ---------------------------------------------------------------------------

_ui_dist = os.path.join(os.path.dirname(__file__), "ui", "dist")
if os.path.isdir(_ui_dist):
    app.mount("/", StaticFiles(directory=_ui_dist, html=True), name="ui")
