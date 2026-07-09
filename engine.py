#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production-ready AI Video Automation Pipeline
- Uses environment variables for configuration (keeps existing names)
- MoviePy for video composition, FFmpeg for final filters
- Faster-Whisper for transcription
- Robust retries, logging, resource cleanup, and safe fallbacks
- Optimized multi-provider upload with direct URL retrieval (n8n compatible)
Compatible with Python 3.10+
"""

import os
import sys
import gc
import json
import time
import math
import random
import shutil
import logging
import subprocess
import re
import numpy as np
from typing import Optional, List
from contextlib import suppress

import requests
from moviepy.editor import (
    VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip,
    concatenate_videoclips, vfx, CompositeAudioClip, VideoClip, ColorClip
)
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ai_video_pipeline")

# ---------------------------------------------------------------------
# Global constants & tracked resources
# ---------------------------------------------------------------------
RETRY_SLEEP_BASE = 2.0
open_clips: List = []
TEMP_FILES_TO_CLEAN = [
    "audio.mp3", "subs.srt", "temp_base.mp4", "final_shorts.mp4", "subscribe_anim.mp4"
]
FINAL_AUDIO_NAME = "audio.mp3"
SUBS_NAME = "subs.srt"
TEMP_BASE = "temp_base.mp4"
FINAL_OUTPUT = "final_shorts.mp4"
SUBSCRIBE_ANIM = "subscribe_anim.mp4"

# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------
def safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.debug(f"Removed file: {path}")
    except Exception as e:
        logger.warning(f"Could not remove {path}: {e}")

def cleanup_old_files() -> None:
    logger.info("Cleaning up leftover temporary files...")
    # remove known temporaries first
    for f in TEMP_FILES_TO_CLEAN:
        safe_remove(f)
    # remove video_* files
    with suppress(Exception):
        for f in os.listdir('.'):
            if f.startswith("video_") and f.endswith(".mp4"):
                safe_remove(f)
    logger.info("Cleanup complete.")

def retry_loop(fn, attempts: int = 3, base_sleep: float = RETRY_SLEEP_BASE, name: str = "operation"):
    """Simple retry wrapper for functions that may raise."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            sleep_t = base_sleep * (2 ** (attempt - 1)) if attempt < attempts else 0
            logger.warning(f"{name} attempt {attempt}/{attempts} failed: {e}")
            if sleep_t:
                logger.debug(f"Sleeping {sleep_t:.1f}s before retry...")
                time.sleep(sleep_t)
    logger.error(f"{name} failed after {attempts} attempts. Last error: {last_exc}")
    raise last_exc

def close_clip(c):
    try:
        if c is None:
            return
        # MoviePy's close should handle readers/subprocesses
        if hasattr(c, 'close') and callable(c.close):
            c.close()
    except Exception as e:
        logger.debug(f"Error closing clip: {e}")

def close_all_tracked_clips():
    logger.info("Closing tracked MoviePy clips to free memory...")
    for c in list(open_clips):
        try:
            close_clip(c)
        except Exception:
            pass
    open_clips.clear()
    gc.collect()
    logger.info("MoviePy resources released.")

def track_clip(clip):
    if clip is not None:
        open_clips.append(clip)
    return clip

def is_valid_clip(clip) -> bool:
    """Verify a clip is valid for composition."""
    if clip is None:
        return False
    # Must have duration and get_frame method
    if not hasattr(clip, 'duration') or clip.duration is None or clip.duration <= 0:
        return False
    if not hasattr(clip, 'get_frame'):
        return False
    return True

# ---------------------------------------------------------------------
# Environment variables parsing
# ---------------------------------------------------------------------
def load_env():
    target_w = int(os.environ.get("TARGET_W", 1080))
    target_h = int(os.environ.get("TARGET_H", 1920))
    try:
        cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 4.0))
    except Exception:
        cut_duration = 4.0
    topic_name = os.environ.get("TOPIC_NAME", "unknown")
    raw_hook = os.environ.get("HOOK_TEXT", "CLASSIFIED ARCHIVE")
    hook_text = (raw_hook or "").strip()
    audio_url = os.environ.get("AUDIO_URL", "").strip()
    video_urls_raw = os.environ.get("VIDEO_URLS", "[]")
    run_number = os.environ.get("GITHUB_RUN_NUMBER")  # may be None
    try:
        video_urls = json.loads(video_urls_raw)
        if not isinstance(video_urls, list):
            raise ValueError("VIDEO_URLS JSON is not a list")
    except Exception:
        # support pipe/commas lists and simple CSVs
        clean_raw = (video_urls_raw or "").replace("|", ",")
        video_urls = [u.strip() for u in clean_raw.split(",") if u.strip()]
    return {
        "target_w": target_w,
        "target_h": target_h,
        "cut_duration": cut_duration,
        "topic_name": topic_name,
        "hook_text": hook_text,
        "audio_url": audio_url,
        "video_urls": video_urls,
        "run_number": run_number
    }

# ---------------------------------------------------------------------
# Network download with robust retry & streaming
# ---------------------------------------------------------------------
def download_with_retry(url: str, dest_path: str, attempts: int = 3, timeout: int = 60, stream: bool = True) -> bool:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    def _do():
        logger.info(f"Downloading: {url} -> {dest_path}")
        with requests.get(url, headers=headers, timeout=timeout, stream=stream) as r:
            r.raise_for_status()
            tmp_path = dest_path + ".part"
            with open(tmp_path, "wb") as wf:
                if stream:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            wf.write(chunk)
                else:
                    wf.write(r.content)
            # basic sanity: size > 1KB
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1024:
                os.replace(tmp_path, dest_path)
                logger.info(f"Downloaded successfully: {dest_path} ({os.path.getsize(dest_path)} bytes)")
                return True
            else:
                safe_remove(tmp_path)
                raise IOError("Downloaded file is too small or empty")
    try:
        return retry_loop(_do, attempts=attempts, name=f"download {url}")
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        return False

# ---------------------------------------------------------------------
# Audio loading & background music mixer
# ---------------------------------------------------------------------
def load_main_audio(audio_path: str) -> AudioFileClip:
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1024:
        raise FileNotFoundError(f"Audio file missing or too small: {audio_path}")
    try:
        a = AudioFileClip(audio_path)
        return track_clip(a)
    except Exception as e:
        logger.error(f"Failed to load audio file {audio_path}: {e}")
        raise

def pick_background_music(exclude_filename: str, run_number: Optional[str], main_audio_duration: float) -> Optional[AudioFileClip]:
    all_music = [f for f in os.listdir('.') if f.endswith(('.mp3', '.wav')) and f != exclude_filename]
    if not all_music:
        logger.info("No local background music files found.")
        return None
    try:
        idx = 0
        if run_number and run_number.isdigit():
            idx = int(run_number) % len(all_music)
        else:
            idx = random.randrange(len(all_music))
        selected = all_music[idx]
        logger.info(f"Selected background music: {selected}")
        raw_bg = AudioFileClip(selected)
        raw_bg = track_clip(raw_bg)
        clip_dur = min(raw_bg.duration or 0.0, main_audio_duration)
        if clip_dur <= 0:
            logger.warning("Background music has no duration or is invalid; skipping.")
            close_clip(raw_bg)
            return None
        bg = raw_bg.subclip(0, clip_dur).volumex(0.12)
        return track_clip(bg)
    except Exception as e:
        logger.warning(f"Failed to load or process background music: {e}")
        return None

# ---------------------------------------------------------------------
# Transcription with Faster-Whisper -> SRT
# ---------------------------------------------------------------------
def transcribe_to_srt(audio_path: str, srt_path: str) -> bool:
    if not os.path.exists(audio_path):
        logger.warning("Audio for transcription not found.")
        return False
    try:
        def _run_model():
            logger.info("Loading Faster-Whisper model (tiny) on CPU...")
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            logger.info("Starting transcription...")
            segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=True)
            # write SRT
            with open(srt_path, "w", encoding="utf-8") as f:
                idx = 1
                for segment in segments:
                    if not getattr(segment, "words", None):
                        continue
                    # chunk words in reasonable groups to avoid tiny subtitle lines
                    chunk_size = 2
                    words = segment.words
                    for i in range(0, len(words), chunk_size):
                        chunk = words[i:i + chunk_size]
                        start = float(chunk[0].start)
                        end = float(chunk[-1].end)
                        text = " ".join(w.word for w in chunk)
                        # sanitize text for SRT (escape unsupported control chars)
                        clean_text = re_sub_clean_text(text)
                        if not clean_text:
                            continue
                        f.write(f"{idx}\n{fmt_srt_time(start)} --> {fmt_srt_time(end)}\n{clean_text}\n\n")
                        idx += 1
            logger.info("Transcription completed and SRT saved.")
            return True

        def re_sub_clean_text(s: str) -> str:
            # preserve unicode letters & numbers and common punctuation, remove problematic control chars
            cleaned = re.sub(r'[\r\n\t]+', ' ', s)
            cleaned = re.sub(r'[^\S\r\n]+', ' ', cleaned).strip()
            return cleaned

        def fmt_srt_time(t: float) -> str:
            hours = int(t // 3600)
            mins = int((t % 3600) // 60)
            secs = int(t % 60)
            ms = int((t - math.floor(t)) * 1000)
            return f"{hours:02d}:{mins:02d}:{secs:02d},{ms:03d}"

        # run with retry wrapper (transcription can be flaky)
        return retry_loop(_run_model, attempts=2, base_sleep=5.0, name="transcription")
    except Exception as e:
        logger.warning(f"Transcription failed/skipped: {e}")
        return False

# ---------------------------------------------------------------------
# Video processing: safe clip processing (resize, crop, loop/subclip)
# FIXED: Proper MoviePy handling and frame compatibility
# ---------------------------------------------------------------------
def process_clip_safely(filename: str, target_duration: float, target_w: int, target_h: int) -> VideoFileClip:
    if not os.path.exists(filename) or os.path.getsize(filename) < 1024:
        raise FileNotFoundError(f"Video file missing or too small: {filename}")
    try:
        clip = VideoFileClip(filename)
        # Remove audio first to avoid codec issues
        clip = clip.without_audio()
        # Standardize FPS to avoid frame issues
        clip = clip.set_fps(30)
        track_clip(clip)
    except Exception as e:
        raise RuntimeError(f"Failed to load video {filename}: {e}")

    if clip.duration is None or clip.duration <= 0:
        close_clip(clip)
        raise ValueError(f"Clip {filename} has invalid or zero duration")

    # Handle duration mismatch
    if clip.duration < target_duration:
        # loop to extend duration
        try:
            looped = clip.fx(vfx.loop, duration=target_duration)
            close_clip(clip)
            clip = track_clip(looped)
        except Exception as e:
            logger.warning(f"Loop failed, attempting concatenation: {e}")
            # fallback: concatenate multiple copies
            parts = []
            remaining = target_duration
            while remaining > 0 and len(parts) < 10:
                part_dur = min(clip.duration, remaining)
                parts.append(clip.subclip(0, part_dur))
                remaining -= part_dur
            if parts:
                try:
                    composed = concatenate_videoclips(parts, method="compose")
                    close_clip(clip)
                    clip = track_clip(composed)
                except Exception as e2:
                    close_clip(clip)
                    raise RuntimeError(f"Could not loop or extend clip {filename}: {e2}")
            else:
                close_clip(clip)
                raise RuntimeError(f"Could not loop or extend clip {filename}: {e}")
    else:
        # trim to target
        try:
            subc = clip.subclip(0, target_duration)
            close_clip(clip)
            clip = track_clip(subc)
        except Exception as e:
            close_clip(clip)
            raise RuntimeError(f"Failed to subclip {filename}: {e}")

    # Resize and crop to target aspect ratio
    try:
        w, h = clip.size
        target_ratio = target_w / target_h
        src_ratio = w / h if h != 0 else 1.0
        if src_ratio > target_ratio:
            # source is wider -> fit height then crop width
            resized = clip.resize(height=target_h)
            cropped = resized.crop(x_center=resized.w / 2, width=target_w)
        else:
            # source is taller -> fit width then crop height
            resized = clip.resize(width=target_w)
            cropped = resized.crop(y_center=resized.h / 2, height=target_h)
        close_clip(clip)
        clip = track_clip(cropped)
    except Exception as e:
        close_clip(clip)
        raise RuntimeError(f"Failed to resize/crop {filename}: {e}")

    # Apply slight darkening effect
    try:
        dark = clip.fx(vfx.colorx, 0.90)
        close_clip(clip)
        clip = track_clip(dark)
    except Exception:
        pass

    return clip

# ---------------------------------------------------------------------
# Text hook overlay: try TextClip, fallback to subtitle injection
# FIXED: Ensures TextClip has proper duration and get_frame
# ---------------------------------------------------------------------
def create_hook_clip(hook_text: str, duration: float, target_w: int, target_h: int) -> Optional[VideoClip]:
    if not hook_text:
        return None
    # sanitize hook for basic safety (allow unicode letters)
    hook = hook_text.strip()
    if not hook:
        return None
    fontsize = 110
    try:
        # Attempt to create TextClip (requires ImageMagick)
        txt_clip = TextClip(
            hook, fontsize=fontsize, color='orange', font='Liberation-Sans-Bold',
            stroke_color='black', stroke_width=5, method='caption', size=(min(1000, target_w - 80), None)
        ).set_position(('center', 200)).set_duration(min(3.0, duration)).set_start(0)
        
        # Verify the clip is valid before returning
        if not is_valid_clip(txt_clip):
            logger.warning("TextClip created but invalid; using SRT fallback instead")
            return None
            
        logger.info("Hook TextClip rendered via MoviePy (ImageMagick).")
        return track_clip(txt_clip)
    except Exception as e:
        logger.warning(f"TextClip rendering failed (ImageMagick may be missing). Falling back to SRT hook: {e}")
        # Fallback: write a one-line subtitle to subs.srt at start (this preserves feature)
        try:
            if os.path.exists(SUBS_NAME):
                # Prepend a hook entry at top
                with open(SUBS_NAME, "r", encoding="utf-8") as f:
                    existing = f.read()
            else:
                existing = ""
            hook_entry = f"1\n00:00:00,000 --> 00:00:03,000\n{sanitize_subtitle_line(hook)}\n\n"
            # Shift indices of existing subtitles
            # Simple approach: write hook first and then existing (indices will be non-sequential but FFmpeg uses timestamps)
            with open(SUBS_NAME, "w", encoding="utf-8") as f:
                f.write(hook_entry + existing)
            logger.info("Hook text injected into SRT file as fallback.")
            return None
        except Exception as e2:
            logger.warning(f"Failed to inject hook into SRT fallback: {e2}")
            return None

# ---------------------------------------------------------------------
# Subscribe animation processing: safe handling and placeholders
# FIXED: Using ColorClip for placeholder, validation for real clips
# ---------------------------------------------------------------------
def ensure_subscribe_animation(target_w: int) -> Optional[VideoClip]:
    # subscribe anim should be present as SUBSCRIBE_ANIM; if not, attempt to download known URL
    subscribe_url = "https://files.catbox.moe/oarfxq.mp4"
    if not os.path.exists(SUBSCRIBE_ANIM) or os.path.getsize(SUBSCRIBE_ANIM) < 1024:
        ok = download_with_retry(subscribe_url, SUBSCRIBE_ANIM, attempts=2, timeout=30, stream=True)
        if not ok:
            logger.warning("Could not download subscribe animation; will use placeholder overlay.")
    if os.path.exists(SUBSCRIBE_ANIM) and os.path.getsize(SUBSCRIBE_ANIM) > 1024:
        try:
            anim = VideoFileClip(SUBSCRIBE_ANIM)
            anim = anim.set_fps(30)
            
            # Validate clip before processing
            if not is_valid_clip(anim):
                logger.warning("Subscribe animation file is invalid; using placeholder")
                close_clip(anim)
                anim = None
            else:
                track_clip(anim)
                # try to mask green background if present; if it fails, just resize
                try:
                    anim2 = anim.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5).resize(width=int(target_w * 0.45))
                    close_clip(anim)
                    anim = track_clip(anim2)
                except Exception:
                    try:
                        anim = anim.resize(width=int(target_w * 0.45))
                    except Exception:
                        pass
                logger.info("Subscribe animation loaded and processed.")
                return anim
        except Exception as e:
            logger.warning(f"Failed to load subscribe animation: {e}")
            anim = None
    
    # If we reach here, create a simple placeholder using ColorClip (more reliable than VideoClip)
    if anim is None:
        try:
            w = int(target_w * 0.25)
            h = int(w * 0.25)
            # Use solid color clip instead of VideoClip
            placeholder = ColorClip(size=(w, h), color=(255, 255, 255)).set_duration(2.0)
            placeholder = placeholder.set_position(('center', 'center'))
            
            # Verify placeholder is valid
            if is_valid_clip(placeholder):
                track_clip(placeholder)
                logger.info("Using placeholder subscribe animation (solid color).")
                return placeholder
            else:
                logger.warning("Placeholder animation is invalid; skipping subscribe animation")
                close_clip(placeholder)
                return None
        except Exception as e:
            logger.warning(f"Failed to create placeholder subscribe animation: {e}")
            return None
    
    return None

# ---------------------------------------------------------------------
# Subtitle helper
# ---------------------------------------------------------------------
def sanitize_subtitle_line(s: str) -> str:
    # Keep characters but avoid control characters that break .srt
    return "".join(ch for ch in s if ch not in ("\r", "\x0b", "\x0c", "\x1a"))

# ---------------------------------------------------------------------
# Build timeline: concatenate b-roll clips to match audio duration
# ---------------------------------------------------------------------
def build_timeline(video_files: List[str], total_audio_time: float, cut_duration: float, target_w: int, target_h: int) -> List:
    if not video_files:
        raise ValueError("No video files to build timeline from.")
    final_clips = []
    accumulated = 0.0
    pool_index = 0
    max_attempts = len(video_files) * 40
    attempts = 0
    while accumulated < total_audio_time and attempts < max_attempts:
        attempts += 1
        filename = video_files[pool_index % len(video_files)]
        pool_index += 1
        time_left = total_audio_time - accumulated
        duration = min(cut_duration, time_left)
        try:
            clip = process_clip_safely(filename, duration, target_w, target_h)
            final_clips.append(clip)
            accumulated += duration
        except Exception as e:
            logger.warning(f"Skipping video {filename} due to processing error: {e}")
            continue
    logger.info(f"Built timeline: total assembled duration {accumulated:.2f}s with {len(final_clips)} clips")
    if not final_clips:
        raise RuntimeError("No valid video clips could be processed for timeline.")
    return final_clips

# ---------------------------------------------------------------------
# Rendering via MoviePy - FIXED VERSION
# Proper clip validation, duration handling and frame compatibility
# ---------------------------------------------------------------------
def render_base_video(clips_to_composite: List, size: tuple, total_audio, output_path: str) -> None:
    try:
        # CRITICAL: Filter and validate all clips before composite
        valid_clips = []
        for i, clip in enumerate(clips_to_composite):
            if clip is None:
                logger.warning(f"Clip {i} is None, skipping")
                continue
            
            # Check if clip is valid for composition
            if not is_valid_clip(clip):
                logger.warning(f"Clip {i} is invalid (no duration or get_frame), skipping")
                close_clip(clip)
                continue
            
            valid_clips.append(clip)
        
        if not valid_clips:
            raise RuntimeError("No valid clips remaining after validation")
        
        logger.info(f"Using {len(valid_clips)} valid clips (filtered from {len(clips_to_composite)} total)")
        
        # Verify audio has valid duration
        if not hasattr(total_audio, 'duration') or total_audio.duration <= 0:
            raise RuntimeError("Audio duration invalid or zero")
        
        logger.info(f"Creating composite video (size: {size}, duration: {total_audio.duration:.2f}s)...")
        
        # Create composite with explicit size and fps
        composite = CompositeVideoClip(valid_clips, size=size, bg_color=(0, 0, 0))
        composite = composite.set_fps(30)
        track_clip(composite)
        
        # Set audio and duration explicitly
        composite = composite.set_audio(total_audio)
        composite = composite.set_duration(total_audio.duration)
        
        logger.info(f"Rendering base timeline to {output_path} (duration: {composite.duration:.2f}s)...")
        
        # Use write_videofile with improved settings
        composite.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            bitrate="4000k",
            preset="ultrafast",
            threads=2,
            logger=None,
            temp_audiofile="temp_audio.m4a",
            remove_temp=True,
            verbose=False,
            progress_bar=False
        )
        logger.info("Base timeline rendered successfully.")
    except Exception as e:
        logger.error(f"MoviePy rendering failed: {e}")
        raise RuntimeError(f"MoviePy rendering failed: {e}")
    finally:
        # close composite explicitly
        try:
            close_clip(composite)
        except Exception:
            pass

# ---------------------------------------------------------------------
# FFmpeg filters application (subtitles + LUT) - robust
# ---------------------------------------------------------------------
def ffmpeg_apply_filters(input_path: str, output_path: str, subs_path: Optional[str], selected_lut: Optional[str]) -> bool:
    cmd = ["ffmpeg", "-y", "-i", input_path]
    vf_parts = []
    # subtitles: use ASS/SSA if subs present; embed font style via force_style
    if subs_path and os.path.exists(subs_path) and os.path.getsize(subs_path) > 0:
        # Use subtitles filter; ensure proper escaping by not embedding quotes from style
        sub_style = "Fontname=Liberation Sans,Bold=1,Fontsize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H8B0000&,BackColour=&H000000&,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginL=30,MarginR=30,MarginV=45"
        # We'll pass the style via subtitles option (requires escaping colon usage). Use a temp file wrapper not necessary.
        vf_parts.append(f"subtitles={subs_path}:force_style='{sub_style}'")
    if selected_lut and os.path.exists(selected_lut):
        vf_parts.append(f"lut3d={selected_lut}")
    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])
    # copy audio (already set) and write output
    cmd.extend(["-c:a", "copy", "-threads", "2", output_path])
    logger.debug("Running FFmpeg with command: " + " ".join(cmd))
    try:
        # run with timeout; capture output for debug in case of failure
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        logger.info("FFmpeg filters applied successfully.")
        return True
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg process timed out. Will fallback to copying base video.")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed: {e.stderr or e}. Will fallback to copying base video.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error running FFmpeg: {e}")
        return False

# ---------------------------------------------------------------------
# Uploading (Optimized Multi-Provider with Direct URL Retrieval)
# Prioritizes: Uguu.se > Bashupload > tmpfiles.org
# Compatible with n8n workflows
# ---------------------------------------------------------------------
def upload_to_providers(file_path: str) -> Optional[str]:
    """
    Upload video to multiple providers with fallback strategy.
    Optimized for direct URL retrieval suitable for n8n integration.
    
    Priority order:
    1. Uguu.se - Returns direct .mp4 URL, reliable
    2. Bashupload - Returns direct download link with ?download=1 parameter
    3. tmpfiles.org - Returns converted direct link
    """
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        logger.error("Final output not found or empty for upload.")
        return None

    providers = [
        {
            "name": "Uguu.se",
            "url": "https://uguu.se/upload.php",
            "field": "files[]",
            "parse": lambda r: r.json().get('files', [{}])[0].get('url') if r.json().get('success') else None
        },
        {
            "name": "Bashupload",
            "url": "https://bashupload.com",
            "field": "file",
            "parse": lambda r: (r.text.strip() + "?download=1") if r.text.strip() else None
        },
        {
            "name": "tmpfiles.org",
            "url": "https://tmpfiles.org/api/v1/upload",
            "field": "file",
            "parse": lambda r: r.json().get('data', {}).get('url', '').replace("tmpfiles.org/", "tmpfiles.org/dl/") if r.json().get('data', {}).get('url') else None
        }
    ]

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    logger.info(f"Uploading video ({file_size_mb:.1f} MB) to providers...")

    for provider in providers:
        provider_name = provider["name"]
        try:
            logger.info(f"Attempting upload to {provider_name}...")
            with open(file_path, "rb") as f:
                response = requests.post(
                    provider["url"],
                    files={provider["field"]: f},
                    timeout=300,
                    allow_redirects=True
                )
            
            if response.status_code == 200:
                try:
                    link = provider["parse"](response)
                    if link and len(link) > 10:  # Basic sanity check
                        logger.info(f"Successfully uploaded to {provider_name}: {link}")
                        return link
                    else:
                        logger.warning(f"{provider_name}: parsed URL is invalid or empty")
                except Exception as parse_err:
                    logger.warning(f"{provider_name}: failed to parse response: {parse_err}")
                    logger.debug(f"Response content: {response.text[:300]}")
            else:
                logger.warning(f"{provider_name} returned HTTP {response.status_code}")
                logger.debug(f"Response: {response.text[:300]}")
                
        except requests.exceptions.Timeout:
            logger.warning(f"{provider_name}: request timed out (file too large or slow connection)")
        except Exception as e:
            logger.warning(f"{provider_name} upload exception: {type(e).__name__}: {e}")

    logger.error("All upload providers failed. Check network connectivity and file size.")
    return None

# ---------------------------------------------------------------------
# Write to GitHub Actions output if available
# ---------------------------------------------------------------------
def write_github_output(var_name: str, value: str) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        logger.debug("GITHUB_OUTPUT not set; skipping writing outputs.")
        return
    try:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"{var_name}={value}\n")
        logger.info("Wrote video URL to GITHUB_OUTPUT.")
    except Exception as e:
        logger.error(f"Failed to write to GITHUB_OUTPUT: {e}")

# ---------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------
def main():
    # initial cleanup
    cleanup_old_files()

    env = load_env()
    target_w = env["target_w"]
    target_h = env["target_h"]
    cut_duration = env["cut_duration"]
    topic_name = env["topic_name"]
    hook_text = env["hook_text"]
    audio_url = env["audio_url"]
    video_urls = env["video_urls"]
    run_number = env["run_number"]

    if not audio_url:
        logger.error("FATAL: AUDIO_URL not provided in environment. Exiting.")
        sys.exit(1)
    if not video_urls:
        logger.error("FATAL: VIDEO_URLS not provided or empty in environment. Exiting.")
        sys.exit(1)

    # Download main audio
    if not download_with_retry(audio_url, FINAL_AUDIO_NAME, attempts=3, timeout=60, stream=True):
        logger.error("FATAL: Failed to download main audio after retries. Exiting.")
        sys.exit(1)

    try:
        main_audio = load_main_audio(FINAL_AUDIO_NAME)
    except Exception as e:
        logger.error(f"FATAL: Could not load main audio: {e}")
        sys.exit(1)

    # Background music mixing
    bg_music = None
    try:
        bg_music = pick_background_music(FINAL_AUDIO_NAME, run_number, main_audio.duration)
        if bg_music:
            # composite audio
            try:
                composite_audio = CompositeAudioClip([main_audio, bg_music.set_start(0)])
                composite_audio = track_clip(composite_audio)
                final_audio = composite_audio
                logger.info("Background music mixed with main audio.")
            except Exception as e:
                logger.warning(f"Failed to mix bg music, using raw voiceover: {e}")
                final_audio = main_audio
                # ensure bg_music closed if unused
                if bg_music:
                    close_clip(bg_music)
        else:
            final_audio = main_audio
    except Exception as e:
        logger.warning(f"Background music selection failed: {e}")
        final_audio = main_audio

    if not getattr(final_audio, "duration", None) or final_audio.duration <= 0:
        logger.error("FATAL: Final audio duration invalid or zero. Exiting.")
        sys.exit(1)

    total_audio_time = final_audio.duration
    logger.info(f"Total audio duration: {total_audio_time:.2f}s")

    # Transcribe to SRT (best-effort)
    try:
        transcribed = transcribe_to_srt(FINAL_AUDIO_NAME, SUBS_NAME)
        if not transcribed:
            logger.info("Proceeding without subtitles (transcription skipped or failed).")
    except Exception as e:
        logger.warning(f"Transcription step encountered an error: {e}")

    # Download video clips robustly
    downloaded_videos = []
    logger.info(f"Attempting to download {len(video_urls)} B-roll video(s)...")
    for idx, url in enumerate(video_urls):
        if not url:
            continue
        vfname = f"video_{idx}.mp4"
        ok = download_with_retry(url, vfname, attempts=2, timeout=80, stream=True)
        if ok:
            downloaded_videos.append(vfname)
        else:
            logger.warning(f"Download failed for video URL {url}")

    if not downloaded_videos:
        logger.error("FATAL: No background videos downloaded. Exiting.")
        sys.exit(1)

    # Build timeline clips to match audio duration
    try:
        final_clips = build_timeline(downloaded_videos, total_audio_time, cut_duration, target_w, target_h)
    except Exception as e:
        logger.error(f"FATAL: Could not build video timeline: {e}")
        sys.exit(1)

    # Concatenate main video track
    try:
        video_track = concatenate_videoclips(final_clips, method="compose")
        video_track = track_clip(video_track)
    except Exception as e:
        logger.error(f"FATAL: Failed to concatenate clips: {e}")
        sys.exit(1)

    clips_to_composite = [video_track]

    # Hook overlay (TextClip with fallback to SRT injection)
    try:
        hook_clip = create_hook_clip(hook_text, total_audio_time, target_w, target_h)
        if hook_clip:
            clips_to_composite.append(hook_clip)
    except Exception as e:
        logger.warning(f"Hook overlay failed to create: {e}")

    # Subscribe animation overlay
    try:
        sub_anim = ensure_subscribe_animation(target_w)
        if sub_anim:
            # schedule occurrences at sensible times if duration allows
            if total_audio_time > 10:
                clips_to_composite.append(sub_anim.copy().set_start(10).set_position(('center', 'center')))
            if total_audio_time > 25:
                clips_to_composite.append(sub_anim.copy().set_start(25).set_position(('center', 'center')))
    except Exception as e:
        logger.warning(f"Subscribe animation processing failed: {e}")

    # Render with MoviePy to temp_base.mp4
    try:
        # Ensure any previous temp files removed
        safe_remove(TEMP_BASE)
        render_base_video(clips_to_composite, (target_w, target_h), final_audio, TEMP_BASE)
    except Exception as e:
        logger.error(f"FATAL: Rendering base timeline failed: {e}")
        close_all_tracked_clips()
        sys.exit(1)

    # Force cleanup before FFmpeg
    close_all_tracked_clips()

    # Choose LUT based on topic keywords
    selected_lut = "DEEN.cube"
    topic_lower = topic_name.lower()
    try:
        if any(kw in topic_lower for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]):
            selected_lut = "Alaska.cube"
        elif any(kw in topic_lower for kw in ["1908", "1918", "1947", "history", "vintage"]):
            selected_lut = "CineStill.cube"
        elif any(kw in topic_lower for kw in ["forest", "drone", "woods", "mountain"]):
            selected_lut = "GREENn.cube"
    except Exception:
        selected_lut = "DEEN.cube"
    if not os.path.exists(selected_lut):
        available = [f for f in os.listdir('.') if f.endswith('.cube')]
        selected_lut = available[0] if available else None
    logger.info(f"Selected LUT: {selected_lut or 'None'}")

    # Apply FFmpeg filters (subtitles + LUT)
    logger.info("Applying FFmpeg filters (subtitles + LUT)...")
    filters_ok = ffmpeg_apply_filters(TEMP_BASE, FINAL_OUTPUT, SUBS_NAME if os.path.exists(SUBS_NAME) else None, selected_lut)
    if not filters_ok:
        # fallback: copy base to final
        try:
            shutil.copy(TEMP_BASE, FINAL_OUTPUT)
            logger.info("FFmpeg failed or timed out; copied unfiltered base to final output as fallback.")
        except Exception as e:
            logger.error(f"FATAL: Could not copy base to final output fallback: {e}")
            sys.exit(1)

    # Verify final output
    if not os.path.exists(FINAL_OUTPUT) or os.path.getsize(FINAL_OUTPUT) == 0:
        logger.error("FATAL: Final output not found or empty after filters. Exiting.")
        sys.exit(1)

    # Upload to providers (optimized for n8n compatibility)
    uploaded_url = upload_to_providers(FINAL_OUTPUT)
    if not uploaded_url:
        logger.error("FATAL: Video upload failed across all providers. Exiting.")
        sys.exit(1)

    # Write to GitHub Actions output var
    write_github_output("video_url", uploaded_url)

    logger.info(f"Process complete. Final URL: {uploaded_url}")
    # final cleanup optional
    # close any remaining clips
    close_all_tracked_clips()
    logger.info("All done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.error("Interrupted by user.")
        close_all_tracked_clips()
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unhandled exception in pipeline: {e}")
        close_all_tracked_clips()
        sys.exit(1)
