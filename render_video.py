import os
import sys
import time
import asyncio
import json
import re
import requests
import edge_tts
from google import genai
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip
from PIL import Image, ImageDraw, ImageFont

# 1. Set up Keys
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not PIXABAY_KEY or not GEMINI_KEY:
    print("ERROR: Missing API Keys!")
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
    print(f"Generating voiceover...")
    async def _generate():
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save("voiceover.mp3")
    asyncio.run(_generate())
    return "voiceover.mp3"

def fetch_clip(query):
    print(f"Fetching clip for: {query}")
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={query}&video_type=film&per_page=3&orientation=portrait"
    response = requests.get(url)
    data = response.json()
    
    if data.get('hits'):
        # Try to get the medium/large version
        video_files = data['hits'][0]['videos']
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
        x_center = w / 2
        clip = clip.crop(x_center=x_center, width=new_w)
    else:
        new_h = int(w / target_ratio)
        y_center = h / 2
        clip = clip.crop(y_center=y_center, height=new_h)
        
    return clip.resize(newsize=(target_w, target_h))

def create_captions(script, total_duration, width, height):
    print("Generating dynamic captions...")
    words = script.upper().split()
    chunks = [' '.join(words[i:i + 2]) for i in range(0, len(words), 2)]
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []
    
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 90)
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.72) 
        
        draw.text((x, y), chunk, font=font, fill="#FFD700", stroke_width=8, stroke_fill="black")
        
        img_path = f"caption_{i}.png"
        img.save(img_path)
        
        caption_clip = ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk)
        caption_clips.append(caption_clip)
        
    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)
    
    # UPGRADED PROMPT: Forces JSON output with per-sentence visual queries
    prompt = f"""
    Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. 
    Start immediately with a shocking hook. Use short sentences. 
    End with a provocative question to drive comments.
    Format your output EXACTLY as a JSON array of objects. 
    Each object must have a 'sentence' key (the spoken words) and a 'visual' key (a 2-word search term for a video background).
    Example format: [{{"sentence": "Banks don't hold your money.", "visual": "empty vault"}}, {{"sentence": "They lend it out.", "visual": "cash counting"}}]
    Return ONLY valid JSON. Do not include markdown formatting like ```json.
    """
        
    script_text = generate_script_with_retry(client, prompt)
    if not script_text: sys.exit(1)
    
    # Clean up potential markdown formatting from Gemini
    json_str = re.sub(r'```json\n|\n```', '', script_text).strip()
    
    try:
        scenes = json.loads(json_str)
    except Exception as e:
        print(f"Failed to parse JSON from Gemini: {e}")
        print(f"Raw output: {script_text}")
        sys.exit(1)
        
    full_script = " ".join([scene['sentence'] for scene in scenes])
    
    audio_path = generate_voiceover(full_script, voice)
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration
    
    # Calculate time per scene
    time_per_scene = total_duration / len(scenes)
    
    print("Downloading B-Roll for each scene...")
    clips = []
    for scene in scenes:
        clip_path = fetch_clip(scene['visual'])
        if not clip_path:
            clip_path = fetch_clip(topic) # Fallback
            
        if clip_path:
            clip = VideoFileClip(clip_path)
            # Ensure clip is long enough, or loop it
            if clip.duration < time_per_scene:
                clip = clip.loop(duration=time_per_scene)
            else:
                clip = clip.subclip(0, time_per_scene)
                
            clip = crop_to_aspect_ratio(clip, 1080, 1920)
            clips.append(clip)
        else:
            # If no clip found, use a black background
            clips.append(ImageClip("black.png").set_duration(time_per_scene).resize((1080, 1920)))

    print("Combining clips...")
    final_video = concatenate_videoclips(clips, method="compose")
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
