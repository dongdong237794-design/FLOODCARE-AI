import os
import json
import math
import datetime
import time
import requests
import google.generativeai as genai
import gspread
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent,
    SeparatorComponent, ButtonComponent, URIAction
)

# =============================================================================
# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
# =============================================================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

# ระบบติดตามสถานะการสนทนาและเก็บข้อมูลคัดกรอง
USER_STATES = {}
USER_DATA = {}

# เริ่มใช้งาน LINE API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

# เริ่มใช้งาน Gemini AI คัดกรองบทบาท "ผู้นำทีมกู้ภัยฉุกเฉิน" (Rescue Incident Commander)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

gemini_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=(
        "คุณคือ FLOODCARE AI ผู้นำทีมกู้ภัยอัจฉริยะในภาวะวิกฤต (Rescue Incident Commander) "
        "ทำหน้าที่ประเมินและสั่งการช่วยเหลือผู้ประสบอุทกภัยอย่างเป็นระบบและถูกต้องตามหลักสากล\n\n"
        "กฎเหล็กการสนทนาและการแสดงผลบนไลน์ (LINE App):\n"
        "1. น้ำเสียงและสรรพนาม: นิ่ง สุภาพ แต่เด็ดขาด ชัดเจน และมั่นคง (Calm & Authoritative) เพื่อลดความตระหนกของผู้ประสบภัย "
        "ใช้สรรพนามลงท้ายว่า 'ครับ' หรือ 'นะครับ' เพื่อรักษามาตรฐานความน่าเชื่อถือระดับมืออาชีพ\n"
        "2. การตรวจจับความวิกฤต (Emergency Triage): หากผู้ใช้ส่งข้อความที่มีคำว่า 'ช่วยด้วย', 'จมน้ำ', 'จะตาย', 'SOS' "
        "ให้หยุดการคุยทั่วไปทันที แล้วส่งขั้นตอนเอาตัวรอด 3 ข้อหลัก (1.ตัดไฟขึ้นที่สูง 2.เตรียมอุปกรณ์ส่องสว่าง/นกหวีด 3.ประหยัดแบตเตอรี่มือถือ) "
        "และแสดงเบอร์สายด่วน ปภ. 1784 และกู้ชีพ 1669 ด่วนที่สุด\n"
        "3. ความน่าเชื่อถือของข้อมูล: อ้างอิงข้อมูลระดับน้ำและข้อมูลศูนย์พักพิงจากฐานข้อมูลที่ได้รับใน Prompt เท่านั้น "
        "หากไม่มีข้อมูลสถานีหรือสถานที่ที่ถามในระบบ ห้ามคาดเดาหรือจินตนาการเส้นทางเองอย่างเด็ดขาด ให้แจ้งตามตรงและแนบลิงก์แผนที่กลางให้ไปตรวจสอบ\n"
        "4. รูปแบบหน้าจอมือถือ: ใช้สัญลักษณ์ไอคอนนำหน้าหัวข้อในการตอบ หลีกเลี่ยงข้อความที่ยาวเป็นเรียงความและห้ามใช้เครื่องหมายดอกจัน (*) ในการจัดรูปแบบเด็ดขาด"
    )
)

# =============================================================================
# 2. ฟังก์ชันระบบช่วยดักคำกรองลบเครื่องหมายดอกจัน (*)
# =============================================================================
def clean_text_for_line(text):
    if not text:
        return ""
    return text.replace("**", "").replace("*", "")

def extract_number(text):
    if not text:
        return "1"
    cleaned = "".join(filter(lambda x: x.isdigit(), text))
    return cleaned if cleaned else "1"

# =============================================================================
# 3. ฟังก์ชันคัดกรองดึงรหัส Google Sheet ID ออกจาก URL
# =============================================================================
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

# =============================================================================
# 4. ฟังก์ชันคำนวณระยะทาง Haversine
# =============================================================================
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # รัศมีโลก (กิโลเมตร)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# =============================================================================
# 5. ระบบแคชข้อมูลระดับน้ำ 12 นาที (ThaiWater Lazy Sync)
# =============================================================================
def get_water_data_lazy():
    client = get_sheets_client()
    if not client:
        return []
    
    sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
    ws = sheet.worksheet("Water_Levels")
    
    # ดึงวันเวลา Sync ล่าสุดจากช่อง L1
    last_sync_str = ws.acell('L1').value
    should_sync = False
    
    if not last_sync_str:
        should_sync = True
    else:
        try:
            last_sync_time = datetime.datetime.strptime(last_sync_str, "%Y-%m-%d %H:%M:%S")
            diff_seconds = (datetime.datetime.now() - last_sync_time).total_seconds()
            if diff_seconds > 720:  # 12 นาที
                should_sync = True
        except Exception as e:
            print(f"Error parsing L1 timestamp: {e}")
            should_sync = True

    if should_sync:
        print("🔄 Lazy Sync: Fetching new data from ThaiWater API...")
        url = "https://api.thaiwater.net/v1/public/waterlevel/latest"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.thaiwater.net/"
        }
        try:
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code == 200:
                api_data = response.json().get('data', [])
                # โครงสร้างตาราง: ID, Name, River, Location, Lat, Lon, WaterLevel, BankLevel, Situation, Trend, Time
                rows_to_update = [["ID", "Name", "River", "Location", "Lat", "Lon", "WaterLevel", "BankLevel", "Situation", "Trend", "Time"]]
                
                for st in api_data:
                    station_info = st.get('station', {})
                    val = st.get('value', 0)
                    threshold = st.get('threshold_level', 0)
                    
                    # ตรวจสอบประเมินระดับความรุนแรงภัยพิบัติ
                    situation = "ปกติ"
                    if threshold > 0:
                        if val >= threshold:
                            situation = "🔴 วิกฤต (ล้นตลิ่ง)"
                        elif val >= (threshold * 0.9):
                            situation = "🟡 เฝ้าระวัง"
                    
                    location_detail = f"{station_info.get('province', {}).get('name', {}).get('th', '')}"
                    
                    rows_to_update.append([
                        station_info.get('id', '-'),
                        station_info.get('name', {}).get('th', '-'),
                        station_info.get('river', {}).get('name', {}).get('th', '-'),
                        location_detail,
                        station_info.get('lat', 0),
                        station_info.get('lng', 0),
                        val,
                        threshold,
                        situation,
                        st.get('vicinity', '-'),
                        st.get('datetime', '-')
                    ])
                
                ws.clear()
                ws.update('A1', rows_to_update)
                ws.update('L1', [[datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]])
                print("✅ Cache update succeeded for 738 stations.")
        except Exception as e:
            print(f"Failed to sync with ThaiWater: {e}")

    return ws.get_all_records()

# =============================================================================
# 6. ระบบวิเคราะห์ศูนย์พักพิงที่ใกล้ที่สุดที่ยังไม่เต็ม (Unlimited Radius)
# =============================================================================
def find_nearest_shelters_unlimited(user_lat, user_lon):
    client = get_sheets_client()
    if not client:
        return []
    
    sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
    ws = sheet.worksheet("Shelters")
    rows = ws.get_all_records()
    
    available_shelters = []
    for r in rows:
        try:
            status = str(r.get('Status', '')).strip()
            capacity = int(r.get('Capacity', 100))
            occupancy = int(r.get('Occupancy', 0))
            
            # เงื่อนไขสำคัญ: ต้องไม่เต็ม
            if status == "เต็ม" or occupancy >= capacity:
                continue
            
            lat = float(r.get('Latitude', r.get('Lat', 0)))
            lon = float(r.get('Longitude', r.get('Lon', 0)))
            
            dist = calculate_distance(user_lat, user_lon, lat, lon)
            r['dist'] = dist
            r['remaining'] = capacity - occupancy
            available_shelters.append(r)
        except Exception as e:
            print(f"Skip shelter parsing row: {e}")
            continue
            
    # เรียงระยะทางจากใกล้ไปไกล และเลือกมา 3 ศูนย์พักพิง
    available_shelters.sort(key=lambda x: x['dist'])
    return available_shelters[:3]

# =============================================================================
# 7. ฟังก์ชันเชื่อมต่อ Google Sheets และจัดโครงสร้างอัตโนมัติ
# =============================================================================
SHEETS_INITIALIZED = False
LAST_SHEETS_ERROR = "ยังไม่ได้เชื่อมต่อระบบ"

def setup_sheets_automatically(sheet):
    existing_sheets = [w.title for w in sheet.worksheets()]
    
    if "users" not in existing_sheets:
        ws = sheet.add_worksheet(title="users", rows="1000", cols="10")
        ws.append_row(["user_id", "first_name", "last_name", "phone", "register_date", "status"])
        
    if "Water_Levels" not in existing_sheets:
        ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="15")
        ws.append_row(["ID", "Name", "River", "Location", "Lat", "Lon", "WaterLevel", "BankLevel", "Situation", "Trend", "Time"])
        
    if "Shelters" not in existing_sheets:
        ws = sheet.add_worksheet(title="Shelters", rows="1000", cols="15")
        ws.append_row(["ShelterID", "Name", "Province", "District", "Latitude", "Longitude", "Capacity", "Occupancy", "Status", "Facilities", "Contact"])
        ws.append_row(["SH001", "โรงเรียนชุมชนบ้านกระจูด", "พัทลุง", "ระโนด", "7.7725", "100.3235", "500", "50", "ว่าง", "ไฟฟ้า, อาหาร, รองรับรถเข็น", "081-234-5678"])
        ws.append_row(["SH002", "วัดโคกสมานคุณ", "สงขลา", "หาดใหญ่", "7.0145", "100.4682", "300", "295", "ใกล้เต็ม", "หน่วยแพทย์, อาหาร", "082-345-6789"])
        
    if "sos_requests" not in existing_sheets:
        ws = sheet.add_worksheet(title="sos_requests", rows="3000", cols="15")
        ws.append_row(["request_id", "user_id", "timestamp", "latitude", "longitude", "group", "severity", "image_url", "status"])
        
    if "user_needs" not in existing_sheets:
        ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="10")
        ws.append_row(["Timestamp", "UserID", "Latitude", "Longitude", "Category", "Details", "Status"])

def get_sheets_client():
    global SHEETS_INITIALIZED, LAST_SHEETS_ERROR
    clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not clean_sheet_id:
        LAST_SHEETS_ERROR = "ข้อมูล Environment Variables ไม่สมบูรณ์"
        return None
    try:
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON.strip())
        client = gspread.service_account_from_dict(creds_dict)
        if not SHEETS_INITIALIZED:
            sheet = client.open_by_key(clean_sheet_id)
            setup_sheets_automatically(sheet)
            SHEETS_INITIALIZED = True
            LAST_SHEETS_ERROR = "เชื่อมต่อสำเร็จและจัดระบบตารางเสร็จสิ้น"
        return client
    except Exception as e:
        LAST_SHEETS_ERROR = f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}"
        print(f"Google Sheets Client Error: {e}")
        return None
