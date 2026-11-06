import os
import sys
import time
import asyncio
import requests
import random
import edge_tts
from google import genai

try:
    from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip, ColorClip
except ImportError:
    from moviepy import VideoFileClip, ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip, ColorClip

from PIL import Image, ImageDraw, ImageFont

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("ERROR: Missing API Key!")
    sys.exit(1)

def download_font():
    font_path = "Montserrat-ExtraBold.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-ExtraBold.ttf"
        try:
            response = requests.get(url, timeout=15)
            with open(font_path, 'wb') as f:
                f.write(response.content)
        except Exception as e:
            print(f"Font download failed: {e}")
    return font_path

def generate_script_with_retry(client, prompt, max_retries=5, use_search=False):
    models_to_try = ['gemini-3.8-flash']
    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                config = {"tools": [{"google_search": {}}]} if use_search else {}
                response = client.models.generate_content(model=model_name, contents=prompt, config=config)
                return response.text
            except Exception as e:
                wait_time = (2 ** attempt) + random.uniform(1, 3)
                print(f"API attempt failed ({e}). Retrying in {wait_time:.1f} seconds...")
                time.sleep(wait_time)
    print("❌ All generation attempts failed due to server load.")
    return None

async def generate_voiceover_with_retry(text, voice_id, max_retries=5):
    voices_to_try = [voice_id, "en-US-JennyNeural", "en-US-GuyNeural"]
    for current_voice in voices_to_try:
        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(text, current_voice)
                await communicate.save("voiceover.mp3")
                if os.path.getsize("voiceover.mp3") > 1000:
                    return "voiceover.mp3"
            except Exception as e:
                print(f"Voiceover error: {e}")
            await asyncio.sleep((2 ** attempt) + random.uniform(0, 1))
    raise Exception("Failed to generate voiceover.")

def get_background_video(output_path="background.mp4"):
    curated_backgrounds = [
        "https://assets.mixkit.co/videos/preview/mixkit-digital-animation-of-screens-and-lights-41908-large.mp4",
        "https://assets.mixkit.co/videos/preview/mixkit-abstract-laser-background-42171-large.mp4"
    ]
    chosen_url = random.choice(curated_backgrounds)
    print(f"Downloading background video loop...")
    
    try:
        response = requests.get(chosen_url, stream=True, timeout=30)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return output_path
    except Exception as e:
        print(f"Background download failed: {e}")
    return None

def create_captions(script, total_duration, width, height):
    font_path = download_font()
    clean_script = script.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    clean_script = clean_script.encode('ascii', 'ignore').decode('ascii')
    
    words = clean_script.upper().split()
    chunks = [' '.join(words[i:i + 2]) for i in range(0, len(words), 2)]
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []

    try:
        font = ImageFont.truetype(font_path, 120)
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.65)

        draw.text((x+5, y+5), chunk, font=font, fill="black")
        draw.text((x, y), chunk, font=font, fill="white", stroke_width=6, stroke_fill="black")

        img_path = f"caption_{i}.png"
        img.save(img_path)
        caption_clips.append(ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk))
    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)

    if not topic or topic.strip() == "":
        print("Searching web for top viral trends...")
        topic_prompt = "Search the web for current viral internet trends, bizarre news, or shocking facts right now. Pick the single most engaging topic for a YouTube Short and return ONLY the topic text."
        topic = generate_script_with_retry(client, topic_prompt, use_search=True)
        print(f"Trend Topic Chosen: {topic}")

    if not topic:
        sys.exit(1)

    prompt = f"Write a short, punchy 20-second YouTube Short script about: {topic}. Return ONLY the plain text script."
    script_text = generate_script_with_retry(client, prompt, use_search=False)
    if not script_text: 
        sys.exit(1)
    
    print("Generating voiceover...")
    audio_path = asyncio.run(generate_voiceover_with_retry(script_text, voice))
    audio_clip = AudioFileClip(audio_path)
    total_duration = min(audio_clip.duration, 25.0)

    print("Fetching background video loop...")
    bg_path = get_background_video("background.mp4")
    
    if bg_path and os.path.exists(bg_path):
        bg_clip = VideoFileClip(bg_path)
        bg_clip = bg_clip.resize(height=1080).crop(x1=bg_clip.w/2 - 304, y1=0, x2=bg_clip.w/2 + 304, y2=1080).resize(newsize=(720, 1280))
        if bg_clip.duration < total_duration:
            bg_clip = bg_clip.loop(duration=total_duration)
        else:
            bg_clip = bg_clip.subclip(0, total_duration)
    else:
        bg_clip = ColorClip(size=(720, 1280), color=(15, 15, 25)).set_duration(total_duration)

    video_base = bg_clip.set_audio(audio_clip)

    print("Adding captions...")
    caption_clips = create_captions(script_text, total_duration, 720, 1280)
    final = CompositeVideoClip([video_base] + caption_clips)

    print("Rendering final MP4 quickly...")
    final.write_videofile(
        "final_video.mp4",
        fps=20,
        codec="libx264",
        audio_codec="aac",
        preset='ultrafast',
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p"],
        logger=None
    )
    print("Done!")

if __name__ == "__main__":
    topic = os.environ.get("VIDEO_TOPIC", "")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")

    build_video(topic, video_format, voice)
