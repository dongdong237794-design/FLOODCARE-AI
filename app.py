import os
import json
import math
import datetime
from flask import Flask, request, abort, render_template_string

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction
)

# Gemini AI
import google.generativeai as genai

# Google Sheets
import gspread

app = Flask(__name__)

# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

# ระบบติดตามสถานะการสนทนาและเก็บข้อมูลคัดกรอง
USER_STATES = {}
USER_DATA = {}

# รายชื่อศูนย์อพยพจำลอง (ตัวสำรองระบบหาก Google Sheets ยังทำงานไม่สมบูรณ์)
FALLBACK_SHELTERS = [
    {"name": "ศูนย์อพยพวัดเสาชิงช้า", "lat": 13.7523, "lon": 100.5015, "capacity": 200, "occupancy": 85, "status": "ว่าง"},
    {"name": "ศูนย์อพยพโรงเรียนวัดสุทัศน์", "lat": 13.7511, "lon": 100.5002, "capacity": 150, "occupancy": 150, "status": "เต็ม"}
]

# เริ่มใช้งาน LINE API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# เริ่มใช้งาน Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=(
        "คุณคือ FLOODCARE AI ผู้ช่วยอัจฉริยะและผู้เชี่ยวชาญด้านอุทกภัยประจำประเทศไทย "
        "มีบทบาทคอยตอบคำถามและให้คำแนะนำในการเอาชีวิตรอดและการรับมือภัยน้ำท่วมอย่างถูกต้องตามหลักสากล\n\n"
        "กฎเหล็กในการตอบและจัดรูปแบบข้อความเพื่อนำไปแสดงผลบน LINE App:\n"
        "1. น้ำเสียงและสรรพนาม: สุภาพ อบอุ่น อ่อนโยน และเป็นกันเองเสมือนคนในครอบครัวคอยดูแลกัน "
        "ให้ใช้คำลงท้ายว่า 'ครับ' หรือ 'นะครับ' เป็นหลักเพื่อความสม่ำเสมอ (หลีกเลี่ยงการสะกดสแลช เช่น 'ครับ/ค่ะ' หรือ 'นะครับ/คะ' ที่ดูแข็งทื่อแบบหุ่นยนต์)\n"
        "2. ความกระชับ: ตอบให้กระชับ ได้ใจความสั้นๆ ไม่ยาวเป็นเรียงความ และแบ่งย่อหน้าให้เหมาะสมกับการอ่านบนหน้าจอมือถือ\n"
        "3. รูปแบบสัญลักษณ์: ห้ามใช้เครื่องหมายดอกจัน (*) ในการทำสัญลักษณ์หัวข้อย่อยหรือเน้นคำเด็ดขาด "
        "แต่ให้ใช้ 'อิโมจิ' ที่แสดงอารมณ์อบอุ่นและปลอดภัยแทนสัญลักษณ์นำหน้าหัวข้อย่อยเสมอ (เช่น 📌, 🏃, 🩹, 📞, 💬, ⚠️, 🟢, 🔴) เพื่อความเป็นระเบียบและสวยงาม\n"
        "4. ความปลอดภัยสูงสุด: ห้ามเดาข้อมูลหรือจินตนาการสิ่งที่ไม่เป็นความจริงเด็ดขาด หากข้อมูลใดไม่แน่ชัด หรือเป็นกรณีฉุกเฉินเฉพาะหน้า "
        "ให้แสดงความห่วงใยและแนะนำเบอร์โทรสายด่วนภัยพิบัติที่ถูกต้องทันที เช่น สายด่วน ปภ. 1784 หรือสายด่วนกู้ชีพ 1669"
    )
)

# 2. ฟังก์ชันตัวกรองลบเครื่องหมายดอกจัน (*)
def clean_text_for_line(text):
    if not text:
        return ""
    cleaned = text.replace("**", "").replace("*", "")
    return cleaned

# 3. ฟังก์ชันคัดกรองดึงรหัส Google Sheet ID ออกจาก URL ลิงก์ยาวโดยอัตโนมัติ
def extract_sheet_id(sheet_var):
    if not sheet_var:
        return ""
    if "/d/" in sheet_var:
        parts = sheet_var.split("/d/")
        if len(parts) > 1:
            sub_parts = parts[1].split("/")
            if len(sub_parts) > 0:
                return sub_parts[0].strip()
    return sheet_var.strip()

# 4. ฟังก์ชันสร้างตาราง คอลัมน์ และกรอกข้อมูลตัวอย่างลง Google Sheets อัตโนมัติ (Auto-Setup)
def setup_sheets_automatically(sheet):
    try:
        existing_sheets = [w.title for w in sheet.worksheets()]
        
        # 1. จัดการชีต SOS_Intake
        if "SOS_Intake" not in existing_sheets:
            print("Creating SOS_Intake worksheet...")
            sos_ws = sheet.add_worksheet(title="SOS_Intake", rows="2000", cols="15")
            headers = [
                "Timestamp", "UserID", "Area", "TotalPeople", "Children", 
                "Elderly", "Bedridden", "Pets", "WaterLevel", "UrgentEvac", 
                "Latitude", "Longitude", "Address", "Priority"
            ]
            sos_ws.append_row(headers)
            
        # 2. จัดการชีต Shelters
        if "Shelters" not in existing_sheets:
            print("Creating Shelters worksheet...")
            shelters_ws = sheet.add_worksheet(title="Shelters", rows="1000", cols="10")
            headers = [
                "ShelterID", "Name", "Province", "District", "Latitude", 
                "Longitude", "Capacity", "Occupancy", "Status"
            ]
            shelters_ws.append_row(headers)
            mock_rows = [
                ["SH001", "ศูนย์อพยพโรงเรียนหาดใหญ่ (วัดโคกสมานคุณ)", "สงขลา", "หาดใหญ่", "7.0095", "100.4682", "500", "120", "ว่าง"],
                ["SH002", "ศูนย์อพยพโรงเรียนวัดสุทัศน์ (กทม)", "กรุงเทพ", "พระนคร", "13.7511", "100.5002", "150", "45", "ว่าง"]
            ]
            for r in mock_rows:
                shelters_ws.append_row(r)
            
        # 3. จัดการชีต AI Logs
        if "AI Logs" not in existing_sheets:
            print("Creating AI Logs worksheet...")
            logs_ws = sheet.add_worksheet(title="AI Logs", rows="5000", cols="5")
            headers = ["Timestamp", "UserID", "Question", "Answer"]
            logs_ws.append_row(headers)
            
        # ลบแท็บเริ่มต้นเพื่อความสะอาด
        for default_name in ["ชีต1", "Sheet1"]:
            if default_name in existing_sheets:
                try:
                    default_ws = sheet.worksheet(default_name)
                    sheet.del_worksheet(default_ws)
                except:
                    pass
        print("Auto-setup Google Sheets structure completed successfully!")
    except Exception as e:
        print(f"Error in automatic sheet setup: {e}")

# ตัวแปรควบคุมการตั้งค่าระบบเพียงครั้งเดียวต่อการรันเซิร์ฟเวอร์
SHEETS_INITIALIZED = False

# 5. ฟังก์ชันเชื่อมต่อ Google Sheets แบบ Native ยุคใหม่
def get_sheets_client():
    global SHEETS_INITIALIZED
    clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)
    
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not clean_sheet_id:
        print("Warning: Google Sheets variables are not configured yet.")
        return None
    try:
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        client = gspread.service_account_from_dict(creds_dict)
        
        if not SHEETS_INITIALIZED:
            try:
                sheet = client.open_by_key(clean_sheet_id)
                setup_sheets_automatically(sheet)
                SHEETS_INITIALIZED = True
            except Exception as setup_err:
                print(f"Auto-setup sheet failed: {setup_err}")
                
        return client
    except Exception as e:
        print(f"Error initializing Google Sheets client: {e}")
        return None

# 6. ฟังก์ชันคำนวณระยะทางภูมิศาสตร์
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# 7. ฟังก์ชันวิเคราะห์ระดับความเร่งด่วนตามหลักกู้ภัยสากล (Triage Priority Calculator)
def calculate_priority(data):
    try:
        bedridden = str(data.get("bedridden", "")).strip()
        water_level = str(data.get("water_level", "")).strip()
        urgent = str(data.get("urgent_evac", "")).strip()
        
        is_bedridden = "มี" in bedridden or "ใช่" in bedridden or "yes" in bedridden.lower()
        is_urgent = "ต้องการ" in urgent or "ด่วน" in urgent or "yes" in urgent.lower()
        
        is_critical_water = False
        for word in water_level.split():
            if ("เมตร" in word or "m" in word.lower()) and any(char.isdigit() for char in word):
                digits = ''.join(filter(lambda x: x.isdigit() or x == '.', word))
                if digits and float(digits) >= 1.0:
                    is_critical_water = True
            elif "มิดหัว" in water_level or "ท่วมหัว" in water_level or "หน้าอก" in water_level:
                is_critical_water = True

        if is_bedridden or is_critical_water or is_urgent:
            return "🔴  เร่งด่วนมาก"
        elif "เอว" in water_level or "เข่า" in water_level or "สูง" in water_level:
            return "🟠  ปานกลาง"
        else:
            return "🟢  ติดตามสถานการณ์"
    except Exception as e:
        print(f"Priority Calc Error: {e}")
        return "🟠  ปานกลาง"

# 8. หน้าหลักเช็กสถานะการรันเซิร์ฟเวอร์อย่างง่าย
@app.route("/", methods=['GET'])
def index():
    return "<h2 style='font-family: sans-serif; text-align: center; margin-top: 100px; color: #1E3A8A;'>🤖 FLOODCARE AI Service is Running Active!</h2>"

# 9. Command Center Web Dashboard สำหรับหน่วยงานกู้ภัย
@app.route("/dashboard", methods=['GET'])
def dashboard():
    sheets_client = get_sheets_client()
    clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)
    sos_cases = []
    shelters = []
    error_msg = ""
    
    if not sheets_client:
        error_msg = "⚠️ ยังไม่ได้ป้อนหรือตั้งค่ารหัสสิทธิ์ของ Google Sheets บนระบบ Render ครับ"
    else:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            
            # 1. ดึงข้อมูลกรณีฉุกเฉินผู้ประสบภัย (SOS_Intake)
            try:
                sos_worksheet = sheet.worksheet("SOS_Intake")
                sos_cases = sos_worksheet.get_all_records()
                sos_cases.reverse()
            except Exception as e:
                print(f"Failed to load SOS: {e}")
                
            # 2. ดึงข้อมูลศูนย์อพยพจริง (Shelters)
            try:
                shelters_worksheet = sheet.worksheet("Shelters")
                shelters = shelters_worksheet.get_all_records()
            except Exception as e:
                print(f"Failed to load Shelters: {e}")
                
        except Exception as e:
            error_msg = f"ไม่สามารถเข้าถึงฐานข้อมูลกลางได้: {e}"

    total_cases = len(sos_cases)
    urgent_count = sum(1 for c in sos_cases if "🔴" in str(c.get("Priority", "")))
    medium_count = sum(1 for c in sos_cases if "🟠" in str(c.get("Priority", "")))
    bedridden_count = sum(1 for c in sos_cases if "มี" in str(c.get("Bedridden", "")) or "ใช่" in str(c.get("Bedridden", "")))
    
    html_template = """
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>COMMAND CENTER — FLOODCARE AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen font-sans">
        <div class="container mx-auto p-4 md:p-6">
            <header class="flex flex-col md:flex-row justify-between items-center pb-6 mb-6 border-b border-slate-800">
                <div class="flex items-center space-x-3">
                    <span class="text-4xl">🚨</span>
                    <div>
                        <h1 class="text-2xl font-bold tracking-wide">FLOODCARE AI</h1>
                        <p class="text-sm text-slate-400">ศูนย์ประสานงานและรายงานเหตุภัยอุทกภัยอัจฉริยะ ( COMMAND CENTER )</p>
                    </div>
                </div>
                <div class="mt-4 md:mt-0 bg-slate-800 px-4 py-2 rounded-lg border border-slate-700 text-sm">
                    🟢 ดึงข้อมูลแบบเรียลไทม์สำเร็จ
                </div>
            </header>

            {% if error_msg %}
            <div class="bg-red-900/50 border border-red-500 text-red-200 p-4 rounded-lg mb-6">
                {{ error_msg }}
            </div>
            {% endif %}

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <p class="text-sm text-slate-400">เคสแจ้งเหตุทั้งหมด</p>
                    <p class="text-3xl font-extrabold text-blue-400 mt-1">{{ total_cases }} <span class="text-lg font-normal">เคส</span></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-red-900/50 bg-red-950/20">
                    <p class="text-sm text-red-300">🔴 เคสเร่งด่วนมาก</p>
                    <p class="text-3xl font-extrabold text-red-500 mt-1">{{ urgent_count }} <span class="text-lg font-normal">เคส</span></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-orange-900/50 bg-orange-950/20">
                    <p class="text-sm text-orange-300">🟠 เคสระดับปานกลาง</p>
                    <p class="text-3xl font-extrabold text-orange-500 mt-1">{{ medium_count }} <span class="text-lg font-normal">เคส</span></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <p class="text-sm text-slate-400">ผู้ป่วยติดเตียงที่ต้องการช่วย</p>
                    <p class="text-3xl font-extrabold text-purple-400 mt-1">{{ bedridden_count }} <span class="text-lg font-normal">ราย</span></p>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div class="lg:col-span-2 bg-slate-800 rounded-xl border border-slate-700 p-4 overflow-hidden">
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-lg font-semibold flex items-center space-x-2">
                            <span>📋</span> <span>รายการขอความช่วยเหลือฉุกเฉิน</span>
                        </h2>
                        <input id="searchInput" onkeyup="filterCases()" type="text" placeholder="🔍 ค้นหาพื้นที่..." class="bg-slate-900 border border-slate-700 text-sm px-3 py-1.5 rounded-lg text-slate-200 focus:outline-none focus:border-blue-500">
                    </div>
                    
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse text-sm">
                            <thead>
                                <tr class="border-b border-slate-700 text-slate-400">
                                    <th class="py-3 px-2">ระดับภัย</th>
                                    <th class="py-3 px-2">พื้นที่/จุดพิกัด</th>
                                    <th class="py-3 px-2">ข้อมูลผู้ประสบภัย</th>
                                    <th class="py-3 px-2">ระดับน้ำ</th>
                                    <th class="py-3 px-2">การกู้ภัย</th>
                                </tr>
                            </thead>
                            <tbody id="sosTable">
                                {% for case in sos_cases %}
                                <tr class="border-b border-slate-700/50 hover:bg-slate-750/30 transition duration-150 py-3">
                                    <td class="py-3 px-2 font-bold">{{ case.get('Priority', '🟢 ตรวจสอบ') }}</td>
                                    <td class="py-3 px-2">
                                        <p class="font-semibold text-slate-200">{{ case.get('Area', 'ไม่ระบุ') }}</p>
                                        <p class="text-xs text-slate-500 mt-0.5">{{ case.get('Timestamp', '') }}</p>
                                    </td>
                                    <td class="py-3 px-2">
                                        <p class="text-slate-300">รวม <b>{{ case.get('TotalPeople', '1') }}</b> คน (เด็ก: {{ case.get('Children', '-') }}, ชรา: {{ case.get('Elderly', '-') }})</p>
                                        <p class="text-xs text-purple-300 mt-1">ผู้ป่วยติดเตียง: {{ case.get('Bedridden', 'ไม่มี') }} | สัตว์เลี้ยง: {{ case.get('Pets', 'ไม่มี') }}</p>
                                    </td>
                                    <td class="py-3 px-2 font-semibold text-sky-400">{{ case.get('WaterLevel', '-') }}</td>
                                    <td class="py-3 px-2">
                                        <a href="https://www.google.com/maps/search/?api=1&query={{ case.get('Latitude', 0) }},{{ case.get('Longitude', 0) }}" target="_blank" class="inline-flex items-center px-3 py-1.5 bg-red-600 hover:bg-red-700 transition font-bold text-xs text-white rounded-lg shadow-md shadow-red-950/20">
                                            🗺️ แผนที่นำทาง
                                        </a>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="bg-slate-800 rounded-xl border border-slate-700 p-4">
                    <h2 class="text-lg font-semibold flex items-center space-x-2 mb-4">
                        <span>🏠</span> <span>สถานะศูนย์อพยพจริงในระบบ</span>
                    </h2>
                    <div class="space-y-4">
                        {% for sh in shelters %}
                        <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-750">
                            <div class="flex justify-between items-start mb-2">
                                <div>
                                    <p class="font-bold text-slate-200">{{ sh.get('Name', 'ไม่ระบุ') }}</p>
                                    <p class="text-xs text-slate-500 mt-0.5">{{ sh.get('District', '') }} จ.{{ sh.get('Province', '') }}</p>
                                </div>
                                <span class="px-2 py-0.5 text-xs font-semibold rounded {{ 'bg-red-950 text-red-400' if sh.get('Status') == 'เต็ม' else 'bg-green-950 text-green-400' }}">
                                    {{ sh.get('Status', 'ว่าง') }}
                                </span>
                            </div>
                            <div class="w-full bg-slate-800 rounded-full h-2 mt-3">
                                <div class="bg-blue-500 h-2 rounded-full" style="width: {{ (sh.get('Occupancy', 0)|int / sh.get('Capacity', 100)|int * 100)|round|int if sh.get('Capacity', 100)|int > 0 else 0 }}%"></div>
                            </div>
                            <div class="flex justify-between items-center text-xs text-slate-400 mt-2">
                                <span>เข้าพัก: {{ sh.get('Occupancy', 0) }} / {{ sh.get('Capacity', 100) }} คน</span>
                                <span>ติดต่อ: {{ sh.get('Contact', '-') }}</span>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>

        <script>
            function filterCases() {
                var input = document.getElementById("searchInput");
                var filter = input.value.toLowerCase();
                var table = document.getElementById("sosTable");
                var tr = table.getElementsByTagName("tr");

                for (var i = 0; i < tr.length; i++) {
                    var areaCell = tr[i].getElementsByTagName("td")[1];
                    if (areaCell) {
                        var textValue = areaCell.textContent || areaCell.innerText;
                        if (textValue.toLowerCase().indexOf(filter) > -1) {
                            tr[i].style.display = "";
                        } else {
                            tr[i].style.display = "none";
                        }
                    }
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template, sos_cases=sos_cases, shelters=shelters, error_msg=error_msg, total_cases=total_cases, urgent_count=urgent_count, medium_count=medium_count, bedridden_count=bedridden_count)

# 10. รับข้อความตัวอักษรและประมวลผลกระบวนการคัดกรองแบบโต้ตอบ (Intake State Machine)
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # ดึงระดับสถานะการคุยปัจจุบัน
    state = USER_STATES.get(user_id)

    # ==================== ส่วนที่ 10.1: ระบบคัดกรองข้อมูลผู้ประสบภัยอัตโนมัติ (Triage Intake State Machine) ====================
    if state:
        if user_id not in USER_DATA:
            USER_DATA[user_id] = {}

        if state == "sos_q1":
            USER_DATA[user_id]["area"] = user_text
            USER_STATES[user_id] = "sos_q2"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 2. มีคนติดอยู่ในบ้านร่วมกันทั้งหมดกี่คนครับ? (กรุณาระบุจำนวนตัวเลข)"))
            return
        elif state == "sos_q2":
            USER_DATA[user_id]["total_people"] = user_text
            USER_STATES[user_id] = "sos_q3"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 3. ในจำนวนนี้ มีเด็กเล็กกี่คนครับ? (ถ้าไม่มีให้พิมพ์ว่า 'ไม่มี')"))
            return
        elif state == "sos_q3":
            USER_DATA[user_id]["children"] = user_text
            USER_STATES[user_id] = "sos_q4"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 4. มีผู้สูงอายุกี่คนครับ? (ถ้าไม่มีให้พิมพ์ว่า 'ไม่มี')"))
            return
        elif state == "sos_q4":
            USER_DATA[user_id]["elderly"] = user_text
            USER_STATES[user_id] = "sos_q5"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 5. มีผู้ป่วยติดเตียงหรือไม่ครับ? (โปรดพิมพ์ว่า 'มี' หรือ 'ไม่มี')"))
            return
        elif state == "sos_q5":
            USER_DATA[user_id]["bedridden"] = user_text
            USER_STATES[user_id] = "sos_q6"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 6. มีสัตว์เลี้ยงติดอยู่ด้วยไหมครับ? (โปรดพิมพ์ว่า 'มี' หรือ 'ไม่มี')"))
            return
        elif state == "sos_q6":
            USER_DATA[user_id]["pets"] = user_text
            USER_STATES[user_id] = "sos_q7"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 7. ระดับน้ำปัจจุบันสูงเท่าไรแล้วครับ? (เช่น ท่วมเข่า, มิดหัว, หรือระบุหน่วยเมตร)"))
            return
        elif state == "sos_q7":
            USER_DATA[user_id]["water_level"] = user_text
            USER_STATES[user_id] = "sos_q8"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 8. ต้องการอพยพด่วนที่สุดเลยหรือไม่ครับ? (โปรดพิมพ์ว่า 'ต้องการ' หรือ 'ยังไม่ต้องการ')"))
            return
        elif state == "sos_q8":
            USER_DATA[user_id]["urgent_evac"] = user_text
            USER_STATES[user_id] = "waiting_sos_location"
            
            # ส่ง Quick Reply เพื่อให้ผู้ใช้แชร์พิกัด GPS เป็นขั้นตอนสุดท้าย
            location_quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=LocationAction(label="กดแชร์พิกัดกู้ภัย"))
                ]
            )
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(
                    text="📢 ขอบคุณสำหรับข้อมูลครับ! ขั้นตอนสุดท้าย โปรดกดปุ่มแชร์พิกัด 'Location' สีเขียวด้านล่างนี้ เพื่อส่งพิกัดแจ้งกู้ภัยทันทีนะครับ",
                    quick_reply=location_quick_reply
                )
            )
            return

        # ==================== ส่วนที่ 10.2: ระบบคัดกรองคำถามอื่น ๆ ย้อนกลับตามเมนู ====================
        elif state == "waiting_emergency_type":
            USER_STATES.pop(user_id, None)
            prompt = f"ผู้ประสบภัยต้องการติดต่อขอกู้ภัยด้วยเรื่องเฉพาะหน้าคือ: '{user_text}' โปรดระบุเบอร์โทรฉุกเฉินและประสานงานกู้ภัยอย่างสั้น กระชับและสุภาพ"
            try:
                res = gemini_model.generate_content(prompt)
                reply = clean_text_for_line(res.text.strip())
            except:
                reply = "🚨 แนะนำโทรประสานงานเร่งด่วนที่สายด่วนกู้ชีพ 1669 หรือ สายด่วน ปภ. 1784 ครับ"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        elif state == "waiting_first_aid_detail":
            USER_STATES.pop(user_id, None)
            prompt = f"ผู้ใช้ต้องการปฐมพยาบาลเบื้องต้นจากเคส: '{user_text}' โปรดอธิบายขั้นตอนสั้นๆ (1, 2, 3) เพื่อปฐมพยาบาลอย่างปลอดภัย"
            try:
                res = gemini_model.generate_content(prompt)
                reply = clean_text_for_line(res.text.strip())
            except:
                reply = "🩹 แนะนำให้ทำความสะอาดแผลเบื้องต้น ปิดปากแผล และหากรุนแรงโทร 1669 ทันทีครับ"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        elif state == "waiting_water_location":
            USER_STATES.pop(user_id, None)
            prompt = f"ผู้ใช้ต้องการประเมินสถานการณ์น้ำหรือเช็กข้อมูลน้ำท่วมในพื้นที่: '{user_text}' โปรดแนะนำแนวทางเฝ้าระวังภัยพิบัติอย่างสั้นและกระชับ"
            try:
                res = gemini_model.generate_content(prompt)
                reply = clean_text_for_line(res.text.strip())
            except:
                reply = "🌊 แนะนำติดตามการรายงานระดับน้ำอย่างใกล้ชิด และสามารถเช็กระดับลุ่มน้ำได้ผ่านแอปฯ ThaiWater ครับ"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # ==================== ฟีเจอร์ตรวจจับการพิมพ์ค้นหาศูนย์อพยพด้วยชื่อจังหวัดหรือชื่ออำเภอ ====================
        elif state == "waiting_shelter_location":
            USER_STATES.pop(user_id, None)
            shelter_list = []
            db_connected = False
            
            clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)
            sheets_client = get_sheets_client()
            if sheets_client:
                try:
                    sheet = sheets_client.open_by_key(clean_sheet_id)
                    shelters_worksheet = sheet.worksheet("Shelters")
                    rows = shelters_worksheet.get_all_records()
                    for row in rows:
                        sh_name = str(row.get('Name', '')).strip()
                        sh_province = str(row.get('Province', '')).strip()
                        sh_district = str(row.get('District', '')).strip()
                        
                        if user_text in sh_name or user_text in sh_province or user_text in sh_district:
                            vacancy_status = check_shelter_vacancy(row.get('Capacity', 100), row.get('Occupancy', 0))
                            shelter_list.append({
                                "name": sh_name,
                                "province": sh_province,
                                "district": sh_district,
                                "vacancy": vacancy_status,
                                "contact": row.get('Contact', '-'),
                                "lat": row.get('Latitude', 0),
                                "lon": row.get('Longitude', 0)
                            })
                    db_connected = True
                except Exception as e:
                    print(f"Failed to query database: {e}")

            if not db_connected:
                reply_text = "⚠️ ขออภัยครับ ระบบตรวจสอบสิทธิ์ฐานข้อมูล Google Sheets ขัดข้องชั่วคราว โปรดตรวจเช็กคีย์สิทธิ์บน Render หรือลองใหม่อีกครั้งครับ"
            elif not shelter_list:
                reply_text = f"📍 ไม่พบข้อมูลศูนย์พักพิงจริงในพื้นที่ชื่อ '{user_text}' เลยครับ โปรดตรวจสอบการสะกดชื่ออำเภอ/จังหวัด แล้วลองพิมพ์ใหม่อีกครั้งนะครับ"
            else:
                reply_text = f"🏠 รายชื่อศูนย์พักพิงจริงในพื้นที่ '{user_text}' ที่เราพบล่าสุดในระบบฐานข้อมูลครับ:\n\n"
                for index, sh in enumerate(shelter_list, 1):
                    reply_text += (
                        f"{index}️⃣ {sh['name']}\n"
                        f"   📌 ที่ตั้ง: อ.{sh['district']} จ.{sh['province']}\n"
                        f"   📌 สถานะความจุ: {sh['vacancy']}\n"
                        f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
                    )
                reply_text += "⚠️ โปรดโทรตรวจสอบความจุกับทางศูนย์อพยพก่อนออกเดินทาง หรือเดินทางด้วยความระมัดระวังสูงสุดนะครับ"
                
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

    # ==================== ส่วนที่ 10.3: ตรวจสอบการคลิกปุ่มหลักบนเมนู 6 ปุ่ม ====================
    if user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        USER_STATES[user_id] = "waiting_emergency_type"
        reply_text = "📞 คุณต้องการติดต่อประสานงานกู้ภัยด้วยสถานการณ์ฉุกเฉินเรื่องใดเป็นพิเศษไหมครับ? พิมพ์บอกผมสั้นๆ ได้เลยนะครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ปฐมพยาบาลเบื้องต้น":
        USER_STATES[user_id] = "waiting_first_aid_detail"
        reply_text = "🩹 คุณได้รับบาดเจ็บหรือเกิดอุบัติเหตุจากอะไรครับ? พิมพ์แจ้งอาการเพื่อให้ผมช่วยค้นหาวิธีปฐมพยาบาลเฉพาะหน้าด่วนได้เลยนะครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ศูนย์พักพิง":
        USER_STATES[user_id] = "waiting_shelter_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="แชร์พิกัดหาศูนย์พักพิง"))
            ]
        )
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(
                text="📍 โปรดกดแชร์พิกัด 'Location' ด้านล่างนี้ หรือพิมพ์บอกชื่ออำเภอ/จังหวัดที่คุณอยู่ในปัจจุบัน เพื่อให้ผมช่วยค้นหาศูนย์พักพิงจริงรอบตัวคุณครับ",
                quick_reply=location_quick_reply
            )
        )
        
    elif user_text == "ตรวจสอบระดับน้ำ":
        USER_STATES[user_id] = "waiting_water_location"
        reply_text = "🌊 คุณต้องการเช็กหรือประเมินระดับน้ำในพื้นที่เขต/อำเภอ และจังหวัดใดครับ? โปรดพิมพ์ระบุชื่อพื้นที่ของคุณมาได้เลยนะครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "SOS ขอความช่วยเหลือ":
        USER_STATES[user_id] = "sos_q1"
        USER_DATA[user_id] = {} # ล้างข้อมูลเก่า
        reply_text = "🚨 เพื่อจัดเตรียมอุปกรณ์ช่วยเหลือได้ถูกต้อง โปรดตอบข้อมูลสั้นๆ นะครับ\n\n📌 1. บ้านของคุณอยู่พื้นที่บริเวณไหนครับ? (ระบุชื่อหมู่บ้าน ซอย หรือจุดสังเกต)"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ถาม AI เรื่องน้ำท่วม":
        reply_text = "🤖 คุณสามารถพิมพ์รายละเอียดคำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมในครั้งนี้เข้ามาได้ทันทีเลยครับ ผมพร้อมตอบคำถามแบบเป็นกันเองให้ครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    else:
        # ระบบคุยตอบโต้แบบอิสระทั่วไป
        ai_response = ""
        try:
            response = gemini_model.generate_content(user_text)
            ai_response = clean_text_for_line(response.text.strip())
        except Exception as e:
            print(f"Gemini API Error: {e}")
            ai_response = "⚠️ บริการ AI ขัดข้องชั่วคราว หากตกอยู่ในภาวะอันตราย โทร ปภ. 1784 ทันทีครับ"
            
        sheets_client = get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, user_text, ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")
                
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))

# 11. รับข้อมูลพิกัด (Location Message) และประมวลผล GIS / ดึงและเก็บข้อมูลลงแผ่นงาน Google Sheets
@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    address = event.message.address or "ไม่ระบุที่อยู่ชัดเจน"
    title = event.message.title or "จุดพิกัด"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    state = USER_STATES.pop(user_id, "default")
    sheets_client = get_sheets_client()

    # --- ค้นหาศูนย์อพยพใกล้ที่สุดในรัศมี 5-20 กม. (อิงพิกัดและดึงฐานข้อมูลจริงจาก Google Sheets) ---
    if state == "waiting_shelter_location":
        shelter_list = []
        db_connected = False
        
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
                shelters_worksheet = sheet.worksheet("Shelters")
                rows = shelters_worksheet.get_all_records()
                for row in rows:
                    if str(row.get('Status')).strip() == "ปิดทำการ":
                        continue
                    shelter_list.append({
                        "name": row.get('Name', 'ไม่ระบุชื่อ'),
                        "lat": float(row.get('Latitude', 0)),
                        "lon": float(row.get('Longitude', 0)),
                        "capacity": row.get('Capacity', 100),
                        "occupancy": row.get('Occupancy', 0),
                        "status": row.get('Status', 'ว่าง')
                    })
                db_connected = True
            except Exception as e:
                print(f"Failed to fetch shelters from Sheets: {e}")
                
        if not db_connected:
            reply_text = "⚠️ ขออภัยครับ ขณะนี้ระบบขัดข้องไม่สามารถตรวจสอบสิทธิ์การอ่านข้อมูลศูนย์พักพิงจริงได้ โปรดโทรติดต่อเบอร์สายด่วนภัยพิบัติ ปภ. 1784 ทันทีครับ"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
                
        nearest_shelters = []
        for sh in shelter_list:
            distance = calculate_distance(latitude, longitude, sh['lat'], sh['lon'])
            if 5.0 <= distance <= 20.0 or distance < 5.0:
                vacancy_status = check_shelter_vacancy(sh['capacity'], sh['occupancy'])
                nearest_shelters.append({
                    "name": sh['name'],
                    "distance": distance,
                    "vacancy": vacancy_status,
                    "lat": sh['lat'],
                    "lon": sh['lon']
                })
                
        nearest_shelters.sort(key=lambda x: x['distance'])
        top_shelters = nearest_shelters[:3]
        
        if not top_shelters:
            reply_text = "📍 ปัจจุบันไม่พบศูนย์พักพิงจริงเปิดทำการในรัศมี 5-20 กม. รอบพิกัดของคุณครับ แนะนำติดต่อสอบถามพิกัดจัดตั้งชั่วคราวโดยตรงทาง ปภ. 1784 ครับ"
        else:
            reply_text = "📍 รายชื่อศูนย์พักพิงจริงที่อยู่ใกล้ตัวคุณที่สุดในรัศมี 5-20 กม. ครับ:\n\n"
            for index, sh in enumerate(top_shelters, 1):
                reply_text += (
                    f"{index}️⃣ {sh['name']}\n"
                    f"   📌 ระยะห่าง: {sh['distance']:.2f} กิโลเมตร\n"
                    f"   📌 สถานะความจุ: {sh['vacancy']}\n"
                    f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
                )
            reply_text += "⚠️ โปรดเดินเท้าตามเส้นทางหลักอย่างระมัดระวังสูงสุดเสมอนะครับ"
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    # --- กรณีการแจ้งเหตุ SOS คัดกรองและประมวลผล Priority แยกเข้า Google Sheets 'SOS_Intake' ---
    elif state == "waiting_sos_location":
        sos_data = USER_DATA.pop(user_id, {})
        priority = calculate_priority(sos_data)
        
        success = False
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
                sos_worksheet = sheet.worksheet("SOS_Intake")
                sos_worksheet.append_row([
                    timestamp,
                    user_id,
                    sos_data.get("area", "ไม่ระบุพื้นที่"),
                    sos_data.get("total_people", "1"),
                    sos_data.get("children", "ไม่มี"),
                    sos_data.get("elderly", "ไม่มี"),
                    sos_data.get("bedridden", "ไม่มี"),
                    sos_data.get("pets", "ไม่มี"),
                    sos_data.get("water_level", "รอตรวจสอบ"),
                    sos_data.get("urgent_evac", "ไม่ระบุ"),
                    latitude,
                    longitude,
                    f"พิกัดไลน์: {address} ({title})",
                    priority
                ])
                success = True
            except Exception as e:
                print(f"Failed to write SOS to Sheets: {e}")
                
        if success:
            confirm_text = (
                f"🚨 ระดับความเร่งด่วน: {priority}\n\n"
                "ส่งเรื่องและบันทึกข้อมูลขอรับการช่วยเหลือด่วนเข้าศูนย์สเปรดชีตกู้ภัยสำเร็จแล้วนะครับ!\n"
                f"📍 พิกัดส่งทีมกู้ภัย: {latitude}, {longitude}\n"
                f"👥 สรุปข้อมูลผู้ประสบภัย: ยื่นขอช่วยเหลือด่วน {sos_data.get('total_people', '1')} คน (ผู้ป่วยติดเตียง: {sos_data.get('bedridden', 'ไม่มี')})\n"
                f"🌊 ระดับน้ำปัจจุบัน: {sos_data.get('water_level', 'รอตรวจสอบ')}\n\n"
                "ทีมกู้ภัยสามารถเปิดตรวจสอบข้อมูลเชิงลึกทั้งหมดของคุณได้ทันทีแบบเรียลไทม์ โปรดเฝ้ารอด้วยความปลอดภัยสูงสุดนะครับ"
            )
        else:
            confirm_text = (
                f"🚨 ระบบประเมินความเร่งด่วน: {priority}\n\n"
                "ระบบได้รับการยืนยันพิกัด SOS ของคุณแล้วนะครับ!\n"
                f"📍 พิกัดของคุณคือ: {latitude}, {longitude}\n"
                f"👥 ข้อมูลคัดกรอง: บ้านอยู่แถว {sos_data.get('area', 'ไม่ระบุ')}, สมาชิก {sos_data.get('total_people', '1')} คน (ติดเตียง: {sos_data.get('bedridden', 'ไม่มี')})\n\n"
                "*(หมายเหตุ: ข้อมูลประมวลผลสำเร็จทางหลังบ้านแล้ว แต่ไม่พบบัญชีสิทธิ์การเขียนข้อมูลลง Google Sheets ปลายทาง โปรดตรวจเช็กสิทธิ์ Editor ใน Google Sheets ของคุณครับ)"
            )
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))
        
    else:
        confirm_text = "📍 คุณส่งพิกัด GPS มาหาผม หากต้องการแจ้งขอความช่วยเหลือ โปรดกดแตะเมนู 'SOS ขอความช่วยเหลือ' บนแถบด้านล่างก่อนเพื่อให้ทีมกู้ภัยวิเคราะห์ความเร่งด่วนได้อย่างแม่นยำนะครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
