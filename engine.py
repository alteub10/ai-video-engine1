    import os
    import gc
    import shutil
    import subprocess

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

    video_track = concatenate_videoclips(final_clips, method="compose")

    # =================================================================
    # الهوك في الأعلى (بدون نقاط، بحواف سوداء نظيفة)
    # =================================================================
    hook_clip = TextClip(hook_text, fontsize=110, color='orange', font='Liberation-Sans-Bold', 
                         stroke_color='black', stroke_width=5, method='caption', size=(1000, None))
    hook_clip = hook_clip.set_position(('center', 200)).set_duration(min(3.0, total_audio_time)).set_start(0)

    final_video = CompositeVideoClip([video_track, hook_clip], size=(target_w, target_h))
    
    # تم إلغاء لقطة "الاشتراك" لينهي الفيديو مع نهاية الصوت فوراً
    final_video = final_video.set_audio(final_audio).set_duration(total_audio_time)

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
    # الترجمة السفلية وفلاتر الألوان (نظام ذكي ومحصن ضد الأعطال)
    # =================================================================
    print("[*] Burning Custom Dark Blue Subtitles via FFmpeg...")
    selected_lut = "DEEN.cube" 
    if any(kw in topic_name for kw in ["river", "ocean", "sea", "water", "ice", "antarctic"]): selected_lut = "Alaska.cube"
    elif any(kw in topic_name for kw in ["1908", "1918", "1947", "history", "vintage"]): selected_lut = "CineStill.cube"
    elif any(kw in topic_name for kw in ["forest", "drone", "woods", "mountain"]): selected_lut = "GREENn.cube"

    if not os.path.exists(selected_lut):
        available_luts = [f for f in os.listdir('.') if f.endswith('.cube')]
        selected_lut = available_luts[0] if available_luts else None

    # بناء قائمة الفلاتر بشكل آمن وديناميكي
    filters_list = []

    # 1️⃣ الحماية القصوى: تحقق من وجود ملف الترجمة وحجمه قبل تمريره
    if os.path.exists("subs.srt") and os.path.getsize("subs.srt") > 0:
        sub_style = "force_style='Fontname=Liberation Sans,Bold=1,Fontsize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H8B0000&,BackColour=&H000000&,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginL=30,MarginR=30,MarginV=45'"
        filters_list.append(f"subtitles=subs.srt:{sub_style}")
    else:
        print("[⚠️] WARNING: 'subs.srt' missing or empty! Rendering video WITHOUT subtitles.")

    # 2️⃣ إضافة فلتر الألوان (LUT)
    if selected_lut: 
        filters_list.append(f"lut3d={selected_lut}")

    final_output = "final_shorts.mp4"
    cmd_final = ['ffmpeg', '-y', '-i', 'temp_base.mp4']

    # تمرير الفلاتر فقط إذا كانت القائمة غير فارغة
    if filters_list:
        vf_filters = ",".join(filters_list)
        cmd_final.extend(['-vf', vf_filters])
    
    cmd_final.extend(['-c:a', 'copy', '-threads', '2', final_output])

    # 3️⃣ تنفيذ أمر FFmpeg بأمان مع بروتوكول طوارئ لإنقاذ الفيديو
    try:
        subprocess.run(cmd_final, check=True)
        print("\n[+] SUCCESS: Video generated with perfect Layout and Clean Borders! [+]")
    except subprocess.CalledProcessError as e:
        print(f"\n[❌] FFmpeg Failed with error: {e}")
        print("[⚡] INITIATING EMERGENCY FALLBACK: Bypassing FFmpeg...")
        # إذا تعطل FFmpeg لأي سبب، ننسخ الفيديو الأساسي كفيديو نهائي لإنقاذ النشر
        shutil.copy("temp_base.mp4", final_output)
        print("[+] EMERGENCY SUCCESS: Temp video saved as final_shorts.mp4. Ready for upload!")

