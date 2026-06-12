import os
import gc
import shutil
import subprocess
import urllib.request
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip, vfx

# =================================================================
# 1️⃣ تحميل الصوت بأمان تام من n8n (معالجة جذرية للخطأ)
# =================================================================
print("[*] Setting up Audio...")
audio_url = os.environ.get("AUDIO_URL", "")
audio_path = "audio.mp3"

# إذا كان هناك رابط من n8n ولم يتم تحميل الملف بعد، قم بتحميله
if audio_url and not os.path.exists(audio_path):
    print(f"[*] Downloading audio from n8n webhook: {audio_url}")
    try:
        urllib.request.urlretrieve(audio_url, audio_path)
    except Exception as e:
        print(f"[❌] FATAL ERROR: Failed to download audio. {e}")
        exit(1)

# التحقق النهائي من وجود الملف الصوتي
if not os.path.exists(audio_path):
    print("[❌] FATAL ERROR: 'audio.mp3' is completely missing! Check your n8n output.")
    exit(1)

# الآن نعرّف المتغير بأمان (لم يعد هناك حاجة لـ main_audio)
final_audio = AudioFileClip(audio_path)
total_audio_time = final_audio.duration

# =================================================================
# 2️⃣ معالجة مقاطع الفيديو
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

final_clips = []
current_time = 0
pool_index = 0

# (نفترض أن downloaded_files و cut_duration و target_w و target_h معرفة في أعلى الملف)
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

# =================================================================
# 3️⃣ دمج الهوك (Hook) والرندرة الأساسية
# =================================================================
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

# تنظيف الذاكرة
video_track.close()
final_video.close()
final_audio.close()
for c in final_clips: 
    c.close()
gc.collect()

# =================================================================
# 4️⃣ الترجمة السفلية وفلاتر الألوان (محصنة ضد الانهيار)
# =================================================================
print("[*] Burning Custom Dark Blue Subtitles via FFmpeg...")
selected_lut = "DEEN.cube" 

if any(kw in topic_name for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]): 
    selected_lut = "Alaska.cube"
elif any(kw in topic_name for kw in ["1908", "1918", "1947", "history", "vintage"]): 
    selected_lut = "CineStill.cube"
elif any(kw in topic_name for kw in ["forest", "drone", "woods", "mountain"]): 
    selected_lut = "GREENn.cube"

if not os.path.exists(selected_lut):
    available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
    selected_lut = available_luts[0] if available_luts else None

filters_list = []

if os.path.exists("subs.srt") and os.path.getsize("subs.srt") > 0:
    sub_style = "force_style='Fontname=Liberation Sans,Bold=1,Fontsize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H8B0000&,BackColour=&H000000&,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginL=30,MarginR=30,MarginV=45'"
    filters_list.append(f"subtitles=subs.srt:{sub_style}")
else:
    print("[⚠️] WARNING: 'subs.srt' missing! Rendering WITHOUT subtitles.")

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
    print("\n[+] SUCCESS: Video generated flawlessly! [+]")
except subprocess.CalledProcessError as e:
    print(f"\n[❌] FFmpeg Failed. Bypassing FFmpeg to save video...")
    shutil.copy("temp_base.mp4", final_output)
