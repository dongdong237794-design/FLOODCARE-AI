from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

import google.generativeai as genai
import os

app = Flask(__name__)

# Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# LINE
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ใช้รุ่นที่เสถียรกว่า
model = genai.GenerativeModel("gemini-1.5-flash")

@app.route("/")
def home():
    return "FLOODCARE AI Running"

@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers.get("X-Line-Signature")

    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        abort(400)

    except Exception as e:
        print(f"Webhook Error: {e}")

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    user_text = event.message.text

    try:

        prompt = f"""
คุณคือ FLOODCARE AI

บทบาท:
- ผู้ช่วยด้านอุทกภัยและการรับมือภัยพิบัติ
- ตอบเป็นภาษาไทยเท่านั้น

กฎการตอบ:
- ตอบไม่เกิน 8 บรรทัด
- ใช้ภาษาง่าย อ่านเร็ว
- เน้นสิ่งที่ผู้ใช้ต้องทำทันที
- ใช้ Bullet Point
- หากข้อมูลไม่พอ ให้ถามกลับไม่เกิน 2 คำถาม
- ห้ามตอบเป็นบทความยาว
- ห้ามใช้คำฟุ่มเฟือย

คำถามผู้ใช้:
{user_text}
"""

        response = model.generate_content(prompt)

        if hasattr(response, "text") and response.text:
            reply = response.text
        else:
            reply = "ขออภัย ไม่สามารถสร้างคำตอบได้ในขณะนี้"

    except Exception as e:

        print(f"Gemini Error: {e}")

        reply = """
⚠️ ขณะนี้ AI ไม่พร้อมใช้งาน

เบอร์ฉุกเฉิน
191 ตำรวจ
1669 แพทย์ฉุกเฉิน
1784 ปภ.
"""

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
