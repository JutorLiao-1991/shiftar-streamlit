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

# CSS 優化 (包含手機版優化與標籤雲樣式)
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
    /* 調整 Expander 的間距，讓點名畫面更緊湊 */
    .streamlit-expanderContent {
        padding-top: 0rem !important;
        padding-bottom: 0.5rem !important;
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

# ★ 點名資料庫
def get_roll_call_from_db(date_str):
    doc = db.collection("roll_call_records").document(date_str).get()
    if doc.exists: return doc.to_dict()
    return None

def get_all_roll_calls():
    """取得所有歷史點名紀錄"""
    docs = db.collection("roll_call_records").stream()
    records = {}
    for doc in docs:
        records[doc.id] = doc.to_dict()
    return records

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
@st.dialog("✏️ 編輯/刪除 行程")
def show_edit_event_dialog(event_id, props):
    # 1. 國定假日防呆
    if props.get('type') == 'holiday':
        st.warning("🌴 這是國定假日，無法編輯。")
        if st.button("關閉"): st.rerun()
        return

    st.write(f"正在編輯：**{props.get('title', '')}**")
    
    # 2. 解析目前的時間 (從 FullCalendar props 取得)
    # props['start'] 可能是 '2025-12-31T18:30:00+08:00' 或 '2025-12-31'
    try:
        start_str = props.get('start')
        end_str = props.get('end')
        
        # 處理 Start
        if "T" in start_str:
            s_dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            # 轉換為本地時間顯示
            if s_dt.tzinfo: s_dt = s_dt.astimezone(pytz.timezone('Asia/Taipei'))
            default_date = s_dt.date()
            default_s_time = s_dt.strftime("%H:%M")
        else:
            # All Day 事件
            s_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d")
            default_date = s_dt.date()
            default_s_time = "09:00"

        # 處理 End (若無 end，預設為 start + 1小時)
        if end_str and "T" in end_str:
            e_dt = datetime.datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if e_dt.tzinfo: e_dt = e_dt.astimezone(pytz.timezone('Asia/Taipei'))
            default_e_time = e_dt.strftime("%H:%M")
        else:
            default_e_time = "10:00"
            
    except Exception as e:
        # 發生解析錯誤時的預設值
        default_date = datetime.date.today()
        default_s_time = "18:00"
        default_e_time = "21:00"

    # --- 3. 根據類型顯示不同編輯介面 ---
    
    if props.get('type') == 'shift':
        # A. 課程編輯 (新增時間調整功能)
        new_title = st.text_input("課程名稱", props.get('title'))
        
        st.caption("📅 時間異動")
        c_d, c_t1, c_t2 = st.columns([2, 1.5, 1.5])
        new_date = c_d.date_input("日期", default_date)
        
        # 確保時間選項包含目前的時間，避免報錯
        time_options = sorted(list(set(TIME_OPTIONS + [default_s_time, default_e_time, "13:30", "16:30"])))
        
        # 嘗試找出目前時間在選單中的 index
        try: idx_s = time_options.index(default_s_time)
        except: idx_s = 0
        try: idx_e = time_options.index(default_e_time)
        except: idx_e = min(idx_s + 2, len(time_options)-1)

        new_start_time = c_t1.selectbox("開始", time_options, index=idx_s)
        new_end_time = c_t2.selectbox("結束", time_options, index=idx_e)

        st.divider()
        col1, col2 = st.columns(2)
        
        if col1.button("💾 儲存修改", type="primary"):
            # 組合新的 ISO 時間字串
            s_dt_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(new_start_time, "%H:%M").time())
            e_dt_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(new_end_time, "%H:%M").time())
            
            update_event_in_db(event_id, {
                "title": new_title,
                "start": s_dt_new.isoformat(),
                "end": e_dt_new.isoformat()
            })
            st.rerun()
            
        if col2.button("🗑️ 刪除此課程", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()

    elif props.get('type') == 'part_time':
        # B. 工讀生編輯 (也可以改時間)
        new_staff = st.text_input("工讀生姓名", props.get('staff'))
        
        st.caption("📅 時間異動")
        c_d, c_t1, c_t2 = st.columns([2, 1.5, 1.5])
        new_date = c_d.date_input("日期", default_date)
        
        time_options = sorted(list(set(TIME_OPTIONS + [default_s_time, default_e_time])))
        try: idx_s = time_options.index(default_s_time)
        except: idx_s = 0
        try: idx_e = time_options.index(default_e_time)
        except: idx_e = 0
        
        new_start_time = c_t1.selectbox("上班", time_options, index=idx_s)
        new_end_time = c_t2.selectbox("下班", time_options, index=idx_e)

        col1, col2 = st.columns(2)
        if col1.button("💾 儲存修改", type="primary"):
            s_dt_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(new_start_time, "%H:%M").time())
            e_dt_new = datetime.datetime.combine(new_date, datetime.datetime.strptime(new_end_time, "%H:%M").time())
            
            update_event_in_db(event_id, {
                "staff": new_staff,
                "start": s_dt_new.isoformat(),
                "end": e_dt_new.isoformat()
            })
            st.rerun()
        if col2.button("🗑️ 刪除此班表", type="secondary"):
            delete_event_from_db(event_id)
            st.rerun()
            
    elif props.get('type') == 'notice':
        # C. 公告編輯
        cat_opts = ["調課", "考試", "活動", "任務", "其他"]
        curr_cat = props.get('category', '其他')
        idx = cat_opts.index(curr_cat) if curr_cat in cat_opts else 4
        new_cat = st.selectbox("分類", cat_opts, index=idx)
        new_content = st.text_area("內容", props.get('title')) 
        
        # 公告通常不需要改時間，但如果有需要也可以加
        
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

@st.dialog("📅 切換日期與檢視紀錄")
def show_roll_call_review_dialog():
    st.caption("點擊任一列可切換至該日期進行編輯")
    
    all_records = get_all_roll_calls()
    if not all_records:
        st.info("目前尚無任何點名紀錄")
        return

    table_data = []
    
    # 準備地點對照 (從當日課程判斷)
    date_loc_map = {}
    all_events_local = get_all_events_cached()
    for e in all_events_local:
        start_date = e.get('start', '').split('T')[0]
        props = e.get('extendedProps', {})
        if props.get('type') == 'shift':
            loc = props.get('location', '')
            # ★ 顯示優化：線上 -> 櫃台
            if loc == '線上': loc = '櫃台'
            
            if start_date not in date_loc_map:
                date_loc_map[start_date] = []
            if loc and loc not in date_loc_map[start_date]:
                date_loc_map[start_date].append(loc)

    # 排序：日期新到舊
    sorted_dates = sorted(all_records.keys(), reverse=True)
    
    for d_str in sorted_dates:
        rec = all_records[d_str]
        
        n_present = len(rec.get('present', []))
        n_leave = len(rec.get('leave', []))
        n_absent = len(rec.get('absent', []))
        
        locs = date_loc_map.get(d_str, [])
        loc_display = "、".join(locs) if locs else ""
        
        status_summary = f"到:{n_present} / 假:{n_leave} / 未:{n_absent}"
        
        table_data.append({
            "日期": d_str,
            "上課地點": loc_display,
            "狀態": status_summary,
            "raw_date": d_str
        })
    
    if table_data:
        df = pd.DataFrame(table_data)
        event = st.dataframe(
            df,
            column_config={
                "日期": st.column_config.TextColumn("日期", width="small"),
                "上課地點": st.column_config.TextColumn("上課地點", width="medium"),
                "狀態": st.column_config.TextColumn("點名狀況", width="medium"),
                "raw_date": None
            },
            selection_mode="single-row",
            on_select="rerun",
            hide_index=True,
            use_container_width=True
        )
        
        if len(event.selection['rows']) > 0:
            idx = event.selection['rows'][0]
            selected_d_str = df.iloc[idx]["raw_date"]
            st.session_state['selected_calendar_date'] = datetime.date.fromisoformat(selected_d_str)
            st.rerun()

@st.dialog("📂 資料管理")
def show_general_management_dialog():
    tab1, tab2 = st.tabs(["🎓 學生名單", "👷 工讀生名單"])
    
    current_students = get_students_data_cached()
    student_map = {}
    for s in current_students:
        label = f"{s.get('姓名')} ({s.get('年級', '')})"
        student_map[label] = s
    
    with tab1:
        st.caption("🎓 學生名單管理 (搜尋增強版)")
        
        # --- 1. 智慧匯入區塊 ---
        with st.expander("📂 批次匯入 (Excel/CSV 轉換沙盒)", expanded=False):
            st.info("💡 請選擇那個「包含所有電話」的欄位，系統會根據 (個人手機/tel/爸爸/媽媽) 自動歸類。")
            uploaded_file = st.file_uploader("上傳原始 Excel/CSV 檔", type=['csv', 'xlsx'])
            
            if uploaded_file:
                try:
                    # 1. 讀取檔案
                    if uploaded_file.name.endswith('.csv'):
                        try:
                            df_raw = pd.read_csv(uploaded_file, encoding='utf-8')
                        except:
                            uploaded_file.seek(0)
                            df_raw = pd.read_csv(uploaded_file, encoding='cp950')
                    else:
                        import openpyxl
                        df_raw = pd.read_excel(uploaded_file, engine='openpyxl')

                    # 清除欄位空白
                    df_raw.columns = [str(c).strip() for c in df_raw.columns]
                    all_columns = list(df_raw.columns)
                    
                    st.divider()
                    st.markdown("### 🔧 欄位對應設定")
                    
                    def get_idx(keywords):
                        for i, opt in enumerate(all_columns):
                            if any(k in opt for k in keywords): return i
                        return 0

                    c1, c2 = st.columns(2)
                    col_name = c1.selectbox("1. 姓名欄位", all_columns, index=get_idx(['姓名', 'Name']))
                    col_grade = c2.selectbox("2. 年級欄位", all_columns, index=get_idx(['年級', 'Grade']))
                    
                    c3, c4 = st.columns(2)
                    col_course = c3.selectbox("3. 課程欄位", all_columns, index=get_idx(['課程', '班別', 'Class', '報名']))
                    col_mixed_contact = c4.selectbox("4. 綜合聯絡資訊欄位", all_columns, index=get_idx(['電話', '聯絡', 'Contact', 'Tel']))

                    st.divider()

                    # --- 2. 轉換邏輯 ---
                    processed_rows = []

                    def clean_only_digits(text):
                        if not text: return ""
                        import re
                        clean = re.sub(r'[^\d\-]', '', text)
                        return clean

                    for index, row in df_raw.iterrows():
                        def get_val(col):
                            val = row.get(col)
                            if pd.isna(val) or str(val).lower() == 'nan': return ""
                            return str(val).strip()

                        base_name = get_val(col_name)
                        if not base_name: continue
                        base_grade = get_val(col_grade)
                        
                        raw_contact = get_val(col_mixed_contact)
                        contact_info = {"學生手機": "", "家裡": "", "爸爸": "", "媽媽": "", "其他家人": ""}
                        
                        if raw_contact:
                            txt = raw_contact.replace("_x000D_", "\n").replace("\r", "\n")
                            segments = txt.split('\n')
                            for seg in segments:
                                seg = seg.strip()
                                if not seg: continue
                                if "個人手機" in seg or "學生" in seg or "手機" in seg:
                                    contact_info["學生手機"] = clean_only_digits(seg)
                                elif "tel" in seg.lower() or "市話" in seg or "家裡" in seg:
                                    contact_info["家裡"] = clean_only_digits(seg)
                                elif "爸爸" in seg or "父" in seg:
                                    contact_info["爸爸"] = clean_only_digits(seg)
                                elif "媽媽" in seg or "母" in seg:
                                    contact_info["媽媽"] = clean_only_digits(seg)
                                else:
                                    clean_num = clean_only_digits(seg)
                                    if clean_num:
                                        if not contact_info["其他家人"]: contact_info["其他家人"] = clean_num
                                        else: contact_info["其他家人"] += f", {clean_num}"

                        raw_courses = get_val(col_course)
                        courses_list = []
                        if raw_courses:
                            txt = raw_courses.replace("_x000D_", "\n").replace("\r", "\n")
                            split_c = txt.split('\n')
                            courses_list = [c.strip() for c in split_c if c.strip()]

                        if not courses_list:
                            new_row = {"姓名": base_name, "年級": base_grade, "班別": "未分班"}
                            new_row.update(contact_info)
                            processed_rows.append(new_row)
                        else:
                            for c in courses_list:
                                new_row = {"姓名": base_name, "年級": base_grade, "班別": c}
                                new_row.update(contact_info)
                                processed_rows.append(new_row)
                    
                    # --- 3. 預覽與存檔 ---
                    df_preview = pd.DataFrame(processed_rows)
                    st.markdown(f"### 🕵️ 預覽結果 ({len(df_preview)} 筆)")
                    st.dataframe(df_preview, use_container_width=True)
                    
                    if st.button("✅ 確認寫入資料庫", type="primary"):
                        if processed_rows:
                            final_data = df_preview.to_dict('records')
                            current_data = get_students_data_cached()
                            save_students_data(current_data + final_data)
                            st.success(f"成功匯入 {len(final_data)} 筆資料！")
                        else:
                            st.error("沒有資料被產出")
                        
                except Exception as e:
                    st.error(f"錯誤: {e}")

        st.divider()
        
        # --- 年度升級區塊 (內嵌版，解決 Dialog 重複開啟問題) ---
        if st.session_state['is_admin']:
            with st.expander("⚠️ 年度升級專區 (每年 7 月使用)", expanded=False):
                st.warning("⚠️ 警告：此操作會將系統內「所有學生」的年級自動 +1。")
                st.markdown("""
                * 例如：小一 ➝ 小二
                * 例如：高三 ➝ 畢業
                * **請務必確認已備份資料後再執行。**
                """)
                
                # 雙重確認機制
                confirm_check = st.checkbox("我確認現在是 7 月，且已備份資料，要執行升級")
                
                if confirm_check and st.button("🚀 確認執行年度升級", type="primary"):
                    current_data = get_students_data_cached()
                    upgraded_count = 0
                    new_data_list = []
                    
                    for s in current_data:
                        old_grade = s.get('年級', '')
                        new_grade = old_grade
                        if old_grade in GRADE_OPTIONS:
                            idx = GRADE_OPTIONS.index(old_grade)
                            if idx < len(GRADE_OPTIONS) - 1:
                                new_grade = GRADE_OPTIONS[idx + 1]
                                upgraded_count += 1
                            else:
                                new_grade = "畢業" 
                        s['年級'] = new_grade
                        new_data_list.append(s)
                    
                    save_students_data(new_data_list)
                    st.success(f"年度升級成功！共 {upgraded_count} 位學生年級已更新。")
                    time.sleep(1.5)
                    st.rerun()

        # --- 2. 手動新增 ---
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

        # --- 3. 列表與刪除 (搜尋功能版) ---
        st.divider()
        st.subheader("🔎 學生資料總表")
        
        if current_students:
            display_cols = ["姓名", "年級", "班別", "學生手機", "家裡", "爸爸", "媽媽", "其他家人"]
            processed_list = []
            for s in current_students:
                row = {col: s.get(col, "") for col in display_cols}
                processed_list.append(row)
            
            df_stu = pd.DataFrame(processed_list)
            
            col_search, col_filter = st.columns([2, 1])
            search_term = col_search.text_input("🔍 搜尋姓名或電話", placeholder="輸入關鍵字...")
            
            all_classes = ["全部班級"] + sorted(list(set([s.get("班別", "") for s in current_students if s.get("班別")])))
            filter_class = col_filter.selectbox("班級篩選", all_classes)
            
            if filter_class != "全部班級":
                df_stu = df_stu[df_stu["班別"] == filter_class]
                
            if search_term:
                mask = df_stu.apply(lambda x: x.astype(str).str.contains(search_term, case=False).any(), axis=1)
                df_stu = df_stu[mask]
            
            st.caption(f"共找到 {len(df_stu)} 筆資料")
            
            st.dataframe(
                df_stu, 
                use_container_width=True,
                hide_index=True,
                column_config={
                    "學生手機": st.column_config.TextColumn("學生手機", width="medium"),
                    "爸爸": st.column_config.TextColumn("爸爸", width="medium"),
                    "媽媽": st.column_config.TextColumn("媽媽", width="medium"),
                }
            )
            
            st.divider()
            with st.expander("🗑️ 刪除學生資料", expanded=False):
                st.warning("注意：刪除後無法復原")
                delete_options = [f"{row['姓名']} ({row['班別']})" for index, row in df_stu.iterrows()]
                to_del = st.multiselect("選擇要刪除的學生", delete_options)
                
                if to_del and st.button("確認刪除選取項目", type="primary"):
                    new_list = [s for s in current_students if f"{s.get('姓名')} ({s.get('班別')})" not in to_del]
                    save_students_data(new_list)
                    st.success(f"已刪除 {len(to_del)} 筆資料")
                    st.rerun()
        else:
            st.info("目前還沒有學生資料，請先匯入或手動新增。")

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

    with tab2:
        st.subheader("👷 工讀生排班系統 (含記憶修改)")
        st.caption("系統會自動帶出已排班表。勾選代表上班，取消勾選代表刪除班表。")
        
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
        
        existing_shifts_query = db.collection("shifts")\
            .where("type", "==", "part_time")\
            .where("staff", "==", pt_name)\
            .where("start", ">=", start_of_month.isoformat())\
            .where("start", "<", end_of_month.isoformat())\
            .stream()
            
        existing_shifts_map = {}
        for doc in existing_shifts_query:
            data = doc.to_dict()
            try:
                shift_date_str = data['start'][:10]
                d_obj = datetime.datetime.strptime(shift_date_str, "%Y-%m-%d").date()
                existing_shifts_map[d_obj] = doc.id
            except: pass

        st.write(f"正在編輯 **{pt_name}** 在 **{pt_year}年{pt_month}月** 的班表：")
        
        cols = st.columns(7)
        weekdays = ["日", "一", "二", "三", "四", "五", "六"] 
        for idx, w in enumerate(weekdays):
            cols[idx].markdown(f"**{w}**")
            
        num_days = py_calendar.monthrange(pt_year, pt_month)[1]
        all_dates = [datetime.date(pt_year, pt_month, d) for d in range(1, num_days + 1)]
        
        weeks = []
        current_week = []
        first_day_weekday = all_dates[0].weekday() 
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
            
        final_selected_dates = []
        
        for w_idx, week_dates in enumerate(weeks):
            col_names = [f"c{i}" for i in range(7)]
            row_data = {}
            col_config = {}
            date_map = {}
            
            for i, d in enumerate(week_dates):
                col_key = col_names[i]
                if d:
                    is_checked = d in existing_shifts_map
                    col_config[col_key] = st.column_config.CheckboxColumn(
                        label=str(d.day), 
                        default=False
                    )
                    row_data[col_key] = is_checked
                    date_map[col_key] = d
                else:
                    col_config[col_key] = st.column_config.Column(label=" ", disabled=True)
                    row_data[col_key] = False 
            
            df_week = pd.DataFrame([row_data]) 
            
            edited_week = st.data_editor(
                df_week,
                column_config=col_config,
                hide_index=True,
                use_container_width=True,
                key=f"week_grid_{pt_year}_{pt_month}_{w_idx}" 
            )
            
            for col in edited_week.columns:
                if col in date_map and edited_week[col][0]:
                    final_selected_dates.append(date_map[col])
        
        st.divider()
        
        if st.button(f"💾 儲存變更", type="primary", key="save_pt_table"):
            current_selected_set = set(final_selected_dates)
            original_set = set(existing_shifts_map.keys())
            
            to_add = current_selected_set - original_set
            to_remove_dates = original_set - current_selected_set
            to_remove_ids = [existing_shifts_map[d] for d in to_remove_dates]
            
            t_s = datetime.datetime.strptime(pt_start, "%H:%M").time()
            t_e = datetime.datetime.strptime(pt_end, "%H:%M").time()
            
            if to_remove_ids:
                batch_delete_events(to_remove_ids)
                
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
                time.sleep(1)
                st.rerun()

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
    st.stop() 

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

# --- 6. 智慧點名系統 (課程優先分組版) ---
st.divider()
st.subheader("📋 每日點名")

# 切換日期按鈕
col_date_btn, col_date_info = st.columns([1, 3], vertical_alignment="center")
if col_date_btn.button("📅 切換日期", type="secondary"):
    show_roll_call_review_dialog()

# 決定日期
if 'selected_calendar_date' in st.session_state:
    selected_date = st.session_state['selected_calendar_date']
else:
    selected_date = datetime.date.today()

with col_date_info:
    st.markdown(f"**{selected_date}**")

date_key = selected_date.isoformat()
db_record = get_roll_call_from_db(date_key)

# 1. 抓取資料並建立「課程 -> 學生名單」的索引 (解決同名不同班問題)
all_students = get_students_data_cached()
course_to_students_map = defaultdict(list) # 關鍵修改：建立 班級 -> [學生A, 學生B...]
for s in all_students:
    c = s.get('班別')
    n = s.get('姓名')
    if c and n:
        course_to_students_map[c].append(n)

# 2. 準備當日課程 & 地點對照表
daily_courses_display = []
daily_courses_filter = []     # 這是今天「真正有開」的課
course_location_map = {} 

for e in all_events:
    if e.get('start', '').startswith(date_key) and e.get('extendedProps', {}).get('type') == 'shift':
        props = e.get('extendedProps', {})
        c_title = props.get('title', '')
        c_loc = props.get('location', '')
        
        if c_loc == "線上": c_loc = "櫃台"
        
        daily_courses_filter.append(c_title)
        course_location_map[c_title] = c_loc
        
        if c_loc: daily_courses_display.append(f"{c_title} ({c_loc})")
        else: daily_courses_display.append(c_title)

# 3. 抓取「現在課表上」應到的學生 (這部分邏輯原本就是對的，因為它是逐列掃描)
target_students = []
if daily_courses_display:
    st.caption(f"當日課程：{'、'.join(daily_courses_display)}")
    for stu in all_students:
        if stu.get('班別') in daily_courses_filter:
            target_students.append(stu['姓名'])
else:
    st.caption("當日無排課紀錄")

target_students = list(set(target_students))

# 決定當前點名狀態 (含自動同步邏輯)
if db_record:
    current_data = db_record
    if "absent" not in current_data: current_data["absent"] = []
    if "present" not in current_data: current_data["present"] = []
    if "leave" not in current_data: current_data["leave"] = []
    
    # 自動同步：補入漏掉的學生
    recorded_students = set(current_data["absent"] + current_data["present"] + current_data["leave"])
    missing_students = [s for s in target_students if s not in recorded_students]
    
    if missing_students:
        current_data["absent"].extend(missing_students)
else:
    current_data = {"absent": target_students, "present": [], "leave": []}

def save_current_state(absent, present, leave):
    save_data = {
        "absent": absent,
        "present": present,
        "leave": leave,
        "updated_at": datetime.datetime.now().isoformat(),
        "updated_by": st.session_state['user']
    }
    save_roll_call_to_db(date_key, save_data)
    st.toast("點名資料已儲存", icon="💾")
    time.sleep(0.5)
    st.rerun()

# --- CSS ---
st.markdown("""
<style>
    .streamlit-expanderContent {
        padding-top: 0rem !important;
        padding-bottom: 0.5rem !important;
    }
</style>
""", unsafe_allow_html=True)

if st.session_state['user']:
    if not target_students and not current_data['absent'] and not current_data['present'] and not current_data['leave']:
        st.info("今日無課程或無學生名單，無須點名")
    else:
        # === A. 尚未報到 ===
        st.markdown("### 🔴 尚未報到")
        st.caption("💡 點擊姓名即可選取，再次點擊取消。")
        
        pending_list = set(current_data['absent']) # 轉成 set 加速查找
        
        if pending_list:
            all_selected_present = []
            all_selected_leave = []
            
            # 用來記錄哪些學生已經被歸類顯示了 (避免重複或漏網之魚)
            displayed_students = set()

            # ★ 關鍵修正：依照「今日課程 (daily_courses_filter)」來產生分類
            # 這樣就絕對不會跑出今天沒開的課 (如高二物理)
            sorted_today_courses = sorted(list(set(daily_courses_filter)))
            
            for course_name in sorted_today_courses:
                # 找出「這堂課」的所有學生
                students_in_this_course = course_to_students_map.get(course_name, [])
                
                # 篩選出「這堂課」且「目前未到」的學生
                # 這樣黃冠穎雖然在數學班也有名單，但數學班今天不會被跑迴圈，所以他只會出現在英文班
                s_list = [s for s in students_in_this_course if s in pending_list]
                
                if s_list:
                    # 標記這些人已顯示
                    displayed_students.update(s_list)
                    
                    loc_str = course_location_map.get(course_name, "")
                    title_suffix = f" @ {loc_str}" if loc_str else ""
                    
                    with st.expander(f"📘 {course_name}{title_suffix} ({len(s_list)}人)", expanded=True):
                        st.markdown("**👇 點擊出席學生 (到)**")
                        selected_p = st.pills(
                            f"pills_present_{course_name}",
                            options=s_list,
                            selection_mode="multi",
                            key=f"pills_p_{course_name}_{date_key}",
                            label_visibility="collapsed"
                        )
                        
                        remaining_for_leave = [s for s in s_list if s not in selected_p]
                        
                        if remaining_for_leave:
                            st.markdown("**👇 點擊請假學生 (假)**")
                            selected_l = st.pills(
                                f"pills_leave_{course_name}",
                                options=remaining_for_leave,
                                selection_mode="multi",
                                key=f"pills_l_{course_name}_{date_key}",
                                label_visibility="collapsed"
                            )
                            all_selected_leave.extend(selected_l)
                        
                        all_selected_present.extend(selected_p)

            # 處理「漏網之魚」：在未到名單中，但卻不屬於今天任何一堂課的學生
            # (可能是手動加的，或是舊資料殘留)
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
                if conflict:
                    st.error(f"錯誤：{', '.join(conflict)} 不能同時選取")
                elif not all_selected_present and not all_selected_leave:
                    st.warning("您未選取任何學生")
                else:
                    new_absent = [p for p in current_data['absent'] if p not in all_selected_present and p not in all_selected_leave]
                    new_present = current_data['present'] + all_selected_present
                    new_leave = current_data['leave'] + all_selected_leave
                    save_current_state(new_absent, new_present, new_leave)
        else:
            st.success("🎉 全員已完成點名！")

        st.divider()

        # === B. 反悔區 ===
        with st.expander(f"已到 ({len(current_data['present'])}) / 請假 ({len(current_data['leave'])})", expanded=False):
            if current_data['present']:
                st.write("**🟢 已到 (點選以取消)**")
                undo_p = st.pills("undo_present", options=current_data['present'], selection_mode="multi", key=f"undo_p_{date_key}")
                if undo_p:
                    if st.button("↩️ 還原選取的學生 (移回未到)", key="btn_undo_p"):
                        new_present = [p for p in current_data['present'] if p not in undo_p]
                        new_absent = current_data['absent'] + undo_p
                        save_current_state(new_absent, new_present, current_data['leave'])
            
            if current_data['leave']:
                st.divider()
                st.write("**🟡 請假 (點選以取消)**")
                undo_l = st.pills("undo_leave", options=current_data['leave'], selection_mode="multi", key=f"undo_l_{date_key}")
                if undo_l:
                    if st.button("↩️ 還原選取的學生 (移回未到)", key="btn_undo_l"):
                        new_leave = [p for p in current_data['leave'] if p not in undo_l]
                        new_absent = current_data['absent'] + undo_l
                        save_current_state(current_data['absent'], current_data['present'], new_leave)

else:
    st.warning("請登入以進行點名")
