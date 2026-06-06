import os, sys, requests
from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips, CompositeVideoClip, TextClip, CompositeAudioClip
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
import whisper

def download_file(url, filename):
    if "tmpfiles.org" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    print(f"Downloading {filename}...")
    r = requests.get(url, stream=True)
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: f.write(chunk)

# استلام الروابط والنص الخاطف (Hook) آلياً من GitHub Actions
urls = sys.argv[1:8]
audio_url = urls[0]
v_urls = urls[1:]
hook_text = sys.argv[8]

# تحميل الملفات
download_file(audio_url, "audio.mp3")
for i, url in enumerate(v_urls):
    download_file(url, f"v{i+1}.mp4")

# --- بداية نظام الموسيقى السينمائية التسلسلية ---
main_audio = AudioFileClip("audio.mp3")
total_audio_time = main_audio.duration

# استخدام عداد تشغيل جيت هاب لضمان الترتيب التسلسلي الدقيق دون تكرار
run_number = int(os.environ.get('GITHUB_RUN_NUMBER', 1))
bg_music_files = [f"bg{i}.mp3" for i in range(2, 41)] # القائمة من bg2 وحتى bg40

# معادلة الدوران: تضمن مرور كل المقاطع بالترتيب ثم البدء من جديد
track_index = (run_number - 1) % len(bg_music_files)
selected_bg = bg_music_files[track_index]

print(f"Adding Eerie Background Music: {selected_bg} (Factory Run: {run_number})")

bg_audio = AudioFileClip(selected_bg)
bg_audio = bg_audio.fx(afx.volumex, 0.08) # خفض الصوت لمستوى الهمس المرعب 8%
bg_audio = bg_audio.fx(afx.audio_loop, duration=total_audio_time)

# دمج التعليق الصوتي لـ AI مع الموسيقى الخلفية
audio = CompositeAudioClip([main_audio, bg_audio])
# --- نهاية نظام الموسيقى ---

cut_duration = 2.5
thumbnail_duration = 1.5 # مدة ظهور الغلاف في بداية الفيديو

# --- إعدادات دقة 2K (الوزن الذهبي) ---
target_w, target_h = 1440, 2560

def process_and_zoom(filename):
    clip = VideoFileClip(filename)
    if clip.duration < cut_duration:
        clip = clip.fx(vfx.loop, duration=cut_duration)
    else:
        clip = clip.subclip(0, cut_duration)

    # الحل الجذري لمنع الخط الأسود النحيف
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
for i in range(6):
    try:
        clips_pool.append(process_and_zoom(f"v{i+1}.mp4"))
    except Exception as e:
        print(f"Error v{i+1}: {e}")

final_clips = []

# التحقق من وجود صورة الغلاف وإضافتها في البداية
if os.path.exists("thumbnail.png"):
    print("Adding Cinematic Thumbnail as the first frame...")
    thumb_clip = ImageClip("thumbnail.png").set_duration(thumbnail_duration)
    final_clips.append(thumb_clip)
    current_time = thumbnail_duration
else:
    print("Thumbnail not found, proceeding with video clips only...")
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
    
    # تعديل حجم الترجمة وإحداثياتها لتناسب 2K
    txt_clip = TextClip(text, fontsize=90, color='white', font='Liberation-Sans-Bold', 
                        stroke_color='black', stroke_width=4, method='caption', size=(1200, None))
    txt_clip = txt_clip.set_start(start).set_duration(duration).set_position(('center', 1750))
    subtitle_clips.append(txt_clip)

# إضافة النص الخاطف الجذاب في أول 3 ثواني آلياً
print("Adding Auto-Hook Text...")
hook_clip = TextClip(hook_text, fontsize=115, color='yellow', font='Liberation-Sans-Bold', 
                     stroke_color='black', stroke_width=5, method='caption', size=(1250, None))
hook_clip = hook_clip.set_position(('center', 350)).set_duration(3).set_start(0)

# دمج مسار الفيديو والترجمة والنص الخاطف
final_video = CompositeVideoClip([video_track, hook_clip] + subtitle_clips, size=(target_w, target_h))
final_video = final_video.set_audio(audio)

print("Rendering PRO 2K video with Thumbnail, Subtitles, and Sequential Eerie Music...")
final_video.write_videofile("final_shorts.mp4", fps=30, codec="libx264", audio_codec="aac", bitrate="10000k", preset="ultrafast", threads=4)
print("Done!")
