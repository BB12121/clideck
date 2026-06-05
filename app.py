from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from agent_console.server import (
    create_app as create_agent_console_app,
    start_notification_monitor,
    stop_notification_monitor,
)


agent_console_app = create_agent_console_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_notification_monitor(agent_console_app)
    try:
        yield
    finally:
        await stop_notification_monitor(agent_console_app)


app = FastAPI(title="CliDeck", lifespan=lifespan)
app.mount("/agent-console", agent_console_app, name="agent-console")


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/agent-console/")
