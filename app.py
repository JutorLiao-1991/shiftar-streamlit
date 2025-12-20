import streamlit as st
from streamlit_calendar import calendar
import datetime
from dateutil.relativedelta import relativedelta # 用來處理月份計算
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
# 預設時間選單 (09:00 - 22:00, 間隔 30 分)
TIME_SLOTS = []
for h in range(9, 22):
    TIME_SLOTS.append(datetime.time(h, 0))
    TIME_SLOTS.append(datetime.time(h, 30))
TIME_SLOTS.append(datetime.time(22, 0)) # 結束時間可以是 22:00

# --- 3. 資料庫存取函數 (新增：老師與學生管理) ---

# A. 取得/更新 老師設定 (包含薪資)
def get_teachers_data():
    docs = db.collection("teachers_config").stream()
    teachers = {}
    for doc in docs:
        teachers[doc.id] = doc.to_dict()
    return teachers

def save_teacher_data(name, rate):
    db.collection("teachers_config").document(name).set({"rate": rate})
    st.toast(f"已更新 {name} 的薪資設定")

# B. 取得/更新 學生名單
def get_students_list():
    doc = db.collection("settings").document("students").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return ["範例學生A", "範例學生B"]

def save_students_list(new_list):
    db.collection("settings").document("students").set({"list": new_list})
    st.toast("學生名單已更新")

# C. 既有功能
def get_cleaning_status(area_name):
    doc = db.collection("latest_cleaning_status").document(area_name).get()
    return doc.to_dict() if doc.exists else None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    db.collection("cleaning_logs").add({"area": area, "staff": user, "timestamp": now})
    db.collection("latest_cleaning_status").document(area).set({"area": area, "staff": user, "timestamp": now})
    st.toast(f"✨ {area} 清潔完成！", icon="🧹")

def add_event_to_db(title, start, end, type, user, location="", teacher_name=""):
    db.collection("shifts").add({
        "title": title, "start": start.isoformat(), "end": end.isoformat(),
        "type": type, "staff": user, "location": location, 
        "teacher": teacher_name, # 紀錄實際上課老師，方便算薪水
        "created_at": datetime.datetime.now()
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

# D. 薪資計算邏輯
def calculate_salary(year, month):
    start_date = datetime.datetime(year, month, 1)
    # 下個月1號減1秒 = 本月最後一刻
    end_date = start_date + relativedelta(months=1)
    
    # 從資料庫抓薪資設定
    teachers_cfg = get_teachers_data()
    
    # 抓取該月份所有排課
    # 注意：Firestore 字串比較日期簡單有效
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    
    docs = db.collection("shifts").where("type", "==", "shift")\
             .where("start", ">=", start_str).where("start", "<", end_str).stream()
    
    salary_report = {}
    
    for doc in docs:
        data = doc.to_dict()
        teacher = data.get("teacher", "未知") # 讀取排課時設定的老師
        
        # 排除鳩特家族
        if teacher in ["鳩特", "鳩婆", "未知"]:
            continue
            
        if teacher not in salary_report:
            salary_report[teacher] = {"count": 0, "rate": teachers_cfg.get(teacher, {}).get("rate", 0)}
            
        salary_report[teacher]["count"] += 1
        
    # 計算總額
    results = []
    total_payout = 0
    for name, info in salary_report.items():
        subtotal = info["count"] * info["rate"]
        total_payout += subtotal
        results.append({
            "姓名": name,
            "單價": info["rate"],
            "堂數": info["count"],
            "應發薪資": subtotal
        })
        
    return results, total_payout

# --- 4. 彈出視窗 UI (@st.dialog) ---

@st.dialog("👤 人員登入")
def show_login_dialog():
    # 這裡的選單改為動態讀取老師列表 + 管理員
    teachers_cfg = get_teachers_data()
    staff_list = list(teachers_cfg.keys()) # 從資料庫讀老師名字
    all_login_users = ADMINS + staff_list
    # 去除重複
    all_login_users = list(set(all_login_users))
    
    user = st.selectbox("請選擇您的身份", ["請選擇"] + all_login_users)
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
    tab1, tab2, tab3 = st.tabs(["📅 排課系統", "💰 薪資結算", "📝 資料設定"])
    
    # 1. 取得最新資料
    teachers_cfg = get_teachers_data()
    teacher_names = list(teachers_cfg.keys())
    # 確保當前使用者(如果是老師)也在名單內
    if st.session_state['user'] not in teacher_names and st.session_state['user'] not in ADMINS:
         teacher_names.append(st.session_state['user'])
    
    # TAB 1: 排課
    with tab1:
        c1, c2 = st.columns(2)
        s_date = c1.date_input("日期")
        # 師資選擇 (從資料庫讀取)
        s_teacher = c2.selectbox("授課師資", ["請選擇"] + ADMINS + teacher_names, index=0)
        
        c3, c4 = st.columns(2)
        # 時間選擇改為 Selectbox，限制範圍
        s_start = c3.selectbox("開始時間", TIME_SLOTS, index=18) # 預設 18:00 (index 18)
        s_end = c4.selectbox("結束時間", TIME_SLOTS, index=24) # 預設 21:00 (index 24)
        
        s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
        s_title = st.text_input("課程名稱")
        is_repeat = st.checkbox("每週重複 (自動排 4 週)")
        
        if st.button("新增課程", type="primary", use_container_width=True):
            if s_teacher == "請選擇":
                st.error("請選擇師資")
            elif s_start >= s_end:
                st.error("結束時間必須晚於開始時間")
            else:
                start_dt = datetime.datetime.combine(s_date, s_start)
                end_dt = datetime.datetime.combine(s_date, s_end)
                full_title = f"[{s_location}] {s_teacher} - {s_title}"
                
                # 寫入第一週
                add_event_to_db(full_title, start_dt, end_dt, "shift", st.session_state['user'], s_location, s_teacher)
                
                if is_repeat:
                    for i in range(1, 4):
                        next_start = start_dt + datetime.timedelta(weeks=i)
                        next_end = end_dt + datetime.timedelta(weeks=i)
                        add_event_to_db(full_title, next_start, next_end, "shift", st.session_state['user'], s_location, s_teacher)
                st.toast("課程已安排！")
                st.rerun()
    
    # TAB 2: 薪資結算
    with tab2:
        st.caption("計算該月份『Shift』類型的課程數量 (不包含鳩特/鳩婆)")
        col_m1, col_m2 = st.columns(2)
        q_year = col_m1.number_input("年份", value=datetime.date.today().year)
        q_month = col_m2.number_input("月份", value=datetime.date.today().month, min_value=1, max_value=12)
        
        if st.button("計算本月薪資"):
            results, total = calculate_salary(q_year, q_month)
            if results:
                st.dataframe(results, use_container_width=True)
                st.metric("本月總發放薪資", f"${total:,}")
            else:
                st.info("本月尚無須發放薪資的紀錄")

    # TAB 3: 資料設定 (師資與學生)
    with tab3:
        st.subheader("👨‍🏫 師資與薪資管理")
        with st.form("add_teacher"):
            c_t1, c_t2 = st.columns([2, 1])
            new_t_name = c_t1.text_input("老師姓名")
            new_t_rate = c_t2.number_input("單堂/時薪", min_value=0, step=100)
            if st.form_submit_button("新增/更新 老師資料"):
                if new_t_name:
                    save_teacher_data(new_t_name, new_t_rate)
                    st.rerun()
        
        # 顯示目前老師列表 (簡單版)
        st.caption("目前系統內的老師 (不含鳩特家族)")
        st.json(teachers_cfg, expanded=False)

        st.divider()

        st.subheader("🎓 學生名單管理")
        current_students = get_students_list()
        
        # 新增學生
        new_student = st.text_input("新增學生姓名 (按 Enter 新增)", key="new_stu_input")
        if new_student:
            if new_student not in current_students:
                current_students.append(new_student)
                save_students_list(current_students)
                st.rerun()
        
        # 刪除學生 (用多選框)
        to_remove = st.multiselect("選擇要移除的學生", current_students)
        if to_remove:
            if st.button("確認移除選取學生"):
                for s in to_remove:
                    current_students.remove(s)
                save_students_list(current_students)
                st.rerun()

# --- 5. 主介面邏輯 ---

# 自動登出 (01:00 - 05:00)
tz = pytz.timezone('Asia/Taipei')
now = datetime.datetime.now(tz)
if 1 <= now.hour < 5 and st.session_state['user'] is not None:
    st.session_state['user'] = None
    st.session_state['is_admin'] = False
    st.rerun()

# 標題與登入
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

# 環境整潔 (沿用)
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

# 按鈕區
if st.session_state['user']:
    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        if st.button("📝 新增公告 / 交接", use_container_width=True):
            show_notice_dialog()
    with btn_c2:
        if st.session_state['is_admin']:
            if st.button("⚙️ 管理員後台 (排課/薪資/設定)", type="primary", use_container_width=True):
                show_admin_dialog()

# 行事曆
all_events = get_all_events()
calendar_options = {
    "editable": False,
    "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
    "selectable": True,
    "initialView": "dayGridMonth",
}
cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick'])

# --- 6. 點名系統 ---
st.divider()
st.subheader("📋 每日點名")

selected_date = datetime.date.today()
if cal_return and "dateClick" in cal_return:
    clicked_date_str = cal_return["dateClick"]["date"].split("T")[0]
    selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()

st.info(f"正在進行 **{selected_date}** 的點名")

date_key = str(selected_date)
if date_key not in st.session_state:
    st.session_state[date_key] = {
        "absent": get_students_list(), # 動態讀取學生名單
        "present": [],
        "leave": []
    }

current_data = st.session_state[date_key]

if st.session_state['user']:
    with st.expander("展開點名表", expanded=True):
        col_absent, col_present, col_leave = st.columns(3)
        with col_absent:
            st.markdown("### 🔴 未到")
            for student in current_data['absent']:
                if st.button(f"👤 {student}", key=f"abs_{student}_{date_key}", use_container_width=True):
                    current_data['absent'].remove(student)
                    current_data['present'].append(student)
                    st.rerun()
        with col_present:
            st.markdown("### 🟢 已到")
            for student in current_data['present']:
                if st.button(f"✅ {student}", key=f"pre_{student}_{date_key}", type="primary", use_container_width=True):
                    current_data['present'].remove(student)
                    current_data['absent'].append(student)
                    st.rerun()
        with col_leave:
            st.markdown("### 🟡 請假/其他")
            move_to_leave = st.selectbox("選擇請假", ["選擇..."] + current_data['absent'], key=f"sel_leave_{date_key}")
            if move_to_leave != "選擇...":
                current_data['absent'].remove(move_to_leave)
                current_data['leave'].append(move_to_leave)
                st.rerun()
            for student in current_data['leave']:
                if st.button(f"🤒 {student}", key=f"lea_{student}_{date_key}", use_container_width=True):
                    current_data['leave'].remove(student)
                    current_data['absent'].append(student)
                    st.rerun()

    if st.button("💾 儲存今日點名紀錄", type="primary", use_container_width=True):
        st.success(f"已儲存：出席 {len(current_data['present'])} 人，請假 {len(current_data['leave'])} 人")
else:
    st.warning("請登入以進行點名")
