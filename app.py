import streamlit as st
from streamlit_calendar import calendar
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json

# --- 1. 設定網頁與資料庫連線 ---
st.set_page_config(page_title="Shiftar 排班表", page_icon="📅", layout="wide")
st.title("📅 Shiftar 補習班排班系統")

# 初始化 Firebase
if not firebase_admin._apps:
    try:
        # 這裡做了修改：優先讀取 Streamlit 的秘密倉庫 (Secrets)，如果沒有才找本地檔案
        # 這樣你的程式碼既能在雲端跑，也能在電腦跑
        if "firebase_key" in st.secrets:
            key_dict = json.loads(st.secrets["firebase_key"])
            cred = credentials.Certificate(key_dict)
        else:
            cred = credentials.Certificate("service_account.json")
            
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"資料庫連線失敗: {e}")

# 取得資料庫控制權
db = firestore.client()

# --- 2. 側邊欄與登入 ---
users = ["王老師", "李助教", "櫃台小美"]
current_user = st.sidebar.selectbox("請選擇您的身分", users)
st.sidebar.success(f"目前登入：{current_user}")

# --- 3. 核心功能函數 ---

# 功能 A: 抓取台灣國定假日
@st.cache_data
def get_taiwan_holidays():
    year = datetime.date.today().year
    url = f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"
    try:
        resp = requests.get(url)
        data = resp.json()
        events = []
        for day in data:
            if day.get('isHoliday'):
                events.append({
                    "title": f"🌴 {day['description']}",
                    "start": day['date'], 
                    "allDay": True,
                    "backgroundColor": "#FFCDD2", # 粉紅色
                    "borderColor": "#EF9A9A",
                    "display": "background"
                })
        return events
    except Exception:
        return []

# 功能 B: 寫入資料庫
def add_shift_to_db(title, start, end, location):
    new_shift = {
        "title": f"{title} ({location})",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "staff": title,
        "location": location,
        "created_at": datetime.datetime.now()
    }
    db.collection("shifts").add(new_shift)

# 功能 C: 從資料庫讀取
def get_shifts_from_db():
    try:
        shifts_ref = db.collection("shifts")
        docs = shifts_ref.stream()
        events = []
        for doc in docs:
            data = doc.to_dict()
            events.append({
                "title": data.get("title", "未知排班"),
                "start": data.get("start"),
                "end": data.get("end"),
                "color": "#42A5F5" if "老師" in data.get("title", "") else "#66BB6A"
            })
        return events
    except Exception:
        return []

# --- 4. 準備資料並顯示 ---

# 1. 抓假日
holidays = get_taiwan_holidays()
# 2. 抓班表
db_shifts = get_shifts_from_db()
# 3. 合併在一起
all_events = holidays + db_shifts

calendar_options = {
    "editable": False,
    "headerToolbar": {
        "left": "today prev,next",
        "center": "title",
        "right": "dayGridMonth,timeGridWeek,timeGridDay"
    },
    "initialView": "dayGridMonth",
}

st.markdown("### 📅 目前班表")
calendar(events=all_events, options=calendar_options)

# --- 5. 新增排班表單 ---
st.divider()
st.subheader("📝 新增排班")

with st.form("shift_form"):
    col1, col2, col3 = st.columns(3)
    with col1:
        date_input = st.date_input("日期", datetime.date.today())
    with col2:
        start_input = st.time_input("開始時間", datetime.time(18, 0))
    with col3:
        end_input = st.time_input("結束時間", datetime.time(21, 0))
    
    location = st.text_input("地點/教室", "A教室")
    
    if st.form_submit_button("送出排班"):
        start_dt = datetime.datetime.combine(date_input, start_input)
        end_dt = datetime.datetime.combine(date_input, end_input)
        
        add_shift_to_db(current_user, start_dt, end_dt, location)
        
        st.toast(f"已新增：{date_input} {current_user}", icon="✅")
        st.rerun()
