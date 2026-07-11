import os
import sys
import requests
import json

# =====================================================================
# Uploading System (Direct MP4 Links - No API Keys Needed)
# =====================================================================
def upload_to_providers(file_path: str):
    """
    يقوم برفع الفيديو النهائي لفيديوهات The Lost Logs ويعيد رابط MP4 مباشر
    """
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        print("[ERROR] Final output video not found or empty.")
        return None

    # نستخدم User-Agent لمتصفح حقيقي لتجاوز حماية المواقع ضد سيرفرات جيت هاب
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }

    # 1. Litterbox (البديل الأساسي - يحتفظ بالملف 12 ساعة وهي مدة ممتازة لليوتيوب)
    try:
        print("[*] Uploading to Litterbox...")
        with open(file_path, "rb") as f:
            res = requests.post(
                "https://litterbox.catbox.moe/user/api.php", 
                data={"reqtype": "fileupload", "time": "12h"}, 
                files={"fileToUpload": f}, 
                headers=headers, 
                timeout=180
            )
        # التأكد من أن الرابط صالح ويبدأ بـ http
        if res.status_code == 200 and ("http://" in res.text or "https://" in res.text):
            link = res.text.strip()
            print(f"[SUCCESS] Direct Video Link (Litterbox): {link}")
            return link
    except Exception as e:
        print(f"[WARNING] Litterbox failed: {e}")

    # 2. Uguu (البديل الاحتياطي القوي - لا يحتاج حساب ويعطي رابط مباشر)
    try:
        print("[*] Uploading to Uguu.se...")
        with open(file_path, 'rb') as f:
            res = requests.post(
                "https://uguu.se/upload.php", 
                files={'files[]': f}, 
                headers=headers, 
                timeout=180
            )
        if res.status_code == 200:
            link = res.json()['files'][0]['url']
            print(f"[SUCCESS] Direct Video Link (Uguu): {link}")
            return link
    except Exception as e:
        print(f"[WARNING] Uguu failed: {e}")

    print("[FATAL] All uploaders failed.")
    return None
