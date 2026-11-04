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

def generate_script_with_retry(client, prompt):
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash']
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except Exception as e:
                print(f"Attempt failed: {e}")
                time.sleep(2)
    return None

def generate_voiceover(text, voice_id):
    print(f"Generating voiceover with {voice_id}...")
    async def _generate():
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save("voiceover.mp3")
    asyncio.run(_generate())
    return "voiceover.mp3"

def fetch_clips(query, video_format, count=3):
    print(f"Fetching clips for: {query}")
    orientation = "portrait" if video_format == "short" else "landscape"
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={query}&video_type=film&per_page={count}&orientation={orientation}"
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

def create_text_image(text, width, height):
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

def build_video(topic, video_format, voice):
    print(f"Building video for topic: {topic}")
    client = genai.Client(api_key=GEMINI_KEY)
    
    if video_format == "short":
        prompt = f"Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. ONLY return the spoken text, no visual cues."
        width, height = 720, 1280
    else:
        prompt = f"Write a detailed, structured, 5-minute YouTube video script about: {topic}. ONLY return the spoken text, no visual cues."
        width, height = 1280, 720
        
    script_text = generate_script_with_retry(client, prompt)
    if not script_text:
        sys.exit(1)
        
    audio_path = generate_voiceover(script_text, voice)
    clip_paths = fetch_clips(topic, video_format)
    
    if not clip_paths:
        sys.exit(1)

    print("Editing clips...")
    clips = []
    for path in clip_paths:
        clip = VideoFileClip(path).subclip(0, 3)
        clip = clip.resize(newsize=(width, height))
        clips.append(clip)

    print("Combining clips...")
    final_video = concatenate_videoclips(clips, method="compose")
    
    print("Adding text...")
    text_png = create_text_image(topic, width, height)
    txt_clip = ImageClip(text_png).set_duration(final_video.duration)
    final = CompositeVideoClip([final_video, txt_clip])

    print("Adding audio...")
    audio_clip = AudioFileClip(audio_path)
    
    if final.duration < audio_clip.duration:
        final = final.loop(duration=audio_clip.duration)
    else:
        final = final.subclip(0, audio_clip.duration)

    final = final.set_audio(audio_clip)
    
    print("Rendering final MP4...")
    final.write_videofile(
        "final_video.mp4", 
        fps=24, 
        codec="libx264", 
        audio_codec="aac", 
        preset='ultrafast',
        ffmpeg_params=["-pix_fmt", "yuv420p"],
        logger=None
    )
    print("Done!")

if __name__ == "__main__":
    # THIS IS THE FIX: It now grabs the inputs from the dashboard!
    topic = os.environ.get("VIDEO_TOPIC", "Ocean")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")
    
    print(f"🚨 DEBUG: Received topic as '{topic}'")
    print(f"🚨 DEBUG: Received format as '{video_format}'")
    print(f"🚨 DEBUG: Received voice as '{voice}'")
    
    build_video(topic, video_format, voice)
