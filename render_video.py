import os
import requests
from moviepy.editor import VideoFileClip, concatenate_videoclips, TextClip, CompositeVideoClip
from moviepy.video.fx.all import crossfadein, crossfadeout

# 1. Get Pexels API Key
API_KEY = os.environ.get("PEXELS_API_KEY")
if not API_KEY:
    raise ValueError("Missing PEXELS_API_KEY environment variable.")

# 2. Search and download HD video clips
def fetch_clips(query, count=3):
    url = f"https://api.pexels.com/videos/search?query={query}&per_page={count}&orientation=portrait"
    headers = {"Authorization": API_KEY}
    response = requests.get(url, headers=headers)
    data = response.json()
    
    clip_paths = []
    for i, video in enumerate(data.get('videos', [])):
        # Find an HD file link
        video_files = video['video_files']
        file_url = video_files[0]['link']
        
        path = f"clip_{i}.mp4"
        print(f"Downloading {path}...")
        with open(path, 'wb') as f:
            f.write(requests.get(file_url).content)
        clip_paths.append(path)
    return clip_paths

# 3. Build the Video
def create_final_video(topic="Nature"):
    print("Fetching stock footage...")
    clip_paths = fetch_clips(topic, count=3)
    
    if not clip_paths:
        print("No clips found. Exiting.")
        return

    print("Editing clips and adding transitions...")
    clips = []
    for path in clip_paths:
        # Load clip, resize to 1080x1920 (TikTok/Shorts format), and trim to 3 seconds
        clip = VideoFileClip(path).subclip(0, 3).resize(height=1920, width=1080)
        clip = clip.crossfadein(1).crossfadeout(1) # Add smooth transitions
        clips.append(clip)

    # Combine all clips together
    final_video = concatenate_videoclips(clips, padding=-1, method="compose")
    
    # Add Text Overlay
    print("Adding text overlay...")
    txt_clip = TextClip(topic, fontsize=70, color='white', font='DejaVu-Sans-Bold', stroke_color='black', stroke_width=2)
    txt_clip = txt_clip.set_position('center').set_duration(final_video.duration)

    # Layer text over video
    final = CompositeVideoClip([final_video, txt_clip])
    
    print("Rendering high-quality video...")
    final.write_videofile("final_video.mp4", fps=30, codec="libx264", audio_codec="aac")
    
    print("Done! Video is ready.")

if __name__ == "__main__":
    # You can change "Nature" to whatever topic you want to test
    create_final_video(topic="Nature")
