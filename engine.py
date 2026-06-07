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
    time_left = total_audio_time - current_time
    if time_left < cut_duration:
        clip = clip.subclip(0, time_left)
        current_time += time_left
    else:
        current_time += cut_duration
    final_clips.append(clip)
    pool_index += 1

video_track = concatenate_videoclips(final_clips, method="compose")

print("AI is listening to audio and generating subtitles...")
model = whisper.load_model("base.en")
result = model.transcribe("audio.mp3")

subtitle_clips = []
for segment in result['segments']:
    text = segment['text'].strip()
    start = segment['start']
    end = segment['end']
    duration = end - start
    
    txt_clip = TextClip(text, fontsize=90, color='white', font='Liberation-Sans-Bold', 
                        stroke_color='black', stroke_width=4, method='caption', size=(1200, None))
    txt_clip = txt_clip.set_start(start).set_duration(duration).set_position(('center', 1750))
    subtitle_clips.append(txt_clip)

hook_clip = TextClip(hook_text, fontsize=115, color='yellow', font='Liberation-Sans-Bold', 
                     stroke_color='black', stroke_width=5, method='caption', size=(1250, None))
hook_clip = hook_clip.set_position(('center', 350)).set_duration(3).set_start(0)

final_video = CompositeVideoClip([video_track, hook_clip] + subtitle_clips, size=(target_w, target_h))
final_video = final_video.set_audio(audio)
final_video = final_video.set_duration(total_audio_time)

print("Rendering Base 2K video...")
final_video.write_videofile("temp_shorts.mp4", fps=30, codec="libx264", audio_codec="aac", bitrate="10000k", preset="ultrafast", threads=4)

# --- نظام توجيه الفلاتر الذكي ---
print("Selecting Cinematic LUT based on Topic...")
selected_lut = "DEEN.cube" 

if any(keyword in topic_name for keyword in ["river", "ocean", "sea", "water", "cyclops", "eltanin", "ice", "antarctic"]):
    selected_lut = "Alaska.cube"
elif any(keyword in topic_name for keyword in ["1908", "1918", "1947", "history", "tunguska", "incident", "vintage"]):
    selected_lut = "CineStill.cube"
elif any(keyword in topic_name for keyword in ["forest", "drone", "woods", "mountain"]):
    selected_lut = "GREENn.cube"

# نظام أمان: التحقق من وجود الفلتر أو البحث عن أي فلتر بديل
if not os.path.exists(selected_lut):
    print(f"Warning: {selected_lut} not found! Searching for any .cube file...")
    available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
    if available_luts:
        selected_lut = available_luts[0]
        print(f"Using alternative LUT: {selected_lut}")
    else:
        print("No .cube files found! Proceeding without color grading.")
        selected_lut = None

final_graded_output = "final_shorts.mp4" 

if selected_lut:
    print(f"Topic is: {topic_name} | Applying LUT: {selected_lut}")
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
        print("Done! Final Graded Video is Ready for Upload.")
    except Exception as e:
        print(f"FFmpeg failed: {e}. Falling back to base video.")
        os.rename('temp_shorts.mp4', final_graded_output)
else:
    os.rename('temp_shorts.mp4', final_graded_output)
    print("Done! Base Video is Ready for Upload (No LUT applied).")
