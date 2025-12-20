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
LOGIN_LIST = ["鳩特", "鳩婆", "世軒", "竣揚", "暐傑"]
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
    st.toast(f"已更新 {name} 的資料")

def delete_teacher_data(name):
    db.collection("teachers_config").document(name).delete()
    st.toast(f"已移除 {name}")

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
                # loc = data.get("location", "未知") 
                teacher = data.get("teacher", "未知")
                course = data.get("title", "課程")
                # 顯示：課程 (老師)
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

# --- 4. 彈出視窗 UI ---

@st.dialog("👤 人員登入")
def show_login_dialog():
    user = st.selectbox("請選擇您的身份", ["請選擇"] + LOGIN_LIST)
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
    if props.get('type') == 'holiday':
        st.warning("🌴 這是國定假日，無法編輯。")
        if st.button("關閉"): st.rerun()
        return

    st.write(f"正在編輯：**{props.get('title', '')}**")
    
    if props.get('type') == 'shift':
        new_title = st.text_input("課程名稱", props.get('title'))
        st.caption("💡 如需修改時間、老師或教室，建議直接刪除後重新排課。")
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_title})
            st.rerun()
        if col2.button("🗑️ 刪除此課程", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()

    elif props.get('type') == 'part_time':
        st.info("工讀生班表")
        new_staff = st.text_input("工讀生姓名", props.get('staff'))
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"staff": new_staff})
            st.rerun()
        if col2.button("🗑️ 刪除此班表", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()
            
    elif props.get('type') == 'notice':
        cat_opts = ["調課", "考試", "活動", "其他"]
        curr_cat = props.get('category', '其他')
        idx = cat_opts.index(curr_cat) if curr_cat in cat_opts else 3
        new_cat = st.selectbox("分類", cat_opts, index=idx)
        new_content = st.text_area("內容", props.get('title')) 
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_content, "category": new_cat})
            st.rerun()
        if col2.button("🗑️ 刪除此公告", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()
    else:
        st.warning("未知類型的資料")
        if st.button("🗑️ 強制刪除", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()

@st.dialog("📢 新增公告 / 交接")
def show_notice_dialog(default_date=None):
    if default_date is None:
        default_date = datetime.date.today()
    st.info(f"正在建立 **{default_date}** 的事項")
    category = st.selectbox("分類 (必選)", ["調課", "考試", "活動", "其他"])
    notice_content = st.text_area("事項內容", placeholder="請輸入詳細內容...")
    if st.button("發布公告", use_container_width=True):
        start_dt = datetime.datetime.combine(default_date, datetime.time(9,0))
        end_dt = datetime.datetime.combine(default_date, datetime.time(10,0))
        add_event_to_db(notice_content, start_dt, end_dt, "notice", st.session_state['user'], category=category)
        st.toast("公告已發布")
        st.rerun()

@st.dialog("🎓 確認年度升級")
def show_promotion_confirm_dialog():
    st.warning("⚠️ **警告：此操作不可逆！**")
    st.write("這將會把所有學生的年級往上加一級。")
    if st.button("我確定要升級所有學生", type="primary"):
        current_data = get_students_data_cached()
        promoted_count = 0
        updated_list = []
        for stu in current_data:
            old_grade = stu.get('年級', '')
            new_grade = promote_student_grade(old_grade)
            new_stu = stu.copy()
            new_stu['年級'] = new_grade
            updated_list.append(new_stu)
            if old_grade != new_grade: promoted_count += 1
        save_students_data(updated_list)
        st.success(f"成功升級 {promoted_count} 位學生！")
        st.rerun()

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 智慧排課", "👷 工讀排班", "💰 薪資", "📝 資料設定", "🗑️ 資料管理"])
    
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
        if s_course_name == "+ 新增班別...":
            s_course_name = st.text_input("輸入新班別名稱")
            
        s_location = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
        
        if "preview_schedule" not in st.session_state:
            st.session_state['preview_schedule'] = None

        if st.button("🔍 檢查時段與假日", key="check_shift"):
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
                if item['conflict']: label += f" ⚠️ 撞期: {item['reason']}"
                if st.checkbox(label, value=item['selected'], key=f"sch_{idx}"):
                    final_schedule.append(item)
            
            if st.button(f"確認排入 {len(final_schedule)} 堂課", type="primary"):
                count = 0
                for item in final_schedule:
                    add_event_to_db(s_course_name, item['start_dt'], item['end_dt'], "shift", st.session_state['user'], s_location, s_teacher)
                    count += 1
                st.success(f"成功排入 {count} 堂課！")
                st.session_state['preview_schedule'] = None
                st.rerun()

    with tab2:
        st.subheader("👷 工讀生排班系統")
        st.caption("請選擇工讀生與月份，然後勾選上班日期。")
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
        st.write(f"請勾選 **{pt_name}** 在 **{pt_year}年{pt_month}月** 的上班日：")
        num_days = py_calendar.monthrange(pt_year, pt_month)[1]
        cols = st.columns(7)
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        for idx, w in enumerate(weekdays):
            cols[idx].write(f"**{w}**")
        selected_dates = []
        first_day_weekday = datetime.date(pt_year, pt_month, 1).weekday()
        cols = st.columns(7)
        col_idx = first_day_weekday 
        for day in range(1, num_days + 1):
            curr_date = datetime.date(pt_year, pt_month, day)
            with cols[col_idx]:
                if st.checkbox(f"{day}", key=f"pt_day_{day}"):
                    selected_dates.append(curr_date)
            col_idx += 1
            if col_idx > 6:
                col_idx = 0
                cols = st.columns(7)
                
        st.divider()
        if st.button(f"確認排入 {len(selected_dates)} 個班次", type="primary", key="save_pt"):
            if not selected_dates:
                st.error("未勾選任何日期")
            else:
                t_s = datetime.datetime.strptime(pt_start, "%H:%M").time()
                t_e = datetime.datetime.strptime(pt_end, "%H:%M").time()
                count = 0
                for date_obj in selected_dates:
                    start_dt = datetime.datetime.combine(date_obj, t_s)
                    end_dt = datetime.datetime.combine(date_obj, t_e)
                    add_event_to_db("工讀", start_dt, end_dt, "part_time", st.session_state['user'], staff=pt_name)
                    count += 1
                st.success(f"成功新增 {count} 筆工讀班表！")
                st.rerun()

    with tab3:
        col_m1, col_m2 = st.columns(2)
        q_year = col_m1.number_input("年份", value=datetime.date.today().year, key="sal_y")
        q_month = col_m2.number_input("月份", value=datetime.date.today().month, min_value=1, max_value=12, key="sal_m")
        if st.button("計算本月薪資"):
            # ★ 修正 Bug: 移除 composite query，改為 Python 內過濾
            start_date = datetime.datetime(q_year, q_month, 1)
            end_date = start_date + relativedelta(months=1)
            start_str = start_date.isoformat()
            end_str = end_date.isoformat()
            
            # 1. 只篩選時間 (不篩選 type) -> 避開 FailedPrecondition
            docs = db.collection("shifts")\
                     .where("start", ">=", start_str).where("start", "<", end_str).stream()
            
            teachers_cfg = get_teachers_data()
            report = {}
            for doc in docs:
                d = doc.to_dict()
                # 2. 在這裡過濾 type
                if d.get("type") != "shift": 
                    continue
                    
                t_name = d.get("teacher", "未知")
                if t_name in ADMINS or t_name == "未知": continue
                if t_name not in report:
                    report[t_name] = {"count": 0, "rate": teachers_cfg.get(t_name, {}).get("rate", 0)}
                report[t_name]["count"] += 1
            res = []
            total = 0
            for name, info in report.items():
                sub = info["count"] * info["rate"]
                total += sub
                res.append({"姓名": name, "單價": info["rate"], "堂數": info["count"], "應發": sub})
            if res:
                st.dataframe(res, use_container_width=True)
                st.metric("總計", f"${total:,}")
            else:
                st.info("無紀錄")

    with tab4:
        st.subheader("🎓 學生名單管理")
        col_op1, col_op2 = st.columns([1, 2])
        with col_op1:
            if st.button("⬆️ 執行年度升級 (7月)", type="primary"):
                show_promotion_confirm_dialog()
        
        st.divider()
        st.subheader("👷 工讀生名單管理")
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
            new_list = [p for p in current_pts if p not in pts_to_del]
            save_part_timers_list(new_list)
            st.rerun()

        st.divider()
        # ★ 修正：將師資管理功能做得更完整
        st.subheader("👨‍🏫 師資名單管理")
        
        # 顯示目前列表
        current_teachers = list(get_teachers_data().keys())
        
        c_t1, c_t2, c_t3 = st.columns([2, 1, 1])
        new_t_name = c_t1.text_input("老師姓名")
        new_t_rate = c_t2.number_input("單價", min_value=0, step=100)
        if c_t3.button("新增/更新老師"):
            if new_t_name:
                save_teacher_data(new_t_name, new_t_rate)
                st.rerun()
                
        # 刪除老師
        t_to_del = st.multiselect("選擇要移除的老師", current_teachers)
        if t_to_del and st.button("確認移除選取老師"):
            for t in t_to_del:
                delete_teacher_data(t)
            st.rerun()

        st.divider()
        uploaded_file = st.file_uploader("📂 從 Excel/CSV 匯入", type=['csv'])
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file)
                required_cols = ["姓名", "年級", "班別", "聯絡人1", "電話1"]
                if all(col in df.columns for col in required_cols):
                    if st.button("確認匯入"):
                        new_students = df.to_dict('records')
                        new_students = [{k: (v if pd.notna(v) else "") for k, v in r.items()} for r in new_students]
                        current_data = get_students_data_cached()
                        merged_data = current_data + new_students
                        save_students_data(merged_data)
                        st.success(f"匯入 {len(new_students)} 筆")
                else:
                    st.error(f"CSV 需包含標題：{required_cols}")
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
                if st.form_submit_button("新增"):
                    if ms_name and ms_grade and ms_class and ms_c1 and ms_p1:
                        new_record = {"姓名": ms_name, "年級": ms_grade, "班別": ms_class, "聯絡人1": ms_c1, "電話1": ms_p1, "聯絡人2": ms_c2, "電話2": ms_p2}
                        current = get_students_data_cached()
                        current.append(new_record)
                        save_students_data(current)
                        st.rerun()
                    else: st.error("缺必填欄位")
        st.caption("學生列表 (可刪除)")
        current_students = get_students_data_cached()
        if current_students:
            df_stu = pd.DataFrame(current_students)
            st.dataframe(df_stu, use_container_width=True)
            to_del = st.multiselect("刪除學生", [s['姓名'] for s in current_students])
            if to_del and st.button("確認刪除"):
                new_list = [s for s in current_students if s['姓名'] not in to_del]
                save_students_data(new_list)
                st.rerun()

    with tab5:
        st.subheader("🗑️ 資料庫強制管理 (批次刪除)")
        st.caption("請小心使用，刪除後無法復原。")
        all_docs = db.collection("shifts").order_by("start", direction=firestore.Query.DESCENDING).stream()
        data_list = []
        for doc in all_docs:
            d = doc.to_dict()
            d['id'] = doc.id
            data_list.append(d)
        if data_list:
            event_map = {}
            for item in data_list:
                label = f"{item.get('start')[:10]} | {item.get('title')} ({item.get('staff')})"
                event_map[label] = item['id']
            selected_labels = st.multiselect("請選擇要刪除的項目", options=list(event_map.keys()))
            if selected_labels:
                st.warning(f"⚠️ 您即將刪除 {len(selected_labels)} 筆資料，確定嗎？")
                if st.button("🗑️ 確認批次刪除", type="primary"):
                    batch_ids = [event_map[label] for label in selected_labels]
                    batch_delete_events(batch_ids)
                    st.rerun()
        else:
            st.info("目前資料庫是空的")

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

# --- 環境整潔監控 ---
st.subheader("🧹 環境整潔監控")
clean_cols = st.columns(4)
areas = ["櫃檯茶水間", "大教室", "小教室", "流放教室"]

for i, area in enumerate(areas):
    status = get_cleaning_status(area)
    days_diff = "N/A"
    delta_days = 999
    last_cleaner = "無紀錄"
    
    if status:
        try:
            ts = status['timestamp']
            if isinstance(ts, str): ts = datetime.datetime.fromisoformat(ts)
            if ts.tzinfo: ts = ts.replace(tzinfo=None)
            delta_days = (datetime.datetime.now() - ts).days
            days_diff = f"{delta_days} 天"
            last_cleaner = status.get('staff', '未知')
        except: pass
    
    if delta_days <= 3:
        color_code = "green"
    elif delta_days <= 6:
        color_code = "orange"
    else:
        color_code = "red"

    with clean_cols[i]:
        st.caption(area)
        st.markdown(f"### :{color_code}[{days_diff}]")
        st.caption(f"最後打掃：{last_cleaner}")
        if st.button("已清潔", key=f"clean_{i}", use_container_width=True):
            if st.session_state['user']:
                log_cleaning(area, st.session_state['user'])
                st.rerun()
            else:
                st.error("請先登入")

st.divider()

if st.session_state['user']:
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
    "locale": "zh-tw",
    "titleFormat": {"year": "2-digit", "month": "numeric"},
    "slotLabelFormat": {
        "hour": "2-digit",
        "minute": "2-digit",
        "hour12": False
    },
    "eventTimeFormat": {
        "hour": "2-digit",
        "minute": "2-digit",
        "hour12": False
    },
    "views": {
        "dayGridMonth": {"displayEventTime": False},
        "listMonth": {"displayEventTime": True}
    },
    "scrollTime": datetime.datetime.now().strftime("%H:%M:%S")
}

cal_return = calendar(events=all_events, options=calendar_options, callbacks=['dateClick', 'eventClick'])

if cal_return.get("dateClick"):
    clicked_date_str = cal_return["dateClick"]["date"].split("T")[0]
    date_obj = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()
    if st.session_state['user']:
        show_notice_dialog(default_date=date_obj)
    else:
        st.toast("請先登入才能新增事項", icon="🔒")

if cal_return.get("eventClick"):
    event_id = cal_return["eventClick"]["event"]["id"]
    props = cal_return["eventClick"]["event"]["extendedProps"]
    if st.session_state['user']:
        show_edit_event_dialog(event_id, props)


# --- 6. 智慧點名系統 ---
st.divider()
st.subheader("📋 每日點名")

selected_date = datetime.date.today()
if cal_return and "dateClick" in cal_return:
    clicked_date_str = cal_return["dateClick"]["date"].split("T")[0]
    selected_date = datetime.datetime.strptime(clicked_date_str, "%Y-%m-%d").date()

st.info(f"日期：**{selected_date}**")

daily_courses = []
s_date_str = selected_date.isoformat()
for e in all_events:
    if e.get('start', '').startswith(s_date_str) and 'extendedProps' in e:
        props = e['extendedProps']
        if props.get('type') == 'shift':
            daily_courses.append(props.get('title', ''))

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
