"""FastAPI app: web chat UI + JSON API. Keeps the play-by-play index fed in the background."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .client import ApiError, MlbClient
from .cutoff import cutoff_date
from .engine import Asker
from .index import db as dbmod
from .index.sync import FIRST_PBP_SEASON, coverage, sync_recent, sync_seasons
from .timeparse import season_of

log = logging.getLogger("mlbasker")
STATIC = Path(__file__).parent / "static"
REFRESH_SECONDS = 6 * 3600


class AskRequest(BaseModel):
    question: str


def _feeder(state: dict) -> None:
    """Background loop: backfill the index once, then top it up through yesterday."""
    con = dbmod.connect()          # own connection: sqlite objects are per-thread here
    client = MlbClient()
    first = int(os.environ.get("MLBASKER_FIRST_SEASON", FIRST_PBP_SEASON))
    while True:
        try:
            state["syncing"] = True
            sync_seasons(con, client, first, season_of(cutoff_date()), progress=lambda m: log.info(m))
            state["last_sync"] = time.time()
        except ApiError as e:
            log.warning("index refresh failed: %s", e)
        except Exception:
            log.exception("index refresh crashed")
        finally:
            state["syncing"] = False
        time.sleep(REFRESH_SECONDS)


def create_app(*, feed: bool = True) -> FastAPI:
    state: dict = {"syncing": False, "last_sync": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if feed and os.environ.get("MLBASKER_NO_FEED") != "1":
            threading.Thread(target=_feeder, args=(state,), daemon=True).start()
        yield

    app = FastAPI(title="mlb-stat-asker", lifespan=lifespan)
    client = MlbClient()
    index = dbmod.connect()
    lock = threading.Lock()        # one shared sqlite connection → serialise use
    asker = Asker(client=client, index=index)

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict:
        try:
            with lock:
                ans = asker.ask(req.question)
            if state["syncing"] and ans.kind == "answer" and ans.footnote:
                ans.footnote += " ⚠️ The history index is still being updated, so results may be incomplete."
            return ans.to_dict()
        except ApiError as e:
            return {"text": f"The MLB API didn't respond: {e}", "columns": [], "rows": [],
                    "footnote": "", "kind": "error"}

    @app.get("/api/status")
    def status() -> dict:
        with lock:
            cov = coverage(index)
        return {"cutoff": cutoff_date().isoformat(), "index": cov, **state}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
