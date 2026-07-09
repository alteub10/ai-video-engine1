import os
import gc
import json
import random
import shutil
import subprocess
import time
import requests
import re
import logging
from moviepy.editor import (
    VideoFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, 
    AudioFileClip, vfx, CompositeAudioClip
)
from faster_whisper import WhisperModel

# =================================================================
# [0] Fix changelog (read me first)
# =================================================================
# Root cause of the "Media type 'text/html' is not supported" / YouTube
# Uploader "Bad request" error in n8n:
#   1) tmpfiles.org deletes files after 60 minutes BY DEFAULT. The old
#      upload call never sent the `expire` field, so every render used
#      the shortest possible TTL. Any delay in the pipeline (webhook
#      queueing, retries, n8n cold start on Render.com free tier, etc.)
#      could push the download past that 60-minute window.
#   2) When a tmpfiles.org link is dead, it returns a small HTML page
#      (not a 404 with a clear video/absent body) instead of the MP4.
#      The old script trusted ANY link returned by the upload API
#      without ever confirming the link actually served a video, so a
#      dead/blocked link was passed to n8n and then to YouTube as if it
#      were valid.
#
# What changed below:
#   - tmpfiles.org upload now requests a 6-hour TTL instead of the
#     default 60 minutes.
#   - New verify_remote_file() re-fetches every candidate link right
#     after upload and rejects it if it's HTML/JSON or clearly too
#     small, BEFORE the script ever reports success.
#   - The 3-provider fallback (Uguu.se -> tmpfiles.org -> Catbox.moe)
#     now loops through providers until one is verified, instead of
#     stopping at the first URL returned.
#   - download_with_retry() also sniffs the first bytes of anything it
#     downloads so an HTML error page can never silently be saved as if
#     it were audio/video (this protects AUDIO_URL / VIDEO_URLS inputs
#     from the same failure class).
# =================================================================

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

logger.info("Initializing AI Video Engine [Production Version]...")

def clean_old_files():
    """Removes leftover files from previous failed runs to prevent corruption."""
    files_to_remove = ["audio.mp3", "subs.srt", "temp_base.mp4", "final_shorts.mp4", "subscribe_anim.mp4"]
    for f in files_to_remove:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception as e:
                logger.warning(f"Could not remove {f}: {e}")
    for f in os.listdir('.'):
        if f.startswith("video_") and f.endswith(".mp4"):
            try:
                os.remove(f)
            except Exception:
                pass

clean_old_files()

open_clips = []
def track_clip(clip):
    if clip is not None:
        open_clips.append(clip)
    return clip

# =================================================================
# [1] Variables & Data Ingestion
# =================================================================
target_w = 1080
target_h = 1920
cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 4.0))
topic_name = os.environ.get("TOPIC_NAME", "unknown")

raw_hook = os.environ.get("HOOK_TEXT", "CLASSIFIED ARCHIVE")
hook_text = re.sub(r'[^\w\s]', '', raw_hook).strip()

audio_url = os.environ.get("AUDIO_URL", "")
video_urls_raw = os.environ.get("VIDEO_URLS", "[]")

try:
    video_urls = json.loads(video_urls_raw)
    if not isinstance(video_urls, list):
        raise ValueError("VIDEO_URLS JSON is not a list")
except Exception:
    clean_raw = video_urls_raw.replace("|", ",")
    video_urls = [url.strip() for url in clean_raw.split(",") if url.strip()]

if not audio_url:
    logger.error("FATAL: AUDIO_URL not provided. Exiting.")
    exit(1)

if not video_urls:
    logger.error("FATAL: No VIDEO_URLS provided. Exiting.")
    exit(1)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# =================================================================
# Robust Unified Downloader
# =================================================================
def download_with_retry(url, dest_path, attempts=3, timeout=30, stream=False):
    for attempt in range(1, attempts + 1):
        try:
            res = requests.get(url, headers=headers, timeout=timeout, stream=stream)
            res.raise_for_status()
            with open(dest_path, 'wb') as f:
                if stream:
                    for chunk in res.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                else:
                    f.write(res.content)

            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024:
                # 🔧 FIX: a >1KB file used to be trusted blindly. An HTML
                # error/expired page from a free host is easily >1KB, so
                # sniff the first bytes before accepting the download.
                with open(dest_path, 'rb') as check_f:
                    head_bytes = check_f.read(512).lower()
                if b"<html" in head_bytes or b"<!doctype html" in head_bytes:
                    logger.warning(
                        f"Downloaded file looks like an HTML page, not media: "
                        f"{dest_path} (Attempt {attempt}/{attempts})"
                    )
                    os.remove(dest_path)
                else:
                    return True
            else:
                logger.warning(f"Downloaded file empty or corrupted: {dest_path} (Attempt {attempt}/{attempts})")
        except Exception as e:
            logger.warning(f"Download attempt {attempt}/{attempts} failed for {url}: {str(e)}")

        if attempt < attempts:
            time.sleep(2)

    if os.path.exists(dest_path):
        os.remove(dest_path)
    return False


def verify_remote_file(url, expected_min_bytes=200_000, timeout=20):
    """
    🔧 NEW: Re-fetches a just-uploaded URL and confirms it actually
    serves the video before the pipeline trusts it. Returns False if
    the host answers with HTML/JSON (expired link, anti-bot page,
    error page) or with a suspiciously small body.
    """
    try:
        with requests.get(url, headers=headers, timeout=timeout, stream=True) as res:
            if res.status_code != 200:
                logger.warning(f"Verify failed: HTTP {res.status_code} for {url}")
                return False

            content_type = res.headers.get("Content-Type", "").lower()
            if "html" in content_type or "json" in content_type:
                logger.warning(f"Verify failed: server returned '{content_type}' instead of a video for {url}")
                return False

            content_length = int(res.headers.get("Content-Length", 0) or 0)
            if content_length and content_length < expected_min_bytes:
                logger.warning(f"Verify failed: remote file too small ({content_length} bytes) for {url}")
                return False

            if not content_length:
                first_chunk = next(res.iter_content(chunk_size=4096), b"")
                if b"<html" in first_chunk.lower() or b"<!doctype" in first_chunk.lower():
                    logger.warning(f"Verify failed: response body looks like HTML for {url}")
                    return False

            return True
    except Exception as e:
        logger.warning(f"Verify request failed for {url}: {e}")
        return False

# =================================================================
# [2] Main Audio Processing
# =================================================================
audio_path = "audio.mp3"
logger.info(f"Downloading main voiceover: {audio_url}")

if not download_with_retry(audio_url, audio_path, attempts=3):
    logger.error("FATAL: audio.mp3 could not be downloaded. Exiting.")
    exit(1)

try:
    main_audio = track_clip(AudioFileClip(audio_path))
except Exception as e:
    logger.error(f"FATAL: Failed to load downloaded audio.mp3: {e}")
    exit(1)

# =================================================================
# [3] Smart Background Music Mixer
# =================================================================
run_id = int(os.environ.get("GITHUB_RUN_NUMBER", random.randint(1, 1000)))
all_music = [f for f in os.listdir('.') if f.endswith(('.mp3', '.wav')) and f != "audio.mp3"]

bg_music = None
if all_music:
    selected_music = all_music[run_id % len(all_music)]
    logger.info(f"Mixing with background music: {selected_music}")
    try:
        raw_bg = track_clip(AudioFileClip(selected_music))
        bg_music = raw_bg.volumex(0.12).subclip(0, min(raw_bg.duration, main_audio.duration))
        final_audio = track_clip(CompositeAudioClip([main_audio, bg_music.set_start(0)]))
    except Exception as e:
        logger.warning(f"Failed to mix music, playing raw voiceover: {e}")
        final_audio = main_audio
else:
    logger.info("No background music found. Proceeding with voiceover only.")
    final_audio = main_audio

total_audio_time = final_audio.duration
if total_audio_time <= 0:
    logger.error("FATAL: Audio duration is 0. Exiting.")
    exit(1)

# =================================================================
# [4] Faster-Whisper Silent Subtitles
# =================================================================
logger.info("Running Faster-Whisper AI for Transcribing...")
try:
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=True)

    with open("subs.srt", "w", encoding="utf-8") as f:
        sub_idx = 1
        for segment in segments:
            if not segment.words:
                continue

            chunk_size = 2
            for i in range(0, len(segment.words), chunk_size):
                chunk = segment.words[i:i + chunk_size]
                start_time = chunk[0].start
                end_time = chunk[-1].end

                raw_text = " ".join([w.word for w in chunk])
                clean_text = re.sub(r'[^\w\s]', '', raw_text).strip()

                if not clean_text:
                    continue

                def fmt_time(t):
                    h, m = int(t // 3600), int((t % 3600) // 60)
                    s, ms = int(t % 60), int((t % 1) * 1000)
                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

                f.write(f"{sub_idx}\n{fmt_time(start_time)} --> {fmt_time(end_time)}\n{clean_text}\n\n")
                sub_idx += 1

    logger.info("Subtitles 'subs.srt' generated perfectly.")
except Exception as e:
    logger.warning(f"Faster-Whisper failed or skipped: {e}. Moving forward safely without subs.")

# =================================================================
# [5] B-Roll Safe Processing Engine
# =================================================================
def process_clip_safely(filename, target_duration):
    clip = VideoFileClip(filename).without_audio()
    if clip.duration is None or clip.duration <= 0:
        clip.close()
        raise ValueError(f"Clip {filename} has invalid/zero duration")

    if clip.duration < target_duration:
        clip = clip.fx(vfx.loop, duration=target_duration)
    else:
        clip = clip.subclip(0, target_duration)

    w, h = clip.size
    target_ratio = target_w / target_h

    if (w / h) > target_ratio:
        clip = clip.resize(height=target_h)
        clip = clip.crop(x_center=clip.w / 2, width=target_w)
    else:
        clip = clip.resize(width=target_w)
        clip = clip.crop(y_center=clip.h / 2, height=target_h)

    clip = clip.fx(vfx.colorx, 0.90)
    return clip

# =================================================================
# [6] Dynamic Timeline Construction
# =================================================================
downloaded_files = []
logger.info(f"Downloading {len(video_urls)} background video clips...")
for idx, url in enumerate(video_urls):
    v_path = f"video_{idx}.mp4"
    if download_with_retry(url, v_path, attempts=2, timeout=30, stream=True):
        downloaded_files.append(v_path)
    else:
        logger.warning(f"Failed to download video clip {idx} after retries.")

if not downloaded_files:
    logger.error("FATAL: No background videos downloaded. Exiting.")
    exit(1)

final_clips = []
accumulated_duration = 0.0
pool_index = 0
max_total_attempts = len(downloaded_files) * 20
attempts_done = 0

logger.info("Building Core Video Timeline based on actual duration...")

while accumulated_duration < total_audio_time and attempts_done < max_total_attempts:
    filename = downloaded_files[pool_index % len(downloaded_files)]
    pool_index += 1
    attempts_done += 1

    time_left = total_audio_time - accumulated_duration
    duration = min(cut_duration, time_left)

    try:
        clip = process_clip_safely(filename, duration)
        final_clips.append(clip)
        track_clip(clip)
        accumulated_duration += duration
    except Exception as e:
        logger.warning(f"Error processing {filename}, skipping this slot: {e}")

if not final_clips:
    logger.error("FATAL: All video clips failed to process. Exiting.")
    exit(1)

video_track = track_clip(concatenate_videoclips(final_clips, method="compose"))
clips_to_composite = [video_track]

# --- Hook Overlay ---
try:
    if hook_text:
        hook_clip = TextClip(
            hook_text, fontsize=110, color='orange', font='Liberation-Sans-Bold',
            stroke_color='black', stroke_width=5, method='caption', size=(1000, None)
        )
        hook_clip = hook_clip.set_position(('center', 200)).set_duration(min(3.0, total_audio_time)).set_start(0)
        clips_to_composite.append(track_clip(hook_clip))
except Exception as e:
    logger.warning(f"Failed to render hook text overlay, skipping: {e}")

# --- Animated Subscribe Button ---
subscribe_url = "https://files.catbox.moe/oarfxq.mp4"
subscribe_file = "subscribe_anim.mp4"

logger.info("Preparing Animated Subscribe Button...")
if not os.path.exists(subscribe_file):
    download_with_retry(subscribe_url, subscribe_file, attempts=2, stream=True)

if os.path.exists(subscribe_file) and os.path.getsize(subscribe_file) > 1024:
    try:
        base_anim = track_clip(VideoFileClip(subscribe_file))
        base_anim = base_anim.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5).resize(width=target_w * 0.45)

        if total_audio_time > 10:
            clips_to_composite.append(track_clip(base_anim.copy().set_start(10).set_position(('center', 'center'))))
        if total_audio_time > 25:
            clips_to_composite.append(track_clip(base_anim.copy().set_start(25).set_position(('center', 'center'))))

        logger.info("Subscribe animations added to timeline successfully.")
    except Exception as e:
        logger.warning(f"Failed to process subscribe animations, skipping: {e}")

# --- Rendering ---
final_video = track_clip(CompositeVideoClip(clips_to_composite, size=(target_w, target_h)))
final_video = final_video.set_audio(final_audio).set_duration(total_audio_time)

logger.info("Rendering Base Timeline (temp_base.mp4)...")
try:
    final_video.write_videofile(
        "temp_base.mp4", fps=30, codec="libx264", audio_codec="aac",
        bitrate="4000k", preset="ultrafast", threads=2, logger=None
    )
except Exception as e:
    logger.error(f"FATAL: Video rendering failed: {e}")
    exit(1)

# --- Force Memory Cleanup Before FFmpeg ---
logger.info("Releasing MoviePy resources to free RAM...")
for c in open_clips:
    try:
        c.close()
    except Exception:
        pass
open_clips.clear()
gc.collect()

# =================================================================
# [7] Subtitle Burning & LUT Application (FFmpeg)
# =================================================================
logger.info("Applying FFmpeg Filters (Subtitles + LUT)...")
selected_lut = "DEEN.cube"
topic_lower = topic_name.lower()

if any(kw in topic_lower for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]):
    selected_lut = "Alaska.cube"
elif any(kw in topic_lower for kw in ["1908", "1918", "1947", "history", "vintage"]):
    selected_lut = "CineStill.cube"
elif any(kw in topic_lower for kw in ["forest", "drone", "woods", "mountain"]):
    selected_lut = "GREENn.cube"

if not os.path.exists(selected_lut):
    available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
    selected_lut = available_luts[0] if available_luts else None

filters_list = []
if os.path.exists("subs.srt") and os.path.getsize("subs.srt") > 0:
    sub_style = "force_style='Fontname=Liberation Sans,Bold=1,Fontsize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H8B0000&,BackColour=&H000000&,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginL=30,MarginR=30,MarginV=45'"
    filters_list.append(f"subtitles=subs.srt:{sub_style}")

if selected_lut:
    filters_list.append(f"lut3d={selected_lut}")

final_output = "final_shorts.mp4"
cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4']

if filters_list:
    cmd_final.extend(['-vf', ",".join(filters_list)])

cmd_final.extend(['-c:a', 'copy', '-threads', '2', final_output])

try:
    subprocess.run(cmd_final, check=True, timeout=180, capture_output=True, text=True)
    logger.info("SUCCESS: Final Video generated with perfect Layout and Clean Borders!")
except subprocess.TimeoutExpired:
    logger.warning("FFmpeg timed out. Saving un-filtered base video as fallback.")
    shutil.copy("temp_base.mp4", final_output)
except subprocess.CalledProcessError as e:
    logger.error(f"FFmpeg Failed. Saving un-filtered base video. Stderr: {e.stderr}")
    shutil.copy("temp_base.mp4", final_output)

# =================================================================
# [8] Multi-Provider Robust Upload Manager (with post-upload verification)
# =================================================================
if not os.path.exists(final_output) or os.path.getsize(final_output) == 0:
    logger.error("FATAL: Output video not found or empty. Cannot upload.")
    exit(1)

logger.info(f"Starting Multi-Provider Upload Manager for {final_output}...")
local_size = os.path.getsize(final_output)
direct_link = None


def upload_uguu():
    with open(final_output, "rb") as f:
        res = requests.post("https://uguu.se/upload.php", files={"files[]": f}, timeout=120)
    if res.status_code == 200:
        data = res.json()
        if data.get('success') and len(data.get('files', [])) > 0:
            return data['files'][0]['url']
    return None


def upload_tmpfiles():
    with open(final_output, "rb") as f:
        res = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": f},
            # 🔧 FIX: default retention is only 60 minutes. Ask for 6
            # hours so a slow webhook/n8n round trip can't outlive the link.
            data={"expire": 21600},
            timeout=120
        )
    if res.status_code == 200:
        raw_url = res.json().get('data', {}).get('url', '')
        if raw_url:
            return raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    return None


def upload_catbox():
    with open(final_output, "rb") as f:
        res = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=120
        )
    if res.status_code == 200 and ("http://" in res.text or "https://" in res.text):
        return res.text.strip()
    return None


providers = [
    ("Uguu.se [PRIMARY]", upload_uguu),
    ("tmpfiles.org [SECONDARY]", upload_tmpfiles),
    ("Catbox.moe [TERTIARY]", upload_catbox),
]

for name, upload_fn in providers:
    logger.info(f"Upload Attempt: {name}...")
    try:
        candidate_url = upload_fn()
    except Exception as e:
        logger.warning(f"❌ {name} upload request failed: {e}")
        continue

    if not candidate_url:
        logger.warning(f"❌ {name} did not return a usable URL.")
        continue

    logger.info(f"{name} returned a link, verifying it actually serves the video before trusting it...")
    time.sleep(3)  # small grace period for the host to finish processing the upload

    # 🔧 FIX: this is the core of the bug fix. The old code accepted
    # whatever URL the upload API returned. Now we re-fetch it and
    # only accept it if it is NOT an HTML/JSON page and is close to
    # the real file size.
    if verify_remote_file(candidate_url, expected_min_bytes=int(local_size * 0.5)):
        direct_link = candidate_url
        logger.info(f"✅ [VERIFIED] {name} is genuinely serving the video: {direct_link}")
        break
    else:
        logger.warning(f"⚠️ {name} link failed verification (expired/blocked/HTML page). Trying next provider...")

# Final verification - all providers failed
if not direct_link:
    logger.error("FATAL: Video upload+verification failed across ALL service providers (Uguu.se, tmpfiles.org, Catbox.moe).")
    exit(1)

# --- GitHub Actions Output ---
github_output_path = os.environ.get("GITHUB_OUTPUT")
if github_output_path:
    try:
        with open(github_output_path, "a") as gh_out:
            gh_out.write(f"video_url={direct_link}\n")
        logger.info("✅ GitHub Output written successfully")
    except Exception as e:
        logger.error(f"Failed to write to GITHUB_OUTPUT: {e}")

logger.info(f"🎉 Process Complete! Final Verified Direct URL: {direct_link}")
logger.info("📤 Upload Priority: Uguu.se → tmpfiles.org (6h TTL) → Catbox.moe, each verified before use")
