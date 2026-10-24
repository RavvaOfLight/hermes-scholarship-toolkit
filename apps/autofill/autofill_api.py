"""What each request to the companion does, shared by the local service (autofill_server.py)
and the portable extension, which runs this same code inside the browser (extension/portable/).

dispatch() never checks who is asking: the service does that before calling it.
"""
from __future__ import annotations

import json
from pathlib import Path

import autofill_store as store
import transcript


def dispatch(method: str, path: str, body: dict | None, data_dir: Path | None = None) -> tuple[int, dict]:
    """Returns (HTTP status, reply)."""
    if method == "GET":
        if path == "/v1/health":
            return 200, {"ok": True, "app": "scholarship-autofill", "version": store.VERSION}
        if path == "/v1/data":
            return 200, {"ok": True, "data": store.load(data_dir)}
        return 404, {"ok": False, "error": "not found"}
    body = body or {}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "expected an object"}
    try:
        if path == "/v1/set":
            data, errors = store.set_values(body.get("changes") or {}, data_dir)
            return 200, {"ok": not errors, "data": data, "errors": errors}
        if path == "/v1/list":
            data = store.list_op(body.get("list", ""), body.get("op", ""), body.get("index"),
                                 body.get("item"), body.get("to"), data_dir)
            return 200, {"ok": True, "data": data}
        if path == "/v1/list-many":
            data, errors = store.list_add_many(body.get("list", ""), body.get("items"), bool(body.get("replace")), data_dir)
            return 200, {"ok": not errors, "data": data, "errors": errors}
        if path == "/v1/import":
            data, errors = store.import_data(body.get("data"), data_dir)
            return 200, {"ok": not errors, "data": data, "errors": errors}
        if path == "/v1/transcript":  # read only: nothing is saved until the student reviews the rows
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                return 200, {"ok": False, "error": "Paste the transcript text first."}
            return 200, {"ok": True, "result": transcript.parse(text)}
        if path == "/v1/log":
            return 200, {"ok": True, "logged": store.append_log(body.get("entries") or [], data_dir)}
    except store.ValidationError as err:
        msg = str(err)
        try:
            return 200, {"ok": False, "errors": json.loads(msg)}
        except json.JSONDecodeError:
            return 200, {"ok": False, "error": msg}
    return 404, {"ok": False, "error": "not found"}
