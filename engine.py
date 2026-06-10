import os
import sys
import requests
import subprocess
import multiprocessing

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

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

# ==========================================
# 1. الغرفة المعزولة للذكاء الاصطناعي (حماية الرام 100%)
# ==========================================
def isolated_ai_transcription(audio_file, srt_filename):
    """هذه الدالة تعمل في Process منفصل. عند انتهائها، يستعيد السيرفر كل الذاكرة بالقوة"""
    import whisper # استدعاء المكتبة هنا فقط!
    print("\n[+] AI Sub-Process Started: Memory allocated.")
    model = whisper.load_model("base.en")
    result = model.transcribe(audio_file, word_timestamps=True)

    with open(srt_filename, "w", encoding="utf-8") as srt_file:
        sub_idx = 1
        for segment in result.get('segments', []):
            words = segment.get('words', [])
            chunk = []
            for i, w_info in enumerate(words):
                if not chunk:
                    chunk_start = w_info['start']
                chunk.append(w_info['word'].strip().upper())
                
                if len(chunk) == 2 or i == len(words) - 1:
                    chunk_end = w_info['end']
                    srt_file.write(f"{sub_idx}\n{format_time(chunk_start)} --> {format_time(chunk_end)}\n{' '.join(chunk)}\n\n")
                    sub_idx += 1
                    chunk = []
    print("[+] AI Sub-Process Completed: Self-destructing to release RAM...\n")

# ==========================================
# التنفيذ الرئيسي للسيرفر
# ==========================================
if __name__ == '__main__':
    audio_url = os.environ.get("AUDIO_URL", "")
    video_string = os.environ.get("VIDEO_URLS", "")
    hook_text = os.environ.get("HOOK_TEXT", "MYSTERY").upper().replace("'", "").replace(":", "")
    topic_name = os.environ.get("TOPIC_NAME", "Unknown Topic").lower()
    cut_duration = float(os.environ.get("MAX_CLIP_DURATION", 2.5))

    if not audio_url or not video_string:
        print("CRITICAL ERROR: Data missing!")
        sys.exit(1)

    # 1. تحميل الملفات
    download_file(audio_url, "audio.mp3")
    v_urls = video_string.split("|")
    downloaded_files = []
    for i, url in enumerate(v_urls):
        fname = f"v{i+1}.mp4"
        download_file(url, fname)
        downloaded_files.append(fname)

    cmd_probe = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', 'audio.mp3']
    total_audio_time = float(subprocess.check_output(cmd_probe).decode('utf-8').strip())

    # 2. إطلاق رصاصة العزل (Multiprocessing Kill-Switch)
    # سيتم تشغيل الذكاء الاصطناعي، ثم مسحه من الوجود لإفراغ الرام لـ FFmpeg
    ai_process = multiprocessing.Process(target=isolated_ai_transcription, args=("audio.mp3", "subs.srt"))
    ai_process.start()
    ai_process.join() # ننتظر حتى ينتهي وتُدمر غرفته
    print("[*] RAM is now 100% free for video rendering.")

    # 3. معالجة الفيديو (التقطيع الدقيق الخفيف)
    print("[*] Forging Video Clips via Raw FFmpeg...")
    processed_clips = []
    for i, vid in enumerate(downloaded_files):
        out_name = f"proc_{i}.mp4"
        vf_string = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,eq=brightness=-0.08"
        cmd = [
            'ffmpeg', '-y', '-stream_loop', '-1', '-i', vid, '-t', str(cut_duration),
            '-vf', vf_string, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', 
            '-threads', '2', '-an', out_name  # تحديد الخيوط بـ 2 يمنع اختناق المعالج
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processed_clips.append(out_name)

    # 4. بناء خط الزمن المجمع
    print("[*] Generating Timeline Concat File...")
    with open("concat.txt", "w") as f:
        current_time = 0
        idx = 0
        while current_time < total_audio_time:
            if not processed_clips: break
            clip_name = processed_clips[idx % len(processed_clips)]
            f.write(f"file '{clip_name}'\n")
            current_time += cut_duration
            idx += 1

    run_number = int(os.environ.get('GITHUB_RUN_NUMBER', 1))
    bg_music_files = [f"bg{i}.mp3" for i in range(2, 41)]
    selected_bg = bg_music_files[(run_number - 1) % len(bg_music_files)]

    print("[*] Merging Video, Voice, and Music...")
    if os.path.exists(selected_bg):
        filter_complex = '[1:a]volume=1.0[a1];[2:a]volume=0.08[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[aout]'
        cmd_merge = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', 'concat.txt',
            '-i', 'audio.mp3', '-stream_loop', '-1', '-i', selected_bg,
            '-filter_complex', filter_complex, '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-threads', '2', '-shortest', 'temp_base.mp4'
        ]
    else:
        cmd_merge = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', 'concat.txt', '-i', 'audio.mp3',
            '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-threads', '2', '-shortest', 'temp_base.mp4'
        ]
    subprocess.run(cmd_merge, check=True)

    # 5. الحرق النهائي (الترجمة، الخطاف، والفلاتر)
    print("[*] Burning AI Subtitles, Hook Text, and Cinematic LUT...")
    selected_lut = "DEEN.cube" 
    if any(kw in topic_name for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]): selected_lut = "Alaska.cube"
    elif any(kw in topic_name for kw in ["1908", "1918", "1947", "history", "vintage"]): selected_lut = "CineStill.cube"
    elif any(kw in topic_name for kw in ["forest", "drone", "woods", "mountain"]): selected_lut = "GREENn.cube"
    if not os.path.exists(selected_lut): selected_lut = [f for f in os.listdir('.') if f.endswith('.cube')][0] if [f for f in os.listdir('.') if f.endswith('.cube')] else None

    font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    drawtext_flt = f"drawtext=fontfile='{font_path}':text='{hook_text}':fontcolor=red:fontsize=115:x=(w-text_w)/2:y=350:borderw=5:bordercolor=black:enable='between(t,0,3)'"
    sub_flt = "subtitles=subs.srt:force_style='Fontname=Liberation Sans,Fontsize=22,PrimaryColour=&H00FFFF,OutlineColour=&H000000,BorderStyle=1,Outline=2,Alignment=5'"

    vf_filters = f"{sub_flt},{drawtext_flt}"
    if selected_lut: vf_filters += f",lut3d={selected_lut}"

    final_output = "final_shorts.mp4"
    cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4', '-vf', vf_filters, '-c:a', 'copy', '-threads', '2', final_output]

    try:
        subprocess.run(cmd_final, check=True)
        print("\n[+] BOOM! Final Masterpiece Rendered Successfully with 0% RAM Waste! [+]")
    except Exception as e:
        print(f"[!] FFmpeg Subtitle Burn Failed: {e}")
