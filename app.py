from flask import Flask, request, render_template_string
import os
import time
import asyncio
import edge_tts
import base64
import textwrap
from google import genai
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, AudioFileClip

app = Flask(__name__)

def generate_script_with_retry(client, prompt):
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash']
    last_error = "Unknown error"
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except Exception as e:
                last_error = str(e)
                time.sleep(2)
    return f"Error: {last_error}"

def generate_voiceover(text, voice_id):
    async def _generate():
        communicate = edge_tts.Communicate(text, voice_id)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data
    return asyncio.run(_generate())

# --- THE VIDEO ENGINE ---
def create_video(text, audio_bytes, video_format):
    temp_audio = "temp_audio.mp3"
    temp_img = "temp_bg.png"
    temp_video = "temp_output.mp4"
    
    with open(temp_audio, "wb") as f:
        f.write(audio_bytes)
    
    # Lower resolution for Shorts to save Render's memory
    if video_format == 'short':
        width, height = 720, 1280
    else:
        width, height = 1280, 720
        
    img = Image.new('RGB', (width, height), color=(15, 15, 25)) # Dark background
    draw = ImageDraw.Draw(img)
    
    font_size = 50 if video_format == 'short' else 40
    try:
        # Use a standard font available on Linux servers
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()

    # Wrap text
    max_chars = 30 if video_format == 'short' else 60
    wrapped_lines = textwrap.wrap(text, width=max_chars)
    
    line_height = font_size + 15
    total_text_height = len(wrapped_lines) * line_height
    y = (height - total_text_height) // 2
    
    for line in wrapped_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        draw.text((x, y), line, fill=(255, 255, 255), font=font)
        y += line_height

    img.save(temp_img)

    # Stitch audio and image together
    audio_clip = AudioFileClip(temp_audio)
    image_clip = ImageClip(temp_img).set_duration(audio_clip.duration)
    video = image_clip.set_audio(audio_clip)
    
    # Render video
    video.write_videofile(temp_video, fps=24, codec="libx264", audio_codec="aac", logger=None)
    
    with open(temp_video, "rb") as f:
        video_bytes = f.read()
    
    # Cleanup
    video.close()
    audio_clip.close()
    image_clip.close()
    os.remove(temp_audio)
    os.remove(temp_img)
    os.remove(temp_video)
    
    return base64.b64encode(video_bytes).decode('utf-8')

@app.route('/', methods=['GET', 'POST'])
def home():
    script = None
    audio_base64 = None
    video_base64 = None
    selected_voice = 'en-US-AriaNeural'
    
    if request.method == 'POST':
        topic = request.form['topic']
        video_format = request.form.get('video_format', 'short')
        selected_voice = request.form.get('voice_id', 'en-US-AriaNeural')
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
                
                if script and not script.startswith("Error"):
                    audio_bytes = generate_voiceover(script, selected_voice)
                    audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                    
                    # Generate the video!
                    video_base64 = create_video(script, audio_bytes, video_format)
                    
            except Exception as e:
                script = f"System Error: {str(e)}"
            
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>AI Studio</title><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="font-family: Arial; text-align: center; margin-top: 30px; background-color: #f0f2f5;">
        <h1 style="color: #333;">🚀 YouTube AI Studio</h1>
        
        <div style="padding: 20px; background: white; border-radius: 10px; display: inline-block; width: 90%; max-width: 500px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <!-- Loading message (hidden by default) -->
            <p id="loadingMsg" style="display: none; color: #007bff; font-weight: bold;">⏳ Generating Script, Voiceover, and Video... This may take 1-2 minutes. Please wait!</p>
            
            <form method="POST" onsubmit="document.getElementById('loadingMsg').style.display = 'block';">
                <input type="text" name="topic" placeholder="Enter video topic..." required style="padding: 10px; width: 80%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px;">
                
                <select name="video_format" style="padding: 10px; width: 85%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; background-color: white; font-size: 16px;">
                    <option value="short">Short-form (9:16)</option>
                    <option value="long">Long-form (16:9)</option>
                </select>
                <br>
                
                <select name="voice_id" style="padding: 10px; width: 85%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; background-color: white; font-size: 16px;">
                    <option value="en-US-AriaNeural" {% if selected_voice == 'en-US-AriaNeural' %}selected{% endif %}>Aria (US Female)</option>
                    <option value="en-US-AndrewNeural" {% if selected_voice == 'en-US-AndrewNeural' %}selected{% endif %}>Andrew (US Male)</option>
                    <option value="en-US-EmmaMultilingualNeural" {% if selected_voice == 'en-US-EmmaMultilingualNeural' %}selected{% endif %}>Emma (US Female)</option>
                    <option value="en-US-GuyNeural" {% if selected_voice == 'en-US-GuyNeural' %}selected{% endif %}>Guy (US Male)</option>
                </select>
                <br>
                
                <button type="submit" style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; width: 85%;">Make Video</button>
            </form>
        </div>
        
        {% if script %}
        <div style="margin-top: 20px; padding: 20px; background: white; border-radius: 10px; text-align: left; width: 90%; max-width: 500px; margin-left: auto; margin-right: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            
            {% if video_base64 %}
            <h3>🎬 Your Video:</h3>
            <video controls style="width: 100%; border-radius: 8px; margin-bottom: 15px;">
                <source src="data:video/mp4;base64,{{ video_base64 }}" type="video/mp4">
            </video>
            <a href="data:video/mp4;base64,{{ video_base64 }}" download="video.mp4" style="display: inline-block; padding: 10px 15px; background: #28a745; color: white; text-decoration: none; border-radius: 5px; margin-bottom: 20px; font-size: 14px;">⬇️ Download MP4</a>
            {% endif %}

            {% if audio_base64 %}
            <h3>🎧 Voiceover:</h3>
            <audio controls style="width: 100%; margin-bottom: 10px;">
                <source src="data:audio/mp3;base64,{{ audio_base64 }}" type="audio/mp3">
            </audio>
            {% endif %}

            <h3>Your Script:</h3>
            <p style="white-space: pre-wrap;">{{ script }}</p>
        </div>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(html, script=script, audio_base64=audio_base64, video_base64=video_base64, selected_voice=selected_voice)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7070)
