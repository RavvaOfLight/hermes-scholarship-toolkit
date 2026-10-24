#!/usr/bin/env python3
"""Set the board login: set_password.py USER  (asks for the password twice)
                      set_password.py USER --random  (makes one up and prints it once)

Writes BOARD_DATA_DIR/.auth.json (salted PBKDF2 hash, owner-only). Delete that file to turn the
login off. Restart nothing: the server reads it on every request.
"""
import getpass
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("BOARD_DATA_DIR") or Path.home() / ".hermes" / "scholarship-board").expanduser()
AUTH = Path(os.environ["BOARD_AUTH"]) if os.environ.get("BOARD_AUTH", "none").lower() != "none" else DATA_DIR / ".auth.json"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    user = argv[0]
    if "--random" in argv[1:]:
        pw = secrets.token_urlsafe(12)
    else:
        pw = getpass.getpass("New board password: ")
        if pw != getpass.getpass("Same password again: "):
            print("The passwords don't match; nothing changed.")
            return 1
    if len(pw) < 10:
        print("Use at least 10 characters; nothing changed.")
        return 1
    salt = secrets.token_bytes(16)
    n = 600_000
    record = {"user": user, "salt": salt.hex(), "iterations": n,
              "hash": hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, n).hex()}
    AUTH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    print(f"login saved to {AUTH}")
    if "--random" in argv[1:]:
        print(f"password (shown once): {pw}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
