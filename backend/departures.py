"""Realtime overlay and departure board construction for a station."""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from google.transit import gtfs_realtime_pb2

from .gtfs_static import StaticIndex, TripStop

log = logging.getLogger(__name__)

RT_URL = "https://gtfsrt.api.translink.com.au/api/realtime/SEQ/TripUpdates/Rail"
POLL_INTERVAL_S = 60
STALE_AFTER_S = 5 * 60
ROWS_PER_SECTION = 3
CITY_ROWS = 6  # Roma Street shows one list instead of two sections
# The 7th line of the Roma Street board is reserved for a message.
CITY_MESSAGE = "Supervise Children at all Times to Ensure the Safety of your Children"
LOOKAHEAD_S = 4 * 3600

# Queensland has no daylight saving, so a fixed UTC+10 offset is exact for Brisbane.
BRISBANE = timezone(timedelta(hours=10), "AEST")

# Names as shown on the physical sign.
DESTINATION_ALIASES = {"Kippa-Ring": "Redcliffe"}

# Line colours sampled from the real sign photo (images/QLD RAil.jpeg).
T1_GREEN = "#3A8014"       # Caboolture / Sunshine Coast / Ipswich
T2_LIGHT_BLUE = "#4A80E0"  # Redcliffe Peninsula / Springfield: softer cornflower blue as rendered on the sign
T4_BLUE = "#4A4AE6"        # Shorncliffe / Cleveland: purple-tinted blue as rendered on the sign
FERNY_GROVE_RED = "#B81212"  # Ferny Grove / Beenleigh: a pure, slightly dark red (GTFS D53E45 is too muted)
GOLD_COAST_GOLD = "#8C7A1E"  # Gold Coast / Airport: muted olive-gold as rendered on the sign
# Keyed by the GTFS route_color of each route.
MAP_COLORS = {
    "27855A": T1_GREEN,
    "6CC6E9": T2_LIGHT_BLUE,
    "4778B2": T4_BLUE,
    "D53E45": FERNY_GROVE_RED,
    "FFC425": GOLD_COAST_GOLD,
}
# The Northgate-Shorncliffe shuttle is tagged light blue in GTFS but is part of the T4 line.
ROUTE_COLOR_OVERRIDES = {"SHSH": T4_BLUE}

# Section titles as shown on the physical sign, by station: (inbound, outbound).
SECTION_TITLES = {"Northgate": ("City & South/West", "North")}
DEFAULT_SECTION_TITLES = ("City", "Outbound")


def _line_color(trip: TripStop) -> str:
    return ROUTE_COLOR_OVERRIDES.get(trip.route_short) or MAP_COLORS.get(trip.color.upper()) or "#" + trip.color

CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED


class RealtimeCache:
    """Polls the GTFS-Realtime trip updates feed and keeps the latest good snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trips: dict[tuple[str, str], gtfs_realtime_pb2.TripUpdate] = {}
        self.feed_timestamp: int | None = None
        self.last_success: float | None = None
        self.last_error: str | None = None

    def refresh(self) -> None:
        try:
            r = requests.get(RT_URL, timeout=30)
            r.raise_for_status()
            msg = gtfs_realtime_pb2.FeedMessage()
            msg.ParseFromString(r.content)
            trips = {}
            for e in msg.entity:
                if e.HasField("trip_update"):
                    tu = e.trip_update
                    trips[(tu.trip.trip_id, tu.trip.start_date)] = tu
            with self._lock:
                self._trips = trips
                self.feed_timestamp = msg.header.timestamp or None
                self.last_success = time.time()
                self.last_error = None
        except Exception as exc:  # keep serving the last good snapshot
            log.warning("Realtime refresh failed: %s", exc)
            with self._lock:
                self.last_error = str(exc)

    def get(self, trip_id: str, start_date: str):
        with self._lock:
            return self._trips.get((trip_id, start_date))

    @property
    def stale(self) -> bool:
        return self.last_success is None or time.time() - self.last_success > STALE_AFTER_S


def _delay_of(stu) -> int | None:
    if stu.departure.HasField("delay"):
        return stu.departure.delay
    if stu.arrival.HasField("delay"):
        return stu.arrival.delay
    return None


def _apply_realtime(trip: TripStop, scheduled: int, tu, platforms) -> tuple[int, str, bool]:
    """Return (departure_epoch, platform_stop_id, excluded) after overlaying realtime."""
    if tu is None:
        return scheduled, trip.stop_id, False
    if tu.trip.schedule_relationship == CANCELED:
        return scheduled, trip.stop_id, True

    actual, stop_id = scheduled, trip.stop_id
    before_delay = None
    for stu in tu.stop_time_update:
        if stu.stop_id in platforms:
            if stu.schedule_relationship == SKIPPED:
                return scheduled, stop_id, True
            stop_id = stu.stop_id
            if stu.departure.HasField("time") and stu.departure.time > 0:
                return stu.departure.time, stop_id, False
            delay = _delay_of(stu)
            return scheduled + (delay or 0), stop_id, False
        if stu.stop_sequence and stu.stop_sequence < trip.stop_sequence:
            d = _delay_of(stu)
            if d is not None:
                before_delay = d  # delay propagates to later stops
    return scheduled + (before_delay or 0), stop_id, False


def _destination(trip: TripStop) -> str:
    name = trip.headsign.removesuffix(" station")
    name = DESTINATION_ALIASES.get(name, name)
    if trip.via_city:
        return "City" if name == "Roma Street" else f"City & {name}"
    return name


def _fmt_time(epoch: int) -> str:
    dt = datetime.fromtimestamp(epoch, BRISBANE)
    return f"{dt.hour % 12 or 12}:{dt.minute:02d}"


def build_board(
    index: StaticIndex, rt: RealtimeCache, station_id: str, now: float | None = None
) -> dict:
    now = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now, BRISBANE)
    view = index.station_view(station_id)
    platforms = view.station.platforms
    sections: dict[bool, list[dict]] = {True: [], False: []}

    # Today plus yesterday (trips running past midnight carry times > 24:00:00).
    for day in (now_dt.date() - timedelta(days=1), now_dt.date()):
        services = index.active_services(day)
        midnight = int(datetime(day.year, day.month, day.day, tzinfo=BRISBANE).timestamp())
        start_date = day.strftime("%Y%m%d")
        for trip in view.trips:
            if trip.is_last or trip.service_id not in services:
                continue
            scheduled = midnight + trip.dep_seconds
            if scheduled < now - 3600 or scheduled > now + LOOKAHEAD_S:
                continue
            actual, stop_id, excluded = _apply_realtime(
                trip, scheduled, rt.get(trip.trip_id, start_date), platforms
            )
            if excluded or actual < now or actual > now + LOOKAHEAD_S:
                continue
            sections[trip.inbound].append(
                {
                    "scheduled": _fmt_time(scheduled),
                    "destination": _destination(trip),
                    "express": trip.express,
                    "platform": platforms.get(stop_id, "-"),
                    "departs_epoch": actual,
                    "minutes": math.floor((actual - now) / 60),
                    "route": trip.route_short,
                    "color": _line_color(trip),
                    "realtime": rt.get(trip.trip_id, start_date) is not None,
                }
            )

    def top(rows: list[dict], n: int = ROWS_PER_SECTION) -> list[dict]:
        return sorted(rows, key=lambda r: r["departs_epoch"])[:n]

    board = {
        "now_epoch": int(now),
        "station_id": view.station.id,
        "station": view.station.name,
        "realtime_timestamp": rt.feed_timestamp,
        "realtime_stale": rt.stale,
        "message": "",
    }
    if station_id == index.city_id:
        # Every train leaves the city, so inbound/outbound is meaningless: one longer list.
        board.update(
            title=f"Next (1-{CITY_ROWS}) Trains",
            single_section=True,
            inbound_heading="Service",
            outbound_heading="",
            message=CITY_MESSAGE,
            inbound=top(sections[True] + sections[False], CITY_ROWS),
            outbound=[],
        )
        return board
    inbound_title, outbound_title = SECTION_TITLES.get(view.station.name, DEFAULT_SECTION_TITLES)
    board.update(
        title="Next 3 Inbound and Outbound Trains",
        single_section=False,
        inbound_heading=f"Next Trains {inbound_title}",
        outbound_heading=f"Next Trains {outbound_title}",
        inbound=top(sections[True]),
        outbound=top(sections[False]),
    )
    return board
