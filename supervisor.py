import os
import subprocess
import sys
import time

from google import genai

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
RUN_ID = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]
NO_CHANGE = "NO_CODE_CHANGE"

if not GEMINI_KEY:
    print("ERROR: Supervisor missing GEMINI_API_KEY!")
    sys.exit(0)


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def diagnose_and_patch():
    print("Supervisor activated: analyzing failure...")

    log_path = "build_error.log"
    if not os.path.exists(log_path):
        print("No build_error.log found, so there is nothing real to diagnose. Skipping.")
        return
    with open(log_path, "r", errors="replace") as f:
        error_log = f.read()[-6000:]  # the end of the log holds the traceback
    if not error_log.strip():
        print("Error log is empty. Skipping.")
        return

    with open("render_video.py", "r") as f:
        current_code = f.read()

    prompt = f"""You are a careful maintenance engineer for an automated video rendering pipeline.
Here is `render_video.py`:
```python
{current_code}
```

Here is the end of the log from the run that failed:
```
{error_log}
```

Rules:
- Make the SMALLEST change that fixes this exact error. Do not rewrite or restructure the file.
- Never add placeholder or fake content (canned scripts, silent audio, blank images) to hide a failure.
- Never change Gemini model names or library versions. Those are set deliberately.
- If the failure is caused by the environment (a package version, a network or API outage, a missing secret,
  a rate limit) and not by a bug in this file, reply with exactly: {NO_CHANGE}
- Otherwise reply with ONLY the complete corrected Python file, no markdown fences, no commentary."""

    response_text = None
    for model in MODELS:
        for attempt in range(3):
            try:
                client = genai.Client(api_key=GEMINI_KEY)
                response_text = client.models.generate_content(model=model, contents=prompt).text
                break
            except Exception as e:
                wait = 5 * (2 ** attempt)
                print(f"{model} unavailable ({e}). Retrying in {wait}s...")
                time.sleep(wait)
        if response_text:
            break

    if not response_text:
        print("Supervisor could not reach Gemini. Skipping patch for this run.")
        return

    fixed = response_text.strip()
    if fixed.startswith("```"):
        fixed = fixed.split("\n", 1)[1] if "\n" in fixed else ""
    if fixed.endswith("```"):
        fixed = fixed[:-3]
    fixed = fixed.strip()

    if fixed == NO_CHANGE or NO_CHANGE in fixed[:40]:
        print("Supervisor decided this is an environment problem, not a code bug. No code change proposed.")
        return

    try:
        compile(fixed, "render_video.py", "exec")
    except SyntaxError as e:
        print(f"Proposed fix is not valid Python ({e}). Discarding it.")
        return
    if "def build_video" not in fixed or fixed == current_code.strip():
        print("Proposed fix failed safety checks or changes nothing. Discarding it.")
        return

    branch = f"self-heal/run-{RUN_ID}"
    try:
        run("git", "config", "user.name", "AI Autonomous Agent")
        run("git", "config", "user.email", "agent@supervisor.local")
        run("git", "checkout", "-b", branch)
        with open("render_video.py", "w") as f:
            f.write(fixed + "\n")
        run("git", "add", "render_video.py")
        run("git", "commit", "-m", "Self-heal: proposed fix from supervisor (needs review)")
        run("git", "push", "origin", branch)
        print(f"Proposed fix pushed to branch '{branch}'. Review it on GitHub and merge if it looks right.")
        print("The main branch was NOT changed.")
    except subprocess.CalledProcessError as e:
        print(f"Could not push the proposed fix: {e.stderr}")


if __name__ == "__main__":
    diagnose_and_patch()
