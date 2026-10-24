// Side panel: choose boxes, review exactly what goes in each, then fill. Also edits "My info".
import * as M from "./matcher.js";

const CFG = self.AUTOFILL || {};
const API = `http://127.0.0.1:${CFG.port || 8781}`;
const PORTABLE = CFG.mode === "portable"; // answers kept in this browser instead of the companion service
const portable = PORTABLE ? import("./portable.js") : null;
const UNREACHABLE = PORTABLE ? "Couldn't open your saved answers" : "Couldn't reach the companion service";
const pinnedTab = Number(new URLSearchParams(location.search).get("tab")) || null; // popup-window fallback and tests

const state = {
  idx: null, data: null, conn: "connecting",
  tabId: null, scan: null, rows: new Map(), selected: new Set(), mode: null, placeItem: null,
  lastBatch: null, wholePage: false, openSections: new Set(["student"]),
};

// ------------------------------------------------------------------ small helpers
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const $ = (s) => document.querySelector(s);
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
class Stop extends Error {}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
}
function setHint(html, tone = "") {
  const el = $("#hint");
  el.className = `hint ${tone}`;
  el.replaceChildren(...(Array.isArray(html) ? html : [html]));
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied. Paste it into the box with Ctrl+V."); }
  catch (e) { toast("Couldn't copy. Select the text and copy it yourself."); }
}

// ------------------------------------------------------------------ the local service
async function api(path, body) {
  if (PORTABLE) return (await portable).api(path, body);
  const res = await fetch(API + path, {
    method: body ? "POST" : "GET", cache: "no-store",
    headers: { "X-Autofill-Token": CFG.token || "", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) throw Object.assign(new Error("token"), { kind: "token" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function loadData() {
  try {
    const r = await api("/v1/data");
    state.data = r.data;
    setConn("ok", PORTABLE ? "Saved in this browser" : "Connected");
  } catch (e) {
    state.data = null;
    setConn("bad", e.kind === "token" ? "Wrong key" : PORTABLE ? "Couldn't start" : "Can't reach the service");
  }
  $("#infoCount").textContent = state.data ? `${savedCount()}` : "";
  return state.data;
}
function savedCount() { return Object.keys(state.data?.values || {}).length; }

function setConn(kind, text) {
  state.conn = kind;
  const c = $("#conn");
  c.className = `conn ${kind}`;
  $("#connText").textContent = text;
  c.title = PORTABLE ? (kind === "ok" ? "Your saved answers are kept in this browser, by this extension." : "Reload the extension in chrome://extensions, then open the panel again.") :
    kind === "ok" ? "Your saved answers come from the companion service on this computer." :
    text === "Wrong key" ? "The extension's key doesn't match the service's. Run install.sh again." :
      "Start the companion service on this computer (on Windows: open WSL), then press a button again.";
}

async function needData() {
  if (await loadData()) return true;
  setHint(PORTABLE ? "Couldn't open your saved answers. Reload the extension in chrome://extensions, then open the panel again." :
    state.conn === "bad" && $("#connText").textContent === "Wrong key"
    ? "The extension's key doesn't match the service's. Run apps/autofill/install.sh again, then reload the extension."
    : "Can't reach your saved answers. Start the companion service on this computer (on Windows: open WSL) and try again.", "bad");
  return false;
}

// ------------------------------------------------------------------ the page
const OFF_LIMITS = /^(chrome|edge|about|devtools|chrome-extension|extension|view-source|chrome-search):|^https:\/\/(chromewebstore\.google\.com|chrome\.google\.com\/webstore|microsoftedge\.microsoft\.com\/addons)/;

async function targetTab() {
  if (pinnedTab) return chrome.tabs.get(pinnedTab).catch(() => null);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}
function send(msg) { return chrome.tabs.sendMessage(state.tabId, msg, { frameId: 0 }); }

async function connectTab() {
  const tab = await targetTab();
  if (!tab) throw new Stop("Open the application form in a tab first.");
  if (!tab.url || OFF_LIMITS.test(tab.url) || /\.pdf($|[?#])/i.test(tab.url))
    throw new Stop("I can't work on this page. Browser pages, the add-ons store and PDF files are off-limits. Open the application form in a normal tab.");
  if (state.tabId !== tab.id) resetRows();
  state.tabId = tab.id;
  try { if ((await send({ cmd: "ping" }))?.ok) return tab; } catch (e) { /* not injected yet */ }
  try { await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] }); }
  catch (e) { throw new Stop("This page won't let extensions in, so I can't fill it."); }
  if (!(await send({ cmd: "ping" }).catch(() => null))?.ok) throw new Stop("Couldn't start on this page. Reload it and try again.");
  return tab;
}

// Runs in the page itself while filling: any submit the page attempts is cancelled.
function saGuard(ms) {
  const w = window;
  w.__saGuardUntil = Math.max(w.__saGuardUntil || 0, Date.now() + ms);
  if (w.__saGuardInstalled) return true;
  w.__saGuardInstalled = true;
  const active = () => Date.now() < w.__saGuardUntil;
  const tell = (how) => { try { document.dispatchEvent(new CustomEvent("sa-guard-blocked", { detail: String(how) })); } catch (e) { /* ignore */ } };
  const P = HTMLFormElement.prototype;
  const submit = P.submit, requestSubmit = P.requestSubmit;
  P.submit = function () { if (active()) { tell("form.submit()"); return undefined; } return submit.apply(this, arguments); };
  if (requestSubmit) P.requestSubmit = function () { if (active()) { tell("form.requestSubmit()"); return undefined; } return requestSubmit.apply(this, arguments); };
  w.addEventListener("submit", (e) => { if (active()) { e.preventDefault(); e.stopImmediatePropagation(); tell("submit"); } }, true);
  w.addEventListener("click", (e) => {
    if (!active() || !e.target?.closest) return;
    const t = e.target.closest("button, input[type=submit], input[type=image], a[href]");
    if (!t) return;
    if (/^(radio|checkbox|switch|option|menuitemradio|menuitemcheckbox)$/.test(t.getAttribute("role") || "")) return;
    if (t.tagName === "A" || t.tagName === "INPUT" || (t.getAttribute("type") || "submit").toLowerCase() === "submit") {
      e.preventDefault(); e.stopImmediatePropagation(); tell(`click on ${t.tagName.toLowerCase()}`);
    }
  }, true);
  return true;
}

async function armGuard(ms) {
  for (const target of [{ tabId: state.tabId, allFrames: true }, { tabId: state.tabId }]) {
    try { await chrome.scripting.executeScript({ target, world: "MAIN", func: saGuard, args: [ms] }); return true; } catch (e) { /* try the top frame alone */ }
  }
  return false;
}

// ------------------------------------------------------------------ modes
function renderModes() {
  $("#mPoint").classList.toggle("on", state.mode === "point");
  $("#mCircle").classList.toggle("on", state.mode === "circle");
  $("#mPage").classList.toggle("on", false);
}

async function guarded(fn) {
  try { await fn(); }
  catch (e) {
    if (e instanceof Stop) setHint(e.message, "bad");
    else { console.error(e); setHint(`Something went wrong: ${e.message || e}`, "bad"); }
    state.mode = null; renderModes();
  }
}

async function begin(mode, hint = "") {
  if (!(await needData())) return;
  await connectTab();
  const r = await send({ cmd: "pick", mode, hint });
  state.scan = r.scan;
  state.mode = mode;
  renderModes();
  showFrames();
  if (mode === "point") setHint(["Click boxes on the form. Each one you click is added below. Press ", h("b", {}, "Esc"), " or ", h("b", {}, "Point"), " again to stop."]);
  else if (mode === "circle") setHint("Hold the mouse down and draw a loop around the boxes you want.");
  else setHint(["Click the box where ", h("b", {}, hint), " should go."]);
}

async function stopMode() {
  if (state.mode && state.tabId != null) await send({ cmd: "stopPick" }).catch(() => {});
  state.mode = null;
  state.placeItem = null;
  renderModes();
}

$("#mPoint").addEventListener("click", () => guarded(async () => { if (state.mode === "point") { await stopMode(); setHint("Stopped pointing."); } else await begin("point"); }));
$("#mCircle").addEventListener("click", () => guarded(async () => { await stopMode(); await begin("circle"); }));
$("#mPage").addEventListener("click", () => guarded(async () => {
  await stopMode();
  if (!(await needData())) return;
  await connectTab();
  const r = await send({ cmd: "scan" });
  state.scan = r.scan;
  resetRows();
  state.wholePage = true;
  merge(M.proposeAll(r.scan.fields, state.data, state.idx));
  showFrames();
  if (!r.scan.fields.length) setHint("I don't see any boxes on this page.", "warn");
  else summarizeHint();
  render();
}));

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg?.event || sender.id !== chrome.runtime.id || sender.tab?.id !== state.tabId || sender.frameId !== 0) return;
  guarded(async () => {
    if (msg.event === "pick-stopped") { state.mode = null; state.placeItem = null; renderModes(); setHint("Stopped."); return; }
    if (msg.event !== "picked") return;
    state.scan = msg.scan;
    if (msg.mode === "place") return placeInto(msg.refs[0]);
    if (msg.mode === "circle") { state.mode = null; renderModes(); }
    if (!msg.refs.length) { setHint("No boxes inside that loop. Try drawing it a little bigger.", "warn"); return; }
    if (!(await needData())) return;
    merge(M.proposeAll(state.scan.fields, state.data, state.idx, { only: new Set(msg.refs) }));
    summarizeHint();
    render();
  });
});

// ------------------------------------------------------------------ the list of boxes
const FILLABLE = new Set(["ready", "check", "has_value"]);
const STATUS_TEXT = { ready: "Ready", check: "Check", has_value: "Has text", choose: "Choose", missing: "Not saved", none: "Unknown", blocked: "Won't fill", done: "Done", filled: "Filled", failed: "Didn't fill", changed: "Changed", gone: "Gone" };

function resetRows() {
  state.rows.clear();
  state.selected.clear();
  state.wholePage = false;
  state.lastBatch = null;
  $("#done").hidden = true;
  if (state.tabId != null) send({ cmd: "clear" }).catch(() => {});
}

function order(ref) { return state.scan?.fields.findIndex((f) => f.ref === ref) ?? 0; }

function merge(rows) {
  for (const row of rows) {
    state.rows.set(row.ref, row);
    if (row.status === "ready") state.selected.add(row.ref);
    else state.selected.delete(row.ref);
  }
}

function summarizeHint() {
  const rows = [...state.rows.values()];
  const ready = rows.filter((r) => r.status === "ready").length;
  const check = rows.filter((r) => ["check", "has_value", "choose"].includes(r.status)).length;
  const parts = [`${rows.length} box${rows.length === 1 ? "" : "es"}.`];
  if (ready) parts.push(` ${ready} ready (ticked).`);
  if (check) parts.push(` ${check} need${check === 1 ? "s" : ""} a look before you tick ${check === 1 ? "it" : "them"}.`);
  parts.push(" Nothing is typed until you press Fill.");
  setHint(parts.join(""));
}

function rowValueOptions(row) {
  // every saved answer, for "use a different answer"
  const sel = h("select", { "aria-label": "Use a different answer", onchange: (e) => useItem(row.ref, e.target.value) },
    h("option", { value: "" }, row.status === "choose" ? "Choose which answer…" : "Use a different answer…"));
  const items = M.allItems(state.data, state.idx).filter((i) => i.value != null && i.value !== "");
  const groups = new Map();
  for (const it of items) {
    const sec = state.idx.schema.sections.find((s) => s.id === it.section)?.title || state.idx.lists[it.section]?.title || "Other";
    if (!groups.has(sec)) groups.set(sec, []);
    groups.get(sec).push(it);
  }
  if (row.status === "choose" && row.choices?.length) {
    const g = h("optgroup", { label: "Suggested" });
    for (const c of row.choices) { const it = M.itemInfo(c, state.data, state.idx); if (it?.value) g.append(h("option", { value: c }, `${it.label}: ${M.displayValue(it, state.idx.states)}`)); }
    sel.append(g);
  }
  for (const [title, list] of groups) {
    const g = h("optgroup", { label: title });
    for (const it of list) g.append(h("option", { value: it.ref }, `${it.label}: ${M.displayValue(it, state.idx.states).slice(0, 40)}`));
    sel.append(g);
  }
  return sel;
}

function useItem(ref, itemRef) {
  if (!itemRef) return;
  const field = state.scan?.fields.find((f) => f.ref === ref);
  if (!field) return;
  const row = M.proposeForItem(field, itemRef, state.data, state.idx);
  row.chosen = true;
  state.rows.set(ref, row);
  if (row.status === "ready") state.selected.add(ref); else state.selected.delete(ref);
  render();
}

function rowEl(row, n) {
  const fillable = FILLABLE.has(row.status) && row.action;
  const tick = fillable ? h("input", { type: "checkbox", checked: state.selected.has(row.ref), "aria-label": `Fill ${row.label}`,
    onchange: (e) => { if (e.target.checked) state.selected.add(row.ref); else state.selected.delete(row.ref); renderBar(); pushTags(); } }) : null;
  const value = row.display != null && !["blocked", "none"].includes(row.status) ? row.display
    : row.status === "missing" ? "Not saved yet" : row.status === "choose" ? "Choose an answer" : "—";
  const tools = h("div", { class: "tools" });
  if (!["blocked", "filled", "done"].includes(row.status) && state.data) tools.append(rowValueOptions(row));
  if (row.status === "missing" && row.item) tools.append(h("button", { class: "link", type: "button", onclick: () => openInfo(row.item) }, "Add it"));
  if ((row.copy || row.display) && ["failed", "changed", "none"].includes(row.status)) tools.append(h("button", { class: "link", type: "button", onclick: () => copy(row.copy || row.display) }, "Copy"));
  tools.append(h("button", { class: "link", type: "button", onclick: () => send({ cmd: "focus", ref: row.ref }).catch(() => {}) }, "Show"));
  return h("div", { class: `row s-${row.status}` },
    h("label", { class: "tick" }, tick),
    h("div", { class: "lab" }, h("span", { class: "num" }, String(n)), h("span", { title: row.label }, row.label)),
    h("div", { class: "what" }, h("span", { class: "val" }, value), row.itemLabel && !["blocked", "none"].includes(row.status) ? h("span", { class: "item" }, row.itemLabel) : null),
    h("div", { class: "why" }, `${STATUS_TEXT[row.status] || row.status} · ${row.reason || ""}`),
    tools);
}

function numbered() {
  return [...state.rows.values()].sort((a, b) => order(a.ref) - order(b.ref)).map((row, i) => ({ row, n: i + 1 }));
}

function render() {
  const box = $("#rows");
  box.replaceChildren();
  const list = numbered();
  if (state.wholePage) {
    const groups = [["Ready to fill", ["ready"]], ["Look at these first", ["check", "has_value", "choose"]], ["Filled", ["filled"]],
      ["Didn't fill", ["failed", "changed", "gone"]], ["Not saved yet", ["missing"]], ["Won't fill", ["blocked", "none", "done"]]];
    for (const [title, statuses] of groups) {
      const part = list.filter(({ row }) => statuses.includes(row.status));
      if (!part.length) continue;
      if (title === "Won't fill") {
        const d = h("details", {}, h("summary", { class: "group-title" }, `${title} (${part.length})`));
        part.forEach(({ row, n }) => d.append(rowEl(row, n)));
        box.append(d);
      } else {
        box.append(h("div", { class: "group-title" }, `${title} (${part.length})`));
        part.forEach(({ row, n }) => box.append(rowEl(row, n)));
      }
    }
  } else list.forEach(({ row, n }) => box.append(rowEl(row, n)));
  renderBar();
  pushTags();
}

function renderBar() {
  const n = [...state.selected].filter((ref) => FILLABLE.has(state.rows.get(ref)?.status)).length;
  $("#bar").hidden = state.rows.size === 0;
  const btn = $("#fillBtn");
  btn.disabled = n === 0;
  btn.textContent = n ? `Fill ${n} box${n === 1 ? "" : "es"}` : "Tick the boxes to fill";
}

function pushTags() {
  if (state.tabId == null) return;
  const tone = (s) => ({ ready: "ready", check: "check", has_value: "check", choose: "check", filled: "filled", failed: "failed", changed: "failed", gone: "failed" }[s]);
  const rows = numbered().filter(({ row }) => tone(row.status)).map(({ row, n }) => ({
    ref: row.ref, n, tone: row.status === "ready" && !state.selected.has(row.ref) ? "check" : tone(row.status),
    text: row.status === "filled" ? "✓" : (row.display || "").slice(0, 28),
  }));
  send({ cmd: "tags", rows }).catch(() => {});
}

function showFrames() {
  const el = $("#frames");
  const frames = (state.scan?.frames || []).filter((f) => /^https?:/.test(f.src));
  if (!frames.length) { el.hidden = true; return; }
  el.hidden = false;
  el.replaceChildren(h("div", {}, "Part of this page is a form from another site, which I can't reach from here. Open it on its own and use me there:"),
    ...frames.slice(0, 3).map((f) => h("button", { class: "chip", type: "button", onclick: () => chrome.tabs.create({ url: f.src }) }, `Open ${new URL(f.src).hostname}`)));
}

$("#clearBtn").addEventListener("click", () => guarded(async () => { await stopMode(); resetRows(); render(); setHint("Cleared."); }));

// ------------------------------------------------------------------ filling
$("#fillBtn").addEventListener("click", () => guarded(() => fillRefs([...state.selected])));

async function fillRefs(refs) {
  await stopMode();
  if (!(await needData())) return;
  await connectTab();
  // Look again right before typing: anything that changed since you reviewed it is skipped, not guessed.
  const r = await send({ cmd: "scan" });
  state.scan = r.scan;
  const actions = [], skipped = [];
  for (const ref of refs) {
    const old = state.rows.get(ref);
    const field = r.scan.fields.find((f) => f.ref === ref);
    if (!old || !old.action) continue;
    const fresh = !field ? null : old.chosen ? M.proposeForItem(field, old.item, state.data, state.idx)
      : M.proposeAll(r.scan.fields, state.data, state.idx, { only: new Set([ref]) })[0];
    const same = fresh && fresh.action && JSON.stringify(fresh.action) === JSON.stringify(old.action) && fresh.item === old.item &&
      (fresh.status === old.status || (old.chosen && fresh.status === "ready")) && (fresh.current ?? "") === (old.current ?? "");
    if (!same) { skipped.push(ref); state.rows.set(ref, { ...(fresh || old), status: fresh ? fresh.status : "gone", reason: "This box or your saved answer changed since you looked. Check it again." }); continue; }
    actions.push({ ref, ...old.action });
  }
  if (!actions.length) { render(); setHint(skipped.length ? "Those boxes changed since you looked, so I didn't fill them. Check them again." : "Nothing ticked to fill.", "warn"); return; }
  const combos = actions.filter((a) => a.kind === "combo"), plain = actions.filter((a) => a.kind !== "combo");
  if (!(await armGuard(4000 + plain.length * 200 + combos.length * 4000))) {
    setHint("I couldn't switch on the submit blocker for this page, so I didn't fill anything.", "bad");
    return;
  }
  const batch = `b${Date.now()}`;
  await send({ cmd: "guardLog" }).catch(() => null);
  const res = plain.length ? await send({ cmd: "fill", actions: plain, batch }) : { ok: true, results: [], blocked: [], left: false };
  if (!res?.ok) throw new Error(res?.error || "fill failed");
  for (const a of combos) res.results.push(await fillCombo(a, r.scan.fields.find((f) => f.ref === a.ref), batch));
  res.blocked = (await send({ cmd: "guardLog" }).catch(() => null))?.blocked || res.blocked;
  let filled = 0;
  for (const result of res.results) {
    const row = state.rows.get(result.ref);
    if (!row) continue;
    row.before = row.status;
    if (result.ok) {
      filled++;
      row.status = "filled";
      row.reason = result.status === "reformatted" ? `Filled. The site shows it as "${result.now}".`
        : row.action?.kind === "combo" ? `Picked "${result.now}" in the dropdown and checked it.` : "Filled and checked.";
      if (row.action?.kind === "combo" && result.now) row.display = result.now;
      if (result.status === "reformatted") row.display = result.now;
    } else {
      row.status = result.status === "changed" ? "changed" : result.status === "gone" ? "gone" : "failed";
      row.reason = result.why;
      row.copy = row.display;
    }
    state.selected.delete(result.ref);
  }
  state.lastBatch = { batch, refs: res.results.filter((x) => x.ok).map((x) => x.ref) };
  const failed = res.results.length - filled;
  const done = $("#done");
  done.hidden = false;
  done.className = `done${failed ? " bad" : ""}`;
  const notes = [];
  notes.push(res.blocked.length ? `The page tried to submit ${res.blocked.length} time${res.blocked.length === 1 ? "" : "s"}; I blocked it.` : "Nothing was submitted.");
  if (skipped.length) notes.push(`${skipped.length} changed since you looked and ${skipped.length === 1 ? "was" : "were"} skipped.`);
  if (res.left) notes.push("The page started changing. Check it before going on.");
  done.replaceChildren(
    h("div", {}, failed ? `Filled ${filled} of ${res.results.length}. ${failed} didn't take — see below.` : `Filled ${filled} box${filled === 1 ? "" : "es"} and checked each one.`, h("small", {}, notes.join(" "))),
    filled ? h("button", { class: "chip", type: "button", onclick: () => guarded(undoLast) }, "Undo") : null);
  render();
  const site = (() => { try { return new URL(r.scan.url).hostname; } catch (e) { return ""; } })();
  api("/v1/log", { entries: res.results.map((x) => ({ site, label: state.rows.get(x.ref)?.label || "", item: state.rows.get(x.ref)?.item || "", status: x.status })) }).catch(() => {});
}

// A custom dropdown: open it, read its real choices, pick with the same rules as a normal dropdown.
async function fillCombo(action, field, batch) {
  const row = state.rows.get(action.ref);
  const info = row && M.itemInfo(row.item, state.data, state.idx);
  if (!info || !field) return { ref: action.ref, ok: false, status: "gone", why: "The box is no longer on the page" };
  const v = String(info.value ?? "");
  const searches = !field.typeahead ? [null]
    : info.type === "state" ? [state.idx.states[v], v] : [M.displayValue(info, state.idx.states)];
  let why = "None of the choices matches";
  for (const type of searches.filter((t, i, a) => a.indexOf(t) === i)) {
    const opened = await send({ cmd: "comboOpen", ref: action.ref, type });
    if (!opened?.ok) { why = opened?.why || why; if (opened?.status === "gone") return { ref: action.ref, ...opened }; continue; }
    const live = { ...field, kind: "select", options: opened.options, selectedIndex: -1, selectDefault: true };
    const out = M.formatFor(live, info, state.idx, { part: row.part || null });
    const choice = out.action?.kind === "select" ? opened.options[out.action.index] : null;
    if (choice && (!action.text || M.normalize(choice.text) === M.normalize(action.text))) {
      const picked = await send({ cmd: "comboPick", ref: action.ref, index: out.action.index, batch });
      return { ...picked, ref: action.ref, now: picked.now || choice.text };
    }
    await send({ cmd: "comboClose", ref: action.ref }).catch(() => null);
    why = choice ? "The dropdown's choices changed since you looked" : out.fail || why;
  }
  return { ref: action.ref, ok: false, status: "failed", why };
}

async function undoLast() {
  if (!state.lastBatch) return;
  await connectTab();
  if (!(await armGuard(4000))) { setHint("I couldn't switch on the submit blocker, so I didn't undo.", "bad"); return; }
  const r = await send({ cmd: "undo", batch: state.lastBatch.batch });
  for (const ref of state.lastBatch.refs) {
    const row = state.rows.get(ref);
    if (row) { row.status = row.before || "ready"; row.reason = "Put back what was there before."; }
  }
  state.lastBatch = null;
  $("#done").hidden = true;
  render();
  setHint(r?.ok ? `Put back ${r.restored} box${r.restored === 1 ? "" : "es"} the way they were.${r.stuck ? ` ${r.stuck} dropdown${r.stuck === 1 ? " was" : "s were"} empty before and can't be emptied again: change ${r.stuck === 1 ? "it" : "them"} by hand.` : ""}` : "Nothing to undo.", r?.stuck ? "warn" : "");
}

// ------------------------------------------------------------------ "tell me what to fill"
const ask = $("#ask");
ask.addEventListener("input", debounce(renderResults, 90));
ask.addEventListener("keydown", (e) => { if (e.key === "Escape") { ask.value = ""; renderResults(); } });

function renderResults() {
  const box = $("#results");
  const q = ask.value.trim();
  if (!q || !state.data) { box.hidden = true; return; }
  const found = M.searchItems(q, state.data, state.idx);
  box.hidden = false;
  if (!found.length) { box.replaceChildren(h("div", { class: "head" }, "No saved answer matches. Add it in My info.")); return; }
  const withValues = found.filter((i) => i.value != null && i.value !== "");
  box.replaceChildren(
    h("div", { class: "head" }, h("span", {}, `${found.length} saved answer${found.length === 1 ? "" : "s"}`),
      withValues.length ? h("button", { class: "chip solid", type: "button", onclick: () => guarded(() => findOnPage(withValues.map((i) => i.ref))) }, "Find on this page") : null),
    ...found.slice(0, 8).map((it) => h("div", { class: "res" },
      h("div", { class: "name" }, it.label),
      h("div", { class: `val${it.value ? "" : " none"}` }, it.value ? it.display : "Not saved yet"),
      h("div", { class: "btns" },
        it.value ? h("button", { class: "chip", type: "button", title: "Then click the box it goes in", onclick: () => guarded(() => place(it)) }, "Put in a box") : h("button", { class: "chip", type: "button", onclick: () => openInfo(it.ref) }, "Add"),
        it.value ? h("button", { class: "chip", type: "button", onclick: () => copy(it.display) }, "Copy") : null))));
}

async function findOnPage(itemRefs) {
  await stopMode();
  if (!(await needData())) return;
  await connectTab();
  const r = await send({ cmd: "scan" });
  state.scan = r.scan;
  const rows = M.proposeAll(r.scan.fields, state.data, state.idx, { items: new Set(itemRefs) });
  if (!rows.length) { setHint("I couldn't find boxes for those on this page. Use “Put in a box” and click the box yourself.", "warn"); return; }
  merge(rows);
  summarizeHint();
  render();
}

async function place(item) {
  await stopMode();
  state.placeItem = item.ref;
  await begin("place", item.label);
}

async function placeInto(fieldRef) {
  const itemRef = state.placeItem;
  state.mode = null; state.placeItem = null; renderModes();
  const field = state.scan?.fields.find((f) => f.ref === fieldRef);
  if (!field || !itemRef) return;
  if (!(await needData())) return;
  const row = M.proposeForItem(field, itemRef, state.data, state.idx);
  row.chosen = true;
  state.rows.set(row.ref, row);
  if (row.status === "ready") {
    state.selected.add(row.ref);
    render();
    await fillRefs([row.ref]); // you chose both the answer and the box
  } else {
    state.selected.delete(row.ref);
    render();
    setHint(row.status === "has_value" ? "That box already has text. Tick it below if you want to replace it." : row.reason, "warn");
  }
}

// ------------------------------------------------------------------ My info
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  const already = !$("#" + name).hidden; // clicking the open tab again mustn't redraw it (and lose a paste in progress)
  document.querySelectorAll(".tabs button").forEach((b) => { b.classList.toggle("on", b.dataset.tab === name); b.setAttribute("aria-selected", b.dataset.tab === name); });
  $("#fill").hidden = name !== "fill";
  $("#info").hidden = name !== "info";
  if (name === "info" && !already) renderInfo();
}

const INPUT_TYPE = { email: "email", phone: "tel", date: "date", month: "month" };

function inputFor(spec, value, onSave) {
  let el;
  if (spec.type === "choice" || spec.type === "yesno" || spec.type === "state") {
    const options = spec.type === "yesno" ? ["Yes", "No"] : spec.type === "state" ? state.idx.schema.states.map(([c]) => c) : spec.options;
    el = h("select", {}, h("option", { value: "" }, "—"),
      ...options.map((o) => h("option", { value: o, selected: o === value }, spec.type === "state" ? `${state.idx.states[o]} (${o})` : o)));
  } else if (spec.type === "longtext") {
    el = h("textarea", { maxlength: spec.maxlength || 1000 });
    el.value = value || "";
  } else {
    el = h("input", { type: INPUT_TYPE[spec.type] || "text", placeholder: spec.placeholder || "", autocomplete: "off", spellcheck: "false",
      inputmode: spec.type === "integer" ? "numeric" : spec.type === "number" ? "decimal" : null });
    el.value = spec.type === "phone" && /^\d{10}$/.test(value || "") ? `(${value.slice(0, 3)}) ${value.slice(3, 6)}-${value.slice(6)}` : value || "";
  }
  el.addEventListener("change", () => onSave(el.value, el));
  return el;
}

function fieldBox(label, spec, value, onSave, key) {
  const msg = h("div", { class: "msg" });
  const input = inputFor(spec, value, async (v, el) => {
    msg.className = "msg"; msg.textContent = "Saving…";
    const err = await onSave(v);
    if (err) { msg.className = "msg bad"; msg.textContent = err; }
    else { msg.className = "msg ok"; msg.textContent = v ? "Saved" : "Cleared"; setTimeout(() => { if (msg.textContent === "Saved" || msg.textContent === "Cleared") msg.textContent = ""; }, 1800); }
  });
  input.id = `in-${key.replace(/\W/g, "-")}`;
  return h("div", { class: "f", dataset: { key } }, h("label", { for: input.id }, label), input, msg);
}

async function saveValue(key, value) {
  try {
    const r = await api("/v1/set", { changes: { [key]: value } });
    if (r.data) state.data = r.data;
    $("#infoCount").textContent = `${savedCount()}`;
    updateSectionCounts();
    return r.errors?.[key] || null;
  } catch (e) { return `${UNREACHABLE}, so this wasn't saved.`; }
}

async function listOp(body) {
  try {
    const r = await api("/v1/list", body);
    if (r.data) state.data = r.data;
    if (!r.ok) return r.errors || { _: r.error || "Not saved" };
    return null;
  } catch (e) { return { _: `${UNREACHABLE}, so this wasn't saved.` }; }
}

function updateSectionCounts() {
  document.querySelectorAll("details.sec[data-sec]").forEach((d) => {
    const sec = state.idx.schema.sections.find((s) => s.id === d.dataset.sec);
    if (!sec) return;
    const n = sec.items.filter((i) => state.data.values[i.key] != null).length;
    d.querySelector(".n").textContent = `${n} of ${sec.items.length}`;
  });
}

async function renderInfo(focusKey = null) {
  const body = $("#infoBody");
  if (!(await loadData())) {
    body.replaceChildren(h("p", { class: "hint bad" }, PORTABLE ? "Couldn't open your saved answers. Reload the extension in chrome://extensions, then come back to this tab." :
      "Can't reach your saved answers. Start the companion service on this computer (on Windows: open WSL), then come back to this tab."),
      h("button", { class: "chip", type: "button", onclick: () => renderInfo() }, "Try again"));
    return;
  }
  body.replaceChildren();
  for (const sec of state.idx.schema.sections) {
    const n = sec.items.filter((i) => state.data.values[i.key] != null).length;
    const d = h("details", { class: "sec", dataset: { sec: sec.id }, open: state.openSections.has(sec.id),
      ontoggle: (e) => { if (e.target.open) state.openSections.add(sec.id); else state.openSections.delete(sec.id); } },
      h("summary", {}, h("span", {}, sec.title), h("span", { class: "n" }, `${n} of ${sec.items.length}`)));
    const inner = h("div", { class: "body" }, sec.hint ? h("div", { class: "note" }, sec.hint) : null,
      ...sec.items.map((item) => fieldBox(item.label, item, state.data.values[item.key], (v) => saveValue(item.key, v), item.key)));
    d.append(inner);
    body.append(d);
  }
  for (const list of state.idx.schema.lists) body.append(listSection(list));
  body.append(backupSection());
  if (focusKey) focusInfo(focusKey);
}

// A backup file moves your answers to another browser or computer, and keeps them safe if this
// one is reset. Restoring checks every value again and replaces everything, or nothing.
function backupSection() {
  const msg = h("div", { class: "msg" });
  const file = h("input", { type: "file", accept: ".json,application/json", hidden: true, onchange: async () => {
    const f = file.files[0];
    file.value = "";
    if (!f) return;
    let incoming;
    try { incoming = JSON.parse(await f.text()); } catch (e) { msg.className = "msg bad"; msg.textContent = "That isn't a backup file."; return; }
    if (!confirm("Replace all your saved answers with the ones in this backup? (The answers you have now are kept in the history.)")) return;
    msg.className = "msg"; msg.textContent = "Restoring…";
    try {
      const r = await api("/v1/import", { data: incoming });
      if (!r.ok) {
        msg.className = "msg bad";
        msg.textContent = r.error || `Nothing was restored. ${Object.entries(r.errors || {}).slice(0, 4).map(([k, e]) => `${state.idx.items[k]?.label || k}: ${e}`).join(" · ")}`;
        return;
      }
      state.data = r.data;
      $("#infoCount").textContent = `${savedCount()}`;
      await renderInfo();
      toast("Restored your answers from the backup.");
    } catch (e) { msg.className = "msg bad"; msg.textContent = `${UNREACHABLE}, so nothing was restored.`; }
  } });
  const download = () => {
    const blob = new Blob([JSON.stringify(state.data, null, 1)], { type: "application/json" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `scholarship-autofill-backup-${new Date().toISOString().slice(0, 10)}.json` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  const open = state.openSections.has("_backup");
  return h("details", { class: "sec", dataset: { sec: "_backup" }, open,
    ontoggle: (e) => { if (e.target.open) state.openSections.add("_backup"); else state.openSections.delete("_backup"); } },
    h("summary", {}, h("span", {}, "Back up or move your answers"), h("span", { class: "n" }, "")),
    h("div", { class: "body" },
      h("div", { class: "note" }, PORTABLE
        ? "Your answers are kept in this browser only. Removing the extension or resetting this computer deletes them, so download a backup now and then. The file isn't locked, so keep it private."
        : "A backup file holds every answer, unlocked. Keep it private."),
      h("button", { class: "chip solid", type: "button", onclick: download }, "Download a backup"), " ",
      h("button", { class: "chip", type: "button", onclick: () => file.click() }, "Restore from a backup…"),
      file, msg));
}

function listSection(list) {
  const rows = state.data.lists[list.id] || [];
  const d = h("details", { class: "sec", dataset: { list: list.id }, open: state.openSections.has(list.id),
    ontoggle: (e) => { if (e.target.open) state.openSections.add(list.id); else state.openSections.delete(list.id); } },
    h("summary", {}, h("span", {}, list.title), h("span", { class: "n" }, `${rows.length} saved`)));
  const inner = h("div", { class: "body" }, list.hint ? h("div", { class: "note" }, list.hint) : null);
  if (list.id === "courses") inner.append(transcriptBox());
  rows.forEach((row, i) => {
    const box = h("div", { class: "lrow" }, h("div", { class: "lhead" }, h("span", {}, `${list.noun} ${i + 1}`),
      h("button", { class: "link", type: "button", onclick: async () => {
        if (!confirm(`Remove ${list.noun.toLowerCase()} ${i + 1}${row.name || row.label || row.employer ? ` (${row.name || row.label || row.employer})` : ""}?`)) return;
        const err = await listOp({ list: list.id, op: "remove", index: i });
        if (err) toast(Object.values(err)[0]); else renderInfo();
      } }, "Remove")));
    for (const f of list.fields) {
      box.append(fieldBox(f.label, f, row[f.id], async (v) => {
        const err = await listOp({ list: list.id, op: "update", index: i, item: { [f.id]: v } });
        return err ? err[f.id] || Object.values(err)[0] : null;
      }, `${list.id}.${i}.${f.id}`));
    }
    inner.append(box);
  });
  if (rows.length < list.max) {
    inner.append(h("button", { class: "chip", type: "button", onclick: (e) => {
      const draft = {};
      const msg = h("div", { class: "msg" });
      const box = h("div", { class: "lrow" }, h("div", { class: "lhead" }, `New ${list.noun.toLowerCase()}`),
        ...list.fields.map((f) => { const el = inputFor(f, "", (v) => { draft[f.id] = v; }); el.addEventListener("input", () => { draft[f.id] = el.value; });
          return h("div", { class: "f" }, h("label", {}, f.label), el); }),
        msg,
        h("button", { class: "chip solid", type: "button", onclick: async () => {
          const err = await listOp({ list: list.id, op: "add", item: draft });
          if (err) { msg.className = "msg bad"; msg.textContent = Object.entries(err).map(([k, v]) => `${list.fields.find((x) => x.id === k)?.label || ""} ${v}`.trim()).join(" · "); }
          else { state.openSections.add(list.id); renderInfo(); }
        } }, "Save"));
      e.target.replaceWith(box);
    } }, `Add ${list.noun.toLowerCase()}`));
  }
  d.append(inner);
  return d;
}

function openInfo(itemRef) {
  showTab("info");
  const key = itemRef;
  const sec = state.idx.items[key]?.section || key.split(".")[0];
  state.openSections.add(sec);
  renderInfo(key);
}

function focusInfo(key) {
  const f = document.querySelector(`.f[data-key="${CSS.escape(key)}"]`);
  const target = f || document.querySelector(`details[data-list="${CSS.escape(key.split(".")[0])}"]`);
  if (!target) return;
  target.closest("details")?.setAttribute("open", "");
  target.scrollIntoView({ block: "center" });
  if (f) { f.classList.add("flash"); f.querySelector("input,select,textarea")?.focus(); setTimeout(() => f.classList.remove("flash"), 1700); }
}

// ------------------------------------------------------------------ "paste your transcript"
// The service reads the text (transcript.py, the same reader Hermes uses). Nothing is saved until
// you've looked at every row and pressed Save; rows the reader wasn't sure about start unticked.
const tx = { result: null, rows: [], summary: {} };
const LETTER_POINTS = { A: 4, B: 3, C: 2, D: 1, F: 0, E: 0 };

function transcriptBox() {
  const wrap = h("div", { class: "tx" });
  const area = h("textarea", { class: "txin", placeholder: "Open your transcript (PDF, portal page or spreadsheet), select everything (Ctrl+A), copy (Ctrl+C) and paste it here (Ctrl+V).", spellcheck: "false" });
  const msg = h("div", { class: "msg" });
  const out = h("div", { class: "txout" });
  const read = h("button", { class: "chip solid", type: "button", onclick: async () => {
    if (!area.value.trim()) { msg.className = "msg bad"; msg.textContent = "Paste your transcript first."; return; }
    msg.className = "msg"; msg.textContent = "Reading…";
    try {
      const r = await api("/v1/transcript", { text: area.value });
      if (!r.ok) { msg.className = "msg bad"; msg.textContent = r.error || "Couldn't read that."; return; }
      tx.result = r.result;
      tx.rows = r.result.courses.map((c) => ({ ...c, on: c.ok }));
      markLikelyDuplicates(tx.rows, state.data.lists.courses || []);
      tx.exams = (r.result.exams || []).map((e) => ({ ...e, on: true }));
      tx.summary = {};
      area.value = ""; // the pasted text is gone from the panel once read; only the rows below remain
      msg.textContent = "";
      renderTranscript(out);
    } catch (e) { msg.className = "msg bad"; msg.textContent = `${UNREACHABLE}, so nothing was read.`; }
  } }, "Read it");
  const open = h("button", { class: "chip", type: "button", onclick: () => { form.hidden = !form.hidden; if (!form.hidden) area.focus(); } }, "Paste your transcript");
  const form = h("div", { class: "txform", hidden: true }, area, h("div", { class: "txbtns" }, read), msg);
  wrap.append(open, form, out);
  if (tx.result) { form.hidden = false; renderTranscript(out); }
  return wrap;
}

// "Precalculus (DE-MAT 161)" in your own list is probably "Hon Precalculus RSA" from the transcript:
// same core words (ignoring codes and level words) and no different grade. A warning, never a merge.
const NAME_NOISE = new Set("adv advanced honors honor h ap ib de dc gs dual enrollment credit college intro introduction to and of the course".split(" "));
const coreWords = (name) => new Set(M.normalize(String(name).replace(/\([^)]*\)/g, " ")).split(" ").filter((w) => w && !NAME_NOISE.has(w) && !/^[a-z]{2,4}\d{3}$/.test(w)));
function markLikelyDuplicates(rows, saved) {
  for (const row of rows) {
    const mine = coreWords(row.name);
    if (!mine.size) continue;
    let twin = null, best = 0;
    for (const s of saved) {
      if (row.grade_level && s.grade_level && row.grade_level !== s.grade_level) continue;
      const theirs = coreWords(s.name);
      const [small, big] = mine.size <= theirs.size ? [mine, theirs] : [theirs, mine];
      if (!small.size || ![...small].every((w) => big.has(w)) || ![...small].some((w) => w.length > 2)) continue;
      const overlap = small.size / big.size;
      if (overlap < 0.6) continue; // "Calculus II" is not "Pre-Calculus"
      const score = overlap + (s.level && s.level === row.level ? 0.25 : 0);
      if (score > best) { twin = s; best = score; }
    }
    if (twin) { row.issues = [...row.issues, `Looks like "${twin.name}" you already saved. Untick it if it's the same class.`]; row.on = false; }
  }
}

function txSelect(options, value, onchange, label) {
  return h("select", { "aria-label": label, onchange: (e) => onchange(e.target.value) }, h("option", { value: "" }, "—"),
    ...options.map((o) => h("option", { value: o, selected: o === value }, o)));
}

function renderTranscript(out) {
  const spec = state.idx.lists.courses;
  const field = (id) => spec.fields.find((f) => f.id === id);
  const r = tx.result;
  const ticked = tx.rows.filter((x) => x.on).length;
  const cards = tx.rows.map((row, i) => {
    const set = (k) => (v) => { row[k] = v || null; };
    const num = (k, label) => h("input", { class: "num", inputmode: "decimal", value: row[k] || "", placeholder: label, "aria-label": label, oninput: (e) => set(k)(e.target.value.trim()) });
    return h("div", { class: `txrow${row.issues.length ? " warn" : ""}` },
      h("label", { class: "txtick" }, h("input", { type: "checkbox", checked: row.on, "aria-label": `Save ${row.name}`, onchange: (e) => { row.on = e.target.checked; renderTranscript(out); } }),
        h("input", { class: "txname", value: row.name, "aria-label": "Class name", oninput: (e) => set("name")(e.target.value) })),
      h("div", { class: "txfields" },
        txSelect(field("level").options, row.level, set("level"), "Level"),
        txSelect(field("grade_level").options, row.grade_level, set("grade_level"), "Grade taken"),
        h("input", { class: "num", value: row.grade || "", placeholder: "Grade", "aria-label": "Final grade", oninput: (e) => set("grade")(e.target.value.trim()) }),
        num("credits", "Credits"), num("gpa_points", "GPA pts"),
        txSelect(field("status").options, row.status, set("status"), "Status")),
      row.term || row.term_grades?.length || row.percent ? h("div", { class: "txmeta" }, [row.term, row.term_grades?.length ? `semesters: ${row.term_grades.join(", ")}` : "", row.percent ? `percent: ${row.percent}` : ""].filter(Boolean).join(" · ")) : null,
      row.issues.length ? h("div", { class: "txissue" }, row.issues.join(" ")) : null);
  });
  // GPA and rank lines: never saved unless you tick them; an unlabeled GPA needs you to say which it is
  const s = r.summary || {};
  const sumRows = [];
  const tickSum = (key, label, value) => h("label", { class: "txsum" }, h("input", { type: "checkbox", checked: !!tx.summary[key], onchange: (e) => { tx.summary[key] = e.target.checked ? value : null; } }), ` ${label}`);
  if (s.gpa_unweighted) sumRows.push(tickSum("academics.gpa_unweighted", `Unweighted GPA ${s.gpa_unweighted.value}${s.gpa_unweighted.scale ? ` (scale ${s.gpa_unweighted.scale})` : ""}`, s.gpa_unweighted));
  if (s.gpa_weighted) sumRows.push(tickSum("academics.gpa_weighted", `Weighted GPA ${s.gpa_weighted.value}${s.gpa_weighted.scale ? ` (scale ${s.gpa_weighted.scale})` : ""}`, s.gpa_weighted));
  if (s.gpa_unlabeled) sumRows.push(h("div", { class: "txsum" }, `GPA ${s.gpa_unlabeled.value} — the transcript doesn't say weighted or unweighted. Save it as: `,
    txSelect(["Unweighted GPA", "Weighted GPA"], tx.summary._unlabeled || "", (v) => { tx.summary._unlabeled = v || null; }, "Save the GPA as")));
  (s.gpa_other || []).forEach((g, i) => sumRows.push(h("div", { class: "txsum" }, `GPA "${g.label}": ${g.value}. Save it as: `,
    txSelect(["Unweighted GPA", "Weighted GPA"], tx.summary[`_other${i}`] || "", (v) => { tx.summary[`_other${i}`] = v || null; }, `Save GPA ${g.label} as`))));
  if (s.class_rank) sumRows.push(tickSum("academics.class_rank", `Class rank ${s.class_rank}`, { value: s.class_rank }));
  if (s.class_size) sumRows.push(tickSum("academics.class_size", `Class size ${s.class_size}`, { value: s.class_size }));
  const examRows = (tx.exams || []).map((e) => h("label", { class: "txsum" },
    h("input", { type: "checkbox", checked: e.on, onchange: (ev) => { e.on = ev.target.checked; } }),
    h("input", { class: "txname", value: e.exam, "aria-label": "Exam", oninput: (ev) => { e.exam = ev.target.value; } }),
    h("input", { class: "num", value: e.score, "aria-label": "Score", style: "max-width:44px", oninput: (ev) => { e.score = ev.target.value.trim(); } })));
  const replace = h("input", { type: "checkbox", id: "txReplace" });
  out.replaceChildren(
    h("div", { class: "note" }, `Read ${tx.rows.length} class${tx.rows.length === 1 ? "" : "es"}. Check each one; amber rows need a look before you tick them.`),
    ...(r.notes || []).map((n) => h("div", { class: "note" }, n)),
    r.unread?.length ? h("details", { class: "txunread" }, h("summary", {}, `${r.unread.length} line${r.unread.length === 1 ? "" : "s"} I couldn't read (add them by hand)`), ...r.unread.map((l) => h("div", {}, l))) : null,
    ...cards,
    pointsHelper(out),
    sumRows.length ? h("div", { class: "txsums" }, h("div", { class: "note" }, "Also on the transcript (tick to save):"), ...sumRows) : null,
    examRows.length ? h("div", { class: "txsums" }, h("div", { class: "note" }, "AP and IB exam scores (saved to My info → AP and IB exam scores):"), ...examRows) : null,
    h("label", { class: "txsum" }, replace, " Replace the classes I already saved"),
    h("div", { class: "txbtns" },
      h("button", { class: "chip solid", type: "button", disabled: !ticked && !sumRows.length && !examRows.length, onclick: () => guarded(() => saveTranscript(out, replace.checked)) }, `Save ${ticked} class${ticked === 1 ? "" : "es"}`),
      h("button", { class: "chip", type: "button", onclick: () => { tx.result = null; tx.rows = []; tx.exams = []; tx.summary = {}; out.replaceChildren(); } }, "Discard")),
    h("div", { class: "msg", id: "txMsg" }));
}

// "GPA points" from your school's own scale: you set the rule, it fills only the rows you choose.
function pointsHelper(out) {
  const levels = state.idx.lists.courses.fields.find((f) => f.id === "level").options;
  const bonusDefault = { Honors: 0.5, Advanced: 0.5, "Pre-AP": 0.5, AP: 1, IB: 1, "Dual enrollment": 1, "College course": 1 };
  const inputs = Object.fromEntries(levels.map((l) => [l, h("input", { class: "num", inputmode: "decimal", value: String(bonusDefault[l] ?? 0), "aria-label": `${l} adds` })]));
  const plus = h("input", { class: "num", inputmode: "decimal", value: "0.3", "aria-label": "Plus or minus is worth" });
  const onlyEmpty = h("input", { type: "checkbox", checked: true });
  const msg = h("div", { class: "msg" });
  return h("details", { class: "txhelp" }, h("summary", {}, "Work out GPA points from my school's scale"),
    h("div", { class: "note" }, "A = 4, B = 3, C = 2, D = 1, F = 0, plus what each level adds at your school. Check your school's own scale; P, percent grades and classes without a grade are skipped."),
    h("div", { class: "txgrid" }, h("span", {}, "+ or − is worth"), plus, ...levels.filter((l) => l !== "Other").flatMap((l) => [h("span", {}, `${l} adds`), inputs[l]])),
    h("label", { class: "txsum" }, onlyEmpty, " Only rows with no GPA points yet"),
    h("button", { class: "chip", type: "button", onclick: () => {
      const step = Number(plus.value);
      let n = 0, skipped = 0;
      for (const row of tx.rows) {
        if (!row.on || (onlyEmpty.checked && row.gpa_points)) continue;
        const m = /^([A-F])([+-]?)$/.exec(String(row.grade || "").trim().toUpperCase());
        const bonus = Number(inputs[row.level || "Regular"]?.value || 0);
        if (!m || !Number.isFinite(step) || !Number.isFinite(bonus)) { skipped++; continue; }
        const pts = Math.max(0, LETTER_POINTS[m[1]] + (m[1] === "F" || m[1] === "E" ? 0 : m[2] === "+" ? step : m[2] === "-" ? -step : 0) + (m[1] === "F" || m[1] === "E" ? 0 : bonus));
        row.gpa_points = String(Math.round(pts * 1000) / 1000);
        n++;
      }
      renderTranscript(out);
      toast(`Filled GPA points for ${n} class${n === 1 ? "" : "es"}${skipped ? `; skipped ${skipped} without a letter grade` : ""}. Check them.`);
    } }, "Fill GPA points"), msg);
}

async function saveTranscript(out, replace) {
  const msg = $("#txMsg");
  const have = replace ? [] : state.data.lists.courses || [];
  const key = (c) => [c.name, c.grade_level, c.term, c.grade].map((x) => String(x || "").toLowerCase().trim()).join("|");
  const seen = new Set(have.map(key));
  const keep = ["name", "level", "grade_level", "term", "grade", "credits", "gpa_points", "status"];
  const picked = tx.rows.filter((r) => r.on);
  const items = picked.filter((r) => !seen.has(key(r))).map((r) => Object.fromEntries(keep.filter((k) => r[k] != null && r[k] !== "").map((k) => [k, r[k]])));
  const dupes = picked.length - items.length;
  if (items.length) {
    const res = await api("/v1/list-many", { list: "courses", items, replace });
    if (res.data) state.data = res.data;
    if (!res.ok) {
      const lines = Object.entries(res.errors || {}).map(([i, e]) => `${items[Number(i)]?.name || `Row ${Number(i) + 1}`}: ${Object.values(e).join(" ")}`);
      msg.className = "msg bad"; msg.textContent = `Nothing saved. Fix these first: ${lines.join(" · ") || res.error}`;
      return;
    }
  }
  const haveExams = new Set((state.data.lists.exams || []).map((e) => String(e.exam).toLowerCase().replace(/[^a-z0-9]/g, "")));
  const exams = (tx.exams || []).filter((e) => e.on && e.exam && !haveExams.has(e.exam.toLowerCase().replace(/[^a-z0-9]/g, "")))
    .map((e) => ({ exam: e.exam, ...(e.score ? { score: e.score } : {}) }));
  if (exams.length) {
    const er = await api("/v1/list-many", { list: "exams", items: exams });
    if (er.data) state.data = er.data;
    if (!er.ok) { msg.className = "msg bad"; msg.textContent = `Classes saved, but the exam scores weren't: ${Object.values(er.errors || {}).map((e) => Object.values(e).join(" ")).join(" · ") || er.error}`; return; }
  }
  const changes = {};
  for (const [k, v] of Object.entries(tx.summary)) if (v && !k.startsWith("_")) changes[k] = v.value;
  const un = tx.result.summary?.gpa_unlabeled;
  if (un && tx.summary._unlabeled) changes[tx.summary._unlabeled === "Weighted GPA" ? "academics.gpa_weighted" : "academics.gpa_unweighted"] = un.value;
  const picks = [];
  (tx.result.summary?.gpa_other || []).forEach((g, i) => { const as = tx.summary[`_other${i}`]; if (as) picks.push([as === "Weighted GPA" ? "academics.gpa_weighted" : "academics.gpa_unweighted", g]); });
  for (const [key] of picks) if (picks.filter(([k]) => k === key).length > 1) { $("#txMsg").className = "msg bad"; $("#txMsg").textContent = "You picked two GPAs for the same box. Choose one each."; return; }
  for (const [key, g] of picks) changes[key] = g.value;
  for (const [k, scaleKey] of [["academics.gpa_unweighted", "academics.gpa_unweighted_scale"], ["academics.gpa_weighted", "academics.gpa_weighted_scale"]]) {
    const src = tx.summary[k] || (un && changes[k] === un.value ? un : null);
    if (changes[k] && src?.scale) changes[scaleKey] = src.scale;
  }
  let gpaErr = "";
  if (Object.keys(changes).length) {
    const r = await api("/v1/set", { changes });
    if (r.data) state.data = r.data;
    if (!r.ok) gpaErr = ` Not saved: ${Object.entries(r.errors || {}).map(([k, e]) => `${state.idx.items[k]?.label || k} (${e})`).join(", ")}.`;
  }
  tx.result = null; tx.rows = []; tx.exams = []; tx.summary = {};
  await renderInfo();
  state.openSections.add("courses");
  toast(`Saved ${items.length} class${items.length === 1 ? "" : "es"}${dupes ? ` (${dupes} already saved)` : ""}${exams.length ? ` and ${exams.length} exam score${exams.length === 1 ? "" : "s"}` : ""}.${gpaErr}`);
}

// ------------------------------------------------------------------ keeping up with the browser
chrome.tabs.onActivated.addListener(() => { if (pinnedTab) return; stopMode().catch(() => {}); resetRows(); state.tabId = null; render(); setHint("Switched tabs. Pick boxes on this page."); });
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (tabId !== state.tabId || info.status !== "loading") return;
  state.mode = null; renderModes();
  state.rows.clear(); state.selected.clear(); state.wholePage = false; state.lastBatch = null; $("#done").hidden = true;
  render();
  setHint("The page changed. Pick the boxes on the new page.");
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.mode) guarded(async () => { await stopMode(); setHint("Stopped."); }); });

// ------------------------------------------------------------------ start
(async () => {
  const schema = await (await fetch(chrome.runtime.getURL("schema.json"))).json();
  state.idx = M.indexSchema(schema);
  if (PORTABLE) {
    $("#infohead").textContent = "Saved in this browser only, nowhere else. Back up your answers at the bottom of this tab.";
    setConn("", "Starting…");
    const seed = await (await portable).pendingSeed();
    if (seed) { await loadData(); render(); return unlockScreen(seed); }
  }
  await loadData();
  if (state.data && !savedCount()) setHint(["You haven't saved any answers yet. Open ", h("button", { class: "link", type: "button", onclick: () => showTab("info") }, "My info"),
    PORTABLE ? " to add them." : " to add them, or ask Hermes to save them for you."]);
  render();
})();

// This copy came with someone's answers, locked with a passphrase. Unlock them once, or start blank.
async function unlockScreen(seed) {
  const pass = h("input", { type: "password", id: "seedPass", autocomplete: "off", spellcheck: "false", placeholder: "four words with dashes" });
  const msg = h("div", { class: "msg" });
  const go = async () => {
    if (!pass.value.trim()) return;
    msg.className = "msg"; msg.textContent = "Unlocking…";
    try {
      const r = await (await portable).unlockSeed(seed, pass.value);
      if (!r.ok) { msg.className = "msg bad"; msg.textContent = r.error || "Some saved answers didn't pass the checks, so nothing was added."; pass.select(); return; }
      pass.value = "";
      await loadData();
      setHint(`Unlocked: ${savedCount()} answers are saved in this browser now. Pick boxes on a form with Point or Circle.`, "ok");
      render();
    } catch (e) { msg.className = "msg bad"; msg.textContent = "Couldn't unlock them. Reload the extension and try again."; }
  };
  pass.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  setHint([h("b", {}, "This copy came with saved answers, locked with a passphrase."), " Type it once to add them to this browser.",
    h("div", { class: "f" }, h("label", { for: "seedPass" }, "Passphrase"), pass, msg),
    h("button", { class: "chip solid", type: "button", onclick: go }, "Unlock"), " ",
    h("button", { class: "chip", type: "button", onclick: async () => {
      if (!confirm("Start with no saved answers? The locked answers won't be offered again.")) return;
      await (await portable).skipSeed();
      setHint(["Starting blank. Open ", h("button", { class: "link", type: "button", onclick: () => showTab("info") }, "My info"), " to add your answers."]);
    } }, "Start blank instead")]);
  pass.focus();
}

// for tests: expose state read-only
self.__panelState = () => ({ rows: [...state.rows.values()], selected: [...state.selected], mode: state.mode, conn: state.conn, tabId: state.tabId });
