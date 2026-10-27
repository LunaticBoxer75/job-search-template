"""Fetch the jobs database from the history-free `jobs-data` branch.

`main` no longer carries data/jobs_deploy.db (see scripts/publish_jobs_db.py).
On Streamlit Cloud the file is absent at boot, so the dashboard downloads it
here, then re-checks the branch every few minutes and swaps in a new copy when
/find-jobs publishes one — no redeploy needed.

A locally exported DB is never overwritten: a download writes a marker file
recording which published commit it came from, and only a file with that marker
is refreshed. export_deploy_db.py removes the marker when it writes a new DB.
"""

import os
import tempfile
import threading
from pathlib import Path

from . import user_store

try:
    import requests
except ImportError:
    requests = None

DATA = Path(__file__).parent.parent / "data"
DB = DATA / "jobs_deploy.db"
MARKER = DATA / ".jobs_deploy.sha"  # which jobs-data commit the local file came from
BRANCH = os.environ.get("MSCM_JOBS_BRANCH", "jobs-data")
_lock = threading.Lock()  # sessions share a process; only one downloads at a time


class JobsDataError(RuntimeError):
    pass


def _headers(raw=False):
    h = {"Authorization": f"Bearer {user_store._token()}"}
    h["Accept"] = "application/vnd.github.raw" if raw else "application/vnd.github+json"
    return h


def published_sha():
    """Head commit of the jobs-data branch, or None if unavailable."""
    if not user_store.is_remote():
        return None
    try:
        r = requests.get(
            f"https://api.github.com/repos/{user_store.REPO}/git/ref/heads/{BRANCH}",
            headers=_headers(), timeout=10,
        )
    except requests.RequestException:
        return None
    return r.json()["object"]["sha"] if r.ok else None


def _download(sha):
    url = f"https://api.github.com/repos/{user_store.REPO}/contents/data/jobs_deploy.db"
    try:
        with requests.get(url, headers=_headers(raw=True), params={"ref": sha},
                          timeout=120, stream=True) as r:
            if not r.ok:
                raise JobsDataError(f"Download failed: HTTP {r.status_code}")
            fd, tmp = tempfile.mkstemp(dir=DATA, suffix=".db.tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            except BaseException:
                os.unlink(tmp)  # don't leave a partial download behind
                raise
    except requests.RequestException as e:
        raise JobsDataError(f"Download failed: {type(e).__name__}") from e
    os.replace(tmp, DB)  # atomic swap; readers never see a half-written file
    MARKER.write_text(sha)


def ensure(sha=None):
    """Make sure a jobs DB is present and current. Returns True if it changed.

    `sha` is the published head (pass a cached value to avoid an API call).
    """
    with _lock:
        return _ensure(sha)


def _ensure(sha):
    local_is_download = MARKER.exists()
    if DB.exists() and not local_is_download:
        return False  # locally exported DB — authoritative on this machine
    sha = sha or published_sha()
    if sha is None:
        if DB.exists():
            return False  # can't check; keep serving what we have
        raise JobsDataError(
            "No jobs database: data/jobs_deploy.db is missing and the jobs-data branch "
            "couldn't be reached (is MSCM_GITHUB_TOKEN set?)."
        )
    if DB.exists() and MARKER.read_text().strip() == sha:
        return False
    DATA.mkdir(parents=True, exist_ok=True)
    _download(sha)
    return True
