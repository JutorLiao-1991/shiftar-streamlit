import streamlit as st
from streamlit_calendar import calendar
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json

# --- 1. 系統設定 ---
st.set_page_config(page_title="鳩特數理行政班表", page_icon="🏫", layout="wide")
st.title("🏫 鳩特數理行政班表")

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

# --- 2. 身份定義 (全域變數) ---
ADMINS = ["鳩特", "鳩婆"]
STAFFS = ["世軒", "竣揚", "暐傑"]
ALL_USERS = ADMINS + STAFFS

# --- 3. 核心功能函數 (修復 Bug 版) ---

# A. 清潔紀錄功能 (改良版：讀取快照)
def get_cleaning_status(area_name):
    # 直接讀取該區域的「最新狀態文件」，不需要用 Query 搜尋，解決索引報錯問題
    doc_ref = db.collection("latest_cleaning_status").document(area_name)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    
    # 1. 寫入歷史流水帳 (保留紀錄用)
    new_log = {
        "area": area,
        "staff": user,
        "timestamp": now
    }
    db.collection("cleaning_logs").add(new_log)
    
    # 2. 更新最新狀態 (快照)，讓讀取變快且不報錯
    status_update = {
        "area": area,
        "staff": user,
        "timestamp": now
    }
    db.collection("latest_cleaning_status").document(area).set(status_update)
    
    st.toast(f"🧹 {area} 已由 {user} 完成清掃！", icon="✨")

# B. 寫入班表/事項
def add_event_to_db(title, start, end, type, user, location=""):
    new_event = {
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "type": type,
        "staff": user,
        "location": location,
        "created_at": datetime.datetime.now()
    }
    db.collection("shifts").add(new_event)

# C. 讀取所有事件
def get_all_events():
    events = []
    try:
        docs = db.collection("shifts").stream()
        for doc in docs:
            data = doc.to_dict()
            color = "#3788d8"
            if data.get("type") == "shift":
                color = "#28a745"
                title_text = f"👨‍🏫 {data.get('title')}"
            elif data.get("type") == "notice":
                color = "#ffc107"
                title_text = f"📢 {data.get('title')}"
            else:
                title_text = data.get("title", "")

            events.append({
                "title": title_text,
                "start": data.get("start"),
                "end": data.get("end"),
                "color": color,
                "allDay": data.get("type") == "notice"
            })
    except:
        pass

    # 抓取國定假日
    year = datetime.date.today().year
    url = f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"
    try:
        resp = requests.get(url)
        for day in resp.json():
            if day.get('isHoliday'):
                events.append({
                    "title": f"🌴 {day['description']}",
                    "start": day['date'], 
                    "allDay": True,
                    "display": "background",
                    "backgroundColor": "#ffebee"
                })
    except:
        pass
    return events

# --- 4. 介面區塊：環境整潔計日器 (移至最上方，所有人可見) ---
st.subheader("🧹 環境整潔監控")
clean_cols = st.columns(4)
areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室"]

for i, area in enumerate(areas):
    status = get_cleaning_status(area)
    days_diff = "N/A"
    delta_days = 999
    last_staff = "無紀錄"
    
    if status:
        # 處理時間格式 (Firestore timestamp 轉 datetime)
        try:
            # 如果是字串格式 (舊資料)
            if isinstance(status['timestamp'], str):
                last_clean = datetime.datetime.fromisoformat(status['timestamp'])
            # 如果是 Firestore Datetime 物件
            else:
                last_clean = status['timestamp']
                # 確保有時區資訊或移除時區以便計算
                if last_clean.tzinfo:
                    last_clean = last_clean.replace(tzinfo=None)
            
            delta = datetime.datetime.now() - last_clean
            delta_days = delta.days
            days_diff = f"{delta_days} 天"
            last_staff = status.get('staff', '未知')
        except Exception as e:
            days_diff = "格式錯誤"

    # 決定顏色
    status_color = "green"
    icon = "✅"
    if delta_days > 7:
        status_color = "red"
        icon = "⚠️"
    elif delta_days > 3:
        status_color = "orange"
        icon = "🧹"

    with clean_cols[i]:
        # 使用 expander 讓卡片可以點開
        with st.expander(f"{icon} {area}", expanded=True):
            st.metric(label="未掃天數", value=days_diff, delta=f"上次: {last_staff}", delta_color="off")
            
            if delta_days > 7:
                st.markdown(f":red[該打掃了！]")

            # 點入後的選單
            st.markdown("---")
            cleaner_name = st.selectbox("誰掃的？", ALL_USERS, key=f"sel_{i}", index=0)
            if st.button("登記已掃拖", key=f"btn_{i}"):
                log_cleaning(area, cleaner_name)
                st.rerun()

st.divider()

# --- 5. 側邊欄與登入邏輯 ---
st.sidebar.header("👤 人員登入 (排課/公告用)")
selected_user = st.sidebar.selectbox("請選擇您的身份", ["請選擇"] + ALL_USERS)

is_logged_in = False
is_admin = False

if selected_user != "請選擇":
    if selected_user in ADMINS:
        password = st.sidebar.text_input("請輸入管理員密碼", type="password")
        if password == "150508":
            st.sidebar.success(f"歡迎管理員：{selected_user}")
            is_logged_in = True
            is_admin = True
        elif password:
            st.sidebar.error("密碼錯誤")
    else:
        st.sidebar.success(f"早安：{selected_user}")
        is_logged_in = True

# --- 6. 行事曆與後續功能 ---
# 讀取資料
all_events = get_all_events()

calendar_options = {
    "editable": False,
    "headerToolbar": {
        "left": "today prev,next",
        "center": "title",
        "right": "dayGridMonth,timeGridWeek"
    },
    "selectable": True,
    "initialView": "dayGridMonth",
}

# 顯示行事曆
cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick'])

# --- 7. 登入後的功能區 ---
if is_logged_in:
    col_left, col_right = st.columns([1, 2])
    
    with col_left:
        st.subheader("📝 新增項目")
        tab1, tab2 = st.tabs(["一般公告/交接", "排課 (管理員)"])
        
        with tab1:
            with st.form("notice_form"):
                notice_date = st.date_input("日期", datetime.date.today())
                notice_content = st.text_input("事項內容")
                if st.form_submit_button("發布公告"):
                    start_dt = datetime.datetime.combine(notice_date, datetime.time(9,0))
                    end_dt = datetime.datetime.combine(notice_date, datetime.time(10,0))
                    add_event_to_db(f"{selected_user}: {notice_content}", start_dt, end_dt, "notice", selected_user)
                    st.toast("公告已發布")
                    st.rerun()

        with tab2:
            if is_admin:
                with st.form("shift_form"):
                    s_date = st.date_input("上課日期")
                    s_start = st.time_input("開始時間", datetime.time(18,0))
                    s_end = st.time_input("結束時間", datetime.time(21,0))
                    s_teacher = st.text_input("授課師資", selected_user)
                    s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
                    s_title = st.text_input("課程名稱")
                    is_repeat = st.checkbox("每週重複 (自動排 4 週)")
                    
                    if st.form_submit_button("新增課程"):
                        start_dt = datetime.datetime.combine(s_date, s_start)
                        end_dt = datetime.datetime.combine(s_date, s_end)
                        full_title = f"[{s_location}] {s_teacher} - {s_title}"
                        
                        add_event_to_db(full_title, start_dt, end_dt, "shift", selected_user, s_location)
                        
                        if is_repeat:
                            for i in range(1, 4):
                                next_start = start_dt + datetime.timedelta(weeks=i)
                                next_end = end_dt + datetime.timedelta(weeks=i)
                                add_event_to_db(full_title, next_start, next_end, "shift", selected_user, s_location)
                                
                        st.toast("課程已安排！")
                        st.rerun()
            else:
                st.info("此區域僅限管理員使用")

    with col_right:
        st.subheader("📋 快速點名")
        selected_date = datetime.date.today()
        if cal_return and "dateClick" in cal_return:
            clicked_date_str = cal_return["dateClick"]["date"]
            selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()
            st.info(f"您選擇了日期：{selected_date}")
        
        with st.expander(f"{selected_date} 學生點名表", expanded=True):
            students = ["王小明", "李小華", "陳大文", "張三", "李四"] 
            attended = st.multiselect("出席學生", students)
            note = st.text_area("備註")
            if st.button("送出紀錄"):
                st.success("紀錄已送出")

else:
    # 未登入時顯示行事曆與提示
    st.info("💡 登入後可使用「排課」、「公告」與「點名」功能")
