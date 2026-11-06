import os
import sys
from google import genai

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO_NAME = os.environ.get("GITHUB_REPOSITORY")

if not GEMINI_KEY:
    print("ERROR: Supervisor missing GEMINI_API_KEY!")
    sys.exit(1)

def diagnose_and_patch():
    print("🤖 Agent Supervisor activated: Analyzing failure dynamically...")
    
    # Read the recent error log if available, or inspect workspace files
    error_log = "Unknown error during execution."
    log_path = "build_error.log"
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
        	error_log = f.read()
            
    # Read current render_video.py code so the AI can reason about it
    with open("render_video.py", "r") as f:
        current_code = f.read()

    client = genai.Client(api_key=GEMINI_KEY)
    
    prompt = f"""
    You are an autonomous AI software maintenance agent. 
    Your job is to fix code execution failures in an automated video rendering pipeline.
    
    Here is the current source code of `render_video.py`:
    ```python
    {current_code}
    ```
    
    Here is the error traceback or failure details:
    ```
    {error_log}
    ```
    
    Analyze why this failure occurred. Think step-by-step about how to fix it (e.g., fixing imports, syntax errors, missing dependencies, or adjusting video processing logic).
    
    CRITICAL: Return ONLY the complete, corrected python code for `render_video.py`. Do not include markdown block ticks like ```python in your response if possible, or ensure it is clean valid Python.
    """

    print("🧠 Reasoning and generating fix via Gemini...")
    response = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt
    )
    
    fixed_code = response.text.strip()
    
    # Clean out markdown code blocks if the model wrapped them
    if fixed_code.startswith("```python"):
        fixed_code = fixed_code[9:]
    elif fixed_code.startswith("```"):
        fixed_code = fixed_code[3:]
    if fixed_code.endswith("```"):
        fixed_code = fixed_code[:-3]
    fixed_code = fixed_code.strip()

    if "def build_video" in fixed_code:
        with open("render_video.py", "w") as f:
            f.write(fixed_code)
        print("✨ Successfully patched `render_video.py` dynamically!")
        
        # Configure git to commit the self-healing fix back to the repository
        os.system("git config --global user.name 'AI Autonomous Agent'")
        os.system("git config --global user.email 'agent@supervisor.local'")
        os.system("git add render_video.py")
        os.system("git commit -m '🤖 Self-heal: Autonomous dynamic code patch by supervisor'")
        os.system("git push")
        print("🚀 Fix committed and pushed back to repository successfully!")
    else:
        print("❌ Agent safety check failed: Generated code did not look valid. Skipping patch.")

if __name__ == "__main__":
    diagnose_and_patch()
