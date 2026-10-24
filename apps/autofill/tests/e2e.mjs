// End-to-end: headless Chromium + the real extension + a form full of submit traps.
//   node tests/e2e.mjs                 the extension with the companion service
//   PORTABLE=1 node tests/e2e.mjs      the portable copy (portable/build.py): answers in the browser,
//                                      unlocked from seed.locked (PYODIDE_TGZ=file to skip the download)
import { spawn, execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readdirSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const HERE = dirname(fileURLToPath(import.meta.url));
const CHROME = findChrome();

// CHROME, else the newest Playwright Chromium, else a system Chromium. (Branded Google Chrome
// ignores --load-extension, so use Chromium or Chrome for Testing.)
function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const pw = join(homedir(), ".cache", "ms-playwright");
  const builds = existsSync(pw) ? readdirSync(pw).filter((d) => /^chromium-\d+$/.test(d)).sort((a, b) => b.split("-")[1] - a.split("-")[1]) : [];
  const found = [...builds.map((d) => join(pw, d, "chrome-linux64", "chrome")), "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium"].find((p) => existsSync(p));
  if (!found) { console.error("No Chromium found. Set CHROME=/path/to/chromium (npx playwright install chromium gets one)."); process.exit(2); }
  return found;
}
const SCRATCH = mkdtempSync(join(process.env.SCRATCH || tmpdir(), "sa-e2e-"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const procs = [];
const PORT = 18781, WEB = 18782, CDP = 19455, TOKEN = "e2e-token";
const PORTABLE = process.env.PORTABLE === "1";
const PASSPHRASE = "test-only-four-words";

function start(cmd, args, opts = {}) { const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts }); procs.push(p); return p; }
function cleanup() { for (const p of procs) try { p.kill("SIGKILL"); } catch (e) { /* gone */ } }
process.on("exit", cleanup);

async function until(fn, what, ms = 8000) {
  const t0 = Date.now();
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch (e) { /* retry */ }
    if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what}`);
    await sleep(100);
  }
}

class Target {
  constructor(info) { this.info = info; this.id = 0; this.pending = new Map(); this.logs = []; }
  async open() {
    this.ws = new WebSocket(this.info.webSocketDebuggerUrl);
    await new Promise((r) => this.ws.addEventListener("open", r));
    this.ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { const { res, rej } = this.pending.get(m.id); this.pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
      if (m.method === "Runtime.exceptionThrown") this.logs.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    });
    await this.send("Runtime.enable");
    return this;
  }
  send(method, params = {}) { return new Promise((res, rej) => { const i = ++this.id; this.pending.set(i, { res, rej }); this.ws.send(JSON.stringify({ id: i, method, params })); }); }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true, userGesture: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  }
  async mouse(type, x, y) { await this.send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, pointerType: "mouse" }); }
}

async function main() {
  // 1. a copy of the extension pointed at a test service, or a portable copy carrying the test answers
  let ext = join(SCRATCH, "ext");
  if (PORTABLE) {
    const seed = join(SCRATCH, "seed.json");
    writeFileSync(seed, JSON.stringify({ _meta: { version: 2 }, ...JSON.parse(readFileSync(join(HERE, "fixtures", "test_data.json"), "utf8")) }));
    execFileSync("python3", [join(HERE, "..", "portable", "build.py"), "--out", join(SCRATCH, "portable"), "--seed", seed,
      ...(process.env.PYODIDE_TGZ ? ["--pyodide-tgz", process.env.PYODIDE_TGZ] : [])], { env: { ...process.env, AUTOFILL_SEED_PASSPHRASE: PASSPHRASE.replaceAll("-", " ") }, stdio: "inherit" });
    ext = join(SCRATCH, "portable", "Scholarship Autofill");
  } else {
    cpSync(join(HERE, "..", "extension"), ext, { recursive: true });
    writeFileSync(join(ext, "config.js"), `self.AUTOFILL = { port: ${PORT}, token: "${TOKEN}" };\n`);
    const svc = start("python3", [join(HERE, "run_service.py"), String(PORT), TOKEN, join(SCRATCH, "data"), join(HERE, "fixtures", "test_data.json")]);
    await until(() => new Promise((r) => svc.stdout.once("data", (d) => r(String(d).includes("ready")))), "service");
  }
  start("python3", ["-m", "http.server", String(WEB), "--bind", "127.0.0.1", "--directory", join(HERE, "fixtures")]);
  await until(async () => (await fetch(`http://127.0.0.1:${WEB}/form.html`)).ok, "web server");
  start(CHROME, ["--headless=new", `--remote-debugging-port=${CDP}`, `--user-data-dir=${join(SCRATCH, "profile")}`, "--no-sandbox", "--disable-gpu",
    "--no-first-run", "--window-size=1200,1600", `--disable-extensions-except=${ext}`, `--load-extension=${ext}`, "about:blank"]);
  const list = () => fetch(`http://127.0.0.1:${CDP}/json/list`).then((r) => r.json());
  // several built-in extensions also have a background.js; ours is the one with our manifest name
  const sw = await until(async () => {
    for (const t of (await list()).filter((x) => x.type === "service_worker")) {
      const tgt = await new Target(t).open();
      if ((await tgt.eval("return chrome.runtime.getManifest().name").catch(() => "")) === "Scholarship Autofill") return tgt;
      tgt.ws.close();
    }
    return null;
  }, "extension");
  const extId = new URL(sw.info.url).host;
  const tabId = await sw.eval(`return (await chrome.tabs.create({ url: "http://127.0.0.1:${WEB}/form.html", active: true })).id`);
  const formInfo = await until(async () => (await list()).find((t) => t.type === "page" && t.url.includes("/form.html")), "form tab");
  const form = await new Target(formInfo).open();
  await until(() => form.eval("return document.readyState === 'complete'"), "form load");
  await sw.eval(`await chrome.tabs.create({ url: "chrome-extension://${extId}/panel.html?tab=${tabId}", active: false }); return true`);
  const panelInfo = await until(async () => (await list()).find((t) => t.url.includes("/panel.html")), "panel");
  const panel = await new Target(panelInfo).open();
  const results = [];
  const check = (name, fn) => { try { fn(); results.push(["ok", name]); } catch (e) { results.push(["FAIL", `${name}: ${e.message}`]); } };
  if (PORTABLE) {
    // the answers that came with this copy are locked: a wrong passphrase adds nothing, the right one adds them once
    await until(() => panel.eval("return !!document.querySelector('#seedPass')"), "unlock screen", 20000);
    const unlock = (pass) => panel.eval(`const p = document.querySelector('#seedPass'); p.value = ${JSON.stringify(pass)};
      [...document.querySelectorAll('#hint button')].find((b) => b.textContent === 'Unlock').click(); return true`);
    await unlock("wrong words entirely here");
    const wrong = await until(() => panel.eval("const m = document.querySelector('#hint .msg'); return /match/.test(m.textContent) && m.textContent"), "wrong passphrase", 20000);
    check("a wrong passphrase unlocks nothing", () => assert.match(wrong, /doesn't match/));
    await unlock(" Test Only Four  Words ");  // case and spacing don't matter
    await until(() => panel.eval("return /Unlocked/.test(document.querySelector('#hint').textContent)"), "unlocked", 20000);
  }
  await until(() => panel.eval("return document.querySelector('#conn').classList.contains('ok')"), "panel connected", 20000);
  const savedData = async () => PORTABLE
    ? panel.eval("return (await (await import('./portable.js')).api('/v1/data')).data")
    : (await fetch(`http://127.0.0.1:${PORT}/v1/data`, { headers: { "X-Autofill-Token": TOKEN } }).then((x) => x.json())).data;
  if (PORTABLE) {
    const d = await savedData();
    check("the unlocked answers are saved in the browser", () => assert.deepEqual([d.values["student.first_name"], d.lists.courses.length], ["Ava", 3]));
  }
  const rows = () => panel.eval("return self.__panelState().rows.map(r => ({ref: r.ref, label: r.label, status: r.status, item: r.item, display: r.display}))");
  const val = (sel) => form.eval(`return document.querySelector(${JSON.stringify(sel)}).value`);

  // 2. Whole page: review list
  await panel.eval("document.querySelector('#mPage').click(); return true");
  await until(async () => (await rows()).length > 5, "whole-page rows");
  const r1 = await rows();
  const by = (label) => r1.find((r) => r.label.replace(/\s*\*$/, "") === label) || {};
  check("first name ready", () => assert.deepEqual([by("First Name").status, by("First Name").display], ["ready", "Ava"]));
  check("middle name kept (has text)", () => assert.equal(by("Middle Name").status, "has_value"));
  check("phone formatted to the box", () => assert.equal(by("Cell Phone").display, "(555) 555-0182"));
  check("dob formatted", () => assert.equal(by("Date of Birth").display, "04/15/2009"));
  check("state option", () => assert.equal(by("State").display, "Kansas"));
  check("gender option", () => assert.equal(by("Gender").display, "Female"));
  check("SSN blocked", () => assert.equal(by("Social Security Number").status, "blocked"));
  check("essay blocked", () => assert.equal(by("Why do you deserve this scholarship?").status, "blocked"));
  check("certify blocked", () => assert.equal(by("I certify that everything above is true").status, "blocked"));
  check("shadow-DOM ZIP found", () => assert.equal(by("ZIP Code").display, "12345"));
  check("iframe county found", () => assert.equal(by("County").display, "Lakeside"));
  const parentFirst = r1.filter((r) => r.label === "First Name" && r.item === "parent1.first_name");
  check("parent section first name", () => assert.equal(parentFirst.length, 1));
  check("counselor email by heading", () => assert.ok(r1.some((r) => r.item === "school.counselor_email" && r.status === "ready")));

  // 3. Fill everything ticked; the page's traps try to submit three ways
  await panel.eval("document.querySelector('#fillBtn').click(); return true");
  await until(() => panel.eval("return !document.querySelector('#done').hidden"), "fill finished", 15000);
  await sleep(300);
  const doneText = await panel.eval("return document.querySelector('#done').textContent");
  const url = await form.eval("return location.pathname");
  const submits = await form.eval("return window.__submits");
  check("no navigation to submitted page", () => assert.equal(url, "/form.html"));
  check("no submit events got through", () => assert.equal(submits, 0));
  check("panel reports blocked submit attempts", () => assert.match(doneText, /tried to submit [1-9]/));
  const values = {
    fn: await val("#fn"), mn: await val("#mn"), ln: await val("#ln"), em: await val("#em"), ph: await val("#ph"), dob: await val("#dob"),
    st: await form.eval("const s = document.querySelector('#st'); return s.options[s.selectedIndex].text"),
    gd: await form.eval("const s = document.querySelector('#gd'); return s.options[s.selectedIndex].text"),
    ssn: await val("#ssn"), es: await val("#es"), cert: await form.eval("return document.querySelector('#cert').checked"),
    zip: await form.eval("return document.querySelector('#shadowhost').shadowRoot.querySelector('#z').value"),
    county: await form.eval("return document.querySelector('#fr').contentDocument.querySelector('#co').value"),
    p1f: await val("#p1f"), p1e: await val("#p1e"), ce: await val("#ce"),
  };
  check("values typed exactly", () => assert.deepEqual(values, {
    fn: "Ava", mn: "Q", ln: "Testerson", em: "ava.t@example.com", ph: "(555) 555-0182", dob: "04/15/2009", st: "Kansas", gd: "Female",
    ssn: "", es: "", cert: false, zip: "12345", county: "Lakeside", p1f: "Maria", p1e: "maria@example.com", ce: "dlee@school.example.org" }));
  const reactState = await form.eval("return window.__reactLast");
  check("React-style box kept the value in its own state", () => assert.equal(reactState, "Testerson"));

  // 4. After the guard window, the user's own Submit still works (checked below); first Undo
  await panel.eval("[...document.querySelectorAll('#done button')].find(b => b.textContent === 'Undo').click(); return true");
  await until(async () => (await val("#fn")) === "", "undo");
  const afterUndo = [await val("#mn"), await val("#em"), await val("#p1f")];
  check("undo put every box back (the untouched middle name stays Q)", () => assert.deepEqual(afterUndo, ["Q", "", ""]));

  // 5. Circle the parent section with a real mouse drag
  await panel.eval("document.querySelector('#clearBtn').click(); return true");
  await panel.eval("document.querySelector('#mCircle').click(); return true");
  await until(() => panel.eval("return self.__panelState().mode === 'circle'"), "circle mode");
  const box = await form.eval(`const a = document.querySelector('#p1f').getBoundingClientRect(), b = document.querySelector('#p1e').getBoundingClientRect();
    return {x1: a.left - 20, y1: a.top - 30, x2: b.right + 20, y2: b.bottom + 20}`);
  await form.send("Page.bringToFront");
  const pts = [[box.x1, box.y1], [box.x2, box.y1], [box.x2, box.y2], [box.x1, box.y2], [box.x1, box.y1 + 4]];
  await form.mouse("mousePressed", pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) for (let k = 1; k <= 6; k++) {
    const [ax, ay] = pts[i - 1], [bx, by2] = pts[i];
    await form.mouse("mouseMoved", ax + ((bx - ax) * k) / 6, ay + ((by2 - ay) * k) / 6);
  }
  await form.mouse("mouseReleased", pts[4][0], pts[4][1]);
  await until(async () => (await rows()).length === 2, "circled rows");
  const r2 = await rows();
  check("circle picked exactly the two parent boxes", () => assert.deepEqual(r2.map((r) => r.item), ["parent1.first_name", "parent1.email"]));

  // 6. Tell it what to fill, then click the box it goes in
  await panel.eval("document.querySelector('#clearBtn').click(); const a = document.querySelector('#ask'); a.value = 'counselor email'; a.dispatchEvent(new Event('input')); return true");
  await until(() => panel.eval("return !!document.querySelector('.res')"), "search results");
  await panel.eval("[...document.querySelectorAll('.res button')].find(b => b.textContent === 'Put in a box').click(); return true");
  await until(() => panel.eval("return self.__panelState().mode === 'place'"), "place mode");
  const ce = await form.eval("const r = document.querySelector('#ce').getBoundingClientRect(); return {x: r.left + 20, y: r.top + r.height / 2}");
  await form.mouse("mouseMoved", ce.x, ce.y);
  await form.mouse("mousePressed", ce.x, ce.y);
  await form.mouse("mouseReleased", ce.x, ce.y);
  await until(async () => (await val("#ce")) === "dlee@school.example.org", "placed value", 10000);
  results.push(["ok", "put-in-a-box filled the clicked box"]);

  // 7. A real user click on Submit after the guard has expired does submit (the guard is temporary)
  await sleep(5200);
  await form.eval("document.querySelector('#fn').value = 'Ava'; document.querySelector('#go').click(); return true");
  await until(async () => (await form.eval("return location.pathname")) === "/submitted.html", "user submit", 6000).then(
    () => results.push(["ok", "your own Submit click still works after filling"]),
    () => results.push(["FAIL", "your own Submit click was blocked after the guard window"]));

  // 8. Static guarantee: the in-page script has no way to submit, and its only key is Escape on a dropdown it opened
  const src = readFileSync(join(HERE, "..", "extension", "content.js"), "utf8");
  check("content.js never submits; presses only happen in press() and Escape only in comboClose()", () => {
    for (const bad of [".submit(", "requestSubmit", "location.href =", "window.open", "keypress", "\"Enter\"", "'Enter'", "key: \"Tab\""]) assert.ok(!src.includes(bad), bad);
    const fn = (name) => { const a = src.indexOf(`function ${name}(`); return src.slice(a, src.indexOf("\n  }\n", a)); };
    const outside = src.replace(fn("press"), "").replace(fn("comboClose"), "");
    assert.ok(!/new (?:Mouse|Pointer|Keyboard)Event/.test(outside), "synthetic mouse/key events outside press()/comboClose()");
    assert.deepEqual([...fn("comboClose").matchAll(/key: "(\w+)"/g)].map((m) => m[1]), ["Escape"]);
    assert.match(fn("press"), /if \(!unsafeToPress\(el\)\)/);
    const clicks = src.match(/\.click\(\)/g) || [];
    assert.ok(clicks.length <= 6, `unexpected .click() count ${clicks.length}`);
  });

  // 9. Custom dropdowns: open them at Fill time, pick exact matches only, never through a submit button
  await form.send("Page.navigate", { url: `http://127.0.0.1:${WEB}/combos.html` });
  await until(async () => (await form.eval("return location.pathname + document.readyState")) === "/combos.htmlcomplete", "combos page");
  await sleep(300);
  await panel.eval("document.querySelector('#mPage').click(); return true");
  await until(async () => (await rows()).length >= 10, "dropdown rows", 10000);
  const r3 = await rows();
  const c = (label) => r3.find((r) => r.label === label) || {};
  check("listbox button waits for Fill", () => assert.deepEqual([c("State of residence").status, c("State of residence").display], ["check", "Kansas (KS)"]));
  check("type-to-search box found", () => assert.equal(c("Citizenship status").status, "check"));
  check("Google-Forms-style list matched before Fill", () => assert.deepEqual([c("Do you qualify for financial aid?").status, c("Do you qualify for financial aid?").display], ["ready", "Yes"]));
  check("Select2-style hidden select uses the income range", () => assert.deepEqual([c("Total family income").status, c("Total family income").display], ["check", "$50,000 - $74,999"]));
  check("Chosen-style hidden select", () => assert.deepEqual([c("Current grade").status, c("Current grade").display], ["ready", "12th"]));
  check("SAI box", () => assert.equal(c("Student Aid Index (SAI)").display, "2400"));
  check("class rows", () => assert.deepEqual([c("Course 1 name").display, c("Course 1 level").display], ["AP Biology", "AP"]));
  check("AP count includes the class in progress (so it waits for you)", () => assert.deepEqual([c("Number of AP courses taken").status, c("Number of AP courses taken").display], ["check", "2"]));
  // tick every "check" row, like a person who looked at each one
  await panel.eval("[...document.querySelectorAll('.row.s-check input[type=checkbox]')].forEach((b) => { if (!b.checked) b.click(); }); return true");
  await panel.eval("document.querySelector('#fillBtn').click(); return true");
  await until(() => panel.eval("return !document.querySelector('#done').hidden"), "dropdown fill finished", 30000);
  await sleep(400);
  const dom = await form.eval(`return {
    state: document.querySelector('#stval').textContent, ctz: document.querySelector('.rs-single').textContent,
    gf: document.querySelector('#gf [aria-selected=true]').textContent.trim(), inc: document.querySelector('#incshow').textContent,
    incSel: document.querySelector('#inc').selectedIndex, gr: document.querySelector('#grshow').textContent,
    gnd: document.querySelector('#gnd').value, sai: document.querySelector('#sai').value, c1n: document.querySelector('#c1n').value,
    c1l: document.querySelector('#c1l').value, apn: document.querySelector('#apn').value, path: location.pathname, submits: window.__submits,
    lists: document.querySelectorAll('[role=listbox].pop').length }`);
  check("dropdowns show exactly the saved answers", () => assert.deepEqual(
    [dom.state, dom.ctz, dom.gf, dom.inc, dom.incSel, dom.gr, dom.sai, dom.c1n, dom.c1l, dom.apn],
    ["Kansas", "U.S. Citizen", "Yes", "$50,000 - $74,999", 3, "12th", "2400", "AP Biology", "AP", "2"]));
  check("a look-alike that ignores its select was put back", () => assert.equal(dom.gnd, ""));
  check("the dropdown that is a submit button was not pressed and nothing submitted", () => assert.deepEqual([dom.path, dom.submits], ["/combos.html", 0]));
  check("no dropdown left open", () => assert.equal(dom.lists, 0));
  const r4 = await rows();
  if (process.env.E2E_DEBUG) console.log(JSON.stringify(await panel.eval("return self.__panelState().rows.filter(r => r.status !== 'filled').map(r => [r.label, r.status, r.reason])")));
  const c4 = (label) => r4.find((r) => r.label === label) || {};
  check("panel reports both dropdowns it couldn't do", () => assert.deepEqual([c4("Gender").status, c4("Pronouns").status], ["failed", "failed"]));
  const done2 = await panel.eval("return document.querySelector('#done').textContent");
  check("panel reports the blocked submit trap", () => assert.match(done2, /tried to submit [1-9]/));
  await panel.eval("[...document.querySelectorAll('#done button')].find(b => b.textContent === 'Undo').click(); return true");
  await until(async () => (await form.eval("return document.querySelector('#sai').value")) === "", "dropdown undo", 15000);
  await sleep(300);
  const undone = await form.eval("return [document.querySelector('#incshow').textContent, document.querySelector('#inc').selectedIndex, document.querySelector('#grshow').textContent, document.querySelector('#c1l').value]");
  check("undo put the hidden selects back", () => assert.deepEqual(undone, ["Select", 0, "Select grade", ""]));
  const hint = await panel.eval("return document.querySelector('#hint').textContent");
  check("undo says which dropdowns can't be emptied again", () => assert.match(hint, /can't be emptied/));

  // 10. Paste a transcript in My info, check the rows, save them (and the GPA lines you tick)
  await panel.eval("document.querySelector('[data-tab=info]').click(); return true");
  await until(() => panel.eval("return !!document.querySelector('details[data-list=courses]')"), "my info");
  const txt = readFileSync(join(HERE, "fixtures", "transcripts", "aligned.txt"), "utf8");
  await panel.eval(`const d = document.querySelector('details[data-list=courses]'); d.open = true;
    [...d.querySelectorAll('button')].find((b) => b.textContent === 'Paste your transcript').click();
    d.querySelector('.txin').value = ${JSON.stringify(txt)};
    [...d.querySelectorAll('button')].find((b) => b.textContent === 'Read it').click(); return true`);
  await until(async () => (await panel.eval("return document.querySelectorAll('.txrow').length")) === 11, "transcript rows");
  const firstRow = await panel.eval("const r = document.querySelector('.txrow'); return [r.querySelector('.txname').value, ...[...r.querySelectorAll('select')].map((s) => s.value)]");
  check("transcript row read into the review list", () => assert.deepEqual(firstRow, ["English 9 Honors", "Honors", "9th grade", "Completed"]));
  const dupNote = await panel.eval("const r = [...document.querySelectorAll('.txrow')].find((x) => x.querySelector('.txname').value === 'English 10 H'); return [r.querySelector('input[type=checkbox]').checked, r.querySelector('.txissue')?.textContent || '']");
  check("a class already saved under another name is flagged and unticked", () => { assert.equal(dupNote[0], false); assert.match(dupNote[1], /English 10 Honors/); });
  const leftText = await panel.eval("return document.querySelector('.txin').value");
  check("the pasted text is cleared once read", () => assert.equal(leftText, ""));
  await panel.eval("[...document.querySelectorAll('.txsums input[type=checkbox]')].forEach((b) => b.click()); [...document.querySelectorAll('.txbtns button')].find((b) => /^Save [0-9]+ class/.test(b.textContent)).click(); return true");
  await until(async () => (await savedData()).lists.courses.length === 13, "saved classes", 8000);
  const saved = await savedData();
  check("10 new classes added to the 3 already saved (the duplicate stayed out)", () => assert.equal(saved.lists.courses.length, 13));
  check("ticked GPA lines saved with their scale", () => assert.deepEqual(
    [saved.values["academics.gpa_unweighted"], saved.values["academics.gpa_unweighted_scale"], saved.values["academics.gpa_weighted"], saved.values["academics.class_rank"], saved.values["academics.class_size"]],
    ["3.78", "4.00", "4.12", "15", "312"]));

  // 11. A backup restores only when every answer passes the checks again
  const restore = (data) => panel.eval(`return await new Promise((done) => {
    const input = document.querySelector('details[data-sec=_backup] input[type=file]');
    self.confirm = () => true;
    const dt = new DataTransfer(); dt.items.add(new File([${JSON.stringify("")} + JSON.stringify(${JSON.stringify(data)})], "backup.json", { type: "application/json" }));
    input.files = dt.files; input.dispatchEvent(new Event("change"));
    const t = setInterval(() => { const m = document.querySelector('details[data-sec=_backup] .msg'); if (m && m.textContent && m.textContent !== "Restoring…") { clearInterval(t); done(m.className + "|" + m.textContent); } }, 50);
    setTimeout(() => { clearInterval(t); done("timeout"); }, 8000);
  })`);
  await panel.eval("document.querySelector('details[data-sec=_backup]').open = true; return true");
  const bad = await restore({ ...saved, values: { ...saved.values, "academics.act_composite": "63" } });
  const afterBad = await savedData();
  check("a backup with a bad answer restores nothing", () => { assert.match(bad, /bad\|Nothing was restored/); assert.equal(afterBad.lists.courses.length, 13); });
  const small = { _meta: { version: 2 }, values: { "student.first_name": "Ava", "student.last_name": "Testerson" }, lists: { courses: [{ name: "AP Biology", level: "AP" }] } };
  await panel.eval(`document.querySelector('details[data-sec=_backup] .msg').textContent = ""; return true`);
  await restore(small);
  await until(async () => (await savedData()).lists.courses.length === 1, "restored backup", 8000);
  const afterGood = await savedData();
  check("a good backup replaces everything", () => assert.deepEqual([Object.keys(afterGood.values).length, afterGood.lists.courses.length], [2, 1]));

  const errs = [...panel.logs, ...form.logs, ...sw.logs];
  check("no script errors", () => assert.deepEqual(errs, []));
  for (const [s, n] of results) console.log(`${s === "ok" ? "✔" : "✖"} ${n}`);
  const failed = results.filter((r) => r[0] !== "ok").length;
  console.log(failed ? `\n${failed} FAILED` : `\nall ${results.length} checks passed`);
  cleanup();
  rmSync(SCRATCH, { recursive: true, force: true });
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); cleanup(); process.exit(1); });
