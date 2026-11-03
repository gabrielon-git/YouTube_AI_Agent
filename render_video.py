import os
import requests
from moviepy.editor import VideoFileClip, concatenate_videoclips, TextClip, CompositeVideoClip

# 1. Paste your Pixabay API Key here
API_KEY = "58383989-415bf7e6dec023402656a285b"

def fetch_clips(query, count=3):
    # Pixabay video search API
    url = f"https://pixabay.com/api/videos/?key={API_KEY}&q={query}&video_type=film&per_page={count}&orientation=vertical"
    response = requests.get(url)
    data = response.json()
    
    clip_paths = []
    for i, video in enumerate(data.get('hits', [])):
        # Pixabay returns different video sizes. We want 'medium' or 'large' to save memory.
        video_files = video['videos']
        file_url = video_files.get('medium', {}).get('url') or video_files.get('large', {}).get('url')
        
        if not file_url:
            continue
            
        path = f"clip_{i}.mp4"
        print(f"Downloading {path}...")
        with open(path, 'wb') as f:
            f.write(requests.get(file_url).content)
        clip_paths.append(path)
    return clip_paths

def create_final_video(topic="Nature"):
    print("Fetching stock footage from Pixabay...")
    clip_paths = fetch_clips(topic, count=3)
    
    if not clip_paths:
        print("No clips found. Please try a different topic.")
        return

    print("Editing clips and adding transitions...")
    clips = []
    for path in clip_paths:
        # Trim to 3 seconds and resize to 1080x1920 (TikTok/Shorts format)
        clip = VideoFileClip(path).subclip(0, 3).resize(height=1920, width=1080)
        # Add 1-second fade transitions
        clip = clip.crossfadein(1).crossfadeout(1)
        clips.append(clip)

    # Combine all clips together
    final_video = concatenate_videoclips(clips, padding=-1, method="compose")
    
    # Add Text Overlay
    print("Adding text overlay...")
    txt_clip = TextClip(topic, fontsize=70, color='white', font='DejaVu-Sans-Bold', stroke_color='black', stroke_width=2)
    txt_clip = txt_clip.set_position('center').set_duration(final_video.duration)

    # Layer text over video
    final = CompositeVideoClip([final_video, txt_clip])
    
    print("Rendering high-quality 1080p video...")
    final.write_videofile("final_video.mp4", fps=30, codec="libx264", audio_codec="aac")
    
    print("Done! Video is ready.")

if __name__ == "__main__":
    # You can change "Nature" to any topic you want to test
    create_final_video(topic="Nature")
