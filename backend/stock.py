"""NGR (New Generation Rollingstock) lookup built from trainspotter sightings.

The GTFS feeds do not say what stock runs a service, but the last part of each trip ID is
Queensland Rail's run code (e.g. "...-44306-D103"). Its first character is the train type.
Observed in the SEQ feed: "D" is the only prefix on the Gold Coast/Airport lines (NGR
territory) and never appears on Ferny Grove or west of Ipswich, where NGRs cannot run (those
use "1"/"T"/"J"). So by default a "D" run is treated as NGR. This is an inference.

Sightings in backend/stock_sightings.csv correct that rule. Each row says "run code X was
seen to be stock Y". A run code's sightings override the rule only once there are at least
MIN_SIGHTINGS of them and they do not tie, so a single swapped train can't change the sign.
The file is re-read whenever it changes, so edits apply without restarting the server.

    python -m backend.stock add D139 IMU --source "platform photo"
    python -m backend.stock check
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

SIGHTINGS_PATH = Path(__file__).with_name("stock_sightings.csv")
FIELDS = ["run", "stock", "date", "source"]
STOCK_TYPES = {"NGR", "SMU", "IMU", "EMU", "ICE", "OTHER"}  # anything but NGR means "not NGR"
NGR_RUN_PREFIXES = {"D"}
MIN_SIGHTINGS = 2

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
    """True/False when enough agreeing sightings exist, otherwise None."""
    c = _votes().get(run)
    if c and sum(c.values()) >= MIN_SIGHTINGS and c["NGR"] != c["OTHER"]:
        return c["NGR"] > c["OTHER"]
    return None


def is_ngr(trip_id: str) -> bool:
    run = run_code(trip_id)
    seen = sighting_says_ngr(run)
    return rule_says_ngr(run) if seen is None else seen


def _add(args: argparse.Namespace) -> None:
    run, stock = args.run.strip().upper(), args.stock.strip().upper()
    if stock not in STOCK_TYPES:
        raise SystemExit(f"stock must be one of: {', '.join(sorted(STOCK_TYPES))}")
    new = not SIGHTINGS_PATH.exists()
    with open(SIGHTINGS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(FIELDS)
        writer.writerow([run, stock, args.date or date.today().isoformat(), args.source or ""])
    print(f"Recorded {run} = {stock}")


def _check(_: argparse.Namespace) -> None:
    from .gtfs_static import load_index

    rows = _read_rows(SIGHTINGS_PATH) if SIGHTINGS_PATH.exists() else []
    index = load_index()
    examples = {}
    for trip in index.trips:
        examples.setdefault(run_code(trip.trip_id), f"{trip.route_short} to {trip.headsign}")
    agree = disagree = 0
    for row in rows:
        run, stock = row["run"].strip().upper(), row["stock"].strip().upper()
        sighted = stock == "NGR"
        in_feed = examples.get(run, "NOT IN FEED")
        rule = rule_says_ngr(run)
        verdict = "agrees" if rule == sighted else "DISAGREES"
        agree += rule == sighted
        disagree += rule != sighted
        print(f"{run:6} sighted {stock:5} rule says {'NGR' if rule else 'not NGR':7} {verdict:9} {in_feed}")
    print(f"\n{len(rows)} sightings: the run-code rule agrees with {agree}, disagrees with {disagree}.")
    overriding = [r for r in _votes() if sighting_says_ngr(r) is not None and sighting_says_ngr(r) != rule_says_ngr(r)]
    print(f"Run codes overridden by the table (need {MIN_SIGHTINGS}+ agreeing sightings): "
          f"{', '.join(sorted(overriding)) or 'none'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trainspotter stock lookup")
    sub = parser.add_subparsers(required=True)
    add = sub.add_parser("add", help="record a sighting")
    add.add_argument("run", help="run code, e.g. D880 (the end of the trip ID)")
    add.add_argument("stock", help="NGR, SMU, IMU, EMU, ICE or OTHER")
    add.add_argument("--date", help="YYYY-MM-DD (default today)")
    add.add_argument("--source", help="where the sighting came from")
    add.set_defaults(func=_add)
    check = sub.add_parser("check", help="compare sightings with the run-code rule and the feed")
    check.set_defaults(func=_check)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
