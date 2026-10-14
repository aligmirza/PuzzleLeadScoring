"""Build a password-protected static copy of the viewer for GitHub Pages.

    .venv/bin/python viewer/build_static.py <list> [<list> ...] [--out docs/viewer]

It asks for a username and password (or reads VIEWER_USER / VIEWER_PASSWORD from the environment), then writes the
page plus ONE encrypted data file into the output folder. The list data is compressed and encrypted with
AES-256-GCM, with the key derived from "username + password" (PBKDF2-SHA256, 600,000 rounds). The browser decrypts it
after the login form; nothing readable is ever written to the output folder.

The encrypted file is public if you publish it, so the password is the only protection: use a long one (the script
refuses fewer than 12 characters). Anyone who knows it can read the data; changing it means building again.
"""
import argparse
import base64
import getpass
import gzip
import json
import os
import shutil
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: E402

import server  # noqa: E402  (reads the saved lists, read-only)

ITERATIONS = 600_000
MIN_PASSWORD = 12


def secret_text(user: str, password: str) -> bytes:
    # same text the browser builds: NFC-normalised username + newline + password
    return (unicodedata.normalize("NFC", user) + "\n" + unicodedata.normalize("NFC", password)).encode("utf-8")


def encrypt(payload: dict, user: str, password: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(secret_text(user, password))
    data = AESGCM(key).encrypt(iv, gzip.compress(json.dumps(payload).encode("utf-8")), None)  # ciphertext + 16-byte tag
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "data": b64(data)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lists", nargs="+", help="list folder names under lists/")
    ap.add_argument("--out", default=str(HERE.parent / "docs" / "viewer"), help="output folder (default: docs/viewer/)")
    args = ap.parse_args()

    user = (os.environ.get("VIEWER_USER") or input("Username: ")).strip()  # the page trims it too
    password = os.environ.get("VIEWER_PASSWORD") or getpass.getpass("Password: ")
    if not os.environ.get("VIEWER_PASSWORD") and getpass.getpass("Repeat password: ") != password:
        sys.exit("The two passwords differ. Nothing was written.")
    if not user:
        sys.exit("A username is required.")
    if len(password) < MIN_PASSWORD:
        sys.exit(f"Use a password of at least {MIN_PASSWORD} characters: the encrypted file is public, so the password is "
                 "the only protection. Nothing was written.")

    tables, meta = {}, []
    for name in args.lists:
        t = server.table(name)
        tables[t["list"]] = t
        meta.append({"list": t["list"], "state": "done"})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.enc.json").write_text(json.dumps(encrypt({"lists": meta, "tables": tables}, user, password)))
    for f in ("index.html", "style.css", "app.js"):
        shutil.copy(HERE / f, out / f)
    html = (out / "index.html").read_text().replace(
        '<script src="app.js">', '<script>window.VIEWER_STATIC = { data: "data.enc.json" };</script>\n<script src="app.js">')
    (out / "index.html").write_text(html)
    (out / ".nojekyll").write_text("")
    size = (out / "data.enc.json").stat().st_size // 1024
    print(f"Wrote {out}/ ({len(tables)} list(s), data.enc.json {size} KB, encrypted). Nothing readable was written.")


if __name__ == "__main__":
    main()
