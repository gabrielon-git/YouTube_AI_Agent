"""Tiny helper that lets the render job report live progress to the app.

The job keeps one GitHub *draft release* per video. Its body holds the progress
("progress: 45 | Generating images 3/8"), so the app can show a live bar. When the
video is done the file is attached and the release is published, which is how the
app finds it for playing and downloading.

Every call is best-effort: a network hiccup here must never break a render.

CLI (used by the workflow):
    python release_tools.py create "<topic>"   # prints the release id
    python release_tools.py publish
    python release_tools.py fail "<message>"
"""
import os
import re
import sys

import requests

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
UPLOADS = os.environ.get("GITHUB_UPLOADS_URL", "https://uploads.github.com")


def _repo():
    return os.environ.get("GITHUB_REPOSITORY", "")


def _headers():
    return {
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
    }


def _release_url():
    return f"{API}/repos/{_repo()}/releases/{os.environ.get('RELEASE_ID', '')}"


def _patch(payload):
    if not os.environ.get("RELEASE_ID"):
        return False
    try:
        r = requests.patch(_release_url(), headers=_headers(), json=payload, timeout=20)
        return r.status_code == 200
    except Exception as e:  # never break the render over a progress update
        print(f"(progress update skipped: {e})")
        return False


def _run_line():
    return f"\nrun: {os.environ.get('GITHUB_RUN_ID', '0')}"


def update(pct, label, name=None):
    payload = {"body": f"progress: {int(pct)} | {label}{_run_line()}"}
    if name:
        payload["name"] = name[:120]
    return _patch(payload)


def create(topic):
    topic = (topic or "").strip() or "Autonomous video"
    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    payload = {
        "tag_name": f"video-{run_id}",
        "name": topic[:120],
        "body": f"progress: 5 | Setting up\nrun: {run_id}",
        "draft": True,
        "prerelease": False,
    }
    r = requests.post(f"{API}/repos/{_repo()}/releases", headers=_headers(), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()["id"]


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "video"


def publish(path="final_video.mp4"):
    if not os.environ.get("RELEASE_ID"):
        sys.exit("No RELEASE_ID: the progress release was not created, so the video cannot be published. "
                 "The video is still in this run's Artifacts.")
    r = requests.get(_release_url(), headers=_headers(), timeout=20)
    r.raise_for_status()
    title = r.json().get("name") or "video"
    update(95, "Uploading video")
    asset_name = f"{_slug(title)}.mp4"
    with open(path, "rb") as f:
        up = requests.post(
            f"{UPLOADS}/repos/{_repo()}/releases/{os.environ['RELEASE_ID']}/assets",
            params={"name": asset_name},
            headers={**_headers(), "Content-Type": "video/mp4"},
            data=f,
            timeout=600,
        )
    up.raise_for_status()
    done = requests.patch(_release_url(), headers=_headers(),
                          json={"draft": False, "tag_name": f"video-{os.environ.get('GITHUB_RUN_ID', '0')}",
                                "body": "done" + _run_line()}, timeout=20)
    done.raise_for_status()


def fail(message):
    msg = " ".join((message or "Render failed").split())[:200]
    _patch({"body": f"failed | {msg}{_run_line()}"})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if cmd == "create":
        print(create(arg))
    elif cmd == "publish":
        publish()
    elif cmd == "fail":
        fail(arg or "Render failed - check the run log")
    else:
        sys.exit("usage: release_tools.py create|publish|fail")
