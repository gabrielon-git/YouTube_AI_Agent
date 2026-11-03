from flask import Flask, request, render_template_string
import os
import time
import asyncio
import edge_tts
import base64
from google import genai

app = Flask(__name__)

# 1. Function to generate the script
def generate_script_with_retry(client, prompt, max_retries=3):
    models_to_try = ['gemini-3.8-flash']
    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except Exception as e:
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    time.sleep(2 ** attempt)
                else:
                    raise e
    raise Exception("All models failed. Please try again.")

# 2. Function to generate the voiceover audio
def generate_voiceover(text):
    async def _generate():
        # Using a highly realistic female voice
        communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data
    
    # Run the async function safely inside Flask
    return asyncio.run(_generate())

@app.route('/', methods=['GET', 'POST'])
def home():
    script = None
    audio_base64 = None
    
    if request.method == 'POST':
        topic = request.form['topic']
        video_format = request.form.get('video_format', 'short')
        api_key = os.environ.get("GEMINI_API_KEY")
        
        if not api_key:
            script = "Error: API Key is missing."
        else:
            try:
                client = genai.Client(api_key=api_key)
                
                if video_format == 'short':
                    prompt = f"Write a highly engaging, fast-paced 60-second YouTube Short script about: {topic}. ONLY return the spoken text, do not include visual cues."
                else:
                    prompt = f"Write a detailed, structured, long-form YouTube video script about: {topic}. ONLY return the spoken text, do not include visual cues."
                
                script = generate_script_with_retry(client, prompt)
                
                # Generate the audio from the script
                if script and not script.startswith("Error"):
                    audio_bytes = generate_voiceover(script)
                    # Convert audio to base64 so we can play it directly in the browser
                    audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                    
            except Exception as e:
                script = f"Error: {str(e)}"
            
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>AI Studio</title><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="font-family: Arial; text-align: center; margin-top: 30px; background-color: #f0f2f5;">
        <h1 style="color: #333;">🚀 YouTube AI Studio</h1>
        
        <div style="padding: 20px; background: white; border-radius: 10px; display: inline-block; width: 90%; max-width: 500px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <form method="POST">
                <input type="text" name="topic" placeholder="Enter video topic..." style="padding: 10px; width: 80%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px;">
                
                <select name="video_format" style="padding: 10px; width: 85%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; background-color: white; font-size: 16px;">
                    <option value="short">Short-form (Under 60 seconds)</option>
                    <option value="long">Long-form (10+ minutes)</option>
                </select>
                <br>
                
                <button type="submit" style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; width: 85%;">Generate Video Assets</button>
            </form>
        </div>
        
        {% if script %}
        <div style="margin-top: 20px; padding: 20px; background: white; border-radius: 10px; text-align: left; width: 90%; max-width: 500px; margin-left: auto; margin-right: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            
            {% if audio_base64 %}
            <h3>🎧 Voiceover:</h3>
            <audio controls style="width: 100%; margin-bottom: 20px;">
                <source src="data:audio/mp3;base64,{{ audio_base64 }}" type="audio/mp3">
                Your browser does not support the audio element.
            </audio>
            {% endif %}

            <h3>Your Script:</h3>
            <p style="white-space: pre-wrap;">{{ script }}</p>
        </div>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(html, script=script, audio_base64=audio_base64)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7070)
