"use strict";

const TZ = "Australia/Brisbane";
const clockEl = document.getElementById("clock");
const statusEl = document.getElementById("status");
const stage = document.getElementById("stage");
const stationInput = document.getElementById("station-input");
const stationMenu = document.getElementById("station-menu");
const messageEl = document.getElementById("message");
const messageText = messageEl.firstElementChild;
const ngrTemplate = document.getElementById("ngr-icon");
const threeCarTemplate = document.getElementById("three-car-icon");
const airportTemplate = document.getElementById("airport-icon");
const MESSAGE_SPEED = 160; // px per second, in the 1920px design canvas
let messageShown = "";

let board = null;
let fetchOk = false;
// null = server default. ?stop= takes an id, bus stop code or name; ?station= is the older form.
const startParams = new URLSearchParams(location.search);
let stationId = startParams.get("stop") ?? startParams.get("station");

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
  const bus = board?.mode === "bus";
  container.replaceChildren();
  for (const r of rows) {
    const el = document.createElement("div");
    el.className = bus ? "row bus" : "row";
    el.style.background = r.color;
    el.style.setProperty("--row", r.color);
    el.dataset.departs = r.departs_epoch;
    for (const [cls, text] of [
      ["time", r.scheduled],
      ...(bus ? [["route", r.route]] : []),  // bus route number, in a badge before the destination
      ["dest", r.destination],
      ["exp", r.express ? "Exp" : ""],
      ["platform", r.platform],  // platform for trains, stop code for buses
      ["minutes", minutesText(r.departs_epoch)],
    ]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      if (cls === "dest" && r.destination.endsWith("Airport")) {
        span.appendChild(airportTemplate.content.firstElementChild.cloneNode(true));
      }
      el.appendChild(span);
      // NGR pictogram sits between the Platform and Departs columns.
      if (cls === "platform") {
        const stockTemplate = r.three_car ? threeCarTemplate : r.ngr ? ngrTemplate : null;
        if (stockTemplate) {
          el.appendChild(stockTemplate.content.firstElementChild.cloneNode(true));
        }
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
  // Repeat the text back to back until it covers the bar plus one spare copy, so the loop is
  // seamless and the bar never runs empty, however short the message is.
  const addItem = () => {
    const item = document.createElement("span");
    item.className = "item";
    item.textContent = text;
    messageText.appendChild(item);
    return item;
  };
  const first = addItem();
  const width = first.offsetWidth;  // one copy, including its gap (layout px, unaffected by scaling)
  if (!width) return;
  const needed = Math.ceil(messageEl.clientWidth / width) + 1;
  for (let i = 1; i < needed; i++) addItem();
  messageText.style.setProperty("--to", `-${width}px`);
  messageText.style.setProperty("--dur", `${width / MESSAGE_SPEED}s`);  // constant speed, no pause
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
    document.title = `${board.station} ${board.kind_label ?? "Station"}`;
    for (const el of document.querySelectorAll(".col-platform")) {
      el.textContent = board.platform_heading ?? "Platform";
    }
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
    const query = requested ? `?stop=${encodeURIComponent(requested)}` : "";
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

// Applies a stop picked from the menu (an item from /api/stops).
function applyChoice(item) {
  if (item.id === stationId) return;
  stationId = item.id;
  board = null;
  render();
  const url = new URL(location.href);
  url.searchParams.delete("station");  // legacy parameter
  url.searchParams.set("stop", item.id);
  history.replaceState(null, "", url);
  load();
}

// The native datalist popup is misplaced inside the scaled stage, so the menu is drawn in the page.
// Results come from the server (/api/stops), as there are far too many bus stops to preload.
let menuItems = [];  // [{id, name, code, mode}]
let menuIndex = -1;
let menuRequest = 0;  // ignores out-of-order search responses
let menuTimer = null;
let pendingChoice = null;

async function fetchMenu() {
  const q = stationInput.value.trim();
  const mine = ++menuRequest;
  try {
    // An empty box lists every train station, as before; a query searches trains and buses.
    const res = await fetch(`/api/stops?q=${encodeURIComponent(q)}&limit=${q ? 40 : 200}`);
    if (!res.ok) return;
    const data = await res.json();
    if (mine !== menuRequest) return;
    menuItems = data.stops;
  } catch {
    return;  // the sign still works without the search box
  }
  renderMenu();
}

function renderMenu() {
  menuIndex = -1;
  stationMenu.replaceChildren(...menuItems.map((item, i) => {
    const li = document.createElement("li");
    li.dataset.i = i;
    const tag = document.createElement("span");
    tag.className = `tag ${item.mode}`;
    tag.textContent = item.mode === "bus" ? "Bus" : "Train";
    li.append(tag, item.name);
    if (item.code) {
      const code = document.createElement("span");
      code.className = "code";
      code.textContent = item.code;
      li.append(code);
    }
    return li;
  }));
  stationMenu.hidden = menuItems.length === 0;
}

function showMenu() {
  clearTimeout(menuTimer);
  menuTimer = setTimeout(fetchMenu, 120);
}

function highlight(i) {
  stationMenu.children[menuIndex]?.classList.remove("active");
  menuIndex = i;
  const li = stationMenu.children[i];
  li?.classList.add("active");
  li?.scrollIntoView({ block: "nearest" });
}

function chooseStation(item) {
  pendingChoice = item;
  stationInput.blur();
}

// Keep focus in the input while the menu is clicked so blur does not close it first.
stationMenu.addEventListener("mousedown", (e) => e.preventDefault());
stationMenu.addEventListener("click", (e) => {
  const li = e.target.closest("li");
  if (li) chooseStation(menuItems[Number(li.dataset.i)]);
});

stationInput.addEventListener("focus", () => { stationInput.value = ""; fetchMenu(); });
stationInput.addEventListener("blur", () => {
  clearTimeout(menuTimer);
  menuRequest++;  // drop any search still in flight
  stationMenu.hidden = true;
  const choice = pendingChoice;
  pendingChoice = null;
  if (choice) applyChoice(choice);
  stationInput.value = choice ? choice.name : board ? board.station : "";
});
stationInput.addEventListener("input", showMenu);
stationInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!menuItems.length) return;
    const step = e.key === "ArrowDown" ? 1 : -1;
    highlight((menuIndex + step + menuItems.length) % menuItems.length);
  } else if (e.key === "Enter") {
    const typed = stationInput.value.trim().toLowerCase();
    const exact = menuItems.find((n) => n.name.toLowerCase() === typed || n.code === typed);
    const pick = menuItems[menuIndex] ?? exact ?? menuItems[0];
    if (pick) chooseStation(pick); else stationInput.blur();
  } else if (e.key === "Escape") {
    stationInput.blur();
  }
});

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
