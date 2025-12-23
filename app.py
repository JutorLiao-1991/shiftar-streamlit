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
    /* 縮小表格間距 */
    .stDataFrame {
        margin-bottom: -1rem;
    }
    /* 讓星期標題置中 */
    div[data-testid="stMarkdownContainer"] p {
        text-align: center;
        font-weight: bold;
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

# ★ 點名資料庫
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

# --- 4. 彈出視窗 UI ---

# 登入功能 (不使用 st.dialog，因為已移至首頁)
# ... (登入邏輯在主程式)

@st.dialog("✏️ 編輯/刪除 行程")
def show_edit_event_dialog(event_id, props):
    if props.get('type') == 'holiday':
        st.warning("🌴 這是國定假日，無法編輯。")
        if st.button("關閉"): st.rerun()
        return

    st.write(f"正在編輯：**{props.get('title', '')}**")
    
    if props.get('type') == 'shift':
        new_title = st.text_input("課程名稱", props.get('title'))
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"title": new_title})
            st.rerun()
        if col2.button("🗑️ 刪除此課程", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()

    elif props.get('type') == 'part_time':
        new_staff = st.text_input("工讀生姓名", props.get('staff'))
        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            update_event_in_db(event_id, {"staff": new_staff})
            st.rerun()
        if col2.button("🗑️ 刪除此班表", type="secondary"):
            delete_event_from_db(event_id)
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
            st.rerun()
        if col2.button("🗑️ 刪除此公告", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()
    else:
        if st.button("🗑️ 強制刪除", type="secondary"):
            delete_event_from_db(event_id)
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
        st.toast("公告已發布")
        st.rerun()

@st.dialog("📅 回顧點名紀錄")
def show_roll_call_review_dialog():
    st.info("請選擇要查看或補點名的日期")
    pick_date = st.date_input("選擇日期", value=datetime.date.today())
    if st.button("確認前往", type="primary", use_container_width=True):
        st.session_state['selected_calendar_date'] = pick_date
        st.rerun()

@st.dialog("🎓 確認年度升級")
def show_promotion_confirm_dialog():
    st.warning("⚠️ **警告：此操作不可逆！**")
    st.write("這將會把所有學生的年級往上加一級。")
    if st.button("我確定要升級所有學生", type="primary"):
        current_data = get_students_data_cached()
        updated_list = []
        for stu in current_data:
            new_stu = stu.copy()
            new_stu['年級'] = promote_student_grade(stu.get('年級', ''))
            updated_list.append(new_stu)
        save_students_data(updated_list)
        st.success(f"成功升級！")
        st.rerun()

@st.dialog("📂 資料管理")
def show_general_management_dialog():
    tab1, tab2 = st.tabs(["🎓 學生名單", "👷 工讀生名單"])
    
    # 準備現有學生資料，用於自動帶入
    current_students = get_students_data_cached()
    # 建立一個 { "姓名 (年級)": 學生資料dict } 的對照表
    student_map = {}
    for s in current_students:
        label = f"{s.get('姓名')} ({s.get('年級', '')})"
        student_map[label] = s
    
    with tab1:
        st.caption("🎓 學生名單管理 (含智慧匯入)")
        
        # --- 1. 智慧匯入區塊 (Sandbox) ---
        with st.expander("📂 批次匯入 (Excel/CSV 轉換沙盒)", expanded=False):
            st.info("💡 這裡專門處理「多課程擠同一格」與「多電話擠同一格」的 ERP 檔案。")
            # ★ 修改點 1：允許上傳 xlsx
            uploaded_file = st.file_uploader("上傳原始 Excel/CSV 檔", type=['csv', 'xlsx'])
            
            if uploaded_file:
                try:
                    # ★ 修改點 2：自動判斷檔案格式
                    if uploaded_file.name.endswith('.csv'):
                        try:
                            df_raw = pd.read_csv(uploaded_file, encoding='utf-8')
                        except:
                            uploaded_file.seek(0)
                            df_raw = pd.read_csv(uploaded_file, encoding='cp950')
                    else:
                        # 讀取 Excel (需要 openpyxl)
                        df_raw = pd.read_excel(uploaded_file, engine='openpyxl')
                    
                    st.write(f"原始資料讀取成功：共 {len(df_raw)} 筆。正在進行智慧轉換...")

                    # --- 轉換邏輯 ---
                    processed_rows = []
                    
                    for index, row in df_raw.iterrows():
                        # 1. 基礎欄位
                        base_name = str(row.get('姓名', '')).strip()
                        base_grade = str(row.get('年級', '')) if pd.notna(row.get('年級')) else ""
                        
                        # 2. 處理電話
                        raw_parent_phone = str(row.get('家長聯絡電話', ''))
                        raw_stu_phone = str(row.get('學生聯絡電話', ''))
                        
                        contact_info = {
                            "學生手機": raw_stu_phone if raw_stu_phone != "nan" else "",
                            "爸爸": "", "媽媽": "", "家裡": "", "其他家人": ""
                        }

                        if raw_parent_phone and raw_parent_phone != "nan":
                            # Excel 讀進來換行可能是 \n 或 _x000D_ (視版本而定)，這裡統一處理
                            raw_parent_phone = raw_parent_phone.replace("_x000D_", "")
                            segments = raw_parent_phone.split('\n')
                            
                            for seg in segments:
                                seg = seg.strip()
                                if not seg: continue
                                if "父" in seg:
                                    contact_info["爸爸"] = seg.replace("父親:", "").replace("父親", "").strip()
                                elif "母" in seg:
                                    contact_info["媽媽"] = seg.replace("母親:", "").replace("母親", "").strip()
                                elif "家" in seg:
                                    contact_info["家裡"] = seg.replace("家裡:", "").strip()
                                else:
                                    if not contact_info["爸爸"]: contact_info["爸爸"] = seg
                                    elif not contact_info["媽媽"]: contact_info["媽媽"] = seg
                                    else: contact_info["其他家人"] += f" {seg}"

                        # 3. 處理課程 (拆分多行)
                        raw_courses = str(row.get('報名課程', ''))
                        if raw_courses and raw_courses != "nan":
                            # 同樣處理 Excel 可能的換行編碼
                            raw_courses = raw_courses.replace("_x000D_", "")
                            courses_list = raw_courses.split('\n')
                        else:
                            courses_list = []

                        if not courses_list:
                            new_row = {"姓名": base_name, "年級": base_grade, "班別": "未分班"}
                            new_row.update(contact_info)
                            processed_rows.append(new_row)
                        else:
                            for c in courses_list:
                                c_clean = c.strip()
                                if not c_clean: continue
                                new_row = {"姓名": base_name, "年級": base_grade, "班別": c_clean}
                                new_row.update(contact_info)
                                processed_rows.append(new_row)
                    
                    # --- 預覽 ---
                    df_preview = pd.DataFrame(processed_rows)
                    st.divider()
                    st.markdown(f"### 🕵️ 轉換預覽 (共 {len(df_preview)} 筆)")
                    st.dataframe(df_preview)
                    
                    if st.button("✅ 確認無誤，寫入資料庫", type="primary"):
                        final_data = df_preview.to_dict('records')
                        current_data = get_students_data_cached()
                        combined_data = current_data + final_data
                        save_students_data(combined_data)
                        st.success(f"成功匯入 {len(final_data)} 筆資料！")
                        
                except Exception as e:
                    st.error(f"解析失敗: {e}")

        st.divider()
        
        if st.session_state['is_admin']:
             if st.button("⬆️ 執行年度升級 (7月)", type="primary"): show_promotion_confirm_dialog()

        # --- 手動新增 ---
        with st.expander("手動新增學生"):
            st.caption("💡 若為舊生加新班，可直接選取姓名帶入資料")
            select_existing = st.selectbox("快速帶入舊生資料 (可選)", ["不使用"] + list(student_map.keys()))
            
            def_name, def_phone, def_grade = "", "", "小一"
            def_home, def_dad, def_mom, def_other = "", "", "", ""
            
            if select_existing != "不使用":
                data = student_map[select_existing]
                def_name = data.get('姓名', '')
                def_phone = data.get('學生手機', '')
                def_grade = data.get('年級', '小一')
                def_home = data.get('家裡', '')
                def_dad = data.get('爸爸', '')
                def_mom = data.get('媽媽', '')
                def_other = data.get('其他家人', '')

            c1, c2 = st.columns(2)
            ms_name = c1.text_input("學生姓名 (必填)", value=def_name)
            ms_phone = c2.text_input("學生手機", value=def_phone)
            
            c3, c4 = st.columns(2)
            grade_index = GRADE_OPTIONS.index(def_grade) if def_grade in GRADE_OPTIONS else 0
            ms_grade = c3.selectbox("年級 (必填)", GRADE_OPTIONS, index=grade_index)
            
            course_opts = get_unique_course_names()
            ms_class = c4.selectbox("班別 (必填)", course_opts)
            
            st.divider()
            st.caption("聯絡電話 (至少填寫一項)")
            c5, c6 = st.columns(2)
            ms_home = c5.text_input("家裡", value=def_home)
            ms_dad = c6.text_input("爸爸", value=def_dad)
            c7, c8 = st.columns(2)
            ms_mom = c7.text_input("媽媽", value=def_mom)
            ms_other = c8.text_input("其他家人", value=def_other)
            
            if st.button("新增學生資料", type="primary"):
                contact_filled = any([ms_home, ms_dad, ms_mom, ms_other])
                if ms_name and ms_grade and ms_class and contact_filled:
                    new_rec = {
                        "姓名": ms_name, "學生手機": ms_phone,
                        "年級": ms_grade, "班別": ms_class,
                        "家裡": ms_home, "爸爸": ms_dad,
                        "媽媽": ms_mom, "其他家人": ms_other
                    }
                    current = get_students_data_cached()
                    current.append(new_rec)
                    save_students_data(current)
                    st.success(f"已新增：{ms_name} - {ms_class}")
                    st.rerun()
                else:
                    if not contact_filled: st.error("請至少填寫一個家長/家裡聯絡電話")
                    else: st.error("缺必填欄位")

        # --- 列表與刪除 ---
        st.divider()
        st.caption("學生列表 (可刪除)")
        if current_students:
            display_cols = ["姓名", "學生手機", "年級", "班別", "家裡", "爸爸", "媽媽", "其他家人"]
            processed_list = []
            for s in current_students:
                row = {col: s.get(col, "") for col in display_cols}
                processed_list.append(row)
                
            df_stu = pd.DataFrame(processed_list)
            st.dataframe(df_stu, use_container_width=True)
            
            delete_options = [f"{s.get('姓名')} ({s.get('班別')})" for s in current_students]
            to_del = st.multiselect("刪除學生", delete_options)
            
            if to_del and st.button("確認刪除"):
                new_list = []
                for s in current_students:
                    label = f"{s.get('姓名')} ({s.get('班別')})"
                    if label not in to_del:
                        new_list.append(s)
                save_students_data(new_list)
                st.rerun()

    with tab2:
        st.caption("工讀生名單管理")
        current_pts = get_part_timers_list_cached()
        c_p1, c_p2 = st.columns([2, 1])
        new_pt = c_p1.text_input("輸入新工讀生姓名")
        if c_p2.button("新增工讀生"):
            if new_pt and new_pt not in current_pts:
                current_pts.append(new_pt); save_part_timers_list(current_pts); st.rerun()
        pts_to_del = st.multiselect("刪除工讀生", current_pts)
        if pts_to_del and st.button("確認刪除工讀生"):
            save_part_timers_list([p for p in current_pts if p not in pts_to_del])
            st.rerun()

@st.dialog("⚙️ 管理員後台")
def show_admin_dialog():
    tab1, tab2, tab3, tab4 = st.tabs(["📅 智慧排課", "👷 工讀排班", "💰 薪資", "🗑️ 資料管理"])
    
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

    # ... (前略: tab1 內容保持不變) ...

    # 工讀生排班：分週次表格 (週曆模式) - 具備記憶與修改功能
    with tab2:
        st.subheader("👷 工讀生排班系統 (含記憶修改)")
        st.caption("系統會自動帶出已排班表。勾選代表上班，取消勾選代表刪除班表。")
        
        part_timers_list = get_part_timers_list_cached()
        c_pt1, c_pt2 = st.columns(2)
        pt_name = c_pt1.selectbox("選擇工讀生", part_timers_list)
        
        c_y, c_m = c_pt2.columns(2)
        # 預設下個月 (方便排班)，或當月
        next_month_date = datetime.date.today() + relativedelta(months=0) 
        pt_year = c_y.number_input("年份", value=next_month_date.year, key="pt_year")
        pt_month = c_m.number_input("月份", value=next_month_date.month, min_value=1, max_value=12, key="pt_month")
        
        c_t1, c_t2 = st.columns(2)
        pt_start = c_t1.selectbox("上班時間 (批次設定)", TIME_OPTIONS, index=18, key="pt_start")
        pt_end = c_t2.selectbox("下班時間 (批次設定)", TIME_OPTIONS, index=24, key="pt_end")
        
        st.divider()

        # --- [STEP 1] 讀取現有班表 (Memory) ---
        # 計算該月起訖時間，用來查詢 DB
        start_of_month = datetime.datetime(pt_year, pt_month, 1)
        end_of_month = start_of_month + relativedelta(months=1)
        
        # 查詢 Firestore：這個人、這個月的所有工讀班表
        # 注意：這裡直接查詢會比較準確，不做 cache 或需手動清除 cache
        existing_shifts_query = db.collection("shifts")\
            .where("type", "==", "part_time")\
            .where("staff", "==", pt_name)\
            .where("start", ">=", start_of_month.isoformat())\
            .where("start", "<", end_of_month.isoformat())\
            .stream()
            
        # 建立對照表： { date_obj: doc_id }
        # 用來判斷哪天已經有班，以及如果要刪除時該刪哪一筆 ID
        existing_shifts_map = {}
        for doc in existing_shifts_query:
            data = doc.to_dict()
            # 解析 ISO 格式的時間字串取日期部分
            try:
                # 假設儲存格式為 isoformat()，直接取前 10 碼 YYYY-MM-DD
                shift_date_str = data['start'][:10]
                d_obj = datetime.datetime.strptime(shift_date_str, "%Y-%m-%d").date()
                existing_shifts_map[d_obj] = doc.id
            except:
                pass

        st.write(f"正在編輯 **{pt_name}** 在 **{pt_year}年{pt_month}月** 的班表：")
        
        # --- [STEP 2] 生成月曆表格並回填狀態 ---
        cols = st.columns(7)
        weekdays = ["日", "一", "二", "三", "四", "五", "六"] 
        for idx, w in enumerate(weekdays):
            cols[idx].markdown(f"**{w}**")
            
        num_days = py_calendar.monthrange(pt_year, pt_month)[1]
        all_dates = [datetime.date(pt_year, pt_month, d) for d in range(1, num_days + 1)]
        
        weeks = []
        current_week = []
        first_day_weekday = all_dates[0].weekday() 
        # Python weekday: 0=Mon, 6=Sun. 我們介面是 日(0)..六(6)
        # 調整偏移量：如果 0(Mon) 顯示在第 1 格，則前面空 1 格。 6(Sun) 顯示在第 0 格
        # Mapping: Sun=6->0, Mon=0->1, ... Sat=5->6
        start_padding = (first_day_weekday + 1) % 7
        
        for _ in range(start_padding):
            current_week.append(None)
            
        for d in all_dates:
            current_week.append(d)
            if len(current_week) == 7:
                weeks.append(current_week)
                current_week = []
        
        if current_week:
            while len(current_week) < 7:
                current_week.append(None)
            weeks.append(current_week)
            
        # 收集使用者最後勾選的日期
        final_selected_dates = []
        
        for w_idx, week_dates in enumerate(weeks):
            col_names = [f"c{i}" for i in range(7)]
            row_data = {}
            col_config = {}
            date_map = {} # 紀錄這一列每個 column 對應的日期物件
            
            for i, d in enumerate(week_dates):
                col_key = col_names[i]
                if d:
                    # ★ 關鍵：檢查這天是否在 existing_shifts_map 裡
                    is_checked = d in existing_shifts_map
                    
                    col_config[col_key] = st.column_config.CheckboxColumn(
                        label=str(d.day), 
                        default=False # st.data_editor 讀取 dataframe 的值，所以這裡 default 沒用，要看 row_data
                    )
                    # 設定初始狀態
                    row_data[col_key] = is_checked
                    date_map[col_key] = d
                else:
                    col_config[col_key] = st.column_config.Column(label=" ", disabled=True)
                    row_data[col_key] = False 
            
            df_week = pd.DataFrame([row_data]) 
            
            # 加上 year_month 確保切換月份時 key 不同，強制重繪
            edited_week = st.data_editor(
                df_week,
                column_config=col_config,
                hide_index=True,
                use_container_width=True,
                key=f"week_grid_{pt_year}_{pt_month}_{w_idx}" 
            )
            
            # 解析編輯後的結果
            for col in edited_week.columns:
                if col in date_map and edited_week[col][0]:
                    final_selected_dates.append(date_map[col])
        
        st.divider()
        
        # --- [STEP 3] 差異更新 (Diff & Save) ---
        if st.button(f"💾 儲存變更", type="primary", key="save_pt_table"):
            current_selected_set = set(final_selected_dates)
            original_set = set(existing_shifts_map.keys())
            
            # 1. 找出要新增的 (在新清單但不在舊清單)
            to_add = current_selected_set - original_set
            
            # 2. 找出要刪除的 (在舊清單但不在新清單)
            to_remove_dates = original_set - current_selected_set
            to_remove_ids = [existing_shifts_map[d] for d in to_remove_dates]
            
            # 執行變更
            t_s = datetime.datetime.strptime(pt_start, "%H:%M").time()
            t_e = datetime.datetime.strptime(pt_end, "%H:%M").time()
            
            # 批次刪除
            if to_remove_ids:
                batch_delete_events(to_remove_ids)
                
            # 逐筆新增
            add_count = 0
            for date_obj in to_add:
                start_dt = datetime.datetime.combine(date_obj, t_s)
                end_dt = datetime.datetime.combine(date_obj, t_e)
                add_event_to_db("工讀", start_dt, end_dt, "part_time", st.session_state['user'], staff=pt_name)
                add_count += 1
                
            if not to_add and not to_remove_ids:
                st.info("資料未變更")
            else:
                msg = []
                if add_count: msg.append(f"新增 {add_count} 筆")
                if to_remove_ids: msg.append(f"刪除 {len(to_remove_ids)} 筆")
                st.success(f"更新成功！({', '.join(msg)})")
                
                # 重新整理頁面以顯示最新狀態
                time.sleep(1) # 稍微等待資料庫寫入
                st.rerun()

    # ... (後略: tab3 內容保持不變) ...
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

# 自動登出：僅在凌晨 06:00 ~ 06:30 之間
if now.hour == 6 and now.minute <= 30 and st.session_state['user'] is not None:
    st.session_state['user'] = None; st.session_state['is_admin'] = False; st.rerun()

# 如果未登入，顯示登入區塊
if st.session_state['user'] is None:
    st.title("🏫 鳩特數理行政班表")
    st.info("請先登入以使用系統")
    
    with st.form("main_login_form"):
        user = st.selectbox("請選擇您的身份", ["請選擇"] + LOGIN_LIST)
        password = st.text_input("請輸入密碼", type="password")
        if st.form_submit_button("登入", use_container_width=True):
            if user == "請選擇":
                st.error("請選擇身份")
            else:
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
    st.stop() # 停止執行

# 登入後顯示的內容
col_title, col_login = st.columns([3, 1], vertical_alignment="center")
with col_title: st.title("🏫 鳩特數理行政班表")
with col_login:
    st.markdown(f"👤 **{st.session_state['user']}**")
    if st.button("登出", type="secondary", use_container_width=True):
        st.session_state['user'] = None; st.session_state['is_admin'] = False; st.rerun()

st.divider()

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

if st.session_state['user']:
    if st.button("📂 資料管理", type="secondary", use_container_width=True): show_general_management_dialog()
    if st.session_state['is_admin']:
        if st.button("⚙️ 管理員後台", type="secondary", use_container_width=True): show_admin_dialog()

all_events = get_all_events_cached()
calendar_options = {
    "editable": True, 
    "headerToolbar": { "left": "today prev,next", "center": "title", "right": "listMonth,dayGridMonth" },
    "initialView": "dayGridMonth", 
    "height": "650px", "locale": "zh-tw",
    "titleFormat": {"year": "numeric", "month": "long"},
    "slotLabelFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
    "eventTimeFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
    "views": { "dayGridMonth": {"displayEventTime": False}, "listMonth": {"displayEventTime": True} },
    "selectable": True,
}
cal = calendar(events=all_events, options=calendar_options, callbacks=['dateClick', 'eventClick'])

# 點擊日期：只開公告
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

# --- 6. 智慧點名系統 ---
st.divider()
st.subheader("📋 每日點名")

# ★ 回顧點名按鈕移到這裡
if st.button("📅 切換/回顧點名日期", type="primary", use_container_width=True):
    show_roll_call_review_dialog()

# 決定日期
if 'selected_calendar_date' in st.session_state:
    selected_date = st.session_state['selected_calendar_date']
else:
    selected_date = datetime.date.today()

st.info(f"正在檢視：**{selected_date}** 的點名紀錄")
date_key = selected_date.isoformat()
db_record = get_roll_call_from_db(date_key)

# ★ 修正重點：拆分顯示清單與比對清單
daily_courses_display = []
daily_courses_filter = []

for e in all_events:
    if e.get('start', '').startswith(date_key) and e.get('extendedProps', {}).get('type') == 'shift':
        props = e.get('extendedProps', {})
        c_title = props.get('title', '')
        c_loc = props.get('location', '')
        
        # 存入比對用的純課程名稱
        daily_courses_filter.append(c_title)
        
        # 存入顯示用的完整名稱 (含教室)
        if c_loc:
            daily_courses_display.append(f"{c_title} ({c_loc})")
        else:
            daily_courses_display.append(c_title)

all_students = get_students_data_cached()
target_students = []

if daily_courses_display:
    # 顯示包含教室的課程清單
    st.write(f"📅 當日課程：{'、'.join(daily_courses_display)}")
    for stu in all_students:
        # 使用純課程名稱來比對學生班別
        if stu.get('班別') in daily_courses_filter:
            target_students.append(stu['姓名'])
else:
    st.write("📅 當日無排課紀錄")

# ★ 修復重複學生 Bug：使用 set 去除重複姓名
target_students = list(set(target_students))

if db_record:
    current_data = db_record
else:
    current_data = {"absent": target_students, "present": [], "leave": []}

def update_status_and_save(student_name, from_list, to_list):
    current_data[from_list].remove(student_name)
    current_data[to_list].append(student_name)
    save_data = {
        "absent": current_data['absent'], "present": current_data['present'], "leave": current_data['leave'],
        "updated_at": datetime.datetime.now().isoformat(), "updated_by": st.session_state['user']
    }
    save_roll_call_to_db(date_key, save_data)
    st.rerun()

if st.session_state['user']:
    if not current_data['absent'] and not current_data['present'] and not current_data['leave']:
        st.info("無須點名")
    else:
        if st.button("🔄 刷新數據 (同步最新狀態)", use_container_width=True): st.rerun()
        with st.expander("點名表單", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("### 🔴 未到")
                if current_data['absent']:
                    cols = st.columns(4)
                    for i, s in enumerate(current_data['absent']):
                        if cols[i%4].button(s, key=f"ab_{s}_{date_key}"):
                            update_status_and_save(s, "absent", "present")
            with c2:
                st.markdown("### 🟢 已到")
                for s in current_data['present']:
                    if st.button(f"✅ {s}", key=f"pr_{s}_{date_key}", type="primary", use_container_width=True):
                        update_status_and_save(s, "present", "absent")
            with c3:
                st.markdown("### 🟡 請假")
                val = st.selectbox("請假", ["選擇..."] + current_data['absent'], key=f"lv_{date_key}")
                if val != "選擇...": update_status_and_save(val, "absent", "leave")
                for s in current_data['leave']:
                    if st.button(f"🤒 {s}", key=f"le_{s}_{date_key}", use_container_width=True):
                        update_status_and_save(s, "leave", "absent")
else:
    st.warning("請登入以進行點名")
