import os
from flask import Flask, request, abort
from google import genai
from google.genai import types
from linebot.v3 import LineBotApi
from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# ดึงค่า Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ตั้งค่า Line Bot
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ตั้งค่า Gemini Client ตัวใหม่ (แทนการใช้ genai.configure แบบเดิม)
client = genai.Client(api_key=GEMINI_API_KEY)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text
    
    # กำหนดลักษณะนิสัยของ Bot (System Instruction)
    system_instruction = "คุณคือผู้ช่วย AI ที่เป็นมิตรและตอบคำถามอย่างชาญฉลาด"

    try:
        # เรียกใช้ Gemini API เวอร์ชันใหม่
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction
            )
        )
        reply_text = response.text
    except Exception as e:
        app.logger.error(f"Gemini API Error: {e}")
        reply_text = "ขออภัยด้วยครับ ตอนนี้ระบบของผมขัดข้องเล็กน้อย"

    # ส่งข้อความกลับหาผู้ใช้ผ่าน LINE
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
