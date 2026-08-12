import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import io
import os

st.set_page_config(page_title="Pro Stock Cycle Tracker", layout="wide")

FILE_PATH = "stock_cycles.xlsx"

# --- HELPER FUNCTIONS ---

@st.cache_data(ttl=3600)
def fetch_fundamental_data(symbol):
    """Fetches CMP, Market Cap, P/E, EV/EBITDA, and Volume data with NSE/BSE fallback."""
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

def get_active_anniversary(ref_date_str, c_type):
    """Determines the correct anniversary date for the current year."""
    try:
        orig_date = datetime.strptime(ref_date_str, "%d-%b-%Y")
    except ValueError:
        orig_date = datetime.strptime(ref_date_str, "%d-%b")
        orig_date = orig_date.replace(year=2020) # Dummy leap year just in case
        
    if c_type == 'Listing':
        return orig_date
        
    now = datetime.now()
    try:
        target_date = orig_date.replace(year=now.year)
    except ValueError:
        target_date = orig_date.replace(year=now.year, month=2, day=28)
        
    # If the date hasn't happened yet this year, we look at last year's anniversary
    if target_date.date() > now.date():
        try:
            target_date = target_date.replace(year=now.year - 1)
        except ValueError:
            target_date = target_date.replace(year=now.year - 1, month=2, day=28)
            
    return target_date

def get_actual_trading_date_and_data(ticker, target_date):
    """Finds the actual data and date. If target is a holiday, grabs the next nearest trading day."""
    end_date = target_date + timedelta(days=10)
    hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
    if not hist.empty:
        # Strip timezone for easier datetime math later
        actual_date = hist.index[0].tz_localize(None) 
        return hist.iloc[0], actual_date
    return None, None

def get_offset_data(ticker, base_date, days_offset):
    """Calculates the price at X days after the reference trading date."""
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

st.title("Pro Stock Cycle Tracker")

# Separate the App into a Dashboard View and an Editor View
tab1, tab2 = st.tabs(["📊 Dashboard & Analysis", "⚙️ Manage Cycles Database"])

# --- TAB 2: DATABASE EDITOR ---
with tab2:
    st.subheader("Add, Edit, or Delete Stocks")
    st.markdown("Double-click any cell to edit. Scroll to the bottom and click the **+** to add a new cycle. Select the left checkbox and press DELETE on your keyboard to remove a row.")
    
    # Load existing data or create empty template
    if "cycles_df" not in st.session_state:
        if os.path.exists(FILE_PATH):
            st.session_state.cycles_df = pd.read_excel(FILE_PATH)
        else:
            st.session_state.cycles_df = pd.DataFrame(columns=["Symbol", "Cycle Name", "Reference Date", "Cycle Type", "Alert Threshold %"])
    
    # The interactive dataframe editor
    edited_df = st.data_editor(st.session_state.cycles_df, num_rows="dynamic", use_container_width=True)
    
    if st.button("💾 Save Changes to Database", type="primary"):
        edited_df.to_excel(FILE_PATH, index=False)
        st.session_state.cycles_df = edited_df
        st.success("Database updated successfully! Go to the Dashboard tab to see the live results.")

# --- TAB 1: DASHBOARD ---
with tab1:
    if len(st.session_state.cycles_df) == 0:
        st.warning("Your database is empty. Go to the 'Manage Cycles Database' tab to add your first stock.")
    else:
        if st.button("🔄 Refresh Market Data"):
            st.cache_data.clear()
            
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        df = st.session_state.cycles_df
        
        for index, row in df.iterrows():
            status_text.text(f"Fetching data for {row['Symbol']} ({row['Cycle Name']})...")
            progress_bar.progress((index + 1) / len(df))
            
            sym = row['Symbol']
            c_name = row['Cycle Name']
            c_type = row['Cycle Type']
            
            fundamentals, ticker = fetch_fundamental_data(sym)
            if fundamentals is None: continue 
                
            cmp = fundamentals["CMP"]
            
            # 1. Figure out which year's anniversary to use
            active_target_date = get_active_anniversary(row['Reference Date'], c_type)
            
            # 2. Adjust to the nearest trading day
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
                    "Trading Ref Date": actual_trading_date.strftime("%d-%b-%Y"),
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
        
        # Interactive UI Filters
        col1, col2 = st.columns(2)
        with col1:
            search_term = st.text_input("🔍 Search Symbol:", "")
        with col2:
            cycle_filter = st.multiselect("🏷️ Filter by Cycle Name:", options=results_df["Cycle Name"].unique() if not results_df.empty else [])
            
        filtered_df = results_df.copy()
        if search_term:
            filtered_df = filtered_df[filtered_df["Symbol"].str.contains(search_term.upper())]
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

