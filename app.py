import streamlit as st
from streamlit_calendar import calendar
import datetime
import time
from dateutil.relativedelta import relativedelta
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json
import pytz
import pandas as pd
import uuid
import calendar as py_calendar
from collections import defaultdict

# --- 1. 系統設定 ---
st.set_page_config(page_title="鳩特數理行政班表", page_icon="🏫", layout="wide")

# CSS 優化
st.markdown("""
<style>
    [data-testid="column"] { min-width: 0px !important; padding: 0px !important; }
    div[data-testid="stCheckbox"] { padding-top: 5px; min-height: 0px; text-align: center; }
    div[data-testid="stCheckbox"] label { min-height: 0px; }
    .stDataFrame { margin-bottom: -1rem; }
    div[data-testid="stMarkdownContainer"] p { text-align: center; font-weight: bold; }
    .streamlit-expanderContent { padding-top: 0rem !important; padding-bottom: 0.5rem !important; }
    /* 試聽追蹤卡片樣式 */
    .trial-card {
        border: 2px solid #ff4b4b;
        border-radius: 10px;
        padding: 15px;
        background-color: #fff5f5;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

if 'user' not in st.session_state: st.session_state['user'] = None
if 'is_admin' not in st.session_state: st.session_state['is_admin'] = False

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
LOGIN_LIST = ["鳩特", "鳩婆", "世軒", "竣揚", "暐傑"]
STAFF_PASSWORD = "88888888"
ADMIN_PASSWORD = "150508"

GRADE_OPTIONS = ["小一", "小二", "小三", "小四", "小五", "小六", "國一", "國二", "國三", "高一", "高二", "高三", "畢業"]
TIME_OPTIONS = [f"{h:02d}:00" for h in range(9, 23)] + [f"{h:02d}:30" for h in range(9, 22)]

# --- 3. 資料庫存取 ---

def get_unique_course_names():
    default_courses = ["小四數學", "小五數學", "小六數學", "國一數學", "國二數學", "國三數學", "國二理化", "國二自然", "高一數學", "高一物理", "高一化學"]
    doc = db.collection("settings").document("courses").get()
    if doc.exists:
        saved_list = doc.to_dict().get("list", [])
        combined = list(set(default_courses + saved_list))
        def sort_key(x):
            order = ["小", "國", "高"]
            for i, prefix in enumerate(order):
                if x.startswith(prefix): return (i, x)
            return (99, x)
        return sorted(combined, key=sort_key)
    return default_courses

def save_course_name(course_name):
    current = get_unique_course_names()
    if course_name not in current:
        current.append(course_name)
        db.collection("settings").document("courses").set({"list": current})

def get_teachers_data():
    docs = db.collection("teachers_config").stream()
    return {doc.id: doc.to_dict() for doc in docs}

def save_teacher_data(name, rate):
    db.collection("teachers_config").document(name).set({"rate": rate})
    st.toast(f"已更新 {name} 的薪資設定")

@st.cache_data(ttl=300)
def get_students_data_cached():
    doc = db.collection("settings").document("students_detail").get()
    return doc.to_dict().get("data", []) if doc.exists else []

def save_students_data(new_data_list):
    db.collection("settings").document("students_detail").set({"data": new_data_list})
    get_students_data_cached.clear()
    st.toast("學生名單已更新")

@st.cache_data(ttl=300)
def get_part_timers_list_cached():
    doc = db.collection("settings").document("part_timers").get()
    return doc.to_dict().get("list", ["工讀生A", "工讀生B"]) if doc.exists else ["工讀生A", "工讀生B"]

def save_part_timers_list(new_list):
    db.collection("settings").document("part_timers").set({"list": new_list})
    get_part_timers_list_cached.clear()
    st.toast("工讀生名單已更新")

# --- 新增：試聽生與潛在名單管理 ---
def get_trial_students():
    docs = db.collection("trial_students").stream()
    return [{**doc.to_dict(), "id": doc.id} for doc in docs]

def save_trial_student(data):
    db.collection("trial_students").add(data)
    st.toast("已新增試聽生")

def delete_trial_student(doc_id):
    db.collection("trial_students").document(doc_id).delete()

def get_potential_students():
    docs = db.collection("potential_students").order_by("archived_at", direction=firestore.Query.DESCENDING).limit(100).stream()
    return [{**doc.to_dict(), "id": doc.id} for doc in docs]

def move_trial_to_official(trial_data, doc_id):
    # 1. 加入正式名單
    current_students = get_students_data_cached()
    new_student = {
        "姓名": trial_data.get("name"),
        "年級": trial_data.get("grade"),
        "班別": trial_data.get("course"),
        "學生手機": trial_data.get("phone", ""),
        "家裡": "", "爸爸": "", "媽媽": "", "其他家人": "" # 試聽時可能資料不全，先留白
    }
    current_students.append(new_student)
    save_students_data(current_students)
    
    # 2. 刪除試聽紀錄
    delete_trial_student(doc_id)
    st.success(f"🎉 歡迎 {trial_data.get('name')} 加入 {trial_data.get('course')}！")
    time.sleep(1.5)
    st.rerun()

def move_trial_to_potential(trial_data, doc_id):
    # 1. 加入潛在名單
    archive_data = trial_data.copy()
    archive_data['archived_at'] = datetime.datetime.now().isoformat()
    archive_data['status'] = 'did_not_join'
    db.collection("potential_students").add(archive_data)
    
    # 2. 刪除試聽紀錄
    delete_trial_student(doc_id)
    st.info(f"📂 已將 {trial_data.get('name')} 歸檔至潛在名單")
    time.sleep(1.5)
    st.rerun()

# --- 點名與活動 ---
def get_roll_call_from_db(date_str):
    doc = db.collection("roll_call_records").document(date_str).get()
    return doc.to_dict() if doc.exists else None

def get_all_roll_calls():
    docs = db.collection("roll_call_records").stream()
    return {doc.id: doc.to_dict() for doc in docs}

def save_roll_call_to_db(date_str, data):
    db.collection("roll_call_records").document(date_str).set(data)

@st.cache_data(ttl=600)
def get_all_events_cached():
    events = []
    try:
        docs = db.collection("shifts").stream()
        for doc in docs:
            data = doc.to_dict()
            title = data.get("title", "")
            color = "#3788d8"
            if data.get("type") == "shift":
                title = f"{data.get('title')} ({data.get('teacher')})"
                color = "#28a745"
            elif data.get("type") == "part_time":
                title = f"{data.get('staff')}"
                color = "#6f42c1"
            elif data.get("type") == "notice":
                cat = data.get("category", "其他")
                title = f"[{cat}] {title}"
                color = {"調課": "#d63384", "考試": "#dc3545", "活動": "#0d6efd", "任務": "#FF4500"}.get(cat, "#ffc107")
                if cat == "任務": title = f"🔥 {title}"
            
            sanitized = {k: str(v) if isinstance(v, (datetime.date, datetime.datetime)) else v for k, v in data.items()}
            events.append({"id": doc.id, "title": title, "start": data.get("start"), "end": data.get("end"), "color": color, "allDay": data.get("type")=="notice", "extendedProps": sanitized})
    except: pass
    
    try:
        resp = requests.get(f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{datetime.date.today().year}.json").json()
        for d in resp:
            if d.get('isHoliday'):
                events.append({"id": f"hol_{d['date']}", "title": f"🌴 {d['description']}", "start": d['date'], "allDay": True, "display": "background", "backgroundColor": "#ffebee", "editable": False, "extendedProps": {"type": "holiday"}})
    except: pass
    return events

def add_event_to_db(title, start, end, type, user, location="", teacher_name="", category="", staff=""):
    db.collection("shifts").add({
        "title": title, "start": start.isoformat(), "end": end.isoformat(), "type": type, "staff": staff if staff else user,
        "location": location, "teacher": teacher_name, "category": category, "created_at": datetime.datetime.now()
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

def batch_delete_events(doc_ids):
    batch = db.batch()
    for doc_id in doc_ids: batch.delete(db.collection("shifts").document(doc_id))
    batch.commit()
    get_all_events_cached.clear()
    st.toast(f"刪除 {len(doc_ids)} 筆")

def get_cleaning_status(area):
    doc = db.collection("latest_cleaning_status").document(area).get()
    return doc.to_dict() if doc.exists else None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    db.collection("cleaning_logs").add({"area": area, "staff": user, "timestamp": now})
    db.collection("latest_cleaning_status").document(area).set({"area": area, "staff": user, "timestamp": now})
    st.toast(f"✨ {area} 清潔完成！", icon="🧹")

# --- 4. Dialogs ---
@st.dialog("✏️ 編輯/刪除 行程")
def show_edit_event_dialog(event_id, props):
    if props.get('type') == 'holiday':
        st.warning("🌴 這是國定假日，無法編輯。"); st.button("關閉", on_click=st.rerun); return

    st.write(f"正在編輯：**{props.get('title', '')}**")
    try:
        s_str, e_str = props.get('start'), props.get('end')
        if "T" in s_str:
            s_dt = datetime.datetime.fromisoformat(s_str.replace("Z", "+00:00")).astimezone(pytz.timezone('Asia/Taipei'))
            def_date, def_s = s_dt.date(), s_dt.strftime("%H:%M")
        else:
            def_date, def_s = datetime.datetime.strptime(s_str, "%Y-%m-%d").date(), "09:00"
        
        if e_str and "T" in e_str:
            e_dt = datetime.datetime.fromisoformat(e_str.replace("Z", "+00:00")).astimezone(pytz.timezone('Asia/Taipei'))
            def_e = e_dt.strftime("%H:%M")
        else: def_e = "10:00"
    except: def_date, def_s, def_e = datetime.date.today(), "18:00", "21:00"

    if props.get('type') == 'shift':
        new_title = st.text_input("課程名稱", props.get('title'))
        c1, c2, c3 = st.columns([2, 1.5, 1.5])
        new_date = c1.date_input("日期", def_date)
        t_opts = sorted(list(set(TIME_OPTIONS + [def_s, def_e, "13:30", "16:30"])))
        n_s = c2.selectbox("開始", t_opts, index=t_opts.index(def_s) if def_s in t_opts else 0)
        n_e = c3.selectbox("結束", t_opts, index=t_opts.index(def_e) if def_e in t_opts else min(len(t_opts)-1, 1))
        
        b1, b2 = st.columns(2)
        if b1.button("💾 儲存"):
            s_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(n_s, "%H:%M").time())
            e_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(n_e, "%H:%M").time())
            update_event_in_db(event_id, {"title": new_title, "start": s_new.isoformat(), "end": e_new.isoformat()}); st.rerun()
        if b2.button("🗑️ 刪除"): delete_event_from_db(event_id); st.rerun()

    elif props.get('type') == 'part_time':
        new_staff = st.text_input("工讀生", props.get('staff'))
        c1, c2, c3 = st.columns([2, 1.5, 1.5])
        new_date = c1.date_input("日期", def_date)
        t_opts = sorted(list(set(TIME_OPTIONS + [def_s, def_e])))
        n_s = c2.selectbox("上班", t_opts, index=t_opts.index(def_s) if def_s in t_opts else 0)
        n_e = c3.selectbox("下班", t_opts, index=t_opts.index(def_e) if def_e in t_opts else 0)
        b1, b2 = st.columns(2)
        if b1.button("💾 儲存"):
            s_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(n_s, "%H:%M").time())
            e_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(n_e, "%H:%M").time())
            update_event_in_db(event_id, {"staff": new_staff, "start": s_new.isoformat(), "end": e_new.isoformat()}); st.rerun()
        if b2.button("🗑️ 刪除"): delete_event_from_db(event_id); st.rerun()

    elif props.get('type') == 'notice':
        cats = ["調課", "考試", "活動", "任務", "其他"]
        n_cat = st.selectbox("分類", cats, index=cats.index(props.get('category', '其他')) if props.get('category') in cats else 4)
        n_con = st.text_area("內容", props.get('title'))
        b1, b2 = st.columns(2)
        if b1.button("💾 儲存"): update_event_in_db(event_id, {"title": n_con, "category": n_cat}); st.rerun()
        if b2.button("🗑️ 刪除"): delete_event_from_db(event_id); st.rerun()
    else:
        if st.button("🗑️ 強制刪除"): delete_event_from_db(event_id); st.rerun()

@st.dialog("📢 新增公告")
def show_notice_dialog(default_date=None):
    if not default_date: default_date = datetime.date.today()
    st.info(f"建立 {default_date} 的事項")
    d = st.date_input("日期", default_date)
    cat = st.selectbox("分類", ["調課", "考試", "活動", "任務", "其他"])
    con = st.text_area("內容")
    if st.button("發布"):
        s = datetime.datetime.combine(d, datetime.time(9,0)); e = datetime.datetime.combine(d, datetime.time(10,0))
        add_event_to_db(con, s, e, "notice", st.session_state['user'], category=cat); st.toast("已發布"); st.rerun()

@st.dialog("📅 紀錄檢視")
def show_roll_call_review_dialog():
    recs = get_all_roll_calls()
    if not recs: st.info("無紀錄"); return
    
    # 準備地點對照
    d_loc = {}
    for e in get_all_events_cached():
        sd = e.get('start', '').split('T')[0]
        p = e.get('extendedProps', {})
        if p.get('type')=='shift':
            loc = p.get('location', '')
            if loc=='線上': loc='櫃檯'
            if sd not in d_loc: d_loc[sd]=[]
            if loc and loc not in d_loc[sd]: d_loc[sd].append(loc)

    data = []
    for d in sorted(recs.keys(), reverse=True):
        r = recs[d]
        loc_str = "、".join(d_loc.get(d, []))
        status = f"到:{len(r.get('present',[]))} / 假:{len(r.get('leave',[]))} / 未:{len(r.get('absent',[]))}"
        data.append({"日期": d, "地點": loc_str, "狀態": status, "raw": d})
    
    event = st.dataframe(pd.DataFrame(data), column_config={"raw":None}, selection_mode="single-row", on_select="rerun", hide_index=True, use_container_width=True)
    if len(event.selection['rows']) > 0:
        st.session_state['selected_calendar_date'] = datetime.date.fromisoformat(data[event.selection['rows'][0]]['raw']); st.rerun()

@st.dialog("📂 資料管理")
def show_general_management_dialog():
    tab1, tab2, tab3 = st.tabs(["🎓 學生名單", "👷 工讀生", "🎧 試聽與潛在名單"])
    
    # --- Tab 1: 學生名單 ---
    with tab1:
        current_students = get_students_data_cached()
        with st.expander("📂 Excel 匯入", expanded=False):
            uploaded = st.file_uploader("上傳 Excel/CSV", type=['csv', 'xlsx'])
            if uploaded:
                try:
                    if uploaded.name.endswith('.csv'): df = pd.read_csv(uploaded)
                    else: import openpyxl; df = pd.read_excel(uploaded, engine='openpyxl')
                    df.columns = [str(c).strip() for c in df.columns]; cols = list(df.columns)
                    
                    def get_idx(k): 
                        for i, o in enumerate(cols): 
                            if any(x in o for x in k): return i
                        return 0
                    
                    c1, c2 = st.columns(2)
                    c_name = c1.selectbox("姓名欄", cols, index=get_idx(['姓名', 'Name']))
                    c_grade = c2.selectbox("年級欄", cols, index=get_idx(['年級', 'Grade']))
                    c3, c4 = st.columns(2)
                    c_course = c3.selectbox("課程欄", cols, index=get_idx(['課程', '班別']))
                    c_cont = c4.selectbox("電話欄", cols, index=get_idx(['電話', '聯絡', 'Tel']))
                    
                    if st.button("✅ 匯入"):
                        new_data = []
                        for _, row in df.iterrows():
                            # 簡化處理：只取基本欄位
                            name = str(row[c_name]).strip(); grade = str(row[c_grade]).strip()
                            raw_cont = str(row[c_cont]).strip() if pd.notna(row[c_cont]) else ""
                            # 簡單電話清洗
                            import re
                            phone_clean = re.sub(r'[^\d\-]', '', raw_cont)
                            
                            raw_courses = str(row[c_course]).strip() if pd.notna(row[c_course]) else ""
                            courses = [c.strip() for c in raw_courses.replace("\n", ",").split(",") if c.strip()]
                            
                            base = {"姓名": name, "年級": grade, "學生手機": phone_clean, "家裡": "", "爸爸": "", "媽媽": ""}
                            if not courses: new_data.append({**base, "班別": "未分班"})
                            else: 
                                for c in courses: new_data.append({**base, "班別": c})
                        
                        save_students_data(current_students + new_data)
                        st.success(f"匯入 {len(new_data)} 筆"); time.sleep(1); st.rerun()
                except Exception as e: st.error(f"Error: {e}")

        with st.expander("手動新增"):
            c1, c2 = st.columns(2)
            n_name = c1.text_input("姓名")
            n_phone = c2.text_input("手機")
            c3, c4 = st.columns(2)
            n_grade = c3.selectbox("年級", GRADE_OPTIONS)
            n_course = c4.selectbox("班別", get_unique_course_names())
            if st.button("新增"):
                current_students.append({"姓名": n_name, "學生手機": n_phone, "年級": n_grade, "班別": n_course, "家裡":"", "爸爸":"", "媽媽":""})
                save_students_data(current_students); st.rerun()

        if current_students:
            st.divider(); st.subheader("🔎 列表")
            df_s = pd.DataFrame(current_students)
            f_class = st.selectbox("班別篩選", ["全部"] + sorted(list(set([x.get('班別') for x in current_students if x.get('班別')]))))
            if f_class != "全部": df_s = df_s[df_s['班別'] == f_class]
            st.dataframe(df_s, use_container_width=True)
            
            with st.expander("🗑️ 刪除"):
                d_opts = [f"{r['姓名']} ({r.get('班別')})" for _, r in df_s.iterrows()]
                to_del = st.multiselect("選擇刪除", d_opts)
                if to_del and st.button("確認刪除"):
                    new_l = [s for s in current_students if f"{s['姓名']} ({s.get('班別')})" not in to_del]
                    save_students_data(new_l); st.rerun()

    # --- Tab 2: 工讀生 ---
    with tab2:
        pts = get_part_timers_list_cached()
        c1, c2 = st.columns([2, 1])
        n_pt = c1.text_input("新工讀生")
        if c2.button("新增"): pts.append(n_pt); save_part_timers_list(pts); st.rerun()
        d_pt = st.multiselect("刪除", pts)
        if d_pt and st.button("確認刪"): save_part_timers_list([x for x in pts if x not in d_pt]); st.rerun()

    # --- Tab 3: 試聽與潛在名單 (NEW) ---
    with tab3:
        st.subheader("🎧 試聽生管理 (未入班)")
        with st.form("new_trial"):
            c1, c2 = st.columns(2)
            t_name = c1.text_input("試聽生姓名")
            t_phone = c2.text_input("聯絡電話")
            c3, c4, c5 = st.columns(3)
            t_grade = c3.selectbox("年級", GRADE_OPTIONS, key="t_g")
            t_course = c4.selectbox("試聽課程", get_unique_course_names(), key="t_c")
            t_date = c5.date_input("試聽日期", datetime.date.today())
            if st.form_submit_button("新增試聽紀錄"):
                if t_name and t_course:
                    save_trial_student({
                        "name": t_name, "phone": t_phone, "grade": t_grade, 
                        "course": t_course, "trial_date": t_date.isoformat(), "created_at": datetime.datetime.now().isoformat()
                    })
                    st.rerun()
                else: st.error("姓名與課程為必填")
        
        # 顯示目前的試聽生
        trials = get_trial_students()
        if trials:
            st.divider()
            st.caption("尚未決定去留的試聽生：")
            for t in trials:
                c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
                c1.write(f"**{t['name']}**")
                c2.write(f"{t['course']}")
                c3.write(f"{t['trial_date']}")
                if c4.button("🗑️ 刪除", key=f"del_t_{t['id']}"):
                    delete_trial_student(t['id']); st.rerun()
        else:
            st.info("目前沒有試聽生")

        st.divider()
        st.subheader("📂 潛在/歸檔名單")
        potentials = get_potential_students()
        if potentials:
            st.dataframe(pd.DataFrame(potentials).drop(columns=['id'], errors='ignore'), use_container_width=True)
        else:
            st.caption("無資料")

# --- 5. Main Logic ---
tz = pytz.timezone('Asia/Taipei')
now = datetime.datetime.now(tz)

if now.hour==6 and now.minute<=30 and st.session_state['user']:
    st.session_state['user']=None; st.session_state['is_admin']=False; st.rerun()

if not st.session_state['user']:
    st.title("🏫 鳩特數理行政班表")
    with st.form("login"):
        u = st.selectbox("身份", ["請選擇"]+LOGIN_LIST)
        p = st.text_input("密碼", type="password")
        if st.form_submit_button("登入"):
            if (u in ADMINS and p==ADMIN_PASSWORD) or (u not in ADMINS and p==STAFF_PASSWORD):
                st.session_state['user']=u; st.session_state['is_admin']=(u in ADMINS); st.rerun()
            else: st.error("錯誤")
    st.stop()

c1, c2 = st.columns([3, 1], vertical_alignment="center")
c1.title("🏫 鳩特數理行政班表")
c2.markdown(f"👤 **{st.session_state['user']}**"); 
if c2.button("登出"): st.session_state['user']=None; st.rerun()

# ★★★ 試聽追蹤提醒區塊 (NEW) ★★★
pending_trials = get_trial_students()
follow_up_list = []
for t in pending_trials:
    try:
        t_date = datetime.date.fromisoformat(t['trial_date'])
        # 邏輯：如果今天是試聽日+7天(或之後)，跳出提醒
        if datetime.date.today() >= (t_date + datetime.timedelta(days=7)):
            follow_up_list.append(t)
    except: pass

if follow_up_list:
    st.markdown("### 🔔 試聽追蹤提醒")
    st.info("以下學生已試聽滿一週，請確認是否入班？")
    
    for t in follow_up_list:
        with st.container():
            st.markdown(f"""
            <div class="trial-card">
                <h4>🎓 {t['name']} ({t['grade']})</h4>
                <p>試聽課程：<b>{t['course']}</b> | 試聽日期：{t['trial_date']}</p>
            </div>
            """, unsafe_allow_html=True)
            
            c_yes, c_no = st.columns(2)
            if c_yes.button(f"✅ {t['name']} 確定入班", key=f"join_{t['id']}", type="primary", use_container_width=True):
                move_trial_to_official(t, t['id'])
            
            if c_no.button(f"📂 {t['name']} 未入班 (歸檔)", key=f"arch_{t['id']}", use_container_width=True):
                move_trial_to_potential(t, t['id'])
    st.divider()

# 打掃與其他功能
clean_cols = st.columns(5)
areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室", "鳩辦公室"]
for i, a in enumerate(areas):
    s = get_cleaning_status(a); day_diff = 999; who = "無"
    if s:
        try: 
            ts = datetime.datetime.fromisoformat(str(s['timestamp'])).replace(tzinfo=None)
            day_diff = (datetime.datetime.now()-ts).days; who = s.get('staff')
        except: pass
    clr = "green" if day_diff<=3 else "orange" if day_diff<=6 else "red"
    with clean_cols[i]:
        st.caption(a); st.markdown(f"### :{clr}[{day_diff} 天]")
        st.caption(f"最後: {who}")
        if st.button("已掃", key=f"cl_{i}", use_container_width=True): log_cleaning(a, st.session_state['user']); st.rerun()

st.divider()
if st.button("📂 資料管理", type="secondary", use_container_width=True): show_general_management_dialog()
if st.session_state['is_admin'] and st.button("⚙️ 後台", type="secondary", use_container_width=True): show_admin_dialog()

# Calendar & Main Events Logic...
all_events = get_all_events_cached()
cal = calendar(events=all_events, options={
    "editable":True, "headerToolbar":{"left":"today prev,next","center":"title","right":"listMonth,dayGridMonth"},
    "initialView":"dayGridMonth", "height":"650px", "locale":"zh-tw",
    "selectable":True
}, callbacks=['dateClick', 'eventClick'])

if cal.get('dateClick'):
    d = cal['dateClick']['date']
    try: d_obj = datetime.datetime.fromisoformat(d.replace("Z","")).date()
    except: d_obj = datetime.date.today()
    show_notice_dialog(d_obj)

if cal.get('eventClick'):
    show_edit_event_dialog(cal['eventClick']['event']['id'], cal['eventClick']['event']['extendedProps'])

# 點名系統
st.divider(); st.subheader("📋 每日點名")
c1, c2 = st.columns([1,3], vertical_alignment="center")
if c1.button("📅 切換日期"): show_roll_call_review_dialog()
sel_date = st.session_state.get('selected_calendar_date', datetime.date.today())
c2.markdown(f"**{sel_date}**")

d_key = sel_date.isoformat()
rec = get_roll_call_from_db(d_key)
all_stu = get_students_data_cached()
c_map = defaultdict(list)
for s in all_stu: c_map[s.get('班別')].append(s.get('姓名'))

today_courses = []; loc_map = {}
for e in all_events:
    if e['start'].startswith(d_key) and e['extendedProps'].get('type')=='shift':
        t = e['extendedProps'].get('title'); l = e['extendedProps'].get('location')
        if l=='線上': l='櫃檯'
        today_courses.append(t); loc_map[t] = l

target_stu = list(set([stu for c in today_courses for stu in c_map.get(c, [])]))

if rec:
    curr = rec
    for k in ['absent','present','leave']: 
        if k not in curr: curr[k]=[]
    # 自動同步
    rec_all = set(curr['absent']+curr['present']+curr['leave'])
    miss = [s for s in target_stu if s not in rec_all]
    if miss: curr['absent'].extend(miss)
else:
    curr = {"absent": target_stu, "present": [], "leave": []}

def save_state(a, p, l):
    save_roll_call_to_db(d_key, {"absent":a, "present":p, "leave":l, "updated_at":datetime.datetime.now().isoformat()})
    st.toast("已儲存"); time.sleep(0.5); st.rerun()

if not target_stu and not curr['absent'] and not curr['present']:
    st.info("無課程")
else:
    st.markdown("### 🔴 尚未報到")
    pending = set(curr['absent'])
    if pending:
        sel_p = []; sel_l = []; shown = set()
        for c_name in sorted(list(set(today_courses))):
            s_list = [s for s in c_map.get(c_name, []) if s in pending]
            if s_list:
                shown.update(s_list)
                loc = loc_map.get(c_name, "")
                suffix = f" @ {loc}" if loc else ""
                with st.expander(f"📘 {c_name}{suffix} ({len(s_list)}人)", expanded=True):
                    st.write("👇 到班")
                    sp = st.pills(f"p_{c_name}", s_list, selection_mode="multi", key=f"p_{c_name}_{d_key}")
                    rem = [x for x in s_list if x not in sp]
                    if rem:
                        st.write("👇 請假")
                        sl = st.pills(f"l_{c_name}", rem, selection_mode="multi", key=f"l_{c_name}_{d_key}")
                        sel_l.extend(sl)
                    sel_p.extend(sp)
        
        leftover = [s for s in pending if s not in shown]
        if leftover:
            with st.expander(f"❓ 未分類 ({len(leftover)}人)", expanded=True):
                lp = st.pills("p_other", leftover, selection_mode="multi")
                rem_l = [x for x in leftover if x not in lp]
                ll = st.pills("l_other", rem_l, selection_mode="multi")
                sel_p.extend(lp); sel_l.extend(ll)
        
        st.divider()
        if st.button("🚀 確認送出", type="primary", use_container_width=True):
            if set(sel_p) & set(sel_l): st.error("衝突")
            elif not sel_p and not sel_l: st.warning("未選")
            else:
                na = [x for x in curr['absent'] if x not in sel_p and x not in sel_l]
                save_state(na, curr['present']+sel_p, curr['leave']+sel_l)

    with st.expander(f"已到 ({len(curr['present'])}) / 請假 ({len(curr['leave'])})", expanded=False):
        if curr['present']:
            st.write("🟢 已到 (點擊取消)")
            up = st.pills("up", curr['present'], selection_mode="multi", label_visibility="collapsed")
            if up and st.button("↩️ 還原到"):
                save_state(curr['absent']+up, [x for x in curr['present'] if x not in up], curr['leave'])
        if curr['leave']:
            st.divider(); st.write("🟡 請假 (點擊取消)")
            ul = st.pills("ul", curr['leave'], selection_mode="multi", label_visibility="collapsed")
            if ul and st.button("↩️ 還原假"):
                save_state(curr['absent']+ul, curr['present'], [x for x in curr['leave'] if x not in ul])
