"""Remembers the last synced state of a folder pair (used by two-way sync to
tell a deletion apart from a new file). Stored locally, never uploaded."""

from __future__ import annotations

import hashlib
import json
import os

from .log import state_dir


def _path(a: str, b: str) -> str:
    key = hashlib.sha256("\n".join(sorted([a, b])).encode()).hexdigest()[:32]
    d = os.path.join(state_dir(), "pairs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, key + ".json")


def load(left_uri: str, right_uri: str) -> dict:
    try:
        with open(_path(left_uri, right_uri), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    entries = data.get("entries", {})
    if data.get("left") == right_uri and data.get("right") == left_uri:
        # stored with the sides swapped
        for rec in entries.values():
            if "lm" in rec:
                rec["lm"], rec["rm"] = rec["rm"], rec["lm"]
    return entries


def save(left_uri: str, right_uri: str, entries: dict) -> None:
    path = _path(left_uri, right_uri)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "left": left_uri, "right": right_uri, "entries": entries}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
