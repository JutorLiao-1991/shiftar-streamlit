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
import re
from collections import defaultdict

# --- 1. 系統設定 ---
st.set_page_config(page_title="鳩特數理行政班表", page_icon="🏫", layout="wide")

# CSS 優化
st.markdown("""
<style>
    /* 讓欄位最小寬度為 0，防止被強制換行 */
    [data-testid="column"] {
        min-width: 0px !important;
        padding: 0px !important;
    }
    /* 調整 checkbox 樣式 */
    div[data-testid="stCheckbox"] {
        padding-top: 5px;
        min-height: 0px;
        text-align: center;
    }
    div[data-testid="stCheckbox"] label {
        min-height: 0px;
    }
    .stDataFrame {
        margin-bottom: -1rem;
    }
    div[data-testid="stMarkdownContainer"] p {
        text-align: center;
        font-weight: bold;
    }
    /* 讓按鈕文字置中且不換行 */
    div[data-testid="stButton"] button {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        padding: 0.25rem 0.5rem;
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

def get_roll_call_from_db(date_str):
    doc = db.collection("roll_call_records").document(date_str).get()
    if doc.exists: return doc.to_dict()
    return None

def save_roll_call_to_db(date_str, data):
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

# ★ 正規化函式：移除特殊字元與空格，用於寬鬆比對
def normalize_string(s):
    if not isinstance(s, str): return str(s)
    # 移除 [ ] ( ) 【 】 還有 - _ 以及所有空白
    return re.sub(r'[ \[\]\(\)（）【】\-_\s]', '', s)

# --- 4. 彈出視窗 UI ---

# 登入功能
@st.dialog("👤 人員登入")
def show_login_dialog():
    with st.form("login_form"):
        user = st.selectbox("請選擇您的身份", ["請選擇"] + LOGIN_LIST)
        password = st.text_input("請輸入密碼", type="password")
        submitted = st.form_submit_button("登入", use_container_width=True)
        if submitted:
            if user == "請選擇": st.error("請選擇身份")
            else:
                if (user in ADMINS and password == ADMIN_PASSWORD) or (user not in ADMINS and password == STAFF_PASSWORD):
                    st.session_state['user'] = user
                    st.session_state['is_admin'] = (user in ADMINS)
                    st.rerun()
                else: st.error("密碼錯誤")

@st.dialog("✏️ 編輯/刪除 行程")
def show_edit_event_dialog(event_id, props):
    if props.get('type') == 'holiday':
        st.warning("🌴 這是國定假日，無法編輯。"); 
        if st.button("關閉"): st.rerun()
        return
    st.write(f"正在編輯：**{props.get('title', '')}**")
    if props.get('type') == 'shift':
        new_title = st.text_input("課程名稱", props.get('title'))
        c1, c2 = st.columns(2)
        if c1.button("💾 儲存修改", type="primary"): update_event_in_db(event_id, {"title": new_title}); st.rerun()
        if c2.button("🗑️ 刪除此課程", type="secondary"): delete_event_from_db(event_id); st.rerun()
    elif props.get('type') == 'part_time':
        new_staff = st.text_input("工讀生姓名", props.get('staff'))
        c1, c2 = st.columns(2)
        if c1.button("💾 儲存修改", type="primary"): update_event_in_db(event_id, {"staff": new_staff}); st.rerun()
        if c2.button("🗑️ 刪除此班表", type="secondary"): delete_event_from_db(event_id); st.rerun()
    elif props.get('type') == 'notice':
        cat_opts = ["調課", "考試", "活動", "任務", "其他"]
        curr_cat = props.get('category', '其他')
        idx = cat_opts.index(curr_cat) if curr_cat in cat_opts else 4
        new_cat = st.selectbox("分類", cat_opts, index=idx)
        new_content = st.text_area("內容", props.get('title')) 
        c1, c2 = st.columns(2)
        if c1.button("💾 儲存修改", type="primary"): update_event_in_db(event_id, {"title": new_content, "category": new_cat}); st.rerun()
        if c2.button("🗑️ 刪除此公告", type="secondary"): delete_event_from_db(event_id); st.rerun()
    else:
        if st.button("🗑️ 強制刪除", type="secondary"): delete_event_from_db(event_id); st.rerun()

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
        st.toast("公告已發布"); st.rerun()

@st.dialog("📅 回顧點名紀錄")
def show_roll_call_review_dialog():
    st.info("請選擇要查看或補點名的日期")
    pick_date = st.date_input("選擇日期", value=datetime.date.today())
    if st.button("確認前往", type="primary", use_container_width=True):
        st.session_state['selected_calendar_date'] = pick_date; st.rerun()

@st.dialog("🎓 確認年度升級")
def show_promotion_confirm_dialog():
    st.warning("⚠️ **警告：此操作不可逆！**")
    if st.button("我確定要升級所有學生", type="primary"):
        current_data = get_students_data_cached()
        updated_list = []
        for stu in current_data:
            new_stu = stu.copy(); new_stu['年級'] = promote_student_grade(stu.get('年級', ''))
            updated_list.append(new_stu)
        save_students_data(updated_list); st.success("成功升級！"); st.rerun()

@st.dialog("📂 資料管理")
def show_general_management_dialog():
    tab1, tab2 = st.tabs(["🎓 學生名單", "👷 工讀生名單"])
    current_students = get_students_data_cached()
    student_map = {f"{s.get('姓名')} ({s.get('年級', '')})": s for s in current_students}
    
    with tab1:
        if st.session_state['is_admin']:
            if st.button("⬆️ 執行年度升級 (7月)", type="primary"): show_promotion_confirm_dialog()
        
        uploaded_file = st.file_uploader("📂 從 Excel/CSV 匯入", type=['csv', 'xlsx'])
        if uploaded_file:
            try:
                df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                if st.button("確認匯入"):
                    new_students = []
                    is_erp = '聯絡方式' in df.columns and '所屬班級' in df.columns
                    if is_erp:
                        for index, row in df.iterrows():
                            name = str(row.get('姓名', '')).strip()
                            grade = str(row.get('年級', '')).strip()
                            raw_class_str = str(row.get('所屬班級', '')).strip()
                            class_list = re.split(r'[\n,]+', raw_class_str)
                            contact_str = str(row.get('聯絡方式', ''))
                            s_phone, dad_phone, mom_phone, home_phone, other_phone = "", "", "", "", ""
                            for line in contact_str.split('\n'):
                                line = line.strip()
                                num_match = re.search(r'\d[\d\-]+', line)
                                if not num_match: continue
                                number = num_match.group(0)
                                if "個人手機" in line: s_phone = number
                                elif "爸爸" in line: dad_phone = number
                                elif "媽媽" in line: mom_phone = number
                                elif "Tel" in line or "家" in line: home_phone = number
                                else: other_phone = (other_phone + f", {line}") if other_phone else line
                            for cls in class_list:
                                cls = cls.strip()
                                if not cls: continue
                                new_students.append({"姓名": name, "年級": grade, "班別": cls, "學生手機": s_phone, "家裡": home_phone, "爸爸": dad_phone, "媽媽": mom_phone, "其他家人": other_phone})
                    else:
                        for r in df.to_dict('records'):
                            if r.get('姓名'):
                                for c in re.split(r'[\n,]+', str(r.get('班別', ''))):
                                    if c.strip(): 
                                        rec = r.copy(); rec['班別'] = c.strip(); new_students.append(rec)
                    if new_students:
                        save_students_data(get_students_data_cached() + new_students)
                        st.success(f"成功匯入 {len(new_students)} 筆"); st.rerun()
            except Exception as e: st.error(f"失敗: {e}")

        with st.expander("手動新增學生"):
            select_existing = st.selectbox("快速帶入舊生資料", ["不使用"] + list(student_map.keys()))
            def_vals = defaultdict(str, student_map[select_existing]) if select_existing != "不使用" else defaultdict(str, {"年級": "小一"})
            
            c1, c2 = st.columns(2)
            ms_name = c1.text_input("姓名", value=def_vals['姓名'])
            ms_phone = c2.text_input("手機", value=def_vals['學生手機'])
            c3, c4 = st.columns(2)
            idx = GRADE_OPTIONS.index(def_vals['年級']) if def_vals['年級'] in GRADE_OPTIONS else 0
            ms_grade = c3.selectbox("年級", GRADE_OPTIONS, index=idx)
            ms_class = c4.selectbox("班別", get_unique_course_names())
            st.divider(); st.caption("聯絡電話")
            c5, c6 = st.columns(2)
            ms_home = c5.text_input("家裡", value=def_vals['家裡'])
            ms_dad = c6.text_input("爸爸", value=def_vals['爸爸'])
            c7, c8 = st.columns(2)
            ms_mom = c7.text_input("媽媽", value=def_vals['媽媽'])
            ms_other = c8.text_input("其他", value=def_vals['其他家人'])
            
            if st.button("新增"):
                if ms_name and ms_grade and ms_class and any([ms_home, ms_dad, ms_mom, ms_other]):
                    new_rec = {"姓名": ms_name, "年級": ms_grade, "班別": ms_class, "學生手機": ms_phone, "家裡": ms_home, "爸爸": ms_dad, "媽媽": ms_mom, "其他家人": ms_other}
                    save_students_data(get_students_data_cached() + [new_rec]); st.success("已新增"); st.rerun()
                else: st.error("缺必填欄位或電話")

        st.divider(); st.caption("📝 學生列表 (直接編輯)")
        if current_students:
            # 準備可編輯的 DataFrame
            df_stu = pd.DataFrame([{col: s.get(col, "") for col in ["姓名", "學生手機", "年級", "班別", "家裡", "爸爸", "媽媽", "其他家人"]} for s in current_students])
            # 加一個不顯示的 ID 欄位來對應原始資料
            df_stu["_id"] = [f"{s.get('姓名')}_{s.get('班別')}" for s in current_students]
            
            edited_df = st.data_editor(
                df_stu, 
                use_container_width=True, 
                num_rows="dynamic", 
                column_config={"_id": None}, # 隱藏 _id 欄位
                key="stu_edit"
            )
            
            if st.button("💾 儲存修改"):
                # 修復語法錯誤：先轉換成 list，再處理
                raw_list = edited_df.fillna("").to_dict('records')
                clean_data = []
                for r in raw_list:
                    # 移除 _id 欄位
                    if "_id" in r: del r["_id"]
                    # 確保有姓名才存入
                    if r.get("姓名"):
                        clean_data.append(r)
                
                save_students_data(clean_data)
                st.success("已更新"); st.rerun()

    with tab2:
        current_pts = get_part_timers_list_cached()
        c_p1, c_p2 = st.columns([2, 1])
        new_pt = c_p1.text_input("輸入新工讀生")
        if c_p2.button("新增"):
            if new_pt and new_pt not in current_pts: save_part_timers_list(current_pts + [new_pt]); st.rerun()
        pts_del = st.multiselect("刪除", current_pts)
        if pts_del and st.button("確認刪除"): save_part_timers_list([p for p in current_pts if p not in pts_del]); st.rerun()

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3, tab4 = st.tabs(["📅 排課", "👷 工讀", "💰 薪資", "🗑️ 管理"])
    with tab1:
        c1, c2 = st.columns(2)
        start_date = c1.date_input("首堂日期"); weeks = c2.number_input("週數", 1, 12, 12)
        s_teacher = st.selectbox("師資", ["請選擇"] + list(set(list(get_teachers_data().keys()) + ADMINS)))
        c3, c4 = st.columns(2)
        t_start = datetime.datetime.strptime(c3.selectbox("開始", TIME_OPTIONS, index=18), "%H:%M").time()
        t_end = datetime.datetime.strptime(c4.selectbox("結束", TIME_OPTIONS, index=24), "%H:%M").time()
        c_name = st.selectbox("班別", get_unique_course_names() + ["+ 新增"])
        if c_name == "+ 新增": c_name = st.text_input("新班別名稱")
        loc = st.selectbox("教室", ["大教室", "小教室", "流放教室", "線上"])
        if st.button("🔍 檢查"):
            if s_teacher == "請選擇": st.error("請選師資")
            else:
                save_course_name(c_name); preview = []
                holidays = {}
                try: holidays = {d['date']: d['description'] for d in requests.get(f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{start_date.year}.json").json() if d['isHoliday']}
                except: pass
                for i in range(weeks):
                    d = start_date + datetime.timedelta(weeks=i); d_str = d.strftime("%Y%m%d")
                    preview.append({"date": d, "s": datetime.datetime.combine(d, t_start), "e": datetime.datetime.combine(d, t_end), "cf": d_str in holidays, "r": holidays.get(d_str, "")})
                st.session_state['preview'] = preview
        if st.session_state.get('preview'):
            final = []
            for i, item in enumerate(st.session_state['preview']):
                if st.checkbox(f"{item['date']} {('⚠️'+item['r']) if item['cf'] else ''}", value=not item['cf'], key=f"sch_{i}"): final.append(item)
            if st.button(f"排入 {len(final)} 堂"):
                for it in final: add_event_to_db(c_name, it['s'], it['e'], "shift", st.session_state['user'], loc, s_teacher)
                st.success("成功"); st.session_state['preview'] = None; st.rerun()

    with tab2:
        pts = get_part_timers_list_cached()
        c1, c2 = st.columns(2)
        pt = c1.selectbox("工讀生", pts)
        y = c2.number_input("年", value=datetime.date.today().year); m = c2.number_input("月", 1, 12, datetime.date.today().month)
        c3, c4 = st.columns(2)
        ts = datetime.datetime.strptime(c3.selectbox("上班", TIME_OPTIONS, index=18), "%H:%M").time()
        te = datetime.datetime.strptime(c4.selectbox("下班", TIME_OPTIONS, index=24), "%H:%M").time()
        st.divider(); cols = st.columns(7)
        for w in ["日","一","二","三","四","五","六"]: cols[list(["日","一","二","三","四","五","六"]).index(w)].markdown(f"**{w}**")
        dates = [datetime.date(y, m, d) for d in range(1, py_calendar.monthrange(y, m)[1]+1)]
        weeks, curr = [], [None]*((dates[0].weekday()+1)%7)
        for d in dates:
            curr.append(d)
            if len(curr)==7: weeks.append(curr); curr=[]
        if curr: weeks.append(curr + [None]*(7-len(curr)))
        sel_dates = []
        for w_idx, wk in enumerate(weeks):
            cols = st.columns(7)
            for i, d in enumerate(wk):
                if d and cols[i].checkbox(f"{d.day}", key=f"pt_{w_idx}_{i}"): sel_dates.append(d)
        if st.button("排入班表"):
            for d in sel_dates: add_event_to_db("工讀", datetime.datetime.combine(d, ts), datetime.datetime.combine(d, te), "part_time", st.session_state['user'], staff=pt)
            st.success("成功"); st.rerun()

    with tab3:
        with st.form("add_t"):
            c1, c2 = st.columns([2,1])
            tn = c1.text_input("姓名"); tr = c2.number_input("薪資", step=50)
            if st.form_submit_button("更新"): save_teacher_data(tn, tr); st.rerun()
        rates = get_teachers_data()
        if rates: st.dataframe([{"姓名":k, "單價":v['rate']} for k,v in rates.items()])
        st.divider()
        c1, c2 = st.columns(2)
        y = c1.number_input("年", value=datetime.date.today().year, key="sy"); m = c2.number_input("月", 1, 12, datetime.date.today().month, key="sm")
        if st.button("計算"):
            s = datetime.datetime(y, m, 1); e = s + relativedelta(months=1)
            docs = db.collection("shifts").where("type","==","shift").where("start",">=",s.isoformat()).where("start","<",e.isoformat()).stream()
            rep = defaultdict(int)
            for d in docs: 
                t = d.to_dict().get("teacher")
                if t not in ADMINS: rep[t] += 1
            st.dataframe([{"姓名":k, "堂數":v, "應發": v*rates.get(k,{}).get('rate',0)} for k,v in rep.items()])

    with tab4:
        docs = list(db.collection("shifts").order_by("start", direction=firestore.Query.DESCENDING).stream())
        if docs:
            opts = {f"{d.to_dict()['start'][:10]} {d.to_dict()['title']} ({d.to_dict().get('staff')})": d.id for d in docs}
            sels = st.multiselect("刪除", list(opts.keys()))
            if sels and st.button("確認刪除"): batch_delete_events([opts[s] for s in sels]); st.rerun()

# --- 5. 主介面 ---
tz = pytz.timezone('Asia/Taipei'); now = datetime.datetime.now(tz)
if now.hour == 6 and now.minute <= 30 and st.session_state['user']: st.session_state['user']=None; st.rerun()

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
c1.title("🏫 鳩特數理行政班表"); c2.markdown(f"👤 **{st.session_state['user']}**")
if c2.button("登出"): st.session_state['user']=None; st.rerun()
st.divider()

cols = st.columns(4)
for i, area in enumerate(["櫃檯茶水間", "大教室", "小教室", "流放教室"]):
    stat = get_cleaning_status(area)
    diff = (datetime.datetime.now() - datetime.datetime.fromisoformat(stat['timestamp'])).days if stat else 999
    clr = "green" if diff<=3 else "orange" if diff<=6 else "red"
    with cols[i]:
        st.markdown(f"{area} ### :{clr}[{diff}天]"); st.caption(f"上次: {stat.get('staff','無') if stat else '無'}")
        if st.button("已清潔", key=f"cl_{i}"): log_cleaning(area, st.session_state['user']); st.rerun()
st.divider()

if st.button("📂 資料管理"): show_general_management_dialog()
if st.session_state['is_admin'] and st.button("⚙️ 後台"): show_admin_dialog()

cal = calendar(events=get_all_events_cached(), options={"initialView": "dayGridMonth", "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,listMonth"}, "height": "650px", "locale": "zh-tw"}, callbacks=['dateClick', 'eventClick'])
if cal.get("dateClick"): 
    d = cal["dateClick"]["date"].split('T')[0]
    show_notice_dialog(datetime.datetime.strptime(d, "%Y-%m-%d").date())
if cal.get("eventClick"): show_edit_event_dialog(cal["eventClick"]["event"]["id"], cal["eventClick"]["event"]["extendedProps"])

st.divider(); st.subheader("📋 每日點名")
if st.button("📅 切換日期"): show_roll_call_review_dialog()

sel_date = st.session_state.get('selected_calendar_date', datetime.date.today())
st.info(f"檢視：**{sel_date}**")
d_key = sel_date.isoformat()
db_rec = get_roll_call_from_db(d_key)

# ★ 核心修正：超級模糊比對邏輯 + Debug
courses_show = []
courses_filter = []
for e in get_all_events_cached():
    if e['start'].startswith(d_key) and e['extendedProps'].get('type') == 'shift':
        t = e['extendedProps'].get('title', '')
        # 儲存「正規化後」的課程名稱以便比對
        courses_filter.append(normalize_string(t))
        courses_show.append(t + (f" ({e['extendedProps']['location']})" if e['extendedProps'].get('location') else ""))

# Debug 區塊
with st.expander("🕵️‍♂️ 偵錯模式 (看不到學生請點我)"):
    st.write(f"今日課程 (正規化)：{courses_filter}")
    st.write("---")
    st.write("比對失敗的學生：")
    for s in get_students_data_cached():
        s_cls = normalize_string(s.get('班別', ''))
        matched = False
        for c in courses_filter:
            # 只要課程名稱出現在學生班級裡，或反過來，就算對到
            if (c in s_cls) or (s_cls in c): matched = True
        if not matched and s_cls:
             st.caption(f"{s['姓名']} ({s.get('班別')}) -> {s_cls}")

targets = []
if courses_show:
    st.write(f"📅 課程：{'、'.join(courses_show)}")
    for s in get_students_data_cached():
        s_cls = normalize_string(s.get('班別', ''))
        for c in courses_filter:
            if (c in s_cls) or (s_cls in c):
                targets.append(s['姓名']); break
else: st.write("無課程")

targets = list(set(targets))
curr = db_rec if db_rec else {"absent": targets, "present": [], "leave": []}

def upd(n, f, t):
    curr[f].remove(n); curr[t].append(n)
    save_roll_call_to_db(d_key, {"absent": curr['absent'], "present": curr['present'], "leave": curr['leave'], "updated_at": datetime.datetime.now().isoformat(), "updated_by": st.session_state['user']})
    st.rerun()

if not curr['absent'] and not curr['present'] and not curr['leave']: st.info("無須點名")
else:
    if st.button("🔄 刷新"): st.rerun()
    with st.expander("點名表", expanded=True):
        st.markdown("### 🔴 未到")
        if curr['absent']:
            cols = st.columns(4)
            for i, s in enumerate(curr['absent']): cols[i%4].button(s, key=f"ab_{s}", on_click=upd, args=(s, "absent", "present"))
        
        st.markdown("### 🟢 已到") # 4欄網格
        if curr['present']:
            cols = st.columns(4)
            for i, s in enumerate(curr['present']): cols[i%4].button(f"✅ {s}", key=f"pr_{s}", type="primary", on_click=upd, args=(s, "present", "absent"))
            
        st.markdown("### 🟡 請假")
        l_who = st.selectbox("選擇請假", ["選擇..."]+curr['absent'], key='lv_sel')
        if l_who != "選擇...": upd(l_who, "absent", "leave")
        if curr['leave']: # 4欄網格
            cols = st.columns(4)
            for i, s in enumerate(curr['leave']): cols[i%4].button(f"🤒 {s}", key=f"le_{s}", on_click=upd, args=(s, "leave", "absent"))
