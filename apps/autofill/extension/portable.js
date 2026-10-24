// Portable mode (a Chromebook, or any computer without the companion service). The same Python
// store and transcript reader the service uses run inside the browser with Pyodide, and the saved
// answers live in this extension's own storage. portable/build.py adds pyodide/ and py/ to the
// extension folder; the normal install never loads this file.
const FILES = "autofill.files";   // {"data.json": "...", "history/data-rev00003.json": "...", "fills.jsonl": "..."}
const SEED_DONE = "autofill.seedDone";
const LOG_KEEP = 2000;            // fill-log lines kept (the log never holds values)

let booting = null;
let py = null;

const text = async (path) => (await fetch(chrome.runtime.getURL(path))).text();

const BRIDGE = `
import json, os, shutil, sys
sys.path.insert(0, "/app")
import autofill_api

def call(method, path, body):
    return json.dumps(autofill_api.dispatch(method, path, json.loads(body), "/data"))

def put(files):
    shutil.rmtree("/data", ignore_errors=True)
    os.makedirs("/data", mode=0o700)
    for rel, body in json.loads(files).items():
        path = os.path.normpath(os.path.join("/data", rel))
        if not path.startswith("/data/"):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(body)

def take():
    out = {}
    for root, _, names in os.walk("/data"):
        for name in names:
            path = os.path.join(root, name)
            if name != ".lock" and not name.endswith(".tmp"):
                with open(path) as fh:
                    out[os.path.relpath(path, "/data")] = fh.read()
    return json.dumps(out)
`;

export function boot() {
  if (!booting) booting = (async () => {
    const { loadPyodide } = await import("./pyodide/pyodide.mjs");
    const p = await loadPyodide({ indexURL: chrome.runtime.getURL("pyodide/"), stdout: () => {}, stderr: () => {} });
    p.FS.mkdirTree("/app/extension");
    for (const f of ["autofill_store.py", "transcript.py", "autofill_api.py"]) p.FS.writeFile(`/app/${f}`, await text(`py/${f}`));
    p.FS.writeFile("/app/extension/schema.json", await text("schema.json"));
    p.runPython(BRIDGE);
    py = { call: p.globals.get("call"), put: p.globals.get("put"), take: p.globals.get("take") };
  })().catch((e) => { booting = null; throw e; });
  return booting;
}

// Same requests and replies as the service. One request at a time across every open panel.
export async function api(path, body) {
  await boot();
  return navigator.locks.request("scholarship-autofill-store", async () => {
    const files = (await chrome.storage.local.get(FILES))[FILES] || {};
    py.put(JSON.stringify(files));
    const [status, reply] = JSON.parse(py.call(body ? "POST" : "GET", path, JSON.stringify(body ?? null)));
    if (body) {
      const after = JSON.parse(py.take());
      if (after["fills.jsonl"]) after["fills.jsonl"] = after["fills.jsonl"].split("\n").slice(-LOG_KEEP - 1).join("\n");
      if (JSON.stringify(after) !== JSON.stringify(files)) await chrome.storage.local.set({ [FILES]: after });
    }
    if (status !== 200) throw new Error(`HTTP ${status}`);
    return reply;
  });
}

// ------------------------------------------------------------------ answers that came with the extension
// A copy made for one person can carry their answers in seed.locked, encrypted with a passphrase
// (PBKDF2 + AES-GCM), so a lost USB drive doesn't give them away. Unlocking imports them once.
export async function pendingSeed() {
  const got = await chrome.storage.local.get([FILES, SEED_DONE]);
  if (got[SEED_DONE] || got[FILES]?.["data.json"]) return null;
  try {
    const res = await fetch(chrome.runtime.getURL("seed.locked"));
    return res.ok ? await res.json() : null;
  } catch (e) { return null; }
}

export const normalizePassphrase = (s) => s.normalize("NFKC").toLowerCase().trim().replace(/[\s_\-]+/g, "-");

export async function unlockSeed(blob, passphrase) {
  const bytes = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(normalizePassphrase(passphrase)), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: bytes(blob.salt), iterations: blob.iter },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  let plain;
  try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes(blob.iv) }, key, bytes(blob.ct)); }
  catch (e) { return { ok: false, error: "That passphrase doesn't match." }; }
  const reply = await api("/v1/import", { data: JSON.parse(new TextDecoder().decode(plain)) });
  if (reply.ok) await chrome.storage.local.set({ [SEED_DONE]: true });
  return reply;
}

export const skipSeed = () => chrome.storage.local.set({ [SEED_DONE]: true });
