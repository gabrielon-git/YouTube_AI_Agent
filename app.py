import hashlib
import hmac
import io
import os
import secrets
import time
from datetime import timedelta
from functools import wraps

import requests
import re

from flask import (Flask, Response, jsonify, redirect, render_template_string, request,
                   send_file, session, stream_with_context, url_for)
from PIL import Image, ImageDraw

# ----------------------------------------------------------------------------
# Config (set these as Environment Variables on Render)
#   APP_PASSWORD         required - the password to open the app
#   SECRET_KEY           optional - long random string; derived from the password if missing
#   GITHUB_TOKEN, GITHUB_REPO, GITHUB_WORKFLOW_ID   as before
# ----------------------------------------------------------------------------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_WORKFLOW_ID = os.environ.get("GITHUB_WORKFLOW_ID")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(("studio:" + APP_PASSWORD).encode()).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("INSECURE_COOKIES") != "1",  # Render is https
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

VOICES = {
    "en-US-AriaNeural": "Aria (US Female)",
    "en-US-AndrewNeural": "Andrew (US Male)",
    "en-US-EmmaMultilingualNeural": "Emma (US Female)",
    "en-US-GuyNeural": "Guy (US Male)",
}
FORMATS = {"short": "Short-form (9:16, ~30s)", "long": "Long-form (16:9, ~90s)"}

MAX_FAILS = 5
LOCKOUT_SECONDS = 300
_fails = {}  # ip -> (count, first_fail_time)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[-1].strip() if forwarded else request.remote_addr) or "unknown"


def locked_out(ip):
    count, first = _fails.get(ip, (0, 0))
    if count >= MAX_FAILS and time.time() - first < LOCKOUT_SECONDS:
        return True
    if count and time.time() - first >= LOCKOUT_SECONDS:
        _fails.pop(ip, None)
    return False


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def csrf_ok():
    sent = request.form.get("csrf", "")
    return bool(sent) and hmac.compare_digest(sent, session.get("csrf", ""))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


app.jinja_env.globals["csrf_token"] = csrf_token


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.endpoint in ("home", "login", "api_status"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ----------------------------------------------------------------------------
# PWA assets (public: the browser fetches these without a login cookie)
# ----------------------------------------------------------------------------
_icon_cache = {}


def make_icon(size):
    if size in _icon_cache:
        return _icon_cache[size]
    top, bottom = (30, 27, 75), (124, 58, 237)
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):  # full-bleed gradient so the icon also works as a maskable icon
        t = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    c, r = size / 2, size * 0.2
    d.ellipse([c - size * 0.3, c - size * 0.3, c + size * 0.3, c + size * 0.3],
              outline="white", width=max(2, size // 36))
    d.polygon([(c - r * 0.55, c - r), (c - r * 0.55, c + r), (c + r * 1.0, c)], fill="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    _icon_cache[size] = buf.getvalue()
    return _icon_cache[size]


@app.route("/icon-<int:size>.png")
def icon(size):
    if size not in (180, 192, 512):
        return Response(status=404)
    resp = Response(make_icon(size), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/manifest.webmanifest")
def manifest():
    data = {
        "name": "YouTube AI Studio",
        "short_name": "AI Studio",
        "description": "One-tap AI video generator",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0f0f1a",
        "theme_color": "#0f0f1a",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    import json
    return Response(json.dumps(data), mimetype="application/manifest+json")


SERVICE_WORKER = """
const OFFLINE = '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<body style="font-family:sans-serif;background:#0f0f1a;color:#fff;display:flex;min-height:100vh;' +
  'align-items:center;justify-content:center;text-align:center"><div><h2>You are offline</h2>' +
  '<p>Reconnect to start a video.</p></div></body>';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, {headers: {'Content-Type': 'text/html'}})));
  }
});
"""


@app.route("/sw.js")
def service_worker():
    resp = Response(SERVICE_WORKER, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ----------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------
BASE_HEAD = """
<meta charset="utf-8">
<title>AI Studio</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-180.png">
<meta name="theme-color" content="#0f0f1a">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="AI Studio">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: #0f0f1a; color: #f2f2f7;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
         padding: env(safe-area-inset-top) 16px env(safe-area-inset-bottom); }
  .wrap { max-width: 480px; margin: 0 auto; padding: 28px 0 40px; }
  h1 { font-size: 26px; margin: 0 0 4px; }
  .sub { color: #9a9ab0; margin: 0 0 22px; }
  .card { background: #1a1a2e; border: 1px solid #2a2a45; border-radius: 16px; padding: 20px; }
  label { display: block; font-size: 13px; color: #9a9ab0; margin: 0 0 6px; }
  input, select, button { width: 100%; font-size: 17px; border-radius: 12px; border: 1px solid #34345a;
         background: #11111f; color: #f2f2f7; padding: 14px; margin-bottom: 16px; -webkit-appearance: none; appearance: none; }
  button { background: linear-gradient(135deg, #7c3aed, #4f46e5); border: 0; font-weight: 700; cursor: pointer; margin-bottom: 0; }
  button:disabled { opacity: .6; }
  button.ghost { background: transparent; border: 1px solid #34345a; color: #9a9ab0; font-weight: 500; margin-top: 12px; }
  .msg { padding: 12px 14px; border-radius: 12px; margin-bottom: 16px; font-size: 15px; word-break: break-word; }
  .ok { background: #12301f; color: #6ee7a0; } .err { background: #3a1620; color: #ff9aa9; }
  .foot { color: #6f6f88; font-size: 13px; text-align: center; margin-top: 22px; line-height: 1.5; }
  .foot a { color: #9b8cff; }
  .lib-h { font-size: 18px; margin: 26px 0 10px; }
  .vid { background: #1a1a2e; border: 1px solid #2a2a45; border-radius: 16px; padding: 14px; margin-bottom: 12px; }
  .vid .t { font-weight: 600; margin: 0 0 4px; word-break: break-word; }
  .vid .m { color: #9a9ab0; font-size: 13px; margin: 0 0 10px; }
  .vid .m.bad { color: #ff9aa9; }
  .bar { height: 8px; background: #11111f; border-radius: 6px; overflow: hidden; }
  .fill { height: 100%; background: linear-gradient(90deg, #7c3aed, #4f46e5); transition: width .6s ease; }
  .vid video { width: 100%; max-height: 60vh; border-radius: 12px; background: #000; margin-bottom: 10px; }
  .dl { display: block; text-align: center; text-decoration: none; background: linear-gradient(135deg, #7c3aed, #4f46e5);
        color: #fff; font-weight: 700; padding: 13px; border-radius: 12px; }
  button.del { background: transparent; border: 1px solid #5a2a3a; color: #ff9aa9; font-size: 14px;
               font-weight: 500; padding: 10px; margin-top: 10px; }
  .vid a.run { color: #9b8cff; font-size: 13px; }
</style>
"""

LOGIN_PAGE = """<!doctype html><html lang="en"><head>""" + BASE_HEAD + """</head><body><div class="wrap">
  <h1>AI Studio</h1><p class="sub">Enter your password to continue</p>
  <div class="card">
    {% if error %}<div class="msg err">{{ error }}</div>{% endif %}
    <form method="POST" autocomplete="on">
      <input type="hidden" name="csrf" value="{{ csrf_token() }}">
      <label for="pw">Password</label>
      <input id="pw" type="password" name="password" autocomplete="current-password" required autofocus>
      <button type="submit">Unlock</button>
    </form>
  </div>
</div></body></html>"""

HOME_PAGE = """<!doctype html><html lang="en"><head>""" + BASE_HEAD + """</head><body><div class="wrap">
  <h1>YouTube AI Studio</h1><p class="sub">One-tap video generator</p>
  <div class="card">
    {% if message %}<div class="msg {{ 'ok' if ok else 'err' }}">{{ message }}</div>{% endif %}
    <form method="POST" id="gen">
      <input type="hidden" name="csrf" value="{{ csrf_token() }}">
      <label for="topic">Video topic</label>
      <input id="topic" type="text" name="topic" maxlength="200" placeholder="e.g. Psychology of cozy spaces" required>
      <label for="fmt">Format</label>
      <select id="fmt" name="video_format">
        {% for key, label in formats.items() %}<option value="{{ key }}">{{ label }}</option>{% endfor %}
      </select>
      <label for="voice">Voice</label>
      <select id="voice" name="voice_id">
        {% for key, label in voices.items() %}<option value="{{ key }}">{{ label }}</option>{% endfor %}
      </select>
      <button type="submit" id="go">Generate video</button>
    </form>
    <button class="ghost" id="install" type="button" hidden>Install app</button>
  </div>
  <h2 class="lib-h">Your videos</h2>
  <div id="lib"><div class="foot">Loading...</div></div>
  <div class="foot">
    Rendering takes about 5-8 minutes. Progress and downloads show up above.
    <br>iPhone: Share &rarr; Add to Home Screen.
    <form method="POST" action="/logout" style="margin-top:14px">
      <input type="hidden" name="csrf" value="{{ csrf_token() }}">
      <button class="ghost" type="submit" style="font-size:14px;padding:10px">Log out</button>
    </form>
  </div>
</div>
<script>
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  const JUST_STARTED = {{ 'true' if ok else 'false' }};
  const pageLoaded = Date.now();
  const CSRF = '{{ csrf_token() }}';
  const hidden = new Set();
  const lib = document.getElementById('lib');
  let timer = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function ago(iso) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' d ago';
  }
  function delBtn(v, c) {
    const b = el('button', 'del', 'Delete');
    b.type = 'button';
    b.addEventListener('click', async () => {
      if (v.state === 'done' && !confirm('Delete this video? This cannot be undone.')) return;
      b.disabled = true; b.textContent = 'Deleting...';
      const f = new FormData();
      f.append('csrf', CSRF); f.append('run_id', v.run_id || ''); f.append('release_id', v.release_id || '');
      try {
        const r = await fetch('/api/delete', {method: 'POST', body: f});
        const d = await r.json();
        if (d.ok) { hidden.add(String(v.id)); c.remove(); poll(); return; }
        alert(d.error || 'Delete failed');
      } catch (e) { alert('Connection problem - try again'); }
      b.disabled = false; b.textContent = 'Delete';
    });
    c.appendChild(b);
  }
  function card(v) {
    const c = el('div', 'vid');
    c.dataset.id = v.id; c.dataset.state = v.state;
    c.appendChild(el('p', 't', v.title));
    if (v.state === 'done') {
      c.appendChild(el('p', 'm', 'Ready - ' + ago(v.created) + ' - ' + (v.video.size / 1048576).toFixed(1) + ' MB'));
      const vid = document.createElement('video');
      vid.controls = true; vid.playsInline = true; vid.preload = 'metadata';
      vid.src = '/file/' + v.video.id + '#t=0.1';
      c.appendChild(vid);
      const a = el('a', 'dl', 'Download video');
      a.href = '/file/' + v.video.id + '?dl=1';
      c.appendChild(a);
      delBtn(v, c);
    } else if (v.state === 'failed') {
      c.appendChild(el('p', 'm bad', v.label));
      const a = el('a', 'run', 'Open run on GitHub');
      a.href = v.run_url; a.target = '_blank'; a.rel = 'noopener';
      c.appendChild(a);
      delBtn(v, c);
    } else {
      const pct = v.pct || 0;
      c.appendChild(el('p', 'm', v.label + ' - ' + Math.round(pct) + '%'));
      const bar = el('div', 'bar'), fill = el('div', 'fill');
      fill.style.width = Math.max(pct, 3) + '%';
      bar.appendChild(fill); c.appendChild(bar);
    }
    return c;
  }
  function draw(all) {
    const items = all.filter(v => !hidden.has(String(v.id)));
    // keep an already-playing video untouched so polling never interrupts it
    // reuse finished cards as-is so videos never reload or stutter while polling
    const keep = {};
    lib.querySelectorAll('.vid').forEach(n => { if (n.dataset.state === 'done') keep[n.dataset.id] = n; });
    const nodes = [];
    items.forEach(v => nodes.push(v.state === 'done' && keep[v.id] ? keep[v.id] : card(v)));
    if (JUST_STARTED && Date.now() - pageLoaded < 60000 && !items.some(v => v.state === 'queued' || v.state === 'running')
        && !(items[0] && Date.now() - new Date(items[0].created).getTime() < 90000)) {
      const ph = card({id: 'new', title: 'Starting...', state: 'queued', pct: 0, label: 'Sending to GitHub'});
      nodes.unshift(ph);
    }
    lib.replaceChildren.apply(lib, nodes.length ? nodes : [el('div', 'foot', 'No videos yet - make your first one above.')]);
  }
  async function poll() {
    clearTimeout(timer);
    let active = false;
    try {
      const r = await fetch('/api/status', {cache: 'no-store'});
      if (r.status === 401) { location.href = '/login'; return; }
      const d = await r.json();
      if (d.error) { lib.replaceChildren(el('div', 'msg err', d.error)); }
      else { draw(d.items); active = d.active || (JUST_STARTED && Date.now() - pageLoaded < 60000); }
    } catch (e) {
      lib.replaceChildren(el('div', 'foot', 'Connection problem - retrying...'));
      active = true;
    }
    timer = setTimeout(poll, active ? 4000 : 20000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  poll();
  document.getElementById('gen').addEventListener('submit', () => {
    const b = document.getElementById('go'); b.disabled = true; b.textContent = 'Starting...';
  });
  let deferred = null;
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault(); deferred = e; document.getElementById('install').hidden = false;
  });
  document.getElementById('install').addEventListener('click', async () => {
    if (!deferred) return; deferred.prompt(); await deferred.userChoice; deferred = null;
    document.getElementById('install').hidden = true;
  });
</script></body></html>"""



# ----------------------------------------------------------------------------
# Video library: live status + play/download, all read from GitHub
# ----------------------------------------------------------------------------
def gh_headers(accept="application/vnd.github+json"):
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": accept}


def gh_get(path, **params):
    r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}{path}", headers=gh_headers(),
                     params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def parse_body(body):
    """Release body is 'progress: 45 | label', 'failed | message' or 'done'."""
    body = ((body or "").strip().split("\n")[0]).strip()
    m = re.match(r"progress:\s*(\d+)\s*\|\s*(.*)", body)
    if m:
        return min(int(m.group(1)), 99), m.group(2).strip()
    if body.startswith("failed"):
        return None, body.split("|", 1)[-1].strip() if "|" in body else "Render failed"
    return None, ""


def release_run_id(rel):
    """Which workflow run made this release (run id is stamped in the body; tag is the fallback)."""
    m = re.search(r"run:\s*(\d+)", rel.get("body") or "") or re.match(r"video-(\d+)$", rel.get("tag_name") or "")
    return m.group(1) if m else None


def mp4_asset(rel):
    for a in rel.get("assets", []):
        if a["name"].endswith(".mp4"):
            return {"id": a["id"], "name": a["name"], "size": a["size"]}
    return None


@app.route("/api/status")
def api_status():
    if not session.get("auth"):
        return jsonify(error="login"), 401
    if not (GITHUB_TOKEN and GITHUB_REPO and GITHUB_WORKFLOW_ID):
        return jsonify(error="Server is missing GITHUB_TOKEN, GITHUB_REPO or GITHUB_WORKFLOW_ID."), 500
    try:
        runs = gh_get(f"/actions/workflows/{GITHUB_WORKFLOW_ID}/runs", per_page=15)["workflow_runs"]
        releases = gh_get("/releases", per_page=30)
    except requests.HTTPError as e:
        return jsonify(error=f"GitHub error {e.response.status_code}. The token may need Contents (read) access."), 502
    except Exception as e:
        return jsonify(error=f"Could not reach GitHub: {e}"), 502

    by_run = {}
    for r in releases:
        rid = release_run_id(r)
        if rid:
            by_run[rid] = r
    items = []
    for run in runs:
        rel = by_run.get(str(run["id"]))
        pct, label = parse_body(rel["body"]) if rel else (None, "")
        video = None
        if rel and not rel.get("draft"):
            video = mp4_asset(rel)

        if video:
            state = "done"
        elif run["status"] == "completed":
            state = "failed"
            label = label or ("Finished, but the video upload failed - check the run on GitHub"
                              if run["conclusion"] == "success" else "Render failed")
        elif run["status"] == "in_progress":
            state = "running"
            pct = pct if pct is not None else 3
            label = label or "Starting"
        else:
            state, pct, label = "queued", 0, "Waiting for GitHub to start"

        items.append({
            "id": run["id"],
            "title": (rel["name"] if rel else run.get("display_title")) or "Video",
            "state": state,
            "pct": pct,
            "label": label,
            "created": run["created_at"],
            "video": video,
            "run_url": run["html_url"],
            "run_id": run["id"],
            "release_id": rel["id"] if rel else None,
        })
    # finished videos whose run is no longer in the recent list (or that could not be matched)
    seen = {str(i["id"]) for i in items}
    for r in releases:
        v = None if r.get("draft") else mp4_asset(r)
        if v and release_run_id(r) not in seen:
            items.append({"id": f"rel{r['id']}", "title": r.get("name") or "Video", "state": "done", "pct": None,
                          "label": "", "created": r.get("published_at") or r["created_at"],
                          "video": v, "run_url": r["html_url"], "run_id": None, "release_id": r["id"]})
    items.sort(key=lambda i: i["created"], reverse=True)
    return jsonify(items=items, active=any(i["state"] in ("queued", "running") for i in items))


@app.route("/api/delete", methods=["POST"])
def api_delete():
    """Delete a video: its release (file + tag) and its workflow run. Needs a token with write access."""
    if not session.get("auth"):
        return jsonify(error="login"), 401
    if not csrf_ok():
        return jsonify(error="Session expired - reload the app and try again."), 403
    run_id = request.form.get("run_id", "").strip()
    release_id = request.form.get("release_id", "").strip()
    if not (run_id or release_id) or (run_id and not run_id.isdigit()) or (release_id and not release_id.isdigit()):
        return jsonify(error="Nothing to delete."), 400
    base = f"https://api.github.com/repos/{GITHUB_REPO}"
    try:
        if release_id:
            rel = requests.get(f"{base}/releases/{release_id}", headers=gh_headers(), timeout=15)
            tag = rel.json().get("tag_name", "") if rel.status_code == 200 else ""
            r = requests.delete(f"{base}/releases/{release_id}", headers=gh_headers(), timeout=15)
            if r.status_code not in (204, 404):
                return jsonify(error=f"Could not delete the video (GitHub {r.status_code}). "
                                     "The token needs Contents: Read and write."), 502
            if tag.startswith(("video-", "untagged-")):  # clean up the leftover tag too
                requests.delete(f"{base}/git/refs/tags/{tag}", headers=gh_headers(), timeout=15)
        if run_id:
            r = requests.delete(f"{base}/actions/runs/{run_id}", headers=gh_headers(), timeout=15)
            if r.status_code == 409:
                return jsonify(error="That render is still running - wait for it to finish."), 409
            if r.status_code not in (204, 404):
                return jsonify(error=f"Video removed, but its run could not be (GitHub {r.status_code})."), 502
    except Exception as e:
        return jsonify(error=f"Could not reach GitHub: {e}"), 502
    return jsonify(ok=True)


@app.route("/file/<int:asset_id>")
@login_required
def video_file(asset_id):
    """Stream a release video through the app (supports seeking, optional ?dl=1 download)."""
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return "Server not configured", 500
    try:
        meta = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/assets/{asset_id}",
                            headers=gh_headers(), timeout=15)
        if meta.status_code != 200:
            return "Not found", 404
        name = re.sub(r"[^A-Za-z0-9._-]", "_", meta.json().get("name", "video.mp4"))
        headers = gh_headers("application/octet-stream")
        if request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]
        up = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/assets/{asset_id}",
                          headers=headers, stream=True, timeout=30)
    except Exception as e:
        return f"Could not reach GitHub: {e}", 502
    if up.status_code not in (200, 206):
        up.close()
        return "Video unavailable", 502

    out = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    for h in ("Content-Length", "Content-Range"):
        if h in up.headers:
            out[h] = up.headers[h]
    if request.args.get("dl"):
        out["Content-Disposition"] = f'attachment; filename="{name}"'
    resp = Response(stream_with_context(up.iter_content(65536)), status=up.status_code, headers=out)
    resp.call_on_close(up.close)
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("auth"):
        return redirect(url_for("home"))
    if not APP_PASSWORD:
        return render_template_string(
            LOGIN_PAGE, error="APP_PASSWORD is not set on the server. Add it in Render, then redeploy."), 503

    error = None
    if request.method == "POST":
        ip = client_ip()
        if not csrf_ok():
            error = "Session expired. Please try again."
        elif locked_out(ip):
            error = "Too many attempts. Try again in a few minutes."
        elif hmac.compare_digest(request.form.get("password", "").encode(), APP_PASSWORD.encode()):
            _fails.pop(ip, None)
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect(url_for("home"))
        else:
            count, first = _fails.get(ip, (0, time.time()))
            _fails[ip] = (count + 1, first)
            time.sleep(1)
            error = "Wrong password."
    return render_template_string(LOGIN_PAGE, error=error)


@app.route("/logout", methods=["POST"])
def logout():
    if csrf_ok():
        session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def home():
    message, ok = None, False

    if request.method == "POST":
        topic = request.form.get("topic", "").strip()[:200]
        video_format = request.form.get("video_format", "short")
        voice = request.form.get("voice_id", "en-US-AriaNeural")

        if not csrf_ok():
            message = "Session expired. Please try again."
        elif not topic:
            message = "Please enter a topic."
        elif video_format not in FORMATS or voice not in VOICES:
            message = "Invalid format or voice."
        elif not (GITHUB_TOKEN and GITHUB_REPO and GITHUB_WORKFLOW_ID):
            message = "Server is missing GITHUB_TOKEN, GITHUB_REPO or GITHUB_WORKFLOW_ID."
        elif time.time() - session.get("last_run", 0) < 20:
            message = "Hold on a few seconds before starting another video."
        else:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW_ID}/dispatches"
            headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
            payload = {"ref": "main", "inputs": {"topic": topic, "format": video_format, "voice": voice}}
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=15)
                if r.status_code == 204:
                    session["last_run"] = time.time()
                    message, ok = f"Started: \"{topic}\". It will be ready in about 5-8 minutes.", True
                else:
                    message = f"GitHub error {r.status_code}: {r.text[:300]}"
            except Exception as e:
                message = f"Could not reach GitHub: {e}"

    return render_template_string(HOME_PAGE, message=message, ok=ok, formats=FORMATS, voices=VOICES,
                                  repo=GITHUB_REPO)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7070)))
