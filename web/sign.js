"use strict";

const TZ = "Australia/Brisbane";
const clockEl = document.getElementById("clock");
const statusEl = document.getElementById("status");
const stage = document.getElementById("stage");
const stationInput = document.getElementById("station-input");
const stationList = document.getElementById("station-list");
const messageEl = document.getElementById("message");
const messageText = messageEl.firstElementChild;
const ngrTemplate = document.getElementById("ngr-icon");
const MESSAGE_SPEED = 160; // px per second, in the 1920px design canvas
let messageShown = "";

let board = null;
let fetchOk = false;
let stationId = new URLSearchParams(location.search).get("station"); // null = server default
const stationIds = new Map(); // lower-case station name -> id

const clockFmt = new Intl.DateTimeFormat("en-AU", {
  timeZone: TZ, hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
});

function fitStage() {
  const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(window.innerWidth - 1920 * s) / 2}px`;
  stage.style.top = `${(window.innerHeight - 1080 * s) / 2}px`;
}

function tickClock() {
  // "5:30:39 pm" -> "5:30:39"
  clockEl.textContent = clockFmt.format(new Date()).replace(/\s?[ap]m$/i, "");
}

function minutesText(departsEpoch) {
  const mins = Math.floor((departsEpoch - Date.now() / 1000) / 60);
  return mins <= 0 ? "Now" : `${mins} min`;
}

function renderSection(id, rows) {
  const container = document.querySelector(`#${id} .rows`);
  container.replaceChildren();
  for (const r of rows) {
    const el = document.createElement("div");
    el.className = "row";
    el.style.background = r.color;
    el.style.setProperty("--row", r.color);
    el.dataset.departs = r.departs_epoch;
    for (const [cls, text] of [
      ["time", r.scheduled],
      ["dest", r.destination],
      ["exp", r.express ? "Exp" : ""],
      ["platform", r.platform],
      ["minutes", minutesText(r.departs_epoch)],
    ]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      el.appendChild(span);
      // NGR pictogram sits between the Platform and Departs columns.
      if (cls === "platform" && r.ngr) {
        const icon = ngrTemplate.content.firstElementChild.cloneNode(true);
        el.appendChild(icon);
      }
    }
    container.appendChild(el);
  }
}

function renderStatus() {
  statusEl.replaceChildren();
  const left = document.createElement("span");
  if (board && board.show_attribution) {
    left.textContent = "Data: TransLink (State of Queensland) GTFS / GTFS Realtime, CC BY 4.0";
  }
  const right = document.createElement("span");
  if (!fetchOk) {
    right.className = "warn";
    right.textContent = "Server unreachable - showing last known data";
  } else if (board && board.realtime_stale) {
    right.className = "warn";
    right.textContent = "Realtime feed unavailable - showing timetable";
  }
  statusEl.append(left, right);
}

function setMessage(text) {
  if (text === messageShown) return;  // keep the marquee running between refreshes
  messageShown = text;
  messageText.classList.remove("scroll");
  messageText.replaceChildren();
  if (!text) return;
  // Two copies back to back, so the text loops seamlessly.
  for (let i = 0; i < 2; i++) {
    const item = document.createElement("span");
    item.className = "item";
    item.textContent = text;
    messageText.appendChild(item);
  }
  const width = messageText.offsetWidth / 2;  // one copy, including its trailing gap
  messageText.style.setProperty("--to", `-${width}px`);
  messageText.style.setProperty("--dur", `${width / MESSAGE_SPEED / 0.88}s`);  // 12% of the cycle is the pause
  messageText.classList.add("scroll");
}

function render() {
  setMessage(board ? board.message : "");
  renderSection("inbound", board ? board.inbound : []);
  renderSection("outbound", board ? board.outbound : []);
  if (board) {
    document.getElementById("title").textContent = board.title;
    document.getElementById("outbound").hidden = board.single_section;
    document.querySelector("#inbound .name").textContent = board.inbound_heading;
    document.querySelector("#outbound .name").textContent = board.outbound_heading;
    document.title = `${board.station} Station`;
    if (document.activeElement !== stationInput) stationInput.value = board.station;
  }
  renderStatus();
}

// Countdowns follow the local clock between fetches.
function updateCountdowns() {
  for (const el of document.querySelectorAll(".row")) {
    el.querySelector(".minutes").textContent = minutesText(Number(el.dataset.departs));
  }
}

async function load() {
  const requested = stationId;
  try {
    const query = requested ? `?station=${encodeURIComponent(requested)}` : "";
    const res = await fetch(`/api/departures${query}`, { cache: "no-store" });
    if (res.status === 404 && requested) {  // unknown station: fall back to the default
      stationId = null;
      return load();
    }
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    if (requested !== stationId) return;  // the user picked another station meanwhile
    board = data;
    stationId = board.station_id;
    fetchOk = true;
  } catch {
    fetchOk = false;
  }
  render();
}

function applyStationInput(revertIfUnknown) {
  const id = stationIds.get(stationInput.value.trim().toLowerCase());
  if (!id) {
    if (revertIfUnknown) stationInput.value = board ? board.station : "";
    return;
  }
  if (id === stationId) return;
  stationId = id;
  board = null;
  render();
  const url = new URL(location.href);
  url.searchParams.set("station", id);
  history.replaceState(null, "", url);
  load();
}

async function loadStations() {
  try {
    const res = await fetch("/api/stations");
    if (!res.ok) return;
    const data = await res.json();
    for (const s of data.stations) {
      stationIds.set(s.name.toLowerCase(), s.id);
      const option = document.createElement("option");
      option.value = s.name;
      stationList.appendChild(option);
    }
  } catch {
    // the sign still works without the search box
  }
}

stationInput.addEventListener("focus", () => stationInput.select());
stationInput.addEventListener("input", () => applyStationInput(false));
stationInput.addEventListener("change", () => applyStationInput(true));
stationInput.addEventListener("keydown", (e) => { if (e.key === "Enter") stationInput.blur(); });

// Refresh on every minute boundary (plus a small offset), i.e. 1-minute resolution.
function scheduleNext() {
  const msToNextMinute = 60000 - (Date.now() % 60000);
  setTimeout(async () => { await load(); scheduleNext(); }, msToNextMinute + 2000);
}

window.addEventListener("resize", fitStage);
fitStage();
tickClock();
setInterval(() => { tickClock(); updateCountdowns(); }, 1000);
load().then(scheduleNext);
loadStations();
