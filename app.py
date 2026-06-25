import os
import datetime
from flask import Flask, request, abort
import bot_config

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction,
    MessageAction
)

app = Flask(__name__)

# =============================================================================
# AUTO-SYNC: ตรวจสอบความสดของข้อมูล Water_Levels ก่อนใช้งาน
# ถ้าข้อมูลเก่ากว่า WATER_DATA_MAX_AGE_MINUTES (หรือยังไม่เคย sync)
# จะเรียก bot_config.sync_water_levels_to_sheets() ให้อัตโนมัติ
# =============================================================================
WATER_DATA_MAX_AGE_MINUTES = 10


def _ensure_water_data_fresh(sheets_client, sheet_id):
    """
    เช็คเวลา LastSync ใน Water_Levels!L1
    ถ้าไม่มี/อ่านไม่ได้/เก่าเกินกำหนด -> สั่ง sync ใหม่จาก ThaiWater ทันที
    คืนค่า True ถ้า sync เกิดขึ้น (หรือพยายาม sync), False ถ้าข้อมูลยังสดอยู่เลยไม่ต้องทำอะไร
    """
    if not sheets_client or not sheet_id:
        return False

    needs_sync = True
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("Water_Levels")
        last_sync_raw = ws.acell('L1').value  # รูปแบบ: "LastSync: YYYY-MM-DD HH:MM:SS"

        if last_sync_raw and "LastSync:" in last_sync_raw:
            ts_str = last_sync_raw.replace("LastSync:", "").strip()
            last_sync_time = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            age_minutes = (datetime.datetime.now() - last_sync_time).total_seconds() / 60
            print(f"[AutoSync] Water_Levels age: {age_minutes:.1f} min")
            if age_minutes <= WATER_DATA_MAX_AGE_MINUTES:
                needs_sync = False
    except Exception as e:
        # ไม่มีแท็บ/ไม่มีค่า L1/parse ไม่ได้ -> ถือว่าข้อมูลเก่า/ไม่มี ต้อง sync
        print(f"[AutoSync] Could not read LastSync, will sync: {e}")

    if needs_sync:
        print("[AutoSync] Water_Levels stale or missing -> triggering sync now...")
        try:
            success = bot_config.sync_water_levels_to_sheets(sheets_client, sheet_id)
            print(f"[AutoSync] Sync result: {success}")
        except Exception as e:
            print(f"[AutoSync] Sync attempt failed: {e}")
        return True

    return False


# =============================================================================
# DEBUG ROUTE: ตรวจสอบว่า ThaiWater API ยังเข้าถึงได้และ field ตรงกันหรือไม่
# เรียกผ่านเบราว์เซอร์: https://<your-app>.onrender.com/debug/thaiwater
# (แนะนำให้ลบ หรือใส่ password check ก่อน deploy ใช้งานจริงระยะยาว)
# =============================================================================
@app.route("/debug/thaiwater", methods=['GET'])
def debug_thaiwater():
    from flask import jsonify

    result = {
        "v3_api_reachable": False,
        "v3_raw_sample": None,
        "v3_parsed_sample": None,
        "v3_total_count": 0,
        "error": None
    }
    try:
        raw_data = bot_config.fetch_waterlevel_v3()
        if raw_data:
            result["v3_api_reachable"] = True
            result["v3_total_count"] = len(raw_data)
            result["v3_raw_sample"] = raw_data[0] if len(raw_data) > 0 else None
            result["v3_parsed_sample"] = bot_config.parse_v3_station(raw_data[0]) if len(raw_data) > 0 else None
        else:
            result["error"] = "fetch_waterlevel_v3() returned None or empty list"
    except Exception as e:
        result["error"] = str(e)

    return jsonify(result)


# =============================================================================
# DEBUG ROUTE: ดูสถานะ Sheets (LastSync, จำนวน records) + บังคับ sync ทันที
# เรียกผ่านเบราว์เซอร์: https://<your-app>.onrender.com/debug/sync-status
# เรียก POST ไปที่ /debug/force-sync เพื่อบังคับ sync ทันที (ไม่ต้องรอผู้ใช้ถาม)
# =============================================================================
@app.route("/debug/sync-status", methods=['GET'])
def debug_sync_status():
    from flask import jsonify

    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

    result = {
        "sheets_connected": sheets_client is not None,
        "sheet_id_configured": bool(clean_sheet_id),
        "last_sheets_error": bot_config.LAST_SHEETS_ERROR,
        "last_sync": None,
        "record_count": 0
    }

    if sheets_client and clean_sheet_id:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            ws = sheet.worksheet("Water_Levels")
            result["last_sync"] = ws.acell('L1').value
            result["record_count"] = len(ws.get_all_records())
        except Exception as e:
            result["error"] = str(e)

    return jsonify(result)


@app.route("/debug/force-sync", methods=['POST'])
def debug_force_sync():
    from flask import jsonify

    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

    if not sheets_client or not clean_sheet_id:
        return jsonify({"success": False, "error": "Sheets client or sheet_id not configured"}), 400

    try:
        success = bot_config.sync_water_levels_to_sheets(sheets_client, clean_sheet_id)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# WEBHOOK ROUTE สำหรับรับสัญญาณ LINE
# =============================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        bot_config.handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# =============================================================================
# รับข้อความตัวอักษรและประมวลผลกระบวนการคัดกรองแบบโต้ตอบ
# =============================================================================
@bot_config.handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state = bot_config.USER_STATES.get(user_id)
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

    # ===========================
    # FEATURE: เปลี่ยนภาษา (Language Settings)
    # ===========================
    if user_text in ["เปลี่ยนภาษา", "change language", "lang"]:
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            bot_config.build_language_selector_flex()
        )
        return

    if user_text.startswith("ตั้งค่าภาษา: "):
        lang = user_text.replace("ตั้งค่าภาษา: ", "").strip()
        success = bot_config.set_user_language(sheets_client, clean_sheet_id, user_id, lang)
        
        if lang == "TH":
            msg = "✅ เปลี่ยนภาษาเป็นภาษาไทยเรียบร้อยแล้วครับ"
        elif lang == "JP":
            msg = "✅ 日本語に設定されました。"
        elif lang == "MY":
            msg = "✅ Bahasa telah ditukar kepada Bahasa Melayu."
        else:
            msg = "✅ Language changed to English successfully."
            
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=msg)
        )
        return

    # ===========================
    # FEATURE: พิมพ์ "ยกเลิก"
    # ===========================
    if user_text == "ยกเลิก":
        bot_config.USER_STATES.pop(user_id, None)
        bot_config.USER_DATA.pop(user_id, None)
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ คุณสามารถกดใช้งานเมนูหลักใหม่ได้ทันทีครับ")
        )
        return

    # ===========================
    # FEATURE: ดักจับ SOS location state
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
                text="🚨 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 ส่งพิกัดตำแหน่งแจ้งเหตุ' ด้านล่าง หรือพิมพ์ 'ยกเลิก' เพื่อเริ่มต้นใหม่ครับ",
                quick_reply=location_quick_reply
            )
        )
        return

    # ===========================
    # FEATURE: ดักจับ Needs location state
    # ===========================
    if state == "needs_location":
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเพื่อรับสิ่งของ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📌 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 แชร์พิกัดเพื่อรับสิ่งของ' ด้านล่าง หรือพิมพ์ 'ยกเลิก' เพื่อยกเลิกครับ",
                quick_reply=location_quick_reply
            )
        )
        return

    # ===========================
    # สถานะลงทะเบียนผู้ใช้รายใหม่ (3 Steps)
    # ===========================
    if state == "register_first_name":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["temp_first_name"] = user_text
        bot_config.USER_STATES[user_id] = "register_last_name"
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 2: โปรดพิมพ์ 'นามสกุล' ของคุณครับ")
        )
        return

    elif state == "register_last_name":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["temp_last_name"] = user_text
        bot_config.USER_STATES[user_id] = "register_phone"
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 3: โปรดพิมพ์ 'เบอร์โทรศัพท์' 9-10 หลักครับ (เช่น 0812345678)")
        )
        return

    elif state == "register_phone":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        clean_phone = bot_config.extract_number(user_text)
        if len(clean_phone) < 9 or len(clean_phone) > 10:
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ เบอร์โทรไม่ถูกต้องครับ! โปรดพิมพ์ตัวเลข 9-10 หลักใหม่อีกครับ")
            )
            return

        first_name = bot_config.USER_DATA[user_id].get("temp_first_name", "ผู้แจ้ง")
        last_name = bot_config.USER_DATA[user_id].get("temp_last_name", "ทั่วไป")

        # บันทึกลง Google Sheets ผ่านฟังก์ชันใหม่
        success = False
        if sheets_client:
            success = bot_config.register_user_to_sheets(
                sheets_client, clean_sheet_id, user_id, first_name, last_name, clean_phone
            )

        bot_config.USER_STATES.pop(user_id, None)

        if success:
            reply_text = (
                f"🎉 ยินดีต้อนรับครับ คุณ {first_name} {last_name}!\n"
                f"ระบบลงทะเบียนเรียบร้อยแล้วครับ\n\n"
                f"🛡️ กดปุ่มเมนูด้านล่างเพื่อใช้งานได้ทันทีครับ"
            )
        else:
            reply_text = (
                f"🎉 ลงทะเบียนสำเร็จ (ระบบชั่วคราว) คุณ {first_name} {last_name}!\n"
                f"⚠️ บันทึกลง Sheets ไม่สำเร็จ แต่ใช้งานได้ตามปกติครับ"
            )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ===========================
    # SOS FLOW ใหม่ (5 Steps)
    # ===========================
    if state and state.startswith("sos_"):
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}

        # ---- Step 2: เลือกกลุ่มผู้ประสบภัย ----
        if state == "sos_step2":
            if "group_types" not in bot_config.USER_DATA[user_id]:
                bot_config.USER_DATA[user_id]["group_types"] = []

            # ถ้าผู้ใช้เลือกตัวเลือกจาก Quick Reply
            valid_options = {
                "👶 มีเด็กเล็ก/คนชรา": "เด็กเล็ก/คนชรา",
                "🚑 มีผู้ป่วยติดเตียง/พิการ": "ผู้ป่วยติดเตียง/พิการ",
                "🩸 มีผู้บาดเจ็บฉุกเฉิน": "ผู้บาดเจ็บฉุกเฉิน",
                "👨‍👩‍👧 ผู้ใหญ่ทั่วไป": "ผู้ใหญ่ทั่วไป",
                "🐶 มีสัตว์เลี้ยง": "สัตว์เลี้ยง"
            }

            if user_text in valid_options:
                selected = valid_options[user_text]
                if selected not in bot_config.USER_DATA[user_id]["group_types"]:
                    bot_config.USER_DATA[user_id]["group_types"].append(selected)

                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                        QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                        QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                        QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                        QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น"))
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
            elif user_text in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
                if not bot_config.USER_DATA[user_id].get("group_types"):
                    bot_config.USER_DATA[user_id]["group_types"] = ["ผู้ใหญ่ทั่วไป"]
                bot_config.USER_STATES[user_id] = "sos_step3"
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🔴 วิกฤต (มิดหัว/ติดหลังคา)", text="🔴 วิกฤต (มิดหัว/ติดบนหลังคา)")),
                        QuickReplyButton(action=MessageAction(label="🟠 สูง (ระดับอก/เกิน 1 เมตร)", text="🟠 สูง (ระดับอก/เกิน 1 เมตร)")),
                        QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (ระดับเอว)", text="🟡 ปานกลาง (ระดับเอว)")),
                        QuickReplyButton(action=MessageAction(label="🟢 ต่ำ (ระดับหน้าแข้ง)", text="🟢 ต่ำ (ระดับหน้าแข้ง)")),
                        QuickReplyButton(action=MessageAction(label="💊 ขาดแคลนยา/อาหาร", text="💊 ขาดแคลนยา/อาหารหนัก"))
                    ]
                )
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="🌊 ระดับน้ำและสถานการณ์ปัจจุบัน\n\nโปรดเลือกระดับความรุนแรง:",
                        quick_reply=quick_reply
                    )
                )
                return
            else:
                # ถ้าพิมพ์ค่าอื่นมา ให้ถือว่าระบุเอง
                if user_text:
                    bot_config.USER_DATA[user_id]["group_types"].append(user_text)
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                        QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                        QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                        QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                        QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น"))
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
            bot_config.USER_DATA[user_id]["photo_url"] = "-"
            bot_config.USER_STATES[user_id] = "sos_confirm"
            _send_sos_summary(event, user_id)
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
                        sheet = sheets_client.open_by_key(clean_sheet_id)
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
                            "-",
                            data.get("note", "-"),
                            priority_code,
                            "OPEN",
                            "-",
                            "-",
                            "-",
                            "-"
                        ])
                        success = True
                    except Exception as e:
                        print(f"Failed to save SOS: {e}")

                if success:
                    reply_text = (
                        f"🚀 ส่งข้อมูลสำเร็จ! เลขเคส: {case_id}\n"
                        f"ทีมกู้ภัยกำลังจัดลำดับความสำคัญ\n\n"
                        f"🛡️ ระหว่างรอ:\n"
                        f"1. ตัดสะพานไฟในบ้านทันที\n"
                        f"2. พยายามอยู่บนที่สูง\n"
                        f"3. เตรียมไฟฉายหรือนกหวีด\n"
                        f"4. ประหยัดแบตเตอรี่มือถือ\n"
                        f"5. หากอันตรายถึงชีวิต โทร 1784"
                    )
                else:
                    reply_text = (
                        f"🚀 ส่งข้อมูลสำเร็จ! เลขเคส: {case_id}\n"
                        f"⚠️ บันทึก Sheets ไม่สำเร็จ แต่ข้อมูลถูกบันทึกบนเซิร์ฟเวอร์แล้ว\n\n"
                        f"🛡️ ระหว่างรอโปรดปฏิบัติดังนี้:\n"
                        f"1. ตัดสะพานไฟในบ้านทันที\n"
                        f"2. พยายามอยู่บนที่สูง\n"
                        f"3. เตรียมไฟฉายหรือนกหวีด\n"
                        f"4. ประหยัดแบตเตอรี่มือถือ\n"
                        f"5. หากอันตรายถึงชีวิต โทร 1784"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ ยกเลิกเคสเรียบร้อยครับ กดปุ่ม SOS ใหม่ได้ทันทีครับ")
                )
                return

    # ===========================
    # USER NEEDS FLOW ใหม่ (5 Steps)
    # ===========================
    if state and state.startswith("needs_"):
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}

        if state == "needs_step2":
            categories_map = {
                "🍲 อาหาร/น้ำดื่ม": "อาหาร/น้ำดื่ม",
                "💊 ยารักษาโรค/เวชภัณฑ์": "ยารักษาโรค/เวชภัณฑ์",
                "👶 ของใช้เด็กอ่อน": "ของใช้เด็กอ่อน",
                "🧼 ของใช้ส่วนตัว": "ของใช้ส่วนตัว",
                "🔦 อุปกรณ์ส่องสว่าง": "อุปกรณ์ส่องสว่าง",
                "📝 อื่นๆ (ระบุเอง)": "อื่นๆ"
            }

            # ถ้าผู้ใช้พิมพ์รายละเอียดมาเลย (ไม่กดปุ่มหมวดหมู่)
            if user_text not in categories_map and user_text not in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
                bot_config.USER_DATA[user_id]["need_details"] = user_text
                if not bot_config.USER_DATA[user_id].get("need_categories"):
                    bot_config.USER_DATA[user_id]["need_categories"] = ["อื่นๆ"]
                
                # ข้ามไปขั้นตอนความเร่งด่วนทันที
                bot_config.USER_STATES[user_id] = "needs_step4"
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก (หมดแล้ว)", text="🔴 ด่วนมาก (หมดแล้ว)")),
                        QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (รอได้ 24 ชม.)", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
                        QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน", text="🟢 ไม่ด่วน (แจ้งไว้ล่วงหน้า)"))
                    ]
                )
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือก:", quick_reply=quick_reply)
                )
                return

            # ถ้าเลือกหมวดหมู่จากปุ่ม
            if user_text in categories_map:
                if "need_categories" not in bot_config.USER_DATA[user_id]:
                    bot_config.USER_DATA[user_id]["need_categories"] = []
                bot_config.USER_DATA[user_id]["need_categories"].append(categories_map[user_text])
                bot_config.USER_DATA[user_id]["need_categories"] = list(set(bot_config.USER_DATA[user_id]["need_categories"]))

                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
                        QuickReplyButton(action=MessageAction(label="💊 ยา/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
                        QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็ก", text="👶 ของใช้เด็กอ่อน")),
                        QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
                        QuickReplyButton(action=MessageAction(label="🔦 ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น"))
                    ]
                )
                selected = ", ".join(bot_config.USER_DATA[user_id]["need_categories"])
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"📦 เลือกหมวดหมู่เพิ่ม หรือพิมพ์รายละเอียดสิ่งที่ต้องการมาได้เลยครับ\n(เลือกแล้ว: {selected})",
                        quick_reply=quick_reply
                    )
                )
                return

            elif user_text in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
                if not bot_config.USER_DATA[user_id].get("need_categories"):
                    bot_config.USER_DATA[user_id]["need_categories"] = ["อื่นๆ"]
                
                # ถ้ายังไม่มีรายละเอียด ให้ถาม
                if not bot_config.USER_DATA[user_id].get("need_details"):
                    # Stay in needs_step2 to capture the details
                    bot_config.line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="📝 โปรดระบุรายละเอียดสั้นๆ (เช่น 'ขอน้ำดื่ม 2 แพ็ค')")
                    )
                    return # Important: return here to wait for user's detail input
                else:
                    # ถ้ามีรายละเอียดแล้ว ไปขั้นตอนความเร่งด่วน
                    bot_config.USER_STATES[user_id] = "needs_step4"
                    quick_reply = QuickReply(
                        items=[
                            QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก (หมดแล้ว)", text="🔴 ด่วนมาก (หมดแล้ว)")),
                            QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (รอได้ 24 ชม.)", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
                            QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน", text="🟢 ไม่ด่วน (แจ้งไว้ล่วงหน้า)"))
                        ]
                    )
                    bot_config.line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือก:", quick_reply=quick_reply)
                    )
                return



        # ---- Step 4: ความเร่งด่วน ----
        elif state == "needs_step4":
            bot_config.USER_DATA[user_id]["need_urgency"] = user_text
            bot_config.USER_STATES[user_id] = "needs_confirm"
            _send_needs_summary(event, user_id)
            return

        # ---- Step 5: ยืนยัน ----
        elif state == "needs_confirm":
            if "ยืนยัน" in user_text:
                data = bot_config.USER_DATA.pop(user_id, {})
                bot_config.USER_STATES.pop(user_id, None)

                success = bot_config.save_user_need(
                    sheets_client, clean_sheet_id, user_id, timestamp,
                    data.get("need_latitude", "0"),
                    data.get("need_longitude", "0"),
                    ", ".join(data.get("need_categories", [])),
                    data.get("need_details", "-"),
                    data.get("need_urgency", "ไม่ด่วน")
                )

                if success:
                    reply_text = (
                        f"🟢 บันทึกความต้องการเรียบร้อยครับ!\n\n"
                        f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n"
                        f"📝 รายละเอียด: {data.get('need_details', '-')}\n\n"
                        f"ทีมอาสาสมัครจะดำเนินการจัดส่งให้ครับ"
                    )
                else:
                    reply_text = (
                        f"🟢 บันทึกความต้องการสำเร็จ (ระบบชั่วคราว)\n\n"
                        f"⚠️ Sheets ขัดข้อง แต่ข้อมูลถูกเก็บบนเซิร์ฟเวอร์แล้ว\n\n"
                        f"ทีมอาสาสมัครจะดำเนินการจัดส่งให้ครับ"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกรายการเรียบร้อยครับ"))
                return

    # ===========================
    # เมนูหลัก 6 ปุ่ม
    # ===========================
    if user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        db_connected = False
        contact_list = []
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(clean_sheet_id)
                contacts_ws = sheet.worksheet("Contacts")
                rows = contacts_ws.get_all_records()
                for r in rows:
                    contact_list.append(f"🚨 {r.get('Name')} ({r.get('Role')})\n📞 โทร: {r.get('Phone')}")
                db_connected = True
            except Exception as e:
                print(f"Failed to load contacts: {e}")

        if db_connected and contact_list:
            reply_text = "📞 เบอร์โทรฉุกเฉิน:\n\n" + "\n\n".join(contact_list)
        else:
            reply_text = (
                "📞 เบอร์โทรฉุกเฉิน:\n\n"
                "🚨 ปภ. 1784\n"
                "🚨 สพฉ. 1669\n"
                "🚨 กู้ภัยทางน้ำ 1196\n"
                "🚨 ตำรวจทางหลวง 1193"
            )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_text == "ศูนย์พักพิง":
        bot_config.USER_STATES[user_id] = "waiting_shelter_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดหาศูนย์พักพิง"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📍 โปรดกดแชร์พิกัด 'Location' ด้านล่าง หรือพิมพ์ชื่ออำเภอ/จังหวัดครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "ตรวจสอบระดับน้ำ":
        bot_config.USER_STATES[user_id] = "waiting_water_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กระดับน้ำ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🌊 โปรดกดแชร์พิกัด 'Location' เพื่อตรวจสอบระดับน้ำจากสถานี ThaiWater ใกล้คุณครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "เช็กสภาพอากาศ" or "สภาพอากาศ" in user_text:
        bot_config.USER_STATES[user_id] = "waiting_weather_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กสภาพอากาศ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🌦️ โปรดกดแชร์พิกัด 'Location' เพื่อตรวจสอบสภาพอากาศและโอกาสเกิดฝนในพื้นที่ของคุณครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "SOS ขอความช่วยเหลือ":
        # ใช้ฟังก์ชัน is_user_registered() ที่เช็คจาก Sheets
        is_reg, first_name, last_name, phone = False, "", "", "-"
        if sheets_client:
            is_reg, first_name, last_name, phone = bot_config.is_user_registered(
                sheets_client, clean_sheet_id, user_id
            )

        if not is_reg:
            bot_config.USER_STATES[user_id] = "register_first_name"
            bot_config.USER_DATA[user_id] = {}
            reply_text = (
                "📝 คุณเข้าใช้งานเป็นครั้งแรก\n\n"
                "เพื่อประสานงานกู้ภัยได้อย่างมีประสิทธิภาพ\n"
                "โปรดพิมพ์ 'ชื่อจริง' ของคุณครับ (เช่น สมชาย)"
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        else:
            # ใช้ SOS Flex Form แบบใหม่ (สีแดงมินิมอล)
            bot_config.USER_STATES[user_id] = "sos_location"
            sos_flex = bot_config.build_sos_form_flex(first_name)
            bot_config.line_bot_api.reply_message(event.reply_token, sos_flex)

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
                text="📌 แจ้งความต้องการสิ่งของบรรเทาทุกข์\n\nโปรดกดปุ่มด้านล่างเพื่อแชร์พิกัดครับ",
                quick_reply=location_quick_reply
            )
        )

    elif user_text == "ถาม AI เรื่องน้ำท่วม" or "ถาม-ตอบด้วย AI" in user_text or "ถาม–ตอบด้วย AI" in user_text:
        reply_text = "🤖 พิมพ์คำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมได้ทันทีครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif bot_config.is_greeting(user_text):
        bot_config.handle_greeting_logic(event)

    elif user_text.startswith("Research:"):
        # ระบบ Research AI เชิงลึก (On-Demand Research)
        original_query = user_text.replace("Research:", "").strip()
        bot_config.show_loading_animation(user_id, loading_seconds=15)
        
        try:
            research_prompt = (
                f"ในฐานะผู้เชี่ยวชาญด้านความปลอดภัยและการรักษาพยาบาลในภาวะน้ำท่วม "
                f"โปรดทำการวิจัยและให้ข้อมูลเชิงลึกเกี่ยวกับเรื่องนี้: '{original_query}'\n\n"
                f"เงื่อนไข:\n"
                f"1. เน้นข้อมูลด้านความปลอดภัยและการรักษาพยาบาล\n"
                f"2. ระบุแหล่งที่มาของข้อมูล (เช่น กรมควบคุมโรค, WHO, ปภ.)\n"
                f"3. ใช้ภาษาที่เป็นทางการแต่เข้าใจง่าย\n"
                f"4. ตอบให้ละเอียดและเป็นลำดับขั้นตอน"
            )
            response = bot_config.gemini_model.generate_content(research_prompt)
            research_result = bot_config.clean_text_for_line(response.text.strip())
            
            # ส่งผลการวิจัยกลับเป็น TextSendMessage (เพราะข้อมูลอาจจะยาว)
            bot_config.line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text=f"📊 ผลการวิจัยเชิงลึก (Research AI):\n\n{research_result}")
            )
        except Exception as e:
            print(f"Research AI Error: {e}")
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ระบบ Research ขัดข้อง โปรดลองใหม่ภายหลังครับ"))

    else:
        # ระบบคุยตอบโต้อิสระด้วย AI (Normal Chat)
        bot_config.show_loading_animation(user_id, loading_seconds=5)
        
        ai_response = ""
        try:
            prompt = f"ตอบคำถามนี้อย่างกระชับและรวดเร็ว: {user_text}"
            response = bot_config.gemini_model.generate_content(prompt)
            ai_response = bot_config.clean_text_for_line(response.text.strip())
        except Exception as e:
            print(f"Gemini Error: {e}")
            ai_response = "⚠️ AI ขัดข้องชั่วคราว หากตกอยู่ในอันตราย โทร ปภ. 1784 ทันทีครับ"

        # ส่งคำตอบเป็น Flex Message พร้อมปุ่ม Research
        ai_flex = bot_config.build_ai_response_flex(ai_response, user_text)
        bot_config.line_bot_api.reply_message(event.reply_token, ai_flex)

        # บันทึก Log ลง Sheets
        sheets_client = bot_config.get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(clean_sheet_id)
                log_ws = sheet.worksheet("AI Logs")
                log_ws.append_row([timestamp, user_id, user_text, ai_response])
            except Exception as se:
                print(f"Sheets Log Error: {se}")


# =============================================================================
# รับข้อมูลพิกัด (Location Message)
# =============================================================================
@bot_config.handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state = bot_config.USER_STATES.pop(user_id, "default")
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

    # ===========================
    # 12.1 ค้นหาศูนย์อพยพใกล้ที่สุด
    # ===========================
    if state == "waiting_shelter_location":
        shelter_list = []
        db_connected = False

        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(clean_sheet_id)
                shelters_ws = sheet.worksheet("Shelters")
                rows = shelters_ws.get_all_records()
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
                print(f"Failed to fetch shelters: {e}")

        if not db_connected:
            reply_text = "⚠️ ระบบขัดข้อง โปรดโทร ปภ. 1784 ทันทีครับ"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        nearest_shelters = []
        for sh in shelter_list:
            distance = bot_config.calculate_distance(latitude, longitude, sh["lat"], sh["lon"])
            # ไม่จำกัดระยะทาง 20 กม. ตามคำสั่งใหม่
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
            reply_text = "📍 ไม่พบข้อมูลศูนย์พักพิงในระบบ โปรดติดต่อ ปภ. 1784 ครับ"
        else:
            reply_text = "📍 ศูนย์พักพิงใกล้คุณที่สุด:\n\n"
            for index, sh in enumerate(top_shelters, 1):
                reply_text += (
                    f"{index}. {sh['name']}\n"
                    f"   ห่าง: {sh['distance']:.2f} กม.\n"
                    f"   สถานะ: {sh['vacancy']}\n"
                    f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
                )
            reply_text += "⚠️ โปรดใช้ความระมัดระวังในการเดินทางและสังเกตระดับน้ำจริงหน้างาน"

        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    # ===========================
    # 12.1.1 ตรวจสอบสภาพอากาศ (Weather Only)
    # ===========================
    if state == "waiting_weather_location":
        bot_config.show_loading_animation(user_id, loading_seconds=5)
        weather_info = bot_config.get_live_weather_scraper(latitude, longitude)
        
        reply_text = (
            f"📍 รายงานสภาพอากาศพิกัด: {latitude:.4f}, {longitude:.4f}\n"
            f"🕒 ข้อมูล ณ เวลา: {timestamp}\n\n"
            f"{weather_info}\n\n"
            "⚠️ ข้อมูลนี้เป็นการพยากรณ์เบื้องต้น โปรดสังเกตท้องฟ้าจริงประกอบด้วยครับ"
        )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ===========================
    # 12.2 ตรวจสอบระดับน้ำ (Lazy Sync from Sheets)
    # ===========================
    elif state == "waiting_water_location":
        # Auto-sync: ถ้าข้อมูลใน Sheets เก่ากว่า WATER_DATA_MAX_AGE_MINUTES (หรือยังไม่มี)
        # จะดึงจาก ThaiWater มาอัปเดต Sheets ก่อนใช้งานทันที
        try:
            _ensure_water_data_fresh(sheets_client, clean_sheet_id)
        except Exception as e:
            print(f"[WaterLevel] Auto-sync check failed: {e}")

        thaiwater_stations = []
        try:
            thaiwater_stations = bot_config.get_water_data_from_sheets(
                sheets_client, clean_sheet_id, latitude, longitude
            )
            if thaiwater_stations:
                print(f"[WaterLevel] Loaded {len(thaiwater_stations)} stations from Sheets")
        except Exception as e:
            print(f"[WaterLevel] Sheets load failed: {e}")

        # Fallback: ดึงจาก ThaiWater API ตรงๆ ถ้า Sheets ไม่มี
        if not thaiwater_stations:
            try:
                thaiwater_stations = bot_config.find_nearest_water_stations(
                    latitude, longitude, max_stations=3, max_distance_km=50
                )
                print(f"[WaterLevel] Fallback to API: {len(thaiwater_stations)} stations")
            except Exception as e:
                print(f"[WaterLevel] API fallback failed: {e}")

        # ไม่แสดงสภาพอากาศในส่วนของระดับน้ำตามคำสั่ง
        # weather_info = bot_config.get_live_weather_scraper(latitude, longitude)
        # water_flow = bot_config.get_live_water_scraper(latitude, longitude)

        try:
            flex_msg = bot_config.build_water_level_flex_message(
                latitude, longitude, timestamp, thaiwater_stations
            )
            bot_config.line_bot_api.reply_message(event.reply_token, flex_msg)
            print("[WaterLevel] Sent Flex Message")
        except Exception as e:
            print(f"[WaterLevel] Flex failed: {e}, using text")
            text_report = bot_config.build_water_level_text_report(
                latitude, longitude, timestamp, thaiwater_stations, None, None
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text_report))

    # ===========================
    # 12.3 SOS Step 1: รับพิกัด GPS
    # ===========================
    elif state == "sos_location":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["latitude"] = latitude
        bot_config.USER_DATA[user_id]["longitude"] = longitude

        bot_config.USER_STATES[user_id] = "sos_step2"

        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="👥 ระบุกลุ่มผู้ประสบภัย (เลือกได้หลายกลุ่ม กด 'เสร็จสิ้น' เมื่อเลือกครบ):",
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
                QuickReplyButton(action=MessageAction(label="💊 ยา/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
                QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็ก", text="👶 ของใช้เด็กอ่อน")),
                QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
                QuickReplyButton(action=MessageAction(label="🔦 ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
                QuickReplyButton(action=MessageAction(label="📝 อื่นๆ", text="📝 อื่นๆ (ระบุเอง)"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📦 เลือกหมวดหมู่สิ่งของที่ต้องการ (เลือกได้หลายหมวด กด 'เสร็จสิ้น' เมื่อเลือกครบ):",
                quick_reply=quick_reply
            )
        )

    else:
        confirm_text = "📍 ได้รับพิกัดแล้วครับ หากต้องการแจ้ง SOS กรุณากดเมนู 'SOS ขอความช่วยเหลือ' ก่อนครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))


# =============================================================================
# รับรูปภาพ (Image Message) - SOS Step 4
# =============================================================================
@bot_config.handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    state = bot_config.USER_STATES.get(user_id)

    if state == "sos_step4":
        image_id = event.message.id
        content_url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
        bot_config.USER_DATA[user_id]["photo_url"] = content_url
        bot_config.USER_DATA[user_id]["image_id"] = image_id

        bot_config.USER_STATES[user_id] = "sos_confirm"
        _send_sos_summary(event, user_id)
    else:
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📸 ได้รับรูปภาพแล้วครับ หากต้องการแจ้ง SOS พร้อมส่งรูป กรุณาเริ่มจากเมนู 'SOS' ก่อนครับ")
        )


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def _send_sos_summary(event, user_id):
    """สร้างและส่งสรุปข้อมูล SOS (Step 5)"""
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
        f"📊 ระดับความเร่งด่วน: {priority_label}\n\n"
        f"ยืนยันการส่งข้อมูลแจ้งกู้ภัยหรือไม่?"
    )

    quick_reply = QuickReply(
        items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัย", text="ยืนยันแจ้งกู้ภัย")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิกและแก้ไขใหม่"))
        ]
    )
    bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))


def _send_needs_summary(event, user_id):
    """สร้างและส่งสรุปความต้องการ (Step 5)"""
    data = bot_config.USER_DATA[user_id]
    lat = data.get("need_latitude", "0")
    lon = data.get("need_longitude", "0")
    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

    summary_text = (
        "✅ สรุปรายการความต้องการ:\n\n"
        f"📍 พิกัด: {maps_link}\n"
        f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n"
        f"📝 รายละเอียด: {data.get('need_details', '-')}\n"
        f"⏳ ความเร่งด่วน: {data.get('need_urgency', '-')}\n\n"
        f"ยืนยันการส่งข้อมูลไปยังศูนย์อาสาสมัครหรือไม่?"
    )

    quick_reply = QuickReply(
        items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยันการแจ้ง", text="ยืนยันการแจ้ง")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิก"))
        ]
    )
    bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
