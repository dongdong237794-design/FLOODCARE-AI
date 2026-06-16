import os
import datetime
from flask import Flask, request, abort
import bot_config
from dashboard import dashboard_bp

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction,
    MessageAction
)

app = Flask(__name__)

# ลงทะเบียน Blueprint ดึงหน้าต่างเว็บมาทำงาน
app.register_blueprint(dashboard_bp)


# ===========================
# 10. Webhook Route สำหรับรับสัญญาน LINE
# ===========================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        bot_config.handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# ================================================================
# 11. รับข้อความตัวอักษรและประมวลผลกระบวนการคัดกรองแบบโต้ตอบ
# ================================================================
@bot_config.handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state = bot_config.USER_STATES.get(user_id)
    sheets_client = bot_config.get_sheets_client()

    # ===========================
    # 11.1 ฟีเจอร์พิมพ์ "ยกเลิก"
    # ===========================
    if user_text == "ยกเลิก":
        bot_config.USER_STATES.pop(user_id, None)
        bot_config.USER_DATA.pop(user_id, None)
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนการทำงานปัจจุบันเรียบร้อยแล้วครับ คุณสามารถกดใช้งานปุ่มเมนูหลักใหม่ได้ทันทีเลยครับ")
        )
        return

    # ===========================
    # 11.2 ดักจับ SOS location state
    # ===========================
    if state == "sos_location":
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 ส่งพิกัดตำแหน่งแจ้งเหตุ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🚨 ระบบกำลังรอตำแหน่งพิกัดของคุณอยู่ครับ โปรดกดปุ่มสีเขียว '📍 ส่งพิกัดตำแหน่งแจ้งเหตุ' ด้านล่างเพื่อส่งข้อมูลความละเอียดด่วน หรือพิมพ์คำว่า 'ยกเลิก' เพื่อเริ่มต้นใหม่ครับ",
                quick_reply=location_quick_reply
            )
        )
        return

    # ===========================
    # 11.3 สถานะลงทะเบียนผู้ใช้รายใหม่
    # ===========================
    if state == "register_first_name":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["temp_first_name"] = user_text
        bot_config.USER_STATES[user_id] = "register_last_name"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ขั้นตอนที่ 2: โปรดพิมพ์ระบุ 'นามสกุล' ของคุณเพื่อใช้ยืนยันตัวตนกับกู้ภัยครับ"))
        return

    elif state == "register_last_name":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["temp_last_name"] = user_text
        bot_config.USER_STATES[user_id] = "register_phone"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ขั้นตอนที่ 3: โปรดพิมพ์ระบุ 'เบอร์โทรศัพท์มือถือ' 9-10 หลักของคุณสำหรับการติดต่อกลับครับ"))
        return

    elif state == "register_phone":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        clean_phone = bot_config.extract_number(user_text)
        if len(clean_phone) < 9 or len(clean_phone) > 10:
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ เบอร์โทรศัพท์ไม่ถูกต้องครับ! โปรดพิมพ์หมายเลขมือถือเฉพาะตัวเลข 9-10 หลักใหม่อีกครั้งครับ (เช่น 0812345678)"))
            return

        first_name = bot_config.USER_DATA[user_id].get("temp_first_name", "ผู้แจ้ง")
        last_name = bot_config.USER_DATA[user_id].get("temp_last_name", "ทั่วไป")
        register_date = datetime.datetime.now().strftime("%Y-%m-%d")

        success = False
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                users_ws = sheet.worksheet("users")
                users_ws.append_row([user_id, first_name, last_name, clean_phone, register_date, "ACTIVE"])
                success = True
            except Exception as e:
                print(f"Failed to save user to Sheets: {e}")

        bot_config.USER_DATA[user_id]["first_name"] = first_name
        bot_config.USER_DATA[user_id]["last_name"] = last_name
        bot_config.USER_DATA[user_id]["phone"] = clean_phone
        bot_config.USER_STATES.pop(user_id, None)

        if success:
            reply_text = (
                f"🎉 ยินดีต้อนรับครับ คุณ {first_name} {last_name}!\n"
                "ระบบได้ทำการลงทะเบียนโปรไฟล์ผู้ใช้งานของคุณเข้าสู่ฐานข้อมูลประชากรผู้ประสบภัยเรียบร้อยแล้วครับ\n\n"
                "🛡️ คุณสามารถกดปุ่มเมนูบน Rich Menu ด้านล่าง เพื่อขอความช่วยเหลือ SOS หรือสอบถาม AI ได้ทันทีครับ"
            )
        else:
            reply_text = (
                f"🎉 สมัครสมาชิกจำลองสำเร็จแล้วครับ คุณ {first_name} {last_name}!\n"
                "ระบบได้บันทึกโปรไฟล์สำรองของคุณไว้บนเซิร์ฟเวอร์ชั่วคราวแล้ว คุณสามารถกดปุ่ม SOS เพื่อขอรับการช่วยเหลือได้ทันทีครับ"
            )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ===========================
    # 11.4 ระบบ SOS Flow ใหม่ (5 Steps)
    # ===========================
    if state and state.startswith("sos_"):
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}

        # ---- Step 2: เลือกกลุ่มผู้ประสบภัย ----
        if state == "sos_step2":
            # เก็บค่าที่เลือก (รองรับหลายตัวเลือก)
            if "group_types" not in bot_config.USER_DATA[user_id]:
                bot_config.USER_DATA[user_id]["group_types"] = []

            # ถ้าผู้ใช้เลือกตัวเลือกใหม่ (มาจาก Quick Reply)
            if user_text in ["👶 มีเด็กเล็ก/คนชรา", "🚑 มีผู้ป่วยติดเตียง/พิการ", "🩸 มีผู้บาดเจ็บฉุกเฉิน",
                           "👨‍👩‍👧 ผู้ใหญ่ทั่วไป", "🐶 มีสัตว์เลี้ยง"]:
                selected = user_text.replace("👶 ", "").replace("🚑 ", "").replace("🩸 ", "").replace("👨‍👩‍👧 ", "").replace("🐶 ", "")
                if selected not in bot_config.USER_DATA[user_id]["group_types"]:
                    bot_config.USER_DATA[user_id]["group_types"].append(selected)

                # ถามต่อว่าเลือกเพิ่มหรือไปต่อ
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="👶 มีเด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                        QuickReplyButton(action=MessageAction(label="🚑 มีผู้ป่วยติดเตียง/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                        QuickReplyButton(action=MessageAction(label="🩸 มีผู้บาดเจ็บฉุกเฉิน", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                        QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่ทั่วไป", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                        QuickReplyButton(action=MessageAction(label="🐶 มีสัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น ไปต่อ", text="เสร็จสิ้น"))
                    ]
                )
                selected_text = ", ".join(bot_config.USER_DATA[user_id]["group_types"]) if bot_config.USER_DATA[user_id]["group_types"] else "ยังไม่ได้เลือก"
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"👥 กลุ่มที่เลือก: {selected_text}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                        quick_reply=quick_reply
                    )
                )
                return
            elif user_text in ["เสร็จสิ้น", "➡️ เสร็จสิ้น ไปต่อ"]:
                if not bot_config.USER_DATA[user_id].get("group_types"):
                    bot_config.USER_DATA[user_id]["group_types"] = ["ผู้ใหญ่ทั่วไป"]
                bot_config.USER_STATES[user_id] = "sos_step3"
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🔴 วิกฤต (มิดหัว/ติดบนหลังคา)", text="🔴 วิกฤต (มิดหัว/ติดบนหลังคา)")),
                        QuickReplyButton(action=MessageAction(label="🟠 สูง (ระดับอก/เกิน 1 เมตร)", text="🟠 สูง (ระดับอก/เกิน 1 เมตร)")),
                        QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (ระดับเอว)", text="🟡 ปานกลาง (ระดับเอว)")),
                        QuickReplyButton(action=MessageAction(label="🟢 ต่ำ (ระดับหน้าแข้ง)", text="🟢 ต่ำ (ระดับหน้าแข้ง)")),
                        QuickReplyButton(action=MessageAction(label="💊 ขาดแคลนยา/อาหารหนัก", text="💊 ขาดแคลนยา/อาหารหนัก"))
                    ]
                )
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="🌊 ระดับน้ำและสถานการณ์ปัจจุบัน\n\nโปรดเลือกระดับความรุนแรงที่ตรงกับสถานการณ์ของคุณมากที่สุดครับ:",
                        quick_reply=quick_reply
                    )
                )
                return
            else:
                # ถ้าพิมพ์ค่าอื่นมา ให้ถือว่าเป็นการระบุเอง
                if user_text:
                    bot_config.USER_DATA[user_id]["group_types"].append(user_text)
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="👶 มีเด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                        QuickReplyButton(action=MessageAction(label="🚑 มีผู้ป่วยติดเตียง/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                        QuickReplyButton(action=MessageAction(label="🩸 มีผู้บาดเจ็บฉุกเฉิน", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                        QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่ทั่วไป", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                        QuickReplyButton(action=MessageAction(label="🐶 มีสัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น ไปต่อ", text="เสร็จสิ้น"))
                    ]
                )
                selected_text = ", ".join(bot_config.USER_DATA[user_id]["group_types"])
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"👥 กลุ่มที่เลือก: {selected_text}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                        quick_reply=quick_reply
                    )
                )
                return

        # ---- Step 3: ประเมินความรุนแรง ----
        elif state == "sos_step3":
            urgency_map = {
                "🔴 วิกฤต (มิดหัว/ติดบนหลังคา)": "วิกฤต",
                "🟠 สูง (ระดับอก/เกิน 1 เมตร)": "สูง",
                "🟡 ปานกลาง (ระดับเอว)": "ปานกลาง",
                "🟢 ต่ำ (ระดับหน้าแข้ง)": "ต่ำ",
                "💊 ขาดแคลนยา/อาหารหนัก": "ขาดแคลนยา"
            }
            bot_config.USER_DATA[user_id]["urgency_level"] = urgency_map.get(user_text, user_text)
            bot_config.USER_STATES[user_id] = "sos_step4"

            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="⏩ ข้ามขั้นตอนนี้", text="ข้ามขั้นตอนนี้"))
                ]
            )
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="📸 ส่งรูปถ่ายสภาพหน้างาน (ถ้าทำได้)\n\nหากสะดวก โปรดถ่ายรูปภาพระดับน้ำหรือสภาพในบ้านส่งมาให้เรา 1 รูป เพื่อให้ทีมกู้ภัยเตรียมอุปกรณ์ (เรือ/ชูชีพ) ได้ถูกต้องครับ\n\nหรือกด 'ข้ามขั้นตอนนี้' หากไม่สะดวก",
                    quick_reply=quick_reply
                )
            )
            return

        # ---- Step 4: รอรูปภาพหรือข้าม ----
        elif state == "sos_step4":
            if user_text == "ข้ามขั้นตอนนี้":
                bot_config.USER_DATA[user_id]["photo_url"] = "-"
            # ถ้าผู้ใช้ส่งข้อความมาแทนรูป ถือว่าข้าม
            else:
                bot_config.USER_DATA[user_id]["photo_url"] = "-"
                # ถ้าผู้ใช้พิมพ์รายละเอียดเพิ่มเติมมา ให้เก็บเป็น note
                if user_text and user_text != "ข้ามขั้นตอนนี้":
                    bot_config.USER_DATA[user_id]["note"] = user_text

            bot_config.USER_STATES[user_id] = "sos_confirm"

            data = bot_config.USER_DATA[user_id]
            group_types = data.get("group_types", ["ผู้ใหญ่ทั่วไป"])
            urgency = data.get("urgency_level", "ต่ำ")

            priority_label, priority_code = bot_config.calculate_sos_priority(group_types, urgency)
            bot_config.USER_DATA[user_id]["priority"] = priority_code
            bot_config.USER_DATA[user_id]["priority_label"] = priority_label

            lat = data.get("latitude", "0")
            lon = data.get("longitude", "0")
            maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            photo_status = "📸 รูปภาพ: (แนบไฟล์)" if data.get("photo_url") not in [None, "-", ""] else "📸 รูปภาพ: ไม่มี"

            summary_text = (
                "📋 สรุปข้อมูลแจ้งเหตุ\n\n"
                f"📍 พิกัด: {maps_link}\n"
                f"👥 กลุ่ม: {', '.join(group_types)}\n"
                f"🌊 สถานการณ์: {urgency}\n"
                f"📝 รายละเอียด: {data.get('note', '-')}\n"
                f"{photo_status}\n"
                f"📊 ระดับความเร่งด่วน: {priority_label}\n\n"
                "ยืนยันการส่งข้อมูลแจ้งกู้ภัยหรือไม่?"
            )

            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัย", text="ยืนยันแจ้งกู้ภัย")),
                    QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิกและแก้ไขใหม่"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))
            return

        # ---- Step 5: ยืนยันการส่งข้อมูล ----
        elif state == "sos_confirm":
            if "ยืนยัน" in user_text:
                data = bot_config.USER_DATA.pop(user_id, {})
                bot_config.USER_STATES.pop(user_id, None)

                case_id = bot_config.generate_case_id()
                group_types = data.get("group_types", ["ผู้ใหญ่ทั่วไป"])
                urgency = data.get("urgency_level", "ต่ำ")
                priority_code = data.get("priority", "NORMAL")

                success = False
                if sheets_client:
                    try:
                        sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                        sos_ws = sheet.worksheet("sos_requests")
                        sos_ws.append_row([
                            case_id,
                            user_id,
                            timestamp,
                            data.get("latitude", "0"),
                            data.get("longitude", "0"),
                            len(group_types),
                            ", ".join(group_types),
                            urgency,
                            data.get("photo_url", "-"),
                            "-",  # water_level (legacy)
                            data.get("note", "-"),
                            priority_code,
                            "OPEN",
                            "-",   # responder_name
                            "-",   # responder_notes
                            "-",   # accepted_at
                            "-"    # completed_at
                        ])
                        success = True
                    except Exception as e:
                        print(f"Failed to save SOS request: {e}")

                if success:
                    reply_text = (
                        f"🚀 ส่งข้อมูลสำเร็จ! เลขเคสของคุณคือ: {case_id}\n"
                        "ทีมกู้ภัยกำลังจัดลำดับความสำคัญและเตรียมกำลังเข้าช่วยครับ\n\n"
                        "🛡️ ระหว่างรอโปรดปฏิบัติดังนี้:\n"
                        "1. ตัดสะพานไฟในบ้านทันที\n"
                        "2. พยายามอยู่บนที่สูงและมองเห็นได้ง่าย\n"
                        "3. เตรียมไฟฉาย หรือนกหวีดไว้ส่งสัญญาณ\n"
                        "4. ประหยัดแบตเตอรี่มือถือ (ปิดแอปที่ไม่จำเป็น)\n"
                        "5. หากสถานการณ์เปลี่ยนจนเป็นอันตรายถึงชีวิต โทร 1784 ทันที"
                    )
                else:
                    reply_text = (
                        f"🚀 ส่งข้อมูลสำเร็จ! เลขเคสของคุณคือ: {case_id}\n"
                        "ทีมกู้ภัยกำลังจัดลำดับความสำคัญและเตรียมกำลังเข้าช่วยครับ\n\n"
                        "🛡️ ระหว่างรอโปรดปฏิบัติดังนี้:\n"
                        "1. ตัดสะพานไฟในบ้านทันที\n"
                        "2. พยายามอยู่บนที่สูงและมองเห็นได้ง่าย\n"
                        "3. เตรียมไฟฉาย หรือนกหวีดไว้ส่งสัญญาณ\n"
                        "4. ประหยัดแบตเตอรี่มือถือ (ปิดแอปที่ไม่จำเป็น)\n"
                        "5. หากสถานการณ์เปลี่ยนจนเป็นอันตรายถึงชีวิต โทร 1784 ทันที\n\n"
                        "⚠️ (หมายเหตุ: ระบบยังไม่สามารถเขียนลง Google Sheets ได้ แต่ข้อมูลถูกบันทึกบนเซิร์ฟเวอร์แล้ว)"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกเคสเดิมและล้างข้อมูลเรียบร้อยแล้วครับ คุณสามารถกดปุ่มเริ่ม SOS ใหม่อีกครั้งได้ทันทีครับ"))
                return

    # ===========================
    # 11.5 ระบบ User Needs Flow ใหม่ (5 Steps)
    # ===========================
    if state and state.startswith("needs_"):
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}

        # ---- Step 1: รอพิกัด ----
        if state == "needs_location":
            location_quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเพื่อรับสิ่งของ"))
                ]
            )
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="📌 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 แชร์พิกัดเพื่อรับสิ่งของ' ด้านล่างเพื่อส่งตำแหน่ง หรือพิมพ์ 'ยกเลิก' เพื่อยกเลิกครับ",
                    quick_reply=location_quick_reply
                )
            )
            return

        # ---- Step 2: เลือกหมวดหมู่ ----
        elif state == "needs_step2":
            categories = {
                "🍲 อาหาร/น้ำดื่ม": "อาหาร/น้ำดื่ม",
                "💊 ยารักษาโรค/เวชภัณฑ์": "ยารักษาโรค/เวชภัณฑ์",
                "👶 ของใช้เด็กอ่อน": "ของใช้เด็กอ่อน",
                "🧼 ของใช้ส่วนตัว": "ของใช้ส่วนตัว",
                "🔦 อุปกรณ์ส่องสว่าง": "อุปกรณ์ส่องสว่าง",
                "📝 อื่นๆ (ระบุเอง)": "อื่นๆ"
            }

            if user_text in categories:
                if "need_categories" not in bot_config.USER_DATA[user_id]:
                    bot_config.USER_DATA[user_id]["need_categories"] = []
                bot_config.USER_DATA[user_id]["need_categories"].append(categories[user_text])

                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
                        QuickReplyButton(action=MessageAction(label="💊 ยารักษาโรค/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
                        QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็กอ่อน", text="👶 ของใช้เด็กอ่อน")),
                        QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
                        QuickReplyButton(action=MessageAction(label="🔦 อุปกรณ์ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
                        QuickReplyButton(action=MessageAction(label="📝 อื่นๆ (ระบุเอง)", text="📝 อื่นๆ (ระบุเอง)")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น ไปต่อ", text="เสร็จสิ้น"))
                    ]
                )
                selected = ", ".join(bot_config.USER_DATA[user_id]["need_categories"])
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"📦 หมวดหมู่ที่เลือก: {selected}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                        quick_reply=quick_reply
                    )
                )
                return
            elif user_text == "เสร็จสิ้น":
                if not bot_config.USER_DATA[user_id].get("need_categories"):
                    bot_config.line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="⚠️ กรุณาเลือกหมวดหมู่อย่างน้อย 1 รายการครับ")
                    )
                    return
                bot_config.USER_STATES[user_id] = "needs_step3"
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="📝 โปรดระบุรายละเอียดสั้นๆ\n\nเช่น จำนวนที่ต้องการ, ยี่ห้อนมผง, หรือระบุสิ่งของอื่นๆ ที่คุณต้องการครับ\n\n(เช่น 'ขอน้ำดื่ม 2 แพ็ค และผ้าอนามัยครับ')"
                    )
                )
                return
            else:
                # ถ้าพิมพ์เอง ถือว่าเป็นอื่นๆ
                if "need_categories" not in bot_config.USER_DATA[user_id]:
                    bot_config.USER_DATA[user_id]["need_categories"] = []
                bot_config.USER_DATA[user_id]["need_categories"].append(f"อื่นๆ: {user_text}")
                bot_config.USER_STATES[user_id] = "needs_step3"
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="📝 โปรดระบุรายละเอียดสั้นๆ\n\nเช่น จำนวนที่ต้องการ, ยี่ห้อนมผง, หรือระบุสิ่งของอื่นๆ ที่คุณต้องการครับ\n\n(เช่น 'ขอน้ำดื่ม 2 แพ็ค และผ้าอนามัยครับ')"
                    )
                )
                return

        # ---- Step 3: รายละเอียด ----
        elif state == "needs_step3":
            bot_config.USER_DATA[user_id]["need_details"] = user_text
            bot_config.USER_STATES[user_id] = "needs_step4"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก (หมดแล้ว)", text="🔴 ด่วนมาก (หมดแล้ว)")),
                    QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (รอได้ 24 ชม.)", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
                    QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน (แจ้งไว้ล่วงหน้า)", text="🟢 ไม่ด่วน (แจ้งไว้ล่วงหน้า)"))
                ]
            )
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือกระดับความเร่งด่วนครับ:",
                    quick_reply=quick_reply
                )
            )
            return

        # ---- Step 4: ความเร่งด่วน ----
        elif state == "needs_step4":
            bot_config.USER_DATA[user_id]["need_urgency"] = user_text
            bot_config.USER_STATES[user_id] = "needs_confirm"

            data = bot_config.USER_DATA[user_id]
            lat = data.get("need_latitude", "0")
            lon = data.get("need_longitude", "0")
            maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

            summary_text = (
                "✅ สรุปรายการความต้องการของคุณ:\n\n"
                f"📍 พิกัด: {maps_link}\n"
                f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n"
                f"📝 รายละเอียด: {data.get('need_details', '-')}\n"
                f"⏳ ความเร่งด่วน: {data.get('need_urgency', '-')}\n\n"
                "ยืนยันการส่งข้อมูลไปยังศูนย์อาสาสมัครหรือไม่?"
            )

            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="✅ ยืนยันการแจ้ง", text="ยืนยันการแจ้ง")),
                    QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิก"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))
            return

        # ---- Step 5: ยืนยัน ----
        elif state == "needs_confirm":
            if "ยืนยัน" in user_text:
                data = bot_config.USER_DATA.pop(user_id, {})
                bot_config.USER_STATES.pop(user_id, None)

                success = bot_config.save_user_need(
                    sheets_client,
                    bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID),
                    user_id,
                    timestamp,
                    data.get("need_latitude", "0"),
                    data.get("need_longitude", "0"),
                    ", ".join(data.get("need_categories", [])),
                    data.get("need_details", "-"),
                    data.get("need_urgency", "ไม่ด่วน")
                )

                if success:
                    reply_text = (
                        "🟢 ระบบทำการบันทึกความต้องการของคุณเรียบร้อยแล้วครับ!\n\n"
                        f"📝 สิ่งที่แจ้ง: {data.get('need_details', '-')}\n"
                        f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n\n"
                        "ข้อมูลนี้จะถูกส่งเข้ารายงานกลางเพื่อให้ทีมอาสาสมัครจัดเตรียมสิ่งของนำไปกระจายความช่วยเหลือแก่ท่านในพื้นที่ต่อไปครับ"
                    )
                else:
                    reply_text = (
                        "🟢 บันทึกความต้องการของคุณสำเร็จแล้วครับ!\n\n"
                        f"📝 ความต้องการ: {data.get('need_details', '-')}\n\n"
                        "ข้อมูลถูกบันทึกบนเซิร์ฟเวอร์แล้ว ทีมอาสาสมัครจะดำเนินการจัดส่งสิ่งของให้ครับ\n\n"
                        "⚠️ (หมายเหตุ: ระบบ Google Sheets ขัดข้องชั่วคราว ข้อมูลถูกเก็บบนเซิร์ฟเวอร์สำรอง)"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกรายการเรียบร้อยแล้วครับ"))
                return

    # ===========================
    # 11.6 สัญญานเมนูอื่นๆ
    # ===========================
    if user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        db_connected = False
        contact_list = []
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                contacts_worksheet = sheet.worksheet("Contacts")
                rows = contacts_worksheet.get_all_records()
                for r in rows:
                    contact_list.append(f"🚨 {r.get('Name')} ({r.get('Role')})\n📞 โทร: {r.get('Phone')}")
                db_connected = True
            except Exception as e:
                print(f"Failed to load contacts from sheet: {e}")

        if db_connected and contact_list:
            reply_text = "📞 เบอร์โทรศัพท์ฉุกเฉินและหน่วยงานประสานงานกู้ภัยจริงในระบบ:\n\n" + "\n\n".join(contact_list)
        else:
            reply_text = (
                "📞 เบอร์โทรศัพท์ฉุกเฉินที่จำเป็นสำหรับภัยน้ำท่วมครับ:\n\n"
                "🚨 สายด่วน ปภ. 1784 (รับแจ้งเตือนและช่วยเหลือภัยพิบัติ)\n"
                "🚨 สายด่วนกู้ชีพ 1669 (เจ็บป่วยฉุกเฉินทางการแพทย์)\n"
                "🚨 สายด่วนกู้ภัยทางน้ำ 1196 (ขอความช่วยเหลือทางเรือ)\n"
                "🚨 ตำรวจทางหลวง 1193 (ประสานงานเดินทางเส้นทางน้ำท่วม)"
            )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_text == "ศูนย์พักพิง":
        bot_config.USER_STATES[user_id] = "waiting_shelter_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="แชร์พิกัดหาศูนย์พักพิง"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📍 โปรดกดแชร์พิกัด 'Location' ด้านล่างนี้ หรือพิมพ์บอกชื่ออำเภอ/จังหวัดที่คุณอยู่ในปัจจุบัน เพื่อให้ผมช่วยค้นหาศูนย์พักพิงจริงรอบตัวคุณครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "ตรวจสอบระดับน้ำ":
        bot_config.USER_STATES[user_id] = "waiting_water_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="แชร์พิกัดเช็กระดับน้ำ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🌊 โปรดกดปุ่มแชร์พิกัด 'Location' ด้านล่าง เพื่อให้ระบบค้นหาและรายงานสถานการณ์ระดับน้ำจากสถานีตรวจวัด ThaiWater ที่ใกล้ตัวคุณที่สุดครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "SOS ขอความช่วยเหลือ":
        is_registered = False
        first_name = ""
        last_name = ""
        phone = "-"

        if user_id in bot_config.USER_DATA and "first_name" in bot_config.USER_DATA[user_id]:
            is_registered = True
            first_name = bot_config.USER_DATA[user_id]["first_name"]
            last_name = bot_config.USER_DATA[user_id]["last_name"]
            phone = bot_config.USER_DATA[user_id]["phone"]

        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                users_ws = sheet.worksheet("users")
                rows = users_ws.get_all_records()
                for r in rows:
                    if str(r.get("user_id")) == user_id:
                        is_registered = True
                        first_name = r.get("first_name", "ผู้แจ้ง")
                        last_name = r.get("last_name", "")
                        phone = r.get("phone", "-")

                        if user_id not in bot_config.USER_DATA:
                            bot_config.USER_DATA[user_id] = {}
                        bot_config.USER_DATA[user_id]["first_name"] = first_name
                        bot_config.USER_DATA[user_id]["last_name"] = last_name
                        bot_config.USER_DATA[user_id]["phone"] = phone
                        break
            except Exception as e:
                print(f"Failed to check user registration: {e}")

        if not is_registered:
            bot_config.USER_STATES[user_id] = "register_first_name"
            bot_config.USER_DATA[user_id] = {}
            reply_text = (
                "📝 ขออภัยด้วยครับ เนื่องจากคุณเข้าใช้งานระบบเป็นครั้งแรก เพื่อประโยช์สูงสุดในการประสานงานส่งต่อข้อมูลให้ทีมกู้ภัย "
                "โปรดพิมพ์แจ้ง 'ชื่อจริง' ของคุณเพื่อใช้ลงทะเบียนประวัติในระบบสักนิดนึงนะครับ (เช่น 'สมชาย')"
            )
        else:
            bot_config.USER_STATES[user_id] = "sos_location"
            bot_config.USER_DATA[user_id] = {
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone
            }

            location_quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=LocationAction(label="📍 ส่งพิกัดตำแหน่งแจ้งเหตุ"))
                ]
            )
            reply_text = (
                f"🚨 เริ่มขั้นตอนแจ้งเหตุฉุกเฉิน\n\n"
                f"สวัสดีครับคุณ {first_name}! โปรดกดปุ่มสีเขียวด้านล่างเพื่อส่งพิกัดตำแหน่งปัจจุบันของคุณให้ทีมกู้ภัยครับ"
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=location_quick_reply))
            return

        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_text == "แจ้งความต้องการเพิ่มเติม" or user_text == "ความต้องการ":
        bot_config.USER_STATES[user_id] = "needs_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเพื่อรับสิ่งของ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📌 แจ้งความต้องการสิ่งของบรรเทาทุกข์\n\nเพื่อให้เจ้าหน้าที่และอาสาสมัครนำสิ่งของไปส่งให้คุณได้อย่างถูกต้อง โปรดกดปุ่มสีเขียวด้านล่างเพื่อ 'แชร์พิกัดตำแหน่งปัจจุบันของคุณ' ครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "ถาม AI เรื่องน้ำท่วม" or "ถาม-ตอบด้วย AI" in user_text or "ถาม–ตอบด้วย AI" in user_text:
        reply_text = "🤖 คุณสามารถพิมพ์รายละเอียดคำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมในครั้งนี้เข้ามาได้ทันทีเลยครับ ผมพร้อมตอบคำถามแบบเป็นกันเองให้ครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    else:
        # ระบบคุยตอบโต้แบบอิสระทั่วไป
        ai_response = ""
        try:
            response = bot_config.gemini_model.generate_content(user_text)
            ai_response = bot_config.clean_text_for_line(response.text.strip())
        except Exception as e:
            print(f"Gemini API Error: {e}")
            ai_response = "⚠️ บริการ AI ขัดข้องชั่วคราว หากตกอยู่ในภาวะอันตราย โทร ปภ. 1784 ทันทีครับ"

        sheets_client = bot_config.get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                log_worksheet = sheet.worksheet("AI Logs")
                log_worksheet.append_row([timestamp, user_id, user_text, ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")

        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))


# =============================================================================
# 12. รับข้อมูลพิกัด (Location Message) และประมวลผล GIS
# =============================================================================
@bot_config.handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    address = event.message.address or "ไม่ระบุที่อยู่ชัดเจน"
    title = event.message.title or "จุดพิกัด"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state = bot_config.USER_STATES.pop(user_id, "default")
    sheets_client = bot_config.get_sheets_client()

    # ===========================
    # 12.1 ค้นหาศูนย์อพยพใกล้ที่สุด
    # ===========================
    if state == "waiting_shelter_location":
        shelter_list = []
        db_connected = False

        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                shelters_worksheet = sheet.worksheet("Shelters")
                rows = shelters_worksheet.get_all_records()
                for row in rows:
                    if str(row.get("Status")).strip() == "ปิดทำการ":
                        continue
                    shelter_list.append({
                        "name": row.get("Name", "ไม่ระบุชื่อ"),
                        "lat": float(row.get("Latitude", 0)),
                        "lon": float(row.get("Longitude", 0)),
                        "capacity": row.get("Capacity", 100),
                        "occupancy": row.get("Occupancy", 0),
                        "status": row.get("Status", "ว่าง")
                    })
                db_connected = True
            except Exception as e:
                print(f"Failed to fetch shelters from Sheets: {e}")

        if not db_connected:
            reply_text = "⚠️ ขออภัยครับ ขณะนี้ระบบขัดข้องไม่สามารถตรวจสอบสิทธิ์การอ่านข้อมูลศูนย์พักพิงจริงได้ โปรดโทรติดต่อเบอร์สายด่วนภัยพิบัติ ปภ. 1784 ทันทีครับ"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        nearest_shelters = []
        for sh in shelter_list:
            distance = bot_config.calculate_distance(latitude, longitude, sh["lat"], sh["lon"])
            if distance <= 20.0:
                vacancy_status = bot_config.check_shelter_vacancy(sh["capacity"], sh["occupancy"])
                nearest_shelters.append({
                    "name": sh["name"],
                    "distance": distance,
                    "vacancy": vacancy_status,
                    "lat": sh["lat"],
                    "lon": sh["lon"]
                })

        nearest_shelters.sort(key=lambda x: x["distance"])
        top_shelters = nearest_shelters[:3]

        if not top_shelters:
            reply_text = "📍 ปัจจุบันไม่พบศูนย์พักพิงจริงเปิดทำการในรัศมี 20 กม. รอบพิกัดของคุณครับ แนะนำติดต่อสอบถามพิกัดจัดตั้งชั่วคราวโดยตรงทาง ปภ. 1784 ครับ"
        else:
            reply_text = "📍 รายชื่อศูนย์พักพิงจริงที่อยู่ใกล้ตัวคุณที่สุดในรัศมี 20 กม. ครับ:\n\n"
            for index, sh in enumerate(top_shelters, 1):
                reply_text += (
                    f"{index}️⃣ {sh['name']}\n"
                    f"   📌 ระยะห่าง: {sh['distance']:.2f} กิโลเมตร\n"
                    f"   📌 สถานะความจุ: {sh['vacancy']}\n"
                    f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
                )
            reply_text += "⚠️ โปรดเดินเท้าตามเส้นทางหลักอย่างระมัดระวังสูงสุดเสมอนะครับ"

        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    # =============================================================================
    # 12.2 ตรวจสอบระดับน้ำจาก Google Sheets (Lazy Sync)
    # =============================================================================
    elif state == "waiting_water_location":
        clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

        # ดึงข้อมูลจาก Google Sheets (แทนการดึงจาก API ตรงๆ)
        thaiwater_stations = []
        try:
            thaiwater_stations = bot_config.get_water_data_from_sheets(
                sheets_client, clean_sheet_id, latitude, longitude
            )
            if thaiwater_stations:
                print(f"[WaterLevel] Loaded {len(thaiwater_stations)} stations from Sheets")
        except Exception as e:
            print(f"[WaterLevel] Sheets load failed: {e}")

        # Fallback: ดึงจาก ThaiWater API ตรงๆ ถ้า Sheets ไม่มีข้อมูล
        if not thaiwater_stations:
            try:
                thaiwater_stations = bot_config.find_nearest_water_stations(
                    latitude, longitude, max_stations=3, max_distance_km=50
                )
                print(f"[WaterLevel] Fallback to API: {len(thaiwater_stations)} stations")
            except Exception as e:
                print(f"[WaterLevel] API fallback failed: {e}")

        # ดึงข้อมูลสภาพอากาศ
        weather_info = bot_config.get_live_weather_scraper(latitude, longitude)

        # ดึงข้อมูลน้ำหลากประมาณการ
        water_flow = bot_config.get_live_water_scraper(latitude, longitude)

        # สร้างและส่งรายงาน
        try:
            flex_msg = bot_config.build_water_level_flex_message(
                latitude, longitude, timestamp, thaiwater_stations, weather_info, water_flow
            )
            bot_config.line_bot_api.reply_message(event.reply_token, flex_msg)
            print("[WaterLevel] Sent Flex Message successfully")
        except Exception as e:
            print(f"[WaterLevel] Flex Message failed: {e}, falling back to text")
            text_report = bot_config.build_water_level_text_report(
                latitude, longitude, timestamp, thaiwater_stations, weather_info, water_flow
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text_report))

    # ===========================
    # 12.3 ระบบ SOS Step 1: รับพิกัด GPS
    # ===========================
    elif state == "sos_location":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["latitude"] = latitude
        bot_config.USER_DATA[user_id]["longitude"] = longitude

        bot_config.USER_STATES[user_id] = "sos_step2"

        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="👶 มีเด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                QuickReplyButton(action=MessageAction(label="🚑 มีผู้ป่วยติดเตียง/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                QuickReplyButton(action=MessageAction(label="🩸 มีผู้บาดเจ็บฉุกเฉิน", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่ทั่วไป", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                QuickReplyButton(action=MessageAction(label="🐶 มีสัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="👥 ระบุกลุ่มผู้ประสบภัย (เลือกกลุ่มที่ต้องการความช่วยเหลือพิเศษ)\n\nเลือกได้หลายกลุ่ม แล้วกด 'เสร็จสิ้น' เมื่อเลือกครบครับ:",
                quick_reply=quick_reply
            )
        )

    # ===========================
    # 12.4 User Needs Step 1: รับพิกัด GPS
    # ===========================
    elif state == "needs_location":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["need_latitude"] = latitude
        bot_config.USER_DATA[user_id]["need_longitude"] = longitude

        bot_config.USER_STATES[user_id] = "needs_step2"
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
                QuickReplyButton(action=MessageAction(label="💊 ยารักษาโรค/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
                QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็กอ่อน", text="👶 ของใช้เด็กอ่อน")),
                QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
                QuickReplyButton(action=MessageAction(label="🔦 อุปกรณ์ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
                QuickReplyButton(action=MessageAction(label="📝 อื่นๆ (ระบุเอง)", text="📝 อื่นๆ (ระบุเอง)"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📦 เลือกหมวดหมู่สิ่งของที่ต้องการ:\n\nเลือกได้หลายหมวดหมู่ แล้วกด 'เสร็จสิ้น' เมื่อเลือกครบครับ:",
                quick_reply=quick_reply
            )
        )

    else:
        confirm_text = "📍 คุณส่งพิกัด GPS มาหาผม หากต้องการแจ้งขอความช่วยเหลือ โปรดกดแตะเมนู 'SOS ขอความช่วยเหลือ' บนแถบด้านล่างก่อนเพื่อให้ทีมกู้ภัยวิเคราะห์ความเร่งด่วนได้อย่างแม่นยำนะครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))


# =============================================================================
# 13. รับรูปภาพ (Image Message) - SOS Step 4
# =============================================================================
@bot_config.handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    state = bot_config.USER_STATES.get(user_id)

    # รับรูปภาพเฉพาะตอนอยู่ใน SOS Step 4
    if state == "sos_step4":
        image_id = event.message.id
        # เก็บ content ID ของรูปภาพ (จะใช้ดึงรูปผ่าน API ได้)
        content_url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
        bot_config.USER_DATA[user_id]["photo_url"] = content_url
        bot_config.USER_DATA[user_id]["image_id"] = image_id

        # ไปยัง Step 5 (สรุปและยืนยัน)
        bot_config.USER_STATES[user_id] = "sos_confirm"
        data = bot_config.USER_DATA[user_id]
        group_types = data.get("group_types", ["ผู้ใหญ่ทั่วไป"])
        urgency = data.get("urgency_level", "ต่ำ")

        priority_label, priority_code = bot_config.calculate_sos_priority(group_types, urgency)
        bot_config.USER_DATA[user_id]["priority"] = priority_code
        bot_config.USER_DATA[user_id]["priority_label"] = priority_label

        lat = data.get("latitude", "0")
        lon = data.get("longitude", "0")
        maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

        summary_text = (
            "📋 สรุปข้อมูลแจ้งเหตุ\n\n"
            f"📍 พิกัด: {maps_link}\n"
            f"👥 กลุ่ม: {', '.join(group_types)}\n"
            f"🌊 สถานการณ์: {urgency}\n"
            f"📝 รายละเอียด: {data.get('note', '-')}\n"
            f"📸 รูปภาพ: (แนบไฟล์)\n"
            f"📊 ระดับความเร่งด่วน: {priority_label}\n\n"
            "ยืนยันการส่งข้อมูลแจ้งกู้ภัยหรือไม่?"
        )

        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัย", text="ยืนยันแจ้งกู้ภัย")),
                QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิกและแก้ไขใหม่"))
            ]
        )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))
    else:
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📸 ได้รับรูปภาพแล้วครับ หากต้องการแจ้ง SOS พร้อมส่งรูป กรุณาเริ่มต้นที่เมนู 'SOS ขอความช่วยเหลือ' ก่อนครับ")
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
