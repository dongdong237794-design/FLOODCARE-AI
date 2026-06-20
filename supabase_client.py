def sync_water_levels_to_supabase():
    """
    ดึงข้อมูลจาก ThaiWater → ลบข้อมูลเก่าทั้งหมด → บันทึกข้อมูลใหม่
    ใช้สำหรับ Sync ข้อมูลระดับน้ำแบบเรียลไทม์
    """
    try:
        print("🔄 กำลัง Sync ข้อมูลระดับน้ำจาก ThaiWater...")

        # 1. ดึงข้อมูลล่าสุดจาก ThaiWater
        from bot_config import get_water_data_from_api  # เรียกใช้ฟังก์ชันเดิมของคุณ
        
        water_data = get_water_data_from_api()  # ดึงข้อมูลทั้งหมด

        if not water_data or len(water_data) == 0:
            print("❌ ไม่มีข้อมูลจาก ThaiWater")
            return False

        print(f"📥 ดึงข้อมูลสำเร็จ {len(water_data)} สถานี")

        # 2. ลบข้อมูลเก่าทั้งหมดใน Supabase
        delete_result = supabase.table("water_levels").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print("🗑️ ลบข้อมูลเก่าทั้งหมดเรียบร้อย")

        # 3. เตรียมข้อมูลสำหรับบันทึก (ปรับให้ตรงกับโครงสร้างตาราง)
        records = []
        for item in water_data:
            record = {
                "station_code": str(item.get("StationCode", "")),
                "name": str(item.get("Name", "ไม่ระบุ")),
                "river": str(item.get("River", "")),
                "province": str(item.get("Location", "")),
                "latitude": float(item.get("Lat", 0)),
                "longitude": float(item.get("Lon", 0)),
                "water_level": float(item.get("WaterLevel")) if item.get("WaterLevel") is not None else None,
                "bank_level": float(item.get("BankLevel")) if item.get("BankLevel") is not None else None,
                "situation": str(item.get("Situation", "ปกติ")),
                "trend": str(item.get("Trend", "คงที่")),
                "measured_at": str(item.get("Time", "")),
                "source": "thaiwater_v3"
            }
            records.append(record)

        # 4. บันทึกข้อมูลใหม่ทั้งหมด
        insert_result = supabase.table("water_levels").insert(records).execute()
        
        print(f"✅ Sync สำเร็จ! บันทึก {len(records)} สถานีลง Supabase")
        return True

    except Exception as e:
        print(f"❌ Sync ล้มเหลว: {e}")
        return False
