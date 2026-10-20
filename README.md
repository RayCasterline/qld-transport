# Queensland Rail station sign

A live departure board for any South East Queensland rail station, built from the TransLink GTFS static and GTFS-realtime feeds. A FastAPI backend serves the sign page (`web/`) and a JSON API (`/api/stations`, `/api/departures`). The default station is Northgate, styled after its real sign.

## Examples

Roma Street shows a single list of the next 6 trains, with a scrolling message on the last line:

![Roma Street](docs/roma-street.png)

Other stations, such as Ipswich, show trains to the city and trains away from it:

![Ipswich](docs/ipswich.png)

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

Sections are split into trains heading to the city (inbound) and away from it (outbound). Northgate keeps the titles from its real sign ("City & South/West" and "North"); other stations use "City" and "Outbound". Roma Street shows a single list of the next 6 trains instead, with the 7th line reserved for a scrolling message. The messages are listed in `backend/city_messages.json` and change every minute.

### Adding your own Roma Street messages

Add your own text to the list in `backend/city_messages.json`; it is a plain JSON array of strings:

```json
[
  "Supervise Children at all Times to Ensure the Safety of your Children",
  "Customers, Please Keep Your Possessions With You at all Times",
  "Your own message here"
]
```

The file is read on every request, so changes show up without restarting the server. The sign rotates through the messages in order, one per minute.

The platform box next to the station box limits the board to one platform (`-` shows all). It then lists the next 6 trains in the direction that platform serves (so Northgate platform 3 also lists platform 1 trains; city platforms list only their own) and, every 20 seconds, switches to the stops of the next train calling at that platform, on its line colour (two columns with larger text when the stops fit, otherwise three), with a 2-second wipe (a thin yellow line moving down the screen) between the two pages. The choice is stored in the URL as `&platform=3`, and the API accepts `/api/departures?station=...&platform=3`. Both boxes fade out when the mouse has not moved for a few seconds.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `--station` | `Northgate` | Station shown when the page opens. |
| `--host` | `127.0.0.1` | Interface to bind to. Use `0.0.0.0` to serve other devices on the network. |
| `--port` | `8731` | Port to listen on. |
| `--show-attribution` | off | Show the TransLink data attribution / licence line at the bottom of the sign. |
| `--no-infer-ngr` | off | Stop guessing the train type from the run code (`D` = NGR icon, `J` = 3 CAR icon). With this, only runs listed in `backend/stock_sightings.csv` show an icon. |

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
