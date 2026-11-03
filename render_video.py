import os
import sys
import time
import asyncio
import requests
import edge_tts
from google import genai
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip
from PIL import Image, ImageDraw, ImageFont

# 1. Set up Keys
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not PIXABAY_KEY or not GEMINI_KEY:
    print("ERROR: Missing API Keys in Secrets!")
    sys.exit(1)

# 2. Generate Script with Retry and Fallback
def generate_script_with_retry(client, prompt):
    # Try the newest model first, then fall back to a stable one.
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash']
    last_error = "Unknown error"
    
    for model_name in models_to_try:
        print(f"Attempting script generation with {model_name}...")
        for attempt in range(3): # Try 3 times per model
            try:
                response = client.models.generate_content(
                    model=model_name, 
                    contents=prompt
                )
                print(f"Success with {model_name}!")
                return response.text
            except Exception as e:
                last_error = str(e)
                print(f"Attempt {attempt + 1} failed: {last_error}")
                if "503" in last_error or "UNAVAILABLE" in last_error:
                    wait_time = 2 ** attempt # Exponential backoff: 1s, 2s, 4s...
                    print(f"Model busy. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    # If it's a different error (e.g., API key issue), break and try next model
                    break 
                    
    print(f"All models failed. Last error: {last_error}")
    return None

# 3. Generate Voiceover
def generate_voiceover(text):
    print("Generating voiceover...")
    async def _generate():
        communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
        await communicate.save("voiceover.mp3")
    asyncio.run(_generate())
    return "voiceover.mp3"

# 4. Fetch B-Roll
def fetch_clips(query, count=3):
    print(f"Fetching clips for: {query}")
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={query}&video_type=film&per_page={count}&orientation=vertical"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Pixabay Error: {response.status_code} - {response.text[:200]}")
        return []
    data = response.json()
    
    clip_paths = []
    for i, video in enumerate(data.get('hits', [])):
        video_files = video['videos']
        file_url = video_files.get('medium', {}).get('url') or video_files.get('large', {}).get('url')
        if not file_url: continue
        
        path = f"clip_{i}.mp4"
        print(f"Downloading {path}...")
        with open(path, 'wb') as f:
            f.write(requests.get(file_url).content)
        clip_paths.append(path)
    return clip_paths

# 5. Create Text Overlay
def create_text_image(text, width=720, height=1280):
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 60)
    except:
        font = ImageFont.load_default()
        
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (width - (bbox[2] - bbox[0])) / 2
    y = (height - (bbox[3] - bbox[1])) / 2
    
    draw.text((x, y), text, font=font, fill="white", stroke_width=3, stroke_fill="black")
    img.save("text_overlay.png")
    return "text_overlay.png"

# 6. Build the Video
def build_video(topic):
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. ONLY return the spoken text, no visual cues."
    
    script_text = generate_script_with_retry(client, prompt)
    if not script_text:
        print("Could not generate script. Exiting.")
        sys.exit(1)
        
    print(f"Script: {script_text[:50]}...")
    
    audio_path = generate_voiceover(script_text)
    clip_paths = fetch_clips(topic)
    
    if not clip_paths:
        print("No clips found.")
        sys.exit(1)

    print("Editing clips...")
    clips = []
    for path in clip_paths:
        clip = VideoFileClip(path).subclip(0, 3).resize(height=1280, width=720)
        clip = clip.crossfadein(0.5).crossfadeout(0.5)
        clips.append(clip)

    print("Combining clips...")
    final_video = concatenate_videoclips(clips, padding=-0.5, method="compose")
    
    print("Adding text and audio...")
    text_png = create_text_image(topic)
    txt_clip = ImageClip(text_png).set_duration(final_video.duration)
    
    audio_clip = AudioFileClip(audio_path)
    final = CompositeVideoClip([final_video, txt_clip])
    final = final.set_audio(audio_clip)
    
    print("Rendering final MP4...")
    final.write_videofile("final_video.mp4", fps=24, codec="libx264", audio_codec="aac", preset='medium', logger=None)
    print("Done!")

if __name__ == "__main__":
    build_video("Ocean")
