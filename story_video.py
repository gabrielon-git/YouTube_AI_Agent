"""Free "character story" videos: talking fruit / animals / objects acting out a short scene.

No paid video model is used. Each character is a cut-out image (generated on a green background and
keyed out), placed on a generated background. The character that is speaking squashes and stretches with the
loudness of its own voice, the camera punches in on every cut, and every character has its own voice.
Everything runs on the free GitHub Actions runner.
"""
import asyncio
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time

import edge_tts
import numpy as np
from google import genai
from google.genai import types
from moviepy.editor import AudioFileClip, CompositeAudioClip, CompositeVideoClip, VideoClip
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import release_tools
import render_video as rv

STORY_FPS = 24
LINE_GAP = 0.18            # silence after each spoken line (seconds)
KEY_THRESH = 100           # how different from the green background a pixel may be and still count as background
CHAR_IMG_SIZE = (768, 1024)

STORY_TOPICS = [
    "A strawberry and a banana argue about which one is healthier",
    "An orange, an apple and a lemon hold a courtroom trial about who is the sourest",
    "A grumpy potato and a cheerful carrot compete in a cooking show",
    "A tired coffee bean tries to convince a sleepy pillow to stay awake",
    "Three vegetables fight over the last spot in the salad bowl",
    "A cat and a goldfish argue over who has the better life",
    "A brain and a heart argue about who is really in charge of the body",
    "A toothbrush gives a dramatic speech to a lazy tooth",
]

# key -> (edge-tts voice, rate, pitch)
VOICE_PRESETS = {
    "squeaky": ("en-US-AnaNeural", "+10%", "+8Hz"),
    "sweet": ("en-US-JennyNeural", "+5%", "+4Hz"),
    "grumpy": ("en-US-ChristopherNeural", "-8%", "-10Hz"),
    "bold": ("en-US-GuyNeural", "+6%", "+0Hz"),
    "smooth": ("en-US-AndrewNeural", "+0%", "+0Hz"),
    "dramatic": ("en-US-BrianNeural", "-4%", "-4Hz"),
    "posh": ("en-GB-SoniaNeural", "+0%", "+0Hz"),
    "wise": ("en-GB-RyanNeural", "-6%", "-8Hz"),
    "cheerful": ("en-US-EmmaNeural", "+8%", "+4Hz"),
}
NEUTRAL_VOICE = ("en-US-AriaNeural", "+0%", "+0Hz")


# ----------------------------------------------------------------------------
# Script
# ----------------------------------------------------------------------------
def build_story_prompt(topic, long_form):
    lines, words = ("22-26", "200-240") if long_form else ("9-12", "70-90")
    return f"""You write viral YouTube Shorts "character story" videos: fruits, animals or everyday objects with
faces talking to each other in one short, funny or dramatic scene.

PREMISE: {topic}

Return JSON with exactly this shape:
{{
  "art_style": "one line describing the visual style used for EVERY image, e.g. cute glossy 3D Pixar-style render, big expressive eyes, soft studio lighting, vibrant colors",
  "characters": [{{"id": "A", "name": "Pippa", "look": "...", "voice": "squeaky"}}],
  "backgrounds": ["...", "..."],
  "scenes": [{{"speaker": "A", "line": "...", "bg": 0}}]
}}

RULES
- 2 or 3 characters that fit the premise. "look" is ONE detailed sentence (shape, colors, face, small accessory).
  It is reused for every image of that character, so be specific. Never use green anywhere on a character
  (no green leaves, stems or clothes), because green is removed from the image to cut the character out.
- "voice" must be one of: squeaky, sweet, grumpy, bold, smooth, dramatic, posh, wise, cheerful.
  Use a different voice for each character, matching their personality.
- "backgrounds": 2 or 3 empty locations with no people, animals or characters in them (one line each).
- "scenes": {lines} lines, {words} spoken words in total. Line 1 is the hook: under 10 words, dropped into the
  middle of a conflict. The conflict escalates, there is a twist, and the last line is a punchline or a
  question that makes people rewatch. Characters speak TO each other and mostly take turns.
- Each line: 14 words or fewer, plain spoken English, no stage directions, no emojis, no names in brackets.
- "bg" is the index of the location for that line. Change location only when the story moves.
- If the premise is about health or science, hide one TRUE, surprising fact in the dialogue.
"""


def generate_story(client, topic, long_form):
    prompt = build_story_prompt(topic, long_form)
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=1.0)

    for model in rv.SCRIPT_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model.strip(), contents=prompt, config=config)
                story = validate_story(json.loads(resp.text))
                if story:
                    print(f"Story OK from {model}: {len(story['characters'])} characters, "
                          f"{len(story['scenes'])} lines")
                    return story
                print(f"{model}: story failed validation, retrying")
            except Exception as e:
                print(f"{model} attempt {attempt + 1} failed: {e}")
            time.sleep(2 + attempt * 2)
    print("ERROR: could not get a valid story from any model")
    sys.exit(1)


def validate_story(data):
    """Returns a cleaned story dict, or None when it cannot be used."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        return None

    chars, seen, used_voices = [], set(), set()
    for c in data.get("characters") or []:
        if not isinstance(c, dict) or not c.get("id") or not c.get("look") or str(c["id"]) in seen:
            continue
        seen.add(str(c["id"]))
        voice = str(c.get("voice", "")).lower()
        if voice not in VOICE_PRESETS or voice in used_voices:  # make sure everyone sounds different
            voice = next((v for v in VOICE_PRESETS if v not in used_voices), "smooth")
        used_voices.add(voice)
        chars.append({"id": str(c["id"]), "name": str(c.get("name") or c["id"]),
                      "look": str(c["look"]).strip(), "voice": voice})
    chars = chars[:3]
    if not chars:
        return None

    bgs = [str(b).strip() for b in (data.get("backgrounds") or []) if str(b).strip()][:3]
    if not bgs:
        bgs = ["a cozy colorful kitchen counter"]

    ids = {c["id"] for c in chars}
    scenes = []
    for s in data.get("scenes") or []:
        if not isinstance(s, dict) or not str(s.get("line", "")).strip():
            continue
        speaker = str(s.get("speaker"))
        if speaker not in ids:
            continue
        try:
            bg = int(s.get("bg", 0))
        except (TypeError, ValueError):
            bg = 0
        scenes.append({"speaker": speaker, "line": " ".join(str(s["line"]).split()),
                       "bg": bg if 0 <= bg < len(bgs) else 0})
    if len(scenes) < 5:
        return None

    return {"art_style": str(data.get("art_style") or "cute glossy 3D Pixar-style render, vibrant colors"),
            "characters": chars, "backgrounds": bgs, "scenes": scenes}


# ----------------------------------------------------------------------------
# Voices
# ----------------------------------------------------------------------------
async def _tts(text, preset, path):
    voice, rate, pitch = preset
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # older edge-tts without the boundary argument
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
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


async def synth_lines(story):
    voices = {c["id"]: VOICE_PRESETS[c["voice"]] for c in story["characters"]}
    out = []
    for i, scene in enumerate(story["scenes"]):
        path = f"line_{i}.mp3"
        attempts = [voices[scene["speaker"]]] * 3 + [NEUTRAL_VOICE] * 2  # fall back to a plain voice rather than fail
        for n, preset in enumerate(attempts):
            try:
                out.append((path, await _tts(scene["line"], preset, path)))
                break
            except Exception as e:
                print(f"TTS line {i} attempt {n + 1}: {e}")
                await asyncio.sleep(2 ** min(n, 3) + random.uniform(0, 1))
        else:
            raise RuntimeError(f"Voice failed for line {i}")
    return out


def loudness_envelope(path, fps, duration):
    """0..1 loudness per video frame, used to make the speaking character bounce."""
    # Decode with ffmpeg directly: moviepy 1.0.3's to_soundarray() crashes on current numpy versions.
    try:
        from moviepy.config import get_setting
        ffmpeg = get_setting("FFMPEG_BINARY")
    except Exception:
        ffmpeg = "ffmpeg"
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    mono = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32)) / 32768.0
    if len(mono) == 0:
        mono = np.zeros(1, dtype=np.float32)
    hop = 16000 / fps
    n = int(math.ceil(duration * fps)) + 2
    env = np.zeros(n)
    for i in range(n):
        a, b = int(i * hop), int((i + 1) * hop)
        if b > a and a < len(mono):
            env[i] = mono[a:b].mean()
    ref = max(float(np.percentile(env, 95)), 1e-6)
    env = np.clip(env / ref, 0, 1)
    for i in range(1, n):  # quick attack, slower release, so it bounces instead of flickering
        env[i] = max(env[i], env[i - 1] * 0.62)
    return env


# ----------------------------------------------------------------------------
# Characters and backgrounds
# ----------------------------------------------------------------------------
def key_out_background(img_path):
    """Remove the flat green background. Returns an RGBA image cropped to the character, or None if it failed."""
    img = Image.open(img_path).convert("RGB")
    w, h = img.size
    work = img.copy()
    marker = (1, 2, 3)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for s in seeds:
        if work.getpixel(s) != marker:
            ImageDraw.floodfill(work, s, marker, thresh=KEY_THRESH)
    bg = (np.array(work) == marker).all(axis=2)
    frac = float(bg.mean())
    if not (0.12 < frac < 0.92):  # nothing removed, or nearly everything removed: keying did not work
        return None

    alpha = Image.fromarray(((~bg) * 255).astype("uint8"))
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    a = np.array(alpha)
    rgb = np.array(img).astype(float)
    edge = (a < 250) & (a > 0)  # remove green spill on the outline only, so green details inside stay intact
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    limit = np.maximum(r, b)
    rgb[..., 1] = np.where(edge & (g > limit), limit + (g - limit) * 0.25, g)
    rgba = Image.fromarray(rgb.clip(0, 255).astype("uint8")).convert("RGBA")
    rgba.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    return rgba.crop(box) if box else None


def rounded_card(img_path):
    """Used when keying fails: keep the whole picture as a rounded card so the character is still visible."""
    img = Image.open(img_path).convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=img.width // 8, fill=255)
    img.putalpha(mask)
    return img


def placeholder_character(name, seed):
    """Always-available fallback if image generation fails completely: a simple blob with eyes."""
    rnd = random.Random(seed)
    color = (rnd.randint(120, 240), rnd.randint(70, 200), rnd.randint(70, 200), 255)
    w, h = 600, 800
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([40, 60, w - 40, h - 20], fill=color, outline=(30, 30, 40, 255), width=10)
    for ex in (w // 2 - 110, w // 2 + 110):
        d.ellipse([ex - 55, 270, ex + 55, 400], fill="white", outline=(30, 30, 40, 255), width=6)
        d.ellipse([ex - 22, 310, ex + 22, 365], fill=(20, 20, 30, 255))
    d.arc([w // 2 - 130, 400, w // 2 + 130, 560], 20, 160, fill=(30, 30, 40, 255), width=12)
    return img


def fit_sprite(sprite, max_h, max_w):
    scale = min(max_h / sprite.height, max_w / sprite.width)
    return sprite.resize((max(1, int(sprite.width * scale)), max(1, int(sprite.height * scale))), Image.LANCZOS)


def make_shadow(width):
    h = max(14, int(width * 0.09))
    sh = Image.new("RGBA", (int(width * 0.9), h * 2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([0, 0, sh.width - 1, sh.height - 1], fill=(0, 0, 0, 90))
    return sh.filter(ImageFilter.GaussianBlur(h / 3))


def prepare_characters(story, seed, w, h, long_form):
    n = len(story["characters"])
    style = story["art_style"]
    max_h = int(h * (0.62 if long_form else (0.36 if n < 3 else 0.30)))
    max_w = int(w * {1: 0.6, 2: 0.42, 3: 0.30}[n])
    sprites = {}
    for i, c in enumerate(story["characters"]):
        release_tools.update(30 + 10 * i / n, f"Drawing {c['name']}")
        path = f"char_{c['id']}.jpg"
        prompt = (f"{c['look']}, {style}, full body, standing upright, facing the camera, centered, "
                  "isolated on a plain solid bright green (#00FF00) background, flat even studio lighting, "
                  "no ground shadow, no props, no other characters")
        cseed = int(hashlib.md5(f"{seed}-{c['id']}".encode()).hexdigest()[:6], 16)  # fixed per character
        print(f"  character {c['name']}: {c['look'][:70]}...")
        got = rv.generate_ai_image(prompt, path, cseed, *CHAR_IMG_SIZE)
        sprite = None
        if got:
            try:
                sprite = key_out_background(got)
                if sprite is None:
                    print("  -> background removal failed, using a rounded card instead")
                    sprite = rounded_card(got)
            except Exception as e:
                print(f"  -> image processing failed ({e}), using placeholder")
        else:
            print("  -> image failed, using placeholder character")
        if sprite is None:
            sprite = placeholder_character(c["name"], cseed)
        sprite = fit_sprite(sprite, max_h, max_w)
        sprites[c["id"]] = (sprite, make_shadow(sprite.width))
        time.sleep(1.5)  # be gentle with the free image API
    return sprites


def prepare_backgrounds(story, seed, w, h):
    out, style = [], story["art_style"]
    for i, desc in enumerate(story["backgrounds"]):
        release_tools.update(40 + 25 * i / len(story["backgrounds"]), f"Painting background {i + 1}")
        path = f"bg_{i}.jpg"
        prompt = (f"{desc}, {style}, empty scene with nobody in it, no characters, no people, no animals, "
                  "soft depth of field, wide open space in the middle")
        got = rv.generate_ai_image(prompt, path, seed + i, w, h)
        if not got:
            print("  -> background failed, using gradient")
            got = rv.make_fallback_image(path, w, h, i)
        img = Image.open(got).convert("RGB")
        scale = max(w / img.width, h / img.height)
        img = img.resize((int(img.width * scale) + 2, int(img.height * scale) + 2), Image.LANCZOS)
        out.append(ImageEnhance.Brightness(img).enhance(0.92))
        time.sleep(1.5)
    return out


# ----------------------------------------------------------------------------
# Animation
# ----------------------------------------------------------------------------
def character_positions(n, w, h, long_form):
    xs = {1: [0.5], 2: [0.27, 0.73], 3: [0.18, 0.5, 0.82]}[n]
    base_y = int(h * (0.80 if long_form else 0.58))
    return [(int(x * w), base_y) for x in xs]


def scene_clip(bg, order, sprites, positions, speaker_id, env, dur, w, h):
    iw, ih = bg.size

    def make_frame(t):
        # camera: quick punch-in on the cut, then a slow push
        z = 1.0 + 0.05 * (t / max(dur, 0.1)) + 0.10 * max(0.0, 1 - t / 0.25) ** 2
        cw, ch = w / z, h / z
        x0, y0 = (iw - cw) / 2, (ih - ch) / 2
        canvas = bg.resize((w, h), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))

        k = min(int(t * STORY_FPS), len(env) - 1)
        for j, cid in enumerate(order):
            sprite, shadow = sprites[cid]
            cx, base_y = positions[j]
            if cid == speaker_id:
                b = float(env[k])
                pop = max(0.0, 1 - t / 0.18)
                s = 1.0 + 0.05 * pop
                sx, sy, bob = s * (1 - 0.07 * b), s * (1 + 0.10 * b), -0.045 * sprite.height * b
            else:  # listeners stay smaller and just breathe
                breathe = 0.012 * math.sin(2 * math.pi * (t * 0.9 + j * 0.31))
                sx, sy, bob = 0.93 * (1 - breathe), 0.93 * (1 + breathe), 0.0
            sw, sh = max(1, int(sprite.width * sx)), max(1, int(sprite.height * sy))
            canvas.paste(shadow, (int(cx - shadow.width / 2), int(base_y - shadow.height / 2)), shadow)
            rs = sprite.resize((sw, sh), Image.BILINEAR)
            canvas.paste(rs, (int(cx - sw / 2), int(base_y - sh + bob)), rs)
        return np.array(canvas)

    return VideoClip(make_frame, duration=dur)


# ----------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------
def build_story_video(topic, video_format):
    release_tools.update(10, "Writing story", name=topic)
    long_form = video_format == "long"
    w, h = (1920, 1080) if long_form else (1080, 1920)

    gem_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not gem_key:
        print("ERROR: Missing GEMINI_API_KEY")
        sys.exit(1)
    story = generate_story(genai.Client(api_key=gem_key), topic, long_form)

    release_tools.update(20, "Recording voices")
    print("Recording character voices...")
    voice_results = asyncio.run(synth_lines(story))

    audio_parts, timed_words, spans = [], [], []
    t = 0.0
    for i, (scene, (path, words)) in enumerate(zip(story["scenes"], voice_results)):
        audio = AudioFileClip(path)
        dur = audio.duration
        if not words:
            toks = scene["line"].split()
            words = [(tok, k * dur / len(toks), dur / len(toks)) for k, tok in enumerate(toks)]
        for text, off, wdur in words:
            timed_words.append((text, t + off, t + off + wdur))
        audio_parts.append(audio.set_start(t))
        spans.append((t, dur + LINE_GAP, path, dur))
        t += dur + LINE_GAP
    total = t
    print(f"Total duration: {total:.1f}s")
    if not long_form and total > 59:
        print("WARNING: over 59s - too long for a YouTube Short. Tighten the script prompt.")

    seed = random.randint(1, 999999)
    # Free image models ignore long style instructions less when the prompt stays plain: drop the photo-style suffix.
    rv.STYLE_SUFFIX = "no text, no letters, no words, no watermark, no logo"
    print("Drawing characters and backgrounds...")
    sprites = prepare_characters(story, seed, w, h, long_form)
    backgrounds = prepare_backgrounds(story, seed + 100, w, h)

    release_tools.update(70, "Animating")
    print("Animating scenes...")
    order = [c["id"] for c in story["characters"]]
    positions = character_positions(len(order), w, h, long_form)
    clips = []
    for scene, (start, dur, path, speech) in zip(story["scenes"], spans):
        env = loudness_envelope(path, STORY_FPS, dur)
        clips.append(scene_clip(backgrounds[scene["bg"]], order, sprites, positions,
                                scene["speaker"], env, dur, w, h).set_start(start))

    release_tools.update(78, "Adding captions")
    print("Building captions...")
    caption_clips = rv.build_caption_clips(timed_words, rv.get_font(), w, h, long_form, total)

    release_tools.update(82, "Mixing audio")
    tracks = list(audio_parts)
    music = rv.get_music_clip(total)
    if music is not None:
        tracks.append(music)
    final_audio = CompositeAudioClip(tracks).set_duration(total)

    release_tools.update(85, "Rendering video")
    print("Rendering final MP4...")
    final = CompositeVideoClip(clips + caption_clips, size=(w, h)).set_duration(total).set_audio(final_audio)
    final.write_videofile("final_video.mp4", fps=STORY_FPS, codec="libx264", audio_codec="aac", preset="veryfast",
                          bitrate="4M", threads=4, ffmpeg_params=["-pix_fmt", "yuv420p"], logger=None)
    print("Done!")
