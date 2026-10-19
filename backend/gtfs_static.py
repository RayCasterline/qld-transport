"""Static GTFS handling: download the SEQ feed and index rail departures for every station."""
from __future__ import annotations

import csv
import difflib
import io
import math
import os
import pickle
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

STATIC_URL = "https://gtfsrt.api.translink.com.au/GTFS/SEQ_GTFS.zip"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ZIP_PATH = DATA_DIR / "SEQ_GTFS.zip"
INDEX_PATH = DATA_DIR / "rail_index.pkl"
INDEX_VERSION = 4
MAX_ZIP_AGE_S = 24 * 3600

RAIL_ROUTE_TYPE = "2"
# Trains calling at this station after the sign's station are heading to the city.
CITY_STATION = "Roma Street"


@dataclass
class Station:
    id: str
    name: str  # without the " station" suffix
    lat: float
    lon: float
    platforms: dict[str, str]  # platform stop_id -> platform label


@dataclass
class RailTrip:
    trip_id: str
    service_id: str
    route_short: str
    route_long: str
    color: str
    text_color: str
    headsign: str
    stops: list[tuple[int, str, int]]  # (stop_sequence, stop_id, dep_seconds), in order


@dataclass
class TripStop:
    """One trip's call at the station a StationView was built for."""
    trip_id: str
    service_id: str
    route_short: str
    route_long: str
    color: str
    text_color: str
    headsign: str
    inbound: bool  # heading towards the city
    via_city: bool  # calls at the city station after this one
    stop_id: str  # platform stop used by this trip
    stop_sequence: int
    dep_seconds: int  # seconds after midnight of the service day (may exceed 86400)
    is_last: bool  # the station is the final stop (no departure)
    express: bool


@dataclass
class StationView:
    station: Station
    trips: list[TripStop]


def _rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def _hms(value: str) -> int:
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def ensure_zip(force: bool = False) -> Path:
    """Download the static feed if missing or older than a day."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not force and ZIP_PATH.exists() and time.time() - ZIP_PATH.stat().st_mtime < MAX_ZIP_AGE_S:
        return ZIP_PATH
    tmp = ZIP_PATH.with_suffix(".tmp")
    try:
        with requests.get(STATIC_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        zipfile.ZipFile(tmp).testzip()
        os.replace(tmp, ZIP_PATH)
    except Exception:
        tmp.unlink(missing_ok=True)
        if not ZIP_PATH.exists():
            raise
    return ZIP_PATH


def _normalise(name: str) -> str:
    return name.strip().lower().removesuffix(" station").strip()


class StaticIndex:
    def __init__(
        self,
        trips: list[RailTrip],
        stations: dict[str, Station],
        stop_station: dict[str, str],
        calendar: dict,
        calendar_dates: dict,
    ):
        self.trips = trips
        self.stations = stations
        self.stop_station = stop_station
        self.calendar = calendar
        self.calendar_dates = calendar_dates
        self.city_id = next((s.id for s in stations.values() if s.name == CITY_STATION), None)
        self._by_name = {_normalise(s.name): s.id for s in stations.values()}
        self._views: dict[str, StationView] = {}
        # station id -> [(trip, index of its first call at that station)]
        self._calls: dict[str, list[tuple[RailTrip, int]]] = defaultdict(list)
        for trip in trips:
            seen: set[str] = set()
            for i, (_, stop_id, _) in enumerate(trip.stops):
                sid = stop_station[stop_id]
                if sid not in seen:
                    seen.add(sid)
                    self._calls[sid].append((trip, i))

    def find_station(self, query: str) -> str | None:
        """Resolve a station id or (case-insensitive) name to a station id."""
        if query in self.stations:
            return query
        return self._by_name.get(_normalise(query))

    def suggest_stations(self, query: str) -> list[str]:
        names = {s.name for s in self.stations.values()}
        return difflib.get_close_matches(query.strip().title(), names, n=5, cutoff=0.5)

    def station_view(self, station_id: str) -> StationView:
        view = self._views.get(station_id)
        if view is None:
            view = self._views[station_id] = self._build_view(station_id)
        return view

    def _distance_to_city(self, station_id: str) -> float:
        city, s = self.stations[self.city_id], self.stations[station_id]
        return math.hypot(s.lat - city.lat, (s.lon - city.lon) * math.cos(math.radians(s.lat)))

    def _build_view(self, station_id: str) -> StationView:
        station = self.stations[station_id]
        raw = []
        for trip, idx in self._calls.get(station_id, []):
            sequence = [self.stop_station[s[1]] for s in trip.stops]
            via_city = self.city_id is not None and self.city_id in sequence[idx + 1:]
            if via_city:
                inbound = True
            elif self.city_id is not None and self.city_id in sequence[:idx]:
                inbound = False
            else:  # never touches the city: compare the end of the line with this station
                inbound = (
                    self.city_id is not None
                    and self._distance_to_city(sequence[-1]) < self._distance_to_city(station_id)
                )
            raw.append((trip, idx, inbound, via_city, tuple(sequence[idx:])))

        # A trip is express if it skips a station that another trip in the same direction
        # calls at between two of this trip's consecutive stops (e.g. Northgate -> Petrie
        # on the Caboolture line, where Redcliffe trains stop at Virginia..Bray Park).
        gaps: dict[bool, set[tuple[str, str]]] = {True: set(), False: set()}
        for inbound in gaps:
            for pattern in {p for _, _, i, _, p in raw if i == inbound}:
                for i in range(len(pattern)):
                    for j in range(i + 2, len(pattern)):
                        gaps[inbound].add((pattern[i], pattern[j]))

        trips = []
        for trip, idx, inbound, via_city, pattern in raw:
            express = any((a, b) in gaps[inbound] for a, b in zip(pattern, pattern[1:]))
            seq, stop_id, dep = trip.stops[idx]
            trips.append(
                TripStop(
                    trip_id=trip.trip_id,
                    service_id=trip.service_id,
                    route_short=trip.route_short,
                    route_long=trip.route_long,
                    color=trip.color,
                    text_color=trip.text_color,
                    headsign=trip.headsign,
                    inbound=inbound,
                    via_city=via_city,
                    stop_id=stop_id,
                    stop_sequence=seq,
                    dep_seconds=dep,
                    is_last=idx == len(trip.stops) - 1,
                    express=express,
                )
            )
        return StationView(station, trips)

    def active_services(self, day: date) -> set[str]:
        ymd = day.strftime("%Y%m%d")
        weekday = day.strftime("%A").lower()
        active = {
            sid
            for sid, c in self.calendar.items()
            if c["start"] <= ymd <= c["end"] and c[weekday]
        }
        for sid, exc in self.calendar_dates.get(ymd, []):
            if exc == "1":
                active.add(sid)
            elif exc == "2":
                active.discard(sid)
        return active


def _build(zip_path: Path) -> StaticIndex:
    zf = zipfile.ZipFile(zip_path)

    routes = {r["route_id"]: r for r in _rows(zf, "routes.txt") if r["route_type"] == RAIL_ROUTE_TYPE}
    trip_meta = {r["trip_id"]: r for r in _rows(zf, "trips.txt") if r["route_id"] in routes}

    stops_by_trip: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
    for r in _rows(zf, "stop_times.txt"):
        if r["trip_id"] in trip_meta:
            dep = r["departure_time"] or r["arrival_time"]
            stops_by_trip[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], _hms(dep)))

    trips: list[RailTrip] = []
    for tid, rows in stops_by_trip.items():
        rows.sort()
        meta = trip_meta[tid]
        route = routes[meta["route_id"]]
        trips.append(
            RailTrip(
                trip_id=tid,
                service_id=meta["service_id"],
                route_short=route["route_short_name"],
                route_long=route["route_long_name"],
                color=route["route_color"] or "FFFFFF",
                text_color=route["route_text_color"] or "FFFFFF",
                headsign=meta["trip_headsign"],
                stops=rows,
            )
        )

    # Stations are the parents of the platform stops that rail trips call at.
    all_stops = {r["stop_id"]: r for r in _rows(zf, "stops.txt")}
    used_stops = {s[1] for t in trips for s in t.stops}
    stations: dict[str, Station] = {}
    stop_station: dict[str, str] = {}
    for stop_id in used_stops:
        stop = all_stops[stop_id]
        sid = stop["parent_station"] or stop_id
        parent = all_stops.get(sid, stop)
        station = stations.get(sid)
        if station is None:
            station = stations[sid] = Station(
                id=sid,
                name=parent["stop_name"].removesuffix(" station"),
                lat=float(parent["stop_lat"]),
                lon=float(parent["stop_lon"]),
                platforms={},
            )
        station.platforms[stop_id] = stop["platform_code"] or "-"
        stop_station[stop_id] = sid

    calendar = {
        r["service_id"]: {
            "start": r["start_date"],
            "end": r["end_date"],
            **{d: r[d] == "1" for d in (
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")},
        }
        for r in _rows(zf, "calendar.txt")
    }
    calendar_dates: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in _rows(zf, "calendar_dates.txt"):
        calendar_dates[r["date"]].append((r["service_id"], r["exception_type"]))

    return StaticIndex(trips, stations, stop_station, calendar, dict(calendar_dates))


def load_index(force_download: bool = False) -> StaticIndex:
    zip_path = ensure_zip(force_download)
    stamp = (INDEX_VERSION, zip_path.stat().st_mtime_ns, zip_path.stat().st_size)
    if INDEX_PATH.exists():
        try:
            with open(INDEX_PATH, "rb") as f:
                cached_stamp, index = pickle.load(f)
            if cached_stamp == stamp:
                return index
        except Exception:
            pass
    index = _build(zip_path)
    with open(INDEX_PATH, "wb") as f:
        pickle.dump((stamp, index), f)
    return index
