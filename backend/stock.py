"""NGR (New Generation Rollingstock) lookup built from trainspotter sightings.

The GTFS feeds do not say what stock runs a service, so a service is assumed to be older
stock (SMU/IMU) unless there is good evidence it is an NGR. The evidence is the sightings in
backend/stock_sightings.csv: each row says "run code X was seen to be stock Y", where the run
code is the last part of the trip ID (e.g. "...-44306-D103" is run D103).

  * An exact run code is NGR once it has at least MIN_RUN_SIGHTINGS sightings and more of them
    say NGR than anything else.
  * A glob ("D1*" for every D1xx run) needs MIN_PATTERN_SIGHTINGS, as it is a broader claim.
  * The most specific pattern wins, so a not-NGR sighting of D139 beats an NGR sighting of D1*.
  * Trips that call at a station NGRs are not permitted to serve (Ferny Grove line, west of
    Ipswich) are never NGR.

The file is re-read whenever it changes, so edits apply without restarting the server.

Optionally (python -m backend --infer-ngr) a weaker guess fills the gaps: the first character
of a run code is the train type, and "D" is the only prefix on the Gold Coast/Airport lines
(NGR territory) and never appears on Ferny Grove or west of Ipswich, so a "D" run is guessed
to be NGR. It matched 5 of 6 rows on a real sign, so it is off by default.

    python -m backend.stock add D880 NGR --source "platform photo"
    python -m backend.stock add D139 IMU --source "platform photo"
    python -m backend.stock add "D1*" IMU --source "..."
    python -m backend.stock check
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, timedelta
from fnmatch import fnmatchcase
from pathlib import Path

SIGHTINGS_PATH = Path(__file__).with_name("stock_sightings.csv")
FIELDS = ["run", "stock", "date", "source"]
STOCK_TYPES = {"NGR", "SMU", "IMU", "EMU", "ICE", "OTHER"}  # anything but NGR means "not NGR"
NGR_RUN_PREFIXES = {"D"}
MIN_RUN_SIGHTINGS = 1
MIN_PATTERN_SIGHTINGS = 2
NGR_FLEET = 75  # six-car sets, units 701-775 (Wikipedia: New Generation Rollingstock)
USE_RUN_CODE_RULE = False  # set by `python -m backend --infer-ngr`

_cache: dict = {"mtime": None, "votes": {}}


def run_code(trip_id: str) -> str:
    return trip_id.rsplit("-", 1)[-1].upper()


def _read_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        lines = (ln for ln in f if ln.strip() and not ln.lstrip().startswith("#"))
        return list(csv.DictReader(lines))


def _votes() -> dict[str, Counter]:
    """Per run code: how many sightings were NGR vs not NGR."""
    try:
        mtime = SIGHTINGS_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        _cache.update(mtime=None, votes={})
        return _cache["votes"]
    if mtime != _cache["mtime"]:
        votes: dict[str, Counter] = defaultdict(Counter)
        for row in _read_rows(SIGHTINGS_PATH):
            run, stock = (row.get("run") or "").strip().upper(), (row.get("stock") or "").strip().upper()
            if run and stock in STOCK_TYPES:
                votes[run]["NGR" if stock == "NGR" else "OTHER"] += 1
        _cache.update(mtime=mtime, votes=dict(votes))
    return _cache["votes"]


def rule_says_ngr(run: str) -> bool:
    return run[:1] in NGR_RUN_PREFIXES


def sighting_says_ngr(run: str) -> bool | None:
    """True/False when enough sightings exist, otherwise None.

    Of the patterns matching this run code, the most specific one (most literal characters,
    so an exact code beats "D1*") that has enough sightings and no tie decides.
    """
    best: tuple[int, int, bool] | None = None
    for pattern, c in _votes().items():
        total = sum(c.values())
        is_glob = any(ch in pattern for ch in "*?[")
        needed = MIN_PATTERN_SIGHTINGS if is_glob else MIN_RUN_SIGHTINGS
        if total < needed or c["NGR"] == c["OTHER"] or not fnmatchcase(run, pattern):
            continue
        specificity = sum(ch not in "*?[]" for ch in pattern)
        candidate = (specificity, total, c["NGR"] > c["OTHER"])
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return None if best is None else best[2]


def is_ngr(trip_id: str, barred: bool = False) -> bool:
    """Assume older stock unless sightings (or the opt-in run-code guess) say NGR.

    barred: the trip calls at a station NGRs cannot serve (see gtfs_static.NGR_BARRED_STATIONS).
    """
    if barred:
        return False
    run = run_code(trip_id)
    seen = sighting_says_ngr(run)
    if seen is not None:
        return seen
    return USE_RUN_CODE_RULE and rule_says_ngr(run)


def _append(run: str, stock: str, when: str, source: str) -> None:
    new = not SIGHTINGS_PATH.exists()
    with open(SIGHTINGS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(FIELDS)
        writer.writerow([run, stock, when, source])


def _add(args: argparse.Namespace) -> None:
    run, stock = args.run.strip().upper(), args.stock.strip().upper()
    if stock not in STOCK_TYPES:
        raise SystemExit(f"stock must be one of: {', '.join(sorted(STOCK_TYPES))}")
    _append(run, stock, args.date or date.today().isoformat(), args.source or "")
    print(f"Recorded {run} = {stock}")


def _sign(args: argparse.Namespace) -> None:
    """Turn the rows of a sign photo (time, platform, NGR icon or not) into sightings."""
    from .gtfs_static import load_index

    index = load_index()
    station_id = index.find_station(args.station)
    if station_id is None:
        raise SystemExit(f"unknown station {args.station!r}")
    view = index.station_view(station_id)
    day = date.fromisoformat(args.date) if args.date else date.today()
    active = index.active_services(day)
    platforms = view.station.platforms
    window = None
    if args.time:  # the sign shows no am/pm: only consider departures soon after the photo was taken
        h, m = args.time.split(":")
        start = int(h) * 3600 + int(m) * 60
        window = (start - 15 * 60, start + 3 * 3600)
    for raw in args.rows:
        parts = raw.split()
        if len(parts) < 2:
            raise SystemExit(f"row must be 'H:MM PLATFORM [NGR|-] [DESTINATION]', got {raw!r}")
        shown, platform = parts[0], parts[1]
        stock = "NGR" if len(parts) > 2 and parts[2].upper() == "NGR" else "OTHER"
        dest = " ".join(parts[3:]).lower()
        found = {
            run_code(t.trip_id): t.headsign for t in view.trips
            if t.service_id in active and not t.is_last and platforms.get(t.stop_id) == platform
            and f"{t.dep_seconds // 3600 % 24 % 12 or 12}:{t.dep_seconds // 60 % 60:02d}" == shown
            and dest in t.headsign.lower()
            and (window is None or window[0] <= t.dep_seconds <= window[1])
        }
        runs = sorted(found)
        if len(runs) != 1:
            options = ', '.join(f"{r} ({found[r].removesuffix(' station')})" for r in runs)
            print(f"{raw:28} -> {'no matching trip' if not runs else 'ambiguous, add a destination: ' + options} (skipped)")
            continue
        print(f"{raw:28} -> {runs[0]} = {stock}")
        if args.save:
            _append(runs[0], stock, day.isoformat(), f"sign photo {view.station.name} {day}")
    if not args.save:
        print("\nDry run: add --save to record these sightings.")


def _check(_: argparse.Namespace) -> None:
    from .gtfs_static import load_index

    rows = _read_rows(SIGHTINGS_PATH) if SIGHTINGS_PATH.exists() else []
    index = load_index()
    examples: dict[str, str] = {}
    for trip in index.trips:
        examples.setdefault(run_code(trip.trip_id), f"{trip.route_short} to {trip.headsign}")
    agree = disagree = 0
    for row in rows:
        pattern, stock = row["run"].strip().upper(), row["stock"].strip().upper()
        sighted = stock == "NGR"
        matches = [r for r in examples if fnmatchcase(r, pattern)]
        if not matches:
            print(f"{pattern:6} sighted {stock:5} NOT IN FEED")
            continue
        wrong = [r for r in matches if rule_says_ngr(r) != sighted]
        agree += not wrong
        disagree += bool(wrong)
        verdict = "agrees" if not wrong else "DISAGREES"
        where = examples[matches[0]] + (f" (+{len(matches) - 1} more runs)" if len(matches) > 1 else "")
        print(f"{pattern:6} sighted {stock:5} rule says {'NGR' if rule_says_ngr(matches[0]) else 'not NGR':7} "
              f"{verdict:9} {where}")
    print(f"\n{len(rows)} sightings: the run-code rule agrees with {agree}, disagrees with {disagree}.")

    flagged = sorted(r for r in examples if is_ngr(r))
    print(f"Run codes treated as NGR (sightings{' + run-code guess' if USE_RUN_CODE_RULE else ' only'}): "
          f"{', '.join(flagged) or 'none'}")
    barred = [t for t in index.trips if t.trip_id in index.ngr_barred and rule_says_ngr(run_code(t.trip_id))]
    print(f"Trips the run-code rule calls NGR but that call at a station NGRs cannot serve: {len(barred)} "
          f"(e.g. {', '.join(sorted({run_code(t.trip_id) for t in barred})[:6]) or 'none'})")

    # Fleet-size sanity check on the next weekday that has services.
    day = date.today()
    for _ in range(14):
        active = index.active_services(day)
        trips = [t for t in index.trips if t.service_id in active]
        if day.weekday() < 5 and trips:
            break
        day += timedelta(days=1)
    else:
        return
    spans = [(t.stops[0][2], t.stops[-1][2]) for t in trips if is_ngr(t.trip_id, t.trip_id in index.ngr_barred)]
    peak = max(sum(a <= s <= b for a, b in spans) for s in range(4 * 3600, 26 * 3600, 300))
    flag = "OK" if peak <= NGR_FLEET else "TOO MANY: the rule over-detects NGR"
    print(f"Peak NGR trips in service at once on {day:%a %d %b}: {peak} of {NGR_FLEET} sets ({flag})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trainspotter stock lookup")
    sub = parser.add_subparsers(required=True)
    add = sub.add_parser("add", help="record a sighting")
    add.add_argument("run", help="run code, e.g. D880 (the end of the trip ID)")
    add.add_argument("stock", help="NGR, SMU, IMU, EMU, ICE or OTHER")
    add.add_argument("--date", help="YYYY-MM-DD (default today)")
    add.add_argument("--source", help="where the sighting came from")
    add.set_defaults(func=_add)
    sign = sub.add_parser(
        "sign", help="turn the rows of a real sign into sightings, e.g. sign --station Northgate "
                     "--date 2026-10-19 --time 17:30 '5:42 1 NGR Cleveland' '5:57 1 - Cleveland' --save")
    sign.add_argument("rows", nargs="+",
                      help="'H:MM PLATFORM NGR|- [DESTINATION]': NGR if the icon is shown, - if not; "
                           "the destination word is needed when two trains share a time and platform")
    sign.add_argument("--station", required=True)
    sign.add_argument("--date", help="YYYY-MM-DD the sign was photographed (default today)")
    sign.add_argument("--time", help="HH:MM (24h) the sign was photographed; resolves am/pm")
    sign.add_argument("--save", action="store_true", help="record the sightings (default is a dry run)")
    sign.set_defaults(func=_sign)
    check = sub.add_parser("check", help="compare sightings with the run-code rule and the feed")
    check.set_defaults(func=_check)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
