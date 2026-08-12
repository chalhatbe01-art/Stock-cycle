import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import io
import os

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Stock Cycle Tracker Plus", layout="wide")
st.title("Stock Cycle Tracker Plus")

FILE_PATH = "stock_cycles.xlsx"

# --- HELPER FUNCTIONS ---

def format_indian_num(val):
    """Formats numbers into Indian numbering style with exactly 1 decimal (e.g., 1,23,456.7)."""
    if val is None or val == "N/A" or pd.isna(val):
        return "N/A"
    try:
        val_float = float(val)
        s = f"{val_float:.1f}" # Enforce 1 decimal place
        parts = s.split('.')
        int_str = parts[0]
        dec_str = parts[1]
        
        is_negative = int_str.startswith('-')
        if is_negative:
            int_str = int_str[1:]
        
        if len(int_str) <= 3:
            formatted_int = int_str
        else:
            last_three = int_str[-3:]
            other_digits = int_str[:-3]
            res = ""
            while len(other_digits) > 2:
                res = "," + other_digits[-2:] + res
                other_digits = other_digits[:-2]
            formatted_int = other_digits + res + "," + last_three
        
        if is_negative:
            formatted_int = "-" + formatted_int
            
        return f"{formatted_int}.{dec_str}"
    except Exception:
        return str(val)

@st.cache_data(ttl=3600)
def fetch_fundamental_data(symbol):
    """Fetches CMP, Market Cap, Volumes, and 1-Year Trend Data."""
    try:
        ticker_str = f"{symbol}.NS"
        ticker = yf.Ticker(ticker_str)
        
        # auto_adjust=False forces Yahoo to return the rawest data possible without dividend distortions
        hist = ticker.history(period="10d", auto_adjust=False)
        
        if hist.empty:
            ticker_str = f"{symbol}.BO"
            ticker = yf.Ticker(ticker_str)
            hist = ticker.history(period="10d", auto_adjust=False)
            
        if hist.empty:
            return None
            
        info = ticker.info
        avg_vol_1w = hist['Volume'].tail(5).mean() if len(hist) >= 5 else hist['Volume'].mean()
        
        # Fetch 1 year of data for the tiny Google-style line chart
        hist_1y = ticker.history(period="1y", auto_adjust=False)
        chart_data = hist_1y['Close'].tolist() if not hist_1y.empty else []
        
        fundamentals = {
            "Company Name": info.get('longName', symbol),
            "CMP": hist['Close'].iloc[-1],
            "Market Cap (Cr)": info.get('marketCap', 0) / 10000000 if info.get('marketCap') else "N/A",
            "Volume": hist['Volume'].iloc[-1],
            "1-Week Avg Volume": avg_vol_1w,
            "P/E Ratio": info.get('trailingPE', "N/A"),
            "EV/EBITDA": info.get('enterpriseToEbitda', "N/A"),
            "Ticker String": ticker_str,
            "Trend (1Y)": chart_data
        }
        return fundamentals
    except Exception:
        return None

def get_active_anniversary(ref_date_str):
    try:
        orig_date = pd.to_datetime(ref_date_str).to_pydatetime()
    except Exception:
        orig_date = datetime(2020, 1, 1)
        
    now = datetime.now()
    
    try:
        target_date = orig_date.replace(year=now.year)
    except ValueError:
        target_date = orig_date.replace(year=now.year, month=2, day=28)
        
    if target_date.date() > now.date():
        target_date = target_date.replace(year=now.year - 1)
        
    return target_date

def get_actual_trading_date_and_data(ticker, target_date):
    end_date = target_date + timedelta(days=10)
    # Using timezone-aware fetching to prevent cross-day shifting in Yahoo Finance
    hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), auto_adjust=False)
    if not hist.empty:
        actual_date = hist.index[0].tz_localize(None) 
        return hist.iloc[0], actual_date
    return None, None

def get_offset_data(ticker, base_date, days_offset):
    target = base_date + timedelta(days=days_offset)
    if target.date() > datetime.now().date():
        return f"Pending ({target.strftime('%d-%b')})"
        
    data, actual_date = get_actual_trading_date_and_data(ticker, target)
    if data is not None:
        return f"{round(data['Close'], 2)} ({actual_date.strftime('%d-%b-%y')})"
    return "N/A"

def get_bucket(pct_change):
    if pct_change > 20: return "> 20%"
    elif 10 < pct_change <= 20: return "10% to 20%"
    elif 5 < pct_change <= 10: return "5% to 10%"
    elif 0 <= pct_change <= 5: return "0% to 5%"
    elif -5 <= pct_change < 0: return "-5% to 0%"
    elif -10 <= pct_change < -5: return "-10% to -5%"
    elif -20 <= pct_change < -10: return "-20% to -10%"
    else: return "< -20%"

# --- UI LAYOUT ---

tab1, tab2 = st.tabs(["📊 Dashboard & Analysis", "⚙️ Manage Cycles Database"])

# --- TAB 2: DATABASE EDITOR & EXCEL UPLOAD ---
with tab2:
    st.subheader("Add, Edit, or Upload Stocks")
    
    if "cycles_df" not in st.session_state:
        if os.path.exists(FILE_PATH):
            st.session_state.cycles_df = pd.read_excel(FILE_PATH)
        else:
            st.session_state.cycles_df = pd.DataFrame(columns=["Symbol", "Cycle Name", "Reference Date", "Cycle Type", "Alert Threshold %"])
    
    st.markdown("### 📥 Upload from Excel")
    uploaded_file = st.file_uploader("Upload your Excel file to merge with existing data", type=["xlsx"])
    
    if uploaded_file:
        try:
            new_df = pd.read_excel(uploaded_file)
            combined_df = pd.concat([st.session_state.cycles_df, new_df]).drop_duplicates(subset=["Symbol", "Cycle Name"], keep='last').reset_index(drop=True)
            st.session_state.cycles_df = combined_df
            combined_df.to_excel(FILE_PATH, index=False)
            st.success("Excel uploaded and database updated successfully!")
        except Exception:
            st.error("Error reading Excel file. Make sure columns match.")

    st.markdown("---")
    
    st.markdown("### ✏️ Manual Editor")
    st.markdown("**NOTE:** Always press **Enter** on your keyboard after typing inside a cell before clicking Save!")
    edited_df = st.data_editor(st.session_state.cycles_df, num_rows="dynamic", use_container_width=True)
    
    if st.button("💾 Save Manual Changes", type="primary"):
        edited_df.to_excel(FILE_PATH, index=False)
        st.session_state.cycles_df = edited_df
        st.success("Database updated! Go to the Dashboard tab to see the live results.")

# --- TAB 1: DASHBOARD ---
with tab1:
    if len(st.session_state.cycles_df) == 0:
        st.warning("Your database is empty. Go to the 'Manage Cycles Database' tab to upload or add your first stock.")
    else:
        if st.button("🔄 Refresh Market Data"):
            st.cache_data.clear()
            
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        df = st.session_state.cycles_df
        
        for index, row in df.iterrows():
            raw_sym = str(row['Symbol'])
            sym = raw_sym.strip().replace(" ", "").upper() 
            c_name = row['Cycle Name']
            
            status_text.text(f"Fetching data for {sym} ({c_name})...")
            progress_bar.progress((index + 1) / len(df))
            
            fundamentals = fetch_fundamental_data(sym)
            if fundamentals is None: continue 
                
            ticker = yf.Ticker(fundamentals["Ticker String"])
            cmp = fundamentals["CMP"]
            
            active_target_date = get_active_anniversary(row['Reference Date'])
            ref_data, actual_trading_date = get_actual_trading_date_and_data(ticker, active_target_date)
            
            if ref_data is not None:
                ref_high = ref_data['High']
                ref_low = ref_data['Low']
                pct_change = ((cmp - ref_high) / ref_high) * 100
                
                results.append({
                    "Symbol": sym,
                    "Company Name": fundamentals["Company Name"],
                    "Trend (1Y)": fundamentals["Trend (1Y)"], # This triggers the mini chart
                    "Cycle Name": c_name,
                    "Latest Ref Date": actual_trading_date.strftime("%d-%b-%Y"),
                    "Ref High": round(ref_high, 2),
                    "Ref Low": round(ref_low, 2),
                    "CMP": round(cmp, 2),
                    "% Change": round(pct_change, 2),
                    "Bucket": get_bucket(pct_change),
                    "Market Cap (Cr)": format_indian_num(fundamentals["Market Cap (Cr)"]),
                    "1W Avg Vol": format_indian_num(fundamentals["1-Week Avg Volume"]),
                    "P/E": round(fundamentals["P/E Ratio"], 2) if isinstance(fundamentals["P/E Ratio"], float) else fundamentals["P/E Ratio"],
                })

        status_text.empty()
        progress_bar.empty()
        
        results_df = pd.DataFrame(results)
        
        if results_df.empty:
            st.error("Could not fetch data. Please ensure your symbols are correct (e.g., JSWENERGY or RELIANCE).")
        else:
            col1, col2 = st.columns(2)
            with col1:
                search_term = st.text_input("🔍 Search Symbol or Name:", "")
            with col2:
                cycle_filter = st.multiselect("🏷️ Filter by Cycle Name:", options=results_df["Cycle Name"].unique())
                
            filtered_df = results_df.copy()
            if search_term:
                clean_search = search_term.upper()
                filtered_df = filtered_df[
                    filtered_df["Symbol"].str.contains(clean_search, na=False) | 
                    filtered_df["Company Name"].str.upper().str.contains(clean_search, na=False)
                ]
            if cycle_filter:
                filtered_df = filtered_df[filtered_df["Cycle Name"].isin(cycle_filter)]
                
            # Render the Dataframe with the specific LineChart integration
            st.dataframe(
                filtered_df,
                column_config={
                    "Trend (1Y)": st.column_config.LineChartColumn(
                        "1 Year Trend", # Displays the Google-style mini chart inside the table
                        width="medium",
                        help="Historical closing prices over the last 1 year"
                    ),
                    "Ref High": st.column_config.NumberColumn(format="%.2f"),
                    "Ref Low": st.column_config.NumberColumn(format="%.2f"),
                    "CMP": st.column_config.NumberColumn(format="%.2f"),
                    "% Change": st.column_config.NumberColumn(format="%.2f%%"),
                },
                use_container_width=True,
                height=450
            )
            
            # --- GOOGLE-STYLE LARGE INTERACTIVE CHART ---
            st.markdown("---")
            st.subheader("📈 Interactive Stock Chart")
            
            chart_col1, chart_col2 = st.columns([2, 1])
            with chart_col1:
                selected_stock = st.selectbox("Select Stock to View Large Chart:", results_df["Symbol"].unique())
            with chart_col2:
                timeframe = st.radio("Timeframe:", ["1M", "6M", "1Y", "5Y", "Max"], horizontal=True, index=2)

            tf_map = {"1M": "1mo", "6M": "6mo", "1Y": "1y", "5Y": "5y", "Max": "max"}
            
            chart_ticker = yf.Ticker(f"{selected_stock}.NS")
            chart_hist = chart_ticker.history(period=tf_map[timeframe], auto_adjust=False)
            if chart_hist.empty:
                chart_ticker = yf.Ticker(f"{selected_stock}.BO")
                chart_hist = chart_ticker.history(period=tf_map[timeframe], auto_adjust=False)

            if not chart_hist.empty:
                st.line_chart(chart_hist['Close'])
            else:
                st.warning("Chart data unavailable for this stock.")

            # Excel Download (Excludes the Chart Data column so Excel doesn't break)
            st.markdown("---")
            output = io.BytesIO()
            excel_export_df = results_df.drop(columns=["Trend (1Y)"]) 
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                excel_export_df.to_excel(writer, index=False, sheet_name='Cycles_Analysis')
            st.download_button("📥 Download Analysis as Excel", data=output.getvalue(), file_name="cycle_analysis.xlsx")
