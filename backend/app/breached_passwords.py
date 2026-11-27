"""The bundled breached-password list: its key, how it is read, and how
it is made.

    cd backend
    python -m app.breached_passwords            # rewrite app/data/breached_passwords.txt
    python -m app.breached_passwords --check    # exit 0 only if rebuilt bytes are identical
    ... --source PATH                           # a local copy of the source, not a download

**One function makes the key, for both sides.** `breached_key` is what
`build` writes into the file and what `auth.check_pwned_password` looks
up, and both live in this module. If the two disagreed (on case, on
encoding, on the hash), the check would never match and would look exactly
like a check that works: it would accept every password, which is what it
did from the beginning until this list shipped (docs/roadmap.md, "The
bundled breached-password list does not exist").
`tests/test_breached_password_list.py` refuses a listed password, typed in
a case the source never had, through the change-password endpoint.

**The file holds keys, not passwords** (Jarrod, 2026-11-27). A line is the
first 16 hex digits (64 bits) of SHA-256 over the lowercased password in
UTF-8. Unsalted, so it hides nothing from anyone holding the source list.
What it does is keep 122,000 leaked strings out of a public repository,
out of secret scanners and clone policies, and out of the question of
whether a licence on someone's compilation reaches the strings. 64 bits
over ~1.2e5 keys makes a chance match for any one password about 7e-15.

**The source, pinned:** SecLists' `Pwdb_top-10000000.txt` at
`SOURCE_COMMIT`, checked against `SOURCE_SHA256` before a byte is read.
SecLists renamed it from `ignis-10M.txt` (2025-03-22); the name points at
ignis-sec/Pwdb-Public, "a collection of all the data i could extract from
1 billion leaked credentials". Kept: lines that decode as UTF-8 and are
as long as the password policy allows (`MIN_LEN` to `MAX_LEN`, measured
as `auth.validate_password_policy` measures, before lowercasing). A
shorter password is refused before the list is asked, so a shorter entry
could never match: of the top million passwords, 0.5% are 15 characters
or longer.

The header (lines starting `#`) records the source URL, its SHA-256 and
the filter, so the bytes can be reproduced from the header alone. The
deployment guide's "The breached-password list" is the operator's page.

Kept free of app imports, so that `python -m` needs no settings.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request

KEY_VERSION = 1
KEY_HEX_DIGITS = 16

PATH = os.path.join(os.path.dirname(__file__), "data", "breached_passwords.txt")

SOURCE_REPO = "danielmiessler/SecLists"
SOURCE_COMMIT = "12274c98fdebe98c7a7284914436a472ed469aed"
SOURCE_PATH = "Passwords/Common-Credentials/Pwdb_top-10000000.txt"
SOURCE_SHA256 = "18dc49ca32b62455a61e3398f4ab9f93eb700ff142fa0d4b9fd11a727f3b80e4"
SOURCE_URL = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_COMMIT}/{SOURCE_PATH}"

# The password policy's bounds (auth._PASSWORD_MIN_LEN, _PASSWORD_MAX_LEN).
# Not imported, because auth imports this module.
# tests/test_breached_password_list.py fails when they differ.
MIN_LEN = 15
MAX_LEN = 128


def breached_key(password: str) -> str:
    """The list's key for `password`. The only place it is computed."""
    digest = hashlib.sha256(password.lower().encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()[:KEY_HEX_DIGITS]


def read(path: str = PATH) -> tuple[dict[str, str], frozenset[str]]:
    """The header's `name: value` fields and the keys. Raises
    FileNotFoundError when the file is absent."""
    header: dict[str, str] = {}
    keys: set[str] = set()
    with open(path, encoding="ascii") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#"):
                name, sep, value = line[1:].strip().partition(":")
                if sep:
                    header[name.strip()] = value.strip()
            elif line:
                keys.add(line)
    return header, frozenset(keys)


def read_header(path: str = PATH) -> tuple[dict[str, str], bool]:
    """The header, and whether a key follows it, without reading the rest:
    `/health` asks on every healthcheck. Raises FileNotFoundError."""
    header: dict[str, str] = {}
    with open(path, encoding="ascii") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.startswith("#"):
                if line:
                    return header, True
                continue
            name, sep, value = line[1:].strip().partition(":")
            if sep:
                header[name.strip()] = value.strip()
    return header, False


def build(raw: bytes, expected_sha256: str = SOURCE_SHA256) -> bytes:
    """The file's bytes from the source's bytes; refuses any other source."""
    got = hashlib.sha256(raw).hexdigest()
    if got != expected_sha256:
        raise SystemExit(f"source SHA-256 is {got}, expected {expected_sha256}; refusing")
    keys: set[str] = set()
    lines = undecodable = 0
    for line in raw.split(b"\n"):
        line = line.removesuffix(b"\r")
        if not line:
            continue
        lines += 1
        try:
            password = line.decode("utf-8")
        except UnicodeDecodeError:
            undecodable += 1
            continue
        if MIN_LEN <= len(password) <= MAX_LEN:
            keys.add(breached_key(password))
    header = [
        "WinGRC breached-password list. Not passwords: each line is a key,",
        f"the first {KEY_HEX_DIGITS} hex digits of SHA-256 over the lowercased password in UTF-8",
        "(backend/app/breached_passwords.py, breached_key).",
        "Rebuild and verify: cd backend; python -m app.breached_passwords [--check]",
        f"key_version: {KEY_VERSION}",
        f"source: {SOURCE_URL}",
        f"source_sha256: {SOURCE_SHA256}",
        f"source_lines: {lines}",
        f"source_lines_not_utf8: {undecodable}",
        f"min_length: {MIN_LEN}",
        f"max_length: {MAX_LEN}",
        f"keys: {len(keys)}",
    ]
    out = "".join(f"# {h}\n" for h in header) + "".join(f"{k}\n" for k in sorted(keys))
    return out.encode("ascii")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.breached_passwords")
    ap.add_argument("--source", help="a local copy of the source file")
    ap.add_argument("--check", action="store_true",
                    help="exit 0 only if rebuilding reproduces the committed file byte for byte")
    args = ap.parse_args(argv)
    if args.source:
        with open(args.source, "rb") as f:
            raw = f.read()
    else:
        with urllib.request.urlopen(SOURCE_URL, timeout=120) as r:
            raw = r.read()
    built = build(raw)
    if args.check:
        try:
            with open(PATH, "rb") as f:
                same = f.read() == built
        except FileNotFoundError:
            same = False
        print("reproduced: identical" if same else f"DIFFERS from {PATH}")
        return 0 if same else 1
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "wb") as f:
        f.write(built)
    print(f"wrote {PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
