import os
import sys
import time
import asyncio
import requests
import edge_tts
from google import genai
from moviepy.editor import VideoFileClip, concatenate_videoclips, TextClip, CompositeVideoClip, AudioFileClip, ColorClip

# 1. Set up Keys
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not PIXABAY_KEY or not GEMINI_KEY:
    print("ERROR: Missing API Keys in Secrets!")
    sys.exit(1)

# 2. Generate Script with Retry
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

# 3. Generate Voiceover
def generate_voiceover(text):
    async def _generate():
        communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
        await communicate.save("voiceover.mp3")
    asyncio.run(_generate())
    return "voiceover.mp3"

# 4. Fetch B-Roll
def fetch_clips(query, count=3):
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

# 5. Build the Video (Simplified and Stable)
def build_video(topic):
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"Write a highly engaging, fast-paced 30-second YouTube Short script about: {topic}. ONLY return the spoken text, no visual cues."
    
    script_text = generate_script_with_retry(client, prompt)
    if not script_text:
        sys.exit(1)
        
    audio_path = generate_voiceover(script_text)
    clip_paths = fetch_clips(topic)
    
    if not clip_paths:
        sys.exit(1)

    print("Editing clips...")
    clips = []
    for path in clip_paths:
        # Resize to standard vertical height, remove crossfades to prevent black screen
        clip = VideoFileClip(path).subclip(0, 3).resize(height=1280)
        clips.append(clip)

    print("Combining clips...")
    # Simple concatenation without padding
    final_video = concatenate_videoclips(clips, method="compose")
    
    print("Adding text...")
    # Draw text directly using moviepy (much more stable than PNG overlays)
    try:
        txt_clip = (TextClip(topic, fontsize=70, color='white', font='DejaVu-Sans-Bold', stroke_color='black', stroke_width=2)
                    .set_position('center')
                    .set_duration(final_video.duration))
        
        final = CompositeVideoClip([final_video, txt_clip])
    except Exception as e:
        print(f"Text overlay failed, rendering without text. Error: {e}")
        final = final_video

    print("Adding audio...")
    audio_clip = AudioFileClip(audio_path)
    
    # Loop video if it's shorter than the audio
    if final.duration < audio_clip.duration:
        final = final.loop(duration=audio_clip.duration)
    else:
        final = final.subclip(0, audio_clip.duration)

    final = final.set_audio(audio_clip)
    
    print("Rendering final MP4...")
    final.write_videofile("final_video.mp4", fps=24, codec="libx264", audio_codec="aac", preset='ultrafast', logger=None)
    print("Done!")

if __name__ == "__main__":
    build_video("Ocean")
