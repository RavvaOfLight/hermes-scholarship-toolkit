#!/usr/bin/env python3
"""Build the portable extension: a folder you can load on a Chromebook, or in Chrome or Edge on any
computer, without the companion service. The saved answers live in the browser, and the same
Python store and transcript reader run there with Pyodide.

    python3 portable/build.py --out DIR                       # blank copy (for anyone)
    AUTOFILL_SEED_PASSPHRASE='four words with-dashes' \\
    python3 portable/build.py --out DIR --seed ~/.hermes/scholarship-fill/data/data.json
                                                              # a copy that carries one person's answers,
                                                              # encrypted with the passphrase (seed.locked)

DIR gets "Scholarship Autofill/" (the extension folder) and "How to install.html".
Pyodide is downloaded once from the npm registry, checked against a pinned SHA-256, and cached in
~/.cache/scholarship-autofill/ (or pass --pyodide-tgz FILE).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
EXT = APP / "extension"
sys.path.insert(0, str(APP))
import autofill_store as store  # noqa: E402

PYODIDE_VERSION = "0.26.4"
PYODIDE_URL = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"
PYODIDE_SHA256 = "04c2d423c77ec87025d2a61e7d82fe00355a8f24471d60e012da5b3731e32ea5"
PYODIDE_FILES = ("pyodide.mjs", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
PY_FILES = ("autofill_store.py", "transcript.py", "autofill_api.py")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "scholarship-autofill"


def pyodide_tarball(given: str | None) -> bytes:
    path = Path(given) if given else CACHE / f"pyodide-{PYODIDE_VERSION}.tgz"
    if path.exists():
        raw = path.read_bytes()
    else:
        print(f"downloading Pyodide {PYODIDE_VERSION} …", file=sys.stderr)
        with urllib.request.urlopen(PYODIDE_URL, timeout=120) as res:
            raw = res.read()
    if hashlib.sha256(raw).hexdigest() != PYODIDE_SHA256:
        sys.exit(f"{path if path.exists() else PYODIDE_URL}: SHA-256 doesn't match the pinned Pyodide {PYODIDE_VERSION}; not using it")
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw


def build(out: Path, seed: Path | None, tgz: str | None) -> Path:
    ext = out / "Scholarship Autofill"
    if ext.exists():
        shutil.rmtree(ext)
    shutil.copytree(EXT, ext, ignore=shutil.ignore_patterns("config.js", "*.map"))
    if (APP.parents[1] / "LICENSE").exists():  # shared copies must carry the license and its Required Notice
        shutil.copy2(APP.parents[1] / "LICENSE", ext / "LICENSE.txt")
    (ext / "config.js").write_text("// Portable copy: answers are kept in this browser (see portable.js).\n"
                                   'self.AUTOFILL = { mode: "portable" };\n')

    manifest = json.loads((EXT / "manifest.json").read_text())
    manifest["key"] = (HERE / "manifest_key.txt").read_text().strip()  # same extension ID wherever the folder is loaded from
    manifest["content_security_policy"] = {"extension_pages": "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'"}
    manifest["description"] = manifest["description"] + " Portable: your answers stay in this browser."
    (ext / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    (ext / "py").mkdir()
    for name in PY_FILES:
        shutil.copy2(APP / name, ext / "py" / name)

    (ext / "pyodide").mkdir()
    with tarfile.open(fileobj=io.BytesIO(pyodide_tarball(tgz)), mode="r:gz") as tar:
        for name in PYODIDE_FILES:
            member = tar.getmember(f"package/{name}")
            (ext / "pyodide" / name).write_bytes(tar.extractfile(member).read())
    (ext / "pyodide" / "THIRD_PARTY.txt").write_text(
        f"Pyodide {PYODIDE_VERSION} (https://github.com/pyodide/pyodide), unmodified, from the npm package\n"
        f"{PYODIDE_URL}\nIt includes CPython and its standard library (Python Software Foundation License).\n"
        "See the Pyodide repository for its license terms.\n")

    if seed:
        passphrase = os.environ.get("AUTOFILL_SEED_PASSPHRASE", "")
        if len([w for w in re.split(r"[\s_\-]+", passphrase) if w]) < 4:
            sys.exit("set AUTOFILL_SEED_PASSPHRASE to at least four words to lock the answers")
        data = json.loads(seed.read_text())
        if data.get("_meta", {}).get("version") != store.VERSION:
            sys.exit(f"{seed} isn't a version {store.VERSION} answers file")
        keep = {"_meta": {"version": data.get("_meta", {}).get("version")}, "values": data.get("values", {}), "lists": data.get("lists", {})}
        subprocess.run(["node", str(HERE / "lock_seed.mjs"), str(ext / "seed.locked")], input=json.dumps(keep),
                       text=True, check=True, env={**os.environ, "AUTOFILL_SEED_PASSPHRASE": passphrase})

    shutil.copy2(HERE / "how-to-install.html", out / "How to install.html")
    return ext


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seed", type=Path, help="data.json whose answers this copy carries (locked)")
    ap.add_argument("--pyodide-tgz", help=f"a local copy of {PYODIDE_URL}")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    ext = build(args.out, args.seed, args.pyodide_tgz)
    size = sum(f.stat().st_size for f in ext.rglob("*") if f.is_file())
    print(f"built {ext} ({size / 1e6:.1f} MB){' with locked answers' if args.seed else ''}")


if __name__ == "__main__":
    main()
