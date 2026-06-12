import os
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

app = Flask(__name__)

# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# เก็บสถานะผู้ใช้ชั่วคราวในหน่วยความจำ
USER_STATES = {}

# รายชื่อศูนย์อพยพจำลอง (Mock Data) สำหรับคำนวณระยะทางจริงจากพิกัดผู้ใช้
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
        "คุณคือผู้เชี่ยวชาญด้านอุทกภัยและกู้ภัย (FLOODCARE AI) ประจำประเทศไทย "
        "คอยให้ข้อมูล คำแนะนำในการเอาชีวิตรอด และการรับมืออุทกภัยอย่างถูกต้อง "
        "เน้นการตอบที่สุภาพ กระชับ เข้าใจง่าย และสมเหตุสมผลตามหลักการกู้ภัยสากล "
        "หากไม่ทราบข้อมูลที่แน่ชัด หรือเป็นข้อมูลเฉพาะหน้า ให้แนะนำเบอร์โทรฉุกเฉินและห้ามเดาหรือสร้างข้อมูลเท็จ"
    )
)

# 2. ฟังก์ชันคำนวณระยะทางและตรวจสอบความจุ
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0  # รัศมีของโลก (กิโลเมตร)
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

# 3. Webhook Route
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 4. รับข้อความตัวอักษร (Text Message)
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    
    if user_text == "เตรียมตัวก่อนน้ำท่วม":
        reply_text = (
            "📌 การเตรียมตัวก่อนน้ำท่วม:\n"
            "1. ติดตามข่าวสารสภาพอากาศอย่างใกล้ชิด\n"
            "2. ยกของขึ้นที่สูง ย้ายปลั๊กไฟขึ้นที่ปลอดภัย\n"
            "3. เตรียมกระสอบทรายกั้นจุดเสี่ยง\n"
            "4. จัดเตรียม 'ชุดยังชีพฉุกเฉิน'\n"
            "5. หากได้รับการแจ้งเตือนให้อพยพ ควรปฏิบัติตามทันที"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "วิธีอพยพ":
        reply_text = (
            "🏃 วิธีการอพยพอย่างปลอดภัย:\n"
            "1. ตัดสะพานไฟและแก๊สหุงต้มก่อนออกจากบ้าน\n"
            "2. สวมรองเท้าบูทหรือรองเท้าหุ้มส้นเพื่อป้องกันการโดนบาดและไฟดูด\n"
            "3. หลีกเลี่ยงการเดินหรือขับรถผ่านกระแสน้ำไหลเชี่ยว\n"
            "4. พยายามเดินทางรวมกันเป็นกลุ่ม\n"
            "5. ไปยังศูนย์พักพิงที่ใกล้ที่สุดตามเส้นทางที่ปลอดภัย"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ชุดยังชีพฉุกเฉิน":
        reply_text = (
            "🎒 สิ่งที่ควรมีในถุงยังชีพฉุกเฉิน:\n"
            "- น้ำดื่ม (อย่างน้อย 3 ลิตรต่อคนต่อวัน)\n"
            "- อาหารแห้ง/กระป๋อง และยาสามัญประจำบ้าน\n"
            "- ไฟฉาย ถ่านไฟฉายสำรอง และนกหวีดสำหรับขอความช่วยเหลือ\n"
            "- พาวเวอร์แบงค์และโทรศัพท์มือถือ\n"
            "- เอกสารสำคัญใส่ซองกันน้ำอย่างดี"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        reply_text = (
            "📞 เบอร์โทรศัพท์ฉุกเฉิน (จำให้ขึ้นใจ):\n"
            "• 1784 : ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)\n"
            "• 1669 : สถาบันการแพทย์ฉุกเฉินแห่งชาติ (กู้ชีพ)\n"
            "• 1193 : ตำรวจทางหลวง (ขอความช่วยเหลือขณะเดินทาง)\n"
            "• 1111 : ศูนย์รับเรื่องร้องเรียนจากอุทกภัยรัฐบาล"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ปฐมพยาบาลเบื้องต้น":
        reply_text = (
            "🩹 คำแนะนำการปฐมพยาบาลเบื้องต้น:\n"
            "1. บาดแผลจากสิ่งขีดข่วน: ล้างแผลด้วยน้ำสะอาด ทายาฆ่าเชื้อ และปิดแผลให้มิดชิดเลี่ยงน้ำท่วมขัง\n"
            "2. สัตว์มีพิษกัด: ล้างแผล ดามอวัยวะให้อยู่นิ่งๆ (ห้ามขันชะเนาะแน่นเกินไป) และรีบนำส่งแพทย์\n"
            "3. ไฟฟ้าดูด: รีบสับสวิตช์ไฟใหญ่ หากต้องดึงตัวผู้ถูกไฟดูดให้ใช้สิ่งที่ไม่นำไฟฟ้า เช่น ไม้แห้ง ดึงห้ามจับตัวโดยตรง"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "ศูนย์พักพิง":
        USER_STATES[user_id] = "waiting_shelter_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="ส่งพิกัดหาศูนย์พักพิง"))
            ]
        )
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(
                text="📍 กรุณากดส่งพิกัดที่ตั้งปัจจุบันของคุณ เพื่อให้ระบบคำนวณหาศูนย์พักพิงที่เปิดทำการและอยู่ใกล้คุณมากที่สุดครับ",
                quick_reply=location_quick_reply
            )
        )
        
    elif user_text == "ตรวจสอบระดับน้ำ":
        reply_text = (
            "🌊 บริการตรวจสอบระดับน้ำ:\n"
            "ท่านสามารถติดตามสถานการณ์น้ำและพื้นที่เฝ้าระวังน้ำท่วมฉับพลันแบบ Real-time ได้ผ่านแอปพลิเคชัน 'ThaiWater' หรือโทรสายด่วน ปภ. 1784"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif user_text == "SOS ขอความช่วยเหลือ":
        USER_STATES[user_id] = "waiting_sos_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="ส่งพิกัดเพื่อแจ้ง SOS"))
            ]
        )
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(
                text="📢 กรุณากดปุ่มส่งพิกัด 'Location' เพื่อแจ้งเหตุฉุกเฉิน เพื่อส่งข้อมูลพิกัดที่แน่นอนให้เจ้าหน้าที่เข้าช่วยเหลือครับ",
                quick_reply=location_quick_reply
            )
        )
        
    elif user_text == "ถาม AI เรื่องน้ำท่วม":
        reply_text = "🤖 คุณสามารถพิมพ์คำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมได้ทันทีเลยครับ AI ยินดีตอบทุกคำถามและแนะนำวิธีเอาชีวิตรอดให้ครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    else:
        # ส่งไปประมวลผลด้วย Gemini API
        ai_response = ""
        try:
            response = gemini_model.generate_content(user_text)
            ai_response = response.text.strip()
        except Exception as e:
            print(f"Gemini API Error: {e}")
            ai_response = (
                "⚠️ ขณะนี้บริการ AI ไม่สามารถใช้งานได้ชั่วคราว หากท่านต้องการความช่วยเหลือฉุกเฉิน "
                "โปรดติดต่อสายด่วนกรมป้องกันและบรรเทาสาธารณภัย โทร. 1784 หรือสายด่วนกู้ชีพ โทร. 1669 ทันทีครับ"
            )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))

# 5. รับข้อมูลพิกัด (Location Message)
@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    
    state = USER_STATES.pop(user_id, "default")
    
    # --- ค้นหาศูนย์อพยพจาก Mock Data ---
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
        
        reply_text = "📍 ศูนย์พักพิงที่ใกล้ที่สุด 3 อันดับแรกสำหรับคุณ (ข้อมูลจำลองเพื่อการทดสอบ):\n\n"
        for index, sh in enumerate(top_shelters, 1):
            reply_text += (
                f"{index}. 🏠 {sh['name']}\n"
                f"   - ระยะห่าง: {sh['distance']:.2f} กม.\n"
                f"   - สถานะ: {sh['vacancy']}\n"
                f"   - ติดต่อ: {sh['contact']}\n"
                f"   - 🗺️ นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
            )
        reply_text += "⚠️ โปรดตรวจสอบระดับความสูงของน้ำและประเมินความปลอดภัยก่อนเดินทางอพยพทุกครั้งครับ"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    # --- จำลองการแจ้งเหตุ SOS (ไม่มีการเขียนลงชีต) ---
    else:
        confirm_text = (
            "🚨 [ระบบจำลองการทำงานสำหรับการสาธิต]\n"
            "ระบบได้รับพิกัดแจ้งเหตุฉุกเฉินของคุณเรียบร้อยแล้ว!\n"
            f"🗺️ พิกัดของคุณคือ: {latitude}, {longitude}\n\n"
            "ในเวอร์ชันใช้งานจริง ข้อมูลชุดนี้จะถูกส่งตรงเข้าศูนย์สั่งการและบันทึกฐานข้อมูลเพื่อนำทีมกู้ภัยเข้าช่วยเหลือทันทีครับ"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
