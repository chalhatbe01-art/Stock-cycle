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

@st.cache_data(ttl=3600)
def fetch_fundamental_data(symbol):
    """Fetches data and handles errors if the symbol is wrong."""
    try:
        # Fallback logic: NSE first, then BSE
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(period="10d")
        
        if hist.empty:
            ticker = yf.Ticker(f"{symbol}.BO")
            hist = ticker.history(period="10d")
            
        if hist.empty:
            return None, None
            
        info = ticker.info
        avg_vol_1w = hist['Volume'].tail(5).mean() if len(hist) >= 5 else hist['Volume'].mean()
        
        fundamentals = {
            "CMP": hist['Close'].iloc[-1],
            "Market Cap (Cr)": info.get('marketCap', 0) / 10000000 if info.get('marketCap') else "N/A",
            "Volume": hist['Volume'].iloc[-1],
            "1-Week Avg Volume": avg_vol_1w,
            "P/E Ratio": info.get('trailingPE', "N/A"),
            "EV/EBITDA": info.get('enterpriseToEbitda', "N/A")
        }
        return fundamentals, ticker
    except Exception:
        # If Yahoo Finance completely fails for this symbol, skip it safely
        return None, None

def get_active_anniversary(ref_date_str):
    """Takes ANY date (even from 2010) and forces it to the most recent anniversary."""
    date_str = str(ref_date_str).strip()
    
    try:
        # Try parsing full date like 10-Jan-2010
        orig_date = datetime.strptime(date_str, "%d-%b-%Y")
    except ValueError:
        try:
            # Fallback to DD-MMM
            orig_date = datetime.strptime(date_str, "%d-%b")
        except ValueError:
            # If the user typed the date totally wrong, default to Jan 1st to prevent crashing
            orig_date = datetime(2020, 1, 1)
        
    now = datetime.now()
    
    # Force the year to the CURRENT year to find the latest anniversary
    try:
        target_date = orig_date.replace(year=now.year)
    except ValueError:
        # Handles leap years (Feb 29) safely
        target_date = orig_date.replace(year=now.year, month=2, day=28)
        
    # If the anniversary hasn't happened yet this year, roll it back to last year
    if target_date.date() > now.date():
        target_date = target_date.replace(year=now.year - 1)
        
    return target_date

def get_actual_trading_date_and_data(ticker, target_date):
    """Finds the next nearest trading day if the anniversary is a holiday."""
    end_date = target_date + timedelta(days=10)
    hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
    if not hist.empty:
        actual_date = hist.index[0].tz_localize(None) 
        return hist.iloc[0], actual_date
    return None, None

def get_offset_data(ticker, base_date, days_offset):
    """Calculates the price at +30, +60, +90 days."""
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
    
    # Ensure database exists
    if "cycles_df" not in st.session_state:
        if os.path.exists(FILE_PATH):
            st.session_state.cycles_df = pd.read_excel(FILE_PATH)
        else:
            st.session_state.cycles_df = pd.DataFrame(columns=["Symbol", "Cycle Name", "Reference Date", "Cycle Type", "Alert Threshold %"])
    
    # 1. EXCEL UPLOADER
    st.markdown("### 📥 Upload from Excel")
    uploaded_file = st.file_uploader("Upload your Excel file to merge with existing data", type=["xlsx"])
    
    if uploaded_file:
        try:
            new_df = pd.read_excel(uploaded_file)
            # Combine old and new data, keeping the newest if there are duplicates
            combined_df = pd.concat([st.session_state.cycles_df, new_df]).drop_duplicates(subset=["Symbol", "Cycle Name"], keep='last').reset_index(drop=True)
            st.session_state.cycles_df = combined_df
            combined_df.to_excel(FILE_PATH, index=False)
            st.success("Excel uploaded and database updated successfully!")
        except Exception:
            st.error("Error reading Excel file. Make sure columns match.")

    st.markdown("---")
    
    # 2. MANUAL DATA EDITOR
    st.markdown("### ✏️ Manual Editor")
    st.markdown("Double-click any cell below to edit directly. Scroll to the bottom to add new rows.")
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
            # Clean the symbol name automatically (removes spaces, makes uppercase)
            raw_sym = str(row['Symbol'])
            sym = raw_sym.strip().replace(" ", "").upper() 
            
            c_name = row['Cycle Name']
            
            status_text.text(f"Fetching data for {sym} ({c_name})...")
            progress_bar.progress((index + 1) / len(df))
            
            fundamentals, ticker = fetch_fundamental_data(sym)
            
            # If the symbol is wrong/missing, skip it so the app doesn't freeze
            if fundamentals is None: 
                continue 
                
            cmp = fundamentals["CMP"]
            
            # 1. Figure out the LATEST anniversary year automatically
            active_target_date = get_active_anniversary(row['Reference Date'])
            
            # 2. Adjust to the nearest actual trading day
            ref_data, actual_trading_date = get_actual_trading_date_and_data(ticker, active_target_date)
            
            if ref_data is not None:
                ref_high = ref_data['High']
                ref_low = ref_data['Low']
                pct_change = ((cmp - ref_high) / ref_high) * 100
                
                # 3. Calculate +30, +60, +90, +120 days
                plus_30 = get_offset_data(ticker, actual_trading_date, 30)
                plus_60 = get_offset_data(ticker, actual_trading_date, 60)
                plus_90 = get_offset_data(ticker, actual_trading_date, 90)
                plus_120 = get_offset_data(ticker, actual_trading_date, 120)
                
                results.append({
                    "Symbol": sym,
                    "Cycle Name": c_name,
                    "Latest Ref Date": actual_trading_date.strftime("%d-%b-%Y"),
                    "Ref High": round(ref_high, 2),
                    "Ref Low": round(ref_low, 2),
                    "CMP": round(cmp, 2),
                    "% Change": round(pct_change, 2),
                    "Bucket": get_bucket(pct_change),
                    "+30 Days": plus_30,
                    "+60 Days": plus_60,
                    "+90 Days": plus_90,
                    "+120 Days": plus_120,
                    "Market Cap (Cr)": round(fundamentals["Market Cap (Cr)"], 2) if isinstance(fundamentals["Market Cap (Cr)"], float) else fundamentals["Market Cap (Cr)"],
                    "1W Avg Vol": int(fundamentals["1-Week Avg Volume"]),
                    "P/E": round(fundamentals["P/E Ratio"], 2) if isinstance(fundamentals["P/E Ratio"], float) else fundamentals["P/E Ratio"],
                })

        status_text.empty()
        progress_bar.empty()
        
        results_df = pd.DataFrame(results)
        
        if results_df.empty:
            st.error("Could not fetch data. Please ensure your symbols are correct (e.g., JSWENERGY).")
        else:
            # Interactive UI Filters
            col1, col2 = st.columns(2)
            with col1:
                search_term = st.text_input("🔍 Search Symbol:", "")
            with col2:
                cycle_filter = st.multiselect("🏷️ Filter by Cycle Name:", options=results_df["Cycle Name"].unique())
                
            filtered_df = results_df.copy()
            if search_term:
                # User can search "JSW Energy" and it will match "JSWENERGY"
                clean_search = search_term.replace(" ", "").upper()
                filtered_df = filtered_df[filtered_df["Symbol"].str.contains(clean_search)]
            if cycle_filter:
                filtered_df = filtered_df[filtered_df["Cycle Name"].isin(cycle_filter)]
                
            st.dataframe(
                filtered_df.style.format({
                    "Ref High": "{:.2f}",
                    "Ref Low": "{:.2f}",
                    "CMP": "{:.2f}",
                    "% Change": "{:.2f}%"
                }),
                use_container_width=True,
                height=500
            )
            
            # Excel Download
            st.markdown("---")
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                results_df.to_excel(writer, index=False, sheet_name='Cycles_Analysis')
            st.download_button("📥 Download Analysis as Excel", data=output.getvalue(), file_name="cycle_analysis.xlsx")
