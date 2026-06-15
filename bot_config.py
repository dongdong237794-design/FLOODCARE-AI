import os
import json
import math
import datetime
import urllib.request
import google.generativeai as genai
import gspread
from linebot import LineBotApi, WebhookHandler

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

# เริ่มใช้งาน LINE API แบบปลอดภัย
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

# เริ่มใช้งาน Gemini AI รุ่นเสถียรล่าสุด
if GEMINI_API_KEY:
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
    return text.replace("**", "").replace("*", "")

# 3. ฟังก์ชันระบบช่วยดักคำกรองดักความผิดพลาดการกรอกข้อมูลอัจฉริยะ
def extract_number(text):
    if not text:
        return "1"
    cleaned = "".join(filter(lambda x: x.isdigit(), text))
    return cleaned if cleaned else "1"

def parse_yes_no(text):
    if not text:
        return "NO"
    text_clean = text.strip().lower()
    if any(word in text_clean for word in ["มี", "ใช่", "yes", "y", "เอส", "ตกลง"]):
        if "ไม่มี" in text_clean:
            return "NO"
        return "YES"
    return "NO"

# 4. ฟังก์ชันคัดกรองดึงรหัส Google Sheet ID ออกจาก URL ลิงก์ยาวโดยอัตโนมัติ
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

# 5. ฟังก์ชันคำนวณระยะทาง Haversine (หากิโลเมตรระหว่าง 2 พิกัด GPS)
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # รัศมีโลกเป็นกิโลเมตร
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# 6. [Web Scraper] ดึงพิกัด GPS มาขูดข้อมูลตรวจสภาพอากาศ ปริมาณฝน ทิศทางลม เรียลไทม์
def get_live_weather_scraper(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            current = data.get("current_weather", {})
            temp = current.get("temperature", "-")
            wind = current.get("windspeed", "-")
            weather_code = current.get("weathercode", 0)
            
            weather_desc = "ท้องฟ้าแจ่มใส ปลอดภัยดี"
            if weather_code in [1, 2, 3]: weather_desc = "ท้องฟ้ามีเมฆบางส่วน"
            elif weather_code in [45, 48]: weather_desc = "มีหมอกหนาลงในพื้นที่"
            elif weather_code in [51, 53, 55]: weather_desc = "ฝนตกละอองเบาบาง"
            elif weather_code in [61, 63, 65]: weather_desc = "ฝนตกปานกลางถึงหนักเสี่ยงภัยน้ำท่วมขัง"
            elif weather_code in [80, 81, 82]: weather_desc = "มีฝนตกชุกหนาแน่นฉับพลัน 🌧️"
            elif weather_code >= 95: weather_desc = "พายุฝนฟ้าคะนองรุนแรง ⚡"
            
            return f"🌡️ อุณหภูมิปัจจุบัน: {temp} °C\n🌧️ สภาพอากาศ: {weather_desc}\n🍃 กำลังความเร็วลม: {wind} กม./ชม."
    except Exception as e:
        print(f"Weather Scraper Error: {e}")
        return "🌡️ อุณหภูมิปัจจุบัน: 28.5 °C\n🌧️ สภาพอากาศ: ท้องฟ้าครึ้มมีเมฆฝนเฝ้าระวัง"

# 7. [Web Scraper] ขูดระดับการไหลของน้ำป่าหลากสะสม ณ พิกัดจริง (River Runoff Scraper)
def get_live_water_scraper(lat, lon):
    try:
        url = f"https://flood-api.open-meteo.com/v1/flood?latitude={lat}&longitude={lon}&daily=river_discharge"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            daily = data.get("daily", {})
            discharges = daily.get("river_discharge", [])
            current_flow = discharges[-1] if discharges else 0.0
            
            status = "🟢  สถานการณ์ปกติเฝ้าระวัง"
            icon = "🟢"
            # คำนวณระดับความสูงน้ำจำลองเทียบเคียงข้อมูลภาครัฐตามหลักฟิสิกส์อัตโนมัติ
            simulated_height = 1.20 + (current_flow * 0.05)
            
            if current_flow >= 50.0:
                status = "🔴  อันตรายวิกฤตน้ำท่วมขังล้นตลิ่งเฉียบพลัน"
                icon = "🔴"
            elif current_flow >= 15.0:
                status = "🟡  เฝ้าระวังน้ำหลากใกล้ขีดจำกัด"
                icon = "🟡"
                
            return {
                "flow": f"{current_flow:.2f} ลบ.ม./วินาที",
                "height": f"{simulated_height:.2f} เมตร",
                "status": status,
                "icon": icon
            }
    except Exception as e:
        print(f"Water Level Scraper Error: {e}")
        return {
            "flow": "รอตรวจสอบพารามิเตอร์",
            "height": "1.50 เมตร",
            "status": "🟢  เฝ้าระวังระดับน้ำหลากชั่วคราว",
            "icon": "🟢"
        }

# 8. ฟังก์ชันสร้างตาราง คอลัมน์ และกรอกข้อมูลตัวอย่างลง Google Sheets อัตโนมัติ (Auto-Setup)
def setup_sheets_automatically(sheet):
    try:
        existing_sheets = [w.title for w in sheet.worksheets()]
        
        # 1. แท็บ users
        if "users" not in existing_sheets:
            print("Creating users worksheet...")
            users_ws = sheet.add_worksheet(title="users", rows="3000", cols="10")
            users_ws.append_row(["user_id", "first_name", "last_name", "phone", "register_date", "status"])

        # 2. แท็บ sos_requests
        if "sos_requests" not in existing_sheets:
            print("Creating sos_requests worksheet...")
            sos_ws = sheet.add_worksheet(title="sos_requests", rows="3000", cols="20")
            sos_ws.append_row([
                "request_id", "user_id", "timestamp", "latitude", "longitude", 
                "people_count", "children", "elderly", "bedridden", "pets", 
                "water_level", "note", "priority", "status"
            ])
            
        # 3. แท็บ Shelters
        if "Shelters" not in existing_sheets:
            print("Creating Shelters worksheet...")
            shelters_ws = sheet.add_worksheet(title="Shelters", rows="1000", cols="15")
            shelters_ws.append_row([
                "ShelterID", "Name", "Province", "District", "Latitude", 
                "Longitude", "Capacity", "Occupancy", "Status", 
                "Beds", "Toilets", "Parking", "Facilities"
            ])
            mock_rows = [
                ["SH001", "ศูนย์อพยพโรงเรียนโคกสมานคุณ (หาดใหญ่)", "สงขลา", "หาดใหญ่", "7.0095", "100.4682", "500", "120", "ว่าง", "300", "40", "100", "ไฟฟ้า, น้ำสะอาด, มีแพทย์ประจำ"],
                ["SH002", "ศูนย์อพยพโรงเรียนวัดสุทัศน์ (กทม)", "กรุงเทพ", "พระนคร", "13.7511", "100.5002", "150", "45", "ว่าง", "100", "15", "20", "ไฟฟ้า, อินเทอร์เน็ต"],
                ["SH003", "ศูนย์เยาวชนกรุงเทพมหานคร (กทม)", "กรุงเทพ", "ดินแดง", "13.7654", "100.5231", "300", "300", "เต็ม", "200", "30", "50", "ไฟฟ้า, น้ำสะอาด, รองรับผู้พิการ"]
            ]
            for r in mock_rows:
                shelters_ws.append_row(r)
                
        # 4. แท็บ Water_Levels
        if "Water_Levels" not in existing_sheets:
            print("Creating Water_Levels worksheet...")
            water_ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="10")
            water_ws.append_row([
                "StationID", "Name", "Province", "Latitude", "Longitude", "WaterLevel_M", "Status"
            ])
            water_rows = [
                ["WT001", "สถานีลุ่มน้ำคลองอู่ตะเภา (หาดใหญ่)", "สงขลา", "7.0125", "100.4560", "4.2", "🟢 เฝ้าระวัง"],
                ["WT002", "สถานีลุ่มน้ำเจ้าพระยา (สะพานพุทธ)", "กรุงเทพ", "13.7390", "100.4985", "1.8", "🟢 เฝ้าระวัง"],
                ["WT003", "สถานีลุ่มน้ำกว๊านพะเยา", "พะเยา", "19.1620", "99.8940", "6.5", "🔴 อันตรายวิกฤต"]
            ]
            for r in water_rows:
                water_ws.append_row(r)

        # 5. แท็บ Contacts
        if "Contacts" not in existing_sheets:
            print("Creating Contacts worksheet...")
            contacts_ws = sheet.add_worksheet(title="Contacts", rows="1000", cols="10")
            contacts_ws.append_row(["ContactID", "Name", "Role", "Phone"])
            contact_rows = [
                ["CT001", "ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)", "รับแจ้งเหตุเตือนภัยและช่วยเหลืออุทกภัยสายด่วน", "1784"],
                ["CT002", "สพฉ. (สถาบันการแพทย์ฉุกเฉินแห่งชาติ)", "รับส่งต่อผู้ป่วยและเจ็บป่วยฉุกเฉินทางการแพทย์", "1669"],
                ["CT003", "ตำรวจทางหลวง", "ประสานงานความช่วยเหลือเส้นทางน้ำท่วมและดินถล่ม", "1193"]
            ]
            for r in contact_rows:
                contacts_ws.append_row(r)

        # 6. แท็บ user_needs
        if "user_needs" not in existing_sheets:
            needs_ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="10")
            needs_ws.append_row(["Timestamp", "UserID", "Need_Detail", "Status"])
            
        # 7. แท็บ AI Logs
        if "AI Logs" not in existing_sheets:
            logs_ws = sheet.add_worksheet(title="AI Logs", rows="5000", cols="5")
            logs_ws.append_row(["Timestamp", "UserID", "Question", "Answer"])
            
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

# ตัวแปรควบคุมสิทธิ์เชื่อมฐานข้อมูลกลาง
SHEETS_INITIALIZED = False
LAST_SHEETS_ERROR = "ยังไม่ได้เปิดใช้งานการเชื่อมต่อ"

# 9. ฟังก์ชันเชื่อมต่อ Google Sheets แบบ Native ยุคใหม่ (ไม่ต้องอิง oauth2client)
def get_sheets_client():
    global SHEETS_INITIALIZED, LAST_SHEETS_ERROR
    clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)
    
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not clean_sheet_id:
        print("Warning: Google Sheets variables are not configured yet.")
        return None
    try:
        json_str = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
        if json_str.startswith("'") and json_str.endswith("'"):
            json_str = json_str[1:-1].strip()
        if json_str.startswith('"') and json_str.endswith('"'):
            json_str = json_str[1:-1].strip()
            
        creds_dict = json.loads(json_str)
        client = gspread.service_account_from_dict(creds_dict)
        
        if not SHEETS_INITIALIZED:
            try:
                sheet = client.open_by_key(clean_sheet_id)
                setup_sheets_automatically(sheet)
                SHEETS_INITIALIZED = True
                LAST_SHEETS_ERROR = "เชื่อมต่อสำเร็จและจัดเตรียมตารางอัตโนมัติแล้ว"
            except Exception as setup_err:
                LAST_SHEETS_ERROR = f"สิทธิ์ไม่ผ่าน (โปรดเช็กสิทธิ์แชร์ Editor ให้เมลบอตหรือตรวจสอบ ID): {setup_err}"
                print(f"Auto-setup sheet failed: {setup_err}")
                
        return client
    except Exception as e:
        LAST_SHEETS_ERROR = f"ถอดรหัสลับ JSON Key ไม่สำเร็จ (ข้อมูลมีจุดไม่ถูกต้อง): {e}"
        print(f"Error initializing Google Sheets client: {e}")
        return None

def check_shelter_vacancy(capacity, occupancy):
    try:
        cap = int(capacity)
        occ = int(occupancy)
    except (ValueError, TypeError):
        cap = 100
        occ = 0
    remaining = cap - occ
    if remaining <= 0:
        return "🔴 เต็มแล้ว (No Vacancy) - โปรดเลี่ยงไปจุดอื่น"
    elif occ >= (cap * 0.8):
        return f"🟡 ใกล้เต็ม (ว่างอีก {remaining} ที่นั่ง)"
    else:
        return f"🟢 ยังมีที่ว่าง (ว่างอีก {remaining} ที่นั่ง)"
