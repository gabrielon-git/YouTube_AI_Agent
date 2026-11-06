import os
import sys
import time
import asyncio
import requests
import random
import json
import re
import edge_tts
from google import genai
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip, ColorClip
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

def generate_script_with_retry(client, prompt, max_retries=3):
    models_to_try = ['gemini-3.8-flash', 'gemini-2.5-flash']
    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except Exception as e:
                print(f"Attempt failed: {e}")
                time.sleep(2)
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
        "https://assets.mixkit.co/videos/preview/mixkit-abstract-laser-background-42171-large.mp4",
        "https://assets.mixkit.co/videos/preview/mixkit-hands-holding-a-smartphone-with-a-green-screen-42998-large.mp4"
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
        font = ImageFont.truetype(font_path, 140)
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.65)

        draw.text((x+7, y+7), chunk, font=font, fill="black")
        draw.text((x, y), chunk, font=font, fill="white", stroke_width=8, stroke_fill="black")

        img_path = f"caption_{i}.png"
        img.save(img_path)
        caption_clips.append(ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk))
    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)

    # Autonomous topic generation if left blank
    if not topic or topic.strip() == "":
        print("No topic provided. Autonomously generating a viral topic...")
        topic_prompt = "Generate a single engaging, bizarre psychological or historical fact topic for a YouTube Short. Return ONLY the topic text."
        topic = generate_script_with_retry(client, topic_prompt)
        print(f"Autonomous Topic Chosen: {topic}")

    prompt = f"""
    Write a 30-second YouTube Short script about: {topic}.
    CRITICAL RULES FOR SCRIPT:
    1. Start with a shocking behavioral hook. 
    2. Tease the end ("The last fact is shocking").
    3. Deliver 3 to 4 counter-intuitive, bizarre facts rapidly.
    4. End with a sharp question.

    Return ONLY the plain text script as a single paragraph or sentences. Do not use JSON.
    """
        
    script_text = generate_script_with_retry(client, prompt)
    if not script_text: sys.exit(1)
    
    print("Generating voiceover...")
    audio_path = asyncio.run(generate_voiceover_with_retry(script_text, voice))
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    print("Fetching background video loop...")
    bg_path = get_background_video("background.mp4")
    
    if bg_path and os.path.exists(bg_path):
        bg_clip = VideoFileClip(bg_path)
        bg_clip = bg_clip.resize(height=1920)
        if bg_clip.w < 1080:
            bg_clip = bg_clip.resize(width=1080)
        
        if bg_clip.duration < total_duration:
            bg_clip = bg_clip.loop(duration=total_duration)
        else:
            bg_clip = bg_clip.subclip(0, total_duration)
    else:
        bg_clip = ColorClip(size=(1080, 1920), color=(15, 15, 25)).set_duration(total_duration)

    dark_overlay = ColorClip(size=(1080, 1920), color=(0, 0, 0)).set_opacity(0.35).set_duration(total_duration)
    video_base = CompositeVideoClip([bg_clip, dark_overlay]).set_audio(audio_clip)

    print("Adding background music...")
    try:
        music_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3"
        music_data = requests.get(music_url, timeout=15).content
        with open("bg_music.mp3", "wb") as f:
            f.write(music_data)

        bg_music = AudioFileClip("bg_music.mp3").volumex(0.08)
        if bg_music.duration < total_duration:
            bg_music = bg_music.loop(duration=total_duration)
        else:
            bg_music = bg_music.subclip(0, total_duration)

        final_audio = CompositeAudioClip([audio_clip, bg_music])
        video_base = video_base.set_audio(final_audio)
    except Exception as e:
        print(f"Music error: {e}")

    print("Adding professional captions...")
    caption_clips = create_captions(script_text, total_duration, 1080, 1920)
    final = CompositeVideoClip([video_base] + caption_clips)

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
    topic = os.environ.get("VIDEO_TOPIC", "")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")

    build_video(topic, video_format, voice)
