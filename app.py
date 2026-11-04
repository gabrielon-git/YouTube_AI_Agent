from flask import Flask, request, render_template_string
import os
import requests

app = Flask(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_WORKFLOW_ID = os.environ.get("GITHUB_WORKFLOW_ID")

@app.route('/', methods=['GET', 'POST'])
def home():
    message = None
    color = "black"
    
    if request.method == 'POST':
        topic = request.form['topic']
        video_format = request.form.get('video_format', 'short')
        voice = request.form.get('voice_id', 'en-US-AriaNeural')
        
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW_ID}/dispatches"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "topic": topic,
                "format": video_format,
                "voice": voice
            }
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            # 204 means "Success, no content to return" which is what GitHub sends for this API
            if response.status_code == 204:
                message = f"✅ Success! '{topic}' video is rendering on GitHub. It will be ready in 2-3 minutes."
                color = "green"
            else:
                message = f"❌ Error {response.status_code}: {response.text}"
                color = "red"
        except Exception as e:
            message = f"❌ System Error: {str(e)}"
            color = "red"
            
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>AI Studio</title><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="font-family: Arial; text-align: center; margin-top: 30px; background-color: #f0f2f5;">
        <h1 style="color: #333;">🚀 YouTube AI Studio</h1>
        <p style="color: #666;">One-Click Video Generator</p>
        
        <div style="padding: 20px; background: white; border-radius: 10px; display: inline-block; width: 90%; max-width: 500px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            
            {"<p style='color: " + color + "; font-weight: bold; background: #f8f9fa; padding: 10px; border-radius: 5px;'>" + message + "</p>" if message else ""}
            
            <form method="POST">
                <input type="text" name="topic" placeholder="Enter video topic..." required style="padding: 10px; width: 80%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px;">
                
                <select name="video_format" style="padding: 10px; width: 85%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; background-color: white; font-size: 16px;">
                    <option value="short">Short-form (9:16)</option>
                    <option value="long">Long-form (16:9)</option>
                </select>
                <br>
                
                <select name="voice_id" style="padding: 10px; width: 85%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; background-color: white; font-size: 16px;">
                    <option value="en-US-AriaNeural">Aria (US Female)</option>
                    <option value="en-US-AndrewNeural">Andrew (US Male)</option>
                    <option value="en-US-EmmaMultilingualNeural">Emma (US Female)</option>
                    <option value="en-US-GuyNeural">Guy (US Male)</option>
                    <option value="en-GB-RyanNeural">Ryan (UK Male)</option>
                </select>
                <br>
                
                <button type="submit" style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; width: 85%;">Trigger Video Render</button>
            </form>
        </div>
        
        <div style="margin-top: 30px; font-size: 14px; color: #888;">
            <p>Once the video finishes rendering (2-3 mins), you can download it from the <strong>Actions</strong> tab on your GitHub repository.</p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7070)
