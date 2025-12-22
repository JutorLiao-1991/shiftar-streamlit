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
