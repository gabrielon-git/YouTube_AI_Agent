import os
import sys
import time
import asyncio
import requests
import random
import wave
import edge_tts

try:
    from google import genai
except ImportError:
    genai = None

try:
    from moviepy.editor import (
        VideoFileClip,
        ImageClip,
        CompositeVideoClip,
        AudioFileClip,
        CompositeAudioClip,
        ColorClip,
        concatenate_videoclips
    )
except ImportError:
    from moviepy import (
        VideoFileClip,
        ImageClip,
        CompositeVideoClip,
        AudioFileClip,
        CompositeAudioClip,
        ColorClip,
        concatenate_videoclips
    )

from PIL import Image, ImageDraw, ImageFont

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

def set_duration_safe(clip, duration):
    if hasattr(clip, "with_duration"):
        return clip.with_duration(duration)
    elif hasattr(clip, "set_duration"):
        return clip.set_duration(duration)
    return clip

def set_start_safe(clip, start):
    if hasattr(clip, "with_start"):
        return clip.with_start(start)
    elif hasattr(clip, "set_start"):
        return clip.set_start(start)
    return clip

def set_audio_safe(clip, audio):
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio)
    elif hasattr(clip, "set_audio"):
        return clip.set_audio(audio)
    return clip

def subclip_safe(clip, start, end):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    elif hasattr(clip, "subclip"):
        return clip.subclip(start, end)
    return clip

def resize_safe(clip, width=None, height=None, size=None):
    if size is not None:
        if hasattr(clip, "resized"):
            return clip.resized(size)
        elif hasattr(clip, "resize"):
            return clip.resize(size)
    if height is not None:
        if hasattr(clip, "resized"):
            return clip.resized(height=height)
        elif hasattr(clip, "resize"):
            return clip.resize(height=height)
    if width is not None:
        if hasattr(clip, "resized"):
            return clip.resized(width=width)
        elif hasattr(clip, "resize"):
            return clip.resize(width=width)
    return clip

def crop_safe(clip, x1=None, y1=None, x2=None, y2=None):
    if hasattr(clip, "cropped"):
        return clip.cropped(x1=x1, y1=y1, x2=x2, y2=y2)
    elif hasattr(clip, "crop"):
        return clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)
    return clip

def download_font():
    font_path = "Montserrat-ExtraBold.ttf"
    if not os.path.exists(font_path) or os.path.getsize(font_path) < 1000:
        url = "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-ExtraBold.ttf"
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200 and len(response.content) > 1000:
                with open(font_path, 'wb') as f:
                    f.write(response.content)
        except Exception as e:
            print(f"Font download failed: {e}")
    return font_path

def generate_script_with_retry(client, prompt, max_retries=3, use_search=False):
    if not client:
        return None
    models_to_try = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']
    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                config = {"tools": [{"google_search": {}}]} if use_search else None
                response = client.models.generate_content(model=model_name, contents=prompt, config=config)
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"API attempt failed with {model_name} ({e}). Retrying...")
                if use_search:
                    try:
                        response = client.models.generate_content(model=model_name, contents=prompt)
                        if response and response.text:
                            return response.text
                    except Exception:
                        pass
                wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                time.sleep(wait_time)
    print("❌ All generation attempts failed.")
    return None

async def generate_voiceover_with_retry(text, voice_id, max_retries=3):
    voices_to_try = [voice_id, "en-US-JennyNeural", "en-US-GuyNeural"]
    for current_voice in voices_to_try:
        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(text, current_voice)
                await communicate.save("voiceover.mp3")
                if os.path.exists("voiceover.mp3") and os.path.getsize("voiceover.mp3") > 1000:
                    return "voiceover.mp3"
            except Exception as e:
                print(f"Voiceover error with voice {current_voice}: {e}")
            await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1))

    print("Warning: Voiceover generation failed. Falling back to silent audio.")
    silent_path = "voiceover.wav"
    with wave.open(silent_path, "w") as wav_file:
        n_channels = 2
        sampwidth = 2
        framerate = 44100
        n_frames = int(framerate * 15)
        wav_file.setparams((n_channels, sampwidth, framerate, n_frames, "NONE", "not compressed"))
        wav_file.writeframes(b"\x00" * (n_frames * n_channels * sampwidth))
    return silent_path

def get_background_video(output_path="background.mp4"):
    curated_backgrounds = [
        "https://assets.mixkit.co/videos/preview/mixkit-digital-animation-of-screens-and-lights-41908-large.mp4",
        "https://assets.mixkit.co/videos/preview/mixkit-abstract-laser-background-42171-large.mp4"
    ]
    chosen_url = random.choice(curated_backgrounds)
    print("Downloading background video loop...")
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        response = requests.get(chosen_url, headers=headers, stream=True, timeout=30)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                return output_path
    except Exception as e:
        print(f"Background download failed: {e}")
    return None

def create_captions(script, total_duration, width, height):
    font_path = download_font()
    clean_script = script.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    clean_script = clean_script.encode('ascii', 'ignore').decode('ascii')
    
    words = clean_script.upper().split()
    if not words:
        return []
    chunks = [' '.join(words[i:i + 2]) for i in range(0, len(words), 2)]
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []

    try:
        font = ImageFont.truetype(font_path, 80)
    except Exception:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), chunk, font=font)
        chunk_w = bbox[2] - bbox[0]
        x = (width - chunk_w) / 2
        y = (height * 0.65)

        draw.text((x + 4, y + 4), chunk, font=font, fill="black")
        draw.text((x, y), chunk, font=font, fill="white", stroke_width=4, stroke_fill="black")

        img_path = f"caption_{i}.png"
        img.save(img_path)
        clip = ImageClip(img_path)
        clip = set_start_safe(clip, i * time_per_chunk)
        clip = set_duration_safe(clip, time_per_chunk)
        caption_clips.append(clip)
    return caption_clips

def build_video(topic, video_format, voice):
    client = None
    if GEMINI_KEY and genai:
        try:
            client = genai.Client(api_key=GEMINI_KEY)
        except Exception as e:
            print(f"GenAI Client initialization failed: {e}")

    if not topic or topic.strip() == "":
        print("Searching web for top viral trends...")
        topic_prompt = "Search the web for current viral internet trends, bizarre news, or shocking facts right now. Pick the single most engaging topic for a YouTube Short and return ONLY the topic text."
        topic = generate_script_with_retry(client, topic_prompt, use_search=True)
        if not topic:
            topic = "Shocking Science Facts That Sound Completely Fake"
        print(f"Trend Topic Chosen: {topic}")

    prompt = f"Write a short, punchy 20-second YouTube Short script about: {topic}. Return ONLY the plain text script."
    script_text = generate_script_with_retry(client, prompt, use_search=False)
    if not script_text:
        script_text = (
            "Did you know that honey found in ancient Egyptian tombs over 3,000 years old is still perfectly edible? "
            "Because of its low moisture and acidic pH, it never goes bad! Follow for more mind-blowing facts."
        )
    
    print("Generating voiceover...")
    audio_path = asyncio.run(generate_voiceover_with_retry(script_text, voice))
    audio_clip = AudioFileClip(audio_path)
    total_duration = min(audio_clip.duration, 25.0)
    audio_clip = subclip_safe(audio_clip, 0, total_duration)

    print("Fetching background video loop...")
    bg_path = get_background_video("background.mp4")
    bg_clip = None
    
    if bg_path and os.path.exists(bg_path):
        try:
            raw_bg = VideoFileClip(bg_path)
            scaled_bg = resize_safe(raw_bg, height=1080)
            cropped_bg = crop_safe(scaled_bg, x1=scaled_bg.w / 2 - 304, y1=0, x2=scaled_bg.w / 2 + 304, y2=1080)
            bg_clip = resize_safe(cropped_bg, size=(720, 1280))
            if bg_clip.duration < total_duration:
                repeats = int(total_duration // bg_clip.duration) + 2
                bg_clip = concatenate_videoclips([bg_clip] * repeats)
            bg_clip = subclip_safe(bg_clip, 0, total_duration)
        except Exception as e:
            print(f"Error processing background video: {e}")
            bg_clip = None

    if bg_clip is None:
        try:
            bg_clip = ColorClip(size=(720, 1280), color=(15, 15, 25), duration=total_duration)
        except TypeError:
            bg_clip = ColorClip(size=(720, 1280), color=(15, 15, 25))
            bg_clip = set_duration_safe(bg_clip, total_duration)

    video_base = set_audio_safe(bg_clip, audio_clip)

    print("Adding captions...")
    caption_clips = create_captions(script_text, total_duration, 720, 1280)
    final = CompositeVideoClip([video_base] + caption_clips)
    final = set_duration_safe(final, total_duration)

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