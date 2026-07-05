import os
import gc
import json
import random
import shutil
import subprocess
import requests
import re
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip, vfx, CompositeAudioClip
from faster_whisper import WhisperModel

# =================================================================
# 1️⃣ استقبال البيانات وتجهيز المتغيرات الأساسية
# =================================================================
print("[*] Initializing AI Video Engine...")

target_w = 1080
target_h = 1920
cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 4.0))
topic_name = os.environ.get("TOPIC_NAME", "unknown")

# جلب نص الهوك ومسح علامات الترقيم منه نهائياً لحماية المظهر العلوي
raw_hook = os.environ.get("HOOK_TEXT", "CLASSIFIED ARCHIVE")
hook_text = re.sub(r'[^\w\s]', '', raw_hook).strip()

audio_url = os.environ.get("AUDIO_URL", "")
video_urls_raw = os.environ.get("VIDEO_URLS", "[]")

# تنظيف وتفكيك روابط الفيديوهات القادمة من n8n
try:
    video_urls = json.loads(video_urls_raw)
except Exception:
    clean_raw = video_urls_raw.replace("|", ",")
    video_urls = [url.strip() for url in clean_raw.split(",") if url.strip()]

# =================================================================
# 2️⃣ تحميل صوت التعليق الأساسي
# =================================================================
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

audio_path = "audio.mp3"
if audio_url and not os.path.exists(audio_path):
    print(f"[*] Downloading main voiceover: {audio_url}")
    try:
        res_audio = requests.get(audio_url, headers=headers, timeout=30)
        res_audio.raise_for_status()
        with open(audio_path, 'wb') as f:
            f.write(res_audio.content)
    except Exception as e:
        print(f"[❌] Audio Download Failed: {e}")

if not os.path.exists(audio_path):
    print("[❌] FATAL: audio.mp3 not found. Exiting.")
    exit(1)

main_audio = AudioFileClip(audio_path)

# =================================================================
# 3️⃣ محرك الموسيقى الذكي (البحث في الخارج وتجنب التكرار المتتالي)
# =================================================================
run_id = int(os.environ.get("GITHUB_RUN_NUMBER", random.randint(1, 1000)))
# البحث عن ملفات الموسيقى في المجلد الرئيسي الحالي مباشرة (.) وتجنب ملف التعليق
all_music = [f for f in os.listdir('.') if f.endswith(('.mp3', '.wav')) and f != "audio.mp3"]

selected_music = None
if all_music:
    # اختيار الملف بالتناوب بناءً على رقم تشغيل السيرفر لضمان عدم التكرار
    selected_music = all_music[run_id % len(all_music)]
    print(f"[*] Mixing with background music: {selected_music}")

# دمج الموسيقى الخلفية مع التعليق الصوتي إذا وجدت
if selected_music:
    try:
        bg_music = AudioFileClip(selected_music).volumex(0.12)
        # قص الموسيقى لتناسب مدة التعليق الصوتي تماماً
        bg_music = bg_music.subclip(0, min(bg_music.duration, main_audio.duration))
        final_audio = CompositeAudioClip([main_audio, bg_music.set_start(0)])
    except Exception as e:
        print(f"[⚠️] Failed to mix music, playing raw voiceover: {e}")
        final_audio = main_audio
else:
    final_audio = main_audio

total_audio_time = final_audio.duration

# =================================================================
# 4️⃣ توليد الترجمة الصامتة (كلمتين فقط في الشاشة وبدون علامات ترقيم)
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
            
            # تجميع كل كلمتين معاً في دفعة واحدة
            chunk_size = 2
            for i in range(0, len(segment.words), chunk_size):
                chunk = segment.words[i:i+chunk_size]
                start_time = chunk[0].start
                end_time = chunk[-1].end
                
                # دمج الكلمات ومسح النقاط والفواصل برمجياً
                raw_text = " ".join([w.word for w in chunk])
                clean_text = re.sub(r'[^\w\s]', '', raw_text).strip()
                
                if not clean_text:
                    continue
                
                start_h = int(start_time // 3600)
                start_m = int((start_time % 3600) // 60)
                start_s = int(start_time % 60)
                start_ms = int((start_time % 1) * 1000)
                
                end_h = int(end_time // 3600)
                end_m = int((end_time % 3600) // 60)
                end_s = int(end_time % 60)
                end_ms = int((end_time % 1) * 1000)
                
                f.write(f"{sub_idx}\n")
                f.write(f"{start_h:02d}:{start_m:02d}:{start_s:02d},{start_ms:03d} --> ")
                f.write(f"{end_h:02d}:{end_m:02d}:{end_s:02d},{end_ms:03d}\n")
                f.write(f"{clean_text}\n\n")
                sub_idx += 1
                
    print("[+] Subtitles 'subs.srt' generated perfectly.")
except Exception as e:
    print(f"[⚠️] Faster-Whisper failed or skipped: {e}. Moving forward safely.")

# =================================================================
# 5️⃣ دالة معالجة وتحجيم مقاطع الفيديو الفرعية الذكية
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

    # تعتيم خفيف بنسبة 10% لزيادة وضوح النصوص والترجمة مع الحفاظ على الألوان
    clip = clip.fx(vfx.colorx, 0.90)
    return clip

# =================================================================
# 6️⃣ تحميل لقطات B-roll وبناء خط التايم لاين الأساسي
# =================================================================
downloaded_files = []
print(f"[*] Downloading {len(video_urls)} background video clips...")
for idx, url in enumerate(video_urls):
    v_path = f"video_{idx}.mp4"
    try:
        res_v = requests.get(url, headers=headers, stream=True, timeout=30)
        res_v.raise_for_status()
        with open(v_path, 'wb') as f:
            for chunk in res_v.iter_content(chunk_size=8192):
                f.write(chunk)
        if os.path.exists(v_path) and os.path.getsize(v_path) > 0:
            downloaded_files.append(v_path)
    except Exception as e:
        print(f" [!] Failed to download video clip {idx}: {e}")

if not downloaded_files:
    print("[❌] FATAL: No background videos downloaded. Exiting.")
    exit(1)

final_clips = []
current_time = 0
pool_index = 0

while current_time < total_audio_time:
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

# تصميم الهوك العلوي النظيف
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

# تجهيز الطبقات التي سيتم دمجها
clips_to_composite = [video_track, hook_clip]

# --- إضافة زر الاشتراك المتحرك مرتين (الثانية 10 والثانية 25) ---
if os.path.exists("subscribe_anim.mp4"):
    print("[*] Adding Animated Subscribe Button at 10s and 25s...")
    try:
        # تحميل الملف وإزالة الخلفية وتصغير الحجم مرة واحدة
        base_anim = VideoFileClip("subscribe_anim.mp4")
        base_anim = base_anim.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
        base_anim = base_anim.resize(width=target_w * 0.45)
        
        # الظهور الأول في الثانية 10 في المنتصف تماماً
        if total_audio_time > 10:
            anim_1 = base_anim.set_start(10).set_position(('center', 'center'))
            clips_to_composite.append(anim_1)
            
        # الظهور الثاني في الثانية 25 في المنتصف تماماً
        if total_audio_time > 25:
            anim_2 = base_anim.set_start(25).set_position(('center', 'center'))
            clips_to_composite.append(anim_2)
            
    except Exception as e:
        print(f"[⚠️] Failed to add subscribe animations: {e}")

final_video = CompositeVideoClip(clips_to_composite, size=(target_w, target_h))
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

# تفريغ الذاكرة فوراً
video_track.close()
final_video.close()
final_audio.close()
for c in final_clips: 
    c.close()
if 'base_anim' in locals():
    base_anim.close()
gc.collect()

# =================================================================
# 7️⃣ حرق الترجمة النظيفة وتطبيق فلاتر الألوان (FFmpeg)
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
    print(f"\n[❌] FFmpeg Failed: {e}")
    shutil.copy("temp_base.mp4", final_output)
    print("[+] EMERGENCY SUCCESS: Saved video without filter to prevent failure.")

