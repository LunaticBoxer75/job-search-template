"""Durable store for the candidate's own data (not scraped jobs).

Why this exists: the dashboard runs on Streamlit Cloud, whose filesystem is
ephemeral. Her data used to live in `data/jobs_deploy.db`, a git-committed file
that every `/find-jobs` push replaced — so anything she typed was destroyed on
the next data refresh.

The source of truth is `data/user_data.json` on a dedicated **`user-data`
branch**, never `main`. Two reasons: Streamlit Cloud reloads the app on every
commit to the branch it deploys from, so saving to `main` restarted the app on
every click; and keeping her data off `main` means a `/find-jobs` push can never
conflict with — or clobber — what she typed.

Modes
-----
remote : read/write the `user-data` branch through the GitHub contents API.
         Used whenever an MSCM_GITHUB_TOKEN is configured — on Streamlit Cloud
         (app secrets) AND locally (.streamlit/secrets.toml), so local scripts
         see her real data. Deliberately not named GITHUB_TOKEN: gh, CI, and
         other tools set that, and it would silently turn local scripts into
         things that commit to GitHub.
file   : no token — read/write the local `data/user_data.json` (gitignored on
         main). Only a snapshot; fine offline, not authoritative.

Write model
-----------
Every write is read-modify-write against the *latest* copy: fetch, apply the
change, write back with that copy's SHA. On a SHA conflict (someone else wrote
in between) it refetches and re-applies the change, so neither writer's update
is lost. The in-process cache only changes after a write succeeds.

Collections are flat lists of dicts, each with an integer `id`.
"""

import base64
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from . import profile as _profile

try:
    import requests
except ImportError:  # requests ships with streamlit, but keep local CLI usable
    requests = None

PATH = Path(__file__).parent.parent / "data" / "user_data.json"
# owner/name of the GitHub repo that holds the user-data and jobs-data branches
REPO = os.environ.get("MSCM_GITHUB_REPO") or _profile.identity()["github_repo"]
BRANCH = os.environ.get("MSCM_GITHUB_BRANCH", "user-data")  # never main — see docstring
API = f"https://api.github.com/repos/{REPO}/contents/data/user_data.json"

COLLECTIONS = [
    "applications",
    "coffee_chats",
    "todos",
    "interview_recs",
    "star_stories",
    "mock_sessions",
    "prep_notes",
    "dismissed_jobs",
    "meta",            # small key/value records, e.g. her last-visit dates
]

# Optional callback(message) run after every successful write. The dashboard uses
# it to confirm saves; scripts leave it None.
on_write = None

CACHE_TTL = 60        # seconds before a remote read is considered stale
RETRY_AFTER = 30      # seconds to wait before retrying GitHub after a failed read
MAX_CONFLICTS = 3

_lock = threading.RLock()
_cache = None
_cache_at = 0.0
_degraded_until = 0.0  # while > now, serve the last-good/committed copy without refetching


class StoreError(RuntimeError):
    """A read or write that must not be silently papered over."""


def _empty():
    data = {"version": 1, "updated_at": None}
    for c in COLLECTIONS:
        data[c] = []
    return data


def _normalize(data):
    for c in COLLECTIONS:
        data.setdefault(c, [])
    return data


def _now():
    return datetime.now().isoformat()


def _token():
    """Store token from env or Streamlit secrets. None means local mode."""
    tok = os.environ.get("MSCM_GITHUB_TOKEN")
    if tok:
        return tok
    try:
        import streamlit as st

        return st.secrets.get("MSCM_GITHUB_TOKEN")
    except Exception:
        return None


def is_remote():
    # MSCM_STORE_MODE=file forces the local snapshot even when a token is
    # configured — tests and smoke checks must never write to GitHub.
    if os.environ.get("MSCM_STORE_MODE") == "file":
        return False
    return bool(_token()) and requests is not None


def is_degraded():
    """True while GitHub is unreachable and reads come from the committed copy."""
    return time.time() < _degraded_until


def _headers():
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
    }


# ---------- raw I/O ----------

def _read_local():
    if not PATH.exists():
        return _empty()
    try:
        return _normalize(json.loads(PATH.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        # Never treat an unreadable file as empty: the next write would replace
        # her entire history with a single record. The usual cause is an
        # unresolved git merge conflict (`<<<<<<<` markers).
        raise StoreError(
            f"{PATH.name} is unreadable ({e}). Refusing to continue — resolve it "
            "(likely an unfinished merge) before writing."
        ) from e


def _write_local(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(PATH)  # atomic: a crash mid-write can't leave a half-written file


def _read_remote():
    """Fetch (data, sha) from GitHub. Raises StoreError on failure."""
    try:
        r = requests.get(API, headers=_headers(), params={"ref": BRANCH}, timeout=10)
    except requests.RequestException as e:
        raise StoreError(f"GitHub unreachable: {type(e).__name__}") from e
    if r.status_code == 404:
        return _empty(), None  # file not created on this branch yet
    if not r.ok:
        raise StoreError(f"GitHub read failed: HTTP {r.status_code}")
    payload = r.json()
    try:
        data = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
    except (KeyError, ValueError) as e:
        raise StoreError("user_data.json on GitHub is not valid JSON") from e
    return _normalize(data), payload["sha"]


def _put_remote(data, sha, message):
    """Write to GitHub. Returns True on success, False on a SHA conflict."""
    body = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(data, indent=2, ensure_ascii=False).encode()
        ).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(API, headers=_headers(), json=body, timeout=15)
    except requests.RequestException as e:
        raise StoreError(f"GitHub unreachable: {type(e).__name__}") from e
    if r.status_code in (409, 422):
        return False  # file moved since we read it
    if not r.ok:
        raise StoreError(f"GitHub write failed: HTTP {r.status_code}")
    return True


# ---------- public API ----------

def load(force=False):
    """Return the whole store (cached). `force` refetches."""
    global _cache, _cache_at, _degraded_until
    with _lock:
        if not is_remote():
            if _cache is None or force:
                _cache = _read_local()
            return _cache

        fresh = _cache is not None and time.time() - _cache_at < CACHE_TTL
        if not force and (fresh or (_cache is not None and is_degraded())):
            return _cache
        try:
            data, _ = _read_remote()
            _cache, _cache_at, _degraded_until = data, time.time(), 0.0
        except StoreError:
            # Serve the last good copy (or the committed file) read-only for a
            # short while, then try GitHub again — never cache this permanently.
            _degraded_until = time.time() + RETRY_AFTER
            if _cache is None:
                _cache = _read_local()
                _cache_at = 0.0
        return _cache


def _mutate(fn, message):
    """Apply `fn(data) -> (result, changed)` to the latest copy and persist it."""
    global _cache, _cache_at, _degraded_until
    with _lock:
        for _ in range(MAX_CONFLICTS):
            if is_remote():
                data, sha = _read_remote()  # always fresh: avoids stale-SHA writes
            else:
                data, sha = _read_local(), None
            result, changed = fn(data)
            if not changed:
                return result
            data["updated_at"] = _now()
            if is_remote():
                if not _put_remote(data, sha, message):
                    continue  # someone else wrote; re-apply on their version
                try:
                    _write_local(data)  # best-effort mirror; container may be read-only
                except OSError:
                    pass
            else:
                _write_local(data)
            _cache, _cache_at, _degraded_until = data, time.time(), 0.0
            if on_write:
                try:
                    on_write(message)
                except Exception:
                    pass  # a confirmation must never break a successful save
            return result
        raise StoreError(f"Gave up after {MAX_CONFLICTS} write conflicts — try again.")


def save(data, message="Replace user data"):
    """Replace the whole store. Used by the one-time migration."""
    snapshot = _normalize(json.loads(json.dumps(data)))

    def op(d):
        d.clear()
        d.update(snapshot)
        return None, True

    _mutate(op, message)


def _next_id(rows):
    return max((r.get("id", 0) for r in rows), default=0) + 1


def add(collection, record, message=None):
    """Append a record, assigning an id. Returns the stored record."""
    return add_many(collection, [record], message)[0]


def add_unique(collection, record, unique_on, message=None):
    """Append unless a record with the same `unique_on` value exists. Returns it or None."""
    out = add_many(collection, [record], message, unique_on=unique_on)
    return out[0] if out else None


def add_many(collection, records, message=None, unique_on=None):
    """Append several records in ONE write (one commit, not one per record).

    `unique_on`: field name; records whose value already exists are skipped.
    The check runs against the latest copy inside the write, so it's race-free,
    and if nothing is new no commit is made at all.
    """
    if not records:
        return []

    def op(d):
        rows = d.setdefault(collection, [])
        seen = {r.get(unique_on) for r in rows} if unique_on else set()
        out = []
        for rec in records:
            if unique_on:
                if rec.get(unique_on) in seen:
                    continue
                seen.add(rec.get(unique_on))
            rec = dict(rec)
            rec["id"] = _next_id(rows)
            rec.setdefault("created_at", _now())
            rec.setdefault("updated_at", rec["created_at"])
            rows.append(rec)
            out.append(rec)
        return out, bool(out)

    return _mutate(op, message or f"Add {len(records)} to {collection}")


def get(collection):
    return list(load().get(collection, []))


def update(collection, record_id, fields, message=None):
    def op(d):
        for row in d.get(collection, []):
            if row.get("id") == record_id:
                row.update(fields)
                row["updated_at"] = _now()
                return dict(row), True
        return None, False

    return _mutate(op, message or f"Update {collection} #{record_id}")


def delete(collection, record_id, message=None):
    def op(d):
        rows = d.get(collection, [])
        remaining = [r for r in rows if r.get("id") != record_id]
        if len(remaining) == len(rows):
            return False, False
        d[collection] = remaining
        return True, True

    return _mutate(op, message or f"Delete {collection} #{record_id}")


def stats():
    data = load()
    return {c: len(data.get(c, [])) for c in COLLECTIONS}
