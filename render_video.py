import os
import sys
import requests
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont

# Safely get the API key from GitHub Secrets
API_KEY = "58383989-415bf7e6dec023402656a285b"
if not API_KEY:
    print("ERROR: PIXABAY_API_KEY is not set in GitHub Secrets!")
    sys.exit(1)

def fetch_clips(query, count=2):
    print(f"Searching Pixabay for: {query}")
    url = f"https://pixabay.com/api/videos/?key={API_KEY}&q={query}&video_type=film&per_page={count}&orientation=vertical"
    response = requests.get(url)
    
    # --- THIS IS THE NEW ERROR CHECK ---
    if response.status_code != 200:
        print(f"Pixabay API Error: Status Code {response.status_code}")
        print(f"Response Text: {response.text[:500]}") # Print the first 500 characters of the error
        sys.exit(1) # Stop the script so we can see the error
        
    data = response.json()
    
    clip_paths = []
    for i, video in enumerate(data.get('hits', [])):
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

def create_text_image(text, width=720, height=1280):
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 60)
    except:
        font = ImageFont.load_default()
        
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = (width - text_width) / 2
    y = (height - text_height) / 2
    
    draw.text((x, y), text, font=font, fill="white", stroke_width=3, stroke_fill="black")
    
    img_path = "text_overlay.png"
    img.save(img_path)
    return img_path

def create_final_video(topic="Ocean"):
    try:
        clip_paths = fetch_clips(topic, count=2)
        
        if not clip_paths:
            print("No clips found. Exiting.")
            sys.exit(1)

        print("Editing clips and adding transitions...")
        clips = []
        for path in clip_paths:
            clip = VideoFileClip(path).subclip(0, 3).resize(height=1280, width=720)
            clip = clip.crossfadein(0.5).crossfadeout(0.5)
            clips.append(clip)

        print("Combining clips...")
        final_video = concatenate_videoclips(clips, padding=-0.5, method="compose")
        
        print("Adding text overlay...")
        text_png = create_text_image(topic)
        txt_clip = ImageClip(text_png).set_duration(final_video.duration)
        
        final = CompositeVideoClip([final_video, txt_clip])
        
        print("Rendering video NOW...")
        final.write_videofile("final_video.mp4", fps=24, codec="libx264", audio=False, preset='ultrafast', logger=None)
        
        print("Done! Video is ready.")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    create_final_video(topic="Ocean")
