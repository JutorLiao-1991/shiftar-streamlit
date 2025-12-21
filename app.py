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
import uuid
import calendar as py_calendar
from collections import defaultdict

# --- 1. 系統設定 ---
st.set_page_config(page_title="鳩特數理行政班表", page_icon="🏫", layout="wide")

# CSS 優化
st.markdown("""
<style>
    [data-testid="column"] {
        min-width: 0px !important;
        padding: 0px !important;
    }
    div[data-testid="stCheckbox"] {
        padding-top: 5px;
    }
    div[data-testid="stCheckbox"] label {
        min-height: 0px;
    }
</style>
""", unsafe_allow_html=True)

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
LOGIN_LIST = ["鳩特", "鳩婆", "世軒", "竣揚", "暐傑"]
STAFF_PASSWORD = "88888888"
ADMIN_PASSWORD = "150508"

GRADE_OPTIONS = [
    "小一", "小二", "小三", "小四", "小五", "小六",
    "國一", "國二", "國三",
    "高一", "高二", "高三",
    "畢業"
]

TIME_OPTIONS = []
for h in range(9, 23):
    TIME_OPTIONS.append(f"{h:02d}:00")
    if h != 22:
        TIME_OPTIONS.append(f"{h:02d}:30")

# --- 3. 資料庫存取 (快取層) ---

def get_unique_course_names():
    default_courses = [
        "小四數學", "小五數學", "小六數學",
        "國一數學", "國二數學", "國三數學", "國二理化", "國二自然",
        "高一數學", "高一物理", "高一化學"
    ]
    doc = db.collection("settings").document("courses").get()
    if doc.exists:
        saved_list = doc.to_dict().get("list", [])
        combined = list(set(default_courses + saved_list))
        def sort_key(x):
            order = ["小", "國", "高"]
            for i, prefix in enumerate(order):
                if x.startswith(prefix):
                    return (i, x)
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

@st.cache_data(ttl=300)
def get_part_timers_list_cached():
    doc = db.collection("settings").document("part_timers").get()
    if doc.exists:
        return doc.to_dict().get("list", ["工讀生A", "工讀生B", "世軒(工讀)", "竣揚(工讀)"])
    return ["工讀生A", "工讀生B", "世軒(工讀)", "竣揚(工讀)"]

def save_part_timers_list(new_list):
    db.collection("settings").document("part_timers").set({"list": new_list})
    get_part_timers_list_cached.clear()
    st.toast("工讀生名單已更新")

def promote_student_grade(grade_str):
    g = str(grade_str).strip()
    progression = {
        "小一": "小二", "小二": "小三", "小三": "小四", "小四": "小五", "小五": "小六", "小六": "國一",
        "國一": "國二", "國二": "國三", "國三": "高一",
        "高一": "高二", "高二": "高三", "高三": "畢業"
    }
    if g in progression: return progression[g]
    if g == "畢業": return "畢業"
    return g

# ★ 點名資料庫功能
def get_roll_call_data(date_str):
    doc = db.collection("roll_call_records").document(date_str).get()
    if doc.exists:
        return doc.to_dict()
    return None

def save_roll_call_data_to_db(date_str, data):
    db.collection("roll_call_records").document(date_str).set(data)

@st.cache_data(ttl=600)
def get_all_events_cached():
    events = []
    try:
        docs = db.collection("shifts").stream()
        for doc in docs:
            data = doc.to_dict()
            title_text = data.get("title", "")
            color = "#3788d8"
            
            if data.get("type") == "shift":
                teacher = data.get("teacher", "未知")
                course = data.get("title", "課程")
                title_text = f"{course} ({teacher})"
                color = "#28a745"
            elif data.get("type") == "part_time":
                staff_name = data.get("staff", "")
                title_text = f"{staff_name}"
                color = "#6f42c1"
            elif data.get("type") == "notice":
                category = data.get("category", "其他")
                title_text = f"[{category}] {title_text}"
                if category == "調課": color = "#d63384"
                elif category == "考試": color = "#dc3545"
                elif category == "活動": color = "#0d6efd"
                elif category == "任務": 
                    color = "#FF4500"
                    title_text = f"🔥 {title_text}"
                else: color = "#ffc107"
            
            sanitized_props = {}
            for k, v in data.items():
                if isinstance(v, (datetime.datetime, datetime.date)):
                    sanitized_props[k] = str(v)
                else:
                    sanitized_props[k] = v

            events.append({
                "id": doc.id,
                "title": title_text, 
                "start": data.get("start"), 
                "end": data.get("end"),
                "color": color, 
                "allDay": data.get("type") == "notice",
                "extendedProps": sanitized_props
            })
    except: pass
    
    try:
        year = datetime.date.today().year
        resp = requests.get(f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json").json()
        for day in resp:
            if day.get('isHoliday'):
                events.append({
                    "id": f"holiday_{day['date']}",
                    "title": f"🌴 {day['description']}", "start": day['date'], 
                    "allDay": True, "display": "background", "backgroundColor": "#ffebee",
                    "editable": False,
                    "extendedProps": {"type": "holiday"}
                })
    except: pass
    return events

def add_event_to_db(title, start, end, type, user, location="", teacher_name="", category="", staff=""):
    db.collection("shifts").add({
        "title": title, "start": start.isoformat(), "end": end.isoformat(),
        "type": type, "staff": staff if staff else user, 
        "location": location, 
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

def batch_delete_events(doc_ids):
    batch = db.batch()
    for doc_id in doc_ids:
        doc_ref = db.collection("shifts").document(doc_id)
        batch.delete(doc_ref)
    batch.commit()
    get_all_events_cached.clear()
    st.toast(f"成功刪除 {len(doc_ids)} 筆資料！")

def get_cleaning_status(area_name):
    doc = db.collection("latest_cleaning_status").document(area_name).get()
    return doc.to_dict() if doc.exists else None

def log_cleaning(area, user):
    now = datetime.datetime.now()
    db.collection("cleaning_logs").add({"area": area, "staff": user, "timestamp": now})
    db.collection("latest_cleaning_status").document(area).set({"area": area, "staff": user, "timestamp": now})
    st.toast(f"✨ {area} 清潔完成！", icon="🧹")

# --- 4. 彈出視窗 UI (由主程式呼叫) ---

@st.dialog("👤 人員登入")
def show_login_dialog():
    user = st.selectbox("請選擇您的身份", ["請選擇"] + LOGIN_LIST)
    password = st.text_input("請輸入密碼", type="password")
    if st.button("登入", use_container_width=True):
        if user == "請選擇": st.error("請選擇身份"); return
        is_valid = False; is_admin = False
        if user in ADMINS:
            if password == ADMIN_PASSWORD: is_valid = True; is_admin = True
        else:
            if password == STAFF_PASSWORD: is_valid = True
        if is_valid:
            st.session_state['user'] = user
            st.session_state['is_admin'] = is_admin
            st.rerun()
        else: st.error("密碼錯誤")

@st.dialog("✏️ 編輯/刪除 行程")
def show_edit_event_dialog(event_id, props):
    if props.get('type') == 'holiday':
        st.warning("🌴 這是國定假日，無法編輯。")
        return

    st.write(f"正在編輯：**{props.get('title', '')}**")
    
    if props.get('type') == 'shift':
        new_title = st.text_input("課程名稱", props.get('title'))
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_title})
            if 'active_dialog_event' in st.session_state: del st.session_state['active_dialog_event'] # 清除狀態
            st.rerun()
        if col2.button("🗑️ 刪除此課程", type="secondary"):
            delete_event_from_db(event_id)
            if 'active_dialog_event' in st.session_state: del st.session_state['active_dialog_event']
            st.rerun()

    elif props.get('type') == 'part_time':
        new_staff = st.text_input("工讀生姓名", props.get('staff'))
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"staff": new_staff})
            if 'active_dialog_event' in st.session_state: del st.session_state['active_dialog_event']
            st.rerun()
        if col2.button("🗑️ 刪除此班表", type="secondary"):
            delete_event_from_db(event_id)
            if 'active_dialog_event' in st.session_state: del st.session_state['active_dialog_event']
            st.rerun()
            
    elif props.get('type') == 'notice':
        cat_opts = ["調課", "考試", "活動", "任務", "其他"]
        curr_cat = props.get('category', '其他')
        idx = cat_opts.index(curr_cat) if curr_cat in cat_opts else 4
        new_cat = st.selectbox("分類", cat_opts, index=idx)
        new_content = st.text_area("內容", props.get('title')) 
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_content, "category": new_cat})
            if 'active_dialog_event' in st.session_state: del st.session_state['active_dialog_event']
            st.rerun()
        if col2.button("🗑️ 刪除此公告", type="secondary"):
            delete_event_from_db(event_id)
            if 'active_dialog_event' in st.session_state: del st.session_state['active_dialog_event']
            st.rerun()

@st.dialog("📢 新增公告 / 交接")
def show_notice_dialog(default_date=None):
    if default_date is None: default_date = datetime.date.today()
    st.info(f"正在建立 **{default_date}** 的事項")
    edit_date = st.date_input("日期", default_date)
    category = st.selectbox("分類 (必選)", ["調課", "考試", "活動", "任務", "其他"])
    notice_content = st.text_area("事項內容", placeholder="請輸入詳細內容...")
    if st.button("發布公告", use_container_width=True):
        start_dt = datetime.datetime.combine(edit_date, datetime.time(9,0))
        end_dt = datetime.datetime.combine(edit_date, datetime.time(10,0))
        add_event_to_db(notice_content, start_dt, end_dt, "notice", st.session_state['user'], category=category)
        if 'active_dialog_notice' in st.session_state: del st.session_state['active_dialog_notice']
        st.toast("公告已發布")
        st.rerun()

@st.dialog("📂 資料管理")
def show_general_management_dialog():
    tab1, tab2 = st.tabs(["🎓 學生名單", "👷 工讀生名單"])
    with tab1:
        st.caption("所有員工皆可編輯")
        if st.session_state['is_admin']:
            if st.button("⬆️ 執行年度升級 (7月)", type="primary"): show_promotion_confirm_dialog()
        
        uploaded_file = st.file_uploader("📂 從 Excel/CSV 匯入", type=['csv'])
        if uploaded_file:
            try:
                df = pd.read_csv(uploaded_file)
                if all(col in df.columns for col in ["姓名", "年級", "班別", "聯絡人1", "電話1"]):
                    if st.button("確認匯入"):
                        new_students = df.to_dict('records')
                        new_students = [{k: (v if pd.notna(v) else "") for k, v in r.items()} for r in new_students]
                        current_data = get_students_data_cached()
                        save_students_data(current_data + new_students)
                        st.success(f"匯入 {len(new_students)} 筆")
                else: st.error("CSV 格式錯誤")
            except Exception as e: st.error(f"讀取失敗: {e}")

        with st.expander("手動新增學生"):
            with st.form("manual_student"):
                ms_name = st.text_input("姓名 (必填)")
                c1, c2 = st.columns(2)
                ms_grade = c1.selectbox("年級 (必填)", GRADE_OPTIONS)
                course_opts = get_unique_course_names()
                ms_class = c2.selectbox("班別 (必填)", course_opts)
                c3, c4 = st.columns(2)
                ms_c1 = c3.text_input("聯絡人1 (必填)")
                ms_p1 = c4.text_input("電話1 (必填)")
                c5, c6 = st.columns(2)
                ms_c2 = c5.text_input("聯絡人2"); ms_p2 = c6.text_input("電話2")
                if st.form_submit_button("新增"):
                    if ms_name and ms_grade and ms_class and ms_c1 and ms_p1:
                        new_rec = {"姓名": ms_name, "年級": ms_grade, "班別": ms_class, "聯絡人1": ms_c1, "電話1": ms_p1, "聯絡人2": ms_c2, "電話2": ms_p2}
                        current = get_students_data_cached()
                        current.append(new_rec)
                        save_students_data(current)
                        st.rerun()
                    else: st.error("缺必填欄位")
        
        current_students = get_students_data_cached()
        if current_students:
            df_stu = pd.DataFrame(current_students)
            st.dataframe(df_stu, use_container_width=True)
            to_del = st.multiselect("刪除學生", [s['姓名'] for s in current_students])
            if to_del and st.button("確認刪除"):
                new_list = [s for s in current_students if s['姓名'] not in to_del]
                save_students_data(new_list)
                st.rerun()

    with tab2:
        current_pts = get_part_timers_list_cached()
        c_p1, c_p2 = st.columns([2, 1])
        new_pt = c_p1.text_input("輸入新工讀生姓名")
        if c_p2.button("新增工讀生"):
            if new_pt and new_pt not in current_pts:
                current_pts.append(new_pt)
                save_part_timers_list(current_pts)
                st.rerun()
        pts_to_del = st.multiselect("刪除工讀生", current_pts)
        if pts_to_del and st.button("確認刪除工讀生"):
            save_part_timers_list([p for p in current_pts if p not in pts_to_del])
            st.rerun()

@st.dialog("🎓 確認年度升級")
def show_promotion_confirm_dialog():
    st.warning("⚠️ 警告：此操作不可逆！")
    if st.button("我確定要升級所有學生", type="primary"):
        current_data = get_students_data_cached()
        updated_list = []
        for stu in current_data:
            new_stu = stu.copy()
            new_stu['年級'] = promote_student_grade(stu.get('年級', ''))
            updated_list.append(new_stu)
        save_students_data(updated_list)
        st.success("成功升級！")
        st.rerun()

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3, tab4 = st.tabs(["📅 智慧排課", "👷 工讀排班", "💰 薪資", "🗑️ 強制刪除"])
    
    with tab1:
        st.subheader("老師課程安排")
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
        if s_course_name == "+ 新增班別...": s_course_name = st.text_input("輸入新班別名稱")
        s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
        
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
                t_start = datetime.datetime.strptime(t_start_str, "%H:%M").time()
                t_end = datetime.datetime.strptime(t_end_str, "%H:%M").time()
                for i in range(weeks_count):
                    current_date = start_date + datetime.timedelta(weeks=i)
                    d_str = current_date.strftime("%Y%m%d")
                    preview.append({
                        "date": current_date,
                        "start_dt": datetime.datetime.combine(current_date, t_start),
                        "end_dt": datetime.datetime.combine(current_date, t_end),
                        "conflict": d_str in holidays,
                        "reason": holidays.get(d_str, ""),
                        "selected": not (d_str in holidays)
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
        part_timers_list = get_part_timers_list_cached()
        c_pt1, c_pt2 = st.columns(2)
        pt_name = c_pt1.selectbox("選擇工讀生", part_timers_list)
        c_y, c_m = c_pt2.columns(2)
        pt_year = c_y.number_input("年份", value=datetime.date.today().year, key="pt_year")
        pt_month = c_m.number_input("月份", value=datetime.date.today().month, min_value=1, max_value=12, key="pt_month")
        c_t1, c_t2 = st.columns(2)
        pt_start = c_t1.selectbox("上班時間", TIME_OPTIONS, index=18, key="pt_start")
        pt_end = c_t2.selectbox("下班時間", TIME_OPTIONS, index=24, key="pt_end")
        st.divider()
        
        num_days = py_calendar.monthrange(pt_year, pt_month)[1]
        weekdays_map = ["一", "二", "三", "四", "五", "六", "日"]
        schedule_data = []
        for day in range(1, num_days + 1):
            curr_date = datetime.date(pt_year, pt_month, day)
            schedule_data.append({
                "日期": f"{pt_month:02d}/{day:02d} ({weekdays_map[curr_date.weekday()]})",
                "排班": False,
                "raw_date": curr_date
            })
        
        df_schedule = pd.DataFrame(schedule_data)
        edited_df = st.data_editor(
            df_schedule,
            column_config={
                "日期": st.column_config.TextColumn("日期", disabled=True),
                "排班": st.column_config.CheckboxColumn("排班", required=True),
                "raw_date": None
            },
            hide_index=True, use_container_width=True, height=400
        )
        
        if st.button("確認排入", type="primary"):
            selected_rows = edited_df[edited_df["排班"] == True]
            if selected_rows.empty: st.error("未選擇日期")
            else:
                t_s = datetime.datetime.strptime(pt_start, "%H:%M").time()
                t_e = datetime.datetime.strptime(pt_end, "%H:%M").time()
                for _, row in selected_rows.iterrows():
                    d = row["raw_date"]
                    if isinstance(d, pd.Timestamp): d = d.date()
                    start_dt = datetime.datetime.combine(d, t_s)
                    end_dt = datetime.datetime.combine(d, t_e)
                    add_event_to_db("工讀", start_dt, end_dt, "part_time", st.session_state['user'], staff=pt_name)
                st.success("工讀排班成功！")
                st.rerun()

    with tab3:
        # (薪資計算代碼簡略保留)
        col_m1, col_m2 = st.columns(2)
        q_year = col_m1.number_input("年份", value=datetime.date.today().year, key="sal_y")
        q_month = col_m2.number_input("月份", value=datetime.date.today().month, min_value=1, max_value=12, key="sal_m")
        if st.button("計算本月薪資"):
            start_date = datetime.datetime(q_year, q_month, 1)
            end_date = start_date + relativedelta(months=1)
            start_str = start_date.isoformat(); end_str = end_date.isoformat()
            docs = db.collection("shifts").where("type", "==", "shift")\
                     .where("start", ">=", start_str).where("start", "<", end_str).stream()
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
            st.session_state['user'] = None; st.session_state['is_admin'] = False; st.rerun()
    else:
        if st.button("登入", type="primary", use_container_width=True): show_login_dialog()

st.divider()

# 環境整潔
clean_cols = st.columns(4)
areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室"]
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

# 主功能按鈕
if st.session_state['user']:
    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("➕ 新增公告/交接", type="primary", use_container_width=True):
            st.session_state['active_dialog_notice'] = True
    if st.button("📂 資料管理", type="secondary", use_container_width=True):
        show_general_management_dialog()
    if st.session_state['is_admin']:
        if st.button("⚙️ 管理員後台", type="secondary", use_container_width=True): show_admin_dialog()

# ★ 處理主程式觸發的 Dialog
if st.session_state.get('active_dialog_notice'):
    # 如果是從日曆點擊來的，會有預設日期
    d_date = st.session_state.get('selected_calendar_date', None)
    show_notice_dialog(d_date)

if st.session_state.get('active_dialog_event'):
    e_id = st.session_state['active_dialog_event']['id']
    props = st.session_state['active_dialog_event']['props']
    show_edit_event_dialog(e_id, props)

# --- 行事曆 Fragment ---
@st.fragment
def calendar_component():
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
        "locale": "zh-tw",
        "titleFormat": {"year": "2-digit", "month": "numeric"},
        "slotLabelFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
        "eventTimeFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
        "views": {"dayGridMonth": {"displayEventTime": False}, "listMonth": {"displayEventTime": True}},
        "selectable": True,
    }
    cal = calendar(events=all_events, options=calendar_options, callbacks=['dateClick', 'eventClick'])
    
    # Fragment 只負責傳遞訊號，不開啟 Dialog
    if cal.get("dateClick"):
        clicked_date = cal["dateClick"]["date"]
        try:
            d_obj = datetime.datetime.strptime(clicked_date[:10], "%Y-%m-%d").date()
            st.session_state['selected_calendar_date'] = d_obj
            # 如果想點日期直接開公告，可打開下面這行：
            # st.session_state['active_dialog_notice'] = True
            st.rerun()
        except: pass

    if cal.get("eventClick"):
        st.session_state['active_dialog_event'] = {
            "id": cal["eventClick"]["event"]["id"],
            "props": cal["eventClick"]["event"]["extendedProps"]
        }
        st.rerun()

calendar_component()

# --- 6. 智慧點名系統 (已修復同步問題) ---
st.divider()
st.subheader("📋 每日點名")

# 決定日期
if 'selected_calendar_date' in st.session_state:
    selected_date = st.session_state['selected_calendar_date']
else:
    selected_date = datetime.date.today()

date_key = selected_date.isoformat()
st.info(f"正在檢視：**{selected_date}** 的點名紀錄")

# ★ 關鍵：從資料庫讀取，確保換電腦也能看到
db_record = get_roll_call_data(date_key)

# 找出當日課程與學生
daily_courses = []
all_events = get_all_events_cached()
for e in all_events:
    if e.get('start', '').startswith(date_key) and e.get('extendedProps', {}).get('type') == 'shift':
        daily_courses.append(e.get('extendedProps', {}).get('title', ''))

all_students = get_students_data_cached()
target_students = []
if daily_courses:
    st.write(f"📅 當日課程：{'、'.join(daily_courses)}")
    for stu in all_students:
        if stu.get('班別') in daily_courses: target_students.append(stu['姓名'])
else:
    st.write("📅 當日無排課紀錄")

# 初始化 Session State (如果沒有 DB 紀錄，則使用預設值)
# 使用 loaded_flag 防止每次 rerun 都重置使用者正在點名的狀態
if f"roll_loaded_{date_key}" not in st.session_state:
    if db_record:
        st.session_state[f"roll_{date_key}"] = db_record
    else:
        st.session_state[f"roll_{date_key}"] = {
            "absent": target_students, "present": [], "leave": [], "dirty": False
        }
    st.session_state[f"roll_loaded_{date_key}"] = True

# 取得當前操作資料
current_roll = st.session_state[f"roll_{date_key}"]

if st.session_state['user']:
    if not current_roll['absent'] and not current_roll['present'] and not current_roll['leave']:
        st.info("無須點名")
    else:
        with st.expander("點名表單", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("### 🔴 未到")
                if current_roll['absent']:
                    cols = st.columns(4)
                    for i, s in enumerate(current_roll['absent']):
                        if cols[i%4].button(s, key=f"ab_{s}_{date_key}"):
                            current_roll['absent'].remove(s); current_roll['present'].append(s); current_roll['dirty'] = True; st.rerun()
            with c2:
                st.markdown("### 🟢 已到")
                for s in current_roll['present']:
                    if st.button(f"✅ {s}", key=f"pr_{s}_{date_key}", type="primary", use_container_width=True):
                        current_roll['present'].remove(s); current_roll['absent'].append(s); current_roll['dirty'] = True; st.rerun()
            with c3:
                st.markdown("### 🟡 請假")
                opts = ["選擇..."] + current_roll['absent']
                val = st.selectbox("請假", opts, key=f"lv_sel_{date_key}")
                if val != "選擇...":
                    current_roll['absent'].remove(val); current_roll['leave'].append(val); current_roll['dirty'] = True; st.rerun()
                for s in current_roll['leave']:
                    if st.button(f"🤒 {s}", key=f"le_{s}_{date_key}", use_container_width=True):
                        current_roll['leave'].remove(s); current_roll['absent'].append(s); current_roll['dirty'] = True; st.rerun()

        # 儲存按鈕
        btn_type = "primary" if current_roll.get('dirty') else "secondary"
        if st.button("💾 儲存點名紀錄", type=btn_type, use_container_width=True):
            save_data = {
                "absent": current_roll['absent'], "present": current_roll['present'], "leave": current_roll['leave'],
                "updated_at": datetime.datetime.now().isoformat(), "updated_by": st.session_state['user']
            }
            save_roll_call_data_to_db(date_key, save_data)
            current_roll['dirty'] = False
            st.success(f"已儲存 {selected_date} 的紀錄！")
            st.rerun()
else:
    st.warning("請登入以進行點名")
