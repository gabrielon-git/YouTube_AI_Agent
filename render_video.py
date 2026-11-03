import os
import sys
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

# 2. Generate Script
def generate_script(topic):
    print("Generating script...")
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. ONLY return the spoken text, no visual cues."
    response = client.models.generate_content(model='gemini-3.8-flash', contents=prompt)
    return response.text

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
    script_text = generate_script(topic)
    print(f"Script: {script_text[:50]}...")
    
    audio_path = generate_voiceover(script_text)
    clip_paths = fetch_clips(topic)
    
    if not clip_paths:
        print("No clips found.")
        sys.exit(1)

    print("Editing clips...")
    clips = []
    for path in clip_paths:
        # Trim to 3 seconds, resize
        clip = VideoFileClip(path).subclip(0, 3).resize(height=1280, width=720)
        # Add transitions
        clip = clip.crossfadein(0.5).crossfadeout(0.5)
        clips.append(clip)

    print("Combining clips...")
    final_video = concatenate_videoclips(clips, padding=-0.5, method="compose")
    
    print("Adding text and audio...")
    text_png = create_text_image(topic)
    txt_clip = ImageClip(text_png).set_duration(final_video.duration)
    
    # Load the generated audio
    audio_clip = AudioFileClip(audio_path)
    
    # Combine video, text, and audio
    final = CompositeVideoClip([final_video, txt_clip])
    final = final.set_audio(audio_clip)
    
    print("Rendering final MP4...")
    final.write_videofile("final_video.mp4", fps=24, codec="libx264", audio_codec="aac", preset='medium', logger=None)
    print("Done!")

if __name__ == "__main__":
    # Change this topic to whatever you want to test
    build_video("Ocean")
