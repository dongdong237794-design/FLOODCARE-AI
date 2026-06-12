from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

import google.generativeai as genai
import os

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")

@app.route("/")
def home():
    return "FLOODCARE AI Running"

@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers["X-Line-Signature"]

    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    user_text = event.message.text

    try:

        response = model.generate_content(
            f"""
            คุณคือ FLOODCARE AI

            ช่วยตอบคำถามเกี่ยวกับ
            - น้ำท่วม
            - อุทกภัย
            - การอพยพ
            - การปฐมพยาบาล
            - การเตรียมตัวรับมือภัยพิบัติ
            - ตอบแบบกระชับ สุภาพ
            - ใช้อีโมจิ

            คำถาม:
            {user_text}
            """
        )

        reply = response.text

    except Exception:

        reply = """
ขณะนี้ AI ไม่พร้อมใช้งาน

191 ตำรวจ
1669 ฉุกเฉิน
1784 ปภ.
"""

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    app.run()
