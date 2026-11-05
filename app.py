import asyncio
import os
from flask import Flask, render_template, request
from moviepy import TextClip, CompositeVideoClip, AudioFileClip, ColorClip
import edge_tts
from orchestrator import run_orchestrator

app = Flask(__name__)

async def generate_voiceover(text, output_filename="temp_voice.mp3"):
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    await communicate.save(output_filename)

def create_video_from_script(script_text, output_path="static/output_video.mp4"):
    audio_file = "temp_voice.mp3"
    asyncio.run(generate_voiceover(script_text, audio_file))
    
    if not os.path.exists(audio_file):
        raise Exception("Failed to generate voiceover audio.")

    audio_clip = AudioFileClip(audio_file)
    duration = audio_clip.duration

    background = ColorClip(size=(1080, 1920), color=(15, 15, 25), duration=duration)
    txt_clip = TextClip(text=script_text[:100] + "...", font_size=50, color='white', size=(900, None), method='caption')
    txt_clip = txt_clip.with_position('center').with_duration(duration)

    video = CompositeVideoClip([background, txt_clip])
    video = video.with_audio(audio_clip)

    os.makedirs("static", exist_ok=True)
    video.write_videofile(output_path, fps=24, codec='libx264', audio_codec='aac')
    
    audio_clip.close()
    background.close()
    if os.path.exists(audio_file):
        os.remove(audio_file)
        
    return output_path

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    topic = request.form.get('topic')
    orchestrator_output = run_orchestrator(topic)
    
    # Compile the orchestrator text into a real MP4 video
    video_path = create_video_from_script(str(orchestrator_output))
    
    return render_template('result.html', video_url="/" + video_path, script=orchestrator_output)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
