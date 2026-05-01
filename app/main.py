import asyncio
import logging

from anyio import to_thread
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from aiavatar.adapter.websocket.server import WebSocketSessionData

from .components import build_aiavatar_server
from .settings import BACKEND_ROOT, Settings


# Load backend/.env before constructing settings and AI components.
# Write API keys in backend/.env, not in this file.
load_dotenv(BACKEND_ROOT / ".env")

settings = Settings()
logger = logging.getLogger(__name__)
_aiavatar_server = None
_aiavatar_lock = asyncio.Lock()

app = FastAPI(
    title="Chat App Realtime Voice Backend",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


async def get_aiavatar_server():
    """Build the voice pipeline lazily.

    Health checks and config inspection should work even before the heavy
    Silero VAD model is downloaded. The first WebSocket connection performs the
    actual VAD/STT/LLM/TTS initialization.
    """

    global _aiavatar_server
    if _aiavatar_server is not None:
        return _aiavatar_server

    async with _aiavatar_lock:
        if _aiavatar_server is None:
            _aiavatar_server = await to_thread.run_sync(build_aiavatar_server, settings)
    return _aiavatar_server


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    server = await get_aiavatar_server()

    subprotocol = server._authenticate_websocket(websocket)
    await websocket.accept(subprotocol=subprotocol)

    session_data = WebSocketSessionData()

    try:
        while True:
            await server.process_websocket(websocket, session_data)
    except WebSocketDisconnect as ex:
        logger.info("WebSocket disconnected: session_id=%s code=%s", session_data.id, ex.code)
    except Exception as ex:
        error_message = str(ex)
        if "WebSocket is not connected" in error_message:
            logger.info("WebSocket disconnected: session_id=%s", session_data.id)
        elif "<CloseCode.NO_STATUS_RCVD: 1005>" in error_message:
            logger.info("WebSocket disconnected without status: session_id=%s", session_data.id)
        else:
            raise
    finally:
        if session_data.id:
            if server._on_disconnect:
                await server._on_disconnect(session_data)
            await server.sts.finalize(session_data.id)
            server.websockets.pop(session_data.id, None)
            server.sessions.pop(session_data.id, None)


@app.get("/health")
async def health():
    return {
        "ok": True,
    }
