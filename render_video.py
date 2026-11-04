import os
import sys
import time
import asyncio
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
    print(f"Generating voiceover with {voice_id}...")
    async def _generate():
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save("voiceover.mp3")
    asyncio.run(_generate())
    return "voiceover.mp3"

def fetch_clips(query, video_format, count=5):
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

# FIX 1: Crop instead of stretch to maintain aspect ratio
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

# FIX 2: Create dynamic word-by-word captions based on the research
def create_captions(script, total_duration, width, height):
    print("Generating dynamic captions...")
    words = script.upper().split()
    # Chunk into 2 words for that fast, punchy TikTok style
    chunks = [' '.join(words[i:i + 2]) for i in range(0, len(words), 2)]
    
    time_per_chunk = total_duration / len(chunks)
    caption_clips = []
    
    try:
        # DejaVuSans-Bold is the standard thick font on GitHub servers
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 90)
    except:
        font = ImageFont.load_default()

    for i, chunk in enumerate(chunks):
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        bbox = draw.textbbox((0, 0), chunk, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height * 0.72) # Lower-middle safe zone
        
        # Draw yellow text with a thick black outline
        draw.text((x, y), chunk, font=font, fill="#FFD700", stroke_width=8, stroke_fill="black")
        
        img_path = f"caption_{i}.png"
        img.save(img_path)
        
        caption_clip = ImageClip(img_path).set_start(i * time_per_chunk).set_duration(time_per_chunk)
        caption_clips.append(caption_clip)
        
    return caption_clips

def build_video(topic, video_format, voice):
    client = genai.Client(api_key=GEMINI_KEY)
    
    # Upgraded Prompt based on your research (The Hook)
    if video_format == "short":
        prompt = (f"Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. "
                  f"Start immediately with a shocking hook. Use short sentences. ONLY return the spoken text.")
        width, height = 1080, 1920 
    else:
        prompt = f"Write a detailed, structured, 5-minute YouTube video script about: {topic}. ONLY return the spoken text."
        width, height = 1920, 1080
        
    script_text = generate_script_with_retry(client, prompt)
    if not script_text: sys.exit(1)
        
    audio_path = generate_voiceover(script_text, voice)
    clip_paths = fetch_clips(topic, video_format)
    if not clip_paths: sys.exit(1)

    print("Editing clips...")
    clips = []
    for path in clip_paths:
        clip = VideoFileClip(path).subclip(0, 2) # Fast 2-second cuts
        clip = crop_to_aspect_ratio(clip, width, height)
        clips.append(clip)

    print("Combining clips and fixing loop bug...")
    base_video = concatenate_videoclips(clips, method="compose")
    audio_clip = AudioFileClip(audio_path)
    
    # FIX 3: Manual looping to prevent the black screen drop-out
    final_video = base_video
    while final_video.duration < audio_clip.duration:
        final_video = concatenate_videoclips([final_video, base_video], method="compose")
    final_video = final_video.subclip(0, audio_clip.duration)

    print("Adding background music...")
    try:
        music_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
        music_data = requests.get(music_url).content
        with open("bg_music.mp3", "wb") as f:
            f.write(music_data)
        
        # Mix background music at 10% volume
        bg_music = AudioFileClip("bg_music.mp3").volumex(0.1) 
        if bg_music.duration < final_video.duration:
            bg_music = bg_music.loop(duration=final_video.duration)
        else:
            bg_music = bg_music.subclip(0, final_video.duration)
        
        final_audio = CompositeAudioClip([audio_clip, bg_music])
        final_video = final_video.set_audio(final_audio)
    except Exception as e:
        print(f"Music error (continuing without music): {e}")
        final_video = final_video.set_audio(audio_clip)
    
    print("Adding dynamic captions...")
    caption_clips = create_captions(script_text, final_video.duration, width, height)
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
