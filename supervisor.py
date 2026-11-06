import os
import requests
import base64
from google import genai

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO = os.environ.get("GITHUB_REPOSITORY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def get_latest_workflow_run():
    url = f"https://api.github.com/repos/{REPO}/actions/runs?per_page=1"
    res = requests.get(url, headers=headers).json()
    runs = res.get("workflow_runs", [])
    return runs[0] if runs else None

def get_failed_log(run_id):
    url = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/logs"
    res = requests.get(url, headers=headers)
    return res.text[:4000]

def get_file_content(path="render_video.py"):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    res = requests.get(url, headers=headers).json()
    content = base64.b64decode(res["content"]).decode("utf-8")
    return content, res["sha"]

def commit_fix(path, new_code, sha, commit_msg):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    encoded = base64.b64encode(new_code.encode("utf-8")).decode("utf-8")
    payload = {
        "message": commit_msg,
        "content": encoded,
        "sha": sha
    }
    res = requests.put(url, headers=headers, json=payload)
    return res.status_code == 200

def generate_ai_fix(error_log, current_code):
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"""
    The following Python script (render_video.py) failed in a GitHub Actions workflow:

    --- CURRENT SCRIPT ---
    {current_code}

    --- ERROR LOG ---
    {error_log}

    Fix the script so the error is resolved. 
    CRITICAL: Output ONLY valid executable Python code. Do not include markdown codeblocks or explanation.
    """
    response = client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
    clean_code = response.text.replace("```python", "").replace("```", "").strip()
    return clean_code

def monitor_and_heal():
    print("Monitoring workflow runs...")
    run = get_latest_workflow_run()
    if not run:
        return

    run_id = run["id"]
    conclusion = run["conclusion"]

    if conclusion == "failure":
        print("Failure detected! Fetching logs...")
        logs = get_failed_log(run_id)
        current_code, sha = get_file_content("render_video.py")

        print("Asking Gemini to diagnose and patch render_video.py...")
        patched_code = generate_ai_fix(logs, current_code)

        print("Committing automated patch to repository...")
        success = commit_fix("render_video.py", patched_code, sha, "auto-fix: supervisor self-healing patch")

        if success:
            print("Patch applied successfully! Re-triggering rendering workflow...")
            url = f"https://api.github.com/repos/{REPO}/actions/workflows/render.yml/dispatches"
            requests.post(url, headers=headers, json={"ref": "main"})

if __name__ == "__main__":
    monitor_and_heal()
