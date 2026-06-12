from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
import google.generativeai as genai
import os

app = Flask(__name__)

# ดึงค่า Token และ Key จาก Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ตั้งค่า LINE Bot และ Webhook
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ตั้งค่า Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# 🌟 [จุดที่ปรับปรุง] ย้ายคำสั่งควบคุมพฤติกรรมมาไว้ใน system_instruction
system_instruction = """
คุณคือ "FLOODCARE AI" ระบบผู้ช่วยปัญญาประดิษฐ์อัจฉริยะที่เชี่ยวชาญด้านการจัดการภัยพิบัติและการเตือนภัยอุทกภัย
ช่วยตอบคำถามเกี่ยวกับ: น้ำท่วม, อุทกภัย, การอพยพ, การปฐมพยาบาล, การเตรียมตัวรับมือภัยพิบัติ
- ตอบแบบกระชับ สุภาพ อ่านง่าย เป็นข้อๆ และใช้อีโมจิให้เหมาะสม
- ห้ามคาดเดาสถานการณ์น้ำท่วมเองเด็ดขาด ให้ยึดหลักความปลอดภัยเป็นหลัก
- หากผู้ใช้พิมพ์ข้อความที่บ่งบอกถึงอันตรายถึงชีวิต (เช่น ไฟดูด, ติดบนหลังคา, จมน้ำ) ให้ตอบกลับด้วยเบอร์ 1669 และ 1784 เป็นตัวหนาในบรรทัดแรกทันที
"""

# สร้าง Model โดยฝัง System Instruction ลงไปเลย
model = genai.GenerativeModel(
    "gemini-2.5-flash",
    system_instruction=system_instruction
)

@app.route("/")
def home():
    return "FLOODCARE AI Running"

@app.route("/callback", methods=["POST"])
def callback():
    # รับค่า Signature จาก LINE เพื่อตรวจสอบความปลอดภัย
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # ข้อความที่ผู้ใช้พิมพ์มา
    user_text = event.message.text

    try:
        # 🌟 [จุดที่ปรับปรุง] ส่งแค่ข้อความผู้ใช้ไปเพียวๆ เพราะ AI รู้บทบาทตัวเองแล้ว
        response = model.generate_content(user_text)
        reply = response.text

    except Exception as e:
        # พิมพ์ Error ลง Console เพื่อให้เรานักพัฒนารู้สาเหตุเวลาที่ระบบพัง
        print(f"Error AI: {e}") 
        
        # ระบบสำรอง (Fallback) เมื่อ AI มีปัญหา
        reply = "⚠️ ขออภัยค่ะ ขณะนี้ AI ไม่พร้อมใช้งาน\n\n📞 สายด่วนฉุกเฉิน:\n191 ตำรวจ\n1669 เจ็บป่วยฉุกเฉิน\n1784 แจ้งภัยน้ำท่วม (ปภ.)"

    # ส่งข้อความกลับไปหาผู้ใช้ใน LINE
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    # เปิดโหมด debug เพื่อให้แอปรีสตาร์ทตัวเองเวลาเราแก้โค้ด
    app.run(port=5000, debug=True)
