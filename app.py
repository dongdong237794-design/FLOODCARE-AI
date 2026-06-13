import os
import json
import math
import datetime
from flask import Flask, request, abort

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
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

# ระบบติดตามสถานะการสนทนาและเก็บข้อมูลคัดกรอง (State Machine & Context Storage)
USER_STATES = {}
USER_DATA = {}

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

# 2. ฟังก์ชันตัวกรองลบเครื่องหมายดอกจัน (*) ออกทั้งหมดก่อนส่งกลับเข้าไลน์
def clean_text_for_line(text):
    if not text:
        return ""
    cleaned = text.replace("**", "").replace("*", "")
    return cleaned

# 3. ฟังก์ชันเชื่อมต่อ Google Sheets อย่างปลอดภัยพร้อมระบบป้องกันเซิร์ฟเวอร์แครช
def get_sheets_client():
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
        print("Warning: Google Sheets variables are not configured yet.")
        return None
    try:
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error initializing Google Sheets client: {e}")
        return None

# 4. ฟังก์ชันคำนวณระยะทางภูมิศาสตร์
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# 5. ฟังก์ชันวิเคราะห์ระดับความเร่งด่วนตามหลักกู้ภัยสากล (Triage Priority Calculator)
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
            return "🔴 เร่งด่วนมาก"
        elif "เอว" in water_level or "เข่า" in water_level or "สูง" in water_level:
            return "🟠 ปานกลาง"
        else:
            return "🟢 ติดตามสถานการณ์"
    except Exception as e:
        print(f"Priority Calc Error: {e}")
        return "🟠 ปานกลาง"

# 6. หน้าหลักเช็กสถานะการรันเซิร์ฟเวอร์อย่างง่าย (Home Route)
@app.route("/", methods=['GET'])
def index():
    return "<h2 style='font-family: sans-serif; text-align: center; margin-top: 100px; color: #1E3A8A;'>🤖 FLOODCARE AI Service is Running Active!</h2>"

# 7. Webhook Route
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 8. รับข้อความตัวอักษรและประมวลผลกระบวนการคัดกรองแบบโต้ตอบ (Intake State Machine)
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # ดึงระดับสถานะการคุยปัจจุบัน
    state = USER_STATES.get(user_id)

    # ==================== ส่วนที่ 8.1: ระบบคัดกรองข้อมูลผู้ประสบภัยอัตโนมัติ (Triage Intake State Machine) ====================
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

        # ==================== ส่วนที่ 8.2: ระบบคัดกรองคำถามอื่น ๆ ย้อนกลับตามเมนู ====================
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

    # ==================== ส่วนที่ 8.3: ตรวจสอบการคลิกปุ่มหลักบนเมนู 6 ปุ่ม ====================
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
                text="📍 โปรดกดแชร์พิกัดที่ตั้งปัจจุบันของคุณ เพื่อให้ระบบช่วยค้นหาศูนย์พักพิงจริงรอบตัวคุณในระยะ 5-20 กม. ครับ",
                quick_reply=location_quick_reply
            )
        )
        
    elif user_text == "ตรวจสอบระดับน้ำ":
        USER_STATES[user_id] = "waiting_water_location"
        reply_text = "🌊 คุณต้องการประเมินระดับน้ำในพื้นที่เขต/อำเภอ และจังหวัดใดครับ? โปรดพิมพ์ระบุชื่อพื้นที่ของคุณมาได้เลยนะครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "SOS ขอความช่วยเหลือ":
        USER_STATES[user_id] = "sos_q1"
        USER_DATA[user_id] = {} # ล้างข้อมูลเก่า
        reply_text = "🚨 เพื่อจัดเตรียมอุปกรณ์ช่วยเหลือได้ถูกต้อง โปรดตอบข้อมูลคัดกรองสั้นๆ นะครับ\n\n📌 1. บ้านของคุณอยู่พื้นที่บริเวณไหนครับ? (ระบุชื่อหมู่บ้าน ซอย หรือจุดสังเกต)"
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
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, user_text, ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")
                
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))

# 10. รับข้อมูลพิกัด (Location Message) และประมวลผล GIS / ดึงและเก็บข้อมูลลงแผ่นงาน Google Sheets
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
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
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
            # หากเชื่อมต่อสเปรดชีตสิทธิไม่สมบูรณ์ จะแจ้งรายงานความล้มเหลวทันทีเพื่อความปลอดภัยในการประสานข้อมูลจริง
            reply_text = "⚠️ ขออภัยครับ ขณะนี้ระบบขัดข้องไม่สามารถตรวจสอบสิทธิ์การอ่านข้อมูลศูนย์พักพิงจริงได้ โปรดโทรติดต่อเบอร์สายด่วนภัยพิบัติ ปภ. 1784 ทันทีครับ"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
                
        nearest_shelters = []
        for sh in shelter_list:
            distance = calculate_distance(latitude, longitude, sh['lat'], sh['lon'])
            # คัดกรองรัศมี 5 - 20 กิโลเมตร
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
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                sos_worksheet = sheet.worksheet("SOS_Intake")
                # บันทึกข้อมูลคัดกรองลงสเปรดชีตตรงตามสเปกเป๊ะๆ
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
                "ข้อมูลนี้กู้ภัยสามารถเปิดตรวจสอบเพื่อเข้าช่วยเหลือได้ทันทีแบบเรียลไทม์ โปรดรอคอยในจุดที่ปลอดภัยที่สุดนะครับ"
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
