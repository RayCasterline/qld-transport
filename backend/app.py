"""FastAPI app: serves the station sign page and its JSON departures API."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import gtfs_static
from .departures import POLL_INTERVAL_S, RealtimeCache, build_board

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

rt_cache = RealtimeCache()
state: dict = {"index": None, "default_station": None}
# Set by `python -m backend --station NAME --show-attribution`.
settings: dict = {"show_attribution": False, "station": "Northgate"}
stop_event = threading.Event()


def _poll_loop() -> None:
    last_static_check = time.time()
    while not stop_event.is_set():
        rt_cache.refresh()
        # Re-check the static feed daily (the loader only downloads if the zip is >24h old).
        if state["index"] is not None and time.time() - last_static_check > 3600:
            last_static_check = time.time()
            try:
                state["index"] = gtfs_static.load_index()
            except Exception as exc:
                log.warning("Static feed refresh failed: %s", exc)
        stop_event.wait(POLL_INTERVAL_S)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Loading static GTFS index (first run downloads and indexes ~35 MB)...")
    state["index"] = gtfs_static.load_index()
    default = state["index"].find_station(settings["station"])
    if default is None:
        hint = ", ".join(state["index"].suggest_stations(settings["station"]))
        raise RuntimeError(
            f"Unknown station {settings['station']!r}" + (f" - did you mean: {hint}?" if hint else "")
        )
    state["default_station"] = default
    thread = threading.Thread(target=_poll_loop, daemon=True)
    thread.start()
    yield
    stop_event.set()


app = FastAPI(lifespan=lifespan)


@app.get("/api/stations")
def stations() -> dict:
    index = state["index"]
    return {
        "default": state["default_station"],
        "stations": sorted(
            ({"id": s.id, "name": s.name} for s in index.stations.values()),
            key=lambda s: s["name"],
        ),
    }


@app.get("/api/departures")
def departures(station: str | None = None) -> dict:
    index = state["index"]
    station_id = index.find_station(station) if station else state["default_station"]
    if station_id is None:
        raise HTTPException(status_code=404, detail=f"Unknown station: {station}")
    board = build_board(index, rt_cache, station_id)
    board["show_attribution"] = settings["show_attribution"]
    return board


@app.get("/")
def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
