import os
import sys
import requests
import gc
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips, TextClip, CompositeVideoClip
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
import whisper

def download_file(url, filename):
    if not url or url.strip() == "":
        url = "https://videos.pexels.com/video-files/5938927/5938927-hd_1080_1920_25fps.mp4"
    if "tmpfiles.org" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    print(f"[*] Downloading {filename}...")
    try:
        r = requests.get(url, stream=True, timeout=15)
        r.raise_for_status()
        with open(filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk: f.write(chunk)
    except Exception as e:
        print(f"[!] Error downloading {filename}: {e}")

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

# ==========================================
# 1. تهيئة المتغيرات وتنزيل الملفات
# ==========================================
audio_url = os.environ.get("AUDIO_URL", "")
video_string = os.environ.get("VIDEO_URLS", "")
hook_text = os.environ.get("HOOK_TEXT", "MYSTERY").upper().replace("'", "").replace(":", "")
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

main_audio = AudioFileClip("audio.mp3")
total_audio_time = main_audio.duration

# ==========================================
# 2. الترجمة عبر الذكاء الاصطناعي (Whisper)
# ==========================================
print("[*] Transcribing with Whisper...")
model = whisper.load_model("base.en")
result = model.transcribe("audio.mp3", word_timestamps=True)

with open("subs.srt", "w", encoding="utf-8") as srt_file:
    sub_idx = 1
    for segment in result.get('segments', []):
        words = segment.get('words', [])
        chunk = []
        for i, w_info in enumerate(words):
            if not chunk:
                chunk_start = w_info['start']
            chunk.append(w_info['word'].strip().upper())
            if len(chunk) == 2 or i == len(words) - 1:
                chunk_end = w_info['end']
                srt_file.write(f"{sub_idx}\n{format_time(chunk_start)} --> {format_time(chunk_end)}\n{' '.join(chunk)}\n\n")
                sub_idx += 1
                chunk = []

del model
del result
gc.collect()
print("[*] SRT generated. Whisper cleared from RAM.")

# ==========================================
# 3. إعداد الصوت والموسيقى
# ==========================================
run_number = int(os.environ.get('GITHUB_RUN_NUMBER', 1))
bg_music_files = [f"bg{i}.mp3" for i in range(2, 41)]
selected_bg = bg_music_files[(run_number - 1) % len(bg_music_files)]

try:
    bg_audio = AudioFileClip(selected_bg).fx(afx.volumex, 0.08).fx(afx.audio_loop, duration=total_audio_time)
    final_audio = CompositeAudioClip([main_audio, bg_audio])
except Exception:
    final_audio = main_audio

# ==========================================
# 4. محرك المونتاج البصري (MoviePy)
# ==========================================
print("[*] Editing Videos in Python...")

def process_clip_safely(filename, target_duration):
    clip = VideoFileClip(filename).without_audio()
    if clip.duration < target_duration:
        clip = clip.fx(vfx.loop, duration=target_duration)
    else:
        clip = clip.subclip(0, target_duration)

    w, h = clip.size
    target_ratio = target_w / target_h
    if (w/h) > target_ratio:
        clip = clip.resize(height=target_h)
        clip = clip.crop(x_center=clip.w/2, width=target_w)
    else:
        clip = clip.resize(width=target_w)
        clip = clip.crop(y_center=clip.h/2, height=target_h)

    clip = clip.fx(vfx.colorx, 0.80)
    return clip

final_clips = []
current_time = 0
pool_index = 0

while current_time < total_audio_time:
    if not downloaded_files: break
    filename = downloaded_files[pool_index % len(downloaded_files)]
    time_left = total_audio_time - current_time
    duration = min(cut_duration, time_left)
    
    try:
        clip = process_clip_safely(filename, duration)
        final_clips.append(clip)
    except Exception as e:
        print(f"Error: {e}")
        
    current_time += duration
    pool_index += 1

video_track = concatenate_videoclips(final_clips, method="compose")

# سحب الخطاف وجعله باللون البرتقالي (Orange) في الأعلى تماماً (Y=300)
hook_clip = TextClip(hook_text, fontsize=110, color='orange', font='Liberation-Sans-Bold', 
                     stroke_color='black', stroke_width=5, method='caption', size=(1000, None))
hook_clip = hook_clip.set_position(('center', 300)).set_duration(min(3.0, total_audio_time)).set_start(0)

final_video = CompositeVideoClip([video_track, hook_clip], size=(target_w, target_h))
final_video = final_video.set_audio(final_audio).set_duration(total_audio_time)

print("[*] Rendering Base Timeline...")
final_video.write_videofile(
    "temp_base.mp4", fps=30, codec="libx264", audio_codec="aac", 
    bitrate="4000k", preset="ultrafast", threads=2, logger=None
)

video_track.close()
final_video.close()
gc.collect()

# ==========================================
# 5. لصق الترجمة باللون الأصفر السينمائي في المنتصف عبر FFmpeg
# ==========================================
print("[*] Burning Dynamic Yellow Subtitles in Center...")

selected_lut = "DEEN.cube" 
if any(kw in topic_name for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]): selected_lut = "Alaska.cube"
elif any(kw in topic_name for kw in ["1908", "1918", "1947", "history", "vintage"]): selected_lut = "CineStill.cube"
elif any(kw in topic_name for kw in ["forest", "drone", "woods", "mountain"]): selected_lut = "GREENn.cube"

if not os.path.exists(selected_lut):
    available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
    selected_lut = available_luts[0] if available_luts else None

# تعديل الألوان بدقة هيدروليكية:
# PrimaryColour=&H00FFFF& تعني أصفر مشع تماماً للترجمة
# Alignment=5 تعني وضع النص في منتصف الشاشة (Center) تماماً ليركز المشاهد على الكلمات
sub_flt = "subtitles=subs.srt:force_style='Fontname=Liberation Sans,Fontsize=26,PrimaryColour=&H00FFFF&,OutlineColour=&H000000&,BorderStyle=1,Outline=3,Alignment=5'"
vf_filters = sub_flt
if selected_lut: vf_filters += f",lut3d={selected_lut}"

final_output = "final_shorts.mp4"
cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4', '-vf', vf_filters, '-c:a', 'copy', '-threads', '2', final_output]

try:
    subprocess.run(cmd_final, check=True)
    print("\n[+] SUCCESS: Color alignment completed! [+]")
except Exception as e:
    print(f"[!] Filter failed: {e}")
