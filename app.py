"""
FLOODCARE AI - Flask Application (Optimized)
=============================================
Main entry point with:
- Intent Classification (reduces Gemini calls by ~80%)
- LIFF integration for SOS and Needs workflows
- State Machine with session management
- Rate limiting per user
- Performance logging

Routes:
  POST /callback          - LINE Webhook
  GET  /liff/sos          - SOS LIFF page
  GET  /liff/need         - Needs LIFF page
  GET  /liff/register     - Registration LIFF page
  POST /api/sos/submit    - SOS form submission
  POST /api/need/submit   - Needs form submission
  POST /api/register/submit - Registration form submission
  GET  /debug/*           - Debug endpoints
"""

import os
import json
import time
import datetime
from typing import Optional
from flask import Flask, request, abort, jsonify, render_template_string, g, session, redirect, url_for

import bot_config
from bot_config import (
    # Core systems
    Logger, cache, rate_limiter, sessions,
    IntentClassifier,
    # Utilities
    sanitize_text, extract_sheet_id, calculate_distance,
    generate_case_id, generate_need_id,
    # LINE
    line_bot_api, handler, show_loading_animation,
    # Flex builders
    build_sos_form_flex, build_ai_response_flex,
    build_language_selector_flex, build_water_level_flex_message,
    build_register_form_flex, build_snake_bite_flex, build_help_flex,
    build_need_form_flex, build_weather_flex,
    # Response handlers
    get_greeting_message, handle_emergency_response,
    build_sos_summary_text, build_needs_summary_text,
    calculate_sos_priority,
    # Services
    ask_gemini, get_live_weather_scraper, get_live_weather_data, sheets_mgr,
    assess_water_level_status, calculate_situation,
    # Legacy state
    USER_STATES, USER_DATA, update_legacy_state,
    # Config
    SOS_LIFF_URL, NEED_LIFF_URL,
    SOS_LIFF_ID, NEED_LIFF_ID,
    REGISTER_LIFF_URL, REGISTER_LIFF_ID,
    WATER_LEVEL_SOURCE_URL, SNAKE_BITE_HOTLINE, SNAKE_BITE_INFO_URL, TMD_SOURCE_URL,
    DASHBOARD_PASSWORD, FLASK_SECRET_KEY,
    LINE_CHANNEL_SECRET,
    WATER_DATA_MAX_AGE_MINUTES,
    GEMINI_API_KEY,
)

# LINE SDK
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage, FollowEvent,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction,
    MessageAction, URIAction, FlexSendMessage
)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY or os.urandom(32)
# ⚠️ ถ้าไม่ตั้ง FLASK_SECRET_KEY ใน environment variable ทุกครั้งที่ deploy ใหม่
# (เช่น Render restart) session ของ dashboard จะหลุดหมด (ผู้ใช้ต้อง login ใหม่)
# เพราะ key สุ่มใหม่ทุกครั้ง — ตั้งค่าให้คงที่ใน .env เพื่อให้ session อยู่ยาวขึ้น


# =============================================================================
# PERFORMANCE MIDDLEWARE
# =============================================================================

@app.before_request
def before_request():
    """Track request start time"""
    request._start_time = time.time()


@app.after_request
def after_request(response):
    """Log request performance"""
    if hasattr(request, '_start_time'):
        elapsed = (time.time() - request._start_time) * 1000
        Logger.perf("HTTP", request.endpoint or request.path, elapsed,
                   {"status": response.status_code, "method": request.method})
    return response


# =============================================================================
# RATE LIMITING MIDDLEWARE
# =============================================================================

def check_rate_limit(user_id: str) -> bool:
    """Check if user is within rate limit"""
    allowed, meta = rate_limiter.check(user_id)
    if not allowed:
        Logger.security("RateLimit", f"Blocked user", user_id,
                       {"retry_after": meta.get("retry_after", 60)})
    return allowed


def _push_save_confirmation(user_id: Optional[str], message: str) -> None:
    """
    Push a confirmation message back to the user's LINE chat after a LIFF
    form (SOS / Needs / Register) has been saved successfully.

    Uses push_message (not reply_message) because the LIFF submit happens
    over a plain HTTP POST — there is no LINE reply token in that request.
    Best-effort only: never raises, so a push failure can't break the API
    response the LIFF page is waiting for.

    Note: push_message counts against your LINE Official Account's monthly
    push message quota (the free tier has a limited number per month).
    """
    if not user_id or user_id == "unknown":
        Logger.warning("Push", "Skipped confirmation push — no verified user_id")
        return
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        Logger.warning("Push", f"Failed to send save-confirmation: {e}")


# =============================================================================
# LIFF API AUTH
# =============================================================================

import urllib.request as _urllib_request
import hmac as _hmac

def _verify_liff_token(id_token: str, expected_liff_id: str) -> Optional[str]:
    """
    Verify LINE LIFF ID token via LINE's verify endpoint.
    Returns user_id string on success, None on failure.

    LINE Verify API: POST https://api.line.me/oauth2/v2.1/verify
    Docs: https://developers.line.biz/en/reference/line-login/#verify-id-token
    """
    if not id_token or not expected_liff_id:
        return None
    try:
        import urllib.parse
        payload = urllib.parse.urlencode({
            "id_token": id_token,
            "client_id": expected_liff_id,
        }).encode()
        req = _urllib_request.Request(
            "https://api.line.me/oauth2/v2.1/verify",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with _urllib_request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            return body.get("sub")  # sub = LINE user_id
    except Exception as e:
        Logger.warning("Auth", f"LIFF token verify failed: {e}")
        return None


def _require_liff_auth(expected_liff_id: str):
    """
    Decorator factory: verify LIFF ID token before running the route.

    Frontend must send header:  Authorization: Bearer <idToken>
    On success, injects `verified_user_id` into request context (g).
    On failure, returns 401.

    Falls back to permissive mode when SOS/NEED LIFF ID env vars are not set
    (i.e. development/local testing), logging a warning instead of blocking.
    """
    from functools import wraps
    from flask import g

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not expected_liff_id:
                # Dev mode: skip verification but warn
                Logger.warning("Auth", f"LIFF_ID not configured — skipping token verify for {fn.__name__}")
                g.verified_user_id = None
                return fn(*args, **kwargs)

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                Logger.security("Auth", "Missing Authorization header", "unknown", {})
                return jsonify({"success": False, "error": "Unauthorized"}), 401

            id_token = auth_header[len("Bearer "):]
            user_id = _verify_liff_token(id_token, expected_liff_id)
            if not user_id:
                Logger.security("Auth", "Invalid LIFF token", "unknown", {})
                return jsonify({"success": False, "error": "Unauthorized"}), 401

            # Rate-limit by verified user_id
            if not check_rate_limit(user_id):
                return jsonify({"success": False, "error": "Rate limit exceeded"}), 429

            g.verified_user_id = user_id
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def _require_debug_key():
    """
    Decorator: protect debug endpoints with DEBUG_API_KEY env var.
    If DEBUG_API_KEY is not set, debug endpoints are disabled entirely.
    Pass key via:  ?key=<DEBUG_API_KEY>  or  X-Debug-Key: <key> header
    """
    from functools import wraps

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            debug_key = os.environ.get("DEBUG_API_KEY", "")
            if not debug_key:
                return jsonify({"error": "Debug endpoints disabled. Set DEBUG_API_KEY to enable."}), 403
            provided = (
                request.args.get("key", "")
                or request.headers.get("X-Debug-Key", "")
            )
            if not _hmac.compare_digest(provided, debug_key):
                Logger.security("Auth", "Invalid debug key", "unknown", {})
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorator


# =============================================================================
# WATER DATA AUTO-SYNC
# =============================================================================

def ensure_water_data_fresh():
    """Check if water data needs refresh"""
    return True  # Placeholder - actual sync in bot_config


# =============================================================================
# MAIN WEBHOOK ROUTE
# =============================================================================

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Webhook endpoint"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK', 200


# =============================================================================
# FOLLOW EVENT (user adds the bot as a friend / first opens the chat)
# =============================================================================

@handler.add(FollowEvent)
def handle_follow(event):
    """
    Fired once when a user adds the official account as a friend.
    This is the very first moment they "เริ่มเข้าใช้งาน" (start using the
    service), so we greet them and invite them to fill in their basic info
    via the Register LIFF form right away.
    """
    user_id = event.source.user_id
    try:
        line_bot_api.reply_message(
            event.reply_token,
            [
                get_greeting_message("คุณ"),
                build_register_form_flex("คุณ"),
            ]
        )
    except Exception as e:
        Logger.error("Follow", f"Welcome message failed: {e}")


# =============================================================================
# TEXT MESSAGE HANDLER (Main Intelligence)
# =============================================================================

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """Optimized text message handler with Intent Classification"""
    start_time = time.time()
    user_text = sanitize_text(event.message.text.strip())
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Rate limiting
    if not check_rate_limit(user_id):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ คุณส่งข้อความเร็วเกินไป กรุณารอสักครู่ครับ")
        )
        return
    
    # Get user session
    session = sessions.get(user_id)
    current_state = session.state
    
    Logger.info("Message", f"Received: '{user_text[:50]}...' " if len(user_text) > 50 else f"Received: '{user_text}'",
               {"user": bot_config.hash_user_id(user_id), "state": current_state})
    
    # ================================================================
    # GLOBAL COMMANDS (work in any state)
    # ================================================================
    
    # Language switch
    if user_text in ["เปลี่ยนภาษา", "change language", "lang"]:
        line_bot_api.reply_message(event.reply_token, build_language_selector_flex())
        return
    
    if user_text.startswith("ตั้งค่าภาษา: "):
        lang = user_text.replace("ตั้งค่าภาษา: ", "").strip()
        session.language = lang
        msgs = {"TH": "✅ ภาษาไทย", "EN": "✅ English", "JP": "✅ 日本語", "MY": "✅ Bahasa Melayu"}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=msgs.get(lang, f"✅ Language set to {lang}"))
        )
        return
    
    # Cancel command
    if user_text in ["ยกเลิก", "cancel", "หยุด", "stop"]:
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ คุณสามารถกดใช้งานเมนูหลักใหม่ได้ทันทีครับ")
        )
        return
    
    # ================================================================
    # ⚠️ DEPRECATED (v2.3): Legacy text-based step-by-step state machines
    # for SOS / Needs / Registration. These are now UNREACHABLE in normal
    # use — _start_sos_flow / _start_needs_flow / _start_registration always
    # send a LIFF form now, and never set these session states anymore.
    # Kept only so old in-flight sessions (started before this update) don't
    # crash; safe to delete entirely once you're sure no one is mid-flow.
    # ================================================================
    if current_state == "sos_location":
        _handle_sos_location_state(event, user_id, user_text)
        return
    
    if current_state.startswith("sos_"):
        _handle_sos_state_machine(event, user_id, user_text, current_state)
        return
    
    if current_state == "needs_location":
        _handle_needs_location_state(event, user_id, user_text)
        return
    
    if current_state.startswith("needs_"):
        _handle_needs_state_machine(event, user_id, user_text, current_state)
        return
    
    if current_state.startswith("register_"):
        _handle_registration(event, user_id, user_text, current_state)
        return
    
    # ================================================================
    # INTENT CLASSIFICATION (for IDLE state)
    # ================================================================
    
    intent, confidence = IntentClassifier.classify(user_text)
    session.last_intent = intent
    
    Logger.info("Intent", f"Classified as {intent} (confidence: {confidence})",
               {"user": bot_config.hash_user_id(user_id)})
    
    # ---- SNAKE BITE (verified first-aid info, not free-form AI) ----
    if intent == "SNAKE_BITE":
        line_bot_api.reply_message(event.reply_token, build_snake_bite_flex())
        return

    # ---- EMERGENCY (Highest Priority) ----
    if intent == "EMERGENCY":
        emergency_msg = handle_emergency_response(user_id)
        line_bot_api.reply_message(event.reply_token, emergency_msg)
        return
    
    # ---- GREETING ----
    if intent == "GREETING":
        greeting_msg = get_greeting_message("คุณ")
        line_bot_api.reply_message(event.reply_token, greeting_msg)
        return

    # ---- HELP (capabilities / menu) ----
    if intent == "HELP":
        line_bot_api.reply_message(event.reply_token, build_help_flex())
        return
    
    # ---- CONTACT ----
    if intent == "CONTACT":
        _handle_contact_request(event)
        return
    
    # ---- SHELTER ----
    if intent == "SHELTER":
        _handle_shelter_request(event, user_id)
        return
    
    # ---- WATER LEVEL ----
    if intent == "WATER_LEVEL":
        _handle_water_level_request(event, user_id)
        return
    
    # ---- WEATHER ----
    if intent == "WEATHER":
        _handle_weather_request(event, user_id)
        return
    
    # ---- SOS ----
    if intent == "SOS":
        _start_sos_flow(event, user_id)
        return
    
    # ---- NEEDS ----
    if intent == "NEEDS":
        _start_needs_flow(event, user_id)
        return
    
    # ---- CANCEL ----
    if intent == "CANCEL":
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ")
        )
        return
    
    # ---- LANGUAGE ----
    if intent == "LANGUAGE":
        line_bot_api.reply_message(event.reply_token, build_language_selector_flex())
        return
    
    # ---- REGISTRATION ----
    if intent == "REGISTRATION":
        _start_registration(event, user_id)
        return
    
    # ================================================================
    # AI QUERY (Default - only if no intent matched)
    # ================================================================
    if intent == "AI_QUERY":
        _handle_ai_query(event, user_id, user_text, timestamp)
        return
    
    # Fallback
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="🤔 ไม่เข้าใจคำถาม กรุณาลองใหม่หรือเลือกจากเมนูครับ")
    )


# =============================================================================
# LOCATION MESSAGE HANDLER
# =============================================================================

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    """Handle location sharing from users"""
    user_id = event.source.user_id
    lat = event.message.latitude
    lon = event.message.longitude
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    session = sessions.get(user_id)
    state = session.state
    
    Logger.info("Location", f"Received location: {lat}, {lon}",
               {"user": bot_config.hash_user_id(user_id), "state": state})
    
    # ---- SOS: Receive GPS ----
    if state == "sos_location":
        session.update(state="sos_step2", data={"latitude": lat, "longitude": lon})
        update_legacy_state(user_id, "sos_step2", session.data)
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
            QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
            QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
            QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
            QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
            QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="👥 ระบุกลุ่มผู้ประสบภัย (เลือกได้หลายกลุ่ม กด 'เสร็จสิ้น' เมื่อเลือกครบ):",
                quick_reply=quick_reply
            )
        )
        return
    
    # ---- Needs: Receive GPS ----
    if state == "needs_location":
        session.update(state="needs_step2", data={"need_latitude": lat, "need_longitude": lon})
        update_legacy_state(user_id, "needs_step2", session.data)
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
            QuickReplyButton(action=MessageAction(label="💊 ยา/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
            QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็ก", text="👶 ของใช้เด็กอ่อน")),
            QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
            QuickReplyButton(action=MessageAction(label="🔦 ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
            QuickReplyButton(action=MessageAction(label="📝 อื่นๆ", text="📝 อื่นๆ (ระบุเอง)")),
            QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📦 เลือกหมวดหมู่สิ่งของที่ต้องการ (เลือกได้หลายหมวด กด 'เสร็จสิ้น' เมื่อเลือกครบ):",
                quick_reply=quick_reply
            )
        )
        return
    
    # ---- Shelter Search ----
    if state == "waiting_shelter_location":
        _process_shelter_search(event, lat, lon, user_id)
        return
    
    # ---- Water Level Check ----
    if state == "waiting_water_location":
        _process_water_level(event, lat, lon, user_id, timestamp)
        return
    
    # ---- Weather Check ----
    if state == "waiting_weather_location":
        _process_weather(event, lat, lon, user_id, timestamp)
        return
    
    # Default
    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="📍 ได้รับพิกัดแล้วครับ หากต้องการใช้งาน กรุณาเลือกจากเมนูหลักครับ")
    )


# =============================================================================
# IMAGE MESSAGE HANDLER (SOS Photo)
# =============================================================================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    """Handle image uploads during SOS flow"""
    user_id = event.source.user_id
    session = sessions.get(user_id)
    
    if session.state == "sos_step4":
        image_id = event.message.id
        content_url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
        session.data["photo_url"] = content_url
        session.data["image_id"] = image_id
        session.update(state="sos_confirm")
        update_legacy_state(user_id, "sos_confirm", session.data)
        
        summary = build_sos_summary_text(session.data)
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัย", text="ยืนยันแจ้งกู้ภัย")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก", text="ยกเลิก")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=summary, quick_reply=quick_reply)
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📸 ได้รับรูปภาพแล้วครับ หากต้องการแจ้ง SOS พร้อมส่งรูป กรุณาเริ่มจากเมนู 'SOS' ก่อนครับ")
        )


# =============================================================================
# SOS STATE MACHINE HANDLERS
# =============================================================================

def _handle_sos_location_state(event, user_id, user_text):
    """Handle SOS location state - prompt for GPS"""
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 ส่งพิกัดตำแหน่งแจ้งเหตุ"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🚨 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 ส่งพิกัดตำแหน่งแจ้งเหตุ' ด้านล่าง หรือพิมพ์ 'ยกเลิก' เพื่อเริ่มต้นใหม่ครับ",
            quick_reply=quick_reply
        )
    )


def _handle_sos_state_machine(event, user_id, user_text, state):
    """Handle SOS multi-step workflow"""
    session = sessions.get(user_id)
    
    # ---- Step 2: Select victim groups ----
    if state == "sos_step2":
        valid_options = {
            "👶 มีเด็กเล็ก/คนชรา": "เด็กเล็ก/คนชรา",
            "🚑 มีผู้ป่วยติดเตียง/พิการ": "ผู้ป่วยติดเตียง/พิการ",
            "🩸 มีผู้บาดเจ็บฉุกเฉิน": "ผู้บาดเจ็บฉุกเฉิน",
            "👨‍👩‍👧 ผู้ใหญ่ทั่วไป": "ผู้ใหญ่ทั่วไป",
            "🐶 มีสัตว์เลี้ยง": "สัตว์เลี้ยง"
        }
        
        if "group_types" not in session.data:
            session.data["group_types"] = []
        
        if user_text in valid_options:
            selected = valid_options[user_text]
            if selected not in session.data["group_types"]:
                session.data["group_types"].append(selected)
        elif user_text in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
            if not session.data.get("group_types"):
                session.data["group_types"] = ["ผู้ใหญ่ทั่วไป"]
            session.update(state="sos_step3")
            update_legacy_state(user_id, "sos_step3", session.data)
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="🔴 วิกฤต (มิดหัว/ติดหลังคา)", text="🔴 วิกฤต (มิดหัว/ติดบนหลังคา)")),
                QuickReplyButton(action=MessageAction(label="🟠 สูง (ระดับอก/เกิน 1 เมตร)", text="🟠 สูง (ระดับอก/เกิน 1 เมตร)")),
                QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (ระดับเอว)", text="🟡 ปานกลาง (ระดับเอว)")),
                QuickReplyButton(action=MessageAction(label="🟢 ต่ำ (ระดับหน้าแข้ง)", text="🟢 ต่ำ (ระดับหน้าแข้ง)")),
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="🌊 ระดับน้ำและสถานการณ์ปัจจุบัน\n\nโปรดเลือกระดับความรุนแรง:", quick_reply=quick_reply)
            )
            return
        else:
            # Custom input
            if user_text:
                session.data["group_types"].append(user_text)
        
        # Show selection again
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
            QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
            QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
            QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
            QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
            QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น")),
        ])
        selected_text = ", ".join(session.data["group_types"]) if session.data["group_types"] else "ยังไม่ได้เลือก"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=f"👥 กลุ่มที่เลือก: {selected_text}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                quick_reply=quick_reply
            )
        )
        return
    
    # ---- Step 3: Urgency level ----
    if state == "sos_step3":
        urgency_map = {
            "🔴 วิกฤต (มิดหัว/ติดบนหลังคา)": "วิกฤต",
            "🟠 สูง (ระดับอก/เกิน 1 เมตร)": "สูง",
            "🟡 ปานกลาง (ระดับเอว)": "ปานกลาง",
            "🟢 ต่ำ (ระดับหน้าแข้ง)": "ต่ำ",
        }
        session.data["urgency_level"] = urgency_map.get(user_text, user_text)
        session.data["photo_url"] = "-"
        session.update(state="sos_step4")
        update_legacy_state(user_id, "sos_step4", session.data)
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 4: โปรดพิมพ์รายละเอียดเพิ่มเติม (หรือส่งรูปภาพสถานการณ์)\n\nเช่น 'น้ำท่วมถึงชั้น 2 ต้องการเรือยาง'")
        )
        return
    
    # ---- Step 4: Details/Photo ----
    if state == "sos_step4":
        session.data["note"] = user_text
        session.data["photo_url"] = "-"
        
        # Calculate priority
        priority_label, priority_code = calculate_sos_priority(
            session.data.get("group_types", []),
            session.data.get("urgency_level", "")
        )
        session.data["priority"] = priority_code
        session.data["priority_label"] = priority_label
        
        session.update(state="sos_confirm")
        update_legacy_state(user_id, "sos_confirm", session.data)
        
        summary = build_sos_summary_text(session.data)
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัย", text="ยืนยันแจ้งกู้ภัย")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก", text="ยกเลิก")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=summary, quick_reply=quick_reply)
        )
        return
    
    # ---- Step 5: Confirm ----
    if state == "sos_confirm":
        if "ยืนยัน" in user_text:
            _submit_sos(event, user_id, session)
        else:
            session.reset()
            update_legacy_state(user_id, "IDLE", {})
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ ยกเลิกเคสเรียบร้อยครับ กดปุ่ม SOS ใหม่ได้ทันทีครับ")
            )
        return


def _submit_sos(event, user_id, session):
    """Submit SOS to database"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = session.data
    case_id = generate_case_id()
    
    # Save to sheets
    # header: case_id, user_id, timestamp, latitude, longitude,
    #         water_level_status, victim_count, vulnerable_groups, group_types,
    #         urgency_level, details, photo_url, priority, status,
    #         responder_name, responder_notes, accepted_at, completed_at
    success = sheets_mgr.batch_append("sos_requests", [[
        case_id,
        user_id,
        timestamp,
        data.get("latitude", "0"),
        data.get("longitude", "0"),
        data.get("water_level_status", "-"),           # col 6: water_level_status
        data.get("victim_count", "-"),                  # col 7: victim_count
        data.get("vulnerable_groups", "-"),             # col 8: vulnerable_groups
        ", ".join(data.get("group_types", [])),         # col 9: group_types
        data.get("urgency_level", "ต่ำ"),               # col 10: urgency_level
        data.get("note", "-"),                          # col 11: details
        data.get("photo_url", "-"),                     # col 12: photo_url
        data.get("priority", "NORMAL"),                 # col 13: priority
        "OPEN",                                         # col 14: status
        "-", "-", "-", "-"                              # responder fields
    ]])
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    
    if success:
        reply = (
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
        reply = (
            f"🚀 ส่งข้อมูลสำเร็จ! เลขเคส: {case_id}\n"
            f"⚠️ บันทึกฐานข้อมูลไม่สำเร็จ แต่ข้อมูลถูกบันทึกบนเซิร์ฟเวอร์แล้ว\n\n"
            f"🛡️ ระหว่างรอโปรดปฏิบัติดังนี้:\n"
            f"1. ตัดสะพานไฟในบ้านทันที\n"
            f"2. พยายามอยู่บนที่สูง\n"
            f"3. เตรียมไฟฉายหรือนกหวีด\n"
            f"4. ประหยัดแบตเตอรี่มือถือ\n"
            f"5. หากอันตรายถึงชีวิต โทร 1784"
        )
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


# =============================================================================
# NEEDS STATE MACHINE HANDLERS
# =============================================================================

def _handle_needs_location_state(event, user_id, user_text):
    """Handle needs location state - prompt for GPS"""
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเพื่อรับสิ่งของ"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="📌 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 แชร์พิกัดเพื่อรับสิ่งของ' ด้านล่าง หรือพิมพ์ 'ยกเลิก' เพื่อยกเลิกครับ",
            quick_reply=quick_reply
        )
    )


def _handle_needs_state_machine(event, user_id, user_text, state):
    """Handle Needs multi-step workflow"""
    session = sessions.get(user_id)
    
    # ---- Step 2: Select categories ----
    if state == "needs_step2":
        categories_map = {
            "🍲 อาหาร/น้ำดื่ม": "อาหาร/น้ำดื่ม",
            "💊 ยารักษาโรค/เวชภัณฑ์": "ยารักษาโรค/เวชภัณฑ์",
            "👶 ของใช้เด็กอ่อน": "ของใช้เด็กอ่อน",
            "🧼 ของใช้ส่วนตัว": "ของใช้ส่วนตัว",
            "🔦 อุปกรณ์ส่องสว่าง": "อุปกรณ์ส่องสว่าง",
            "📝 อื่นๆ (ระบุเอง)": "อื่นๆ",
        }
        
        if "need_categories" not in session.data:
            session.data["need_categories"] = []
        
        # User typed custom details directly
        if user_text not in categories_map and user_text not in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
            session.data["need_details"] = user_text
            if not session.data.get("need_categories"):
                session.data["need_categories"] = ["อื่นๆ"]
            session.update(state="needs_step4")
            update_legacy_state(user_id, "needs_step4", session.data)
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก", text="🔴 ด่วนมาก (หมดแล้ว)")),
                QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
                QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน", text="🟢 ไม่ด่วน")),
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือก:", quick_reply=quick_reply)
            )
            return
        
        # User selected a category
        if user_text in categories_map:
            cat = categories_map[user_text]
            if cat not in session.data["need_categories"]:
                session.data["need_categories"].append(cat)
        
        # User pressed "Done"
        if user_text in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
            if not session.data.get("need_categories"):
                session.data["need_categories"] = ["อื่นๆ"]
            
            # Ask for details
            if not session.data.get("need_details"):
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="📝 โปรดระบุรายละเอียดสั้นๆ (เช่น 'ขอน้ำดื่ม 2 แพ็ค ข้าวกล่อง 5 กล่อง')")
                )
                return
            else:
                session.update(state="needs_step4")
                update_legacy_state(user_id, "needs_step4", session.data)
                
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก", text="🔴 ด่วนมาก (หมดแล้ว)")),
                    QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
                    QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน", text="🟢 ไม่ด่วน")),
                ])
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือก:", quick_reply=quick_reply)
                )
                return
        
        # Show categories again
        selected = ", ".join(session.data["need_categories"]) if session.data["need_categories"] else "ยังไม่ได้เลือก"
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
            QuickReplyButton(action=MessageAction(label="💊 ยา/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
            QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็ก", text="👶 ของใช้เด็กอ่อน")),
            QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
            QuickReplyButton(action=MessageAction(label="🔦 ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
            QuickReplyButton(action=MessageAction(label="📝 อื่นๆ", text="📝 อื่นๆ (ระบุเอง)")),
            QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=f"📦 เลือกหมวดหมู่เพิ่ม หรือพิมพ์รายละเอียด\n(เลือกแล้ว: {selected})",
                quick_reply=quick_reply
            )
        )
        return
    
    # ---- Step 3: Details (captured inline in step 2) ----
    if state == "needs_step3":
        session.data["need_details"] = user_text
        session.update(state="needs_step4")
        update_legacy_state(user_id, "needs_step4", session.data)
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก", text="🔴 ด่วนมาก (หมดแล้ว)")),
            QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
            QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน", text="🟢 ไม่ด่วน")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือก:", quick_reply=quick_reply)
        )
        return
    
    # ---- Step 4: Urgency ----
    if state == "needs_step4":
        session.data["need_urgency"] = user_text
        session.update(state="needs_confirm")
        update_legacy_state(user_id, "needs_confirm", session.data)
        
        # Build summary
        lat = session.data.get("need_latitude", "0")
        lon = session.data.get("need_longitude", "0")
        maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        summary = (
            "📋 สรุปรายการความต้องการ\n\n"
            f"📍 พิกัด: {maps_link}\n"
            f"📦 หมวดหมู่: {', '.join(session.data.get('need_categories', []))}\n"
            f"📝 รายละเอียด: {session.data.get('need_details', '-')}\n"
            f"⏳ ความเร่งด่วน: {session.data.get('need_urgency', '-')}\n\n"
            f"ยืนยันการส่งข้อมูลไปยังศูนย์อาสาสมัคร?"
        )
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยัน", text="ยืนยันการแจ้ง")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก", text="ยกเลิก")),
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=summary, quick_reply=quick_reply)
        )
        return
    
    # ---- Step 5: Confirm ----
    if state == "needs_confirm":
        if "ยืนยัน" in user_text:
            _submit_needs(event, user_id, session)
        else:
            session.reset()
            update_legacy_state(user_id, "IDLE", {})
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ ยกเลิกรายการเรียบร้อยครับ")
            )
        return


def _submit_needs(event, user_id, session):
    """Submit needs to database"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = session.data
    need_id = generate_need_id()
    
    success = sheets_mgr.batch_append("user_needs", [[
        need_id, timestamp, user_id,
        data.get("need_latitude", "0"), data.get("need_longitude", "0"),
        ", ".join(data.get("need_categories", [])),
        data.get("need_details", "-"),
        data.get("need_urgency", "ไม่ด่วน"),
        "PENDING", "-", "-", "-"
    ]])
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    
    if success:
        reply = (
            f"🟢 บันทึกความต้องการเรียบร้อยครับ!\n\n"
            f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n"
            f"📝 รายละเอียด: {data.get('need_details', '-')}\n\n"
            f"ทีมอาสาสมัครจะดำเนินการจัดส่งให้ครับ"
        )
    else:
        reply = (
            f"🟢 บันทึกความต้องการสำเร็จ (ระบบชั่วคราว)\n\n"
            f"⚠️ ฐานข้อมูลขัดข้อง แต่ข้อมูลถูกเก็บบนเซิร์ฟเวอร์แล้ว\n\n"
            f"ทีมอาสาสมัครจะดำเนินการจัดส่งให้ครับ"
        )
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


# =============================================================================
# MENU HANDLERS
# =============================================================================

def _start_sos_flow(event, user_id):
    """Start SOS workflow - LIFF ONLY (v2.3).

    ⚠️ ในสถานการณ์ฉุกเฉิน ผู้ใช้ต้องแจ้งเหตุได้ทันที ไม่มีการกรอกข้อมูลทีละขั้นตอน
    ในแชทอีกต่อไป — ทุกอย่างทำผ่านฟอร์ม LIFF เดียวที่ครบในหน้าเดียว เร็วกว่าและ
    ไม่มี state ค้างให้สับสน (เดิมถ้า SOS_LIFF_URL ไม่ได้ตั้งค่า จะ fallback ไปเป็น
    ฟอร์มถามทีละคำถามในแชท ซึ่งตัดออกไปแล้วตามที่ขอ)
    """
    if not SOS_LIFF_URL:
        Logger.warning("SOS", "SOS_LIFF_URL not configured — button will not open correctly")
    line_bot_api.reply_message(event.reply_token, build_sos_form_flex("คุณ"))


def _start_needs_flow(event, user_id):
    """Start Needs workflow - LIFF ONLY (v2.3). See _start_sos_flow docstring."""
    if not NEED_LIFF_URL:
        Logger.warning("Needs", "NEED_LIFF_URL not configured — button will not open correctly")
    line_bot_api.reply_message(event.reply_token, build_need_form_flex("คุณ"))


def _handle_contact_request(event):
    """Handle emergency contact request"""
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


def _handle_shelter_request(event, user_id):
    """Handle shelter search request"""
    session = sessions.get(user_id)
    session.update(state="waiting_shelter_location")
    update_legacy_state(user_id, "waiting_shelter_location", session.data)
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดหาศูนย์พักพิง"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🏠 ค้นหาศูนย์พักพิง\n\nโปรดกดแชร์พิกัดด้านล่าง:",
            quick_reply=quick_reply
        )
    )


def _handle_water_level_request(event, user_id):
    """Handle water level check request"""
    session = sessions.get(user_id)
    session.update(state="waiting_water_location")
    update_legacy_state(user_id, "waiting_water_location", session.data)
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กระดับน้ำ"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🌊 ตรวจสอบระดับน้ำ\n\nโปรดกดแชร์พิกัด:",
            quick_reply=quick_reply
        )
    )


def _handle_weather_request(event, user_id):
    """Handle weather check request"""
    session = sessions.get(user_id)
    session.update(state="waiting_weather_location")
    update_legacy_state(user_id, "waiting_weather_location", session.data)
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กอากาศ"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🌦️ ตรวจสอบสภาพอากาศ\n\nโปรดกดแชร์พิกัด:",
            quick_reply=quick_reply
        )
    )


def _start_registration(event, user_id):
    """Start user registration flow - LIFF ONLY (v2.3). See _start_sos_flow docstring."""
    if not REGISTER_LIFF_URL:
        Logger.warning("Register", "REGISTER_LIFF_URL not configured — button will not open correctly")
    line_bot_api.reply_message(event.reply_token, build_register_form_flex("คุณ"))


def _handle_registration(event, user_id, user_text, state):
    """Handle registration state machine"""
    session = sessions.get(user_id)
    
    if state == "register_first_name":
        session.data["temp_first_name"] = user_text
        session.update(state="register_last_name")
        update_legacy_state(user_id, "register_last_name", session.data)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 2: โปรดพิมพ์ 'นามสกุล' ครับ")
        )
        return
    
    if state == "register_last_name":
        session.data["temp_last_name"] = user_text
        session.update(state="register_phone")
        update_legacy_state(user_id, "register_phone", session.data)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 3: โปรดพิมพ์ 'เบอร์โทร' 9-10 หลักครับ")
        )
        return
    
    if state == "register_phone":
        clean_phone = "".join(filter(str.isdigit, user_text))
        if len(clean_phone) < 9 or len(clean_phone) > 10:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ เบอร์โทรไม่ถูกต้อง! โปรดพิมพ์ตัวเลข 9-10 หลัก")
            )
            return
        
        first_name = session.data.get("temp_first_name", "ผู้แจ้ง")
        last_name = session.data.get("temp_last_name", "ทั่วไป")
        
        # Save to sheets
        success = sheets_mgr.batch_append("users", [[
            user_id, first_name, last_name, clean_phone,
            "-", "-", "-", "0", "0", "0", "-", "FALSE", "PENDING",
            datetime.datetime.now().strftime("%Y-%m-%d"), "ACTIVE"
        ]])

        # ✅ Invalidate users cache ทันที เพื่อให้ตรวจสอบสถานะลงทะเบียนถูกต้อง
        cache.sheets.delete("sheets:users")
        
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        
        if success:
            reply = f"🎉 ยินดีต้อนรับ คุณ {first_name} {last_name}!\nระบบลงทะเบียนเรียบร้อยแล้วครับ"
        else:
            reply = f"🎉 ลงทะเบียนสำเร็จ (ระบบชั่วคราว) คุณ {first_name} {last_name}!"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return


def _handle_ai_query(event, user_id, user_text, timestamp):
    """Handle AI query with optimized Gemini call"""
    show_loading_animation(user_id, loading_seconds=8)

    # Use optimized Gemini call.
    # Ask for a *complete* and *easy to understand* answer — the previous
    # prompt ("ตอบอย่างกระชับ ไม่เกิน 5 บรรทัด") combined with the very
    # strict brevity rule in the model's system_instruction was causing
    # unhelpfully short, sometimes non-answering replies (e.g. answering
    # "what can you do" with just "I am FLOODCARE"). We now explicitly ask
    # for a full, clear answer and — for safety/health-related questions —
    # to mention a real, named source so the person can verify it.
    ai_response = ask_gemini(
        "ก่อนสร้างคำตอบ ให้ประเมินระดับความฉุกเฉิน (Normal/Warning/Emergency/SOS) ตามที่ระบุใน System Prompt ก่อน "
        "และให้ตอบตามระดับความฉุกเฉินนั้น หากเป็น Emergency หรือ SOS ต้องให้คำแนะนำเร่งด่วนและเบอร์ติดต่อทันที "
        "ถ้าคำถามไม่เกี่ยวกับน้ำท่วมหรือภัยพิบัติ ให้ปฏิเสธอย่างสุภาพตาม System Prompt ห้ามตอบข้อมูลนอกขอบเขต\n\n"
        f"คำถาม: {user_text}",
        max_tokens=500
    )
    
    # Send as Flex
    ai_flex = build_ai_response_flex(ai_response, user_text)
    line_bot_api.reply_message(event.reply_token, ai_flex)
    
    # Log to sheets (async)
    try:
        sheets_mgr.batch_append("AI_Logs", [[
            timestamp, user_id, "AI_QUERY", user_text[:200],
            ai_response[:500], "0"
        ]])
    except Exception as e:
        Logger.error("AI", f"Log error: {e}")


# =============================================================================
# LOCATION PROCESSORS
# =============================================================================

def _process_shelter_search(event, lat, lon, user_id):
    """Process shelter search with location"""
    session = sessions.get(user_id)
    records = sheets_mgr.get_all_records("Shelters")
    
    shelters = []
    for r in records:
        if str(r.get("Status", "")).strip() == "ปิดทำการ":
            continue
        try:
            sh_lat = float(r.get("Latitude", 0))
            sh_lon = float(r.get("Longitude", 0))
            dist = calculate_distance(lat, lon, sh_lat, sh_lon)
            cap = int(r.get("Capacity", 100))
            occ = int(r.get("Occupancy", 0))
            remaining = cap - occ
            
            if remaining <= 0:
                status = "🔴 เต็ม"
            elif occ >= cap * 0.8:
                status = f"🟡 ใกล้เต็ม (ว่าง {remaining})"
            else:
                status = f"🟢 มีที่ว่าง (ว่าง {remaining})"
            
            shelters.append({
                "name": r.get("Name", "ไม่ระบุ"),
                "distance": dist,
                "status": status,
                "lat": sh_lat,
                "lon": sh_lon
            })
        except (ValueError, TypeError):
            continue
    
    shelters.sort(key=lambda x: x["distance"])
    top = shelters[:3]
    
    if not top:
        reply = "📍 ไม่พบข้อมูลศูนย์พักพิง โปรดติดต่อ ปภ. 1784 ครับ"
    else:
        reply = "📍 ศูนย์พักพิงใกล้คุณ:\n\n"
        for i, sh in enumerate(top, 1):
            reply += (
                f"{i}. {sh['name']}\n"
                f"   ห่าง: {sh['distance']:.2f} กม.\n"
                f"   สถานะ: {sh['status']}\n"
                f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
            )
        reply += "⚠️ โปรดใช้ความระมัดระวังในการเดินทาง"
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


def _process_water_level(event, lat, lon, user_id, timestamp):
    """Process water level check"""
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=5)
    
    # Get water data from sheets (cached)
    records = sheets_mgr.get_all_records("Water_Levels")
    stations = []
    
    for r in records:
        try:
            st_lat = float(r.get("Lat", 0))
            st_lon = float(r.get("Lon", 0))
            if st_lat == 0 and st_lon == 0:
                continue
            dist = calculate_distance(lat, lon, st_lat, st_lon)
            
            wl_val = r.get("WaterLevel", "-")
            bl_val = r.get("BankLevel", "-")
            situation = r.get("Situation", "ปกติ")
            trend = r.get("Trend", "คงที่")
            
            stations.append({
                "stationName": r.get("Name", "ไม่ระบุ"),
                "provinceName": r.get("Location", ""),
                "riverName": r.get("River", ""),
                "latitude": st_lat,
                "longitude": st_lon,
                "distance_km": dist,
                "water_level": {"value": wl_val, "uom": "m"},
                "bank_level": bl_val,
                "situation": situation,
                "trend": trend,
                "measure_time": r.get("Time", "-"),
                "source": "sheets"
            })
        except (ValueError, TypeError):
            continue
    
    stations.sort(key=lambda x: x["distance_km"])
    top_stations = stations[:3]
    
    try:
        flex_msg = build_water_level_flex_message(lat, lon, timestamp, top_stations)
        line_bot_api.reply_message(event.reply_token, flex_msg)
    except Exception as e:
        Logger.error("WaterLevel", f"Flex failed: {e}")
        # Fallback to text
        lines = ["🌊 ระดับน้ำใกล้คุณ:\n"]
        for st in top_stations:
            wl = st.get("water_level", {})
            wl_val = wl.get("value", "-")
            lines.append(f"• {st['stationName']} (ห่าง {st['distance_km']:.1f} กม.): {wl_val} ม.")
        lines.append(f"\n🔗 ดูแผนที่ระดับน้ำทั้งประเทศ: {WATER_LEVEL_SOURCE_URL}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines)))
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})


def _process_weather(event, lat, lon, user_id, timestamp):
    """Process weather check — uses TMD (กรมอุตุนิยมวิทยา) official open-data API,
    shown as a clear, professional Flex card with a link back to the source."""
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=5)

    weather_data = get_live_weather_data(lat, lon)

    session.reset()
    update_legacy_state(user_id, "IDLE", {})

    try:
        flex_msg = build_weather_flex(lat, lon, weather_data, timestamp)
        line_bot_api.reply_message(event.reply_token, flex_msg)
    except Exception as e:
        Logger.error("Weather", f"Flex failed: {e}")
        # Fallback to plain text if Flex rendering fails for any reason
        weather_text = get_live_weather_scraper(lat, lon)
        reply = (
            f"📍 พิกัด: {lat:.4f}, {lon:.4f}\n"
            f"🕒 {timestamp}\n\n"
            f"{weather_text}\n\n"
            f"⚠️ ข้อมูลพยากรณ์เบื้องต้น โปรดสังเกตท้องฟ้าจริงประกอบ\n"
            f"🔗 ดูพยากรณ์เต็มรูปแบบ: {TMD_SOURCE_URL}"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


# =============================================================================
# LIFF ENDPOINTS
# =============================================================================

# In-process cache for LIFF HTML (loaded once at startup, never re-read per request)
_liff_html_cache: dict = {}

def _load_liff_template(filename: str) -> Optional[str]:
    """
    Load LIFF HTML from templates/ directory with in-process caching.
    Falls back to extracting from the legacy .py source if the file doesn't exist.
    """
    if filename in _liff_html_cache:
        return _liff_html_cache[filename]

    template_path = os.path.join(os.path.dirname(__file__), "templates", filename)
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        _liff_html_cache[filename] = html
        Logger.info("LIFF", f"Loaded template from file: {filename}")
        return html

    Logger.warning("LIFF", f"Template file not found: {template_path}")
    return None


def _inject_liff_id(html: str, liff_id: str) -> str:
    """
    Inject LIFF ID from environment variable into the HTML template.
    Replaces the getLiffId() function body to return the server-side LIFF ID first,
    then falls back to URL query string. Handles both template variants:
    - sos_liff.html: has '// Try to get from URL params' comment
    - need_liff.html / register_liff.html: no comment line
    Uses regex for robust matching regardless of whitespace differences.
    """
    import re
    # Match getLiffId function body (with or without comment line)
    pattern = r'(function getLiffId\(\)\s*\{)([^}]+)(\})'
    replacement = (
        r'\1\n'
        ' // LIFF ID injected server-side from environment variable\n'
        f" const serverLiffId = '{liff_id}';\n"
        ' if (serverLiffId) return serverLiffId;\n'
        ' // Fallback: try URL params\n'
        ' const params = new URLSearchParams(window.location.search);\n'
        " return params.get('liffId') || '';\n"
        r'\3'
    )
    injected = re.sub(pattern, replacement, html, count=1, flags=re.DOTALL)
    if injected == html:
        # Pattern didn't match — log warning but still return html (don't crash)
        Logger.warning("LIFF", "getLiffId() pattern not found in template — LIFF ID not injected")
    return injected


@app.route("/liff/sos")
def sos_liff_page():
    """SOS LIFF HTML page — served from templates/sos_liff.html
    
    Injects SOS_LIFF_ID from environment into the template so the frontend
    does not need to read it from the query string.
    Returns HTTP 200 on success, 500 only if template file is missing.
    """
    try:
        html = _load_liff_template("sos_liff.html")
        if not html:
            Logger.warning("LIFF", "sos_liff.html not found, returning 500")
            return "SOS LIFF โหลดไม่สำเร็จ — ไม่พบไฟล์ templates/sos_liff.html", 500
        html_injected = _inject_liff_id(html, SOS_LIFF_ID or "")
        return render_template_string(html_injected), 200
    except Exception as e:
        Logger.warning("LIFF", f"sos_liff_page error: {e}")
        return "SOS LIFF เกิดข้อผิดพลาด กรุณาติดต่อผู้ดูแลระบบ", 500


@app.route("/liff/need")
def need_liff_page():
    """Needs LIFF HTML page — served from templates/need_liff.html
    
    Injects NEED_LIFF_ID from environment into the template.
    Returns HTTP 200 on success, 500 only if template file is missing.
    """
    try:
        html = _load_liff_template("need_liff.html")
        if not html:
            Logger.warning("LIFF", "need_liff.html not found, returning 500")
            return "Needs LIFF โหลดไม่สำเร็จ — ไม่พบไฟล์ templates/need_liff.html", 500
        html_injected = _inject_liff_id(html, NEED_LIFF_ID or "")
        return render_template_string(html_injected), 200
    except Exception as e:
        Logger.warning("LIFF", f"need_liff_page error: {e}")
        return "Needs LIFF เกิดข้อผิดพลาด กรุณาตดต่อผู้ดูแลระบบ", 500


@app.route("/liff/register")
def register_liff_page():
    """Registration LIFF HTML page — served from templates/register_liff.html
    
    Injects REGISTER_LIFF_ID from environment into the template.
    Returns HTTP 200 on success, 500 only if template file is missing.
    """
    try:
        html = _load_liff_template("register_liff.html")
        if not html:
            Logger.warning("LIFF", "register_liff.html not found, returning 500")
            return "Register LIFF โหลดไม่สำเร็จ — ไม่พบไฟล์ templates/register_liff.html", 500
        html_injected = _inject_liff_id(html, REGISTER_LIFF_ID or "")
        return render_template_string(html_injected), 200
    except Exception as e:
        Logger.warning("LIFF", f"register_liff_page error: {e}")
        return "Register LIFF เกิดข้อผิดพลาด กรุณาติดต่อผู้ดูแลระบบ", 500


# =============================================================================
# STAFF DASHBOARD (read-only admin view — SOS / Needs / Users)
# =============================================================================
# Protected by a single shared password (DASHBOARD_PASSWORD env var) + Flask
# session cookie. This is intentionally simple (no per-user accounts) since
# it's meant for a small relief team — if you need per-user logins/roles
# later, swap this for a proper auth system.

def _dashboard_logged_in() -> bool:
    return bool(session.get("dashboard_authed"))


@app.route("/dashboard/login", methods=["GET", "POST"])
def dashboard_login():
    if not DASHBOARD_PASSWORD:
        return (
            "Dashboard ยังไม่ได้ตั้งค่า — กรุณาตั้ง environment variable "
            "DASHBOARD_PASSWORD ก่อนใช้งานหน้านี้ (ดูวิธีตั้งค่าใน README หัวข้อ Dashboard)",
            500,
        )

    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["dashboard_authed"] = True
            return redirect(url_for("dashboard_home"))
        error = "รหัสผ่านไม่ถูกต้อง"

    return render_template_string(_DASHBOARD_LOGIN_HTML, error=error)


@app.route("/dashboard/logout")
def dashboard_logout():
    session.pop("dashboard_authed", None)
    return redirect(url_for("dashboard_login"))


@app.route("/dashboard")
def dashboard_home():
    if not DASHBOARD_PASSWORD:
        return (
            "Dashboard ยังไม่ได้ตั้งค่า — กรุณาตั้ง environment variable "
            "DASHBOARD_PASSWORD ก่อนใช้งานหน้านี้ (ดูวิธีตั้งค่าใน README หัวข้อ Dashboard)",
            500,
        )
    if not _dashboard_logged_in():
        return redirect(url_for("dashboard_login"))

    html = _load_liff_template("dashboard.html")
    if not html:
        return "Dashboard โหลดไม่สำเร็จ — ไม่พบไฟล์ templates/dashboard.html", 500
    return render_template_string(html)


@app.route("/api/dashboard/data")
def api_dashboard_data():
    """JSON data feed for the dashboard page (table rows + map pins).
    Re-checked on every load so staff always see current sheet data
    (subject to the normal 5-minute Sheets cache in get_all_records)."""
    if not _dashboard_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    try:
        sos_records = sheets_mgr.get_all_records("sos_requests") or []
        need_records = sheets_mgr.get_all_records("user_needs") or []
        user_records = sheets_mgr.get_all_records("users") or []
    except Exception as e:
        Logger.error("Dashboard", f"Sheets read error: {e}")
        sos_records, need_records, user_records = [], [], []

    def _is_pending(rec, status_field="status"):
        s = str(rec.get(status_field, "")).strip().upper()
        return s in ("", "PENDING", "NEW", "รอดำเนินการ")

    sos_pending = sum(1 for r in sos_records if _is_pending(r))
    need_pending = sum(1 for r in need_records if _is_pending(r))

    # newest first; keep the dashboard light by capping rows sent to the browser
    sos_sorted = list(reversed(sos_records))[:100]
    need_sorted = list(reversed(need_records))[:100]

    return jsonify({
        "stats": {
            "sos_total": len(sos_records),
            "sos_pending": sos_pending,
            "need_total": len(need_records),
            "need_pending": need_pending,
            "users_total": len(user_records),
        },
        "sos": sos_sorted,
        "needs": need_sorted,
    })


_DASHBOARD_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>เข้าสู่ระบบ - FLOODCARE Dashboard</title>
<style>
  :root{--bg:#F6F4EF;--surface:#FFFFFF;--ink:#15151A;--muted:#8C8980;--line:#EAE6DF;--primary:#2F6F8F;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Sukhumvit Set',sans-serif;background:var(--bg);
       min-height:100vh;display:flex;align-items:center;justify-content:center;color:var(--ink);}
  .box{background:var(--surface);border:1px solid var(--line);border-radius:24px;padding:40px 32px;width:320px;}
  h1{font-size:20px;font-weight:700;margin-bottom:6px;}
  p{font-size:13px;color:var(--muted);margin-bottom:24px;}
  input{width:100%;height:48px;border:1px solid var(--line);border-radius:12px;padding:0 14px;font-size:16px;margin-bottom:14px;}
  input:focus{outline:none;border-color:var(--primary);}
  button{width:100%;height:48px;border:none;border-radius:12px;background:var(--primary);color:#fff;font-size:16px;font-weight:600;cursor:pointer;}
  .error{color:#C2452F;font-size:13px;margin-bottom:14px;}
</style>
</head>
<body>
  <form class="box" method="post">
    <h1>FLOODCARE Dashboard</h1>
    <p>สำหรับเจ้าหน้าที่เท่านั้น</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <input type="password" name="password" placeholder="รหัสผ่าน" autofocus required>
    <button type="submit">เข้าสู่ระบบ</button>
  </form>
</body>
</html>
"""


@app.route("/api/sos/submit", methods=['POST'])
@_require_liff_auth(SOS_LIFF_ID)
def api_sos_submit():
    """API endpoint for SOS LIFF form submission"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        # Use LINE-verified user_id from token; fallback to payload only in dev mode
        verified_uid = g.get("verified_user_id")
        user_id = verified_uid or data.get("user_id", "unknown")

        case_id = generate_case_id()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        success = sheets_mgr.batch_append("sos_requests", [[
            case_id,
            user_id,                                    # from verified token
            timestamp,
            data.get("latitude", "0"),
            data.get("longitude", "0"),
            data.get("water_level_status", "-"),        # col 6: water_level_status
            data.get("victim_count", "1"),              # col 7: victim_count
            data.get("vulnerable_groups", ""),          # col 8: vulnerable_groups
            data.get("group_types", ""),                # col 9: group_types
            data.get("urgency_level", "ต่ำ"),           # col 10: urgency_level
            data.get("details", "-"),                   # col 11: details
            data.get("photo_url", "-"),                 # col 12: photo_url
            data.get("priority", "NORMAL"),             # col 13: priority
            "OPEN",                                     # col 14: status
            "-", "-", "-", "-"                          # responder fields
        ]])
        
        Logger.info("SOS_API", f"Submitted case {case_id}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ บันทึกข้อมูลแจ้งเหตุเรียบร้อยแล้วครับ\n"
                f"เลขเคส: {case_id}\n\n"
                f"ทีมงานได้รับแจ้งเหตุแล้ว กรุณารอการติดต่อกลับ "
                f"หากสถานการณ์เปลี่ยนแปลงหรือฉุกเฉินมากขึ้น พิมพ์ 'sos' เพื่อแจ้งซ้ำได้ครับ"
            )

        return jsonify({"success": success, "case_id": case_id})
        
    except Exception as e:
        Logger.error("SOS_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/need/submit", methods=['POST'])
@_require_liff_auth(NEED_LIFF_ID)
def api_need_submit():
    """API endpoint for Needs LIFF form submission"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        # Use LINE-verified user_id from token; fallback to payload only in dev mode
        verified_uid = g.get("verified_user_id")
        user_id = verified_uid or data.get("user_id", "unknown")

        need_id = generate_need_id()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        success = sheets_mgr.batch_append("user_needs", [[
            need_id,
            timestamp,
            user_id,                                    # from verified token
            data.get("latitude", "0"),
            data.get("longitude", "0"),
            data.get("categories", ""),
            data.get("details", "-"),
            data.get("urgency", "ไม่ด่วน"),
            "PENDING",
            data.get("halal", "FALSE"),
            "-", "-"
        ]])
        
        Logger.info("Need_API", f"Submitted need {need_id}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ บันทึกข้อมูลความต้องการเรียบร้อยแล้วครับ\n"
                f"เลขที่รายการ: {need_id}\n\n"
                f"ทีมงานจะประสานจัดส่งสิ่งของให้เร็วที่สุดครับ"
            )

        return jsonify({"success": success, "need_id": need_id})
        
    except Exception as e:
        Logger.error("Need_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/register/submit", methods=['POST'])
@_require_liff_auth(REGISTER_LIFF_ID)
def api_register_submit():
    """API endpoint for the Register LIFF form submission (basic user profile)"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        # Use LINE-verified user_id from token; fallback to payload only in dev mode
        verified_uid = g.get("verified_user_id")
        user_id = verified_uid or data.get("user_id", "unknown")

        first_name = (data.get("first_name") or "ผู้แจ้ง").strip()
        last_name = (data.get("last_name") or "ทั่วไป").strip()
        phone = "".join(filter(str.isdigit, data.get("phone", "")))
        if len(phone) < 9 or len(phone) > 10:
            return jsonify({"success": False, "error": "เบอร์โทรไม่ถูกต้อง"}), 400

        register_date = datetime.datetime.now().strftime("%Y-%m-%d")

        success = sheets_mgr.batch_append("users", [[
            user_id,
            first_name,
            last_name,
            phone,
            data.get("province", "-") or "-",
            data.get("district", "-") or "-",
            data.get("sub_district", "-") or "-",
            data.get("latitude", "0"),
            data.get("longitude", "0"),
            data.get("member_count", "1"),
            data.get("emergency_contact", "-") or "-",
            "TRUE",                                       # sms_enabled
            "TRUE" if data.get("consent_pdpa") else "FALSE",  # consent_pdpa
            register_date,
            "ACTIVE",
        ]])

        # Invalidate users cache so registration status is reflected immediately
        cache.sheets.delete("sheets:users")

        Logger.info("Register_API", f"Registered user {bot_config.hash_user_id(user_id)}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ บันทึกข้อมูลเรียบร้อยแล้วครับ\n"
                f"ยินดีต้อนรับคุณ {first_name} {last_name} เข้าสู่ FLOODCARE AI\n\n"
                f"พิมพ์ 'sos' เพื่อแจ้งเหตุฉุกเฉิน หรือ 'ขอของ' เพื่อแจ้งความต้องการสิ่งของได้ทันทีครับ"
            )

        return jsonify({"success": success})

    except Exception as e:
        Logger.error("Register_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# DEBUG ENDPOINTS
# =============================================================================

@app.route("/debug/status", methods=['GET'])
@_require_debug_key()
def debug_status():
    """System status endpoint"""
    return jsonify({
        "system": "FLOODCARE AI v2.0",
        "timestamp": datetime.datetime.now().isoformat(),
        "gemini_ready": bot_config._gemini_initialized if hasattr(bot_config, '_gemini_initialized') else False,
        "sheets_connected": sheets_mgr.get_client() is not None,
        "rate_limiter": {
            "max_requests": bot_config.RATE_LIMIT_REQUESTS,
            "window_seconds": bot_config.RATE_LIMIT_WINDOW
        },
        "cache": cache.all_stats(),
        "sessions": sessions.stats(),
    })


@app.route("/debug/sync-status", methods=['GET'])
@_require_debug_key()
def debug_sync_status():
    """Water data sync status"""
    records = sheets_mgr.get_all_records("Water_Levels")
    return jsonify({
        "record_count": len(records),
        "sheets_connected": sheets_mgr.get_client() is not None,
    })


@app.route("/debug/force-sync", methods=['POST'])
@_require_debug_key()
def debug_force_sync():
    """Force water data sync"""
    # Trigger sync via bot_config
    try:
        client = sheets_mgr.get_client()
        if client:
            return jsonify({"success": True, "message": "Sync triggered"})
        return jsonify({"success": False, "error": "Sheets not connected"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/debug/logs", methods=['GET'])
@_require_debug_key()
def debug_logs():
    """Get recent logs"""
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"logs": Logger.get_logs(limit)})


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    Logger.info("System", f"Starting FLOODCARE AI on port {port}")
    app.run(host="0.0.0.0", port=port)
