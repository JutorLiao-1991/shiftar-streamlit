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
                    
                    if st.button("✅ 匯入", key="btn_import_stu"):
                        new_data = []
                        for _, row in df.iterrows():
                            # 簡化處理
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

        if current_students:
            st.divider(); st.subheader("🔎 列表")
            df_s = pd.DataFrame(current_students)
            f_class = st.selectbox("班別篩選", ["全部"] + sorted(list(set([x.get('班別') for x in current_students if x.get('班別')]))))
            if f_class != "全部": df_s = df_s[df_s['班別'] == f_class]
            st.dataframe(df_s, use_container_width=True)
            
            with st.expander("🗑️ 刪除"):
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

    # --- Tab 3: 試聽與潛在名單 (功能升級版) ---
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
        
        # 顯示目前的試聽生 (新增操作按鈕)
        trials = get_trial_students()
        if trials:
            st.divider()
            st.caption("尚未決定去留的試聽生 (可手動操作)：")
            
            # 使用 Container 讓排版更整齊
            for t in trials:
                with st.container(border=True):
                    c_info, c_action = st.columns([3, 2])
                    
                    with c_info:
                        st.markdown(f"**🎓 {t['name']}** ({t['grade']})")
                        st.caption(f"課程：{t['course']} | 日期：{t['trial_date']}")
                    
                    with c_action:
                        # 放置三個操作按鈕
                        b1, b2, b3 = st.columns(3)
                        if b1.button("✅", key=f"man_join_{t['id']}", help="確定入班 (加入學生名單)"):
                            move_trial_to_official(t, t['id'])
                        
                        if b2.button("📂", key=f"man_arch_{t['id']}", help="歸檔 (移至潛在名單)"):
                            move_trial_to_potential(t, t['id'])
                            
                        if b3.button("🗑️", key=f"man_del_{t['id']}", help="刪除紀錄"):
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
