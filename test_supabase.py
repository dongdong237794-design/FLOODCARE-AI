from supabase_client import supabase

try:
    # ทดสอบดึงข้อมูล 3 แถวจากตาราง water_levels
    response = supabase.table("water_levels").select("*").limit(3).execute()
    
    print("✅ เชื่อมต่อ Supabase สำเร็จ!")
    print("จำนวนข้อมูลที่ดึงได้:", len(response.data))
    if response.data:
        print("ตัวอย่างข้อมูล:", response.data[0])
except Exception as e:
    print("❌ เกิดข้อผิดพลาด:", str(e))
