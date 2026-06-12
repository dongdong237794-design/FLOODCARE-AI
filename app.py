import os
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

app = Flask(__name__)

# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")

USER_STATES = {}

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

# 2. ฟังก์ชันคำนวณระยะทางและประเมินที่ว่าง
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

# 3. Endpoint สำหรับสร้าง Rich Menu (โปรแกรมมิ่งแบบ 6 ปุ่ม)
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
            # แถวที่ 1 (y: 0 ถึง 843)
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "เบอร์โทรศัพท์ฉุกเฉิน"}},
            {"bounds": {"x": 833, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "ปฐมพยาบาลเบื้องต้น"}},
            {"bounds": {"x": 1666, "y": 0, "width": 834, "height": 843}, "action": {"type": "message", "text": "ศูนย์พักพิง"}},
            # แถวที่ 2 (y: 843 ถึง 1686)
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

# 4. หน้าเว็บอัปโหลดรูปภาพเมนูแบบ 6 ปุ่ม
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

# 5. Webhook Route
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 6. รับข้อความตัวอักษร
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    
    if RICH_MENU_ID:
        try:
            line_bot_api.link_rich_menu_to_user(user_id, RICH_MENU_ID)
        except Exception as link_err:
            print(f"Failed to link rich menu: {link_err}")
            
    # เมนูพรีเซ็ต (แบบ 6 ปุ่ม)
    if user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        reply_text = (
            "📞 เบอร์โทรศัพท์ฉุกเฉินที่ควรบันทึกไว้:\n\n"
            "🚨 สายด่วน ปภ. 1784 (เตือนภัยและช่วยเหลืออุทกภัย)\n"
            "🚨 สายด่วนกู้ชีพ 1669 (เจ็บป่วยฉุกเฉินทางแพทย์)\n"
            "🚨 สายด่วนตำรวจทางหลวง 1193 (ขอความช่วยเหลือขณะเดินทาง)\n"
            "🚨 สายด่วนรัฐบาล 1111 (ร้องเรียนและขอความช่วยเหลือทั่วไป)"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ปฐมพยาบาลเบื้องต้น":
        reply_text = (
            "🩹 วิธีการปฐมพยาบาลเบื้องต้นในสถานการณ์น้ำท่วม:\n\n"
            "🩹 บาดแผลจากสิ่งขีดข่วน: ล้างแผลด้วยน้ำสะอาดและสบู่ ทายาฆ่าเชื้อ แล้วปิดแผลให้มิดชิด พยายามหลีกเลี่ยงการสัมผัสน้ำท่วมขัง\n"
            "🩹 สัตว์มีพิษกัด: ล้างแผลด้วยน้ำสะอาด พยายามให้ผู้ถูกกัดขยับอวัยวะนั้นให้น้อยที่สุดเพื่อไม่ให้พิษแล่นเร็ว และรีบนำส่งแพทย์\n"
            "🩹 กระแสไฟดูด: รีบสับสวิตช์ไฟหลักทันที ห้ามสัมผัสตัวผู้โดนไฟดูดด้วยมือเปล่า ให้ใช้ไม้แห้งหรือวัสดุที่ไม่นำไฟฟ้าในการผลักหรือดึงตัวออกมา"
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
        reply_text = (
            "🌊 วิธีการตรวจสอบระดับน้ำและเตือนภัย:\n\n"
            "📌 ท่านสามารถดาวน์โหลดแอปพลิเคชัน 'ThaiWater' เพื่อเช็กปริมาณฝนและระดับน้ำในลุ่มน้ำหลักทั่วไทย\n"
            "📌 ติดตามการรายงานสถานการณ์รายชั่วโมงผ่านทางสายด่วน ปภ. โทร 1784"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "SOS ขอความช่วยเหลือ":
        USER_STATES[user_id] = "waiting_sos_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="ส่งพิกัดแจ้ง SOS"))
            ]
        )
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(
                text="🚨 โปรดแตะส่งพิกัด 'Location' ด้านล่างเพื่อส่งสัญญาณขอความช่วยเหลือฉุกเฉินและรายงานตำแหน่งที่แท้จริงของคุณครับ",
                quick_reply=location_quick_reply
            )
        )
        
    elif user_text == "ถาม AI เรื่องน้ำท่วม":
        reply_text = "🤖 คุณสามารถพิมพ์รายละเอียดคำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมในครั้งนี้เข้ามาได้ทันทีเลยครับ ผมพร้อมวิเคราะห์และตอบทุกข้อสงสัยให้ครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    else:
        # ประมวลผลโมเดล Gemini 2.5 Flash
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
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))

# 7. รับข้อมูลพิกัด (Location Message)
@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    
    state = USER_STATES.pop(user_id, "default")
    
    if RICH_MENU_ID:
        try:
            line_bot_api.link_rich_menu_to_user(user_id, RICH_MENU_ID)
        except Exception as link_err:
            print(f"Failed to link rich menu: {link_err}")
            
    # --- ค้นหาศูนย์อพยพจากจำลองพิกัดจริง ---
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
        
    # --- จำลองการแจ้งเหตุ SOS ---
    else:
        confirm_text = (
            "🚨 [ระบบจำลองการทำงานเพื่อทดสอบความแม่นยำ]\n\n"
            "ระบบตรวจจับสัญญาณและยืนยันการรับพิกัด SOS ของคุณแล้วครับ!\n"
            f"📍 ละติจูดของคุณคือ: {latitude}\n"
            f"📍 ลองจิจูดของคุณคือ: {longitude}\n\n"
            "ในเวอร์ชันเชื่อมฐานข้อมูล ข้อมูลพิกัดนี้จะถูกเขียนลงสเปรดชีตกู้ภัยแบบเรียลไทม์ และแจ้งเตือนทีมเจ้าหน้าที่ภาคสนามเพื่อเข้าทำการช่วยเหลือด่วนทันทีครับ"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
