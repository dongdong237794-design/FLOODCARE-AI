import os
import datetime
from flask import Flask, request, abort
import bot_config
from dashboard import dashboard_bp

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage,
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

    # ดึงระดับสถานะการคุยปัจจุบัน
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
                QuickReplyButton(action=LocationAction(label="กดส่งพิกัดตำแหน่งแจ้งเหตุ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🚨 ระบบกำลังรอตำแหน่งพิกัดของคุณอยู่ครับ โปรดกดปุ่มสีเขียว 'กดส่งพิกัดตำแหน่งแจ้งเหตุ' ด้านล่างเพื่อส่งข้อมูลความละเอียดด่วน หรือพิมพ์คำว่า 'ยกเลิก' เพื่อเริ่มต้นใหม่ครับ",
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
    # 11.4 ระบบ SOS Steps 2-8
    # ===========================
    if state:
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}

        if state == "sos_q2":
            cleaned_count = bot_config.extract_number(user_text)
            bot_config.USER_DATA[user_id]["people_count"] = cleaned_count
            bot_config.USER_STATES[user_id] = "sos_q3"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="มี (YES)", text="YES")),
                    QuickReplyButton(action=MessageAction(label="ไม่มี (NO)", text="NO"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 Step 3: ในบ้านมีเด็กเล็ก (อายุต่ำกว่า 12 ปี) หรือไม่ครับ?", quick_reply=quick_reply))
            return

        elif state == "sos_q3":
            val = bot_config.parse_yes_no(user_text)
            bot_config.USER_DATA[user_id]["children"] = val
            bot_config.USER_STATES[user_id] = "sos_q4"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="มี (YES)", text="YES")),
                    QuickReplyButton(action=MessageAction(label="ไม่มี (NO)", text="NO"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 Step 4: ในบ้านมีผู้สูงอายุหรือไม่ครับ?", quick_reply=quick_reply))
            return

        elif state == "sos_q4":
            val = bot_config.parse_yes_no(user_text)
            bot_config.USER_DATA[user_id]["elderly"] = val
            bot_config.USER_STATES[user_id] = "sos_q5"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="มี (YES)", text="YES")),
                    QuickReplyButton(action=MessageAction(label="ไม่มี (NO)", text="NO"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 Step 5: ในบ้านมีผู้ป่วยติดเตียงหรือไม่ครับ?", quick_reply=quick_reply))
            return

        elif state == "sos_q5":
            val = bot_config.parse_yes_no(user_text)
            bot_config.USER_DATA[user_id]["bedridden"] = val
            bot_config.USER_STATES[user_id] = "sos_q6"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="มี (YES)", text="YES")),
                    QuickReplyButton(action=MessageAction(label="ไม่มี (NO)", text="NO"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 Step 6: มีสัตว์เลี้ยงที่ต้องอพยพร่วมด้วยหรือไม่ครับ?", quick_reply=quick_reply))
            return

        elif state == "sos_q6":
            val = bot_config.parse_yes_no(user_text)
            bot_config.USER_DATA[user_id]["pets"] = val
            bot_config.USER_STATES[user_id] = "sos_q7"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="ต่ำกว่า 30 ซม.", text="ต่ำกว่า 30 ซม.")),
                    QuickReplyButton(action=MessageAction(label="30-50 ซม.", text="30-50 ซม.")),
                    QuickReplyButton(action=MessageAction(label="50 ซม. - 1 เมตร", text="50 ซม. - 1 เมตร")),
                    QuickReplyButton(action=MessageAction(label="สูงกว่า 1 เมตร", text="สูงกว่า 1 เมตร"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 Step 7: ระดับน้ำท่วมบ้านในปัจจุบันโดยประมาณครับ?", quick_reply=quick_reply))
            return

        elif state == "sos_q7":
            bot_config.USER_DATA[user_id]["water_level"] = user_text
            bot_config.USER_STATES[user_id] = "sos_q8"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📌 Step 8: โปรดระบุข้อมูลเพิ่มเติมเฉพาะหน้าเพื่อแจ้งกู้ภัยครับ (เช่น น้ำท่วมมิดชั้นหนึ่ง, ไฟฟ้าดับ, ขาดแคลนอาหารหนัก หรือไม่ระบุ)"))
            return

        elif state == "sos_q8":
            bot_config.USER_DATA[user_id]["note"] = user_text

            data = bot_config.USER_DATA[user_id]
            bedridden = data.get("bedridden", "NO")
            water_level = data.get("water_level", "")
            elderly = data.get("elderly", "NO")
            children = data.get("children", "NO")

            is_critical_water = "สูงกว่า 1 เมตร" in water_level or "1 เมตร" in water_level

            if bedridden == "YES" or is_critical_water or (bedridden == "YES" and elderly == "YES") or (bedridden == "YES" and children == "YES"):
                priority = "🔴  CRITICAL (เร่งด่วนวิกฤตสูงสุด)"
            elif elderly == "YES" or children == "YES" or "50 ซม." in water_level:
                priority = "🟠  HIGH (ความเสี่ยงสูง)"
            else:
                priority = "🟢  NORMAL (สถานการณ์ปกติ)"

            bot_config.USER_DATA[user_id]["priority"] = priority
            bot_config.USER_STATES[user_id] = "sos_confirm"

            first_name = bot_config.USER_DATA[user_id].get("first_name", "ผู้แจ้ง")
            last_name = bot_config.USER_DATA[user_id].get("last_name", "ทั่วไป")
            phone = bot_config.USER_DATA[user_id].get("phone", "-")

            if sheets_client:
                try:
                    sheet = sheets_client.open_by_key(bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID))
                    users_ws = sheet.worksheet("users")
                    rows = users_ws.get_all_records()
                    for r in rows:
                        if str(r.get("user_id")) == user_id:
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

            summary_text = (
                "🚨 สรุปคำขอรับการช่วยเหลือ SOS 🚨\n\n"
                f"👤 ชื่อ-นามสกุล: {first_name} {last_name}\n"
                f"📞 เบอร์โทรศัพท์: {phone}\n"
                f"📍 พิกัดแจ้งเหตุ: {data.get('latitude', '0')}, {data.get('longitude', '0')}\n"
                f"👥 สมาชิกติดในบ้าน: {data.get('people_count', '1')} คน\n"
                f"👶 เด็กเล็ก: {data.get('children', 'NO')}\n"
                f"🧓 ผู้สูงอายุ: {data.get('elderly', 'NO')}\n"
                f"🏥 ผู้ป่วยติดเตียง: {data.get('bedridden', 'NO')}\n"
                f"🐶 สัตว์เลี้ยง: {data.get('pets', 'NO')}\n"
                f"🌊 ระดับน้ำโดยประมาณ: {data.get('water_level', '-')}\n"
                f"📝 รายละเอียดอื่น ๆ: {data.get('note', '-')}\n\n"
                f"📊 ประเมินความเร็วช่วยเหลือ: {priority}\n\n"
                "ต้องการส่งข้อมูลเพื่อยืนยันแจ้งกู้ภัยหรือไม่ครับ? (กรุณากดเลือกปุ่มด้านล่าง)"
            )

            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="ยืนยันการส่งข้อมูล", text="ยืนยันการส่งข้อมูล")),
                    QuickReplyButton(action=MessageAction(label="ยกเลิกและแก้ไขใหม่", text="ยกเลิกและแก้ไขใหม่"))
                ]
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))
            return

        elif state == "sos_confirm":
            if "ยืนยัน" in user_text:
                data = bot_config.USER_DATA.pop(user_id, {})
                bot_config.USER_STATES.pop(user_id, None)

                today_str = datetime.datetime.now().strftime("%Y%m%d")
                random_suffix = datetime.datetime.now().strftime("%f")[:4]
                case_id = f"SOS-{today_str}-{random_suffix}"

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
                            data.get("people_count", "1"),
                            data.get("children", "NO"),
                            data.get("elderly", "NO"),
                            data.get("bedridden", "NO"),
                            data.get("pets", "NO"),
                            data.get("water_level", "-"),
                            data.get("note", "-"),
                            data.get("priority", "🟢  NORMAL (สถานการณ์ปกติ)"),
                            "OPEN"
                        ])
                        success = True
                    except Exception as e:
                        print(f"Failed to save SOS request: {e}")

                if success:
                    reply_text = (
                        "🎉 ยืนยันบันทึกข้อมูลเข้ารหัสกู้ภัยออนไลน์เรียบร้อยแล้วครับ!\n\n"
                        f"🎫 เลขที่อ้างอิงเคส (Case ID): {case_id}\n"
                        f"📊 ความเร็วช่วยเหลือ: {data.get('priority', '-')}\n\n"
                        "ข้อมูลนี้ถูกส่งเข้าระบบของทีมกู้ภัยสำเร็จแล้ว แอดมินสามารถเปิดตรวจพิกัดเพื่อเข้าจัดส่งเรือกู้ชีพไปช่วยเหลือคุณทันที โปรดรอคอยในพิกัดที่ปลอดภัยที่สุดนะครับ"
                    )
                else:
                    reply_text = (
                        f"🎉 บันทึกสัญญาณ SOS จำลองสำเร็จแล้วครับ!\n🎫 เลขเคสอ้างอิง: {case_id}\n\n"
                        "*(หมายเหตุ: ระบบยังไม่สามารถเขียนลงแผ่นงาน Google Sheets ได้เนื่องจากรหัสสิทธิ์สเปรดชีตขัดข้อง แต่พิกัดของคุณยืนยันบนระบบบอตแล้วครับ)"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกเคสเดิมและล้างข้อมูลเรียบร้อยแล้วครับ คุณสามารถกดปุ่มเริ่ม SOS ใหม่อีกครั้งได้ทันทีครับ"))
                return

        # ===========================
        # 11.5 สัญญานเมนูอื่นๆ
        # ===========================
        elif state == "waiting_emergency_type":
            bot_config.USER_STATES.pop(user_id, None)
            prompt = f"ผู้ประสบภัยต้องการติดต่อขอกู้ภัยด้วยเรื่องเฉพาะหน้าคือ: '{user_text}' โปรดระบุเบอร์โทรฉุกเฉินและประสานงานกู้ภัยอย่างสั้น กระชับและสุภาพ"
            try:
                res = bot_config.gemini_model.generate_content(prompt)
                reply = bot_config.clean_text_for_line(res.text.strip())
            except:
                reply = "🚨 แนะนำโทรประสานงานเร่งด่วนที่สายด่วนกู้ชีพ 1669 หรือ สายด่วน ปภ. 1784 ครับ"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        elif state == "waiting_first_aid_detail":
            bot_config.USER_STATES.pop(user_id, None)
            prompt = f"ผู้ใช้ต้องการคำแนะนำวิธีการอพยพจากสถานการณ์อุทกภัย: '{user_text}' ในฐานะ FLOODCARE AI โปรดแนะนำขั้นตอนการหนีภัยและเตรียมตัวอพยพเฉพาะหน้าที่สั้น กระชับ เป็นขั้นเป็นตอน (1, 2, 3) เน้นความปลอดภัย และความมีสติ หลีกเลี่ยงข้อความที่ยาวและเครื่องหมายดอกจัน"
            try:
                res = bot_config.gemini_model.generate_content(prompt)
                reply = bot_config.clean_text_for_line(res.text.strip())
            except:
                reply = "🏃 ปลอดภัยไว้ก่อนนะครับ! แนะนำให้มีสติ สวมเสื้อชูชีพหรือเตรียมอุปกรณ์ลอยตัว ตัดกระแสไฟในบ้าน และอพยพขึ้นพิกัดที่สูงตามการนำของเจ้าหน้าที่ครับ"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        elif state == "waiting_water_location":
            # ผู้ใช้พิมพ์ชื่อสถานที่แทนการแชร์ GPS
            bot_config.USER_STATES.pop(user_id, None)
            prompt = f"ผู้ใช้ต้องการประเมินสถานการณ์น้ำหรือเช็กข้อมูลน้ำท่วมในพื้นที่: '{user_text}' โปรดแนะนำแนวทางเฝ้าระวังภัยพิบัติอย่างสั้นและกระชับ"
            try:
                res = bot_config.gemini_model.generate_content(prompt)
                reply = bot_config.clean_text_for_line(res.text.strip())
            except:
                reply = "🌊 แนะนำติดตามการรายงานระดับน้ำอย่างใกล้ชิด และสามารถเช็กระดับลุ่มน้ำได้ผ่านแอปฯ ThaiWater ครับ"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # ===========================
        # 11.5.1 ฟีเจอร์ค้นหาศูนย์อพยพ
        # ===========================
        elif state == "waiting_shelter_location":
            bot_config.USER_STATES.pop(user_id, None)
            shelter_list = []
            db_connected = False

            clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
            if sheets_client:
                try:
                    sheet = sheets_client.open_by_key(clean_sheet_id)
                    shelters_worksheet = sheet.worksheet("Shelters")
                    rows = shelters_worksheet.get_all_records()
                    for row in rows:
                        sh_name = str(row.get('Name', '')).strip()
                        sh_province = str(row.get('Province', '')).strip()
                        sh_district = str(row.get('District', '')).strip()

                        if user_text in sh_name or user_text in sh_province or user_text in sh_district:
                            vacancy_status = bot_config.check_shelter_vacancy(row.get('Capacity', 100), row.get('Occupancy', 0))
                            shelter_list.append({
                                "name": sh_name,
                                "province": sh_province,
                                "district": sh_district,
                                "vacancy": vacancy_status,
                                "contact": row.get('Contact', '-'),
                                "lat": row.get('Latitude', 0),
                                "lon": row.get('Longitude', 0)
                            })
                    db_connected = True
                except Exception as e:
                    print(f"Failed to query database: {e}")

            if not db_connected:
                reply_text = "⚠️ ขออภัยครับ ระบบตรวจสอบสิทธิ์ฐานข้อมูล Google Sheets ขัดข้องชั่วคราว โปรดตรวจเช็กคีย์สิทธิ์บน Render หรือลองใหม่อีกครั้งครับ"
            elif not shelter_list:
                reply_text = f"📍 ไม่พบข้อมูลศูนย์พักพิงจริงในพื้นที่ชื่อ '{user_text}' เลยครับ โปรดตรวจสอบการสะกดชื่ออำเภอ/จังหวัด แล้วลองพิมพ์ใหม่อีกครั้งนะครับ"
            else:
                reply_text = f"🏠 รายชื่อศูนย์พักพิงจริงในพื้นที่ '{user_text}' ที่เราพบล่าสุดในระบบฐานข้อมูลครับ:\n\n"
                for index, sh in enumerate(shelter_list, 1):
                    reply_text += (
                        f"{index}️⃣ {sh['name']}\n"
                        f"   📌 ที่ตั้ง: อ.{sh['district']} จ.{sh['province']}\n"
                        f"   📌 สถานะความจุ: {sh['vacancy']}\n"
                        f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
                    )
                reply_text += "⚠️ โปรดโทรตรวจสอบความจุกับทางศูนย์อพยพก่อนออกเดินทาง หรือเดินทางด้วยความระมัดระวังสูงสุดนะครับ"

            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # ===========================
        # 11.5.2 ฟีเจอร์แจ้งความต้องการเพิ่มเติม
        # ===========================
        elif state == "waiting_needs_form":
            bot_config.USER_STATES.pop(user_id, None)
            clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
            success = False
            if sheets_client:
                try:
                    sheet = sheets_client.open_by_key(clean_sheet_id)
                    needs_ws = sheet.worksheet("user_needs")
                    needs_ws.append_row([timestamp, user_id, user_text, "PENDING"])
                    success = True
                except Exception as e:
                    print(f"Failed to save needs: {e}")

            if success:
                reply_text = (
                    "🟢 ระบบทำการบันทึกความต้องการเพิ่มเติมของคุณเรียบร้อยแล้วครับ!\n\n"
                    f"📝 สิ่งที่แจ้งความประสงค์: {user_text}\n\n"
                    "ข้อมูลนี้จะถูกส่งเข้ารายงานกลางเพื่อให้ทีมอาสาสมัครจัดเตรียมสิ่งของ ยา อาหาร หรือเวชภัณฑ์นำไปกระจายความช่วยเหลือแก่ท่านในพื้นที่ต่อไปครับ"
                )
            else:
                reply_text = (
                    f"🟢 บันทึกความต้องการจำลองของคุณสำเร็จแล้วครับ!\n📝 ความประสงค์: {user_text}\n\n"
                    "*(หมายเหตุ: ระบบยังไม่สามารถเขียนลงแผ่นงาน Google Sheets ได้เนื่องจากสเปรดชีตขัดข้องสิทธิ์เข้าถึง)"
                )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

    # ===========================
    # 11.6 ตรวจสอบการคลิกปุ่มหลักบนเมนู 6 ปุ่ม
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
                text="🌊 โปรดกดปุ่มแชร์พิกัด 'Location' ด้านล่าง เพื่อให้ระบบค้นหาและรายงานสถานการณ์ระดับน้ำและระดับความรุนแรงจากสถานีตรวจวัด ThaiWater ที่ใกล้ตัวคุณที่สุดครับ",
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
                    QuickReplyButton(action=LocationAction(label="กดส่งพิกัดตำแหน่งแจ้งเหตุ"))
                ]
            )
            reply_text = (
                f"🚨 สวัสดีครับคุณ {first_name}! ระบบพบข้อมูลการลงทะเบียนของคุณแล้วครับ "
                "โปรดกดปุ่มแชร์พิกัด 'Location' สีเขียวด้านล่างนี้ เพื่อระบุตำแหน่งที่คุณต้องการให้ทีมกู้ภัยเข้าช่วยเหลือด่วนที่สุดทันทีเลยครับ"
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=location_quick_reply))
            return

        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif user_text == "แจ้งความต้องการเพิ่มเติม" or user_text == "ความต้องการ":
        bot_config.USER_STATES[user_id] = "waiting_needs_form"
        reply_text = (
            "📌 แจ้งแบบฟอร์มความต้องการพิเศษ:\n\n"
            "โปรดพิมพ์อธิบายสิ่งของ อาหาร ยารักษาโรค นมผงเด็ก หรือเวชภัณฑ์อื่นๆ "
            "ที่คุณต้องการได้รับความช่วยเหลือเพิ่มเติมเข้ามาได้ทันทีเลยครับ ระบบจะนำความประสงค์ส่งต่อให้อาสาสมัครกู้ชีพเข้าจัดการด่วนครับ"
        )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

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
                    if str(row.get('Status')).strip() == "ปิดทำการ":
                        continue
                    shelter_list.append({
                        "name": row.get('Name', 'ไม่ระบุชื่อ'),
                        "lat": float(row.get('Latitude', 0)),
                        "lon": float(row.get('Longitude', 0)),
                        "capacity": row.get('Capacity', 100),
                        "occupancy": row.get('Occupancy', 0),
                        "status": row.get('Status', 'ว่าง')
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
            distance = bot_config.calculate_distance(latitude, longitude, sh['lat'], sh['lon'])
            if distance <= 20.0:
                vacancy_status = bot_config.check_shelter_vacancy(sh['capacity'], sh['occupancy'])
                nearest_shelters.append({
                    "name": sh['name'],
                    "distance": distance,
                    "vacancy": vacancy_status,
                    "lat": sh['lat'],
                    "lon": sh['lon']
                })

        nearest_shelters.sort(key=lambda x: x['distance'])
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
    # 12.2 ตรวจสอบระดับน้ำจาก ThaiWater API (NEW - WITH FALLBACK CHAIN)
    # =============================================================================
    elif state == "waiting_water_location":
        # ---- Step 1: ดึงข้อมูลจาก ThaiWater API (Primary Source) ----
        thaiwater_stations = []
        thaiwater_success = False

        try:
            thaiwater_stations = bot_config.find_nearest_water_stations(
                latitude, longitude, max_stations=3, max_distance_km=50
            )
            if thaiwater_stations:
                thaiwater_success = True
                print(f"[WaterLevel] ThaiWater API success: {len(thaiwater_stations)} stations found")
        except Exception as e:
            print(f"[WaterLevel] ThaiWater API failed: {e}")

        # ---- Step 2: Fallback to Google Sheets ถ้า ThaiWater ล่ม ----
        if not thaiwater_success and sheets_client:
            try:
                clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
                thaiwater_stations = bot_config.get_water_data_from_sheets(
                    sheets_client, clean_sheet_id, latitude, longitude
                )
                if thaiwater_stations:
                    print(f"[WaterLevel] Fallback to Sheets: {len(thaiwater_stations)} stations")
            except Exception as e:
                print(f"[WaterLevel] Sheets fallback failed: {e}")

        # ---- Step 3: ดึงข้อมูลสภาพอากาศ (Open-Meteo) ----
        weather_info = bot_config.get_live_weather_scraper(latitude, longitude)

        # ---- Step 4: ดึงข้อมูลน้ำหลากประมาณการ (Open-Meteo Flood) ----
        water_flow = bot_config.get_live_water_scraper(latitude, longitude)

        # ---- Step 5: สร้างและส่งรายงาน ----
        try:
            # พยายามส่ง Flex Message ก่อน (สวยงามกว่า)
            flex_msg = bot_config.build_water_level_flex_message(
                latitude, longitude, timestamp, thaiwater_stations, weather_info, water_flow
            )
            bot_config.line_bot_api.reply_message(event.reply_token, flex_msg)
            print("[WaterLevel] Sent Flex Message successfully")
        except Exception as e:
            # ถ้า Flex Message ส่งไม่ได้ ให้ fallback เป็น Text Message
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

        bot_config.USER_STATES[user_id] = "sos_q2"

        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📌 Step 2: โปรดพิมพ์แจ้งจำนวนคนที่ประสบภัยที่ติดอยู่ร่วมกันในบ้านของคุณในตอนนี้ครับ? (กรุณาระบุจำนวนตัวเลข เช่น '3')")
        )

    else:
        confirm_text = "📍 คุณส่งพิกัด GPS มาหาผม หากต้องการแจ้งขอความช่วยเหลือ โปรดกดแตะเมนู 'SOS ขอความช่วยเหลือ' บนแถบด้านล่างก่อนเพื่อให้ทีมกู้ภัยวิเคราะห์ความเร่งด่วนได้อย่างแม่นยำนะครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
