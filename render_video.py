import os
import sys
import time
import asyncio
import requests
import urllib.parse
import random
import edge_tts
from google import genai
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip, ColorClip
from PIL import Image, ImageDraw, ImageFont

# 1. Set up Keys
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not PIXABAY_KEY or not GEMINI_KEY:
    print("ERROR: Missing API Keys!")
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

# FIXED: Using only the current active models
def generate_script_with_retry(client, prompt, max_retries=3):
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash']
    last_error = None

    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                print(f"Trying {model_name} (attempt {attempt + 1})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                print(f"Success with {model_name}!")
                return response.text
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    print(f"Rate limited. Waiting {wait_time:.1f}s before retry...")
                    time.sleep(wait_time)
                elif "503" in error_str or "UNAVAILABLE" in error_str:
                    print(f"Model busy. Retrying in 2s...")
                    time.sleep(2)
                else:
                    print(f"Non-retryable error: {e}")
                    break
    print(f"All models failed. Last error: {last_error}")
    return None

# Retry logic for edge-tts
async def generate_voiceover_with_retry(text, voice_id, max_retries=3):
    for attempt in range(max_retries):
        try:
            print(f"Generating voiceover (attempt {attempt + 1})...")
            communicate = edge_tts.Communicate(text, voice_id)
            await communicate.save("voiceover.mp3")
            if os.path.getsize("voiceover.mp3") > 1000:
                return "voiceover.mp3"
            else:
                print("Audio file too small, retrying...")
        except Exception as e:
            print(f"Voiceover error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                raise e
    raise Exception("Failed to generate voiceover after multiple attempts")

def fetch_satisfying_clip():
    queries = [
        "satisfying kinetic sand",
        "hydraulic press crushing",
        "soap cutting asmr",
        "satisfying slime",
        "hot knife foam",
        "oddly satisfying machine"
    ]
    query = random.choice(queries)
    print(f"Fetching hypnotic visual: {query}")

    safe_query = urllib.parse.quote(query)
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={safe_query}&video_type=film&per_page=3&orientation=portrait&min_width=1080"

    try:
        response = requests.get(url, timeout=15)
        data = response.json()

        if data.get('hits'):
            hit = random.choice(data['hits'])
            video_files = hit['videos']
            file_url = video_files.get('large', {}).get('url') or video_files.get('medium', {}).get('url')

            if file_url:
                path = f"asmr_{int(time.time() * 1000)}.mp4"
                video_content = requests.get(file_url, timeout=20).content
                with open(path, 'wb') as f:
                    f.write(video_content)
                return path
    except Exception as e:
        print(f"Pixabay error: {e}")
    return None

def crop_to_aspect_ratio(clip, target_w, target_h):
    w, h = clip.size
    target_ratio = target_w / target_h
    clip_ratio = w / h

    if clip_ratio > target_ratio:
        new_w = int(h * target_ratio)
        clip = clip.crop(x_center=w/2, width=new_w)
    else:
        new_h = int(w / target_ratio)
        clip = clip.crop(y_center=h/2, height=new_h)

    return clip.resize(newsize=(target_w, target_h))

def create_captions(script, total_duration, width, height):
    print("Generating professional captions...")
    font_path = download_font()

    words = script.upper().split()
    chunks = [' '.join(words[i:i + 2]) for i in range(0, len(words), 2)]
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []

    try:
        font = ImageFont.truetype(font_path, 130)
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.68)

        draw.text((x+6, y+6), chunk, font=font, fill="black")
        draw.text((x, y), chunk, font=font, fill="white", stroke_width=9, stroke_fill="black")

        img_path = f"caption_{i}.png"
        img.save(img_path)

        caption_clip = ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk)
        caption_clips.append(caption_clip)

    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)

    prompt = f"""
    Write a 30-second YouTube Short script about: {topic}.
    CRITICAL RULES:
    1. Start with a shocking behavioral hook (e.g., "Don't sleep with a mirror in your room").
    2. Tease the end (e.g., "The last fact is shocking").
    3. Deliver 3 to 4 counter-intuitive, bizarre facts rapidly. No fluff.
    4. End with a sharp question.
    5. ONLY return the spoken text.
    """

    script_text = generate_script_with_retry(client, prompt)
    if not script_text:
        print("Could not generate script. Exiting.")
        sys.exit(1)

    print(f"Script generated: {script_text[:100]}...")

    try:
        audio_path = asyncio.run(generate_voiceover_with_retry(script_text, voice))
    except Exception as e:
        print(f"Voiceover failed completely: {e}")
        sys.exit(1)

    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    print("Downloading hypnotic background loops...")
    clips = []
    for _ in range(3):
        clip_path = fetch_satisfying_clip()
        if clip_path:
            clip = VideoFileClip(clip_path)
            if clip.duration < 2:
                clip = clip.loop(duration=2)
            else:
                clip = clip.subclip(0, 2)

            clip = crop_to_aspect_ratio(clip, 1080, 1920)
            dark_overlay = ColorClip(size=(1080, 1920), color=(0, 0, 0)).set_opacity(0.2).set_duration(2)
            clip = CompositeVideoClip([clip, dark_overlay])
            clips.append(clip)

    if not clips:
        print("No background clips downloaded. Exiting.")
        sys.exit(1)

    print("Combining and looping background...")
    base_loop = concatenate_videoclips(clips, method="compose")

    final_video = base_loop
    while final_video.duration < audio_clip.duration:
        final_video = concatenate_videoclips([final_video, base_loop], method="compose")
    final_video = final_video.subclip(0, audio_clip.duration)

    final_video = final_video.set_audio(audio_clip)

    print("Adding background music...")
    try:
        music_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3"
        music_data = requests.get(music_url, timeout=15).content
        with open("bg_music.mp3", "wb") as f:
            f.write(music_data)

        bg_music = AudioFileClip("bg_music.mp3").volumex(0.1)
        if bg_music.duration < final_video.duration:
            bg_music = bg_music.loop(duration=final_video.duration)
        else:
            bg_music = bg_music.subclip(0, final_video.duration)

        final_audio = CompositeAudioClip([audio_clip, bg_music])
        final_video = final_video.set_audio(final_audio)
    except Exception as e:
        print(f"Music error: {e}")

    print("Adding professional captions...")
    caption_clips = create_captions(script_text, final_video.duration, 1080, 1920)
    final = CompositeVideoClip([final_video] + caption_clips)

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
    topic = os.environ.get("VIDEO_TOPIC", "Ocean")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")

    print(f"🚨 DEBUG: Building '{video_format}' video for topic: '{topic}'")
    build_video(topic, video_format, voice)
