import os
import sys
import time
import asyncio
import requests
import urllib.parse
import random
import json
import re
import edge_tts
from google import genai
from moviepy.editor import ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip, ColorClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

# 1. Set up Keys
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
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash']
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

# NEW: Generate AI Images using your 4-part formula
def generate_ai_image(prompt, filename):
    print(f"Generating AI Image: {prompt[:50]}...")
    safe_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1080&height=1920&nologo=true&enhance=true"
    
    try:
        response = requests.get(url, timeout=45)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            return filename
    except Exception as e:
        print(f"Image generation failed: {e}")
    return None

# NEW: Ken Burns effect to make static images feel like cinematic video
def add_ken_burns_effect(image_path, duration):
    clip = ImageClip(image_path).set_duration(duration)
    # Slow zoom in from 1.0 to 1.15
    def zoom(t):
        return 1 + (0.15 * (t / duration))
    return clip.resize(zoom).set_position('center')

def create_captions(script, total_duration, width, height):
    font_path = download_font()
    clean_script = script.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    clean_script = clean_script.encode('ascii', 'ignore').decode('ascii')
    
    words = clean_script.upper().split()
    chunks = [' '.join(words[i:i + 2]) for i in range(0, len(words), 2)]
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []

    try:
        font = ImageFont.truetype(font_path, 160) # Standard YouTube size
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.65)

        draw.text((x+7, y+7), chunk, font=font, fill="black")
        draw.text((x, y), chunk, font=font, fill="white", stroke_width=10, stroke_fill="black")

        img_path = f"caption_{i}.png"
        img.save(img_path)
        caption_clips.append(ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk))
    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)

    # ADVANCED PROMPT: Uses your exact 4-part visual formula
    prompt = f"""
    Write a 30-second YouTube Short script about: {topic}.
    CRITICAL RULES FOR SCRIPT:
    1. Start with a shocking behavioral hook. 
    2. Tease the end ("The last fact is shocking").
    3. Deliver 3 to 4 counter-intuitive, bizarre facts rapidly.
    4. End with a sharp question.

    You must output EXACTLY as a JSON array of objects.
    Each object must have a 'sentence' key (the spoken text) and an 'image_prompt' key.
    
    CRITICAL RULES FOR IMAGE PROMPTS:
    You are a master visual director. For every sentence, write an AI image prompt using this 4-part formula:
    1. The Subject (Tactile, warm, orderly, or nostalgic).
    2. The Contrast (Dramatic directional lighting, rim lighting, deep shadows).
    3. The Texture (Matte, weathered, glossy, granular film grain).
    4. The Framing (Macro close-up, rule of thirds, shallow depth of field).
    Choose a vibe: "Nostalgic Macro", "Cyber-Zen Minimalist", or "Cinematic Cozy".
    
    Return ONLY valid JSON. Do not include markdown formatting.
    """
        
    script_text = generate_script_with_retry(client, prompt)
    if not script_text: sys.exit(1)
    
    json_str = re.sub(r'```json\n|\n```', '', script_text).strip()
    try:
        scenes = json.loads(json_str)
    except Exception as e:
        print(f"Failed to parse JSON: {e}\n{script_text}")
        sys.exit(1)
        
    full_script = " ".join([scene['sentence'] for scene in scenes])
    audio_path = asyncio.run(generate_voiceover_with_retry(full_script, voice))
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration
    time_per_scene = total_duration / len(scenes)

    print("Generating cinematic AI B-Roll...")
    clips = []
    for i, scene in enumerate(scenes):
        img_path = f"scene_{i}.jpg"
        result = generate_ai_image(scene['image_prompt'], img_path)
        
        if result:
            # Apply Ken Burns to the AI image
            clip = add_ken_burns_effect(result, time_per_scene)
            # Very slight dark tint so the white captions pop
            dark_overlay = ColorClip(size=(1080, 1920), color=(0, 0, 0)).set_opacity(0.3).set_duration(time_per_scene)
            clip = CompositeVideoClip([clip, dark_overlay])
            clips.append(clip)
        else:
            # Fallback if AI fails
            img = Image.new('RGB', (1080, 1920), color=(10, 10, 15))
            img.save("fallback.png")
            clips.append(ImageClip("fallback.png").set_duration(time_per_scene))

    print("Combining scenes...")
    final_video = concatenate_videoclips(clips, padding=-0.5, method="compose")
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
    caption_clips = create_captions(full_script, final_video.duration, 1080, 1920)
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
    topic = os.environ.get("VIDEO_TOPIC")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")

    if not topic:
        sys.exit(1)

    build_video(topic, video_format, voice)
