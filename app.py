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
    /* 離班學生樣式 */
    .leaving-student {
        color: #e63946;
        font-weight: bold;
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

# --- 假期管理 ---
def get_teacher_vacations():
    docs = db.collection("teacher_vacations").stream()
    return [{**doc.to_dict(), "id": doc.id} for doc in docs]

def save_teacher_vacation(teacher, start, end, reason):
    db.collection("teacher_vacations").add({
        "teacher": teacher, "start": start.isoformat(), "end": end.isoformat(), "reason": reason, "created_at": datetime.datetime.now().isoformat()
    })
    get_teacher_vacations_cached.clear() 

def delete_teacher_vacation(doc_id):
    db.collection("teacher_vacations").document(doc_id).delete()
    get_teacher_vacations_cached.clear()

@st.cache_data(ttl=300)
def get_teacher_vacations_cached():
    return get_teacher_vacations()

# --- 試聽生與潛在名單管理 ---
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
    current_students = get_students_data_cached()
    new_student = {
        "姓名": trial_data.get("name"),
        "年級": trial_data.get("grade"),
        "班別": trial_data.get("course"),
        "學生手機": trial_data.get("stu_mob", ""),
        "家裡": trial_data.get("home_tel", ""),
        "爸爸": trial_data.get("dad_tel", ""),
        "媽媽": trial_data.get("mom_tel", ""),
        "其他家人": trial_data.get("other_tel", "")
    }
    current_students.append(new_student)
    save_students_data(current_students)
    delete_trial_student(doc_id)
    st.success(f"🎉 歡迎 {trial_data.get('name')} 加入 {trial_data.get('course')}！資料已自動轉入。")
    time.sleep(1.5)
    st.rerun()

def move_trial_to_potential(trial_data, doc_id):
    archive_data = trial_data.copy()
    archive_data['archived_at'] = datetime.datetime.now().isoformat()
    archive_data['status'] = 'did_not_join'
    db.collection("potential_students").add(archive_data)
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
                if "⚠️ 調課" in title: color = "#FF0000"
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

def batch_mark_reschedule(doc_ids):
    batch = db.batch()
    for doc_id in doc_ids:
        ref = db.collection("shifts").document(doc_id)
        curr = ref.get().to_dict()
        title = curr.get('title', '')
        if "⚠️ 調課" not in title:
            new_title = f"⚠️ 調課-{title}"
            batch.update(ref, {"title": new_title})
    batch.commit()
    get_all_events_cached.clear()
    st.toast(f"已將 {len(doc_ids)} 堂課標記為需調課", icon="⚠️")

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
        
        # 1. 離班功能 (NEW)
        with st.expander("👋 辦理離班/退班"):
            st.warning("設定後，學生將於「最後上課日」隔天起自動從點名表移除，但資料會保留。")
            
            # 建立選單：只顯示還沒離班的學生 (或顯示所有但標記狀態)
            active_opts = []
            for s in current_students:
                name = s.get('姓名')
                c = s.get('班別')
                leave_date = s.get('leaving_date')
                label = f"{name} ({c})"
                if leave_date: label += f" [已設離班: {leave_date}]"
                active_opts.append(label)
            
            sel_student_label = st.selectbox("選擇學生", ["請選擇"] + active_opts)
            
            if sel_student_label != "請選擇":
                c1, c2 = st.columns(2)
                last_date = c1.date_input("最後上課日 (該日之後將不再點名)")
                refund = c2.checkbox("需要計算退費 (待結算)", value=False)
                
                if refund:
                    st.info("💡 提示：退費系統開發中，此標記將用於未來的財務報表提醒。")
                
                if st.button("確認辦理離班", type="primary"):
                    # 找到該學生並更新資料
                    target_name = sel_student_label.split(" (")[0]
                    target_course = sel_student_label.split("(")[1].split(")")[0] # 簡單解析
                    
                    updated_list = []
                    for s in current_students:
                        # 比對姓名與班別 (最保險)
                        if s.get('姓名') == target_name and s.get('班別') == target_course:
                            s['leaving_date'] = last_date.isoformat()
                            s['refund_needed'] = refund
                        updated_list.append(s)
                    
                    save_students_data(updated_list)
                    st.success(f"已設定 {target_name} 於 {last_date} 離班。")
                    time.sleep(1)
                    st.rerun()

        # 2. 匯入功能
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
                    
                    if st.button("✅ 匯入", key="btn_import_stu"):
                        new_data = []
                        for _, row in df.iterrows():
                            name = str(row[c_name]).strip(); grade = str(row[c_grade]).strip()
                            raw_cont = str(row[c_cont]).strip() if pd.notna(row[c_cont]) else ""
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

        # 3. 手動新增
        with st.expander("手動新增"):
            c1, c2 = st.columns(2)
            n_name = c1.text_input("姓名")
            n_phone = c2.text_input("手機")
            c3, c4 = st.columns(2)
            n_grade = c3.selectbox("年級", GRADE_OPTIONS)
            n_course = c4.selectbox("班別", get_unique_course_names())
            if st.button("新增", key="btn_add_manual_stu"):
                current_students.append({"姓名": n_name, "學生手機": n_phone, "年級": n_grade, "班別": n_course, "家裡":"", "爸爸":"", "媽媽":""})
                save_students_data(current_students); st.rerun()

        # 4. 列表與刪除
        if current_students:
            st.divider(); st.subheader("🔎 列表")
            
            # 整理顯示資料，包含離班狀態
            display_list = []
            for s in current_students:
                s_copy = s.copy()
                if s.get('leaving_date'):
                    s_copy['狀態'] = f"離班 ({s['leaving_date']})"
                else:
                    s_copy['狀態'] = "在班"
                display_list.append(s_copy)

            df_s = pd.DataFrame(display_list)
            target_cols = ["姓名", "狀態", "年級", "班別", "學生手機", "爸爸", "媽媽", "家裡"]
            for c in target_cols:
                if c not in df_s.columns: df_s[c] = ""
            df_s = df_s[target_cols]

            f_class = st.selectbox("班別篩選", ["全部"] + sorted(list(set([x.get('班別') for x in current_students if x.get('班別')]))))
            if f_class != "全部": df_s = df_s[df_s['班別'] == f_class]
            
            st.dataframe(df_s, use_container_width=True, hide_index=True)
            
            with st.expander("🗑️ 刪除資料 (慎用)"):
                st.caption("此操作會完全刪除學生資料。若是學生不補了，建議使用上方的「辦理離班」功能。")
                d_opts = [f"{r['姓名']} ({r.get('班別')})" for _, r in df_s.iterrows()]
                to_del = st.multiselect("選擇刪除", d_opts)
                if to_del and st.button("確認刪除", key="btn_del_manual_stu"):
                    new_l = [s for s in current_students if f"{s['姓名']} ({s.get('班別')})" not in to_del]
                    save_students_data(new_l); st.rerun()

    # --- Tab 2: 工讀生 ---
    with tab2:
        pts = get_part_timers_list_cached()
        c1, c2 = st.columns([2, 1])
        n_pt = c1.text_input("新工讀生")
        if c2.button("新增", key="btn_add_pt"): pts.append(n_pt); save_part_timers_list(pts); st.rerun()
        d_pt = st.multiselect("刪除", pts)
        if d_pt and st.button("確認刪", key="btn_del_pt"): save_part_timers_list([x for x in pts if x not in d_pt]); st.rerun()

    # --- Tab 3: 試聽與潛在名單 ---
    with tab3:
        st.subheader("🎧 試聽生管理 (未入班)")
        with st.form("new_trial"):
            st.write("📝 **基本資料**")
            c1, c2 = st.columns(2)
            t_name = c1.text_input("試聽生姓名")
            t_grade = c2.selectbox("年級", GRADE_OPTIONS, key="t_g")
            c3, c4 = st.columns(2)
            t_course = c3.selectbox("試聽課程", get_unique_course_names(), key="t_c")
            t_date = c4.date_input("試聽日期", datetime.date.today())
            st.write("📞 **聯絡方式 (轉正後會自動帶入)**")
            c5, c6 = st.columns(2)
            t_mobile = c5.text_input("學生手機")
            t_home = c6.text_input("家裡電話")
            c7, c8 = st.columns(2)
            t_dad = c7.text_input("爸爸電話")
            t_mom = c8.text_input("媽媽電話")
            t_other = st.text_input("其他聯絡人")
            if st.form_submit_button("新增試聽紀錄"):
                if t_name and t_course:
                    save_trial_student({
                        "name": t_name, "grade": t_grade, 
                        "course": t_course, "trial_date": t_date.isoformat(), 
                        "stu_mob": t_mobile, "home_tel": t_home,
                        "dad_tel": t_dad, "mom_tel": t_mom, "other_tel": t_other,
                        "created_at": datetime.datetime.now().isoformat()
                    })
                    st.rerun()
                else: st.error("姓名與課程為必填")
        
        trials = get_trial_students()
        if trials:
            st.divider()
            st.caption("尚未決定去留的試聽生 (可手動操作)：")
            for t in trials:
                with st.container(border=True):
                    c_info, c_action = st.columns([3, 2])
                    with c_info:
                        st.markdown(f"**🎓 {t['name']}** ({t['grade']})")
                        st.caption(f"課程：{t['course']} | 日期：{t['trial_date']}")
                    with c_action:
                        b1, b2, b3 = st.columns(3)
                        if b1.button("✅", key=f"man_join_{t['id']}", help="確定入班"):
                            move_trial_to_official(t, t['id'])
                        if b2.button("📂", key=f"man_arch_{t['id']}", help="歸檔"):
                            move_trial_to_potential(t, t['id'])
                        if b3.button("🗑️", key=f"man_del_{t['id']}", help="刪除"):
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

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 智慧排課", "👷 工讀排班", "💰 薪資", "🗑️ 資料管理", "🌴 假期管理"])
    
    with tab1:
        st.subheader("老師課程安排")
        c1, c2 = st.columns(2)
        start_date = c1.date_input("首堂課日期")
        freq_type = c2.radio("排課頻率", ["每週固定 (Regular)", "連續每日 (寒暑假)"], horizontal=True)
        weeks_count = st.number_input("持續次數 (週數/天數)", min_value=1, value=12)
        
        teachers_cfg = get_teachers_data()
        teacher_names = list(teachers_cfg.keys()) + ADMINS
        s_teacher = st.selectbox("授課師資", ["請選擇"] + list(set(teacher_names)))
        c3, c4 = st.columns(2)
        t_start_str = c3.selectbox("開始時間", TIME_OPTIONS, index=18)
        t_end_str = c4.selectbox("結束時間", TIME_OPTIONS, index=24)
        course_options = get_unique_course_names()
        s_course_name = st.selectbox("課程/班別", course_options + ["+ 新增班別..."])
        if s_course_name == "+ 新增班別...": s_course_name = st.text_input("輸入新班別名稱")
        s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "櫃檯"])
        
        if "preview_schedule" not in st.session_state: st.session_state['preview_schedule'] = None
        if st.button("🔍 檢查時段與假日", key="check_shift"):
            if s_teacher == "請選擇": st.error("請選擇師資")
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
                
                teacher_vacs = get_teacher_vacations_cached()
                t_start = datetime.datetime.strptime(t_start_str, "%H:%M").time()
                t_end = datetime.datetime.strptime(t_end_str, "%H:%M").time()
                
                for i in range(weeks_count):
                    if freq_type == "連續每日 (寒暑假)":
                        current_date = start_date + datetime.timedelta(days=i)
                    else:
                        current_date = start_date + datetime.timedelta(weeks=i)
                    d_str = current_date.strftime("%Y%m%d")
                    is_conflict = False
                    reason = ""
                    if d_str in holidays:
                        is_conflict = True
                        reason = holidays[d_str]
                    for v in teacher_vacs:
                        if v['teacher'] == s_teacher:
                            v_start = datetime.datetime.fromisoformat(v['start']).date()
                            v_end = datetime.datetime.fromisoformat(v['end']).date()
                            if v_start <= current_date <= v_end:
                                is_conflict = True
                                r_text = f"老師休假 ({v['reason']})"
                                reason = f"{reason} | {r_text}" if reason else r_text

                    preview.append({
                        "date": current_date,
                        "start_dt": datetime.datetime.combine(current_date, t_start),
                        "end_dt": datetime.datetime.combine(current_date, t_end),
                        "conflict": is_conflict,
                        "reason": reason,
                        "selected": not is_conflict
                    })
                st.session_state['preview_schedule'] = preview
        if st.session_state['preview_schedule']:
            st.divider()
            final_schedule = []
            for idx, item in enumerate(st.session_state['preview_schedule']):
                label = f"第 {idx+1} 堂: {item['date']}"
                if item['conflict']: label += f" ⚠️ {item['reason']}"
                if st.checkbox(label, value=item['selected'], key=f"sch_{idx}"):
                    final_schedule.append(item)
            if st.button(f"確認排入 {len(final_schedule)} 堂課", type="primary"):
                for item in final_schedule:
                    add_event_to_db(s_course_name, item['start_dt'], item['end_dt'], "shift", st.session_state['user'], s_location, s_teacher)
                st.success("排課成功！")
                st.session_state['preview_schedule'] = None
                st.rerun()

    with tab2:
        st.subheader("👷 工讀生排班系統 (防拖曳版)")
        st.caption("已鎖定日期欄位，避免誤觸拖曳。勾選即代表排班。")
        part_timers_list = get_part_timers_list_cached()
        c_pt1, c_pt2 = st.columns(2)
        pt_name = c_pt1.selectbox("選擇工讀生", part_timers_list)
        c_y, c_m = c_pt2.columns(2)
        next_month_date = datetime.date.today() + relativedelta(months=0) 
        pt_year = c_y.number_input("年份", value=next_month_date.year, key="pt_year")
        pt_month = c_m.number_input("月份", value=next_month_date.month, min_value=1, max_value=12, key="pt_month")
        c_t1, c_t2 = st.columns(2)
        pt_start = c_t1.selectbox("上班時間 (批次設定)", TIME_OPTIONS, index=18, key="pt_start")
        pt_end = c_t2.selectbox("下班時間 (批次設定)", TIME_OPTIONS, index=24, key="pt_end")
        st.divider()
        start_of_month = datetime.datetime(pt_year, pt_month, 1)
        end_of_month = start_of_month + relativedelta(months=1)
        existing_shifts_query = db.collection("shifts").where("type", "==", "part_time").where("staff", "==", pt_name).where("start", ">=", start_of_month.isoformat()).where("start", "<", end_of_month.isoformat()).stream()
        existing_shifts_map = {}
        for doc in existing_shifts_query:
            data = doc.to_dict()
            try:
                d_obj = datetime.datetime.strptime(data['start'][:10], "%Y-%m-%d").date()
                existing_shifts_map[d_obj] = doc.id
            except: pass
        st.write(f"正在編輯 **{pt_name}** 在 **{pt_year}年{pt_month}月** 的班表：")
        cols_header = st.columns(7)
        weekdays = ["日", "一", "二", "三", "四", "五", "六"] 
        for idx, w in enumerate(weekdays):
            cols_header[idx].markdown(f"<div style='text-align: center; font-weight: bold; color: #666;'>{w}</div>", unsafe_allow_html=True)
        num_days = py_calendar.monthrange(pt_year, pt_month)[1]
        all_dates = [datetime.date(pt_year, pt_month, d) for d in range(1, num_days + 1)]
        weeks = []
        current_week = []
        first_day_weekday = all_dates[0].weekday() 
        start_padding = (first_day_weekday + 1) % 7
        for _ in range(start_padding): current_week.append(None)
        for d in all_dates:
            current_week.append(d)
            if len(current_week) == 7: weeks.append(current_week); current_week = []
        if current_week:
            while len(current_week) < 7: current_week.append(None)
            weeks.append(current_week)
        final_selected_dates = []
        for week_dates in weeks:
            cols = st.columns(7) 
            for i, d in enumerate(week_dates):
                with cols[i]:
                    if d:
                        with st.container(border=True):
                            st.markdown(f"<div style='text-align: center; font-weight: bold; margin-bottom: 5px;'>{d.day}</div>", unsafe_allow_html=True)
                            is_checked = d in existing_shifts_map
                            val = st.checkbox("排班", value=is_checked, key=f"chk_{pt_name}_{d}", label_visibility="collapsed")
                            if val: final_selected_dates.append(d)
                    else: st.write("") 
        st.divider()
        if st.button(f"💾 儲存變更", type="primary", key="save_pt_table"):
            current_selected_set = set(final_selected_dates)
            original_set = set(existing_shifts_map.keys())
            to_add = current_selected_set - original_set
            to_remove_dates = original_set - current_selected_set
            to_remove_ids = [existing_shifts_map[d] for d in to_remove_dates]
            t_s = datetime.datetime.strptime(pt_start, "%H:%M").time()
            t_e = datetime.datetime.strptime(pt_end, "%H:%M").time()
            if to_remove_ids: batch_delete_events(to_remove_ids)
            add_count = 0
            for date_obj in to_add:
                start_dt = datetime.datetime.combine(date_obj, t_s)
                end_dt = datetime.datetime.combine(date_obj, t_e)
                add_event_to_db("工讀", start_dt, end_dt, "part_time", st.session_state['user'], staff=pt_name)
                add_count += 1
            if not to_add and not to_remove_ids: st.info("資料未變更")
            else:
                msg = []
                if add_count: msg.append(f"新增 {add_count} 筆")
                if to_remove_ids: msg.append(f"刪除 {len(to_remove_ids)} 筆")
                st.success(f"更新成功！({', '.join(msg)})")
                time.sleep(1); st.rerun()

    with tab3:
        st.subheader("👨‍🏫 師資薪資設定")
        with st.form("add_teacher"):
            c_t1, c_t2 = st.columns([2, 1])
            new_t_name = c_t1.text_input("老師姓名 (輸入現有姓名即為修改)")
            new_t_rate = c_t2.number_input("單堂薪資", min_value=0, step=50)
            if st.form_submit_button("新增 / 更新"):
                if new_t_name:
                    save_teacher_data(new_t_name, new_t_rate)
                    st.rerun()
        teachers_cfg = get_teachers_data()
        if teachers_cfg:
            with st.expander("查看目前師資與薪資列表"):
                t_list = [{"姓名": k, "單價": v.get('rate', 0)} for k, v in teachers_cfg.items()]
                st.dataframe(t_list)
        st.divider()
        st.subheader("📊 薪資結算報告")
        col_m1, col_m2 = st.columns(2)
        q_year = col_m1.number_input("年份", value=datetime.date.today().year, key="sal_y")
        q_month = col_m2.number_input("月份", value=datetime.date.today().month, min_value=1, max_value=12, key="sal_m")
        if st.button("計算本月薪資"):
            start_date = datetime.datetime(q_year, q_month, 1)
            end_date = start_date + relativedelta(months=1)
            start_str = start_date.isoformat(); end_str = end_date.isoformat()
            docs = db.collection("shifts").where("type", "==", "shift").where("start", ">=", start_str).where("start", "<", end_str).stream()
            teachers_cfg = get_teachers_data()
            report = {}
            for doc in docs:
                d = doc.to_dict(); t_name = d.get("teacher", "未知")
                if t_name in ADMINS or t_name == "未知": continue
                if t_name not in report: report[t_name] = {"count": 0, "rate": teachers_cfg.get(t_name, {}).get("rate", 0)}
                report[t_name]["count"] += 1
            res = []
            for name, info in report.items():
                res.append({"姓名": name, "單價": info["rate"], "堂數": info["count"], "應發": info["count"]*info["rate"]})
            if res: st.dataframe(res, use_container_width=True)
            else: st.info("無紀錄")

    with tab4:
        st.subheader("🗑️ 資料庫強制管理")
        all_docs = db.collection("shifts").order_by("start", direction=firestore.Query.DESCENDING).stream()
        data_list = []
        for doc in all_docs:
            d = doc.to_dict(); d['id'] = doc.id; data_list.append(d)
        if data_list:
            event_map = {f"{item.get('start')[:10]} | {item.get('title')} ({item.get('staff')})": item['id'] for item in data_list}
            selected_labels = st.multiselect("選擇刪除項目", list(event_map.keys()))
            if selected_labels and st.button("🗑️ 確認刪除"):
                batch_delete_events([event_map[l] for l in selected_labels])
                st.rerun()

    with tab5:
        st.subheader("🌴 老師假期設定")
        st.caption("設定老師的請假區間，系統會在智慧排課時自動偵測衝突。")
        teachers_cfg = get_teachers_data()
        teacher_names = list(teachers_cfg.keys()) + ADMINS
        with st.form("add_vacation"):
            c1, c2 = st.columns(2)
            v_teacher = c1.selectbox("選擇老師", ["請選擇"] + list(set(teacher_names)))
            v_reason = c2.text_input("事由 (例如：出國、進修)")
            c3, c4 = st.columns(2)
            v_start = c3.date_input("開始日期")
            v_end = c4.date_input("結束日期")
            if st.form_submit_button("💾 儲存假期"):
                if v_teacher == "請選擇": st.error("請選擇老師")
                elif v_end < v_start: st.error("結束日期不能早於開始日期")
                else:
                    start_dt = datetime.datetime.combine(v_start, datetime.time(0, 0))
                    end_dt = datetime.datetime.combine(v_end, datetime.time(23, 59))
                    conflict_docs = db.collection("shifts").where("type", "==", "shift").where("teacher", "==", v_teacher).where("start", ">=", start_dt.isoformat()).where("start", "<=", end_dt.isoformat()).stream()
                    conflict_ids = [d.id for d in conflict_docs]
                    save_teacher_vacation(v_teacher, start_dt, end_dt, v_reason)
                    if conflict_ids:
                        st.session_state['pending_reschedule'] = conflict_ids
                        st.warning(f"⚠️ 偵測到該時段已有 {len(conflict_ids)} 堂課！建議標記為「需調課」。")
                    else:
                        st.success("假期設定成功！無衝突課程。")
                        st.rerun()
        if 'pending_reschedule' in st.session_state and st.session_state['pending_reschedule']:
            if st.button("🚩 將衝突課程標記為「⚠️ 需調課」", type="primary"):
                batch_mark_reschedule(st.session_state['pending_reschedule'])
                st.session_state['pending_reschedule'] = None 
                st.rerun()
        st.divider()
        st.write("📋 **目前假期列表**")
        vacs = get_teacher_vacations_cached()
        if vacs:
            for v in vacs:
                c1, c2, c3 = st.columns([2, 3, 1])
                c1.write(f"**{v['teacher']}**")
                c2.write(f"{v['start'][:10]} ~ {v['end'][:10]} ({v['reason']})")
                if c3.button("🗑️", key=f"del_vac_{v['id']}"):
                    delete_teacher_vacation(v['id']); st.rerun()
        else: st.info("尚無假期紀錄")

# --- 5. 主介面邏輯 ---

tz = pytz.timezone('Asia/Taipei')
now = datetime.datetime.now(tz)

if now.hour == 6 and now.minute <= 30 and st.session_state['user'] is not None:
    st.session_state['user'] = None; st.session_state['is_admin'] = False; st.rerun()

if st.session_state['user'] is None:
    st.title("🏫 鳩特數理行政班表")
    st.info("請先登入以使用系統")
    with st.form("main_login_form"):
        user = st.selectbox("請選擇您的身份", ["請選擇"] + LOGIN_LIST)
        password = st.text_input("請輸入密碼", type="password")
        if st.form_submit_button("登入", use_container_width=True):
            if user == "請選擇": st.error("請選擇身份")
            else:
                is_valid = False; is_admin = False
                if user in ADMINS:
                    if password == ADMIN_PASSWORD: is_valid = True; is_admin = True
                else:
                    if password == STAFF_PASSWORD: is_valid = True
                if is_valid:
                    st.session_state['user'] = user; st.session_state['is_admin'] = is_admin; st.rerun()
                else: st.error("密碼錯誤")
    st.stop() 

col_title, col_login = st.columns([3, 1], vertical_alignment="center")
with col_title: st.title("🏫 鳩特數理行政班表")
with col_login:
    st.markdown(f"👤 **{st.session_state['user']}**")
    if st.button("登出", type="secondary", use_container_width=True):
        st.session_state['user'] = None; st.session_state['is_admin'] = False; st.rerun()

st.divider()

clean_cols = st.columns(5)
areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室", "鳩辦公室"]
for i, area in enumerate(areas):
    status = get_cleaning_status(area)
    days_diff = "N/A"; delta_days = 999; last_cleaner = "無紀錄"
    if status:
        try:
            ts = status['timestamp']
            if isinstance(ts, str): ts = datetime.datetime.fromisoformat(ts)
            if ts.tzinfo: ts = ts.replace(tzinfo=None)
            delta_days = (datetime.datetime.now() - ts).days
            days_diff = f"{delta_days} 天"; last_cleaner = status.get('staff', '未知')
        except: pass
    color = "green" if delta_days <= 3 else "orange" if delta_days <= 6 else "red"
    with clean_cols[i]:
        st.caption(area)
        st.markdown(f"### :{color}[{days_diff}]")
        st.caption(f"最後打掃：{last_cleaner}")
        if st.button("已清潔", key=f"clean_{i}", use_container_width=True):
            if st.session_state['user']: log_cleaning(area, st.session_state['user']); st.rerun()
            else: st.error("請先登入")

st.divider()

pending_trials = get_trial_students()
follow_up_list = []
for t in pending_trials:
    try:
        t_date = datetime.date.fromisoformat(t['trial_date'])
        if datetime.date.today() >= (t_date + datetime.timedelta(days=7)): follow_up_list.append(t)
    except: pass

if follow_up_list:
    st.markdown("### 🔔 試聽追蹤提醒")
    st.info("以下學生已試聽滿一週，請確認是否入班？")
    for t in follow_up_list:
        with st.container(border=True):
            st.markdown(f"**🎓 {t['name']}** ({t['grade']})")
            st.caption(f"試聽：{t['course']} ({t['trial_date']})")
            c1, c2 = st.columns(2)
            if c1.button("✅ 入班", key=f"alert_join_{t['id']}"): move_trial_to_official(t, t['id'])
            if c2.button("📂 歸檔", key=f"alert_arch_{t['id']}"): move_trial_to_potential(t, t['id'])
    st.divider()

if st.session_state['user']:
    if st.button("📂 資料管理", type="secondary", use_container_width=True): show_general_management_dialog()
    if st.session_state['is_admin']:
        if st.button("⚙️ 管理員後台", type="secondary", use_container_width=True): show_admin_dialog()

# --- 6. 智慧點名系統 ---
st.divider()
st.subheader("📋 每日點名")
col_date_btn, col_date_info = st.columns([1, 3], vertical_alignment="center")
if col_date_btn.button("📅 切換日期", type="secondary"): show_roll_call_review_dialog()
if 'selected_calendar_date' in st.session_state: selected_date = st.session_state['selected_calendar_date']
else: selected_date = datetime.date.today()
with col_date_info: st.markdown(f"**{selected_date}**")

date_key = selected_date.isoformat()
db_record = get_roll_call_from_db(date_key)
all_students = get_students_data_cached()
course_to_students_map = defaultdict(list) 
for s in all_students:
    c = s.get('班別'); n = s.get('姓名')
    if c and n: course_to_students_map[c].append(s) # Store full student obj

all_events = get_all_events_cached()
daily_courses_display = []
daily_courses_filter = []     
course_location_map = {} 

for e in all_events:
    if e.get('start', '').startswith(date_key) and e.get('extendedProps', {}).get('type') == 'shift':
        props = e.get('extendedProps', {})
        c_title = props.get('title', '')
        c_loc = props.get('location', '')
        if c_loc == "線上": c_loc = "櫃檯"
        daily_courses_filter.append(c_title)
        course_location_map[c_title] = c_loc
        if c_loc: daily_courses_display.append(f"{c_title} ({c_loc})")
        else: daily_courses_display.append(c_title)

# Filter Logic: Check Departure Date
target_students = []
if daily_courses_display:
    st.caption(f"當日課程：{'、'.join(daily_courses_display)}")
    for c_name in daily_courses_filter:
        for s_obj in course_to_students_map.get(c_name, []):
            # Check leaving date
            leave_date = s_obj.get('leaving_date')
            if leave_date and date_key > leave_date: continue # Skip if left
            target_students.append(s_obj['姓名'])
else: st.caption("當日無排課紀錄")

target_students = list(set(target_students))

if db_record:
    current_data = db_record
    if "absent" not in current_data: current_data["absent"] = []
    if "present" not in current_data: current_data["present"] = []
    if "leave" not in current_data: current_data["leave"] = []
    
    recorded_students = set(current_data["absent"] + current_data["present"] + current_data["leave"])
    missing_students = [s for s in target_students if s not in recorded_students]
    if missing_students: current_data["absent"].extend(missing_students)
else:
    current_data = {"absent": target_students, "present": [], "leave": []}

def save_current_state(absent, present, leave):
    save_data = {
        "absent": absent, "present": present, "leave": leave,
        "updated_at": datetime.datetime.now().isoformat(),
        "updated_by": st.session_state['user']
    }
    save_roll_call_to_db(date_key, save_data)
    st.toast("點名資料已儲存", icon="💾"); time.sleep(0.5); st.rerun()

if st.session_state['user']:
    if not target_students and not current_data['absent'] and not current_data['present'] and not current_data['leave']:
        st.info("今日無課程或無學生名單，無須點名")
    else:
        st.markdown("### 🔴 尚未報到")
        st.caption("💡 點擊姓名即可選取，再次點擊取消。")
        pending_list = set(current_data['absent']) 
        
        if pending_list:
            all_selected_present = []
            all_selected_leave = []
            displayed_students = set()
            sorted_today_courses = sorted(list(set(daily_courses_filter)))
            
            for course_name in sorted_today_courses:
                # Get names from student objects, filtered by leaving date again just in case
                students_in_this_course = []
                for s_obj in course_to_students_map.get(course_name, []):
                     leave_date = s_obj.get('leaving_date')
                     if leave_date and date_key > leave_date: continue
                     students_in_this_course.append(s_obj['姓名'])

                s_list = [s for s in students_in_this_course if s in pending_list]
                
                if s_list:
                    displayed_students.update(s_list)
                    loc_str = course_location_map.get(course_name, "")
                    title_suffix = f" @ {loc_str}" if loc_str else ""
                    
                    with st.expander(f"📘 {course_name}{title_suffix} ({len(s_list)}人)", expanded=True):
                        st.markdown("**👇 點擊出席學生 (到)**")
                        selected_p = st.pills(f"pills_present_{course_name}", options=s_list, selection_mode="multi", key=f"pills_p_{course_name}_{date_key}", label_visibility="collapsed")
                        remaining_for_leave = [s for s in s_list if s not in selected_p]
                        if remaining_for_leave:
                            st.markdown("**👇 點擊請假學生 (假)**")
                            selected_l = st.pills(f"pills_leave_{course_name}", options=remaining_for_leave, selection_mode="multi", key=f"pills_l_{course_name}_{date_key}", label_visibility="collapsed")
                            all_selected_leave.extend(selected_l)
                        all_selected_present.extend(selected_p)

            leftover_students = [s for s in pending_list if s not in displayed_students]
            if leftover_students:
                with st.expander(f"❓ 其他 / 未分類 ({len(leftover_students)}人)", expanded=True):
                    st.caption("這些學生不在今日排定的課程名單中，但出現在未到列表")
                    st.markdown("**👇 點擊出席學生 (到)**")
                    l_p = st.pills("pills_other_p", options=leftover_students, selection_mode="multi", key=f"p_other_{date_key}")
                    rem_l = [s for s in leftover_students if s not in l_p]
                    if rem_l:
                        st.markdown("**👇 點擊請假學生 (假)**")
                        l_l = st.pills("pills_other_l", options=rem_l, selection_mode="multi", key=f"l_other_{date_key}")
                        all_selected_leave.extend(l_l)
                    all_selected_present.extend(l_p)

            st.divider()
            if st.button("🚀 確認送出 (更新狀態)", type="primary", use_container_width=True):
                conflict = set(all_selected_present) & set(all_selected_leave)
                if conflict: st.error(f"錯誤：{', '.join(conflict)} 不能同時選取")
                elif not all_selected_present and not all_selected_leave: st.warning("您未選取任何學生")
                else:
                    new_absent = [p for p in current_data['absent'] if p not in all_selected_present and p not in all_selected_leave]
                    new_present = current_data['present'] + all_selected_present
                    new_leave = current_data['leave'] + all_selected_leave
                    save_current_state(new_absent, new_present, new_leave)
        else: st.success("🎉 全員已完成點名！")

        st.divider()
        with st.expander(f"已到 ({len(current_data['present'])}) / 請假 ({len(current_data['leave'])})", expanded=False):
            if current_data['present']:
                st.write("**🟢 已到 (點選以取消)**")
                undo_p = st.pills("undo_present", options=current_data['present'], selection_mode="multi", key=f"undo_p_{date_key}", label_visibility="collapsed")
                if undo_p:
                    if st.button("↩️ 還原選取的學生 (移回未到)", key="btn_undo_p"):
                        new_present = [p for p in current_data['present'] if p not in undo_p]
                        new_absent = current_data['absent'] + undo_p
                        save_current_state(new_absent, new_present, current_data['leave'])
            if current_data['leave']:
                st.divider()
                st.write("**🟡 請假 (點選以取消)**")
                undo_l = st.pills("undo_leave", options=current_data['leave'], selection_mode="multi", key=f"undo_l_{date_key}", label_visibility="collapsed")
                if undo_l:
                    if st.button("↩️ 還原選取的學生 (移回未到)", key="btn_undo_l"):
                        new_leave = [p for p in current_data['leave'] if p not in undo_l]
                        new_absent = current_data['absent'] + undo_l
                        save_current_state(current_data['absent'], current_data['present'], new_leave)
else:
    st.warning("請登入以進行點名")

# --- 7. 行事曆 (Calendar) 移至底部 ---
st.divider()
st.subheader("📅 行事曆")

calendar_options = {
    "editable": True, 
    "headerToolbar": {
        "left": "prev,next",
        "center": "title",
        "right": "today dayGridMonth,listMonth,timeGridDay" 
    },
    "initialView": "dayGridMonth", 
    "height": "650px", "locale": "zh-tw",
    "slotMinTime": "08:00:00", 
    "slotMaxTime": "22:00:00", 
    "titleFormat": {"year": "numeric", "month": "long"},
    "slotLabelFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
    "eventTimeFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
    "views": {
        "dayGridMonth": {"displayEventTime": False},
        "listMonth": {"displayEventTime": True},
        "timeGridDay": {"displayEventTime": True} 
    },
    "selectable": True,
}
cal = calendar(events=all_events, options=calendar_options, callbacks=['dateClick', 'eventClick'])

if cal.get("dateClick"):
    clicked = cal["dateClick"]["date"]
    try:
        if "T" in clicked:
            if clicked.endswith("Z"): clicked = clicked.replace("Z", "+00:00")
            dt_utc = datetime.datetime.fromisoformat(clicked)
            if dt_utc.tzinfo is None: dt_utc = dt_utc.replace(tzinfo=datetime.timezone.utc)
            d_obj = dt_utc.astimezone(pytz.timezone('Asia/Taipei')).date()
        else: d_obj = datetime.datetime.strptime(clicked, "%Y-%m-%d").date()
        if st.session_state['user']: show_notice_dialog(default_date=d_obj)
    except: pass

if cal.get("eventClick"):
    if st.session_state['user']:
        show_edit_event_dialog(cal["eventClick"]["event"]["id"], cal["eventClick"]["event"]["extendedProps"])
