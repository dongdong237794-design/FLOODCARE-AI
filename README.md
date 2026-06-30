# FLOODCARE AI v2.0 - Production Ready

ระบบแชทบอทอัจฉริยะสำหรับจัดการภัยน้ำท่วม ปรับปรุงประสิทธิภาพครั้งใหญ่ (Major Performance Upgrade)

> ### 🆕 อัปเดตล่าสุด (v2.1)
> - **แก้บั๊กร้ายแรง**: `app.py` ขาด `from typing import Optional` ทำให้แอป**ค้างไม่ขึ้นเลย**ตอน start — แก้แล้ว
> - **แก้บั๊ก LIFF 401 Unauthorized**: หน้า `sos_liff.html` / `need_liff.html` ไม่ได้ส่ง Authorization token ตอนยิง API submit ทำให้ถ้าตั้งค่า `SOS_LIFF_ID` / `NEED_LIFF_ID` ไว้แล้วจะส่งฟอร์มไม่ผ่าน (401) — แก้แล้วโดยส่ง `liff.getIDToken()` ไปด้วย
> - **เพิ่ม Register LIFF**: ฟอร์มลงทะเบียนข้อมูลเบื้องต้น (ชื่อ/เบอร์โทร/ที่อยู่/PDPA consent) เปิดอัตโนมัติทันทีที่ผู้ใช้เพิ่มเพื่อนบอท (ดูหัวข้อ 5.3)
> - **ข้อความยืนยันอัตโนมัติ**: หลังกดส่งฟอร์ม SOS / Needs / Register สำเร็จ บอทจะส่งข้อความ "บันทึกข้อมูลเรียบร้อยแล้วครับ" กลับเข้าแชท LINE ให้ทันที
>
> ### 🆕 อัปเดตล่าสุด (v2.2)
> - **แก้ปัญหา AI ตอบสั้นเกินไป/ไม่ตอบคำถามจริง**: ปรับ prompt และ system instruction ของ Gemini ที่เคยบีบให้ตอบสั้นเกินไปจนถามว่า "ทำอะไรได้บ้าง" แล้วได้คำตอบแค่ "ฉันคือ FLOODCARE" หรือถามขอคำตอบยาวขึ้นแล้วบอทกลับอ้างคำสั่งภายในแทนที่จะตอบ — ตอนนี้ตอบครบถ้วน เข้าใจง่ายขึ้น
> - **เพิ่ม Intent ใหม่**: `HELP` (ตอบ "ทำอะไรได้บ้าง" ด้วยรายการความสามารถที่ตายตัว ไม่ต้องเดาจาก AI) และ `SNAKE_BITE` (ถูกงูกัด → ตอบด้วยขั้นตอนปฐมพยาบาลที่ตรวจสอบแล้ว พร้อมปุ่มโทร 1367 ศูนย์พิษวิทยารามาธิบดี และลิงก์อ้างอิง ไม่ปล่อยให้ AI เดาคำตอบเรื่องความปลอดภัยเอง)
> - **แนบลิงก์แหล่งข้อมูลในการรายงานระดับน้ำ**: ผลลัพธ์เช็คระดับน้ำแนบลิงก์ไปยัง thaiwater.net (สถาบันสารสนเทศทรัพยากรน้ำ) ให้ผู้ใช้ดูข้อมูลทั้งประเทศเพิ่มได้
>
> ### 🆕 อัปเดตล่าสุด (v2.3)
> - **บังคับใช้ LIFF เท่านั้นสำหรับ SOS / Needs / Register**: ลบ flow แบบกรอกข้อมูลทีละขั้นตอนในแชท (ถามพิกัด → เลือกกลุ่มผู้ประสบภัย → พิมพ์รายละเอียด) ออกแล้ว ตอนนี้กดปุ่มหรือพิมพ์คำสั่งจะได้การ์ดเปิด LIFF ทันที ไม่มี fallback กลับไปเป็นข้อความอีก
> - **จำกัดขอบเขตคำตอบของ AI**: ปรับ system instruction ให้ AI ตอบเฉพาะเรื่องน้ำท่วม/ภัยพิบัติ/ความปลอดภัย/สภาพอากาศ/ที่พักพิงเท่านั้น คำถามนอกเรื่อง (เช่น ถามเรื่องน้ำมันมอเตอร์ไซค์) จะได้รับคำตอบปฏิเสธสั้นๆพร้อมแนะนำให้พิมพ์ "ทำอะไรได้บ้าง" แทน
> - **สภาพอากาศแบบมืออาชีพ**: เปลี่ยนจากข้อความธรรมดาเป็นการ์ด Flex ที่อ่านง่าย พร้อมลิงก์อ้างอิงไปยังกรมอุตุนิยมวิทยา (tmd.go.th) — ข้อมูลดึงจาก TMD Open Data API (มี token ยืนยันตัวตน) ซึ่งเป็นช่องทางที่กรมอุตุฯเปิดให้ใช้ได้ตามสิทธิ์ ไม่ใช่การ scrape เว็บ
> - **แอนิเมชันยืนยันสำเร็จในหน้า LIFF**: เพิ่มเครื่องหมายถูกแบบวาดเส้นเคลื่อนไหว (animated checkmark) ในหน้า SOS / Needs / Register LIFF ตอนบันทึกข้อมูลสำเร็จ ให้ดูเป็นมืออาชีพมากขึ้น
> - **คู่มือตั้งค่า Rich Menu**: เพิ่มหัวข้อ 5.4 อธิบายวิธีตั้งปุ่ม Rich Menu ให้เปิด LIFF ตรง แยกจากปุ่มที่ส่งข้อความพิมพ์
>
> ### 🆕 อัปเดตล่าสุด (v2.4)
> - **ออกแบบหน้า LIFF ใหม่ทั้งหมด**: ปรับสไตล์ SOS / Needs / Register LIFF ให้มินิมอล สีโทนกลาง ไม่ใช้อีโมจิ อ่านง่ายขึ้น (อ้างอิงแนวทาง health/fitness app)
> - **แผนที่ปักหมุดในทุกฟอร์ม LIFF**: เพิ่มแผนที่ (Leaflet/OpenStreetMap) ปักหมุดอัตโนมัติจาก GPS — ลากหมุดปรับเอง หรือแตะบนแผนที่เพื่อปักหมุดเองก็ได้ ถ้าไม่อนุญาต GPS ระบบจะเปิดแผนที่ให้ปักหมุดเองได้ทันที
> - **Dashboard เจ้าหน้าที่ใหม่** ที่ `/dashboard`: ดูเคส SOS/คำขอสิ่งของ/ผู้ลงทะเบียนทั้งหมดแบบเรียลไทม์ พร้อมแผนที่และตาราง มีระบบรหัสผ่านป้องกัน (ดูหัวข้อ 5.5)
>
> ### 🆕 อัปเดตล่าสุด (v2.5) — แก้ 2 บั๊กหลัก: AI ตอบไม่ตรงประเด็น และ LIFF เปิดไม่ได้ (HTTP 500)
>
> **1. AI ตอบไม่ตรงประเด็น (เคยปฏิเสธคำถามสุขภาพที่เกี่ยวกับน้ำท่วม เช่น "ปวดหัวเป็นไข้")**
> - ปรับ system instruction ของ Gemini ใหม่ทั้งหมด ให้มีขั้นตอนชัดเจน 2 ชั้น:
>   1. **ประเมินระดับสถานการณ์ก่อนตอบทุกครั้ง** (Normal / Warning / Emergency / SOS) จากความหมายของข้อความ ไม่ใช่จับคีย์เวิร์ดตรงตัวอย่างเดียว เช่น "ลูกติดอยู่ที่โรงเรียน", "น้ำเข้ารถแล้วทำไง" ถูกจัดเป็น Emergency แม้ไม่มีคำว่า "ช่วยด้วย"
>   2. **เช็คขอบเขตเนื้อหา**: ตอบได้เรื่องน้ำท่วม/ภัยพิบัติ/ความปลอดภัย/ปฐมพยาบาล/สภาพอากาศ/ที่พักพิง/การช่วยเหลือ **รวมถึงอาการเจ็บป่วยทางกายและความรู้สึกของผู้ใช้เสมอ** (เพราะอาจเกี่ยวกับโรคจากน้ำสกปรกหรือความเครียดจากภัยพิบัติ) — แก้บั๊กเดิมที่ AI ปฏิเสธคำถามเหล่านี้ผิดพลาด ปฏิเสธเฉพาะเรื่องที่ไม่เกี่ยวข้องจริงๆ (ฟุตบอล, เกม, การบ้าน, ยานยนต์ ฯลฯ) ด้วยข้อความปฏิเสธที่สุภาพและคงที่
> - เพิ่มคำสำคัญ Emergency ในตัวจับคีย์เวิร์ด (`IntentClassifier`) ให้ครอบคลุมขึ้น: "น้ำเข้าบ้าน", "ติดอยู่บนหลังคา", "น้ำเชี่ยว", "คนจมน้ำ", "รถติดกลางน้ำ", "น้ำเข้ารถ" เพื่อให้เคสชัดเจนถูกจับเข้า EMERGENCY handler ทันทีโดยไม่ต้องรอ AI ประเมิน
>
> **2. LIFF เปิดไม่ได้ (HTTP 500) ที่ `/liff/sos`, `/liff/need`, `/liff/register`**
> - ต้นเหตุ: โค้ดเดิมเปิดไฟล์ HTML ด้วย `open()` + path joining เอง แล้วใช้ `render_template_string()` — ถ้าโฟลเดอร์ `templates/` ไม่ถูก deploy ขึ้นไปครบ หรือมี exception ใดๆตอนอ่านไฟล์ จะได้ error 500 เปล่าๆโดยไม่มี log ให้ตรวจสอบเลย
> - แก้โดยเปลี่ยนมาใช้ `render_template()` ของ Flask เอง (สิ่งที่ Flask ออกแบบมาให้ใช้กับโฟลเดอร์ `templates/` อยู่แล้ว) ผ่านฟังก์ชันกลาง `_render_liff_page()` ที่ครอบ try/except ไว้ — ถ้าเกิด error ใดๆในอนาคต จะ log traceback แบบเต็มลง Render logs ทันที ไม่ใช่ 500 เปล่าๆแบบเดิม
> - ทดสอบแล้วด้วย Flask test client: `/liff/sos`, `/liff/need`, `/liff/register`, `/dashboard` ตอบ HTTP 200 ครบทุกหน้า
> - **ยืนยันว่าไม่กระทบฟีเจอร์อื่น**: route อื่นทั้งหมด (`/callback`, `/api/sos/submit`, `/api/need/submit`, `/api/register/submit`, `/debug/*`) ไม่ถูกแก้ ไม่มีการเปลี่ยนโครงสร้างโปรเจ็กต์หรือลบฟังก์ชันเดิม

---

## สารบัญ

1. [สิ่งที่ปรับปรุง](#whats-new)
2. [โครงสร้างไฟล์](#file-structure)
3. [การติดตั้ง](#installation)
4. [การตั้งค่า Environment Variables](#env-setup)
5. [การสร้าง LIFF App](#liff-setup)
6. [การ Deploy](#deployment)
7. [คู่มือการใช้งาน](#usage)
8. [ระบบ Intent Classification](#intent-system)
9. [ระบบ Cache](#cache-system)
10. [ระบบ Rate Limiting](#rate-limit)
11. [การ Troubleshoot](#troubleshoot)

---

## 1. สิ่งที่ปรับปรุง <a name="whats-new"></a>

### Performance ที่ดีขึ้น
| รายการ | Before | After | ปรับปรุง |
|---------|--------|-------|----------|
| Gemini API Calls | ทุกข้อความ | เฉพาะ AI Query | -80% Token |
| Response Time | 3-10 วินาที | 1-2 วินาที | 60-80% |
| Sheets Reads | ทุก Request | Memory Cache | -90% Reads |
| State Machine | if-elif ยาว | Class-based | อ่านง่าย |
| Memory Leak | ไม่มี TTL | Auto-cleanup 30 นาที | เสถียร |

### Architecture ใหม่
- **Intent Classification System** - แยกข้อความก่อนส่ง AI
- **Multi-Layer Cache** - Memory LRU + TTL Cache
- **Session Manager** - Class-based พร้อม Auto-cleanup
- **Rate Limiter** - Token bucket algorithm
- **Structured Logging** - ติดตาม Performance
- **Sheets Manager** - Batch writes, connection pooling

### SOS LIFF ที่ครบถ้วน
1. ดึง GPS อัตโนมัติ
2. เลือกระดับเหตุ (วิกฤต/สูง/ปานกลาง/ต่ำ)
3. จำนวนผู้ประสบภัย
4. กลุ่มเปราะบาง (เด็ก/ผู้สูงอายุ/ผู้พิการ/ผู้ป่วยติดเตียง/หญิงตั้งครรภ์/สัตว์เลี้ยง)
5. รายละเอียด + รูปภาพ
6. หน้าสรุป + ยืนยัน
7. เลขเคสอ้างอิง

### Needs LIFF ที่ครบถ้วน
1. 11 หมวดหมู่สิ่งของ (อาหาร/น้ำ/ยา/ของใช้/เครื่องนอน/เสื้อผ้า/ไฟฉาย/PowerBank/เด็กอ่อน/สัตว์เลี้ยง/อื่นๆ)
2. ตัวเลือกอาหารฮาลาล
3. พิมพ์รายละเอียดเอง
4. 3 ระดับความเร่งด่วน
5. หน้าสรุป + ยืนยัน

---

## 2. โครงสร้างไฟล์ <a name="file-structure"></a>

```
floodcare-ai/
├── app.py              # Flask App หลัก (Webhook, Routes, API)
├── bot_config.py       # Config, Cache, Gemini, State Machine
├── templates/
│   ├── sos_liff.html       # SOS LIFF (แจ้งเหตุฉุกเฉิน)
│   ├── need_liff.html      # Needs LIFF (ขอความช่วยเหลือ)
│   ├── register_liff.html  # Register LIFF (ลงทะเบียนข้อมูลผู้ใช้เบื้องต้น) — ใหม่
│   └── dashboard.html      # หน้า Dashboard เจ้าหน้าที่ (ดูเคส SOS/Needs/ผู้ลงทะเบียน) — ใหม่
├── sos_liff.py         # (legacy) SOS LIFF HTML source
├── need_liff.py        # (legacy) Needs LIFF HTML source
├── requirements.txt    # Python Dependencies
├── .env.example        # ตัวอย่างไฟล์ environment variables
└── README.md           # คู่มือนี้
```

---

## 3. การติดตั้ง <a name="installation"></a>

### 3.1 ติดตั้ง Dependencies

```bash
pip install -r requirements.txt
```

หรือติดตั้งทีละ package:

```bash
pip install flask line-bot-sdk google-generativeai gspread google-auth requests supabase python-dotenv
```

### 3.2 โครงสร้าง Google Sheets

ระบบจะสร้าง Sheets อัตโนมัติ แต่ต้องมี Sheet หลักที่มีชีตดังนี้:
- `users` - ข้อมูลผู้ใช้
- `sos_requests` - รายการ SOS
- `user_needs` - ความต้องการสิ่งของ
- `Shelters` - ศูนย์พักพิง
- `Water_Levels` - ระดับน้ำ
- `Contacts` - เบอร์ติดต่อ
- `AI_Logs` - บันทึก AI
- `System_Logs` - บันทึกระบบ

---

## 4. การตั้งค่า Environment Variables <a name="env-setup"></a>

สร้างไฟล์ `.env` หรือตั้งค่าใน Hosting:

```env
# LINE Configuration (จำเป็น)
LINE_CHANNEL_ACCESS_TOKEN=your_line_channel_access_token
LINE_CHANNEL_SECRET=your_line_channel_secret

# Gemini AI (จำเป็น)
GEMINI_API_KEY=your_gemini_api_key

# Google Sheets (จำเป็น)
GOOGLE_SHEET_ID=your_google_sheet_id
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}

# TMD Weather API (แนะนำ)
TMD_ACCESS_TOKEN=your_tmd_token

# LIFF Configuration
SOS_LIFF_ID=your_sos_liff_id
SOS_LIFF_URL=https://liff.line.me/your_sos_liff_id
NEED_LIFF_ID=your_need_liff_id
NEED_LIFF_URL=https://liff.line.me/your_need_liff_id
REGISTER_LIFF_ID=your_register_liff_id
REGISTER_LIFF_URL=https://liff.line.me/your_register_liff_id

# Staff Dashboard
DASHBOARD_PASSWORD=your_dashboard_password
FLASK_SECRET_KEY=your_random_session_key

# Performance Tuning (optional)
WATER_DATA_MAX_AGE_MINUTES=10
CACHE_TTL_SECONDS=300
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW=60
SESSION_TTL_MINUTES=30
```

### วิธีการเอา GOOGLE_SERVICE_ACCOUNT_JSON:
1. ไปที่ [Google Cloud Console](https://console.cloud.google.com/)
2. APIs & Services > Credentials > Create Service Account
3. สร้าง Key แบบ JSON
4. เปิดไฟล์ JSON แล้ว copy ทั้งหมดใส่ใน environment variable

---

## 5. การสร้าง LIFF App <a name="liff-setup"></a>

> ⚠️ **สำคัญมาก**: ตอนตั้งค่า Endpoint URL ในแต่ละ LIFF ต้องต่อ `?liffId=YOUR_LIFF_ID` ไว้ท้าย URL ด้วยเสมอ
> เพราะหน้า LIFF (`sos_liff.html`, `need_liff.html`, `register_liff.html`) อ่านค่า `liffId` จาก query string
> เพื่อเอาไปใช้ตอนเรียก `liff.init()` — ถ้าลืมต่อ จะเปิดแอปไม่ติด (LIFF init failed)

### 5.1 SOS LIFF
1. ไปที่ [LINE Developers Console](https://developers.line.biz/)
2. เลือก Provider > Channel
3. เมนู **LIFF** > **Add**
4. ตั้งค่า:
   - LIFF app name: `FLOODCARE SOS`
   - Size: `Full`
   - Endpoint URL: `https://your-app.onrender.com/liff/sos?liffId=YOUR_SOS_LIFF_ID`
   - เช็คถูก `profile`, `chat_message.write`
5. คัดลอก LIFF ID ไปใส่ใน `SOS_LIFF_ID` และใส่ใน Endpoint URL ด้านบนแทน `YOUR_SOS_LIFF_ID`
6. URL เปิดแอปจะเป็น: `https://liff.line.me/{LIFF_ID}` → ใส่ใน `SOS_LIFF_URL`

### 5.2 Needs LIFF
1. เมนู **LIFF** > **Add**
2. ตั้งค่า:
   - LIFF app name: `FLOODCARE Needs`
   - Size: `Full`
   - Endpoint URL: `https://your-app.onrender.com/liff/need?liffId=YOUR_NEED_LIFF_ID`
3. คัดลอก LIFF ID ไปใส่ใน `NEED_LIFF_ID` / `NEED_LIFF_URL`

### 5.3 Register LIFF (ลงทะเบียนข้อมูลผู้ใช้เบื้องต้น) — ใหม่
1. เมนู **LIFF** > **Add**
2. ตั้งค่า:
   - LIFF app name: `FLOODCARE Register`
   - Size: `Full`
   - Endpoint URL: `https://your-app.onrender.com/liff/register?liffId=YOUR_REGISTER_LIFF_ID`
   - เช็คถูก `profile`
3. คัดลอก LIFF ID ไปใส่ใน `REGISTER_LIFF_ID` / `REGISTER_LIFF_URL`
4. เมื่อตั้งค่าครบ ระบบจะ:
   - ส่งฟอร์มนี้ให้ผู้ใช้ใหม่อัตโนมัติทันทีที่เขากดเพิ่มเพื่อน (Follow Event)
   - ส่งฟอร์มนี้เมื่อผู้ใช้พิมพ์ "ลงทะเบียน"
   - หลังกดส่งข้อมูลในฟอร์มสำเร็จ บอทจะ**ส่งข้อความยืนยันกลับเข้าแชท LINE อัตโนมัติ**ว่า "บันทึกข้อมูลเรียบร้อยแล้วครับ"
   - ถ้าไม่ตั้งค่า `REGISTER_LIFF_URL` ระบบจะ fallback กลับไปใช้การลงทะเบียนแบบพิมพ์ข้อความทีละขั้นตอนแทน

> ℹ️ ฟอร์ม SOS และ Needs ก็ถูกอัปเดตให้ส่งข้อความยืนยัน "บันทึกข้อมูลเรียบร้อยแล้วครับ" กลับเข้าแชท LINE
> หลังกดส่งข้อมูลสำเร็จเช่นกัน (ผ่าน LINE push message — ดูหมายเหตุเรื่อง quota ในหัวข้อ Troubleshoot)

---

## 5.4 การตั้งค่า Rich Menu ให้กดแล้วเปิด LIFF ตรง (แยกจากการพิมพ์) <a name="rich-menu-setup"></a>

ตั้งแต่ v2.3 ปุ่ม SOS / แจ้งความต้องการ / ลงทะเบียน **ทำงานผ่าน LIFF เท่านั้น** — เลิกใช้การกรอกข้อมูล
ทีละขั้นตอนในแชทแล้ว (ที่เห็นในภาพถามพิกัด → เลือกกลุ่มผู้ประสบภัย → พิมพ์รายละเอียด คือ flow เก่าที่ตัดออกแล้ว)

วิธีที่ดีที่สุดในการเชื่อม **ปุ่มบน Rich Menu** เข้ากับ LIFF คือตั้ง action เป็น **`uri`** ชี้ตรงไปที่ LIFF URL เลย
ข้อดีคือ **กดปุ่มจะเปิด LIFF ทันที ไม่มีข้อความถูกส่งเข้าระบบแชทเลย** ต่างจากการพิมพ์คำว่า "sos" ที่จะ
วิ่งผ่าน webhook → Intent Classifier → ได้การ์ด Flex ที่มีปุ่มเปิด LIFF อีกที (เพิ่มมา 1 ขั้น เพราะ LINE
ไม่ยอมให้เว็บเปิด LIFF popup เองโดยไม่มีการแตะจากผู้ใช้ก่อน) — นี่คือการ "แยกข้อความปุ่ม Rich Menu
กับการพิมพ์" ที่ทำได้ดีที่สุดและปลอดภัยที่สุดในข้อจำกัดของ LINE

**ขั้นตอนสร้าง Rich Menu:**

1. ไปที่ [LINE Official Account Manager](https://manager.line.biz/) > เลือกบอทของคุณ > เมนู **Rich menu** > **Create**
2. ออกแบบ layout (เช่น แบ่ง 3-6 ช่อง) แล้วกำหนด Action ของแต่ละปุ่มดังนี้:

| ปุ่ม | Action type | ค่าที่ใส่ |
|---|---|---|
| 🆘 SOS แจ้งเหตุ | `Link` (URI) | `https://liff.line.me/{SOS_LIFF_ID}` |
| 📦 ขอความช่วยเหลือ/ของ | `Link` (URI) | `https://liff.line.me/{NEED_LIFF_ID}` |
| 📝 ลงทะเบียน | `Link` (URI) | `https://liff.line.me/{REGISTER_LIFF_ID}` |
| 🌊 เช็คระดับน้ำ | `Text` | `เช็คระดับน้ำ` |
| 🌦️ สภาพอากาศ | `Text` | `สภาพอากาศ` |
| 🏠 ศูนย์พักพิง | `Text` | `ศูนย์พักพิง` |

   > ปุ่มที่ใช้ `Link` (uri) เปิด LIFF ตรงทันที ไม่ผ่านแชท — เหมาะกับ SOS/ความต้องการ/ลงทะเบียน เพราะ
   > ต้องการความเร็วและไม่อยากให้พิมพ์ผิด ส่วนปุ่มที่ใช้ `Text` จะพิมพ์คำนั้นแทนผู้ใช้ลงในแชท แล้ววิ่งเข้า
   > Intent Classifier ตามปกติ — เหมาะกับฟีเจอร์ที่ยังต้องคุยต่อในแชท (เช่น เช็คระดับน้ำที่ต้องขอพิกัดต่อ)

3. กด **Set as default rich menu** เพื่อให้ผู้ใช้ทุกคนเห็นเมนูนี้ทันทีที่เปิดแชท
4. ทดสอบ: กดปุ่ม SOS บน Rich Menu → ต้องเด้งเข้าหน้าฟอร์ม SOS LIFF ทันที โดยไม่มีข้อความ "sos" โผล่ขึ้นในแชท

> ℹ️ ถ้าอยากให้ปุ่ม Rich Menu ส่ง postback event เพื่อ log การกดแยกจากการพิมพ์ในฝั่งเซิร์ฟเวอร์ด้วย
> สามารถเปลี่ยน Action เป็น `Postback` พร้อมส่ง URL ของ LIFF ไปใน data แล้วเขียน handler รับ
> `PostbackEvent` ใน `app.py` เพิ่มเพื่อ log แล้ว reply ด้วยการ์ด Flex ที่มีปุ่มเปิด LIFF — แจ้งมาได้ถ้าต้องการ
> ให้เพิ่มส่วนนี้ให้ครับ

## 5.5 Dashboard เจ้าหน้าที่ <a name="dashboard"></a>

หน้าเว็บสำหรับทีมเจ้าหน้าที่ดูเคสทั้งหมดแบบเรียลไทม์ (ไม่ต้องเปิด Google Sheets เอง) อยู่ที่ `/dashboard`

**ฟีเจอร์:**
- การ์ดสรุปยอด: เคส SOS ทั้งหมด/รอดำเนินการ, คำขอสิ่งของทั้งหมด/รอจัดส่ง, ผู้ลงทะเบียนทั้งหมด
- แผนที่แสดงหมุดทุกเคส (สีแดง = SOS, สีฟ้า = คำขอสิ่งของ) คลิกหมุดดูรายละเอียดย่อได้
- ตารางรายการ SOS และคำขอสิ่งของล่าสุด เรียงใหม่สุดก่อน พร้อมสถานะ
- รีเฟรชข้อมูลอัตโนมัติทุก 1 นาที

**วิธีตั้งค่า:**
1. ไปที่ Render > เลือก service ของบอท > **Environment**
2. เพิ่มตัวแปร:
   - `DASHBOARD_PASSWORD` = รหัสผ่านที่ตั้งเอง (ใช้ร่วมกันทั้งทีม เช่น `flood2024secure`)
   - `FLASK_SECRET_KEY` = ค่าสุ่มสำหรับเข้ารหัส session — สร้างได้ด้วยคำสั่ง
     `python3 -c "import secrets; print(secrets.token_hex(32))"` แล้วเอาผลลัพธ์มาใส่
     (ถ้าไม่ตั้งจะ login หลุดทุกครั้งที่ Render restart บอท)
3. Save → รอ deploy เสร็จ
4. เข้า `https://your-app.onrender.com/dashboard` → ระบบจะพาไปหน้า login อัตโนมัติ → ใส่รหัสผ่านที่ตั้งไว้

> ⚠️ หน้านี้เห็นข้อมูลส่วนตัวของผู้ประสบภัย (เบอร์โทร พิกัดบ้าน) ตั้งรหัสผ่านให้คาดเดายาก และแจ้งรหัส
> ให้เฉพาะเจ้าหน้าที่ที่เกี่ยวข้องเท่านั้น ห้ามแปะรหัสในที่สาธารณะ



### 6.1 เตรียมโค้ดขึ้น GitHub
```bash
cd floodcare-ai
git init
git add .
git commit -m "Initial commit - FLOODCARE AI"

# สร้าง repo ใหม่บน GitHub (ผ่านเว็บ github.com/new) แล้วเอา URL มาใส่
git remote add origin https://github.com/YOUR_USERNAME/floodcare-ai.git
git branch -M main
git push -u origin main
```
> ⚠️ ห้าม commit ไฟล์ `.env` ขึ้น GitHub เด็ดขาด (มี secret อยู่) — ตรวจสอบว่ามีไฟล์ `.gitignore` ที่ exclude `.env` ไว้แล้ว

### 6.2 Deploy บน Render
1. ไปที่ [render.com](https://render.com) > Sign in ด้วย GitHub
2. **New** > **Web Service**
3. เลือก repo `floodcare-ai` ที่ push ขึ้นไป แล้วกด **Connect**
4. ตั้งค่า:
   - **Name**: `floodcare-ai` (หรือชื่อที่ต้องการ — จะได้ URL เป็น `https://floodcare-ai.onrender.com`)
   - **Region**: `Singapore` (ใกล้ไทยที่สุด)
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`
   - **Instance Type**: `Free` (เริ่มต้นได้ แต่จะ sleep หลังไม่มีคนใช้ 15 นาที ทำให้ข้อความแรกตอบช้า — ถ้าจะใช้งานจริงควรอัปเป็น `Starter` ขึ้นไป)
5. เลื่อนลงไปที่ **Environment Variables** > **Add Environment Variable** แล้วใส่ค่าตาม [หัวข้อที่ 4](#env-setup) ให้ครบทุกตัว (รวมถึง `SOS_LIFF_ID`, `NEED_LIFF_ID`, `REGISTER_LIFF_ID` และ URL คู่กัน)
6. กด **Create Web Service** — Render จะ build และ deploy ให้อัตโนมัติ รอสักครู่จนสถานะเป็น `Live`
7. คัดลอก URL ที่ได้ (เช่น `https://floodcare-ai.onrender.com`) ไปตั้งเป็น:
   - Webhook URL ใน LINE Developers Console > Messaging API: `https://floodcare-ai.onrender.com/callback`
   - Endpoint URL ของแต่ละ LIFF ตาม [หัวข้อที่ 5](#liff-setup) (อย่าลืมต่อ `?liffId=...`)
8. กลับไปที่ LINE Developers Console > Messaging API > เปิด **Use webhook** และกด **Verify** เพื่อทดสอบว่า webhook เชื่อมต่อสำเร็จ

### 6.3 อัปเดตโค้ดในอนาคต
ทุกครั้งที่แก้โค้ดแล้ว push ขึ้น GitHub (`git push`), Render จะ deploy เวอร์ชันใหม่ให้อัตโนมัติ (Auto-Deploy เปิดอยู่โดย default)

### 6.4 Heroku (ตัวเลือกอื่น)
```bash
# สร้าง Procfile
echo "web: python app.py" > Procfile

# Heroku CLI
heroku create your-floodcare-app
heroku config:set LINE_CHANNEL_ACCESS_TOKEN=xxx
heroku config:set LINE_CHANNEL_SECRET=xxx
heroku config:set GEMINI_API_KEY=xxx
heroku config:set GOOGLE_SHEET_ID=xxx
heroku config:set GOOGLE_SERVICE_ACCOUNT_JSON='{...}'

git push heroku main
```

### 6.5 Railway / Fly.io / DigitalOcean
ใช้ Dockerfile หรือติดตั้งตาม template ของแต่ละ platform

---

## 7. คู่มือการใช้งาน <a name="usage"></a>

### 7.1 สำหรับผู้ใช้ทั่วไป
พิมพ์ข้อความใน LINE:
- **ทักทาย**: "สวัสดี", "หวัดดี", "hello"
- **ลงทะเบียน**: "ลงทะเบียน" (เปิดฟอร์ม Register LIFF ให้กรอกข้อมูลเบื้องต้น — หรือเปิดอัตโนมัติทันทีที่เพิ่มเพื่อนบอท)
- **SOS**: "sos", "ขอความช่วยเหลือ", "🆘"
- **ความต้องการ**: "ขอของ", "ต้องการ", "ขาดแคลน"
- **เบอร์โทร**: "เบอร์ฉุกเฉิน", "1784"
- **ศูนย์พักพิง**: "ศูนย์พักพิง", "หาที่พัก"
- **ระดับน้ำ**: "ระดับน้ำ", "น้ำท่วม"
- **สภาพอากาศ**: "อากาศ", "ฝน"
- **ถาม AI**: พิมพ์คำถามทั่วไป
- **ยกเลิก**: "ยกเลิก"
- **เปลี่ยนภาษา**: "เปลี่ยนภาษา"

### 7.2 สำหรับเจ้าหน้าที่
**Debug Endpoints:**
```
GET  /debug/status      # สถานะระบบ, cache, sessions
GET  /debug/sync-status # สถานะข้อมูลระดับน้ำ
POST /debug/force-sync  # บังคับ sync ข้อมูล
GET  /debug/logs        # ดู logs ล่าสุด
```

---

## 8. ระบบ Intent Classification <a name="intent-system"></a>

ระบบจำแนกข้อความอัตโนมัติเพื่อลดการเรียก Gemini API:

| Intent | คำสั่งที่รองรับ | การตอบสนอง |
|--------|----------------|------------|
| GREETING | สวัสดี, hello, hi | ทักทาย + เมนู |
| SOS | sos, ขอความช่วยเหลือ | เปิด SOS flow |
| EMERGENCY | ช่วยด้วย, จะตาย | คำแนะนำฉุกเฉินทันที |
| NEEDS | ขอของ, ต้องการ | เปิด Needs flow |
| SHELTER | ศูนย์พักพิง, ที่พัก | ค้นหาศูนย์พักพิง |
| WATER_LEVEL | ระดับน้ำ, น้ำท่วม | เช็คระดับน้ำ |
| WEATHER | อากาศ, ฝน | พยากรณ์อากาศ |
| CONTACT | เบอร์, โทรศัพท์ | เบอร์ฉุกเฉิน |
| AI_QUERY | คำถามทั่วไป | ส่งให้ Gemini |

**ประหยัด Token ~80%** เพราะข้อความส่วนใหญ่ไม่ต้องผ่าน AI

---

## 9. ระบบ Cache <a name="cache-system"></a>

Multi-layer cache อัตโนมัติ:
- **General Cache** - API responses (5 นาที)
- **Weather Cache** - ข้อมูลอากาศ (30 นาที)
- **Water Cache** - ระดับน้ำ (15 นาที)
- **Sessions Cache** - ข้อมูลผู้ใช้ (30 นาที)
- **Sheets Cache** - ข้อมูล Sheets (10 นาที)

---

## 10. ระบบ Rate Limiting <a name="rate-limit"></a>

จำกัดการใช้งาน: **30 requests / 60 seconds / user**
- ป้องกัน Spam
- ลดค่าใช้จ่าย API
- ป้องกันการโจมตี

---

## 11. การ Troubleshoot <a name="troubleshoot"></a>

| ปัญหา | สาเหตุ | แก้ไข |
|-------|--------|-------|
| AI ไม่ตอบ | GEMINI_API_KEY ไม่ถูกต้อง | ตรวจสอบ API Key |
| Sheets ไม่บันทึก | JSON Key ผิด | ตรวจสอบ GOOGLE_SERVICE_ACCOUNT_JSON |
| GPS ไม่ทำงาน | ไม่อนุญาต Location | เปิด Permission ในเบราว์เซอร์ |
| LIFF ไม่เปิด | LIFF ID ผิด | ตรวจสอบ LIFF ID ใน Console |
| ระบบช้า | Cache miss | ตรวจสอบ /debug/status |
| กดส่งฟอร์ม LIFF แล้ว error 401 Unauthorized | ลืมต่อ `?liffId=...` ใน Endpoint URL หรือ id token หมดอายุ | ตรวจสอบ Endpoint URL ตามหัวข้อ 5 แล้วลองเปิดฟอร์มใหม่ |
| กดส่งฟอร์มสำเร็จ แต่ไม่มีข้อความ "บันทึกข้อมูลเรียบร้อยแล้ว" ขึ้นในแชท | Push message quota ของ LINE OA เต็ม (แพลน Free มีโควต้าต่อเดือนจำกัด) หรือยังไม่ได้เป็นเพื่อนกับบอท | เช็คโควต้าใน LINE OA Manager > Settings > Response settings/Statistics หรืออัปเกรดแพลน |

---

## License

MIT License - ใช้สำหรับช่วยเหลือผู้ประสบภัยน้ำท่วม

## Contact

FLOODCARE AI Team
