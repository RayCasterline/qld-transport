# Queensland Rail station sign

A live departure board for any South East Queensland rail station, built from the TransLink GTFS static and GTFS-realtime feeds. A FastAPI backend serves the sign page (`web/`) and a JSON API (`/api/stations`, `/api/departures`). The default station is Northgate, styled after its real sign.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Start

```bash
python -m backend
```

Then open <http://127.0.0.1:8731/>.

The default port is `8731`. The first run downloads and indexes the static GTFS feed (~35 MB), so startup takes a little longer. The feed is cached in `data/` and refreshed daily.

### Choosing a station

The sign opens on Northgate. Use the search box at the top right of the page: click it and type part of a station name, then pick from the list. The choice is stored in the URL, e.g. `http://127.0.0.1:8731/?station=Ipswich`, so you can bookmark it.

To change the default station, use `--station` (a station name, with or without " station"):

```bash
python -m backend --station "Fortitude Valley"
```

Sections are split into trains heading to the city (inbound) and away from it (outbound). Northgate keeps the titles from its real sign ("City & South/West" and "North"); other stations use "City" and "Outbound". Roma Street shows a single list of the next 6 trains instead, with the 7th line reserved for a message (`CITY_MESSAGE` in `backend/departures.py`).

### Options

| Option | Default | Description |
| --- | --- | --- |
| `--station` | `Northgate` | Station shown when the page opens. |
| `--host` | `127.0.0.1` | Interface to bind to. Use `0.0.0.0` to serve other devices on the network. |
| `--port` | `8731` | Port to listen on. |
| `--show-attribution` | off | Show the TransLink data attribution / licence line at the bottom of the sign. |

Example:

```bash
python -m backend --host 0.0.0.0 --port 9000 --show-attribution
```

### Running with uvicorn directly

```bash
uvicorn backend.app:app --port 8731
```

This skips the `--station` and `--show-attribution` options.

## Troubleshooting

- `address already in use`: another process is on the port. Check with `ss -ltnp 'sport = :8731'`, or start on a different port with `--port`.
