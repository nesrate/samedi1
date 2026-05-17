#from flask import Flask, request, render_template, send_file, jsonify
from flask import Blueprint, request, render_template, send_file, jsonify
import os
import ezdxf
import re
from shapely.geometry import LineString, Point, Polygon, MultiLineString, MultiPolygon
from shapely.ops import polygonize, unary_union, snap, linemerge, nearest_points
import math
import numpy as np
from PIL import Image
import io
import fitz
import easyocr



#bp1 = Blueprint('main', __name__)
bp1 = Blueprint('route1', __name__)


# =========================
# دوال تصحيح OCR
# =========================
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

current_file_path = None

# تهيئة EasyOCR - استخدام اللغات المدعومة فقط
reader = easyocr.Reader(['fr', 'en'])  # فقط اللغات المدعومة



def normalize_title(title):
    """تصحيح الأخطاء الشائعة في OCR"""
    subs = {
        "I": "1",
        "l": "1", 
        "O": "0",
        "S": "5",
        "8": "B",  # تصحيح 8 إلى B للعناوين
    }
    result = "".join(subs.get(c, c) for c in title)
    return result

def title_ends_with_number(title):
    """التحقق من أن العنوان ينتهي برقم"""
    title = title.strip()
    if not title:
        return False
    return title[-1].isdigit()

def is_valid_coordinate(coord_str):
    """التحقق من صحة صيغة الإحداثي"""
    coord_str = coord_str.strip()
    
    # الأنماط المدعومة
    patterns = [
        r'^\d{6}$',           # 123456
        r'^\d{3} \d{3}$',     # 123 456
        r'^\d{6}\.\d{2}$',    # 123456.78
        r'^\d{3} \d{3}\.\d{2}$' # 123 456.78
    ]
    
    for pattern in patterns:
        if re.match(pattern, coord_str):
            return True
    return False

def format_coordinate(coord_str):
    """تنسيق الإحداثي إلى الشكل المطلوب"""
    coord_str = coord_str.strip()
    
    if '.' not in coord_str:
        coord_str = coord_str.replace(' ', '')
        if len(coord_str) == 6:
            return f"{coord_str[:3]} {coord_str[3:]}"
        return coord_str
    
    parts = coord_str.split('.')
    integer_part = parts[0].replace(' ', '')
    decimal_part = parts[1] if len(parts) > 1 else ''
    
    if len(integer_part) == 6:
        return f"{integer_part[:3]} {integer_part[3:]}.{decimal_part}"
    
    return coord_str

def filter_text(input_text):
    """استخراج العناوين التي تنتهي برقم مع الإحداثيات"""
    lines = [l.strip() for l in input_text.splitlines() if l.strip()]
    results = []
    
    i = 0
    total_points = 0
    
    while i < len(lines) - 2:
        title = normalize_title(lines[i])
        
        if not title_ends_with_number(title):
            i += 1
            continue
        
        # تنظيف الإحداثيات
        raw1 = re.sub(r'[^\d. ]', '', lines[i+1]).strip()
        raw2 = re.sub(r'[^\d. ]', '', lines[i+2]).strip()
        
        if is_valid_coordinate(raw1) and is_valid_coordinate(raw2):
            formatted_x = format_coordinate(raw1)
            formatted_y = format_coordinate(raw2)
            
            results.append(title)
            results.append(formatted_x)
            results.append(formatted_y)
            results.append("")  # سطر فارغ للفصل
            
            total_points += 1
            i += 3
        else:
            i += 1
    
    print(f"[معلومات] تم العثور على {total_points} نقطة")
    return "\n".join(results)

# المسارات (Routes)

@bp1.route("/coor", methods=["GET", "POST"])
def coor():   #def index():
    extracted_text = ""
    
    if request.method == "POST":
        file = request.files.get("file")
        
        if file:
            #filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            filepath = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(filepath)
            
            # معالجة PDF
            if file.filename.lower().endswith(".pdf"):
                pdf = fitz.open(filepath)
                
                for page in pdf:
                    text = page.get_text().strip()
                    
                    if text:
                        extracted_text += text + "\n"
                    else:
                        pix = page.get_pixmap()
                        img_bytes = pix.tobytes("png")
                        image = Image.open(io.BytesIO(img_bytes))
                        image_np = np.array(image)
                        result = reader.readtext(image_np)
                        
                        for (_, t, _) in result:
                            extracted_text += t + "\n"
            
            # معالجة الصور
            else:
                result = reader.readtext(filepath)
                for (_, t, _) in result:
                    extracted_text += t + "\n"
    
    return render_template("coor.html", text=extracted_text)
#     return render_template("coor.html", text=extracted_text)

@bp1.route("/filter2", methods=["POST"])
def filter_api():
    """API للفلترة"""
    data = request.json
    text = data.get("text", "")
    
    filtered = filter_text(text)
    
    return jsonify({"result": filtered})

@bp1.route("/generate_dxf2", methods=["POST"])
def generate_dxf2():
    """توليد ملف DXF"""
    xs = request.form.getlist("x[]")
    ys = request.form.getlist("y[]")
    titles = request.form.getlist("title[]")
    
    points = []
    valid_titles = []
    
    for x, y, title in zip(xs, ys, titles):
        if x and y:
            try:
                # تحويل الإحداثيات إلى float (مع دعم التنسيقات المختلفة)
                x_clean = x.replace(' ', '')
                y_clean = y.replace(' ', '')
                points.append((float(x_clean), float(y_clean)))
                valid_titles.append(title)
            except ValueError:
                continue
    
    if not points:
        return "لم يتم اكتشاف أي نقاط", 400
    
    # إنشاء ملف DXF
    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()
    
    # إعدادات الرسم
    mark_length = 3.5
    circle_diameter = 2.0
    circle_radius = circle_diameter / 2
    
    # إضافة النقاط
    for (x, y), title in zip(points, valid_titles):
        # إضافة النقطة
        msp.add_point((x, y))
        
        # إضافة النص
        text = msp.add_text(title, dxfattribs={'height': 1.5})
        text.dxf.insert = (x + 1, y + 1)
        
        # رسم علامة الصليب
        msp.add_line((x - mark_length/2, y), (x + mark_length/2, y))
        msp.add_line((x, y - mark_length/2), (x, y + mark_length/2))
        
        # رسم الدائرة
        msp.add_circle(center=(x, y), radius=circle_radius)
    
    # رسم الخط الواصل بين النقاط
    if len(points) > 1:
        msp.add_lwpolyline(points, close=True)
    
    file_name = "points++.dxf"
    doc.saveas(file_name)
    
    return send_file(file_name, as_attachment=True, download_name="النقاط.dxf")

#--------------------------------------------------------------------------------------------------------------------------------------------------------------------
#--------------------------------------------------------------------------------------------------------------------------------------------------------------------
#--------------------------------------------------------------------------------------------------------------------------------------------------------------------



#if __name__ == "__main__":
#    app.run(debug=True)
if __name__ == "__main__":
    from flask import Flask
    test_app = Flask(__name__)
    test_app.register_blueprint(bp1)
    test_app.run(debug=True)