import os, sys, requests
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
import moviepy.video.fx.all as vfx

def download_file(url, filename):
    if "tmpfiles.org" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    print(f"Downloading {filename}...")
    r = requests.get(url, stream=True)
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: f.write(chunk)

# استلام الصوت و 6 فيديوهات
urls = sys.argv[1:8]
audio_url = urls[0]
v_urls = urls[1:]

download_file(audio_url, "audio.mp3")
for i, url in enumerate(v_urls):
    download_file(url, f"v{i+1}.mp4")

audio = AudioFileClip("audio.mp3")
total_audio_time = audio.duration

# مدة المشهد الواحد: 2.5 ثانية لإيقاع سريع ومثير
cut_duration = 2.5

def process_and_zoom(filename):
    clip = VideoFileClip(filename)
    
    # التأكد من أن الفيديو أطول من 2.5 ثانية، وإلا يتم تكراره
    if clip.duration < cut_duration:
        clip = clip.fx(vfx.loop, duration=cut_duration)
    else:
        clip = clip.subclip(0, cut_duration)

    # ضبط المقاس الطولي
    clip = clip.resize(height=1920)
    w, h = clip.size
    clip = clip.crop(x_center=w/2, y_center=h/2, width=1080, height=1920)
    
    # تأثير الزووم الاحترافي (Ken Burns Effect)
    clip = clip.resize(lambda t: 1 + 0.03 * t) # تكبير تدريجي ناعم
    
    # إجبار الفيديو على البقاء داخل مقاس الهاتف وقص الزيادات الناتجة عن الزووم
    clip = CompositeVideoClip([clip.set_position("center")], size=(1080, 1920)).set_duration(cut_duration)
    return clip

print("Processing scenes with Fast Cuts & Zoom...")
clips_pool = []
for i in range(6):
    try:
        c = process_and_zoom(f"v{i+1}.mp4")
        clips_pool.append(c)
    except Exception as e:
        print(f"Skipping v{i+1} due to error: {e}")

# تجميع المشاهد بالتتابع السريع حتى ينتهي الصوت
final_clips = []
current_time = 0
pool_index = 0

while current_time < total_audio_time:
    clip = clips_pool[pool_index % len(clips_pool)]
    time_left = total_audio_time - current_time
    
    # إذا كان الوقت المتبقي للصوت أقل من 2.5 ثانية، نقص المشهد ليتطابق تماماً
    if time_left < cut_duration:
        clip = clip.subclip(0, time_left)
        current_time += time_left
    else:
        current_time += cut_duration
        
    final_clips.append(clip)
    pool_index += 1

# الدمج النهائي للصورة والصوت
final_video = concatenate_videoclips(final_clips, method="compose")
final_video = final_video.set_audio(audio)

print("Rendering final PRO video...")
final_video.write_videofile("final_shorts.mp4", fps=30, codec="libx264", audio_codec="aac", bitrate="5000k")
print("Video rendered successfully!")
