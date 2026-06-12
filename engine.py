import os
import gc
import json
import shutil
import subprocess
import urllib.request
import requests
import re
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip, vfx
from faster_whisper import WhisperModel

# =================================================================
# 1️⃣ استقبال البيانات وتفكيك الروابط المعقدة
# =================================================================
print("[*] Initializing AI Video Engine...")

target_w = 1080
target_h = 1920
cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 4.0))
topic_name = os.environ.get("TOPIC_NAME", "unknown")

# جلب الهوك ومسح أي نقاط أو فواصل منه برمجياً لحماية الشاشة
raw_hook = os.environ.get("HOOK_TEXT", "CLASSIFIED ARCHIVE")
hook_text = re.sub(r'[^\w\s]', '', raw_hook).strip()

audio_url = os.environ.get("AUDIO_URL", "")
video_urls_raw = os.environ.get("VIDEO_URLS", "[]")

# تنظيف الروابط
try:
    video_urls = json.loads(video_urls_raw)
except Exception:
    clean_raw = video_urls_raw.replace("|", ",")
    video_urls = [url.strip() for url in clean_raw.split(",") if url.strip()]

# =================================================================
# 2️⃣ تحميل الملفات (مع تخطي حماية السيرفرات)
# =================================================================
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

audio_path = "audio.mp3"
if audio_url and not os.path.exists(audio_path):
    print(f"[*] Downloading audio: {audio_url}")
    try:
        response = requests.get(audio_url, timeout=30)
        response.raise_for_status()
        with open(audio_path, 'wb') as f:
            f.write(response.content)
    except Exception as e:
        print(f"[❌] Audio Download Failed: {e}")

if not os.path.exists(audio_path):
    print("[❌] FATAL: audio.mp3 not found. Exiting.")
    exit(1)

main_audio = AudioFileClip(audio_path)
total_audio_time = main_audio.duration
final_audio = main_audio

downloaded_files = []
print(f"[*] Downloading {len(video_urls)} b-roll video clips...")
for idx, url in enumerate(video_urls):
    v_path = f"video_{idx}.mp4"
    try:
        print(f" -> Downloading clip {idx}...")
        res = requests.get(url, headers=headers, stream=True, timeout=30)
        res.raise_for_status()
        with open(v_path, 'wb') as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        
        if os.path.exists(v_path) and os.path.getsize(v_path) > 0:
            downloaded_files.append(v_path)
            print(f"    [+] Clip {idx} downloaded successfully.")
    except Exception as e:
        print(f" [!] Failed to download video clip {idx}: {e}")

if not downloaded_files:
    print("[❌] FATAL: No background videos downloaded. Exiting.")
    exit(1)

# =================================================================
# 3️⃣ توليد الترجمة (كلمتين فقط في الشاشة وبدون فواصل أو نقاط)
# =================================================================
print("[*] Running Faster-Whisper AI for Transcribing...")
try:
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=True)
    
    with open("subs.srt", "w", encoding="utf-8") as f:
        sub_idx = 1
        for segment in segments:
            if not segment.words:
                continue
            
            # تقطيع الكلمات إلى مجموعات من كلمتين
            chunk_size = 2
            for i in range(0, len(segment.words), chunk_size):
                chunk = segment.words[i:i+chunk_size]
                start_time = chunk[0].start
                end_time = chunk[-1].end
                
                # تجميع الكلمات ومسح علامات الترقيم بالكامل
                raw_text = " ".join([w.word for w in chunk])
                clean_text = re.sub(r'[^\w\s]', '', raw_text).strip()
                
                if not clean_text:
                    continue
                
                start_h = int(start_time // 3600)
                start_m = int((start_time % 3600) // 60)
                start_s = start_time % 60
                start_ms = int((start_s - int(start_s)) * 1000)
                
                end_h = int(end_time // 3600)
                end_m = int((end_time % 3600) // 60)
                end_s = end_time % 60
                end_ms = int((end_s - int(end_s)) * 1000)
                
                f.write(f"{sub_idx}\n")
                f.write(f"{start_h:02d}:{start_m:02d}:{int(start_s):02d},{start_ms:03d} --> ")
                f.write(f"{end_h:02d}:{end_m:02d}:{int(end_s):02d},{end_ms:03d}\n")
                f.write(f"{clean_text}\n\n")
                sub_idx += 1
                
    print("[+] Subtitles 'subs.srt' generated perfectly (2 words per screen, no punctuation).")
except Exception as e:
    print(f"[⚠️] Faster-Whisper failed or skipped: {e}. Moving forward safely.")

# =================================================================
# 4️⃣ دالة معالجة وقص مقاطع الفيديو (تعديل الأبعاد والمحاذاة)
# =================================================================
def process_clip_safely(filename, target_duration):
    clip = VideoFileClip(filename).without_audio()
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

    clip = clip.fx(vfx.colorx, 0.80)
    return clip

# =================================================================
# 5️⃣ بناء التايم لاين والمونتاج التلقائي
# =================================================================
final_clips = []
current_time = 0
pool_index = 0

while current_time < total_audio_time:
    if not downloaded_files:
        break
        
    filename = downloaded_files[pool_index % len(downloaded_files)]
    time_left = total_audio_time - current_time
    duration = min(cut_duration, time_left)
    
    try:
        clip = process_clip_safely(filename, duration)
        final_clips.append(clip)
    except Exception as e:
        print(f"[!] Error processing {filename}: {e}")
        
    current_time += duration
    pool_index += 1

video_track = concatenate_videoclips(final_clips, method="compose")

# تصميم الهوك العلوي للقناة
hook_clip = TextClip(
    hook_text, 
    fontsize=110, 
    color='orange', 
    font='Liberation-Sans-Bold', 
    stroke_color='black', 
    stroke_width=5, 
    method='caption', 
    size=(1000, None)
)
hook_clip = hook_clip.set_position(('center', 200)).set_duration(min(3.0, total_audio_time)).set_start(0)

final_video = CompositeVideoClip([video_track, hook_clip], size=(target_w, target_h))
final_video = final_video.set_audio(final_audio).set_duration(total_audio_time)

print("[*] Rendering Base Timeline...")
final_video.write_videofile(
    "temp_base.mp4", 
    fps=30, 
    codec="libx264", 
    audio_codec="aac", 
    bitrate="4000k", 
    preset="ultrafast", 
    threads=2, 
    logger=None
)

# تفريغ الذاكرة فوراً لمنع الانهيار
video_track.close()
final_video.close()
final_audio.close()
for c in final_clips: 
    c.close()
gc.collect()

# =================================================================
# 6️⃣ تطبيق فلاتر الألوان والترجمة الاحترافية عبر FFmpeg
# =================================================================
print("[*] Burning Custom Dark Blue Subtitles via FFmpeg...")
selected_lut = "DEEN.cube" 

if any(kw in topic_name.lower() for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]): 
    selected_lut = "Alaska.cube"
elif any(kw in topic_name.lower() for kw in ["1908", "1918", "1947", "history", "vintage"]): 
    selected_lut = "CineStill.cube"
elif any(kw in topic_name.lower() for kw in ["forest", "drone", "woods", "mountain"]): 
    selected_lut = "GREENn.cube"

if not os.path.exists(selected_lut):
    available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
    selected_lut = available_luts[0] if available_luts else None

filters_list = []

if os.path.exists("subs.srt") and os.path.getsize("subs.srt") > 0:
    sub_style = "force_style='Fontname=Liberation Sans,Bold=1,Fontsize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H8B0000&,BackColour=&H000000&,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginL=30,MarginR=30,MarginV=45'"
    filters_list.append(f"subtitles=subs.srt:{sub_style}")
else:
    print("[⚠️] WARNING: 'subs.srt' missing or empty! Rendering video WITHOUT subtitles.")

if selected_lut: 
    filters_list.append(f"lut3d={selected_lut}")

final_output = "final_shorts.mp4"
cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4']

if filters_list:
    vf_filters = ",".join(filters_list)
    cmd_final.extend(['-vf', vf_filters])

cmd_final.extend(['-c:a', 'copy', '-threads', '2', final_output])

try:
    subprocess.run(cmd_final, check=True)
    print("\n[+] SUCCESS: Video generated with perfect Layout and Clean Borders! [+]")
except subprocess.CalledProcessError as e:
    print(f"\n[❌] FFmpeg Failed with error: {e}")
    print("[⚡] INITIATING EMERGENCY FALLBACK: Bypassing FFmpeg...")
    shutil.copy("temp_base.mp4", final_output)
    print("[+] EMERGENCY SUCCESS: Temp video saved as final_shorts.mp4. Ready for upload!")
