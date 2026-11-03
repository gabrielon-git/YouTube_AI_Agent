import os
import requests
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont

# 1. Paste your Pixabay API Key here
API_KEY = "58383989-415bf7e6dec023402656a285b"

def fetch_clips(query, count=3):
    print(f"Searching Pixabay for: {query}")
    url = f"https://pixabay.com/api/videos/?key={API_KEY}&q={query}&video_type=film&per_page={count}&orientation=vertical"
    response = requests.get(url)
    data = response.json()
    
    if 'hits' not in data:
        print(f"Pixabay Error: {data}")
        return []
        
    clip_paths = []
    for i, video in enumerate(data['hits']):
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

def create_text_image(text, width=1080, height=1920):
    # This uses Pillow instead of ImageMagick to draw the text
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 80)
    except:
        font = ImageFont.load_default()
        
    # Calculate text position
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = (width - text_width) / 2
    y = (height - text_height) / 2
    
    # Draw text with a black stroke
    draw.text((x, y), text, font=font, fill="white", stroke_width=3, stroke_fill="black")
    
    img_path = "text_overlay.png"
    img.save(img_path)
    return img_path

def create_final_video(topic="Nature"):
    clip_paths = fetch_clips(topic, count=3)
    
    if not clip_paths:
        print("No clips found. Exiting.")
        return

    print("Editing clips and adding transitions...")
    clips = []
    for path in clip_paths:
        try:
            clip = VideoFileClip(path).subclip(0, 3).resize(height=1920, width=1080)
            clip = clip.crossfadein(1).crossfadeout(1)
            clips.append(clip)
        except Exception as e:
            print(f"Skipping {path} due to error: {e}")

    if not clips:
        print("All clips failed to load.")
        return

    final_video = concatenate_videoclips(clips, padding=-1, method="compose")
    
    print("Adding text overlay...")
    text_png = create_text_image(topic)
    # Load the text as an image clip that lasts the whole video
    txt_clip = ImageClip(text_png).set_duration(final_video.duration)
    
    final = CompositeVideoClip([final_video, txt_clip])
    
    print("Rendering high-quality 1080p video...")
    final.write_videofile("final_video.mp4", fps=30, codec="libx264", audio_codec="aac")
    
    print("Done! Video is ready.")

if __name__ == "__main__":
    create_final_video(topic="Nature")
