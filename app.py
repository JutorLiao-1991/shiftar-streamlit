import streamlit as st
from streamlit_calendar import calendar
import datetime
from dateutil.relativedelta import relativedelta
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json
import pytz
import pandas as pd

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

# --- 2. 常數與設定 ---
ADMINS = ["鳩特", "鳩婆"]
STAFF_PASSWORD = "88888888"
ADMIN_PASSWORD = "150508"

TIME_OPTIONS = []
for h in range(9, 23):
    TIME_OPTIONS.append(f"{h:02d}:00")
    if h != 22:
        TIME_OPTIONS.append(f"{h:02d}:30")

# --- 3. 資料庫存取 (快取層) ---

def get_unique_course_names():
    doc = db.collection("settings").document("courses").get()
    if doc.exists:
        return doc.to_dict().get("list", ["國一數學", "國二數學", "國三數學", "高一數學", "國二理化"])
    return ["國一數學", "國二數學", "國三數學", "高一數學", "國二理化"]

def save_course_name(course_name):
    current = get_unique_course_names()
    if course_name not in current:
        current.append(course_name)
        db.collection("settings").document("courses").set({"list": current})

def get_teachers_data():
    docs = db.collection("teachers_config").stream()
    teachers = {}
    for doc in docs:
        teachers[doc.id] = doc.to_dict()
    return teachers

def save_teacher_data(name, rate):
    db.collection("teachers_config").document(name).set({"rate": rate})
    st.toast(f"已更新 {name} 的薪資設定")

@st.cache_data(ttl=300)
def get_students_data_cached():
    doc = db.collection("settings").document("students_detail").get()
    if doc.exists:
        return doc.to_dict().get("data", [])
    return []

def save_students_data(new_data_list):
    db.collection("settings").document("students_detail").set({"data": new_data_list})
    get_students_data_cached.clear()
    st.toast("學生名單已更新")

# ★ 關鍵修正：這裡加入了 sanitize 機制，解決報錯
@st.cache_data(ttl=600)
def get_all_events_cached():
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
                category = data.get("category", "其他")
                title_text = f"[{category}] {title_text}"
                if category == "調課": color = "#d63384"
                elif category == "考試": color = "#dc3545"
                elif category == "活動": color = "#0d6efd"
                else: color = "#ffc107"
            
            # --- 修正開始：將 datetime 物件轉為字串 ---
            sanitized_props = {}
            for k, v in data.items():
                # 如果值是 datetime 或 date 類型，轉成字串
                if isinstance(v, (datetime.datetime, datetime.date)):
                    sanitized_props[k] = str(v)
                else:
                    sanitized_props[k] = v
            # --- 修正結束 ---

            events.append({
                "id": doc.id,
                "title": title_text, 
                "start": data.get("start"), 
                "end": data.get("end"),
                "color": color, 
                "allDay": data.get("type") == "notice",
                "extendedProps": sanitized_props # 使用淨化後的資料
            })
    except: pass
    
    try:
        year = datetime.date.today().year
        resp = requests.get(f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json").json()
        for day in resp:
            if day.get('isHoliday'):
                events.append({
                    "title": f"🌴 {day['description']}", "start": day['date'], 
                    "allDay": True, "display": "background", "backgroundColor": "#ffebee",
                    "editable": False
                })
    except: pass
    return events

def add_event_to_db(title, start, end, type, user, location="", teacher_name="", category=""):
    db.collection("shifts").add({
        "title": title, "start": start.isoformat(), "end": end.isoformat(),
        "type": type, "staff": user, "location": location, 
        "teacher": teacher_name, "category": category,
        "created_at": datetime.datetime.now()
    })
    get_all_events_cached.clear()

def update_event_in_db(doc_id, update_dict):
    db.collection("shifts").document(doc_id).update(update_dict)
    get_all_events_cached.clear()
    st.toast("更新成功！")

def delete_event_from_db(doc_id):
    db.collection("shifts").document(doc_id).delete()
    get_all_events_cached.clear()
    st.toast("刪除成功！")

def get_cleaning_status(area_name):
    doc = db.collection("latest_cleaning_status").document(area_name).get()
    return doc.to_dict() if doc.exists else None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    db.collection("cleaning_logs").add({"area": area, "staff": user, "timestamp": now})
    db.collection("latest_cleaning_status").document(area).set({"area": area, "staff": user, "timestamp": now})
    st.toast(f"✨ {area} 清潔完成！", icon="🧹")

# --- 4. 彈出視窗 UI ---

@st.dialog("👤 人員登入")
def show_login_dialog():
    teachers_cfg = get_teachers_data()
    staff_list = list(teachers_cfg.keys())
    DEFAULT_STAFFS = ["世軒", "竣揚", "暐傑"]
    all_users = list(set(ADMINS + DEFAULT_STAFFS + staff_list))
    
    user = st.selectbox("請選擇您的身份", ["請選擇"] + all_users)
    password = st.text_input("請輸入密碼", type="password")
    
    if st.button("登入", use_container_width=True):
        if user == "請選擇": 
            st.error("請選擇身份")
            return

        is_valid = False
        is_admin = False
        
        if user in ADMINS:
            if password == ADMIN_PASSWORD:
                is_valid = True
                is_admin = True
        else:
            if password == STAFF_PASSWORD:
                is_valid = True
        
        if is_valid:
            st.session_state['user'] = user
            st.session_state['is_admin'] = is_admin
            st.rerun()
        else:
            st.error("密碼錯誤")

@st.dialog("✏️ 編輯/刪除 行程")
def show_edit_event_dialog(event_id, props):
    st.write(f"正在編輯：**{props.get('title', '')}**")
    
    if props.get('type') == 'shift':
        new_title = st.text_input("標題", props.get('title'))
        st.caption("💡 如需修改時間或師資，建議刪除後重新排課")
        
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_title})
            st.rerun()
        if col2.button("🗑️ 刪除此課程", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()
            
    elif props.get('type') == 'notice':
        cat_opts = ["調課", "考試", "活動", "其他"]
        curr_cat = props.get('category', '其他')
        idx = cat_opts.index(curr_cat) if curr_cat in cat_opts else 3
        
        new_cat = st.selectbox("分類", cat_opts, index=idx)
        # title 存內容
        new_content = st.text_area("內容", props.get('title')) 
        
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_content, "category": new_cat})
            st.rerun()
        if col2.button("🗑️ 刪除此公告", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()

@st.dialog("📢 新增公告 / 交接")
def show_notice_dialog():
    notice_date = st.date_input("日期", datetime.date.today())
    category = st.selectbox("分類 (必選)", ["調課", "考試", "活動", "其他"])
    notice_content = st.text_area("事項內容", placeholder="請輸入詳細內容...")
    
    if st.button("發布公告", use_container_width=True):
        start_dt = datetime.datetime.combine(notice_date, datetime.time(9,0))
        end_dt = datetime.datetime.combine(notice_date, datetime.time(10,0))
        add_event_to_db(notice_content, start_dt, end_dt, "notice", st.session_state['user'], category=category)
        st.toast("公告已發布")
        st.rerun()

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3 = st.tabs(["📅 智慧排課", "💰 薪資", "📝 資料設定"])
    
    with tab1:
        st.subheader("批次排課系統")
        c1, c2 = st.columns(2)
        start_date = c1.date_input("首堂課日期")
        weeks_count = c2.number_input("排課週數", min_value=1, value=12)
        
        teachers_cfg = get_teachers_data()
        teacher_names = list(teachers_cfg.keys()) + ADMINS
        s_teacher = st.selectbox("授課師資", ["請選擇"] + list(set(teacher_names)))
        
        c3, c4 = st.columns(2)
        t_start_str = c3.selectbox("開始時間", TIME_OPTIONS, index=18)
        t_end_str = c4.selectbox("結束時間", TIME_OPTIONS, index=24)
        
        course_options = get_unique_course_names()
        s_course_name = st.selectbox("課程/班別", course_options + ["+ 新增班別..."])
        if s_course_name == "+ 新增班別...":
            s_course_name = st.text_input("輸入新班別名稱")
            
        s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
        
        if "preview_schedule" not in st.session_state:
            st.session_state['preview_schedule'] = None

        if st.button("🔍 檢查時段與假日"):
            if s_teacher == "請選擇":
                st.error("請選擇師資")
            else:
                save_course_name(s_course_name)
                preview = []
                year = start_date.year
                holidays = {}
                try:
                    resp = requests.get(f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json").json()
                    for d in resp:
                        if d['isHoliday']: holidays[d['date']] = d['description']
                except: pass

                t_start = datetime.datetime.strptime(t_start_str, "%H:%M").time()
                t_end = datetime.datetime.strptime(t_end_str, "%H:%M").time()
                
                for i in range(weeks_count):
                    current_date = start_date + datetime.timedelta(weeks=i)
                    d_str = current_date.strftime("%Y%m%d")
                    is_conflict = d_str in holidays
                    conflict_reason = holidays.get(d_str, "")
                    
                    preview.append({
                        "date": current_date,
                        "start_dt": datetime.datetime.combine(current_date, t_start),
                        "end_dt": datetime.datetime.combine(current_date, t_end),
                        "conflict": is_conflict,
                        "reason": conflict_reason,
                        "selected": not is_conflict
                    })
                st.session_state['preview_schedule'] = preview

        if st.session_state['preview_schedule']:
            st.divider()
            st.write("請確認排課日期：")
            final_schedule = []
            for idx, item in enumerate(st.session_state['preview_schedule']):
                label = f"第 {idx+1} 堂: {item['date']}"
                if item['conflict']:
                    label += f" ⚠️ 撞期: {item['reason']}"
                if st.checkbox(label, value=item['selected'], key=f"sch_{idx}"):
                    final_schedule.append(item)
            
            if st.button(f"確認排入 {len(final_schedule)} 堂課", type="primary"):
                title = f"[{s_location}] {s_teacher} - {s_course_name}"
                count = 0
                for item in final_schedule:
                    add_event_to_db(s_course_name, item['start_dt'], item['end_dt'], "shift", st.session_state['user'], s_location, s_teacher)
                    count += 1
                st.success(f"成功排入 {count} 堂課！")
                st.session_state['preview_schedule'] = None
                st.rerun()

    with tab3:
        st.subheader("👨‍🏫 師資薪資")
        with st.form("add_teacher"):
            c_t1, c_t2 = st.columns([2, 1])
            new_t_name = c_t1.text_input("老師姓名")
            new_t_rate = c_t2.number_input("單價", min_value=0, step=100)
            if st.form_submit_button("更新"):
                if new_t_name:
                    save_teacher_data(new_t_name, new_t_rate)
                    st.rerun()
        
        st.divider()
        st.subheader("🎓 學生名單管理")
        uploaded_file = st.file_uploader("📂 從 Excel/Google Sheet 匯入 (.csv)", type=['csv'])
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file)
                required_cols = ["姓名", "年級", "班別", "聯絡人1", "電話1"]
                if all(col in df.columns for col in required_cols):
                    if st.button("確認匯入上述名單"):
                        new_students = df.to_dict('records')
                        new_students = [{k: (v if pd.notna(v) else "") for k, v in r.items()} for r in new_students]
                        current_data = get_students_data_cached()
                        merged_data = current_data + new_students
                        save_students_data(merged_data)
                        st.success(f"成功匯入 {len(new_students)} 位學生")
                else:
                    st.error(f"CSV 格式錯誤！必須包含標題：{required_cols}")
            except Exception as e:
                st.error(f"讀取失敗: {e}")

        with st.expander("手動新增學生"):
            with st.form("manual_student"):
                ms_name = st.text_input("姓名 (必填)")
                c1, c2 = st.columns(2)
                ms_grade = c1.text_input("年級 (必填)")
                course_opts = get_unique_course_names()
                ms_class = c2.selectbox("班別 (必填)", course_opts)
                c3, c4 = st.columns(2)
                ms_c1 = c3.text_input("聯絡人1 (必填)")
                ms_p1 = c4.text_input("電話1 (必填)")
                c5, c6 = st.columns(2)
                ms_c2 = c5.text_input("聯絡人2")
                ms_p2 = c6.text_input("電話2")
                
                if st.form_submit_button("新增學生"):
                    if ms_name and ms_grade and ms_class and ms_c1 and ms_p1:
                        new_record = {
                            "姓名": ms_name, "年級": ms_grade, "班別": ms_class,
                            "聯絡人1": ms_c1, "電話1": ms_p1,
                            "聯絡人2": ms_c2, "電話2": ms_p2
                        }
                        current = get_students_data_cached()
                        current.append(new_record)
                        save_students_data(current)
                        st.rerun()
                    else:
                        st.error("請填寫所有必填欄位")
        
        st.write("目前學生列表：")
        current_students = get_students_data_cached()
        if current_students:
            st.dataframe(pd.DataFrame(current_students), use_container_width=True)
            del_names = [s['姓名'] for s in current_students]
            to_del = st.multiselect("選擇要刪除的學生", del_names)
            if to_del and st.button("確認刪除選取學生"):
                new_list = [s for s in current_students if s['姓名'] not in to_del]
                save_students_data(new_list)
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
            if st.session_state['user']:
                log_cleaning(area, st.session_state['user'])
                st.rerun()
            else:
                st.error("請先登入")

st.divider()

if st.session_state['user']:
    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        if st.button("📝 公告/交接", use_container_width=True): show_notice_dialog()
    with btn_c2:
        if st.session_state['is_admin']:
            if st.button("⚙️ 管理員後台", type="primary", use_container_width=True): show_admin_dialog()

# 行事曆
all_events = get_all_events_cached()
calendar_options = {
    "editable": True, 
    "headerToolbar": {
        "left": "today prev,next",
        "center": "title",
        "right": "listMonth,dayGridMonth"
    },
    "initialView": "listMonth",
    "height": "650px", 
}

cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick', 'eventClick'])

# 處理刪除/編輯的邏輯
if cal_return.get("eventClick"):
    event_id = cal_return["eventClick"]["event"]["id"]
    props = cal_return["eventClick"]["event"]["extendedProps"]
    show_edit_event_dialog(event_id, props)


# --- 6. 智慧點名系統 (格狀按鈕優化) ---
st.divider()
st.subheader("📋 每日點名")

selected_date = datetime.date.today()
if cal_return and "dateClick" in cal_return:
    clicked_date_str = cal_return["dateClick"]["date"].split("T")[0]
    selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()

st.info(f"日期：**{selected_date}**")

# 1. 找出當日課程
daily_courses = []
s_date_str = selected_date.isoformat()
for e in all_events:
    if e.get('start', '').startswith(s_date_str) and 'extendedProps' in e:
        props = e['extendedProps']
        if props.get('type') == 'shift':
            daily_courses.append(props.get('title', ''))

# 2. 篩選學生
all_students = get_students_data_cached()
target_students = []
if daily_courses:
    st.write(f"📅 今日課程：{'、'.join(daily_courses)}")
    for stu in all_students:
        if stu.get('班別') in daily_courses:
            target_students.append(stu['姓名'])
else:
    st.write("📅 今日無排課紀錄")

date_key = str(selected_date)
# 初始化邏輯：當鍵不存在，或「今日有課」且「未到名單為空」（防止資料卡住）時重置
if date_key not in st.session_state or (daily_courses and not st.session_state[date_key]['absent'] and not st.session_state[date_key]['present']):
    if date_key not in st.session_state:
        st.session_state[date_key] = {
            "absent": target_students,
            "present": [],
            "leave": [],
            "dirty": False
        }

current_data = st.session_state[date_key]

if st.session_state['user']:
    if not current_data['absent'] and not current_data['present'] and not current_data['leave']:
        st.info("今日無符合班別的學生需點名")
    else:
        with st.expander("點名表單", expanded=True):
            col_absent, col_present, col_leave = st.columns(3)
            
            with col_absent:
                st.markdown("### 🔴 未到")
                if current_data['absent']:
                    # 改為 4 欄格狀排列，節省空間
                    grid_cols = st.columns(4)
                    for i, student in enumerate(current_data['absent']):
                        with grid_cols[i % 4]:
                            if st.button(student, key=f"abs_{student}_{date_key}", use_container_width=True):
                                current_data['absent'].remove(student)
                                current_data['present'].append(student)
                                current_data['dirty'] = True
                                st.rerun()
                else:
                    st.caption("全勤！")

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

        btn_type = "primary" if current_data.get('dirty', False) else "secondary"
        btn_text = "💾 儲存 (有更動)" if current_data.get('dirty', False) else "💾 資料已儲存"
        
        if st.button(btn_text, type=btn_type, use_container_width=True):
            current_data['dirty'] = False
            st.success("點名紀錄已儲存")
            st.rerun()
else:
    st.warning("請登入以進行點名")
