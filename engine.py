import os, sys, requests
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
import moviepy.video.fx.all as vfx

def download_file(url, filename):
    print(f"Downloading {filename}...")
    r = requests.get(url, stream=True)
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk: f.write(chunk)

audio_url = sys.argv[1]
v1_url = sys.argv[2]
v2_url = sys.argv[3]
v3_url = sys.argv[4]

download_file(audio_url, "audio.mp3")
download_file(v1_url, "v1.mp4")
download_file(v2_url, "v2.mp4")
download_file(v3_url, "v3.mp4")

audio = AudioFileClip("audio.mp3")
clip_duration = audio.duration / 3.0 

def process_clip(filename):
    clip = VideoFileClip(filename)
    clip = clip.fx(vfx.loop, duration=clip_duration)
    clip = clip.resize(height=1920)
    w, h = clip.size
    clip = clip.crop(x_center=w/2, y_center=h/2, width=1080, height=1920)
    return clip

print("Processing scenes...")
c1 = process_clip("v1.mp4")
c2 = process_clip("v2.mp4")
c3 = process_clip("v3.mp4")

final_video = concatenate_videoclips([c1, c2, c3], method="compose")
final_video = final_video.set_audio(audio)

print("Rendering final video...")
final_video.write_videofile("final_shorts.mp4", fps=30, codec="libx264", audio_codec="aac", bitrate="5000k")
print("Video rendered successfully!")
