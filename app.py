import os
import requests
from flask import Flask, render_template, request

app = Flask(__name__)

# GitHub Repository Details
GITHUB_OWNER = "gabrielon-git"  
GITHUB_REPO = "YouTube_AI_Agent"   

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    topic = request.form.get('topic', 'Default Topic')
    
    # Pull the token directly from Render's environment variables / secrets
    github_token = os.getenv("GITHUB_TOKEN")
    
    if not github_token:
        return "<h3>Error: GITHUB_TOKEN environment variable is not set on Render.</h3>", 500

    # Trigger the GitHub Actions workflow via API
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/render_video.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json"
    }
    data = {
        "ref": "main",
        "inputs": {
            "topic": topic
        }
    }
    
    response = requests.post(url, json=data, headers=headers)
    
    if response.status_code == 204:
        return render_template('result.html', script=f"Workflow triggered successfully for topic: '{topic}'. Check your GitHub Actions tab to watch it render!")
    else:
        return f"<h3>Failed to trigger GitHub Action:</h3><pre>{response.text}</pre>", 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
