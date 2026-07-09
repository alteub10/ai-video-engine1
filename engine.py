Import os
import gc
import json
import random
import shutil
import subprocess
import time
import requests
import re
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip, vfx, CompositeAudioClip
from faster_whisper import WhisperModel

=================================================================

1️⃣ استقبال البيانات وتجهيز المتغيرات الأساسية

=================================================================

print("[*] Initializing AI Video Engine...")

target_w = 1080
target_h = 1920
cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 4.0))
topic_name = os.environ.get("TOPIC_NAME", "unknown")

جلب نص الهوك ومسح علامات الترقيم منه نهائياً لحماية المظهر العلوي

raw_hook = os.environ.get("HOOK_TEXT", "CLASSIFIED ARCHIVE")
hook_text = re.sub(r'[^\w\s]', '', raw_hook).strip()

audio_url = os.environ.get("AUDIO_URL", "")
video_urls_raw = os.environ.get("VIDEO_URLS", "[]")

تنظيف وتفكيك روابط الفيديوهات القادمة من n8n

try:
video_urls = json.loads(video_urls_raw)
if not isinstance(video_urls, list):
raise ValueError("VIDEO_URLS is valid JSON but not a list")
except Exception:
clean_raw = video_urls_raw.replace("|", ",")
video_urls = [url.strip() for url in clean_raw.split(",") if url.strip()]

headers = {
'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

--- دالة تحميل موحّدة مع إعادة محاولة تلقائية، لحماية الـ pipeline من هفوات الشبكة العابرة ---

def download_with_retry(url, dest_path, attempts=3, timeout=30, stream=False):
for attempt in range(1, attempts + 1):
try:
res = requests.get(url, headers=headers, timeout=timeout, stream=stream)
res.raise_for_status()
with open(dest_path, 'wb') as f:
if stream:
for chunk in res.iter_content(chunk_size=8192):
f.write(chunk)
else:
f.write(res.content)
if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
return True
print(f" [!] Downloaded file is empty: {dest_path} (attempt {attempt}/{attempts})")
except Exception as e:
print(f" [!] Download attempt {attempt}/{attempts} failed for {url}: {e}")
if attempt < attempts:
time.sleep(2)
return False

=================================================================

2️⃣ تحميل صوت التعليق الأساسي

=================================================================

audio_path = "audio.mp3"

نحذف أي ملف صوت متبقٍّ من تشغيل سابق فاشل، حتى لا يُستخدم بالغلط صوت حلقة قديمة

if os.path.exists(audio_path):
os.remove(audio_path)

if not audio_url:
print("[❌] FATAL: AUDIO_URL not provided. Exiting.")
exit(1)

print(f"[*] Downloading main voiceover: {audio_url}")
if not download_with_retry(audio_url, audio_path, attempts=3, timeout=30, stream=False):
print("[❌] FATAL: audio.mp3 could not be downloaded. Exiting.")
exit(1)

main_audio = AudioFileClip(audio_path)

=================================================================

3️⃣ محرك الموسيقى الذكي

=================================================================

run_id = int(os.environ.get("GITHUB_RUN_NUMBER", random.randint(1, 1000)))
all_music = [f for f in os.listdir('.') if f.endswith(('.mp3', '.wav')) and f != "audio.mp3"]

selected_music = None
if all_music:
selected_music = all_music[run_id % len(all_music)]
print(f"[*] Mixing with background music: {selected_music}")

bg_music = None
if selected_music:
try:
bg_music = AudioFileClip(selected_music).volumex(0.12)
bg_music = bg_music.subclip(0, min(bg_music.duration, main_audio.duration))
final_audio = CompositeAudioClip([main_audio, bg_music.set_start(0)])
except Exception as e:
print(f"[⚠️] Failed to mix music, playing raw voiceover: {e}")
final_audio = main_audio
else:
final_audio = main_audio

total_audio_time = final_audio.duration

=================================================================

4️⃣ توليد الترجمة الصامتة

=================================================================

print("[*] Running Faster-Whisper AI for Transcribing...")
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
            chunk = segment.words[i:i+chunk_size]  
            start_time = chunk[0].start  
            end_time = chunk[-1].end  

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

=================================================================

5️⃣ معالجة لقطات الفيديو الفرعية

=================================================================

def process_clip_safely(filename, target_duration):
clip = VideoFileClip(filename).without_audio()
if clip.duration is None or clip.duration <= 0:
raise ValueError(f"Clip {filename} has an invalid/zero duration")

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

=================================================================

6️⃣ تحميل لقطات B-roll وبناء التايم لاين الأساسي (معتمد على المدة الفعلية المتراكمة)

=================================================================

downloaded_files = []
print(f"[*] Downloading {len(video_urls)} background video clips...")
for idx, url in enumerate(video_urls):
v_path = f"video_{idx}.mp4"
if download_with_retry(url, v_path, attempts=2, timeout=30, stream=True):
downloaded_files.append(v_path)
else:
print(f" [!] Failed to download video clip {idx} after retries.")

if not downloaded_files:
print("[❌] FATAL: No background videos downloaded. Exiting.")
exit(1)

ملاحظة مهمة: نبني الآن بالاعتماد على "المدة الفعلية" للقطات التي نجحت فعلاً في المعالجة،

وليس على "الوقت المخطط له" كما في السابق. هذا يمنع أن يصبح الفيديو النهائي أقصر من الصوت

(شاشة سوداء/متجمدة بينما التعليق الصوتي مستمر) في حال فشل تحميل أو معالجة أي مقطع.

final_clips = []
accumulated_duration = 0.0
pool_index = 0
max_total_attempts = len(downloaded_files) * 20  # سقف أمان يمنع أي حلقة لا نهائية

attempts_done = 0
while accumulated_duration < total_audio_time and attempts_done < max_total_attempts:
filename = downloaded_files[pool_index % len(downloaded_files)]
pool_index += 1
attempts_done += 1

time_left = total_audio_time - accumulated_duration  
duration = min(cut_duration, time_left)  

try:  
    clip = process_clip_safely(filename, duration)  
    final_clips.append(clip)  
    accumulated_duration += duration  
except Exception as e:  
    print(f"[!] Error processing {filename}, skipping this slot: {e}")

if not final_clips:
print("[❌] FATAL: All video clips failed to process. Exiting.")
exit(1)

if accumulated_duration < total_audio_time - 0.5:
print(f"[⚠️] WARNING: Video coverage ({accumulated_duration:.1f}s) still shorter than audio ({total_audio_time:.1f}s) after retries.")

video_track = concatenate_videoclips(final_clips, method="compose")

=================================================================

نص الـ Hook — محمي الآن بـ try/except حتى لا يوقف الفيديو كله لو فشل الخط أو ImageMagick

=================================================================

hook_clip = None
try:
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
except Exception as e:
print(f"[⚠️] Failed to render hook text overlay, continuing without it: {e}")
hook_clip = None

clips_to_composite = [video_track]
if hook_clip is not None:
clips_to_composite.append(hook_clip)

# --- زر الاشتراك المتحرك (تحميل تلقائي من الرابط المباشر) ---
subscribe_url = "https://files.catbox.moe/oarfxq.mp4"
subscribe_file = "subscribe_anim.mp4"

print("[*] Preparing Animated Subscribe Button...")

# تحميل الملف إذا لم يكن موجوداً
if not os.path.exists(subscribe_file):
    try:
        print(f"[*] Downloading subscribe animation from: {subscribe_url}")
        res_sub = requests.get(subscribe_url, headers=headers, stream=True, timeout=30)
        res_sub.raise_for_status()
        with open(subscribe_file, 'wb') as f:
            for chunk in res_sub.iter_content(chunk_size=8192):
                f.write(chunk)
        print("[+] Subscribe animation downloaded successfully.")
    except Exception as e:
        print(f"[⚠️] Failed to download subscribe animation: {e}")

# معالجة الملف وإضافته
if os.path.exists(subscribe_file):
    try:
        base_anim = VideoFileClip(subscribe_file)
        # إزالة الخلفية الخضراء
        base_anim = base_anim.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
        base_anim = base_anim.resize(width=target_w * 0.45)
        
        if total_audio_time > 10:
            anim_1 = base_anim.copy().set_start(10).set_position(('center', 'center'))
            clips_to_composite.append(anim_1)
            
        if total_audio_time > 25:
            anim_2 = base_anim.copy().set_start(25).set_position(('center', 'center'))
            clips_to_composite.append(anim_2)
            
        print("[+] Subscribe animations added to timeline.")
    except Exception as e:
        print(f"[⚠️] Failed to process subscribe animations: {e}")
else:
    print("[⚠️] WARNING: 'subscribe_anim.mp4' could not be prepared. Skipping animation.")



final_video = CompositeVideoClip(clips_to_composite, size=(target_w, target_h))
final_video = final_video.set_audio(final_audio).set_duration(total_audio_time)

print("[*] Rendering Base Timeline...")
try:
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
except Exception as e:
print(f"[❌] FATAL: Video rendering failed: {e}")
exit(1)

video_track.close()
final_video.close()
final_audio.close()
if main_audio is not final_audio:
main_audio.close()
if bg_music is not None:
bg_music.close()
if hook_clip is not None:
hook_clip.close()
for c in final_clips:
c.close()
if base_anim is not None:
base_anim.close()
gc.collect()

=================================================================

7️⃣ حرق الترجمة النظيفة وتطبيق فلاتر الألوان (FFmpeg)

=================================================================

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

if selected_lut:
filters_list.append(f"lut3d={selected_lut}")

final_output = "final_shorts.mp4"
cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4']

if filters_list:
vf_filters = ",".join(filters_list)
cmd_final.extend(['-vf', vf_filters])

cmd_final.extend(['-c:a', 'copy', '-threads', '2', final_output])

try:
subprocess.run(cmd_final, check=True, timeout=180)
print("\n[+] SUCCESS: Video generated with perfect Layout and Clean Borders! [+]")
except subprocess.TimeoutExpired:
print("\n[❌] FFmpeg timed out after 180s.")
shutil.copy("temp_base.mp4", final_output)
print("[+] EMERGENCY SUCCESS: Saved video without filters after timeout.")
except subprocess.CalledProcessError as e:
print(f"\n[❌] FFmpeg Failed: {e}")
shutil.copy("temp_base.mp4", final_output)
print("[+] EMERGENCY SUCCESS: Saved video without filter to prevent failure.")

=================================================================

8️⃣ Catbox Upload (Fixed Version)

=================================================================

if not os.path.exists(final_output):
print("[❌] FATAL: Output video not found, cannot upload.")
exit(1)

print(f"\n[*] Uploading {final_output} to Catbox.moe...")

upload_url = "https://catbox.moe/user/api.php"
direct_link = None

upload_headers = {
"User-Agent": "Mozilla/5.0"
}

for attempt in range(1, 4):
try:
with open(final_output, "rb") as f:

data = {  
            "reqtype": "fileupload"  
        }  

        files = {  
            "fileToUpload": (  
                os.path.basename(final_output),  
                f,  
                "video/mp4"  
            )  
        }  

        response = requests.post(  
            upload_url,  
            data=data,  
            files=files,  
            headers=upload_headers,  
            timeout=300  
        )  

    print(f"[*] Response Status: {response.status_code}")  
    print(f"[*] Response Body: {response.text}")  

    if response.status_code == 200:  
        response_text = response.text.strip()  

        if response_text.startswith("http://") or response_text.startswith("https://"):  
            direct_link = response_text  
            print(f"\n[🚀] SUCCESS! Video Uploaded:")  
            print(direct_link)  
            break  

    print(f"[❌] Upload attempt {attempt}/3 failed.")  

except Exception as e:  
    print(f"[❌] Exception during upload attempt {attempt}/3:")  
    print(str(e))  

if attempt < 3:  
    print("[*] Retrying in 5 seconds...")  
    time.sleep(5)

if not direct_link:
print("[❌] FATAL: Video upload failed after all retries.")
exit(1)

=================================================================

GitHub Actions Output

=================================================================

github_output_path = os.environ.get("GITHUB_OUTPUT")

if github_output_path:
with open(github_output_path, "a") as gh_out:
gh_out.write(f"video_url={direct_link}\n")

print(f"[✅] Final URL: {direct_link}")