import os
import datetime
from flask import Flask, request, abort
import bot_config as cfg
from dashboard import dashboard_bp
import google.generativeai as genai

# LINE SDK
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage, FollowEvent,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction, MessageAction
)

app = Flask(__name__)

# ลงทะเบียน Blueprint หน้าต่างระบบเว็บแผงบัญชาการหลัก
app.register_blueprint(dashboard_bp)

# =============================================================================
# 1. การเชื่อมต่อ Webhook ของ LINE
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
# 2. ตรวจจับการเพิ่มเพื่อนของบอตเป็นครั้งแรก (Follow Event)
# =============================================================================
@cfg.handler.add(FollowEvent)
def handle_follow_event(event):
    user_id = event.source.user_id
    
    # กำหนดสถานะลงทะเบียนบังคับทันที
    cfg.USER_STATES[user_id] = "register_first_name"
    cfg.USER_DATA[user_id] = {}
    
    welcome_text = (
        "🎉 ยินดีต้อนรับเข้าสู่ระบบ FLOODCARE AI ผู้ช่วยกู้ภัยอุทกภัยอัจฉริยะครับ!\n\n"
        "เพื่อประโยชน์สูงสุดในการประสานงานช่วยเหลือกรณีเกิดเหตุอุทกภัย "
        "โปรดลงทะเบียนประวัติผู้ประสบภัยสั้นๆ ก่อนเริ่มใช้งานระบบครับ\n\n"
        "📝 **ขั้นตอนที่ 1:** โปรดพิมพ์ส่งเฉพาะ **'ชื่อจริง'** ของคุณส่งเข้ามาในแชทนี้ได้เลยครับ (เช่น 'สมชาย')"
    )
    cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome_text))

# =============================================================================
# 3. จัดการประมวลผลข้อความตัวอักษรของระบบ
# =============================================================================
@cfg.handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = cfg.USER_STATES.get(user_id)

    # 🚨 ระบบวิเคราะห์ดักจับประมวลคำวิกฤตอันตรายถึงชีวิต (Emergency Triage)
    if any(k in user_text for k in ["ช่วยด้วย", "จมน้ำ", "จะตาย", "ติดอยู่", "ช่วยเหลือด่วน"]):
        cfg.USER_STATES.pop(user_id, None)
        cfg.USER_DATA.pop(user_id, None)
        msg = (
            "🚨 **ตรวจพบสัญญาณวิกฤตอันตรายถึงชีวิต!**\n"
            "โปรดตั้งสติและปฏิบัติตามคำแนะนำกู้ชีพเร่งด่วน:\n\n"
            "1. 🔌 **ตัดสะพานไฟหลัก** และกระแสไฟในบ้านทันที\n"
            "2. 🧗 **ขึ้นที่สูง** หรือจุดที่ปลอดภัยที่สุด\n"
            "3. 📱 กดเลือกปุ่มเมนู **'SOS ขอความช่วยเหลือ'** ด้านล่าง เพื่อยืนยันพิกัดทางภูมิศาสตร์แก่กู้ภัย\n\n"
            "📞 ประสานสายด่วนภัยพิบัติ ปภ. โทร **1784** หรือ กู้ชีพแพทย์ โทร **1669** ทันทีครับ!"
        )
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # คำสั่งยกเลิกสถานะ
    if user_text == "ยกเลิก":
        cfg.USER_STATES.pop(user_id, None)
        cfg.USER_DATA.pop(user_id, None)
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนปัจจุบันเรียบร้อยแล้ว คุณสามารถเลือกเมนูหลักใหม่ได้ทันทีครับ")
        )
        return

    # ==========================================
    # 3.1 บล็อกขั้นตอนการลงทะเบียนผู้ใช้รายใหม่
    # ==========================================
    if state == "register_first_name":
        cfg.USER_DATA[user_id] = {"temp_first_name": user_text}
        cfg.USER_STATES[user_id] = "register_last_name"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ขั้นตอนที่ 2: โปรดพิมพ์ระบุ 'นามสกุล' ของคุณเพื่อใช้ยืนยันประวัติกับหน่วยกู้ภัยครับ"))
        return

    elif state == "register_last_name":
        cfg.USER_DATA[user_id]["temp_last_name"] = user_text
        cfg.USER_STATES[user_id] = "register_phone"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ขั้นตอนที่ 3: โปรดพิมพ์ระบุ 'เบอร์โทรศัพท์มือถือ' 10 หลักของคุณเพื่อประสานงานทีมกู้ชีพครับ"))
        return

    elif state == "register_phone":
        clean_phone = cfg.extract_number(user_text)
        if len(clean_phone) < 9 or len(clean_phone) > 10:
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ เบอร์โทรไม่ถูกต้องครับ! โปรดกรอกเป็นหมายเลขตัวเลข 9-10 หลักใหม่อีกครั้งครับ"))
            return

        first_name = cfg.USER_DATA[user_id].get("temp_first_name", "ผู้แจ้ง")
        last_name = cfg.USER_DATA[user_id].get("temp_last_name", "ทั่วไป")
        
        success = False
        sheets_client = cfg.get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID))
                ws = sheet.worksheet("users")
                ws.append_row([user_id, first_name, last_name, clean_phone, datetime.date.today().strftime("%Y-%m-%d"), "ACTIVE"])
                success = True
            except Exception as e:
                print(f"Register sheets error: {e}")

        cfg.USER_STATES.pop(user_id, None)
        cfg.USER_DATA.pop(user_id, None)
        
        if success:
            msg = f"🎉 สมัครสมาชิกเรียบร้อยแล้วคุณ {first_name} {last_name}!\nคุณสามารถกดใช้งานเมนูหลักของระบบเพื่อขอรับความช่วยเหลือได้ทันทีครับ"
        else:
            msg = "⚠️ เกิดข้อขัดข้องในการบันทึกข้อมูลประวัติผู้ใช้ โปรดทดลองพิมพ์ใหม่อีกครั้งครับ"
            
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ==========================================
    # 3.2 บล็อกสเต็ปสะสมกลุ่มเปราะบาง SOS (Multi-Select)
    # ==========================================
    if state == "sos_step2":
        if "selected_groups" not in cfg.USER_DATA[user_id]:
            cfg.USER_DATA[user_id]["selected_groups"] = []

        if user_text == "👉 ยืนยันเลือกกลุ่ม":
            if not cfg.USER_DATA[user_id]["selected_groups"]:
                cfg.USER_DATA[user_id]["selected_groups"].append("ผู้ใหญ่ทั่วไป")
            
            cfg.USER_STATES[user_id] = "sos_step3"
            qr = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="🔴 วิกฤต (มิดหลังคา)", text="วิกฤตสูงสุด")),
                QuickReplyButton(action=MessageAction(label="🟠 สูง (ระดับเอว)", text="ระดับสูง")),
                QuickReplyButton(action=MessageAction(label="🟢 ต่ำ (ระดับเข่า)", text="ปานกลางปกติ"))
            ])
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🌊 **SOS - ขั้นที่ 3**\nโปรดระบุระดับความสูงของน้ำที่ท่วมในบ้านปัจจุบันครับ:", quick_reply=qr))
            return
        
        # เพิ่มสิ่งที่เลือกเข้าไปในลิสต์ป้องกันการซ้ำ
        if user_text not in cfg.USER_DATA[user_id]["selected_groups"]:
            cfg.USER_DATA[user_id]["selected_groups"].append(user_text)
            
        current_selection = ", ".join(cfg.USER_DATA[user_id]["selected_groups"])
        
        qr = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก", text="เด็กเล็ก")),
            QuickReplyButton(action=MessageAction(label="🧓 ผู้สูงอายุ", text="ผู้สูงอายุ")),
            QuickReplyButton(action=MessageAction(label="🚑 ติดเตียง", text="ผู้ป่วยติดเตียง")),
            QuickReplyButton(action=MessageAction(label="🐱 สัตว์เลี้ยง", text="สัตว์เลี้ยง")),
            QuickReplyButton(action=MessageAction(label="👉 ยืนยันเลือกกลุ่ม", text="👉 ยืนยันเลือกกลุ่ม"))
        ])
        
        msg = f"🛒 เลือกสะสมแล้ว: [{current_selection}]\n\nคุณสามารถกดเพิ่มกลุ่มอื่นได้อีก หรือกดปุ่ม **'👉 ยืนยันเลือกกลุ่ม'** ด้านล่างได้ทันทีครับ"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg, quick_reply=qr))
        return

    # ==========================================
    # 3.3 บล็อกสเต็ประดับน้ำ SOS (Step 3) และรูปถ่าย (Step 4)
    # ==========================================
    elif state == "sos_step3":
        cfg.USER_DATA[user_id]["severity"] = user_text
        cfg.USER_STATES[user_id] = "sos_step4"
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📸 **SOS - ขั้นที่ 4**\nโปรดถ่ายรูปภาพสถานการณ์หน้างานจริงส่งเข้ามาในแชท 1 รูป หรือหากไม่สะดวกโปรดพิมพ์บอกคำว่า **'ข้าม'** ได้เลยครับ")
        )
        return

    elif state == "sos_step4":
        if "ข้าม" in user_text:
            cfg.USER_DATA[user_id]["image_url"] = "ไม่ได้แนบรูปถ่าย"
            send_sos_summary(event, user_id)
        else:
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ โปรดแชร์รูปถ่ายหน้างาน หรือพิมพ์คำว่า 'ข้าม' เพื่อดำเนินรายการต่อครับ"))
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
                    
                    groups_str = ", ".join(data.get("selected_groups", ["ไม่ระบุ"]))
                    sos_ws.append_row([
                        case_id, user_id, timestamp, data.get("lat", 0), data.get("lon", 0),
                        groups_str, data.get("severity", "ไม่ระบุ"), data.get("image_url", "ไม่ได้แนบรูปถ่าย"), "OPEN"
                    ])
                    success = True
                except Exception as e:
                    print(f"Failed writing SOS: {e}")

            if success:
                reply_text = (
                    f"🚀 **ส่งสัญญาณ SOS แก่ศูนย์กู้ภัยสำเร็จ!**\n\n"
                    f"🎫 รหัสเคสของคุณ: `{case_id}`\n"
                    f"📊 ลำดับวิกฤต: {data.get('severity')}\n\n"
                    "🛡️ **คำแนะนำระหว่างรอเรือกู้ชีพเข้าพื้นที่:**\n"
                    "1. 🔌 **ตัดคัทเอาท์สะพานไฟใหญ่** ห้ามยืนจุดที่มีปลั๊กไฟจมน้ำเด็ดขาด\n"
                    "2. 🧗 **ย้ายขึ้นจุดที่สูงที่สุด** ของบ้านเพื่อการมองเห็นของเจ้าหน้าที่\n"
                    "3. 🔦 **เตรียมอุปกรณ์ส่งสัญญานสีสันเด่น** เช่น นกหวีด ไฟฉาย หรือผ้าเช็ดตัวสะท้อนแสง\n"
                    "4. 📱 **ปิดแอปพลิเคชันที่ไม่จำเป็น** เพื่อรักษาแบตเตอรี่สื่อสาร\n"
                    "5. 📞 หากน้ำสูงขึ้นต่อเนื่องจนเกิดเหตุอันตรายถึงชีวิต โทร ปภ. **1784** ด่วนครับ"
                )
            else:
                reply_text = f"⚠️ ระบบจัดเก็บข้อมูลกลางล้มเหลว โปรดโทรประสานกู้ภัยโดยตรงที่ 1784 หรือกู้ชีพ 1669 ทันทีครับ!"
                
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        else:
            cfg.USER_STATES.pop(user_id, None)
            cfg.USER_DATA.pop(user_id, None)
            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกการร้องขอ SOS เคสนี้แล้ว สามารถกดเริ่มเหตุการณ์ใหม่ได้ทุกเวลาครับ"))
            return

    # ==========================================
    # 3.4 บล็อกสเต็ปแจ้งขอรับสิ่งของ (User Needs)
    # ==========================================
    if state == "needs_step2":
        cfg.USER_DATA[user_id]["category"] = user_text
        cfg.USER_STATES[user_id] = "needs_step3"
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 **พิมพ์ระบุของที่จำนง:**\nพิมพ์บอกจำนวน หรือระบุประเภทที่ต้องการ (เช่น ขออาหารแห้งและน้ำดื่มสำหรับ 3 คน)")
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
                print(f"Failed writing Needs: {e}")

        if success:
            reply_text = (
                "🟢 **ลงทะเบียนความต้องการเพิ่มเติมเข้าระบบแล้ว!**\n\n"
                f"📦 หมวดสินค้า: {data.get('category')}\n"
                f"📝 รายละเอียด: {details}\n\n"
                "ระบบจะรวบรวมรายชื่อผู้มีความต้องการในพิกัดใกล้เคียงกัน ส่งให้อาสาสมัครจัดถุงยังชีพเพื่อนำส่งให้ถึงพื้นที่ต่อไปครับ"
            )
        else:
            reply_text = "❌ เกิดปัญหาทางด้านการบันทึกข้อมูลลงฐานข้อมูล โปรดทดลองส่งใหม่อีกครั้งครับ"
            
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ==========================================
    # 3.5 ตรวจจับคีย์เวิร์ด Rich Menu 6 ปุ่มหลัก
    # ==========================================
    if user_text == "ตรวจสอบระดับน้ำ":
        cfg.USER_STATES[user_id] = "waiting_water_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="📍 เช็กระดับน้ำ"))])
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🌊 โปรดกดปุ่มแชร์พิกัด 'Location' ด้านล่าง เพื่อให้ระบบคำนวณระดับน้ำจากสถานีที่ใกล้ตัวคุณที่สุดครับ", quick_reply=qr)
        )
        return

    elif user_text == "ศูนย์พักพิง":
        cfg.USER_STATES[user_id] = "waiting_shelter_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="📍 หาศูนย์พักพิง"))])
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🏠 โปรดกดแชร์พิกัด 'Location' ด้านล่าง เพื่อค้นหาศูนย์พักพิงที่มีที่ว่างและอยู่ใกล้คุณที่สุดในขณะนี้ครับ", quick_reply=qr)
        )
        return

    elif user_text == "SOS ขอความช่วยเหลือ":
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
                print(f"Error checking register: {e}")

        # Fast-Track SOS: หากไม่ได้ลงทะเบียนไว้แต่เดิม จะไม่บังคับล็อกอิน แต่จะตั้งชื่อชั่วคราวและส่งพิกัดช่วยชีวิตทันที!
        if registered:
            cfg.USER_DATA[user_id] = {
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone
            }
            reply_text = f"🚨 สวัสดีครับคุณ {first_name}! ระบบพบข้อมูลประวัติลงทะเบียนของคุณเรียบร้อยแล้ว โปรดกดปุ่มแชร์พิกัด 'Location' สีเขียวด้านล่างนี้เพื่อแจ้งตำแหน่งแก่กู้ภัยด่วนที่สุดทันทีครับ"
        else:
            cfg.USER_DATA[user_id] = {
                "first_name": "ผู้ประสบภัย",
                "last_name": "(ไม่ได้ลงทะเบียนล่วงหน้า)",
                "phone": "-"
            }
            reply_text = "🚨 **แจ้งกู้ภัยฉุกเฉินเร่งด่วน!**\nระบบไม่พบข้อมูลลงทะเบียนของคุณล่วงหน้า แต่เนื่องจากเป็นเคสวิกฤตเร่งด่วน ระบบได้ตั้งประวัติให้ชั่วคราวแล้ว โปรดกดปุ่มแชร์พิกัด 'Location' สีเขียวด้านล่างเพื่อยืนยันพิกัดจุดแจ้งเหตุให้กู้ภัยทันทีครับ!"

        cfg.USER_STATES[user_id] = "sos_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="📍 ส่งพิกัดกู้ภัย SOS"))])
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=qr))
        return

    elif user_text in ["แจ้งความต้องการเพิ่มเติม", "ความต้องการ", "แจ้งความต้องการ"]:
        cfg.USER_STATES[user_id] = "needs_location"
        qr = QuickReply(items=[QuickReplyButton(action=LocationAction(label="📍 พิกัดรับสิ่งของ"))])
        cfg.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📦 **ขั้นตอนแจ้งรับของบรรเทาทุกข์**\nโปรดกดแชร์พิกัด 'Location' ด้านล่าง เพื่อระบุตำแหน่งที่คุณอยู่ ณ ปัจจุบันสำหรับรับถุงยังชีพครับ", quick_reply=qr)
        )
        return

    elif user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        db_connected = False
        contact_list = []
        sheets_client = cfg.get_sheets_client()
        if sheets_client:
            try:
                sheet = sheets_client.open_by_key(cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID))
                contacts_worksheet = sheet.worksheet("Contacts")
                rows = contacts_worksheet.get_all_records()
                for r in rows:
                    contact_list.append(f"🚨 {r.get('Name')} ({r.get('Role')})\n📞 โทร: {r.get('Phone')}")
                db_connected = True
            except Exception as e:
                print(f"Failed to load contacts: {e}")

        if db_connected and contact_list:
            reply_text = "📞 **เบอร์โทรศัพท์ฉุกเฉินและหน่วยงานประสานงานในระบบ:**\n\n" + "\n\n".join(contact_list)
        else:
            reply_text = (
                "📞 **เบอร์โทรศัพท์ฉุกเฉินหลักที่จำเป็น:**\n\n"
                "🚨 สายด่วน ปภ. โทร **1784** (แจ้งเตือนภัยและช่วยเหลืออุทกภัย)\n"
                "🚨 สายด่วนกู้ชีพ โทร **1669** (เจ็บป่วยฉุกเฉินทางการแพทย์)\n"
                "🚨 สายด่วนกู้ภัยทางน้ำ โทร **1196** (อุบัติเหตุทางน้ำ/ทางเรือ)\n"
                "🚨 ตำรวจทางหลวง โทร **1193** (ตรวจสอบเส้นทางน้ำท่วมขัง)"
            )
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    elif "ถาม AI" in user_text or "ถาม-ตอบ" in user_text:
        reply_text = "🤖 คุณสามารถพิมพ์รายละเอียดคำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมเข้ามาได้เลยครับ ผมพร้อมจะให้คำแนะนำในการปฏิบัติตัวอย่างถูกต้องให้ครับ"
        cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ตอบคำถามทั่วไปด้วยระบบสมองกล AI แผนการตอบภัยพิบัติ
    if cfg.gemini_model:
        try:
            response = cfg.gemini_model.generate_content(user_text)
            reply_msg = cfg.clean_text_for_line(response.text.strip())
        except Exception as e:
            print(f"Gemini processing error: {e}")
            reply_msg = "⚠️ ระบบ AI ขัดข้องชั่วคราว หากตกอยู่ในความเสี่ยงภัย โปรดโทรสายด่วน ปภ. 1784 ทันทีครับ"
    else:
        reply_msg = "🤖 แนะนำมีสติ สวมเสื้อชูชีพ หลีกเลี่ยงการสัมผัสสายไฟที่เปียกน้ำ และติดตามข่าวสาร ปภ. โทร 1784 เสมอครับ"

    cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

# =============================================================================
# 4. ลอจิกการประมวลผลพิกัด Location Message (ระดับน้ำ, ศูนย์พักพิง, SOS, Needs)
# =============================================================================
@cfg.handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    lat = event.message.latitude
    lon = event.message.longitude
    state = cfg.USER_STATES.pop(user_id, "default")

    # ป้องกันและรองรับระบบค้างหรือแครชด้วย try-except ครอบคลุม 100%
    try:
        # ==========================================
        # 4.1 รายงานระดับน้ำใกล้เคียง (Water Levels)
        # ==========================================
        if state == "waiting_water_location":
            stations = []
            
            # ดึงข้อมูลจากแคช Sheets เป็นด่านแรก
            try:
                stations = cfg.get_water_data_lazy()
            except Exception as sheet_err:
                print(f"[Sheet Error] สิทธิ์ชีตขัดข้อง กำลังสลับไปใช้ระบบสำรองดึงตรง: {sheet_err}")

            # ด่านสำรอง: หากดึงจาก Sheets ไม่ได้ ให้ดึงจาก API ตรงทันทีเพื่อไม่ให้บอตเงียบ
            if not stations:
                try:
                    url = "https://api.thaiwater.net/v1/public/waterlevel/latest"
                    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.thaiwater.net/"}
                    res = cfg.requests.get(url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        api_data = res.json().get('data', [])
                        for st in api_data:
                            s = st.get('station', {})
                            w, b = st.get('value', 0), st.get('threshold_level', 0)
                            sit = "🟢 ปกติ"
                            if b > 0:
                                if w >= b: sit = "🔴 วิกฤต"
                                elif w >= (b * 0.9): sit = "🟡 เฝ้าระวัง"
                            stations.append({
                                "Name": s.get('name', {}).get('th', '-'),
                                "River": s.get('river', {}).get('name', {}).get('th', '-'),
                                "Lat": s.get('lat', 0),
                                "Lon": s.get('lng', 0),
                                "WaterLevel": w,
                                "BankLevel": b,
                                "Situation": sit,
                                "Time": st.get('datetime', '-')
                            })
                except Exception as api_err:
                    print(f"[API Emergency Error] สัญญาณอินเทอร์เน็ตมีปัญหา: {api_err}")

            # คำนวณหาระยะทาง
            nearby_stations = []
            for s in stations:
                try:
                    distance = cfg.calculate_distance(lat, lon, float(s.get('Lat', 0)), float(s.get('Lon', 0)))
                    s['dist'] = distance
                    nearby_stations.append(s)
                except:
                    continue
                    
            nearby_stations.sort(key=lambda x: x['dist'])
            top_stations = nearby_stations[:3]

            if not top_stations:
                reply_text = "⚠️ ขออภัยครับ ไม่พบสถานีตรวจวัดระดับน้ำใกล้พิกัดของคุณในขณะนี้ครับ"
            else:
                reply_text = "🌊 **รายงานระดับน้ำใกล้คุณล่าสุด (ดึงข้อมูลพิกัดจริง):**\n\n"
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

        # ==========================================
        # 4.2 ค้นหาศูนย์อพยพใกล้เคียง (Shelters)
        # ==========================================
        elif state == "waiting_shelter_location":
            top_shelters = []
            try:
                top_shelters = cfg.find_nearest_shelters_unlimited(lat, lon)
            except Exception as e:
                print(f"Error searching shelters: {e}")

            if not top_shelters:
                reply_text = "🏠 ขออภัยครับ ไม่พบข้อมูลศูนย์พักพิงที่เปิดทำการในระบบขณะนี้ โปรดติดต่อ ปภ. 1784 ครับ"
            else:
                reply_text = "🏠 **ศูนย์พักพิงอพยพที่ใกล้พิกัดคุณที่สุด (เฉพาะที่มีที่ว่าง):**\n\n"
                for i, sh in enumerate(top_shelters, 1):
                    reply_text += (
                        f"{i}️⃣ {sh['Name']}\n"
                        f"   📍 ที่อยู่: อ.{sh['District']} จ.{sh['Province']}\n"
                        f"   🟢 สถานะ: ยังมีที่ว่างพักได้ (ว่าง {sh['remaining']} ที่)\n"
                        f"   🎒 สิ่งอำนวยความสะดวก: {sh['Facilities']}\n"
                        f"   📞 โทร: {sh['Contact']}\n"
                        f"   🛣️ ห่างจากคุณ: {sh['dist']:.2f} กิโลเมตร\n"
                        f"   🧭 แผนที่นำทาง GPS: https://www.google.com/maps/search/?api=1&query={sh['Latitude']},{sh['Longitude']}\n\n"
                    )
                reply_text += "⚠️ แนะนำโทรตรวจสอบข้อมูลอัปเดตกับเจ้าหน้าที่ก่อนอพยพเสมอนะครับ"

            cfg.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

        # ==========================================
        # 4.3 ลำดับพิกัดการแจ้ง SOS
        # ==========================================
        elif state == "sos_location":
            cfg.USER_DATA[user_id]["lat"] = lat
            cfg.USER_DATA[user_id]["lon"] = lon
            cfg.USER_STATES[user_id] = "sos_step2"
            
            qr = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก", text="เด็กเล็ก")),
                QuickReplyButton(action=MessageAction(label="🧓 ผู้สูงอายุ", text="ผู้สูงอายุ")),
                QuickReplyButton(action=MessageAction(label="🚑 ติดเตียง", text="ผู้ป่วยติดเตียง")),
                QuickReplyButton(action=MessageAction(label="🐱 สัตว์เลี้ยง", text="สัตว์เลี้ยง")),
                QuickReplyButton(action=MessageAction(label="👉 ยืนยันเลือกกลุ่ม", text="👉 ยืนยันการเลือกกลุ่ม"))
            ])
            cfg.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="👥 **SOS - ขั้นที่ 2**\nโปรดกดระบุกลุ่มผู้ประสบภัยในบ้านของคุณ (เลือกได้หลายข้อ ค่อยกดปุ่มยืนยัน):", quick_reply=qr)
            )

        # ==========================================
        # 4.4 ลำดับพิกัดการรับสิ่งของ (User Needs)
        # ==========================================
        elif state == "needs_location":
            cfg.USER_DATA[user_id] = {"lat": lat, "lon": lon}
            cfg.USER_STATES[user_id] = "needs_step2"
            
            qr = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="อาหาร/น้ำดื่ม")),
                QuickReplyButton(action=MessageAction(label="💊 ยารักษาโรค", text="ยารักษาโรค")),
                QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็กอ่อน", text="ของใช้เด็กอ่อน")),
                QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="ของใช้ส่วนตัว")),
                QuickReplyButton(action=MessageAction(label="🔦 อุปกรณ์ส่องสว่าง", text="อุปกรณ์ส่องสว่าง")),
                QuickReplyButton(action=MessageAction(label="📝 อื่น ๆ (ระบุเอง)", text="อื่น ๆ"))
            ])
            cfg.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="📦 **รับถุงยังชีพ - ขั้นที่ 2**\nโปรดกดเลือกหมวดหมู่ประเภทสิ่งของบริจาคบรรเทาทุกข์ที่ท่านจำเป็นต้องได้รับครับ:", quick_reply=qr)
            )

        else:
            cfg.line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text="📍 ได้รับพิกัดแล้ว หากต้องการเช็กระดับน้ำหรือแจ้ง SOS กรุณากดเลือกที่แถบเมนูก่อนส่งพิกัดพิกัดนะครับ")
            )

    except Exception as global_err:
        print(f"[Global Location Handler Error] เกิดข้อผิดพลาดร้ายแรง: {global_err}")
        try:
            cfg.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ ขออภัยครับ ระบบระบุพิกัดกู้ภัยเกิดข้อขัดข้องชั่วคราว หากท่านกำลังประสบภัยเร่งด่วน โปรดโทรติดต่อเบอร์สายด่วน ปภ. โทร **1784** หรือ กู้ชีพ **1669** ทันทีครับ")
            )
        except Exception as line_send_err:
            print(f"Cannot send fallback message: {line_send_err}")

# =============================================================================
# 5. ตรวจจับรูปถ่ายพิกัด SOS หน้างานจริง (Step 4)
# =============================================================================
@cfg.handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    state = cfg.USER_STATES.get(user_id)
    
    if state == "sos_step4":
        cfg.USER_DATA[user_id]["image_url"] = "แนบรูปถ่ายสภาพระดับน้ำประกอบพิกัดมายังแผนที่"
        send_sos_summary(event, user_id)
