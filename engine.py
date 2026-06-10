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
        print(f"Warning: URL for {filename} is empty! Using fallback video.")
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
# 1. استقبال البيانات وتجهيز البيئة
# ==========================================
audio_url = os.environ.get("AUDIO_URL", "")
video_string = os.environ.get("VIDEO_URLS", "")
hook_text = os.environ.get("HOOK_TEXT", "MYSTERY").upper()
topic_name = os.environ.get("TOPIC_NAME", "Unknown Topic").lower()

cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 2.5)) 
thumbnail_duration = 1.5 
target_w, target_h = 1080, 1920

print(f"[*] AI Video Engine Initialize...")
print(f"[*] Target Pacing: {cut_duration}s | Resolution: 1080p")

if not audio_url or not video_string:
    print("CRITICAL ERROR: Data did not arrive properly!")
    sys.exit(1)

v_urls = video_string.split("|")
download_file(audio_url, "audio.mp3")
for i, url in enumerate(v_urls):
    download_file(url, f"v{i+1}.mp4")

main_audio = AudioFileClip("audio.mp3")
total_audio_time = main_audio.duration

run_number = int(os.environ.get('GITHUB_RUN_NUMBER', 1))
bg_music_files = [f"bg{i}.mp3" for i in range(2, 41)]
track_index = (run_number - 1) % len(bg_music_files)
selected_bg = bg_music_files[track_index]

try:
    bg_audio = AudioFileClip(selected_bg)
    bg_audio = bg_audio.fx(afx.volumex, 0.08)
    bg_audio = bg_audio.fx(afx.audio_loop, duration=total_audio_time)
    audio = CompositeAudioClip([main_audio, bg_audio])
except Exception:
    audio = main_audio

# ==========================================
# 2. معالجة اللقطات (تم إزالة الزووم الديناميكي لحماية الذاكرة)
# ==========================================
def process_clip(filename):
    clip = VideoFileClip(filename)
    
    if clip.duration < cut_duration:
        clip = clip.fx(vfx.loop, duration=cut_duration)
    else:
        clip = clip.subclip(0, cut_duration)

    clip_ratio = clip.w / clip.h
    target_ratio = target_w / target_h

    if clip_ratio > target_ratio:
        clip = clip.resize(height=target_h)
    else:
        clip = clip.resize(width=target_w)

    w, h = clip.size
    clip = clip.crop(x_center=w/2, y_center=h/2, width=target_w, height=target_h)
    
    # فلتر التعتيم السينمائي فقط (استهلاك ذاكرة منخفض جداً)
    clip = clip.fx(vfx.colorx, 0.80)
    return clip

print("Processing Fast Cuts...")
clips_pool = []
for i in range(len(v_urls)):
    try:
        clips_pool.append(process_clip(f"v{i+1}.mp4"))
    except Exception as e:
        print(f"Error processing v{i+1}: {e}")

final_clips = []
if os.path.exists("thumbnail.png"):
    thumb_clip = ImageClip("thumbnail.png").set_duration(thumbnail_duration)
    final_clips.append(thumb_clip)
    current_time = thumbnail_duration
else:
    current_time = 0

pool_index = 0
while current_time < total_audio_time:
    if not clips_pool:
        break
    clip = clips_pool[pool_index % len(clips_pool)]
    time_left = total_audio_time - current_time
    
    if time_left < cut_duration:
        clip = clip.subclip(0, time_left)
        current_time += time_left
    else:
        current_time += cut_duration
        
    final_clips.append(clip)
    pool_index += 1

video_track = concatenate_videoclips(final_clips, method="compose")

# ==========================================
# 3. نظام الترجمة (باستخدام النسخة الأخف tiny.en)
# ==========================================
print("AI is listening to audio (using tiny.en to save RAM)...")
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

# تنظيف شامل للذاكرة 
del model
del result
gc.collect() 
print("[*] Memory forcefully cleared.")

hook_clip = TextClip(hook_text, fontsize=110, color='red', font='Liberation-Sans-Bold', 
                     stroke_color='black', stroke_width=5, method='caption', size=(1000, None))
hook_clip = hook_clip.set_position(('center', 350)).set_duration(min(3.0, total_audio_time)).set_start(0)

final_video = CompositeVideoClip([video_track, hook_clip] + subtitle_clips, size=(target_w, target_h))
final_video = final_video.set_audio(audio)
final_video = final_video.set_duration(total_audio_time)

# ==========================================
# 4. الرندرة والتصدير (بإعدادات تمنع تسريب الذاكرة)
# ==========================================
print("Rendering Final 1080p video...")
final_video.write_videofile(
    "temp_shorts.mp4", 
    fps=30, 
    codec="libx264", 
    audio_codec="aac", 
    bitrate="5000k", 
    preset="ultrafast", 
    threads=2,
    logger=None, # لمنع انهيار السيرفر بسبب امتلاء السجلات
    ffmpeg_params=["-max_muxing_queue_size", "1024"]
)

# ==========================================
# 5. نظام توجيه الفلاتر الذكي السينمائي
# ==========================================
selected_lut = "DEEN.cube" 

if any(keyword in topic_name for keyword in ["river", "ocean", "sea", "water", "cyclops", "eltanin", "ice", "antarctic"]):
    selected_lut = "Alaska.cube"
elif any(keyword in topic_name for keyword in ["1908", "1918", "1947", "history", "tunguska", "incident", "vintage"]):
    selected_lut = "CineStill.cube"
elif any(keyword in topic_name for keyword in ["forest", "drone", "woods", "mountain"]):
    selected_lut = "GREENn.cube"

if not os.path.exists(selected_lut):
    available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
    if available_luts:
        selected_lut = available_luts[0]
    else:
        selected_lut = None

final_graded_output = "final_shorts.mp4" 

if selected_lut:
    print(f"Applying LUT: {selected_lut}")
    command = [
        'ffmpeg',
        '-i', 'temp_shorts.mp4',
        '-vf', f'lut3d={selected_lut}',
        '-c:a', 'copy', 
        '-y',
        final_graded_output
    ]
    try:
        subprocess.run(command, check=True)
        print("Done! Final Graded Video is Ready.")
    except Exception as e:
        print(f"FFmpeg failed: {e}. Falling back to base video.")
        os.rename('temp_shorts.mp4', final_graded_output)
else:
    os.rename('temp_shorts.mp4', final_graded_output)
    print("Done! Base Video is Ready.")
