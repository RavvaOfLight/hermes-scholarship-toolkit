// Real-browser check of the board page: headless Chromium, fictional data, one status change.
//   node tests/browser_smoke.mjs        (CHROME=/path/to/chrome to pick the browser)
import { spawn, execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync } from "node:fs";
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
const DATA = mkdtempSync(join(process.env.SCRATCH || tmpdir(), "board-smoke-"));
const PORT = 18765, CDP = 19465;
const env = { ...process.env, BOARD_DATA_DIR: DATA, BOARD_AUTH: "none" };
const procs = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
process.on("exit", () => procs.forEach((p) => { try { p.kill("SIGKILL"); } catch (e) { /* gone */ } }));
async function until(fn, what, ms = 8000) {
  for (const t0 = Date.now(); ; await sleep(100)) {
    try { const v = await fn(); if (v) return v; } catch (e) { /* retry */ }
    if (Date.now() - t0 > ms) throw new Error("timed out waiting for " + what);
  }
}

execFileSync("python3", [join(HERE, "..", "import.py"), "--evaluated", join(HERE, "fixtures", "evaluated.json"), "--id", "fall", "--label", "Fall search"], { env });
procs.push(spawn("python3", [join(HERE, "..", "server.py"), String(PORT)], { env, stdio: "ignore" }));
await until(async () => (await fetch(`http://127.0.0.1:${PORT}/`)).ok, "board server");
procs.push(spawn(CHROME, ["--headless=new", `--remote-debugging-port=${CDP}`, `--user-data-dir=${join(DATA, "chrome")}`,
  "--no-sandbox", "--disable-gpu", "--no-first-run", "--window-size=400,860", `http://127.0.0.1:${PORT}/`], { stdio: "ignore" }));
const page = await until(async () => (await (await fetch(`http://127.0.0.1:${CDP}/json/list`)).json()).find((t) => t.type === "page" && t.url.includes(String(PORT))), "page");

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let n = 0; const pending = new Map(), errors = [];
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
});
const send = (method, params = {}) => new Promise((r) => { pending.set(++n, r); ws.send(JSON.stringify({ id: n, method, params })); });
const run = async (expr) => (await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true })).result.result.value;
await send("Runtime.enable");
await send("Page.reload");

let passed = 0;
const check = async (name, fn) => { await fn(); passed++; console.log("ok -", name); };
await check("three awards render", async () => assert.equal(await until(() => run("return document.querySelectorAll('.card').length || 0"), "cards"), 3));
await check("an unverified lead is labelled", async () => assert.match(await run("return document.body.innerText"), /Database Lead Award/));
await check("a status tap is saved", async () => {
  await run(`document.querySelector('.card[data-id="example-community-foundation-award"] [data-act=toggle]').click();`);
  await until(() => run(`return !!document.querySelector('.card[data-id="example-community-foundation-award"] [data-status="applied"]')`), "card opens");
  await run(`document.querySelector('.card[data-id="example-community-foundation-award"] [data-status="applied"]').click();`);
  await until(() => JSON.parse(readFileSync(join(DATA, "stores", "fall.json"), "utf8")).scholarships.find((r) => r.id === "example-community-foundation-award").status === "applied", "store write");
});
await check("no page errors", async () => assert.deepEqual(errors, []));
console.log(`all ${passed} board checks passed`);
process.exit(0);
