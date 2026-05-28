import os, sys, requests
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, TextClip
import moviepy.video.fx.all as vfx
import whisper

def download_file(url, filename):
    if "tmpfiles.org" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    print(f"Downloading {filename}...")
    r = requests.get(url, stream=True)
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: f.write(chunk)

# استلام الروابط
urls = sys.argv[1:8]
audio_url = urls[0]
v_urls = urls[1:]

# تحميل الملفات
download_file(audio_url, "audio.mp3")
for i, url in enumerate(v_urls):
    download_file(url, f"v{i+1}.mp4")

audio = AudioFileClip("audio.mp3")
total_audio_time = audio.duration

# مدة القص السريع
cut_duration = 2.5

def process_and_zoom(filename):
    clip = VideoFileClip(filename)
    if clip.duration < cut_duration:
        clip = clip.fx(vfx.loop, duration=cut_duration)
    else:
        clip = clip.subclip(0, cut_duration)

    clip = clip.resize(height=1920)
    w, h = clip.size
    clip = clip.crop(x_center=w/2, y_center=h/2, width=1080, height=1920)
    # زووم احترافي
    clip = clip.resize(lambda t: 1 + 0.03 * t)
    clip = CompositeVideoClip([clip.set_position("center")], size=(1080, 1920)).set_duration(cut_duration)
    return clip

print("Processing Fast Cuts...")
clips_pool = []
for i in range(6):
    try:
        clips_pool.append(process_and_zoom(f"v{i+1}.mp4"))
    except Exception as e:
        print(f"Error v{i+1}: {e}")

final_clips = []
current_time = 0
pool_index = 0

while current_time < total_audio_time:
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

# تشغيل الذكاء الاصطناعي للاستماع وكتابة النص
print("AI is listening to audio and generating subtitles...")
model = whisper.load_model("base")
result = model.transcribe("audio.mp3")

subtitle_clips = []
for segment in result['segments']:
    text = segment['text'].strip()
    start = segment['start']
    end = segment['end']
    duration = end - start
    
    # تصميم النص: خط عريض أبيض مع حواف سوداء وتوسيط في الأسفل
    txt_clip = TextClip(text, fontsize=65, color='white', font='Liberation-Sans-Bold', 
                        stroke_color='black', stroke_width=3, method='caption', size=(900, None))
    txt_clip = txt_clip.set_start(start).set_duration(duration).set_position(('center', 1300))
    subtitle_clips.append(txt_clip)

# تركيب الفيديو مع الترجمة والصوت
final_video = CompositeVideoClip([video_track] + subtitle_clips, size=(1080, 1920))
final_video = final_video.set_audio(audio)

print("Rendering PRO video with Subtitles...")
final_video.write_videofile("final_shorts.mp4", fps=30, codec="libx264", audio_codec="aac", bitrate="5000k")
print("Done!")
