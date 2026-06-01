import os
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def download_pexels_image(api_key, query, save_path="base_image.jpg"):
    """
    تحميل صورة من API الخاص بـ Pexels بناءً على كلمة البحث.
    """
    headers = {"Authorization": api_key}
    url = f"https://api.pexels.com/v1/search?query={query}&per_page=1&orientation=portrait"
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if data.get("photos"):
            image_url = data["photos"][0]["src"]["large2x"]
            img_data = requests.get(image_url).content
            with open(save_path, 'wb') as handler:
                handler.write(img_data)
            print(f"[+] تم تحميل الصورة بنجاح وحفظها في: {save_path}")
            return save_path
        else:
            print("[-] لم يتم العثور على صور تطابق البحث.")
            return None
    except Exception as e:
        print(f"[-] خطأ أثناء تحميل الصورة من Pexels: {e}")
        return None

def create_cinematic_thumbnail(image_path, text, output_path="thumbnail.png", font_path=None):
    """
    معالجة الصورة لتبدو سينمائية (بدقة 2K رأسية) وإضافة نص غامض واحترافي بدون إيموجي.
    """
    # أبعاد الـ 2K الرأسية للشورتز (1440x2560)
    target_width = 1440
    target_height = 2560
    
    try:
        img = Image.open(image_path)
        
        # 1. تغيير الحجم وقص الصورة لتناسب أبعاد 2K الرأسية (9:16)
        img_ratio = img.width / img.height
        target_ratio = target_width / target_height
        
        if img_ratio > target_ratio:
            # الصورة أعرض من المطلوب
            new_width = int(target_height * img_ratio)
            img = img.resize((new_width, target_height), Image.Resampling.LANCZOS)
            left = (img.width - target_width) / 2
            img = img.crop((left, 0, left + target_width, target_height))
        else:
            # الصورة أطول من المطلوب
            new_height = int(target_width / img_ratio)
            img = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
            top = (img.height - target_height) / 2
            img = img.crop((0, top, target_width, top + target_height))
            
        # 2. إضافة تأثير تعتيم سينمائي (Vignette/Dark Overlay) لتعزيز طابع الغموض والرعب
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # طبقة تعتيم خفيفة على كامل الصورة لزيادة التباين مع النص
        draw.rectangle([(0, 0), (target_width, target_height)], fill=(0, 0, 0, 60))
        img = Image.alpha_composite(img.convert("RGBA"), overlay)
        
        # 3. إعداد الخط (Font)
        # إذا لم يتم تمرير خط مخصص، سيتم استخدام الخط الافتراضي
        if font_path and os.path.exists(font_path):
            font = ImageFont.truetype(font_path, 95)  # حجم الخط متناسب مع دقة 2K
            shadow_font = ImageFont.truetype(font_path, 95)
        else:
            font = ImageFont.load_default()
            print("[!] تحذير: يتم استخدام الخط الافتراضي، يفضل رفع خط مخصص مثل VCR أو Typewriter إلى مستودع GitHub.")

        # 4. كتابة النص مع تأثير الظل (Drop Shadow) لضمان الوضوح التام
        draw = ImageDraw.Draw(img)
        
        # تقسيم النص إلى أسطر إذا كان طويلاً
        words = text.split()
        lines = []
        current_line = []
        for word in words:
            current_line.append(word)
            # تحديد عدد الكلمات في السطر لضمان عدم الخروج عن الإطار
            if len(current_line) >= 3: 
                lines.append(" ".join(current_line))
                current_line = []
        if current_line:
            lines.append(" ".join(current_line))
            
        # حساب الموضع الرأسي ليكون في منتصف الشاشة تقريباً
        y_offset = int(target_height * 0.4) 
        
        for line in lines:
            # حساب عرض النص لتوسطه أفقياً
            text_width = draw.textlength(line, font=font)
            x = (target_width - text_width) // 2
            
            # رسم الظل (باللون الأسود وبإزاحة خفيفة)
            shadow_offset = 6
            draw.text((x + shadow_offset, y_offset + shadow_offset), line, font=font, fill=(0, 0, 0, 200))
            
            # رسم النص الأساسي (باللون الأبيض النقي أو المائل قليلاً للرمادي البارد)
            draw.text((x, y_offset), line, font=font, fill=(240, 240, 240, 255))
            
            y_offset += 130 # المسافة بين الأسطر
            
        # حفظ الصورة النهائية جاهزة للاستخدام كإطار أول للفيديو
        img.convert("RGB").save(output_path, "PNG")
        print(f"[+] تم إنشاء الغلاف السينمائي بنجاح وحفظه في: {output_path}")
        return output_path
        
    except Exception as e:
        print(f"[-] خطأ أثناء معالجة الصورة: {e}")
        return None

if __name__ == "__main__":
    # هذا الجزء مخصص للاختبار أو التشغيل المباشر عبر GitHub Actions
    # قراءة المتغيرات من البيئة (Environment Variables) التي يمررها GitHub Actions
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "YOUR_PEXELS_API_KEY_HERE")
    SEARCH_QUERY = os.getenv("SEARCH_QUERY", "liminal space horror")
    VIDEO_TITLE = os.getenv("VIDEO_TITLE", "UNSEEN REALITY \n INCIDENT #04")
    FONT_FILE = os.getenv("FONT_FILE", "vcr_osd_mono.ttf") # ارفع ملف الخط في نفس المستودع
    
    print("[*] بدء تشغيل سكريبت توليد الأغلفة السينمائية...")
    
    # تنفيذ الخطوات
    downloaded_img = download_pexels_image(PEXELS_API_KEY, SEARCH_QUERY)
    if downloaded_img:
        create_cinematic_thumbnail(downloaded_img, VIDEO_TITLE, font_path=FONT_FILE)
