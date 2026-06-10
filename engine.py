import os
import sys
import requests
import subprocess
import gc
from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips, CompositeVideoClip, TextClip, CompositeAudioClip
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
import whisper

def download_file(url, filename):
    if not url or url.strip() == "":
        url = "https://videos.pexels.com/video-files/5938927/5938927-hd_1080_1920_25fps.mp4"
    if "tmpfiles.org" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    print(f"Downloading {filename}...")
    try:
        r = requests.get(url, stream=True, timeout=15)
        r.raise_for_status()
        with open(filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk: f.write(chunk)
    except Exception as e:
        print(f"Error downloading {filename}: {e}")

# ==========================================
# 1. تهيئة المتغيرات وتنزيل الملفات
# ==========================================
audio_url = os.environ.get("AUDIO_URL", "")
video_string = os.environ.get("VIDEO_URLS", "")
hook_text = os.environ.get("HOOK_TEXT", "MYSTERY").upper()
topic_name = os.environ.get("TOPIC_NAME", "Unknown Topic").lower()

cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 2.5))
target_w, target_h = 1080, 1920

if not audio_url or not video_string:
    print("CRITICAL ERROR: Data missing!")
    sys.exit(1)

download_file(audio_url, "audio.mp3")
v_urls = video_string.split("|")
downloaded_files = []
for i, url in enumerate(v_urls):
    fname = f"v{i+1}.mp4"
    download_file(url, fname)
    downloaded_files.append(fname)

# ==========================================
# 2. تشغيل Whisper أولاً (ثم تدميره لتفريغ الرام)
# ==========================================
print("[*] Running AI Audio Transcription first to save memory...")
model = whisper.load_model("tiny.en")
result = model.transcribe("audio.mp3", word_timestamps=True)

subtitle_clips = []
for segment in result.get('segments', []):
    words = segment.get('words', [])
    chunk = []
    chunk_start = 0
    
    for idx, w_info in enumerate(words):
        if not chunk:
            chunk_start = w_info['start']
        chunk.append(w_info['word'].strip().upper())
        
        if len(chunk) == 2 or idx == len(words) - 1:
            chunk_end = w_info['end']
            text_str = " ".join(chunk)
            chunk_duration = chunk_end - chunk_start
            
            if chunk_duration > 0:
                txt_clip = TextClip(text_str, fontsize=95, color='yellow', font='Liberation-Sans-Bold', 
                                    stroke_color='black', stroke_width=4, method='caption', size=(900, None))
                txt_clip = txt_clip.set_start(chunk_start).set_duration(chunk_duration).set_position(('center', 'center'))
                subtitle_clips.append(txt_clip)
            chunk = []

# تدمير الذكاء الاصطناعي من الذاكرة كلياً
del model
