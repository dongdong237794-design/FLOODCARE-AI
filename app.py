import os
import datetime
from flask import Flask, request, abort
import bot_config as cfg
from dashboard import dashboard_bp

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction, MessageAction
)

app = Flask(__name__)
app.register_blueprint(dashboard_bp)

# =============================================================================
# Webhook สำหรับไลน์
# =============================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        cfg.handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# =============================================================================
# ดักรับข้อความตัวอักษรและประมวลผลกระบวนการกู้ภัยฉุกเฉิน
# =============================================================================
@cfg.handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = cfg.USER_STATES.get(user_id)
    
    # 🚨 ตรวจจับความวิกฤตของสถานการณ์ (Emergency Triage)
    if any(k in user_text for k in ["ช่วยด้วย", "จมน้ำ", "จะตาย", "ติดอยู่", "ช่วยเหลือด่วน"]):
        cfg.USER_STATES.pop(user_id, None)
        cfg.USER_DATA.pop(user_id, None)
        msg = (
            "🚨 **ตรวจพบสัญญาณวิกฤตอันตรายถึงชีวิต!**\n"
            "โปรดตั้งสติและปฏิบัติตามคำแนะนำกู้ชีพเร่งด่วน:\n\n"
            "1. 🔌 **ตัดสะพานไฟ** และกระแสไฟในบ้านทันที\n"
            "2. 🧗 **ขึ้นที่สูง** หรือจุดที่ปลอดภัยที่สุด\n"
            "3. 📱 **เปิดระบบ SOS** เพื่อยืนยันพิกัดทางภูมิศาสตร์แก่กู้ภัย\n\n"
            "📞 ประสานสายด่วนภัยพิบัติ ปภ. โทร **1784** หรือ กู้ชีพแพทย์ โทร **1669** ทันทีครับ!"
        )
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ฟีเจอร์พิมพ์ "ยกเลิก" เพื่อเคลียร์สถานะระบบทั้งหมด
    if user_text == "ยกเลิก":
        cfg.USER_STATES.pop(user_id, None)
        cfg.USER_DATA.pop(user_id, None)
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนการทำงานปัจจุบันเรียบร้อยแล้ว คุณสามารถเลือกเมนูหลักใหม่ได้ทันทีครับ")
        )
        return

    # =============================================================================
    # จัดการกระบวนการลงทะเบียนสมาชิกครั้งแรก
    # =============================================================================
    if state == "register_first_name":
        cfg.USER_DATA[user_id] = {"temp_first_name": user_text}
        cfg.USER_STATES[user_id] = "register_last_name"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ขั้นตอนที่ 2: โปรดพิมพ์ระบุ 'นามสกุล' เพื่อยืนยันข้อมูลกับหน่วยกู้ภัยครับ"))
        return

    elif state == "register_last_name":
        cfg.USER_DATA[user_id]["temp_last_name"] = user_text
        cfg.USER_STATES[user_id] = "register_phone"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ขั้นตอนที่ 3: โปรดพิมพ์ระบุ 'เบอร์โทรศัพท์มือถือ' 10 หลักเพื่อรับสายยืนยันตัวตนครับ"))
        return

    elif state == "register_phone":
        clean_phone = cfg.extract_number(user_text)
        if len(clean_phone) < 9 or len(clean_phone) > 10:
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ เบอร์โทรศัพท์มือถือไม่ถูกต้อง! โปรดพิมพ์หมายเลขตัวเลข 9-10 หลักใหม่อีกครั้งครับ"))
            return

        first_name = cfg.USER_DATA[user_id].get("temp_first_name", "ผู้แจ้ง")
        last_name = cfg.USER_DATA[user_id].get("temp_last_name", "ทั่วไป")
        register_date = datetime.datetime.now().strftime("%Y-%m-%d")

        sheets_client = cfg.get_sheets_client()
        success = False
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID))
                users_ws = sheet.worksheet("users")
                users_ws.append_row([user_id, first_name, last_name, clean_phone, register_date, "ACTIVE"])
                success = True
            except Exception as e:
                print(f"Error registering user: {e}")

        cfg.USER_STATES.pop(user_id, None)
        cfg.USER_DATA.pop(user_id, None)

        if success:
            reply_text = (
                f"🎉 ยินดีต้อนรับครับ คุณ {first_name} {last_name}!\n"
                "ระบบได้บันทึกโปรไฟล์ผู้ประสบภัยเข้าสู่ระบบเรียบร้อยแล้ว\n\n"
                "🛡️ คุณสามารถเลือกปุ่มเมนูด้านล่าง เพื่อขอความช่วยเหลือระบบ SOS หรือตรวจสอบระดับน้ำได้ทันทีครับ"
            )
        else:
            reply_text = "⚠️ เกิดความขัดพลาดในการบันทึกข้อมูลเข้าฐานข้อมูลหลัก โปรดลองพิมพ์ลงทะเบียนใหม่อีกครั้งครับ"
            
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # =============================================================================
    # กระบวนการแจ้งเหตุ SOS (Steps 2-4 และยืนยัน)
    # =============================================================================
    if state == "sos_step2":
        cfg.USER_DATA[user_id]["group"] = user_text
        cfg.USER_STATES[user_id] = "sos_step3"
        qr = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="🔴 วิกฤต (มิดหัว/ติดบนหลังคา)", text="วิกฤต")),
            QuickReplyButton(action=MessageAction(label="🟠 สูง (ระดับเอวถึงหน้าอก)", text="ระดับสูง")),
            QuickReplyButton(action=MessageAction(label="🟢 ต่ำ (ระดับหน้าแข้ง)", text="ปานกลาง"))
        ])
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🌊 ระดับน้ำในพื้นที่ของคุณสูงระดับใดครับ?", quick_reply=qr))
        return

    elif state == "sos_step3":
        cfg.USER_DATA[user_id]["severity"] = user_text
        cfg.USER_STATES[user_id] = "sos_step4"
        cfg.line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(text="📸 **ส่งรูปถ่ายสภาพหน้างาน**\nโปรดส่งรูปถ่ายสถานการณ์จริงมา 1 รูป หรือพิมพ์คำว่า **'ข้าม'** หากไม่สะดวกส่งรูปครับ")
        )
        return

    elif state == "sos_step4":
        # กรณีผู้ใช้เลือกพิมพ์ "ข้าม"
        if "ข้าม" in user_text:
            cfg.USER_DATA[user_id]["image_url"] = "ไม่ได้แนบรูปถ่าย"
            # ส่งไปยังสรุปการทำรายการ SOS
            send_sos_summary(event, user_id)
        else:
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ โปรดส่งรูปถ่าย หรือพิมพ์คำว่า 'ข้าม' เพื่อทำขั้นตอนถัดไปครับ"))
        return

    elif state == "sos_confirm":
        if "ยืนยัน" in user_text:
            data = cfg.USER_DATA.pop(user_id, {})
            cfg.USER_STATES.pop(user_id, None)
            
            case_id = f"SOS-{user_id[:4]}-{datetime.datetime.now().strftime('%M%S')}"
            success = False
            sheets_client = cfg.get_sheets_client()
            if sheets_client:
                try:
                    sheet = sheets_client.open_by_key(cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID))
                    sos_ws = sheet.worksheet("sos_requests")
                    sos_ws.append_row([
                        case_id, user_id, timestamp, data.get("lat", 0), data.get("lon", 0),
                        data.get("group", "ไม่ระบุ"), data.get("severity", "ไม่ระบุ"),
                        data.get("image_url", "ไม่ได้แนบรูปถ่าย"), "OPEN"
                    ])
                    success = True
                except Exception as e:
                    print(f"Failed to record SOS request: {e}")

            if success:
                reply_text = (
                    f"🚀 **ส่งสัญญาณขอรับความช่วยเหลือสำเร็จ!**\n\n"
                    f"🎫 รหัสอ้างอิงเคส: `{case_id}`\n"
                    f"📊 สถานการณ์ประเมิน: {data.get('severity', '-')}\n\n"
                    "🚨 **คู่มือเอาตัวรอดทันที (โปรดทำตามด่วนที่สุด):**\n"
                    "1. 🔌 **ตัดสะพานไฟหลัก** ห้ามยืนในจุดที่มีปลั๊กไฟจมน้ำ\n"
                    "2. 🧗 **ขึ้นที่สูง** เตรียมอุปกรณ์ลอยน้ำหรือเสื้อชูชีพให้พร้อม\n"
                    "3. 🔦 **เตรียมอุปกรณ์นำทาง** เช่น ไฟฉาย หรือนกหวีดเพื่อส่งสัญญาณ\n"
                    "4. 📱 **ประหยัดแบตเตอรี่โทรศัพท์** ปิดการระบุตำแหน่งแอปอื่นนอกเหนือจากระบบ\n"
                    "5. 📞 หากสถานการณ์ถึงแก่ชีวิต โทรเบอร์ประสานงาน ปภ. **1784** ทันที"
                )
            else:
                reply_text = f"❌ ระบบจัดเก็บล้มเหลว โปรดประสานงานสายด่วนกู้ภัย ปภ. โทร 1784 ทันทีครับ"
            
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        else:
            cfg.USER_STATES.pop(user_id, None)
            cfg.USER_DATA.pop(user_id, None)
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกและล้างข้อมูลเรียบร้อย คุณสามารถกดเริ่มขอความช่วยเหลือ SOS ใหม่ได้ทุกเวลาครับ"))
            return

    # =============================================================================
    # กระบวนการคัดแยกหมวดหมู่ความต้องการบรรเทาทุกข์
    # =============================================================================
    if state == "needs_step2":
        cfg.USER_DATA[user_id]["category"] = user_text
        cfg.USER_STATES[user_id] = "needs_step3"
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 **โปรดระบุรายละเอียดสิ่งของ:**\nพิมพ์บอกจำนวน หรือระบุประเภทที่ต้องการ (เช่น นมผงเด็กสูตร 1 สำหรับเด็ก 6 เดือน หรือ ยาสามัญ 2 ชุด)")
        )
        return

    elif state == "needs_step3":
        details = user_text
        data = cfg.USER_DATA.pop(user_id, {})
        cfg.USER_STATES.pop(user_id, None)
        
        success = False
        sheets_client = cfg.get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID))
                needs_ws = sheet.worksheet("user_needs")
                needs_ws.append_row([
                    timestamp, user_id, data.get("lat", 0), data.get("lon", 0),
                    data.get("category", "ทั่วไป"), details, "PENDING"
                ])
                success = True
            except Exception as e:
                print(f"Failed to record user needs: {e}")

        if success:
            reply_text = (
                "🟢 **บันทึกข้อมูลเข้าระบบอาสาสมัครสำเร็จแล้ว!**\n\n"
                f"📦 หมวดหมู่: {data.get('category')}\n"
                f"📝 รายละเอียด: {details}\n\n"
                "ระบบนำพิกัดพิกัดใหม่ไปปักหมุดบนเว็บบัญชาการหลักแล้ว ทีมอาสาสมัครจะเตรียมของและเข้าจัดส่งโดยด่วนที่สุดครับ"
            )
        else:
            reply_text = "❌ บันทึกความต้องการล้มเหลวชั่วคราว โปรดทดลองจัดส่งใหม่อีกครั้งครับ"
            
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # =============================================================================
    # ระบบตรวจสอบเมนูการกด Rich Menu หลัก
    # =============================================================================
    if user_text == "ตรวจสอบระดับน้ำ":
        cfg.USER_STATES[user_id] = "waiting_water_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="แชร์พิกัดเช็กระดับน้ำ"))])
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🌊 โปรดกดปุ่มแชร์พิกัดด้านล่าง เพื่อวัดระดับน้ำเทียบพิกัดแบบเรียลไทม์ครับ", quick_reply=qr)
        )
        return

    elif user_text == "ศูนย์พักพิง":
        cfg.USER_STATES[user_id] = "waiting_shelter_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="หาศูนย์พักพิงใกล้ฉัน"))])
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🏠 โปรดแชร์พิกัด เพื่อประเมินศูนย์อพยพที่ใกล้ที่สุดที่ยังมีที่ว่างให้พักอาศัยครับ", quick_reply=qr)
        )
        return

    elif user_text == "SOS ขอความช่วยเหลือ":
        # ตรวจสอบการลงทะเบียน
        sheets_client = cfg.get_sheets_client()
        registered = False
        first_name, last_name, phone = "", "", ""
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID))
                users_ws = sheet.worksheet("users")
                rows = users_ws.get_all_records()
                for r in rows:
                    if str(r.get("user_id")) == user_id:
                        registered = True
                        first_name = r.get("first_name", "ผู้แจ้ง")
                        last_name = r.get("last_name", "")
                        phone = r.get("phone", "-")
                        break
            except Exception as e:
                print(f"Error checking registry: {e}")

        if not registered:
            cfg.USER_STATES[user_id] = "register_first_name"
            cfg.USER_DATA[user_id] = {}
            cfg.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="📝 ขออภัยด้วยครับ เนื่องจากคุณเพิ่งเข้าใช้งานระบบเป็นครั้งแรก เพื่อความปลอดภัยในการระบุตัวตนกับทีมกู้ภัย โปรดพิมพ์แจ้ง **'ชื่อจริง'** ของคุณเพื่อยืนยันประวัติครับ")
            )
        else:
            cfg.USER_STATES[user_id] = "sos_location"
            cfg.USER_DATA[user_id] = {"first_name": first_name, "last_name": last_name, "phone": phone}
            qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="กดส่งพิกัดแจ้งเหตุ SOS"))])
            cfg.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"🚨 ยินดีต้อนรับครับ คุณ {first_name}! โปรดกดแชร์พิกัดด้านล่างเพื่อระบุตำแหน่งวิกฤตที่ต้องการให้ช่วยเหลือด่วนที่สุดครับ", quick_reply=qr)
            )
        return

    elif user_text == "แจ้งความต้องการเพิ่มเติม" or user_text == "ความต้องการ":
        cfg.USER_STATES[user_id] = "needs_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="แชร์พิกัดรับของบรรเทาทุกข์"))])
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📦 บังคับแชร์พิกัดพิกัดใหม่ทุกครั้ง เพื่อให้อาสาสมัครนำของบริจาคส่งถึงมือของคุณอย่างแม่นยำ โปรดส่งพิกัดด้านล่างครับ", quick_reply=qr)
        )
        return

    # กรณีพูดคุยสนทนาทั่วไป ให้ AI ประมวลผลตอบกลับ
    try:
        response = cfg.gemini_model.generate_content(user_text)
        reply_msg = cfg.clean_text_for_line(response.text.strip())
    except Exception as e:
        print(f"Gemini AI error: {e}")
        reply_msg = "⚠️ ระบบประมวลผลขัดข้องชั่วคราว หากอยู่ในอันตรายเร่งด่วนโปรดประสานงานกู้ชีพสายด่วน 1669 ด่วนครับ"
        
    cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

# =============================================================================
# รับพิกัดทางไลน์ประมวลผล GIS เช็กแคช/ค้นหาพิกัด
# =============================================================================
@cfg.handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    lat = event.message.latitude
    lon = event.message.longitude
    state = cfg.USER_STATES.pop(user_id, "default")

    if state == "waiting_water_location":
        # ตรวจสอบและดึงระบบแคชระดับน้ำ 12 นาที
        stations = cfg.get_water_data_lazy()
        
        # คำนวณระยะทาง
        nearby_stations = []
        for s in stations:
            try:
                distance = cfg.calculate_distance(lat, lon, float(s['Lat']), float(s['Lon']))
                s['dist'] = distance
                nearby_stations.append(s)
            except:
                continue
                
        nearby_stations.sort(key=lambda x: x['dist'])
        top_stations = nearby_stations[:3]

        if not top_stations:
            reply_text = "⚠️ ขออภัยครับ ไม่พบสถานีระดับน้ำในเขตพื้นที่ตรวจจับรอบตัวคุณเลยครับ"
        else:
            reply_text = "🌊 **รายงานระดับน้ำใกล้คุณล่าสุด (Sync ทุก 12 นาที):**\n\n"
            for i, st in enumerate(top_stations, 1):
                reply_text += (
                    f"{i}️⃣ สถานี: {st['Name']}\n"
                    f"   📌 ลุ่มน้ำ: {st['River']}\n"
                    f"   📏 ระดับน้ำ: {st['WaterLevel']} ม.รทก.\n"
                    f"   📉 ระดับตลิ่ง: {st['BankLevel']} ม.รทก.\n"
                    f"   📊 สถานการณ์: {st['Situation']}\n"
                    f"   🛣️ ห่างจากคุณ: {st['dist']:.2f} กิโลเมตร\n"
                    f"   ⏱️ อัปเดตล่าสุด: {st['Time']}\n\n"
                )
            reply_text += "🔗 ตรวจสอบแผนที่ระดับน้ำกรมชลประทานแบบละเอียดได้ที่:\nhttps://www.thaiwater.net/water/wl"

        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif state == "waiting_shelter_location":
        top_shelters = cfg.find_nearest_shelters_unlimited(lat, lon)
        if not top_shelters:
            reply_text = "🏠 ขออภัยครับ ขณะนี้ไม่พบศูนย์พักพิงที่เปิดให้พักอาศัยอยู่ในระบบเลยครับ"
        else:
            reply_text = "🏠 **ศูนย์พักพิงใกล้พิกัดคุณที่สุด (เฉพาะที่มีที่ว่าง):**\n\n"
            for i, sh in enumerate(top_shelters, 1):
                reply_text += (
                    f"{i}️⃣ {sh['Name']}\n"
                    f"   📍 ที่ตั้ง: อ.{sh['District']} จ.{sh['Province']}\n"
                    f"   🟢 สถานะ: ยังมีที่ว่าง (ว่าง {sh['remaining']} ที่)\n"
                    f"   🎒 สิ่งอำนวยความสะดวก: {sh['Facilities']}\n"
                    f"   📞 โทร: {sh['Contact']}\n"
                    f"   🛣️ ห่างจากคุณ: {sh['dist']:.2f} กิโลเมตร\n"
                    f"   🧭 ลิงก์นำทาง GPS: https://www.google.com/maps/search/?api=1&query={sh['Latitude']},{sh['Longitude']}\n\n"
                )
            reply_text += "⚠️ แนะนำโทรตรวจสอบข้อมูลอัปเดตกับเจ้าหน้าที่ก่อนอพยพเสมอนะครับ"

        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif state == "sos_location":
        # สเต็ป SOS 1 สำเร็จ นำพิกัดไปเก็บชั่วคราว
        cfg.USER_DATA[user_id]["lat"] = lat
        cfg.USER_DATA[user_id]["lon"] = lon
        cfg.USER_STATES[user_id] = "sos_step2"
        
        qr = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="👶 มีเด็ก/คนชรา", text="กลุ่มเปราะบางเด็กชรา")),
            QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วยติดเตียง/พิการ", text="ผู้ป่วยช่วยเหลือตนเองไม่ได้")),
            QuickReplyButton(action=MessageAction(label="👱 ผู้ใหญ่ทั่วไป", text="กลุ่มบุคคลทั่วไป")),
            QuickReplyButton(action=MessageAction(label="🐱 สัตว์เลี้ยง", text="มีสัตว์เลี้ยงร่วมด้วย"))
        ])
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="👥 **SOS - ขั้นที่ 2**\nโปรดระบุกลุ่มผู้ประสบภัยที่ติดอยู่ร่วมกับคุณครับ:", quick_reply=qr))

    elif state == "needs_location":
        cfg.USER_DATA[user_id] = {"lat": lat, "lon": lon}
        cfg.USER_STATES[user_id] = "needs_step2"
        
        qr = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="อาหาร/น้ำดื่ม")),
            QuickReplyButton(action=MessageAction(label="💊 ยารักษาโรค/เวชภัณฑ์", text="ยารักษาโรค")),
            QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็กอ่อน", text="ของใช้เด็กอ่อน")),
            QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="ของใช้ส่วนตัว")),
            QuickReplyButton(action=MessageAction(label="🔦 อุปกรณ์ส่องสว่าง", text="อุปกรณ์ส่องสว่าง")),
            QuickReplyButton(action=MessageAction(label="📝 อื่น ๆ (ระบุเอง)", text="ความต้องการพิเศษเพิ่มเติม"))
        ])
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📦 **รับถุงยังชีพ - ขั้นที่ 2**\nโปรดระบุหมวดหมู่สิ่งของบรรเทาทุกข์ที่ต้องการได้รับครับ:", quick_reply=qr))

    else:
        # กรณีไม่ได้กดคำสั่งอะไรไว้
        reply_text = "📍 คุณส่งพิกัด GPS เข้ามาหาผม หากต้องการแจ้ง SOS หรือค้นหาศูนย์อพยพ โปรดกดเลือกปุ่มคำสั่งที่เหมาะสมจากแถบเมนูด้านล่างก่อนนะครับ"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# =============================================================================
# จัดการรับรูปถ่ายหน้างาน SOS (Step 4)
# =============================================================================
@cfg.handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    state = cfg.USER_STATES.get(user_id)
    
    if state == "sos_step4":
        # ในกระบวนการจริง จะนำรูปอัปโหลดลง Storage (เช่น ImgBB)
        # โค้ดนี้จำลองการได้รับภาพและบันทึก URL เป็นพิกัดอ้างอิงชั่วคราว
        cfg.USER_DATA[user_id]["image_url"] = "มีรูปถ่ายประกอบแนบส่งมายังแผนที่"
        send_sos_summary(event, user_id)

# =============================================================================
# แสดงสรุปยืนยันเคส SOS ท้ายขั้นตอน
# =============================================================================
def send_sos_summary(event, user_id):
    cfg.USER_STATES[user_id] = "sos_confirm"
    data = cfg.USER_DATA[user_id]
    
    summary_text = (
        "🚨 **สรุปคำขอรับการช่วยเหลือฉุกเฉิน SOS** 🚨\n\n"
        f"👤 ชื่อผู้แจ้ง: {data.get('first_name', 'ผู้ส่ง')} {data.get('last_name', '')}\n"
        f"📞 เบอร์ติดต่อกู้ชีพ: {data.get('phone', '-')}\n"
        f"📍 พิกัดแจ้งเหตุ: {data.get('lat', 0):.4f}, {data.get('lon', 0):.4f}\n"
        f"👥 ประเภทผู้ประสบภัย: {data.get('group', '-')}\n"
        f"🌊 สภาพระดับน้ำ: {data.get('severity', '-')}\n"
        f"📸 รูปถ่ายหน้างาน: {data.get('image_url', '-')}\n\n"
        "**โปรดยืนยันข้อมูลพิกัดและความถูกต้องเพื่อกดยื่นข้อมูลแจ้งไปยังศูนย์กู้ภัยบัญชาการหลัก**"
    )
    
    qr = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัยด่วน", text="ยืนยันการส่งข้อมูล")),
        QuickReplyButton(action=MessageAction(label="❌ ยกเลิกและแก้ไขใหม่", text="ยกเลิกการส่งข้อมูล"))
    ])
    cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=qr))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
