import streamlit as st
from streamlit_calendar import calendar
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json
import pytz # 用來處理時區

# --- 1. 系統設定 ---
st.set_page_config(page_title="鳩特數理行政班表", page_icon="🏫", layout="wide")

# 初始化 Session State (用於點名系統暫存)
if 'attendance_state' not in st.session_state:
    st.session_state['attendance_state'] = {}
if 'user' not in st.session_state:
    st.session_state['user'] = None
if 'is_admin' not in st.session_state:
    st.session_state['is_admin'] = False

# 初始化 Firebase
if not firebase_admin._apps:
    try:
        if "firebase_key" in st.secrets:
            key_dict = json.loads(st.secrets["firebase_key"])
            cred = credentials.Certificate(key_dict)
        else:
            cred = credentials.Certificate("service_account.json")
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"資料庫連線失敗: {e}")

db = firestore.client()

# --- 2. 身份與全域變數 ---
ADMINS = ["鳩特", "鳩婆"]
STAFFS = ["世軒", "竣揚", "暐傑"]
ALL_USERS = ADMINS + STAFFS
CLEANERS = STAFFS
STUDENTS_LIST = ["王小明", "李小華", "陳大文", "張三", "李四", "測試學生A", "測試學生B"] # 之後可從 DB 讀取

# --- 3. 核心邏輯函數 ---

# A. 自動登出機制 (01:00 AM 強制登出)
def check_auto_logout():
    tw_tz = pytz.timezone('Asia/Taipei')
    now = datetime.datetime.now(tw_tz)
    # 如果現在時間大於 01:00 且 小於 05:00 (避免整天無法登入)，且目前是登入狀態
    if 1 <= now.hour < 5 and st.session_state['user'] is not None:
        st.session_state['user'] = None
        st.session_state['is_admin'] = False
        st.rerun()

# B. Firebase 操作
def get_cleaning_status(area_name):
    doc = db.collection("latest_cleaning_status").document(area_name).get()
    return doc.to_dict() if doc.exists else None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    db.collection("cleaning_logs").add({"area": area, "staff": user, "timestamp": now})
    db.collection("latest_cleaning_status").document(area).set({"area": area, "staff": user, "timestamp": now})
    st.toast(f"✨ {area} 清潔完成！", icon="🧹")

def add_event_to_db(title, start, end, type, user, location=""):
    db.collection("shifts").add({
        "title": title, "start": start.isoformat(), "end": end.isoformat(),
        "type": type, "staff": user, "location": location, "created_at": datetime.datetime.now()
    })

def get_all_events():
    events = []
    try:
        docs = db.collection("shifts").stream()
        for doc in docs:
            data = doc.to_dict()
            color = "#3788d8"
            title_text = data.get("title", "")
            if data.get("type") == "shift":
                color = "#28a745"
                title_text = f"👨‍🏫 {title_text}"
            elif data.get("type") == "notice":
                color = "#ffc107"
                title_text = f"📢 {title_text}"
            
            events.append({
                "title": title_text, "start": data.get("start"), "end": data.get("end"),
                "color": color, "allDay": data.get("type") == "notice"
            })
    except: pass
    
    # 國定假日
    try:
        year = datetime.date.today().year
        resp = requests.get(f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json").json()
        for day in resp:
            if day.get('isHoliday'):
                events.append({
                    "title": f"🌴 {day['description']}", "start": day['date'], 
                    "allDay": True, "display": "background", "backgroundColor": "#ffebee"
                })
    except: pass
    return events

# --- 4. 彈出視窗 UI (@st.dialog) ---

@st.dialog("👤 人員登入")
def show_login_dialog():
    user = st.selectbox("請選擇您的身份", ["請選擇"] + ALL_USERS)
    password = ""
    if user in ADMINS:
        password = st.text_input("請輸入管理員密碼", type="password")
    
    if st.button("登入", use_container_width=True):
        if user == "請選擇":
            st.error("請選擇身份")
        elif user in ADMINS and password != "150508":
            st.error("密碼錯誤")
        else:
            st.session_state['user'] = user
            st.session_state['is_admin'] = (user in ADMINS)
            st.rerun()

@st.dialog("🧹 環境清潔登記")
def show_cleaning_dialog(area_name):
    st.write(f"登記 **{area_name}** 清潔")
    cleaner = st.selectbox("清潔人員", CLEANERS)
    if st.button("確認已掃拖", use_container_width=True):
        log_cleaning(area_name, cleaner)
        st.rerun()

@st.dialog("📢 新增公告 / 交接")
def show_notice_dialog():
    notice_date = st.date_input("日期", datetime.date.today())
    notice_content = st.text_area("事項內容", height=100)
    if st.button("發布公告", use_container_width=True):
        start_dt = datetime.datetime.combine(notice_date, datetime.time(9,0))
        end_dt = datetime.datetime.combine(notice_date, datetime.time(10,0))
        add_event_to_db(f"{st.session_state['user']}: {notice_content}", start_dt, end_dt, "notice", st.session_state['user'])
        st.toast("公告已發布")
        st.rerun()

@st.dialog("📅 排課系統 (管理員)")
def show_shift_dialog():
    c1, c2 = st.columns(2)
    s_date = c1.date_input("日期")
    s_teacher = c2.text_input("師資", st.session_state['user'])
    c3, c4 = st.columns(2)
    s_start = c3.time_input("開始", datetime.time(18,0))
    s_end = c4.time_input("結束", datetime.time(21,0))
    
    s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
    s_title = st.text_input("課程名稱")
    is_repeat = st.checkbox("每週重複 (自動排 4 週)")
    
    if st.button("新增課程", use_container_width=True):
        start_dt = datetime.datetime.combine(s_date, s_start)
        end_dt = datetime.datetime.combine(s_date, s_end)
        full_title = f"[{s_location}] {s_teacher} - {s_title}"
        add_event_to_db(full_title, start_dt, end_dt, "shift", st.session_state['user'], s_location)
        if is_repeat:
            for i in range(1, 4):
                next_start = start_dt + datetime.timedelta(weeks=i)
                next_end = end_dt + datetime.timedelta(weeks=i)
                add_event_to_db(full_title, next_start, next_end, "shift", st.session_state['user'], s_location)
        st.toast("課程已安排！")
        st.rerun()

# --- 5. 主介面邏輯 ---

# 執行自動登出檢查
check_auto_logout()

# 標題與登入按鈕區 (使用 columns 排版)
col_title, col_login = st.columns([3, 1], vertical_alignment="center")
with col_title:
    st.title("🏫 鳩特數理行政班表")
with col_login:
    if st.session_state['user']:
        st.markdown(f"👤 **{st.session_state['user']}**")
        if st.button("登出", type="secondary", use_container_width=True):
            st.session_state['user'] = None
            st.session_state['is_admin'] = False
            st.rerun()
    else:
        if st.button("登入系統", type="primary", use_container_width=True):
            show_login_dialog()

st.divider()

# 環境整潔監控 (改良版)
st.subheader("🧹 環境整潔監控")
clean_cols = st.columns(4)
areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室"]

for i, area in enumerate(areas):
    status = get_cleaning_status(area)
    days_diff = "N/A"
    delta_days = 999
    if status:
        try:
            ts = status['timestamp']
            if isinstance(ts, str): ts = datetime.datetime.fromisoformat(ts)
            if ts.tzinfo: ts = ts.replace(tzinfo=None)
            delta_days = (datetime.datetime.now() - ts).days
            days_diff = f"{delta_days} 天"
        except: pass

    with clean_cols[i]:
        st.caption(area)
        color = "green" if delta_days <= 3 else "orange" if delta_days <= 7 else "red"
        st.markdown(f"### :{color}[{days_diff}]")
        if st.button("登記", key=f"clean_{i}", use_container_width=True):
            show_cleaning_dialog(area)

st.divider()

# 操作按鈕區 (僅登入顯示)
if st.session_state['user']:
    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        if st.button("📝 新增公告 / 交接", use_container_width=True):
            show_notice_dialog()
    with btn_c2:
        if st.session_state['is_admin']:
            if st.button("📅 新增排課", use_container_width=True):
                show_shift_dialog()

# 行事曆
all_events = get_all_events()
calendar_options = {
    "editable": False,
    "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
    "selectable": True,
    "initialView": "dayGridMonth",
}
cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick'])

# --- 6. 點名系統 (手機版特別優化) ---
st.divider()
st.subheader("📋 每日點名")

# 1. 取得選擇的日期
selected_date = datetime.date.today()
if cal_return and "dateClick" in cal_return:
    # --- BUG FIX: 這裡加了 split("T")[0] 來處理可能的時間字串 ---
    clicked_date_str = cal_return["dateClick"]["date"].split("T")[0]
    selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()

st.info(f"正在進行 **{selected_date}** 的點名")

# 2. 準備點名資料 (使用 Session State 暫存，避免畫面重整資料不見)
date_key = str(selected_date)
if date_key not in st.session_state['attendance_state']:
    # 預設所有人都在「未到」
    st.session_state['attendance_state'][date_key] = {
        "absent": STUDENTS_LIST.copy(),
        "present": [],
        "leave": []
    }

current_data = st.session_state['attendance_state'][date_key]

# 3. 三欄式點名介面 (手機上 Columns 會自動變成直排，很好按)
if st.session_state['user']:
    with st.expander("展開點名表", expanded=True):
        col_absent, col_present, col_leave = st.columns(3)

        # 欄位 1: 未到 (點擊 -> 變已到)
        with col_absent:
            st.markdown("### 🔴 未到")
            st.caption("點擊名字移至已到")
            for student in current_data['absent']:
                if st.button(f"👤 {student}", key=f"abs_{student}_{date_key}", use_container_width=True):
                    current_data['absent'].remove(student)
                    current_data['present'].append(student)
                    st.rerun()

        # 欄位 2: 已到 (點擊 -> 變未到)
        with col_present:
            st.markdown("### 🟢 已到")
            st.caption("點擊名字取消")
            for student in current_data['present']:
                if st.button(f"✅ {student}", key=f"pre_{student}_{date_key}", type="primary", use_container_width=True):
                    current_data['present'].remove(student)
                    current_data['absent'].append(student)
                    st.rerun()

        # 欄位 3: 請假 (手動選擇)
        with col_leave:
            st.markdown("### 🟡 請假/其他")
            # 這裡用選單來移動，因為「未到」可能很多，直接移動到請假比較快
            move_to_leave = st.selectbox("選擇請假學生", ["選擇..."] + current_data['absent'], key=f"sel_leave_{date_key}")
            if move_to_leave != "選擇...":
                current_data['absent'].remove(move_to_leave)
                current_data['leave'].append(move_to_leave)
                st.rerun()
            
            # 顯示請假名單 (點擊還原)
            for student in current_data['leave']:
                if st.button(f"🤒 {student}", key=f"lea_{student}_{date_key}", use_container_width=True):
                    current_data['leave'].remove(student)
                    current_data['absent'].append(student)
                    st.rerun()

    # 送出按鈕
    if st.button("💾 儲存今日點名紀錄", type="primary", use_container_width=True):
        # 這裡將資料寫入 Firebase
        # db.collection("attendance").add({ ... })
        st.success(f"已儲存：出席 {len(current_data['present'])} 人，請假 {len(current_data['leave'])} 人")

else:
    st.warning("請登入以進行點名")
