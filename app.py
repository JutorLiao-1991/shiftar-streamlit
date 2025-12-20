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

# --- 2. 身份與權限驗證 ---
# 定義人員名單
ADMINS = ["鳩特", "鳩婆"]
STAFFS = ["世軒", "竣揚", "暐傑"]
ALL_USERS = ADMINS + STAFFS

# 側邊欄登入
st.sidebar.header("👤 人員登入")
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

# --- 3. 核心功能函數 ---

# A. 清潔紀錄功能
def get_cleaning_status(area_name):
    # 從資料庫抓取該區域最後一次打掃的時間
    docs = db.collection("cleaning_logs").where("area", "==", area_name)\
             .order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream()
    for doc in docs:
        return doc.to_dict()
    return None

def log_cleaning(area, user):
    new_log = {
        "area": area,
        "staff": user,
        "timestamp": datetime.datetime.now()
    }
    db.collection("cleaning_logs").add(new_log)
    st.toast(f"🧹 {area} 已由 {user} 完成清掃！", icon="✨")

# B. 寫入班表/事項
def add_event_to_db(title, start, end, type, user, location=""):
    # type: 'shift' (正式排課), 'notice' (一般事項), 'rollcall' (點名紀錄)
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
    
    # 1. 抓取資料庫事件
    try:
        docs = db.collection("shifts").stream()
        for doc in docs:
            data = doc.to_dict()
            color = "#3788d8" # 預設藍色
            if data.get("type") == "shift":
                color = "#28a745" # 排課是綠色
                title_text = f"👨‍🏫 {data.get('title')}"
            elif data.get("type") == "notice":
                color = "#ffc107" # 公告是黃色
                title_text = f"📢 {data.get('title')}"
            else:
                title_text = data.get("title", "")

            events.append({
                "title": title_text,
                "start": data.get("start"),
                "end": data.get("end"),
                "color": color,
                "allDay": data.get("type") == "notice" # 公告預設全天
            })
    except:
        pass

    # 2. 抓取國定假日 (沿用之前的邏輯)
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

# --- 4. 介面區塊：環境整潔計日器 ---
if is_logged_in:
    st.subheader("🧹 環境整潔監控")
    clean_cols = st.columns(4)
    areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室"]
    
    for i, area in enumerate(areas):
        status = get_cleaning_status(area)
        days_diff = "尚未清掃"
        delta_days = 999
        
        if status:
            last_clean = datetime.datetime.fromisoformat(str(status['timestamp'])) # 處理時間格式
            # 簡單計算天數差
            delta = datetime.datetime.now() - last_clean.replace(tzinfo=None)
            delta_days = delta.days
            days_diff = f"{delta_days} 天"

        # 顯示指標
        with clean_cols[i]:
            st.metric(label=f"{area} 未掃天數", value=days_diff)
            # 超過 7 天顯示紅色警告
            if delta_days > 7:
                st.markdown(f":red[⚠️ 該打掃了！]")
            
            if st.button(f"我掃了{area}", key=f"btn_{i}"):
                log_cleaning(area, selected_user)
                st.rerun()
    
    st.divider()

# --- 5. 介面區塊：行事曆 ---
# 設定行事曆回調 (Callback)，讓我們知道使用者點了哪一天
calendar_options = {
    "editable": False,
    "headerToolbar": {
        "left": "today prev,next",
        "center": "title",
        "right": "dayGridMonth,timeGridWeek"
    },
    "selectable": True, # 允許點擊日期
    "initialView": "dayGridMonth",
}

# 讀取資料
all_events = get_all_events()

# 顯示行事曆並接收回傳值
cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick'])

# --- 6. 互動功能區 (新增事項 / 點名) ---

if is_logged_in:
    col_left, col_right = st.columns([1, 2])
    
    # 左邊：功能表單
    with col_left:
        st.subheader("📝 新增項目")
        tab1, tab2 = st.tabs(["一般公告/交接", "排課 (管理員)"])
        
        # TAB 1: 所有人可用
        with tab1:
            with st.form("notice_form"):
                notice_date = st.date_input("日期", datetime.date.today())
                notice_content = st.text_input("事項內容", placeholder="例如：明天要交接鑰匙、補習班消毒...")
                if st.form_submit_button("發布公告"):
                    start_dt = datetime.datetime.combine(notice_date, datetime.time(9,0))
                    end_dt = datetime.datetime.combine(notice_date, datetime.time(10,0))
                    add_event_to_db(f"{selected_user}: {notice_content}", start_dt, end_dt, "notice", selected_user)
                    st.toast("公告已發布")
                    st.rerun()

        # TAB 2: 管理員專用
        with tab2:
            if is_admin:
                with st.form("shift_form"):
                    s_date = st.date_input("上課日期")
                    s_start = st.time_input("開始時間", datetime.time(18,0))
                    s_end = st.time_input("結束時間", datetime.time(21,0))
                    s_teacher = st.text_input("授課師資", selected_user)
                    s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
                    s_title = st.text_input("課程名稱", placeholder="例如：國二數學")
                    
                    # 重複排課功能
                    is_repeat = st.checkbox("每週重複 (自動排 4 週)")
                    
                    if st.form_submit_button("新增課程"):
                        # 計算時間
                        start_dt = datetime.datetime.combine(s_date, s_start)
                        end_dt = datetime.datetime.combine(s_date, s_end)
                        
                        # 標題格式：[教室] 師資 - 課程
                        full_title = f"[{s_location}] {s_teacher} - {s_title}"
                        
                        # 寫入一次
                        add_event_to_db(full_title, start_dt, end_dt, "shift", selected_user, s_location)
                        
                        # 如果要重複
                        if is_repeat:
                            for i in range(1, 4): # 多加 3 週
                                next_start = start_dt + datetime.timedelta(weeks=i)
                                next_end = end_dt + datetime.timedelta(weeks=i)
                                add_event_to_db(full_title, next_start, next_end, "shift", selected_user, s_location)
                                
                        st.toast("課程已安排！")
                        st.rerun()
            else:
                st.info("此區域僅限 鳩特/鳩婆 使用")

    # 右邊：點名系統 (連動行事曆點擊)
    with col_right:
        st.subheader("📋 快速點名")
        
        # 偵測是否有其點擊日期
        selected_date = datetime.date.today() # 預設今天
        if cal_return and "dateClick" in cal_return:
            clicked_date_str = cal_return["dateClick"]["date"]
            selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()
            st.info(f"您選擇了日期：{selected_date}")
        else:
            st.caption("💡 提示：點擊左側行事曆的日期，可切換點名日期")

        # 簡單點名表單
        with st.expander(f"{selected_date} 學生點名表", expanded=True):
            # 這裡之後可以改成從資料庫讀學生名單
            students = ["王小明", "李小華", "陳大文", "張三", "李四"] 
            
            # 使用多選框來點名
            attended = st.multiselect("請選擇今日出席學生", students)
            note = st.text_area("課堂紀錄/備註", placeholder="例如：小明作業沒交、小華早退...")
            
            if st.button("送出點名紀錄"):
                # 這裡只是示範，實際上要寫入資料庫
                st.success(f"已紀錄 {len(attended)} 位學生出席！\n備註：{note}")
                # 未來功能：add_rollcall_to_db(...)

else:
    st.warning("👈 請先在左側欄登入以使用系統")
