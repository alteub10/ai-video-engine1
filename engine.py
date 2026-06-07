import os, sys, requests, subprocess
from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips, CompositeVideoClip, TextClip, CompositeAudioClip
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
import whisper

def download_file(url, filename):
    # نظام الحماية: إذا كان الرابط فارغاً، يتم استخدام فيديو احتياطي لمنع تعطل النظام
    if not url or url.strip() == "":
        print(f"Warning: URL for {filename} is empty! Using fallback video.")
        url = "https://videos.pexels.com/video-files/5938927/5938927-hd_1080_1920_25fps.mp4"
        
    if "tmpfiles.org" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    print(f"Downloading {filename} from {url}...")
    
    try:
        r = requests.get(url, stream=True, timeout=15)
        r.raise_for_status()
        with open(filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk: f.write(chunk)
    except Exception as e:
        print(f"Error downloading {filename}: {e}")

# استلام البيانات المدمجة بأمان تام من البيئة
audio_url = os.environ.get("AUDIO_URL", "")
video_string = os.environ.get("VIDEO_URLS", "")
hook_text = os.environ.get("HOOK_TEXT", "MYSTERY")
topic_name = os.environ.get("TOPIC_NAME", "Unknown Topic").lower()

if not audio_url or not video_string:
    print("CRITICAL ERROR: Data did not arrive from GitHub Actions env properly!")
    sys.exit(1)

# فك دمج الفيديوهات لتصبح قائمة جاهزة للتحميل
v_urls = video_string.split("|")

# تحميل الملفات الأساسية
download_file(audio_url, "audio.mp3")
for i, url in enumerate(v_urls):
    download_file(url, f"v{i+1}.mp4")

# إعداد الصوت الأساسي
main_audio = AudioFileClip("audio.mp3")
total_audio_time = main_audio.duration

# نظام الموسيقى الخلفية التسلسلي 
run_number = int(os.environ.get('GITHUB_RUN_NUMBER', 1))
bg_music_files = [f"bg{i}.mp3" for i in range(2, 41)]
track_index = (run_number - 1) % len(bg_music_files)
selected_bg = bg_music_files[track_index]

print(f"Adding Background Music: {selected_bg} (Run: {run_number})")

try:
    bg_audio = AudioFileClip(selected_bg)
    bg_audio = bg_audio.fx(afx.volumex, 0.08)
    bg_audio = bg_audio.fx(afx.audio_loop, duration=total_audio_time)
    audio = CompositeAudioClip([main_audio, bg_audio])
except Exception as e:
    print(f"Warning: Music error {e}. Proceeding without it.")
    audio = main_audio

cut_duration = 6.0 
thumbnail_duration = 1.5 
target_w, target_h = 1440, 2560

def process_and_zoom(filename):
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
    clip = clip.resize(lambda t: 1 + 0.03 * t)
    clip = CompositeVideoClip([clip.set_position("center")], size=(target_w, target_h)).set_duration(cut_duration)
    return clip

print("Processing Fast Cuts in 2K...")
clips_pool = []
for i in range(len(v_urls)):
    try:
        clips_pool.append(process_and_zoom(f"v{i+1}.mp4"))
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
    time_left = total_audio_time -
