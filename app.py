"""
FLOODCARE AI - Main Application Server (AI-Powered State Engine)
===============================================================
Author: Senior Software Architect
Description: Flask-based webhook server for LINE Official Account.
Integrates Pure AI semantic classification, state machine, and 
natural language generation for local search queries (water levels and shelters).
"""

import os
import time
import json
import traceback
import datetime
from flask import Flask, request, abort, jsonify, render_template_string

from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, FollowEvent,
    TextSendMessage, FlexSendMessage, QuickReply, QuickReplyButton, LocationAction
)

# นำเข้าการตั้งค่าและฟังก์ชันทั้งหมดจาก bot_config
from bot_config import (
    line_bot_api, handler, Logger, rate_limiter, sessions, IntentClassifier,
    ask_gemini, ask_gemini_with_search, show_loading_animation, build_snake_bite_flex,
    build_prep_guide_flex, build_help_flex, get_greeting_message,
    build_faq_response_flex, get_live_weather_data, build_weather_flex,
    find_nearest_shelters, build_shelter_flex_message, get_bangkok_time,
    get_live_water_levels_from_api, build_water_level_flex_message,
    calculate_distance, SOS_LIFF_URL, NEED_LIFF_URL, REGISTER_LIFF_URL,
    FLASK_SECRET_KEY, DASHBOARD_API_KEY, sheets_mgr
)

# =============================================================================
# FLASK APP INITIALIZATION
# =============================================================================

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY or os.urandom(24)

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint to ensure the server is running."""
    return jsonify({
        "status": "online",
        "service": "FLOODCARE AI",
        "timestamp": get_bangkok_time().isoformat()
    }), 200

# =============================================================================
# LINE WEBHOOK ENDPOINT
# =============================================================================

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Webhook Endpoint"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    Logger.info("Webhook", f"Received request", {"signature": signature})
    
    try:
        # Handle the webhook body
        if handler:
            handler.handle(body, signature)
    except InvalidSignatureError:
        Logger.security("Webhook", "Invalid signature detected")
        abort(400)
    except Exception as e:
        Logger.error("Webhook", f"Error processing webhook: {e}\n{traceback.format_exc()}")
        abort(500)
        
    return 'OK'

# =============================================================================
# FOLLOW EVENT HANDLER
# =============================================================================

@handler.add(FollowEvent)
def handle_follow(event):
    """ส่งข้อความต้อนรับเมื่อผู้ใช้แอดไลน์มาครั้งแรก"""
    user_id = event.source.user_id
    show_loading_animation(user_id, loading_seconds=5)
    
    # ส่งข้อความต้อนรับและแนะนำการลงทะเบียนเพื่อความปลอดภัย
    welcome_text = get_greeting_message("คุณ")
    reg_instruction = TextSendMessage(
        text=f"📝 เพื่อเตรียมพร้อมรับความช่วยเหลืออย่างรวดเร็วและถูกต้อง กรุณาลงทะเบียนข้อมูลครัวเรือนของคุณล่วงหน้าโดยกดลิงก์ด้านล่างนี้ได้เลยครับ:\n{REGISTER_LIFF_URL}"
    )
    
    try:
        line_bot_api.reply_message(event.reply_token, [welcome_text, reg_instruction])
    except Exception as e:
        Logger.error("Follow", f"Error sending welcome: {e}")

# =============================================================================
# MESSAGE EVENT HANDLERS
# =============================================================================

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """Handles incoming text messages, extracts intent using LLM, and routes accordingly."""
    user_id = event.source.user_id
    text = event.message.text.strip()
    text_lower = text.lower()
    
    # 1. Rate Limiting Check
    allowed, limit_info = rate_limiter.check(user_id)
    if not allowed:
        retry_in = limit_info.get("retry_after", 60)
        warning_msg = TextSendMessage(text=f"⚠️ ระบบจำกัดการใช้งานชั่วคราว กรุณารอ {retry_in} วินาทีแล้วลองใหม่ครับ")
        line_bot_api.reply_message(event.reply_token, warning_msg)
        return

    # 2. Get or create user session
    session = sessions.get(user_id)
    session.update() # Update timestamp and message count
    
    # 3. ตรวจจับการเรียกใช้งานตรงผ่าน Rich Bar หรือพิมพ์เจาะจง (ทำงานแบบเดิม 100%)
    if text == "สภาพอากาศ":
        session.update(state="WAITING_LOCATION_WEATHER_FLEX")
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กสภาพอากาศ"))
        ])
        msg = TextSendMessage(
            text="🌦️ เพื่อตรวจสอบสภาพอากาศอย่างแม่นยำ กรุณาแชร์พิกัดตำแหน่งที่ตั้งของคุณผ่านปุ่มด้านล่างครับ",
            quick_reply=quick_reply
        )
        line_bot_api.reply_message(event.reply_token, msg)
        return

    if text == "เช็คระดับน้ำ" or text == "ระดับน้ำ":
        session.update(state="WAITING_LOCATION_WATER_FLEX")
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กระดับน้ำ"))
        ])
        msg = TextSendMessage(
            text="🌊 เพื่อสืบค้นข้อมูลสถานีวัดระดับน้ำใกล้ตัว กรุณาแชร์พิกัดตำแหน่งของคุณผ่านปุ่มด้านล่างครับ",
            quick_reply=quick_reply
        )
        line_bot_api.reply_message(event.reply_token, msg)
        return

    if text == "ศูนย์พักพิง":
        session.update(state="WAITING_LOCATION_SHELTER_FLEX")
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดหาศูนย์พักพิง"))
        ])
        msg = TextSendMessage(
            text="🏠 เพื่อค้นหาศูนย์พักพิงหรือจุดอพยพที่ใกล้ที่สุด กรุณาแชร์พิกัดตำแหน่งของคุณผ่านปุ่มด้านล่างครับ",
            quick_reply=quick_reply
        )
        line_bot_api.reply_message(event.reply_token, msg)
        return

    # 4. เรียกพิมพ์ผ่านคำสั่งปกติ (SOS, ขอของ, ลงทะเบียน) -> ส่งลิงก์ LIFF โดยตรงทันที
    if text_lower == "sos" or text == "แจ้งเหตุฉุกเฉิน":
        msg = TextSendMessage(
            text=f"🚨 ต้องการแจ้งเหตุด่วน (SOS) กู้ภัยน้ำท่วมใช่ไหมครับ?\nกรุณากดลิงก์ด้านล่างเพื่อส่งข้อมูลตำแหน่งและรายละเอียดให้เจ้าหน้าที่ทันที:\n{SOS_LIFF_URL}"
        )
        line_bot_api.reply_message(event.reply_token, msg)
        session.reset()
        return

    if text_lower == "ขอของ" or text == "ขอความช่วยเหลือ" or text == "ขอความช่วยเหลือเรื่องสิ่งของ":
        msg = TextSendMessage(
            text=f"📦 ต้องการขอรับสิ่งของช่วยเหลือใช่ไหมครับ?\nกรุณากดลิงก์ด้านล่างเพื่อระบุสิ่งของที่ต้องการ (เช่น ยา, อาหาร, น้ำดื่ม):\n{NEED_LIFF_URL}"
        )
        line_bot_api.reply_message(event.reply_token, msg)
        session.reset()
        return

    if text == "ลงทะเบียน" or text_lower == "register":
        msg = TextSendMessage(
            text=f"📝 ต้องการลงทะเบียนข้อมูลครัวเรือนใช่ไหมครับ?\nกรุณากดลิงก์ด้านล่างเพื่อกรอกข้อมูลล่วงหน้าให้ง่ายต่อการช่วยเหลือ:\n{REGISTER_LIFF_URL}"
        )
        line_bot_api.reply_message(event.reply_token, msg)
        session.reset()
        return

    if text == "วิธีเตรียมตัว" or text == "เตรียมตัวรับมือ":
        show_loading_animation(user_id, loading_seconds=5)
        flex_msg = build_prep_guide_flex()
        line_bot_api.reply_message(event.reply_token, flex_msg)
        session.reset()
        return

    if text == "เบอร์โทร" or text == "เบอร์ติดต่อฉุกเฉิน":
        show_loading_animation(user_id, loading_seconds=5)
        # ดึงรายชื่อเบอร์โทรฉุกเฉิน
        records = sheets_mgr.get_all_records("Contacts")
        if records:
            contacts = []
            for r in records:
                contacts.append(f"🚨 {r.get('Name')}\n   📞 {r.get('Phone')}\n   📝 {r.get('Role', '')}")
            reply = "📞 เบอร์โทรฉุกเฉิน:\n\n" + "\n\n".join(contacts)
        else:
            reply = (
                "📞 เบอร์โทรฉุกเฉิน:\n\n"
                "🚨 ปภ. 1784\n"
                "🚨 สพฉ. 1669\n"
                "🚨 หน่วยกู้ชีพ 1554\n"
                "🚨 ตำรวจทางหลวง 1193"
            )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        session.reset()
        return

    # Show typing/loading animation for AI Processing
    show_loading_animation(user_id, loading_seconds=5)
    
    # 5. Classify Intent using Gemini LLM (Pure Semantic Analysis)
    start_time = time.time()
    intent, confidence = IntentClassifier.classify(text)
    session.last_intent = intent
    
    Logger.perf("Intent", f"Classified as {intent}", (time.time() - start_time) * 1000, {"text": text[:20]})

    try:
        if intent == "SOS":
            msg = TextSendMessage(text=f"🚨 ต้องการแจ้งเหตุด่วน (SOS) กู้ภัยน้ำท่วมใช่ไหมครับ?\nกรุณากดลิงก์ด้านล่างเพื่อส่งข้อมูลตำแหน่งและรายละเอียดให้เจ้าหน้าที่ทันที:\n{SOS_LIFF_URL}")
            line_bot_api.reply_message(event.reply_token, msg)
            session.reset()

        elif intent == "NEEDS":
            msg = TextSendMessage(text=f"📦 ต้องการขอรับสิ่งของช่วยเหลือใช่ไหมครับ?\nกรุณากดลิงก์ด้านล่างเพื่อระบุสิ่งของที่ต้องการ (เช่น ยา, อาหาร, น้ำดื่ม):\n{NEED_LIFF_URL}")
            line_bot_api.reply_message(event.reply_token, msg)
            session.reset()

        elif intent == "REGISTRATION":
            msg = TextSendMessage(text=f"📝 ต้องการลงทะเบียนข้อมูลครัวเรือนใช่ไหมครับ?\nกรุณากดลิงก์ด้านล่างเพื่อกรอกข้อมูลล่วงหน้าให้ง่ายต่อการช่วยเหลือ:\n{REGISTER_LIFF_URL}")
            line_bot_api.reply_message(event.reply_token, msg)
            session.reset()

        elif intent == "WEATHER":
            # สอบถามสภาพอากาศทั่วไปแบบแชร์ตำแหน่ง
            session.update(state="WAITING_LOCATION_WEATHER_FLEX")
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กสภาพอากาศ"))
            ])
            msg = TextSendMessage(
                text="🌦️ เพื่อรายงานสภาพอากาศได้อย่างแม่นยำ กรุณากดแชร์ตำแหน่งที่ตั้งของคุณผ่านปุ่มด้านล่างครับ",
                quick_reply=quick_reply
            )
            line_bot_api.reply_message(event.reply_token, msg)

        elif intent == "WATER_LEVEL_NEARBY":
            # ตรวจจับเจตนาขอข้อมูลระดับน้ำใกล้ตัวด้วย AI -> นำสู่กระบวนการ WAITING_LOCATION_WATER_AI
            session.update(state="WAITING_LOCATION_WATER_AI")
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดตรวจระดับน้ำ"))
            ])
            msg = TextSendMessage(
                text="🌊 ผมยินดีตรวจสอบระดับน้ำให้ครับ กรุณากดแชร์พิกัดที่ตั้งของคุณ เพื่อหาพิกัดสถานีใกล้เคียงที่สุดมาสังเคราะห์คำตอบให้ครับ",
                quick_reply=quick_reply
            )
            line_bot_api.reply_message(event.reply_token, msg)

        elif intent == "SHELTER_NEARBY":
            # ตรวจจับเจตนาขอข้อมูลศูนย์พักพิงใกล้ตัวด้วย AI -> นำสู่กระบวนการ WAITING_LOCATION_SHELTER_AI
            session.update(state="WAITING_LOCATION_SHELTER_AI")
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดหาศูนย์พักพิง"))
            ])
            msg = TextSendMessage(
                text="🏠 ผมช่วยสืบค้นศูนย์พักพิงที่อยู่ใกล้ตัวคุณได้ครับ กรุณากดปุ่มแชร์พิกัดด้านล่างเพื่อให้ระบบค้นหาและวิเคราะห์ข้อมูลให้ทันทีครับ",
                quick_reply=quick_reply
            )
            line_bot_api.reply_message(event.reply_token, msg)

        elif intent == "SNAKE_BITE":
            flex_msg = build_snake_bite_flex()
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()

        elif intent == "PREP_GUIDE":
            flex_msg = build_prep_guide_flex()
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()

        elif intent == "HELP" or intent == "CONTACT":
            flex_msg = build_help_flex()
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()

        elif intent == "GREETING":
            msg = get_greeting_message()
            line_bot_api.reply_message(event.reply_token, msg)
            session.reset()

        elif intent == "CANCEL":
            session.reset()
            msg = TextSendMessage(text="ยกเลิกคำสั่งเรียบร้อยแล้วครับ หากต้องการความช่วยเหลือเพิ่มเติมพิมพ์มาได้เลยครับ")
            line_bot_api.reply_message(event.reply_token, msg)

        else: # AI_QUERY หรือกรณีสืบค้นข้อมูลภาพรวมทั่วไป (ไม่ใช่พิกัดส่วนตัว)
            # ตัวอย่างเช่น: "ภาคเหนือระดับน้ำเป็นอย่างไร" จะไม่มีการขอ Location
            show_loading_animation(user_id, loading_seconds=10)
            result = ask_gemini_with_search(text)
            flex_msg = build_faq_response_flex(
                answer=result["answer"],
                sources=result["sources"],
                question=text
            )
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()

    except Exception as e:
        Logger.error("MessageHandler", f"Error routing intent {intent}: {e}\n{traceback.format_exc()}")
        error_msg = TextSendMessage(text="⚠️ ขออภัยครับ ระบบประมวลผลขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง")
        line_bot_api.reply_message(event.reply_token, error_msg)

# =============================================================================
# LOCATION MESSAGE HANDLER
# =============================================================================

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    """Handles incoming location messages based on the current user state."""
    user_id = event.source.user_id
    lat = event.message.latitude
    lon = event.message.longitude
    
    session = sessions.get(user_id)
    current_state = session.state
    
    Logger.info("LocationHandler", f"Received location {lat},{lon} for state {current_state}")
    
    try:
        # 1. จัดการกรณีมาจากคำถามของ AI (ต้องการให้ AI เป็นผู้เรียบเรียงอธิบายข้อมูลดิบเป็นภาษาธรรมชาติ)
        if current_state == "WAITING_LOCATION_WATER_AI":
            show_loading_animation(user_id, loading_seconds=10)
            all_stations = get_live_water_levels_from_api()
            
            # ค้นหา 3 สถานีที่ใกล้ที่สุด
            nearest = []
            for st in all_stations:
                dist = calculate_distance(lat, lon, st["Lat"], st["Lon"])
                if dist <= 50.0:  # ภายในรัศมี 50 กิโลเมตร
                    st_copy = dict(st)
                    st_copy["distance_km"] = dist
                    nearest.append(st_copy)
            
            nearest.sort(key=lambda x: x["distance_km"])
            nearest = nearest[:3]
            
            # แปลงข้อมูล 3 สถานีเป็นข้อความดิบ (Raw Data)
            raw_data_text = ""
            if nearest:
                for idx, st in enumerate(nearest, 1):
                    raw_data_text += (
                        f"สถานีที่ {idx}: {st['Name']} (แม่น้ำ {st['River']} จังหวัด {st['Location']}) "
                        f"ห่าง {st['distance_km']:.2f} กม. ระดับน้ำวัดได้ {st['WaterLevel']} ม. (ระดับตลิ่งอยู่ที่ {st['BankLevel']} ม.) "
                        f"สถานการณ์เป็นแบบ: {st['Situation']} (แนวโน้ม: {st['Trend']})\n"
                    )
            else:
                raw_data_text = "ไม่พบสถานีวัดระดับน้ำในรัศมี 50 กิโลเมตรใกล้ตำแหน่งนี้"
                
            # ส่งข้อมูลดิบนี้ให้ Gemini AI นำไปแปลงเรียบเรียงให้ออกมาเป็นภาษาธรรมชาติแสนอบอุ่นตามคำสั่งระบบ
            prompt = (
                f"ผู้ใช้ส่งตำแหน่งตำแหน่งพิกัดละติจูดและลองจิจูดของตนเองเข้ามา ระบบตรวจพบข้อมูลสถานีน้ำที่อยู่ใกล้เคียงดังนี้:\n\n"
                f"{raw_data_text}\n"
                "กรุณานำข้อมูลดิบข้างต้นมาอธิบายและเรียบเรียงสรุปให้ผู้ใช้งานด้วยภาษาเขียนที่อบอุ่น อ่อนน้อม กระชับ แนะนำอย่างเป็นมิตร "
                "สรุปสั้นๆ เข้าใจง่ายว่าน้ำท่วมหรือปกติ โดยไม่ต้องใส่ข้อมูลทางเทคนิคเยอะจนเกินไป ไม่ต้องระบุโค้ดสถานี "
                "ห้ามใช้เครื่องหมายดอกจันเด็ดขาด และเขียนความยาวไม่เกิน 3-4 บรรทัดให้ครบประโยค"
            )
            
            ai_response = ask_gemini(prompt)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))
            session.reset()
            
        elif current_state == "WAITING_LOCATION_SHELTER_AI":
            show_loading_animation(user_id, loading_seconds=10)
            shelters = find_nearest_shelters(lat, lon, limit=3)
            
            # แปลงข้อมูล 3 ศูนย์พักพิงเป็นข้อความดิบ (Raw Data)
            raw_data_text = ""
            if shelters:
                for idx, sh in enumerate(shelters, 1):
                    raw_data_text += (
                        f"ศูนย์พักพิงที่ {idx}: {sh['Name']} (ตั้งอยู่ที่ {sh['District']} {sh['Province']}) "
                        f"ห่าง {sh['distance_km']:.2f} กม. มีความจุสูงสุด {sh['Capacity']} ราย ปัจจุบันมีผู้อพยพ {sh['Occupancy']} ราย "
                        f"สถานะรับ: {sh['Status']} (สิ่งอำนวยความสะดวก: เตียง {sh['Beds']} เตียง, ห้องน้ำ {sh['Toilets']} ห้อง, ที่จอดรถ {sh['Parking']})\n"
                    )
            else:
                raw_data_text = "ไม่พบศูนย์พักพิงหรือจุดหลบภัยที่เปิดรับในรัศมีใกล้เคียง"
                
            # ส่งให้ Gemini AI เรียบเรียงสรุปเป็นภาษาพูดแบบมิตร
            prompt = (
                f"ผู้ใช้แชร์ตำแหน่งตำแหน่งพิกัดละติจูดและลองจิจูดของตนเองมา ระบบสืบค้นข้อมูลศูนย์พักพิงที่ใกล้ที่สุดพบดังนี้:\n\n"
                f"{raw_data_text}\n"
                "กรุณาเรียบเรียงอธิบายข้อมูลศูนย์พักพิงที่ใกล้ตัวผู้ใช้ข้างต้นออกมาเป็นภาษาพูดธรรมชาติที่อบอุ่น และน่าพึ่งพิง "
                "บอกว่าสถานใดน่าไปที่สุดและห่างกี่กิโลเมตร สรุปสั้นๆ ให้จบภายใน 3-4 บรรทัด ห้ามใช้เครื่องหมายดอกจันทุกกรณี และไม่ระบุลิงก์ยาว"
            )
            
            ai_response = ask_gemini(prompt)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))
            session.reset()

        # 2. จัดการกรณีมาจากเมนู Rich Bar / Rich Menu (ต้องการผลลัพธ์เป็น Flex Message สวยงามเหมือนเดิม)
        elif current_state == "WAITING_LOCATION_WEATHER_FLEX":
            show_loading_animation(user_id, loading_seconds=5)
            weather_data = get_live_weather_data(lat, lon)
            timestamp = get_bangkok_time().strftime("%d %b %Y %H:%M")
            flex_msg = build_weather_flex(lat, lon, weather_data, timestamp)
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()
            
        elif current_state == "WAITING_LOCATION_SHELTER_FLEX":
            show_loading_animation(user_id, loading_seconds=5)
            shelters = find_nearest_shelters(lat, lon, limit=3)
            flex_msg = build_shelter_flex_message(lat, lon, shelters)
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()
            
        elif current_state == "WAITING_LOCATION_WATER_FLEX":
            show_loading_animation(user_id, loading_seconds=8)
            all_stations = get_live_water_levels_from_api()
            
            # Find nearest 3 stations
            nearest = []
            for st in all_stations:
                dist = calculate_distance(lat, lon, st["Lat"], st["Lon"])
                if dist <= 50.0:
                    st_copy = dict(st)
                    st_copy["distance_km"] = dist
                    
                    # Convert to legacy format for the flex builder
                    st_copy["stationName"] = st["Name"]
                    st_copy["water_level"] = {"value": st["WaterLevel"]}
                    st_copy["bank_level"] = st["BankLevel"]
                    st_copy["situation"] = st["Situation"]
                    
                    nearest.append(st_copy)
            
            nearest.sort(key=lambda x: x["distance_km"])
            nearest = nearest[:3]
            
            timestamp = get_bangkok_time().strftime("%d %b %Y %H:%M")
            flex_msg = build_water_level_flex_message(lat, lon, timestamp, nearest)
            line_bot_api.reply_message(event.reply_token, flex_msg)
            session.reset()
            
        else:
            # แชร์พิกัดแบบไม่ได้ระบุขั้นตอนล่วงหน้า
            msg = TextSendMessage(
                text="📍 ได้รับตำแหน่งพิกัดของคุณเรียบร้อยแล้วครับ\nหากต้องการเช็คสภาพอากาศ ให้พิมพ์ 'สภาพอากาศ'\nหากต้องการค้นหาศูนย์อพยพ ให้พิมพ์ 'ศูนย์พักพิง'\nหากต้องการเช็คระดับน้ำในคลอง ให้พิมพ์ 'เช็คระดับน้ำ' ได้เลยครับ"
            )
            line_bot_api.reply_message(event.reply_token, msg)
            
    except Exception as e:
        Logger.error("LocationHandler", f"Error processing location: {e}\n{traceback.format_exc()}")
        error_msg = TextSendMessage(text="⚠️ ไม่สามารถประมวลผลดึงพิกัดตำแหน่งในระบบได้ชั่วคราว กรุณาลองใหม่อีกครั้งครับ")
        line_bot_api.reply_message(event.reply_token, error_msg)
        session.reset()

# =============================================================================
# DASHBOARD API ROUTES
# =============================================================================

def require_api_key(f):
    """Decorator to require an API key for sensitive dashboard routes."""
    import functools
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if not key or key != DASHBOARD_API_KEY:
            Logger.security("API", "Unauthorized access attempt")
            abort(401)
        return f(*args, **kwargs)
    return decorated_function

@app.route('/api/stats', methods=['GET'])
@require_api_key
def get_system_stats():
    from bot_config import cache
    return jsonify({
        "status": "online",
        "cache_stats": cache.all_stats(),
        "timestamp": get_bangkok_time().isoformat()
    }), 200

# Placeholder routes for LIFF to prevent 404s if accessed directly
@app.route('/liff/<path:page>')
def serve_liff_placeholder(page):
    html = """
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>FLOODCARE AI - {{ page.title() }}</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #F3F4F6; }
            .card { background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; }
            h1 { color: #1F2937; }
            p { color: #6B7280; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>FLOODCARE AI</h1>
            <p>กำลังโหลดระบบฟอร์ม <b>{{ page.upper() }}</b>...</p>
            <p style="font-size: 0.8rem; margin-top: 2rem;">หมายเหตุ: หน้านี้ออกแบบมาเพื่อเปิดใช้งานผ่าน LINE LIFF</p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, page=page)

# =============================================================================
# SERVER STARTUP
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    Logger.info("System", f"Starting FLOODCARE AI server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
