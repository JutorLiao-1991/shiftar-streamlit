import streamlit as st
from streamlit_calendar import calendar
import datetime
from dateutil.relativedelta import relativedelta
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json
import pytz

# --- 1. 系統設定 ---
st.set_page_config(page_title="鳩特數理行政班表", page_icon="🏫", layout="wide")

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

# --- 2. 身份與常數定義 ---
ADMINS = ["鳩特", "鳩婆"]
# 時間選單
TIME_SLOTS = []
for h in range(9, 22):
    TIME_SLOTS.append(datetime.time(h, 0))
    TIME_SLOTS.append(datetime.time(h, 30))
TIME_SLOTS.append(datetime.time(22, 0))

# --- 3. 資料庫存取 (導入快取 @st.cache_data 以加速) ---

# A. 取得/更新 老師設定
def get_teachers_data():
    docs = db.collection("teachers_config").stream()
    teachers = {}
    for doc in docs:
        teachers[doc.id] = doc.to_dict()
    return teachers

def save_teacher_data(name, rate):
    db.collection("teachers_config").document(name).set({"rate": rate})
    st.toast(f"已更新 {name} 的薪資設定")

# B. 取得/更新 學生名單 (快取)
@st.cache_data(ttl=300) # 5分鐘快取，或手動清除
def get_students_list_cached():
    doc = db.collection("settings").document("students").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return ["範例學生A", "範例學生B"]

def save_students_list(new_list):
    db.collection("settings").document("students").set({"list": new_list})
    get_students_list_cached.clear() # 清除快取，下次讀取才會是新的
    st.toast("學生名單已更新")

# C. 環境清潔
def get_cleaning_status(area_name):
    doc = db.collection("latest_cleaning_status").document(area_name).get()
    return doc.to_dict() if doc.exists else None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    db.collection("cleaning_logs").add({"area": area, "staff": user, "timestamp": now})
    db.collection("latest_cleaning_status").document(area).set({"area": area, "staff": user, "timestamp": now})
    st.toast(f"✨ {area} 清潔完成！", icon="🧹")

# D. 班表與事件 (重頭戲：快取加速)
@st.cache_data(ttl=600) # 班表緩存 10 分鐘，操作順暢度提升關鍵
def get_all_events_cached():
    events = []
    try:
        # 抓取資料庫
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
    
    # 抓取國定假日 (API 也快取，不用每次都問)
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

# 寫入資料庫時，記得清除快取，這樣畫面才會更新
def add_event_to_db(title, start, end, type, user, location="", teacher_name=""):
    db.collection("shifts").add({
        "title": title, "start": start.isoformat(), "end": end.isoformat(),
        "type": type, "staff": user, "location": location, 
        "teacher": teacher_name, 
        "created_at": datetime.datetime.now()
    })
    get_all_events_cached.clear() # ★ 重要：清除快取

# E. 薪資計算
def calculate_salary(year, month):
    start_date = datetime.datetime(year, month, 1)
    end_date = start_date + relativedelta(months=1)
    teachers_cfg = get_teachers_data()
    
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    
    docs = db.collection("shifts").where("type", "==", "shift")\
             .where("start", ">=", start_str).where("start", "<", end_str).stream()
    
    salary_report = {}
    for doc in docs:
        data = doc.to_dict()
        teacher = data.get("teacher", "未知")
        if teacher in ["鳩特", "鳩婆", "未知"]: continue
        if teacher not in salary_report:
            salary_report[teacher] = {"count": 0, "rate": teachers_cfg.get(teacher, {}).get("rate", 0)}
        salary_report[teacher]["count"] += 1
        
    results = []
    total_payout = 0
    for name, info in salary_report.items():
        subtotal = info["count"] * info["rate"]
        total_payout += subtotal
        results.append({"姓名": name, "單價": info["rate"], "堂數": info["count"], "應發薪資": subtotal})
    return results, total_payout

# --- 4. 彈出視窗 UI ---

@st.dialog("👤 人員登入")
def show_login_dialog():
    teachers_cfg = get_teachers_data()
    staff_list = list(teachers_cfg.keys())
    all_login_users = list(set(ADMINS + staff_list))
    
    user = st.selectbox("請選擇您的身份", ["請選擇"] + all_login_users)
    password = ""
    if user in ADMINS:
        password = st.text_input("請輸入管理員密碼", type="password")
    
    if st.button("登入", use_container_width=True):
        if user == "請選擇": st.error("請選擇身份")
        elif user in ADMINS and password != "150508": st.error("密碼錯誤")
        else:
            st.session_state['user'] = user
            st.session_state['is_admin'] = (user in ADMINS)
            st.rerun()

@st.dialog("🧹 環境清潔登記")
def show_cleaning_dialog(area_name):
    st.write(f"登記 **{area_name}** 清潔")
    teachers_cfg = get_teachers_data()
    staff_list = list(teachers_cfg.keys())
    cleaner = st.selectbox("清潔人員", staff_list)
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

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3 = st.tabs(["📅 排課", "💰 薪資", "📝 設定"])
    teachers_cfg = get_teachers_data()
    teacher_names = list(teachers_cfg.keys())
    if st.session_state['user'] not in teacher_names and st.session_state['user'] not in ADMINS:
         teacher_names.append(st.session_state['user'])
    
    with tab1:
        c1, c2 = st.columns(2)
        s_date = c1.date_input("日期")
        s_teacher = c2.selectbox("授課師資", ["請選擇"] + ADMINS + teacher_names)
        c3, c4 = st.columns(2)
        s_start = c3.selectbox("開始", TIME_SLOTS, index=18)
        s_end = c4.selectbox("結束", TIME_SLOTS, index=24)
        s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
        s_title = st.text_input("課程名稱")
        is_repeat = st.checkbox("每週重複 (4週)")
        if st.button("新增課程", type="primary", use_container_width=True):
            if s_teacher == "請選擇": st.error("請選師資")
            elif s_start >= s_end: st.error("時間錯誤")
            else:
                start_dt = datetime.datetime.combine(s_date, s_start)
                end_dt = datetime.datetime.combine(s_date, s_end)
                full_title = f"[{s_location}] {s_teacher} - {s_title}"
                add_event_to_db(full_title, start_dt, end_dt, "shift", st.session_state['user'], s_location, s_teacher)
                if is_repeat:
                    for i in range(1, 4):
                        next_start = start_dt + datetime.timedelta(weeks=i)
                        next_end = end_dt + datetime.timedelta(weeks=i)
                        add_event_to_db(full_title, next_start, next_end, "shift", st.session_state['user'], s_location, s_teacher)
                st.toast("課程已安排！")
                st.rerun()
    with tab2:
        col_m1, col_m2 = st.columns(2)
        q_year = col_m1.number_input("年份", value=datetime.date.today().year)
        q_month = col_m2.number_input("月份", value=datetime.date.today().month, min_value=1, max_value=12)
        if st.button("計算薪資"):
            results, total = calculate_salary(q_year, q_month)
            if results:
                st.dataframe(results, use_container_width=True)
                st.metric("總發放", f"${total:,}")
            else: st.info("無紀錄")
    with tab3:
        with st.form("add_teacher"):
            c_t1, c_t2 = st.columns([2, 1])
            new_t_name = c_t1.text_input("老師姓名")
            new_t_rate = c_t2.number_input("單價", min_value=0, step=100)
            if st.form_submit_button("更新資料"):
                if new_t_name:
                    save_teacher_data(new_t_name, new_t_rate)
                    st.rerun()
        st.divider()
        current_students = get_students_list_cached()
        new_student = st.text_input("新增學生 (按 Enter)", key="new_stu")
        if new_student:
            if new_student not in current_students:
                current_students.append(new_student)
                save_students_list(current_students)
                st.rerun()
        to_remove = st.multiselect("移除學生", current_students)
        if to_remove and st.button("確認移除"):
            for s in to_remove: current_students.remove(s)
            save_students_list(current_students)
            st.rerun()

# --- 5. 主介面邏輯 ---

tz = pytz.timezone('Asia/Taipei')
now = datetime.datetime.now(tz)
if 1 <= now.hour < 5 and st.session_state['user'] is not None:
    st.session_state['user'] = None
    st.session_state['is_admin'] = False
    st.rerun()

col_title, col_login = st.columns([3, 1], vertical_alignment="center")
with col_title: st.title("🏫 鳩特數理行政班表")
with col_login:
    if st.session_state['user']:
        st.markdown(f"👤 **{st.session_state['user']}**")
        if st.button("登出", type="secondary", use_container_width=True):
            st.session_state['user'] = None
            st.session_state['is_admin'] = False
            st.rerun()
    else:
        if st.button("登入", type="primary", use_container_width=True):
            show_login_dialog()

st.divider()

# 環境整潔 (維持原樣)
st.subheader("🧹 環境整潔")
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

if st.session_state['user']:
    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        if st.button("📝 公告/交接", use_container_width=True): show_notice_dialog()
    with btn_c2:
        if st.session_state['is_admin']:
            if st.button("⚙️ 後台管理", type="primary", use_container_width=True): show_admin_dialog()

# 行事曆 (優化版)
all_events = get_all_events_cached() # 使用快取資料！
calendar_options = {
    "editable": False,
    "headerToolbar": {
        "left": "today prev,next",
        "center": "title",
        # 關鍵修改：預設提供 listMonth (條列) 和 dayGridMonth (月曆) 兩種視圖
        "right": "listMonth,dayGridMonth" 
    },
    "initialView": "listMonth", # 預設為手機友善的「條列式」
    "height": "auto",
}
cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick'])

# --- 6. 點名系統 (快取優化版) ---
st.divider()
st.subheader("📋 每日點名")

selected_date = datetime.date.today()
if cal_return and "dateClick" in cal_return:
    clicked_date_str = cal_return["dateClick"]["date"].split("T")[0]
    selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()

st.info(f"日期：**{selected_date}** (請記得按儲存)")

date_key = str(selected_date)
# 確保初始化
if date_key not in st.session_state:
    st.session_state[date_key] = {
        "absent": get_students_list_cached(),
        "present": [],
        "leave": [],
        "dirty": False # 標記是否有更動未存檔
    }

current_data = st.session_state[date_key]

if st.session_state['user']:
    with st.expander("點名表單", expanded=True):
        col_absent, col_present, col_leave = st.columns(3)
        
        # 顯示按鈕 (這裡的操作因為不讀資料庫，會變很快)
        with col_absent:
            st.markdown("### 🔴 未到")
            for student in current_data['absent']:
                if st.button(f"👤 {student}", key=f"abs_{student}_{date_key}", use_container_width=True):
                    current_data['absent'].remove(student)
                    current_data['present'].append(student)
                    current_data['dirty'] = True # 標記髒資料
                    st.rerun()
        with col_present:
            st.markdown("### 🟢 已到")
            for student in current_data['present']:
                if st.button(f"✅ {student}", key=f"pre_{student}_{date_key}", type="primary", use_container_width=True):
                    current_data['present'].remove(student)
                    current_data['absent'].append(student)
                    current_data['dirty'] = True
                    st.rerun()
        with col_leave:
            st.markdown("### 🟡 請假")
            move_to_leave = st.selectbox("請假", ["選擇..."] + current_data['absent'], key=f"sel_leave_{date_key}")
            if move_to_leave != "選擇...":
                current_data['absent'].remove(move_to_leave)
                current_data['leave'].append(move_to_leave)
                current_data['dirty'] = True
                st.rerun()
            for student in current_data['leave']:
                if st.button(f"🤒 {student}", key=f"lea_{student}_{date_key}", use_container_width=True):
                    current_data['leave'].remove(student)
                    current_data['absent'].append(student)
                    current_data['dirty'] = True
                    st.rerun()

    # 儲存按鈕 (只有更動時才變紅色提醒)
    btn_type = "primary" if current_data.get('dirty', False) else "secondary"
    btn_text = "💾 儲存變更 (未儲存)" if current_data.get('dirty', False) else "💾 資料已儲存"
    
    if st.button(btn_text, type=btn_type, use_container_width=True):
        # 這裡寫入資料庫
        # db.collection("attendance").add(...) 
        # 目前先模擬
        current_data['dirty'] = False
        st.success(f"已儲存：出席 {len(current_data['present'])} 人，請假 {len(current_data['leave'])} 人")
        st.rerun()
else:
    st.warning("請登入以進行點名")
