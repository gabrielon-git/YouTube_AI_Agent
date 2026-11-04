import os
import sys
import time
import asyncio
import json
import re
import requests
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

# NEW: Download a professional font for the captions
def download_font():
    font_path = "Montserrat-ExtraBold.ttf"
    if not os.path.exists(font_path):
        print("Downloading professional font...")
        url = "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-ExtraBold.ttf"
        response = requests.get(url)
        with open(font_path, 'wb') as f:
            f.write(response.content)
    return font_path

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
    print(f"Generating voiceover...")
    async def _generate():
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save("voiceover.mp3")
    asyncio.run(_generate())
    return "voiceover.mp3"

def fetch_clip(query):
    print(f"Fetching cinematic clip for: {query}")
    search_query = f"{query} cinematic dark"
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={search_query}&video_type=film&per_page=5&orientation=portrait"
    response = requests.get(url)
    data = response.json()
    
    if data.get('hits'):
        import random
        hit = random.choice(data['hits'])
        video_files = hit['videos']
        file_url = video_files.get('medium', {}).get('url') or video_files.get('large', {}).get('url')
        if file_url:
            path = f"clip_{int(time.time() * 1000)}.mp4"
            with open(path, 'wb') as f:
                f.write(requests.get(file_url).content)
            return path
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

# UPGRADED: Professional captions with Montserrat font
def create_captions(script, total_duration, width, height):
    print("Generating professional captions...")
    font_path = download_font()
    
    words = script.upper().split()
    # 3 words per chunk
    chunks = [' '.join(words[i:i + 3]) for i in range(0, len(words), 3)]
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []
    
    try:
        font = ImageFont.truetype(font_path, 110)
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.70) 
        
        # Drop shadow
        draw.text((x+5, y+5), chunk, font=font, fill="black")
        # White text with thick black outline
        draw.text((x, y), chunk, font=font, fill="white", stroke_width=8, stroke_fill="black")
        
        img_path = f"caption_{i}.png"
        img.save(img_path)
        
        caption_clip = ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk)
        caption_clips.append(caption_clip)
        
    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)
    
    prompt = f"""
    Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. 
    Start immediately with a shocking hook. Use short sentences. 
    End with a sharp, polarizing question to drive comments.
    Format your output EXACTLY as a JSON array of objects. 
    Each object must have a 'sentence' key and a 'visual' key.
    The 'visual' key MUST be a 2-word, moody search term (e.g., 'dark vault', 'macro dollars', 'stock market crash').
    Return ONLY valid JSON. Do not include markdown formatting.
    """
        
    script_text = generate_script_with_retry(client, prompt)
    if not script_text: sys.exit(1)
    
    json_str = re.sub(r'```json\n|\n```', '', script_text).strip()
    try:
        scenes = json.loads(json_str)
    except Exception as e:
        print(f"Failed to parse JSON: {e}")
        sys.exit(1)
        
    full_script = " ".join([scene['sentence'] for scene in scenes])
    audio_path = generate_voiceover(full_script, voice)
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration
    time_per_scene = total_duration / len(scenes)
    
    print("Downloading cinematic B-Roll...")
    clips = []
    for scene in scenes:
        clip_path = fetch_clip(scene['visual'])
        if not clip_path:
            clip_path = fetch_clip(topic) 
            
        if clip_path:
            clip = VideoFileClip(clip_path)
            if clip.duration < time_per_scene:
                clip = clip.loop(duration=time_per_scene)
            else:
                clip = clip.subclip(0, time_per_scene)
            
            clip = crop_to_aspect_ratio(clip, 1080, 1920)
            
            # Cinematic dark tint
            dark_overlay = ColorClip(size=(1080, 1920), color=(0, 0, 0)).set_opacity(0.5).set_duration(time_per_scene)
            clip = CompositeVideoClip([clip, dark_overlay])
            
            # Smooth crossfade
            clip = clip.crossfadein(0.5).crossfadeout(0.5)
            
            clips.append(clip)
        else:
            img = Image.new('RGB', (1080, 1920), color=(10, 10, 15))
            img.save("fallback.png")
            clips.append(ImageClip("fallback.png").set_duration(time_per_scene))

    print("Combining clips with transitions...")
    final_video = concatenate_videoclips(clips, padding=-0.5, method="compose")
    final_video = final_video.set_audio(audio_clip)

    print("Adding background music...")
    try:
        music_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
        music_data = requests.get(music_url).content
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

    print("Adding dynamic captions...")
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
    topic = os.environ.get("VIDEO_TOPIC", "Ocean")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")
    
    print(f"🚨 DEBUG: Building '{video_format}' video for topic: '{topic}'")
    build_video(topic, video_format, voice)
