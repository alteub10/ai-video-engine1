import os
import sys
import requests
import gc
import subprocess

# =================================================================
# 1. الوضع المنعزل: تشغيل الذكاء الاصطناعي في بيئة مغلقة ثم تدميرها
# =================================================================
if len(sys.argv) > 1 and sys.argv[1] == '--transcribe':
    import warnings
    warnings.filterwarnings("ignore")
    
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError:
        print("[*] faster_whisper not found! Installing it automatically...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "faster-whisper"])
        from faster_whisper import WhisperModel

    def format_time(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    print("\n[*] Sub-Process: Transcribing with Faster-Whisper (CPU Optimized)...")
    model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    segments, info = model.transcribe("audio.mp3", word_timestamps=True)

    with open("subs.srt", "w", encoding="utf-8") as srt_file:
        sub_idx = 1
        for segment in segments:
            chunk = []
            for i, word in enumerate(segment.words):
                if not chunk:
                    chunk_start = word.start
                chunk.append(word.word.strip().upper())
                
                # كلمتين فقط كحد أقصى لضمان بقاء النص قوياً وفي سطر واحد
                if len(chunk) == 2 or i == len(segment.words) - 1:
                    chunk_end = word.end
                    srt_file.write(f"{sub_idx}\n{format_time(chunk_start)} --> {format_time(chunk_end)}\n{' '.join(chunk)}\n\n")
                    sub_idx += 1
                    chunk = []
                    
    print("[*] Sub-Process: Transcription complete. Self-destructing to free 100% RAM.\n")
    sys.exit(0)

# =================================================================
# 2. الكود الرئيسي (Main Process) - إدارة السيرفر والمونتاج
# =================================================================
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

if __name__ == '__main__':
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

    # تشغيل الترجمة في مسار معزول لحماية الرام
    subprocess.run([sys.executable, __file__, '--transcribe'], check=True)
    print("[*] Back to Main: AI RAM fully reclaimed by OS.")

    print("[*] Loading MoviePy for Video Editing...")
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips, TextClip, CompositeVideoClip, ColorClip
    import moviepy.video.fx.all as vfx
    import moviepy.audio.fx.all as afx

    main_audio = AudioFileClip("audio.mp3")
    total_audio_time = main_audio.duration

    run_number = int(os.environ.get('GITHUB_RUN_NUMBER', 1))
    bg_music_files = [f"bg{i}.mp3" for i in range(2, 41)]
    selected_bg = bg_music_files[(run_number - 1) % len(bg_music_files)]

    try:
        bg_audio = AudioFileClip(selected_bg).fx(afx.volumex, 0.08).fx(afx.audio_loop, duration=total_audio_time)
        final_audio = CompositeAudioClip([main_audio, bg_audio])
    except Exception:
        final_audio = main_audio

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
            print(f"Error processing {filename}: {e}")
            
        current_time += duration
        pool_index += 1

    # الخاتمة الإلزامية للمقاطع
    outro_text = "Don't forget to like and subscribe"
    outro_clip = TextClip(outro_text, fontsize=85, color='white', font='Liberation-Sans-Bold', 
                          stroke_color='black', stroke_width=4, method='caption', size=(900, None))
    outro_bg = ColorClip(size=(target_w, target_h), color=(0,0,0)).set_duration(2.5)
    final_outro = CompositeVideoClip([outro_bg, outro_clip.set_position('center')]).set_duration(2.5)
    final_clips.append(final_outro)

    video_track = concatenate_videoclips(final_clips, method="compose")

    # الخطاف البصري (الهوك) البرتقالي - تم تثبيته في منتصف الشاشة (Center)
    hook_clip = TextClip(hook_text, fontsize=110, color='orange', font='Liberation-Sans-Bold', 
                         stroke_color='black', stroke_width=5, method='caption', size=(1000, None))
    hook_clip = hook_clip.set_position(('center', 'center')).set_duration(min(3.0, total_audio_time)).set_start(0)

    final_video = CompositeVideoClip([video_track, hook_clip], size=(target_w, target_h))
    final_video = final_video.set_audio(final_audio).set_duration(total_audio_time + 2.5)

    print("[*] Rendering Base Timeline...")
    final_video.write_videofile(
        "temp_base.mp4", fps=30, codec="libx264", audio_codec="aac", 
        bitrate="4000k", preset="ultrafast", threads=2, logger=None
    )

    # تنظيف الذاكرة
    video_track.close()
    final_video.close()
    for c in final_clips: c.close()
    gc.collect()

    # =================================================================
    # ضبط تصميم الترجمة الأزرق القاتم (بالحجم المتوسط الأنيق 26)
    # =================================================================
    print("[*] Burning Custom Dark Blue Subtitles via FFmpeg...")
    selected_lut = "DEEN.cube" 
    if any(kw in topic_name for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]): selected_lut = "Alaska.cube"
    elif any(kw in topic_name for kw in ["1908", "1918", "1947", "history", "vintage"]): selected_lut = "CineStill.cube"
    elif any(kw in topic_name for kw in ["forest", "drone", "woods", "mountain"]): selected_lut = "GREENn.cube"

    if not os.path.exists(selected_lut):
        available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
        selected_lut = available_luts[0] if available_luts else None

    # التعديلات الهندسية هنا:
    # Fontsize=26 (أصغر بقليل من الصورة، ليكون أنيقاً ومتناسقاً)
    # Outline=3 (إطار أزرق قاتم متناسب مع حجم الخط)
    # Alignment=2 (توسيط في الأسفل)
    # MarginV=250 (الارتفاع المثالي ليكون فوق أزرار الإعجاب تماماً كما في صورتك المرجعية)
    sub_flt = "subtitles=subs.srt:force_style='Fontname=Liberation Sans,Bold=1,Fontsize=26,PrimaryColour=&HFFFFFF&,OutlineColour=&H8B0000&,BackColour=&H000000&,BorderStyle=1,Outline=3,Shadow=1.5,Alignment=2,MarginL=40,MarginR=40,MarginV=250'"
    
    vf_filters = sub_flt
    if selected_lut: vf_filters += f",lut3d={selected_lut}"

    final_output = "final_shorts.mp4"
    cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4', '-vf', vf_filters, '-c:a', 'copy', '-threads', '2', final_output]

    subprocess.run(cmd_final, check=True)
    print("\n[+] SUCCESS: Video generated with perfect Bottom Dark Blue Subtitles! [+]")
