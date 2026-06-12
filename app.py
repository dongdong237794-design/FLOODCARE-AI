import os
import json
import math
import datetime
import requests
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
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

# ระบบติดตามสถานะการสนทนาชั่วคราว (Conversation State & Data Tracker)
USER_STATES = {}
USER_DATA = {}

# รายชื่อศูนย์อพยพจำลอง (Mock Data) สำหรับการคำนวณทางภูมิศาสตร์จริง
STATIC_SHELTERS = [
    {
        "name": "ศูนย์พักพิงวัดเสาชิงช้า (เขตพระนคร)",
        "lat": 13.7523,
        "lon": 100.5015,
        "capacity": 200,
        "occupancy": 85,
        "contact": "02-123-4567"
    },
    {
        "name": "ศูนย์พักพิงโรงเรียนวัดสุทัศน์ (เขตพระนคร)",
        "lat": 13.7511,
        "lon": 100.5002,
        "capacity": 150,
        "occupancy": 145,
        "contact": "02-987-6543"
    },
    {
        "name": "ศูนย์พักพิงโรงเรียนสามเสนวิทยาลัย (เขตพญาไท)",
        "lat": 13.7820,
        "lon": 100.5340,
        "capacity": 300,
        "occupancy": 50,
        "contact": "02-555-5555"
    }
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
        "1. น้ำเสียง: สุภาพ อบอุ่น เป็นกันเอง และคอยให้กำลังใจผู้ประสบภัยเสมอ\n"
        "2. ความกระชับ: ตอบให้กระชับ ได้ใจความสั้นๆ ไม่ยาวเป็นเรียงความ และแบ่งย่อหน้าให้เหมาะสมกับการอ่านบนหน้าจอมือถือ\n"
        "3. รูปแบบสัญลักษณ์: ห้ามใช้เครื่องหมายดอกจัน (*) ในการทำสัญลักษณ์หัวข้อย่อยหรือเน้นคำเด็ดขาด "
        "แต่ให้ใช้ 'อิโมจิ' ที่เกี่ยวข้องทำหน้าที่เป็นสัญลักษณ์นำหน้าหัวข้อย่อยแทนเสมอ (เช่น 📌, 🏃, 🩹, 📞, 💬, ⚠️, 🟢, 🔴) เพื่อความเป็นระเบียบและสวยงาม\n"
        "4. ความปลอดภัยสูงสุด: ห้ามเดาข้อมูลหรือจินตนาการสิ่งที่ไม่เป็นความจริงเด็ดขาด หากข้อมูลใดไม่แน่ชัด หรือเป็นกรณีฉุกเฉินเฉพาะหน้า "
        "ให้แสดงความห่วงใยและแนะนำเบอร์โทรสายด่วนภัยพิบัติที่ถูกต้องทันที เช่น สายด่วน ปภ. 1784 หรือสายด่วนกู้ชีพ 1669"
    )
)

# 2. ฟังก์ชันเชื่อมต่อ Google Sheets อย่างปลอดภัย (พร้อมระบบป้องกันเซิร์ฟเวอร์แครช)
def get_sheets_client():
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
        print("Warning: Google Sheets credentials are not fully configured yet.")
        return None
    try:
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error initializing Google Sheets client: {e}")
        return None

# 3. ฟังก์ชันคำนวณระยะทางและประเมินที่ว่าง
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def check_shelter_vacancy(capacity, occupancy):
    remaining = capacity - occupancy
    if remaining <= 0:
        return "🔴 เต็มแล้ว (No Vacancy) - โปรดเลี่ยงไปจุดอื่น"
    elif occupancy >= (capacity * 0.8):
        return f"🟡 ใกล้เต็ม (ว่างอีก {remaining} ที่นั่ง)"
    else:
        return f"🟢 ยังมีที่ว่าง (ว่างอีก {remaining} ที่นั่ง)"

# 4. Endpoint สำหรับสร้าง Rich Menu (โปรแกรมมิ่งแบบ 6 ปุ่ม)
@app.route("/create_rich_menu", methods=['GET'])
def create_rich_menu():
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "size": {"width": 2500, "height": 1686},
        "selected": True,
        "name": "FLOODCARE AI 6-Button Menu",
        "chatBarText": "คุยกับ AI / กู้ภัย",
        "areas": [
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "เบอร์โทรศัพท์ฉุกเฉิน"}},
            {"bounds": {"x": 833, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "ปฐมพยาบาลเบื้องต้น"}},
            {"bounds": {"x": 1666, "y": 0, "width": 834, "height": 843}, "action": {"type": "message", "text": "ศูนย์พักพิง"}},
            {"bounds": {"x": 0, "y": 843, "width": 833, "height": 843}, "action": {"type": "message", "text": "ตรวจสอบระดับน้ำ"}},
            {"bounds": {"x": 833, "y": 843, "width": 833, "height": 843}, "action": {"type": "message", "text": "SOS ขอความช่วยเหลือ"}},
            {"bounds": {"x": 1666, "y": 843, "width": 834, "height": 843}, "action": {"type": "message", "text": "ถาม AI เรื่องน้ำท่วม"}}
        ]
    }
    
    response = requests.post("https://api.line.me/v2/bot/richmenu", headers=headers, json=payload)
    if response.status_code == 200:
        res_data = response.json()
        rich_menu_id = res_data.get("richMenuId")
        return f"""
        <div style="font-family: sans-serif; padding: 20px;">
            <h2 style="color: #28a745;">🎉 สร้าง Rich Menu สำเร็จ!</h2>
            <p>รหัส Rich Menu ID ของคุณคือ: <code style="background: #f1f1f1; padding: 5px 10px; border-radius: 4px; font-weight: bold;">{rich_menu_id}</code></p>
            <p><b>ขั้นตอนถัดไป:</b></p>
            <ol>
                <li>คัดลอกรหัสนี้ไปเพิ่มใน Environment Variables บน Render ในชื่อ <b>RICH_MENU_ID</b></li>
                <li>ไปที่ลิงก์นี้เพื่ออัปโหลดรูปภาพเมนูของคุณ: <a href="/upload_image/{rich_menu_id}" style="color: #007bff; font-weight: bold; text-decoration: none;">กดเพื่อไปหน้าอัปโหลดรูปภาพ</a></li>
            </ol>
        </div>
        """
    else:
        return f"<h3>เกิดข้อผิดพลาดในการสร้าง</h3><p>Error: {response.text}</p>"

# 5. หน้าเว็บอัปโหลดรูปภาพเมนูแบบ 6 ปุ่ม
@app.route("/upload_image/<rich_menu_id>", methods=['GET', 'POST'])
def upload_image(rich_menu_id):
    if request.method == 'POST':
        if 'file' not in request.files:
            return "ไม่มีไฟล์อัปโหลดเข้ามา"
        file = request.files['file']
        if file.filename == '':
            return "ไม่ได้เลือกไฟล์"
        
        image_data = file.read()
        
        headers = {
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": file.content_type
        }
        upload_url = f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content"
        response = requests.post(upload_url, headers=headers, data=image_data)
        
        if response.status_code == 200:
            return f"""
            <div style="font-family: sans-serif; padding: 40px; text-align: center; max-width: 500px; margin: auto; border: 1px solid #28a745; border-radius: 8px; margin-top: 50px;">
                <h2 style="color: #28a745;">🎉 อัปโหลดและผูกภาพเมนูสำเร็จแล้ว!</h2>
                <p style="color: #666;">ริชเมนู 6 ปุ่มบน LINE OA ของคุณ พร้อมให้บริการด้วยภาพประกอบที่สวยงามแล้วครับ</p>
            </div>
            """
        else:
            return f"<h3>อัปโหลดไม่สำเร็จ</h3><p>Error: {response.text}</p>"
            
    return f"""
    <div style="font-family: sans-serif; padding: 40px; max-width: 500px; margin: auto; border: 1px solid #ccc; border-radius: 8px; margin-top: 50px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h2 style="color: #007bff; text-align: center;">🎨 อัปโหลดภาพริชเมนู FLOODCARE (6 ปุ่ม)</h2>
        <p style="color: #666; font-size: 14px; line-height: 1.5; background: #f8f9fa; padding: 15px; border-left: 4px solid #007bff;">
            กรุณาเลือกไฟล์ภาพขนาดความกว้าง <b>2500x1686 พิกเซล</b> (ประเภทไฟล์ JPG หรือ PNG และขนาดห้ามเกิน 1 MB) 
            ที่แบ่งการออกแบบเป็น 6 ช่องปุ่มกด (2 แถว แถวละ 3 ช่อง) ให้ตรงกับหน้าเมนูของเราครับ
        </p>
        <form method="post" enctype="multipart/form-data" style="margin-top: 25px;">
            <input type="file" name="file" accept="image/jpeg, image/png" style="display: block; margin-bottom: 25px; width: 100%; padding: 12px; border: 1px dashed #ccc; border-radius: 4px;" required>
            <button type="submit" style="background: #28a745; color: white; border: none; padding: 14px; width: 100%; font-size: 16px; border-radius: 4px; cursor: pointer; font-weight: bold;">ส่งและผูกรูปเข้ากับเมนู LINE</button>
        </form>
    </div>
    """

# 6. Webhook Route
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 7. รับข้อความตัวอักษรและจัดการระบบคิวพูดคุยโต้ตอบ (Interactive Workflow)
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # ดึงระดับสถานะการคุยของผู้ใช้ในปัจจุบัน
    current_state = USER_STATES.get(user_id)
    
    # ช่วยเชื่อมริชเมนูอัตโนมัติหากมีการอัปเดตแปร
    if RICH_MENU_ID:
        try:
            line_bot_api.link_rich_menu_to_user(user_id, RICH_MENU_ID)
        except:
            pass

    # ==================== ส่วนที่ 7.1: ดักจับคำตอบโต้ตอบเชิงลึก (Interactive Responses) ====================
    
    if current_state == "waiting_emergency_type":
        # ล้างสถานะเพื่อจบกระบวนการรอบนี้
        USER_STATES.pop(user_id, None)
        prompt = (
            f"ผู้ใช้แจ้งต้องการเบอร์กู้ภัยฉุกเฉินด้วยเรื่อง: '{user_text}' "
            "โปรดแนะนำเบอร์โทรศัพท์และหน่วยงานที่ถูกต้องตรงประเด็นทันทีด้วยน้ำเสียงที่กระชับ สุภาพ "
            "พร้อมแนะนำสิ่งสำคัญที่เขาควรแจ้งเจ้าหน้าที่ปลายสาย"
        )
        try:
            response = gemini_model.generate_content(prompt)
            ai_response = response.text.strip()
        except Exception as e:
            ai_response = "🚨 สายด่วน ปภ. 1784 หรือสายด่วนกู้ชีพ 1669 พร้อมให้บริการประสานงานทันทีครับ"
            
        # บันทึกลง Google Sheets "AI Logs"
        sheets_client = get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, f"[เบอร์ฉุกเฉิน] {user_text}", ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))
        return

    elif current_state == "waiting_first_aid_detail":
        USER_STATES.pop(user_id, None)
        prompt = (
            f"ผู้ประสบภัยแจ้งอาการบาดเจ็บ/อุบัติเหตุเฉพาะหน้า: '{user_text}' "
            "ในฐานะ FLOODCARE AI โปรดให้คำแนะนำขั้นตอนการปฐมพยาบาลเบื้องต้นที่สั้น กระชับ เป็นขั้นเป็นตอน (1, 2, 3) "
            "และนำไปปฏิบัติตามได้ทันทีอย่างปลอดภัยสูงสุด หลีกเลี่ยงข้อความที่ยาวและเยิ่นเย้อ"
        )
        try:
            response = gemini_model.generate_content(prompt)
            ai_response = response.text.strip()
        except Exception as e:
            ai_response = "🩹 โปรดล้างแผลด้วยน้ำสะอาดและปิดปากแผลเบื้องต้น หากเจ็บป่วยรุนแรง โทรสายด่วน 1669 ทันทีครับ"
            
        sheets_client = get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, f"[ปฐมพยาบาล] {user_text}", ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))
        return

    elif current_state == "waiting_water_location":
        USER_STATES.pop(user_id, None)
        prompt = (
            f"ผู้ใช้ต้องการเช็กระดับน้ำในพื้นที่: '{user_text}' "
            "โปรดสรุปวิธีเช็กสถานการณ์ภัยพิบัติในพื้นที่นั้น หรือประเมินและเตือนข้อระวังภัยน้ำท่วมอย่างสั้น กระชับ "
            "พร้อมแนะนำแอปพลิเคชัน ThaiWater เพื่ออ้างอิงข้อมูลครับ"
        )
        try:
            response = gemini_model.generate_content(prompt)
            ai_response = response.text.strip()
        except Exception as e:
            ai_response = "🌊 แนะนำตรวจสอบปริมาณน้ำแบบเรียลไทม์ได้ทางแอปพลิเคชัน ThaiWater ของคลังข้อมูลน้ำแห่งชาติครับ"
            
        sheets_client = get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, f"[ระดับน้ำ] {user_text}", ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))
        return

    elif current_state == "waiting_sos_details":
        # บันทึกรายละเอียดจำนวนคนเข้าสู่หน่วยความจำชั่วคราว
        USER_DATA[user_id] = {"sos_detail": user_text}
        # อัปเกรดสถานะไปสู่การรับพิกัด GPS
        USER_STATES[user_id] = "waiting_sos_location"
        
        # ส่งปุ่มด่วนขอแชร์ตำแหน่ง GPS
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="กดแชร์พิกัดกู้ภัย"))
            ]
        )
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(
                text="📢 ข้อมูลคนติดในบ้านได้รับการบันทึกแล้วครับ! เพื่อระบุจุดพิกัดกู้ภัย โปรดกดปุ่มแชร์พิกัด 'Location' สีเขียวด้านล่างนี้ได้เลยครับ",
                quick_reply=location_quick_reply
            )
        )
        return

    # ==================== ส่วนที่ 7.2: คำสั่งตรวจจับการกดปุ่มเมนูหลัก (Rich Menu Clicks) ====================
    
    if user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        USER_STATES[user_id] = "waiting_emergency_type"
        reply_text = (
            "📞 คุณต้องการติดต่อกู้ภัยหรือติดต่อเรื่องใดเป็นพิเศษไหมครับ? "
            "(พิมพ์บอกปัญหาของคุณได้เลยครับ เช่น ต้องการเรืออพยพ, สัตว์มีพิษเข้าบ้าน, หรือขอรับแจกถุงยังชีพ "
            "เพื่อให้ผมช่วยแนะนำหน่วยงานที่ถูกต้องเจาะจงให้ทันทีครับ)"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ปฐมพยาบาลเบื้องต้น":
        USER_STATES[user_id] = "waiting_first_aid_detail"
        reply_text = (
            "🩹 คุณหรือคนที่อยู่ด้วยได้รับบาดเจ็บหรือเกิดอุบัติเหตุจากอะไรครับ? "
            "(เช่น โดนไฟดูด, โดนสัตว์มีพิษกัด, เลือดไหลไม่หยุด หรือเป็นลมหมดสติ "
            "พิมพ์บอกรายละเอียดอาการเพื่อให้ผมช่วยหาวิธีการปฐมพยาบาลเฉพาะหน้าได้อย่างรวดเร็วครับ)"
        )
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
                text="📍 โปรดกดแชร์พิกัดที่ตั้งปัจจุบันของคุณ เพื่อให้ระบบช่วยคำนวณและค้นหาศูนย์พักพิงที่เปิดทำการและอยู่ใกล้ตัวคุณที่สุดครับ",
                quick_reply=location_quick_reply
            )
        )
        
    elif user_text == "ตรวจสอบระดับน้ำ":
        USER_STATES[user_id] = "waiting_water_location"
        reply_text = (
            "🌊 คุณต้องการประเมินระดับน้ำหรือปริมาณฝนในพื้นที่เขต/อำเภอ และจังหวัดใดครับ? "
            "(กรุณาพิมพ์ชื่ออำเภอและจังหวัดที่คุณอยู่ในปัจจุบัน เพื่อให้ผมช่วยเตือนและประเมินสถานการณ์เฉพาะจุดให้ครับ)"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "SOS ขอความช่วยเหลือ":
        USER_STATES[user_id] = "waiting_sos_details"
        reply_text = (
            "🚨 เพื่อข้อมูลที่ทีมกู้ภัยสามารถนำไปจัดเตรียมอุปกรณ์ช่วยชีวิตได้เหมาะสมที่สุด "
            "โปรดพิมพ์แจ้งรายละเอียดจำนวนคนที่ติดอยู่ร่วมกันในบ้านของคุณสักนิดนึงครับ (เช่น ติดอยู่บนหลังคา 4 คน มีเด็กเล็ก 1 คน)"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ถาม AI เรื่องน้ำท่วม":
        reply_text = "🤖 คุณสามารถพิมพ์รายละเอียดคำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมในครั้งนี้เข้ามาได้ทันทีเลยครับ ผมพร้อมวิเคราะห์และตอบทุกข้อสงสัยให้ครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    else:
        # การสนทนาถามตอบแบบอิสระรอบปกติ
        ai_response = ""
        try:
            response = gemini_model.generate_content(user_text)
            ai_response = response.text.strip()
        except Exception as e:
            print(f"Gemini API Error: {e}")
            ai_response = (
                "⚠️ ระบบประสาทเครือข่าย AI ขัดข้องชั่วคราว หากท่านตกอยู่ในเหตุการณ์เร่งด่วนและเป็นอันตรายต่อชีวิต "
                "โปรดติดต่อสายด่วนกรมป้องกันและบรรเทาสาธารณภัย โทร. 1784 หรือโทร 1669 ทีมแพทย์กู้ชีพทันทีครับ"
            )
            
        # บันทึกลง Google Sheets "AI Logs"
        sheets_client = get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, user_text, ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")
                
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))

# 8. รับข้อมูลพิกัด (Location Message) และประสานข้อมูลเข้า Google Sheets "SOS"
@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    address = event.message.address or "ไม่ระบุที่อยู่"
    title = event.message.title or "จุดพิกัด"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    state = USER_STATES.pop(user_id, "default")
    
    if RICH_MENU_ID:
        try:
            line_bot_api.link_rich_menu_to_user(user_id, RICH_MENU_ID)
        except:
            pass
            
    # --- ค้นหาศูนย์อพยพจากพิกัดจริง ---
    if state == "waiting_shelter_location":
        nearest_shelters = []
        for sh in STATIC_SHELTERS:
            distance = calculate_distance(latitude, longitude, sh['lat'], sh['lon'])
            vacancy_status = check_shelter_vacancy(sh['capacity'], sh['occupancy'])
            nearest_shelters.append({
                "name": sh['name'],
                "distance": distance,
                "vacancy": vacancy_status,
                "contact": sh['contact'],
                "lat": sh['lat'],
                "lon": sh['lon']
            })
            
        nearest_shelters.sort(key=lambda x: x['distance'])
        top_shelters = nearest_shelters[:3]
        
        reply_text = "📍 รายชื่อศูนย์พักพิงที่อยู่ใกล้พิกัดของคุณมากที่สุด 3 ลำดับแรกครับ:\n\n"
        for index, sh in enumerate(top_shelters, 1):
            reply_text += (
                f"{index}️⃣ {sh['name']}\n"
                f"   📌 ระยะทางห่าง: {sh['distance']:.2f} กิโลเมตร\n"
                f"   📌 สถานะความจุ: {sh['vacancy']}\n"
                f"   📌 เบอร์ติดต่อติดต่อ: {sh['contact']}\n"
                f"   🧭 แผนที่นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
            )
        reply_text += "⚠️ โปรดสำรวจเส้นทางน้ำท่วมและเคลื่อนย้ายด้วยความระมัดระวังสูงสุดในทุกย่างก้าวครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    # --- กรณีการแจ้งเหตุ SOS (บันทึกเข้าระบบ Google Sheets สมบูรณ์) ---
    elif state == "waiting_sos_location":
        # ดึงรายละเอียดผู้ประสบภัยจากความจำชั่วคราว
        sos_meta = USER_DATA.pop(user_id, {}).get("sos_detail", "ไม่ระบุข้อมูลจำนวนคน")
        
        # เตรียมเขียนข้อมูลลง Google Sheets แผ่นงาน "SOS"
        sheets_client = get_sheets_client()
        success = False
        
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
                sos_worksheet = sheet.worksheet("SOS")
                # บันทึกข้อมูลแบบมีโครงสร้างลง Sheet (แถวใหม่)
                sos_worksheet.append_row([
                    timestamp, user_id, "ผู้แจ้งผ่าน LINE", "-", "1", "รอตรวจสอบ",
                    latitude, longitude, f"รายละเอียดผู้ประสบภัย: {sos_meta} (พิกัด: {address} - {title})", "Pending"
                ])
                success = True
            except Exception as sheet_err:
                print(f"Failed to log SOS to Google Sheets: {sheet_err}")

        if success:
            confirm_text = (
                "🚨 ระบบบันทึกข้อมูลและพิกัด SOS ของคุณเข้ารหัสกู้ภัยออนไลน์เรียบร้อยแล้ว!\n\n"
                f"📍 พิกัดกู้ภัย: {latitude}, {longitude}\n"
                f"👥 ข้อมูลผู้ประสบภัย: {sos_meta}\n\n"
                "ขณะนี้ข้อมูลถูกส่งไปยังแผงควบคุมของทีมกู้ภัย (Google Sheets) แล้ว เจ้าหน้าที่จะประเมินความเร่งด่วนและจัดสรรกำลังช่วยเหลือโดยเร็วที่สุด โปรดรอคอยในจุดที่ปลอดภัยที่สุดครับ"
            )
        else:
            # กลไก Fallback ในกรณีที่ Google Sheets ยังไม่ได้เชื่อมต่อ
            confirm_text = (
                "🚨 ระบบได้รับการยืนยันการแจ้งเหตุ SOS ของคุณเรียบร้อยแล้วครับ!\n"
                f"📍 พิกัดของคุณคือ: {latitude}, {longitude}\n"
                f"👥 รายละเอียดผู้ประสบภัย: {sos_meta}\n\n"
                "*(หมายเหตุการทดสอบ: ข้อมูลจะยังไม่เข้า Google Sheets เนื่องจากคุณยังไม่ได้ตั้งค่าคีย์ API แต่สัญญาณเตือนได้ทำงานในฝั่งบอตแล้วครับ)"
            )
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))
        
    else:
        # หากส่งพิกัดมาเฉยๆ โดยไม่มีบริบท จะแนะนำให้แตะปุ่มเพื่อเข้าสู่กระบวนการที่ถูกต้อง
        confirm_text = "📍 คุณส่งพิกัด GPS มาหาผม หากต้องการแจ้งขอความช่วยเหลือ โปรดกดแตะเมนู 'SOS ขอความช่วยเหลือ' บนแถบด้านล่างก่อนเพื่อให้ทีมกู้ภัยประเมินสถานการณ์ได้รวดเร็วที่สุดนะครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
