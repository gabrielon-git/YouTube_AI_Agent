import os
import sys
import io
import time
import json
import random
import asyncio
import urllib.parse

import numpy as np
import requests
import edge_tts
from google import genai
from google.genai import types
from moviepy.editor import (
    VideoClip, ImageClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip,
)
from moviepy.audio.fx.all import audio_loop, volumex, audio_fadeout
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

# ----------------------------------------------------------------------------
# Settings (tweak these, not the logic)
# ----------------------------------------------------------------------------
SCRIPT_MODELS = os.environ.get("GEMINI_MODELS", "gemini-3.8-flash,gemini-3.6-flash").split(",")
FALLBACK_VOICES = ["en-US-JennyNeural", "en-US-GuyNeural"]

FPS = 30
FADE = 0.4              # crossfade between scenes (seconds)
PAD = 0.25              # tiny breath after each sentence (seconds)
DARKEN = 0.78           # 1.0 = no darkening, lower = darker (helps caption contrast)
MUSIC_VOLUME = 0.08     # background music level under the voice
IMAGE_MODEL = "flux"
IMAGE_RETRIES = 4

# Appended to EVERY image prompt so all scenes look like one video
STYLE_SUFFIX = (
    "cinematic lighting, shallow depth of field, fine film grain, rich color grade, "
    "highly detailed, no text, no letters, no words, no watermark, no logo"
)

FONT_URLS = [
    "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-ExtraBold.ttf",
    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
]
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
# Drop your own royalty-free track into the repo as music.mp3 and it will be used first.
MUSIC_URL = os.environ.get("MUSIC_URL", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3")


# ----------------------------------------------------------------------------
# Script (Gemini)
# ----------------------------------------------------------------------------
def build_prompt(topic, long_form):
    if long_form:
        words, scenes, length, kind = "220-260", "12-14", "about 90 seconds", "YouTube video"
    else:
        words, scenes, length, kind = "70-85", "8-10", "about 30 seconds", "YouTube Short"

    return f"""You write viral faceless {kind} scripts ({length}).
Topic: {topic}

SCRIPT RULES
- Sentence 1 is the hook: under 10 words, a surprising claim or a direct question about the viewer.
  Never begin with "Welcome", "Today", "Hey guys" or "Did you know".
- Sentence 2 teases the payoff (e.g. "The last one changes everything.").
- Then 3-4 counter-intuitive, concrete facts (numbers, names, specifics). One idea per sentence.
- Plain spoken English with contractions. Max 14 words per sentence.
- No emojis, hashtags, stage directions or parentheses.
- The final sentence is a sharp question that loops back to the hook, so viewers rewatch.
- Total spoken words: {words}. Exactly {scenes} scenes, one sentence per scene.

IMAGE PROMPT RULES (one per sentence)
- 25-45 words describing ONE concrete visual that illustrates that sentence.
- Use this 4-part formula: Subject (tactile, warm, orderly or nostalgic); Contrast (dramatic directional
  light, rim light, deep shadows); Texture (matte, weathered, glossy, film grain); Framing (macro close-up,
  rule of thirds, shallow depth of field).
- Pick ONE vibe for the whole video and keep it for every scene: "Nostalgic Macro",
  "Cyber-Zen Minimalist" or "Cinematic Cozy".
- Prefer objects, environments and silhouettes. Avoid close-up faces and hands (AI draws them badly).
- Never ask for text, signs, letters or logos in the image.

Return a JSON array of objects with exactly two keys: "sentence" and "image_prompt"."""


def generate_scenes(client, topic, long_form):
    prompt = build_prompt(topic, long_form)
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.9)

    for model in SCRIPT_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model.strip(), contents=prompt, config=config)
                data = json.loads(resp.text)
                if isinstance(data, dict):  # model wrapped the array in an object
                    data = next((v for v in data.values() if isinstance(v, list)), [])
                scenes = [
                    {"sentence": str(s["sentence"]).strip(), "image_prompt": str(s["image_prompt"]).strip()}
                    for s in data
                    if isinstance(s, dict) and s.get("sentence") and s.get("image_prompt")
                ]
                if len(scenes) >= 5:
                    print(f"Script OK from {model}: {len(scenes)} scenes")
                    return scenes
                print(f"{model}: too few valid scenes ({len(scenes)}), retrying")
            except Exception as e:
                print(f"{model} attempt {attempt + 1} failed: {e}")
            time.sleep(2 + attempt * 2)
    print("ERROR: could not get a valid script from any model")
    sys.exit(1)


# ----------------------------------------------------------------------------
# Voice (Edge-TTS) - one clip per sentence, with real word timings
# ----------------------------------------------------------------------------
async def _tts_once(text, voice, path):
    try:
        comm = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    except TypeError:  # older edge-tts without the boundary argument
        comm = edge_tts.Communicate(text, voice)
    words = []
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["text"], chunk["offset"] / 1e7, chunk["duration"] / 1e7))
    if os.path.getsize(path) < 1000:
        raise RuntimeError("audio too small")
    return words


async def synth_all(scenes, voice):
    voices = [voice] + [v for v in FALLBACK_VOICES if v != voice]
    results = []
    for i, scene in enumerate(scenes):
        path = f"voice_{i}.mp3"
        done = False
        for v in list(voices):
            for attempt in range(4):
                try:
                    words = await _tts_once(scene["sentence"], v, path)
                    voices = [v] + [x for x in voices if x != v]  # stick with what works
                    results.append((path, words))
                    done = True
                    break
                except Exception as e:
                    print(f"TTS scene {i} voice {v} attempt {attempt + 1}: {e}")
                    await asyncio.sleep((2 ** attempt) + random.uniform(0, 1))
            if done:
                break
        if not done:
            raise RuntimeError(f"Voiceover failed for scene {i}")
    return results


# ----------------------------------------------------------------------------
# Images (Pollinations)
# ----------------------------------------------------------------------------
def generate_ai_image(prompt, filename, seed, w, h):
    full_prompt = f"{prompt}, {STYLE_SUFFIX}"
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(full_prompt)
    params = {"width": w, "height": h, "nologo": "true", "seed": seed, "model": IMAGE_MODEL}

    for attempt in range(IMAGE_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=90)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                Image.open(io.BytesIO(r.content)).verify()  # make sure it's a real image
                with open(filename, "wb") as f:
                    f.write(r.content)
                return filename
            print(f"Image attempt {attempt + 1}: HTTP {r.status_code}")
        except Exception as e:
            print(f"Image attempt {attempt + 1} failed: {e}")
        time.sleep(3 * (attempt + 1) + random.uniform(0, 2))
    return None


def make_fallback_image(filename, w, h, idx):
    """Dark gradient so a failed image still looks intentional."""
    palettes = [((12, 14, 28), (45, 30, 70)), ((10, 24, 30), (30, 60, 70)), ((28, 16, 12), (80, 45, 30))]
    top, bottom = palettes[idx % len(palettes)]
    t = np.linspace(0, 1, h)[:, None, None]
    arr = (np.array(top) * (1 - t) + np.array(bottom) * t).astype("uint8")
    arr = np.repeat(arr, w, axis=1)
    Image.fromarray(arr).save(filename)
    return filename


# ----------------------------------------------------------------------------
# Visuals
# ----------------------------------------------------------------------------
def ken_burns(image_path, duration, w, h, idx):
    """Slow zoom + drift, alternating direction per scene. Cropped from the source, never stretched."""
    img = Image.open(image_path).convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 2, int(img.height * scale) + 2), Image.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(DARKEN)
    iw, ih = img.size

    z0, z1 = (1.0, 1.16) if idx % 2 == 0 else (1.16, 1.0)
    drift = 1 if idx % 3 != 0 else -1

    def make_frame(t):
        p = min(max(t / duration, 0.0), 1.0)
        z = z0 + (z1 - z0) * p
        cw, ch = w / z, h / z
        x0 = (iw - cw) / 2 + drift * (iw - cw) * 0.4 * (p - 0.5)
        y0 = (ih - ch) / 2
        frame = img.resize((w, h), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
        return np.array(frame)

    return VideoClip(make_frame, duration=duration)


def get_font():
    path = "Montserrat-ExtraBold.ttf"

    def usable(p):
        try:
            ImageFont.truetype(p, 40)
            return True
        except Exception:
            return False

    if os.path.exists(path) and usable(path):
        return path
    for url in FONT_URLS:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and len(r.content) > 20000:
                with open(path, "wb") as f:
                    f.write(r.content)
                if usable(path):
                    return path
                os.remove(path)
        except Exception as e:
            print(f"Font download failed: {e}")
    for p in FALLBACK_FONTS:
        if os.path.exists(p) and usable(p):
            print(f"Using fallback font {p}")
            return p
    raise RuntimeError("No usable font found")


def clean_word(word):
    word = (word.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2014", "-").replace("\u2013", "-"))
    return word.encode("ascii", "ignore").decode("ascii").strip().upper()


def render_caption(group, active, font_path, w, long_form):
    """One caption frame: a group of words with the active one highlighted. Auto-shrinks to fit the frame."""
    max_w = int(w * (0.7 if long_form else 0.88))
    size = 100 if long_form else 150
    measure = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    while True:
        font = ImageFont.truetype(font_path, size)
        stroke = max(4, size // 15)
        space = measure.textlength(" ", font=font)
        widths = [measure.textlength(wd, font=font) for wd in group]
        total = sum(widths) + space * (len(group) - 1)
        if total + stroke * 2 <= max_w or size <= 44:
            break
        size -= 6

    bbox = measure.textbbox((0, 0), "HQgy", font=font, stroke_width=stroke)
    text_h = bbox[3] - bbox[1]
    pad = stroke * 2 + 12
    img = Image.new("RGBA", (int(total) + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = pad
    for i, wd in enumerate(group):
        color = (255, 214, 10, 255) if i == active else (255, 255, 255, 255)
        draw.text((x + 5, pad - bbox[1] + 5), wd, font=font, fill=(0, 0, 0, 200))  # drop shadow
        draw.text((x, pad - bbox[1]), wd, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        x += widths[i] + space
    return np.array(img)


def build_caption_clips(timed_words, font_path, w, h, long_form, total):
    """timed_words: list of (text, start, end) in absolute seconds. Groups of 3, one frame per spoken word."""
    words = [(clean_word(t), s, e) for t, s, e in timed_words]
    words = [x for x in words if x[0]]
    y = int(h * (0.78 if long_form else 0.62))
    clips = []

    for g in range(0, len(words), 3):
        group = words[g:g + 3]
        texts = [x[0] for x in group]
        for i, (_, start, end) in enumerate(group):
            nxt = words[g + i + 1][1] if g + i + 1 < len(words) else total
            stop = min(nxt, end + 0.4, total)
            if stop - start < 0.05:
                continue
            frame = render_caption(texts, i, font_path, w, long_form)
            clips.append(ImageClip(frame).set_start(start).set_duration(stop - start).set_position(("center", y)))
    return clips


# ----------------------------------------------------------------------------
# Music
# ----------------------------------------------------------------------------
def get_music_clip(total):
    path = "music.mp3"
    if not os.path.exists(path):
        try:
            r = requests.get(MUSIC_URL, timeout=20)
            if r.status_code != 200 or len(r.content) < 50000:
                raise RuntimeError(f"bad music download (HTTP {r.status_code})")
            with open("bg_music.mp3", "wb") as f:
                f.write(r.content)
            path = "bg_music.mp3"
        except Exception as e:
            print(f"Music skipped: {e}")
            return None
    try:
        music = AudioFileClip(path)
        music = audio_loop(music, duration=total) if music.duration < total else music.subclip(0, total)
        return audio_fadeout(volumex(music, MUSIC_VOLUME), 1.5)
    except Exception as e:
        print(f"Music skipped: {e}")
        return None


# ----------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------
def build_video(topic, video_format, voice):
    long_form = video_format == "long"
    w, h = (1920, 1080) if long_form else (1080, 1920)

    gem_key = os.environ.get("GEMINI_API_KEY")
    if not gem_key:
        print("ERROR: Missing GEMINI_API_KEY")
        sys.exit(1)
    client = genai.Client(api_key=gem_key)

    scenes = generate_scenes(client, topic, long_form)

    print("Generating voice (per sentence)...")
    voice_results = asyncio.run(synth_all(scenes, voice))

    # Timeline: each scene lasts exactly as long as its sentence is spoken
    audio_parts, timed_words, scene_spans = [], [], []
    t = 0.0
    for scene, (path, words) in zip(scenes, voice_results):
        audio = AudioFileClip(path)
        dur = audio.duration
        if not words:  # no word timings came back -> spread words evenly
            toks = scene["sentence"].split()
            words = [(tok, i * dur / len(toks), dur / len(toks)) for i, tok in enumerate(toks)]
        for text, off, wdur in words:
            timed_words.append((text, t + off, t + off + wdur))
        audio_parts.append(audio.set_start(t))
        scene_spans.append((t, dur + PAD))
        t += dur + PAD
    total = t
    print(f"Total duration: {total:.1f}s")
    if not long_form and total > 59:
        print("WARNING: over 59s - too long for a YouTube Short. Tighten the script prompt.")

    print("Generating AI images...")
    seed = random.randint(1, 999999)  # one seed per video keeps the look consistent
    scene_clips = []
    for i, (scene, (start, dur)) in enumerate(zip(scenes, scene_spans)):
        img_path = f"scene_{i}.jpg"
        print(f"  scene {i + 1}/{len(scenes)}: {scene['image_prompt'][:70]}...")
        result = generate_ai_image(scene["image_prompt"], img_path, seed, w, h)
        if not result:
            print("  -> using fallback gradient")
            result = make_fallback_image(img_path, w, h, i)

        extra = FADE if i < len(scenes) - 1 else 0.0  # tail overlaps the next scene for the crossfade
        clip = ken_burns(result, dur + extra, w, h, i).set_start(start)
        if i > 0:
            clip = clip.add_mask().crossfadein(FADE)
        scene_clips.append(clip)
        time.sleep(1.5)  # be gentle with the free image API

    print("Building captions...")
    caption_clips = build_caption_clips(timed_words, get_font(), w, h, long_form, total)

    print("Mixing audio...")
    tracks = list(audio_parts)
    music = get_music_clip(total)
    if music is not None:
        tracks.append(music)
    final_audio = CompositeAudioClip(tracks).set_duration(total)

    print("Rendering final MP4...")
    final = CompositeVideoClip(scene_clips + caption_clips, size=(w, h)).set_duration(total)
    final = final.set_audio(final_audio)
    final.write_videofile(
        "final_video.mp4",
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="veryfast",
        bitrate="8M",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p"],
        logger=None,
    )
    print("Done!")


if __name__ == "__main__":
    topic = os.environ.get("VIDEO_TOPIC")
    video_format = os.environ.get("VIDEO_FORMAT", "short")
    voice = os.environ.get("VIDEO_VOICE", "en-US-AriaNeural")

    if not topic:
        print("ERROR: VIDEO_TOPIC is empty")
        sys.exit(1)

    build_video(topic, video_format, voice)
