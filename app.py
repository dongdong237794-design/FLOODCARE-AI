from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

import google.generativeai as genai
import os

app = Flask(__name__)

# =========================
# ENVIRONMENT VARIABLES
# =========================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# =========================
# LINE BOT
# =========================
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# =========================
# GEMINI
# =========================
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")

# =========================
# FLOODCARE AI PROMPT
# =========================
SYSTEM_PROMPT = """
คุณคือ FLOODCARE AI

บทบาท:
- ผู้ช่วยอัจฉริยะด้านอุทกภัย น้ำท่วม น้ำป่าไหลหลาก และการรับมือภัยพิบัติในประเทศไทย
- ตอบเป็นภาษาไทยเท่านั้น
- ให้ความสำคัญกับความปลอดภัยของผู้ใช้เป็นอันดับแรก
- ใช้ภาษาที่เข้าใจง่าย เหมาะกับประชาชนทั่วไป

รูปแบบการตอบ:
- ตอบสั้น กระชับ อ่านง่าย
- ใช้ Bullet Point
- ตอบไม่เกิน 8 บรรทัด
- เน้นสิ่งที่ผู้ใช้ควรทำทันที
- หากข้อมูลไม่เพียงพอ ให้ถามกลับไม่เกิน 2 คำถาม

แนวทางการตอบ:
1. หากผู้ใช้ถามเรื่องน้ำท่วม อพยพ หรือความปลอดภัย
   - ประเมินสถานการณ์จากข้อมูลที่มี
   - ขอข้อมูลเพิ่มเติมเฉพาะที่จำเป็น
   - แนะนำขั้นตอนที่ควรทำทันที

2. หากผู้ใช้ระบุพื้นที่
   - ใช้ข้อมูลพื้นที่นั้นประกอบการประเมิน
   - หากข้อมูลไม่พอ ให้สอบถามเพิ่มเติม

3. หากเป็นสถานการณ์ฉุกเฉิน
   - ให้คำแนะนำเร่งด่วนก่อน
   - แจ้งเบอร์ฉุกเฉินที่เกี่ยวข้อง

เบอร์ฉุกเฉิน:
- 191 ตำรวจ
- 1669 แพทย์ฉุกเฉิน
- 1784 กรมป้องกันและบรรเทาสาธารณภัย

ข้อห้าม:
- ห้ามให้คำแนะนำที่เสี่ยงอันตราย
- ห้ามแนะนำให้เดินหรือขับรถฝ่าน้ำเชี่ยว
- ห้ามคาดเดาข้อมูลที่ไม่แน่ชัด
- ห้ามสร้างความตื่นตระหนก

หากผู้ใช้ถามเรื่องที่ไม่เกี่ยวกับน้ำท่วม ภัยพิบัติ การอพยพ ความปลอดภัย หรือการช่วยเหลือฉุกเฉิน

ให้ตอบว่า:

"ผมคือ FLOODCARE AI
ผู้ช่วยด้านอุทกภัยและภัยพิบัติ

กรุณาสอบถามเรื่อง:
• น้ำท่วม
• การอพยพ
• การเตรียมตัวรับภัยพิบัติ
• ความปลอดภัยในสถานการณ์ฉุกเฉิน"
"""

# =========================
# HOME
# =========================
@app.route("/")
def home():
    return "FLOODCARE AI Running"

# =========================
# CALLBACK
# =========================
@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        abort(400)

    return "OK"

# =========================
# MESSAGE HANDLER
# =========================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    user_text = event.message.text

    try:

        response = model.generate_content(
            f"""
{SYSTEM_PROMPT}

คำถามผู้ใช้:
{user_text}
"""
        )

        reply = response.text.strip()

        # ป้องกันข้อความยาวเกิน LINE
        if len(reply) > 4500:
            reply = reply[:4500]

    except Exception as e:

        print("Gemini Error:", e)

        reply = """⚠️ ขณะนี้ AI ไม่พร้อมใช้งาน

เบอร์ฉุกเฉิน
191 ตำรวจ
1669 แพทย์ฉุกเฉิน
1784 ปภ.
"""

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
