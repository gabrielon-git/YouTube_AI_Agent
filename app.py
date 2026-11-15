import hashlib
import hmac
import io
import os
import secrets
import time
from datetime import timedelta
from functools import wraps

import requests
from flask import (Flask, Response, redirect, render_template_string, request,
                   send_file, session, url_for)
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
    if request.endpoint in ("home", "login"):
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
  <div class="foot">
    Rendering takes about 5-8 minutes. Download the result from the
    {% if repo %}<a href="https://github.com/{{ repo }}/actions" target="_blank" rel="noopener">Actions tab</a>{% else %}Actions tab{% endif %}.
    <br>iPhone: Share &rarr; Add to Home Screen.
    <form method="POST" action="/logout" style="margin-top:14px">
      <input type="hidden" name="csrf" value="{{ csrf_token() }}">
      <button class="ghost" type="submit" style="font-size:14px;padding:10px">Log out</button>
    </form>
  </div>
</div>
<script>
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
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
