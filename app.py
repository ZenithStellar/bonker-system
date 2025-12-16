import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import time
import warnings
import requests

# --- 1. CONFIGURATION ---
warnings.filterwarnings("ignore")
st.set_page_config(page_title="Bonker V5.3", layout="wide", page_icon="🏆")

# --- 🔐 KEYPASS SYSTEM ---
def check_password():
    if "PASSWORD" not in st.secrets: return True
    correct_password = st.secrets["PASSWORD"] 
    if "password_correct" not in st.session_state: st.session_state.password_correct = False
    if st.session_state.password_correct: return True
    
    st.markdown("<h1 style='text-align: center; color: #FFD700;'>🔒 SYSTEM LOCKED</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password_input = st.text_input("Access Key", type="password")
        if st.button("UNLOCK SYSTEM", use_container_width=True):
            if password_input == correct_password:
                st.session_state.password_correct = True
                st.rerun()
            else: st.error("❌ Access Denied")
    return False

if not check_password(): st.stop()

# --- 2. CSS STYLING ---
st.markdown("""
    <style>
    .metric-box { padding: 10px; border-radius: 5px; text-align: center; color: white; font-weight: bold; box-shadow: 0px 0px 5px rgba(255,255,255,0.1); }
    h1 { color: #FFD700; text-align: center; font-family: sans-serif; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #1E1E1E; border-radius: 5px; }
    .stTabs [aria-selected="true"] { background-color: #263238; border-bottom: 2px solid #FFD700; }
    </style>
""", unsafe_allow_html=True)

# --- 3. SIDEBAR SETTINGS ---
st.sidebar.header("⚙️ Master Settings")

try:
    DEFAULT_BOT_TOKEN = st.secrets["telegram"]["bot_token"]
    DEFAULT_CHAT_ID = st.secrets["telegram"]["chat_id"]
    DEFAULT_ENABLE = True
except (FileNotFoundError, KeyError):
    DEFAULT_BOT_TOKEN = ""
    DEFAULT_CHAT_ID = ""
    DEFAULT_ENABLE = False

symbol = st.sidebar.text_input("Symbol", value="GC=F") 
refresh_rate = st.sidebar.slider("Refresh Speed (s)", 5, 300, 5) 
sensitivity = st.sidebar.number_input("Structure Sensitivity", min_value=1, max_value=10, value=2)

st.sidebar.markdown("---")
st.sidebar.header("📱 Telegram Alerts")
tg_token = st.sidebar.text_input("Bot Token", value=DEFAULT_BOT_TOKEN, type="password")
tg_chat_id = st.sidebar.text_input("Chat ID", value=DEFAULT_CHAT_ID)
enable_tg = st.sidebar.checkbox("Enable Notifications", value=DEFAULT_ENABLE)

if st.sidebar.button("🔒 LOCK SYSTEM"):
    st.session_state.password_correct = False
    st.rerun()

stop_btn = st.sidebar.button("🟥 STOP DATA ENGINE")

# --- 4. DATA ENGINE ---
def clean_data(df):
    if df is None or df.empty: return None
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    if df.index.tz is not None: df.index = df.index.tz_localize(None)
    return df

def fetch_hierarchical_data(symbol):
    try:
        df_long = yf.download(symbol, period="2y", interval="1d", progress=False, auto_adjust=False)
        df_mid = yf.download(symbol, period="60d", interval="1h", progress=False, auto_adjust=False)
        df_short = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=False)
        return clean_data(df_long), clean_data(df_mid), clean_data(df_short)
    except Exception as e:
        print(e)
        return None, None, None

def resample_data(df, tf):
    if df is None or df.empty: return None
    agg = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
    try:
        if tf == "1wk": return df.resample("W-FRI").agg(agg).dropna()
        if tf == "4h": return df.resample("4h").agg(agg).dropna()
        if tf == "30m": return df.resample("30min").agg(agg).dropna()
        if tf == "15m": return df.resample("15min").agg(agg).dropna()
    except: return df 
    return df 

# --- 5. LOGIC ENGINE ---
def calculate_structure(df, lookback):
    if df is None or df.empty: return df, "N/A"
    df = df.copy()
    
    df['Rolling_Max'] = df['High'].rolling(window=lookback).max()
    df['Rolling_Min'] = df['Low'].rolling(window=lookback).min()
    
    state_list = []
    curr_state = -1 
    curr_res = df['High'].iloc[0]
    curr_sup = df['Low'].iloc[0]
    
    for i in range(len(df)):
        close = df['Close'].iloc[i]
        high = df['High'].iloc[i]
        low = df['Low'].iloc[i]
        
        local_max = df['Rolling_Max'].iloc[i] if not pd.isna(df['Rolling_Max'].iloc[i]) else high
        local_min = df['Rolling_Min'].iloc[i] if not pd.isna(df['Rolling_Min'].iloc[i]) else low
        
        if curr_state == -1: # BEARISH
            if local_max < curr_res: curr_res = local_max 
            if close > curr_res: curr_state = 1; curr_sup = local_min 
        elif curr_state == 1: # BULLISH
            if local_min > curr_sup: curr_sup = local_min 
            if close < curr_sup: curr_state = -1; curr_res = local_max
                
        state_list.append("BULLISH" if curr_state == 1 else "BEARISH")
    
    df['State'] = state_list
    return df, df['State'].iloc[-1]

def get_trend_start_time(df):
    if df is None or df.empty: return None
    current_state = df['State'].iloc[-1]
    for i in range(len(df)-1, -1, -1):
        if df['State'].iloc[i] != current_state:
            return df.index[i+1] if i+1 < len(df) else df.index[i]
    return df.index[0]

# --- 🧠 HIERARCHY LOGIC: LRCF / HRCF ---
def analyze_hierarchy(df_setup, df_filter, df_trigger, setup_name, filter_name, trigger_name):
    if df_setup is None or df_filter is None or df_trigger is None:
        return "N/A", "Loading...", "#37474F"

    # 1. SETUP PHASE
    setup_state = df_setup['State'].iloc[-1]
    setup_dir = "BUY" if setup_state == "BULLISH" else "SELL"
    setup_start = get_trend_start_time(df_setup)
    
    # 2. VALID RETRACEMENT (VR) PHASE
    df_filter_slice = df_filter[df_filter.index >= setup_start].copy()
    
    if df_filter_slice.empty: 
        return "WAITING VR", f"Setup: {setup_name} {setup_dir} | Type: N/A | Status: No Data", "#37474F"

    vr_target_state = "BEARISH" if setup_state == "BULLISH" else "BULLISH"
    vr_candles = df_filter_slice[df_filter_slice['State'] == vr_target_state]
    
    # No VR yet (Filter never pulled back)
    if vr_candles.empty:
        return "⏳ WAITING VR", f"Setup: {setup_name} {setup_dir} | Type: PULLBACK | Status: Waiting for VR", "#FF6D00"

    first_vr_time = vr_candles.index[0]
    
    # 3. CONFIRMATION / TRIGGER PHASE
    curr_filter_state = df_filter['State'].iloc[-1]
    curr_trigger_state = df_trigger['State'].iloc[-1]
    
    # A. LOW RISK (LRCF)
    if curr_filter_state == setup_state:
        df_after_vr = df_filter_slice[df_filter_slice.index >= first_vr_time]
        df_after_vr['group'] = (df_after_vr['State'] != df_after_vr['State'].shift()).cumsum()
        cf_count = len(df_after_vr[df_after_vr['State'] == setup_state]['group'].unique())
        tag = "(Origin)" if cf_count == 1 else f"(Re-Entry {cf_count})"
        
        return f"💎 LRCF {setup_dir}", f"Setup: {setup_name} {setup_dir} | Type: LRCF ({filter_name}) | Status: CF Formed {tag}", "#00C853"

    # B. HIGH RISK (HRCF)
    elif curr_trigger_state == setup_state:
        return f"⚠️ HRCF {setup_dir}", f"Setup: {setup_name} {setup_dir} | Type: HRCF ({trigger_name}) | Status: CF Formed (Aggressive)", "#FF9100"

    # C. WAITING CF (VR is Formed, but no break yet)
    else:
        return "💤 WAITING CF", f"Setup: {setup_name} {setup_dir} | Type: {filter_name} VR | Status: VR Formed (Waiting Break)", "#37474F"

# --- 6. PLOTTING ---
def plot_candlestick(df, title, state):
    if df is None or df.empty: return go.Figure()
    df_slice = df.tail(80) 
    fig = go.Figure()
    c = '#00C853' if state == "BULLISH" else '#FF5252'
    fig.add_trace(go.Candlestick(
        x=df_slice.index, open=df_slice['Open'], high=df_slice['High'], low=df_slice['Low'], close=df_slice['Close'],
        name='Price', increasing_line_color='#00C853', decreasing_line_color='#FF5252'
    ))
    fig.update_layout(
        title=dict(text=f"{title} ({state})", font=dict(color=c)), height=300,
        margin=dict(t=30,b=0,l=0,r=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False, rangeslider=dict(visible=False)), yaxis=dict(showgrid=True, gridcolor="#333"),
        font=dict(color="white")
    )
    return fig

# --- 7. ALERTS (UPDATED FORMAT) ---
def send_telegram_msg(message):
    if not enable_tg or not tg_token or not tg_chat_id: return
    try: requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage", data={"chat_id": tg_chat_id, "text": message}, timeout=5)
    except: pass

if "alert_state" not in st.session_state: st.session_state.alert_state = {}

def check_and_alert(header, signal, desc):
    sig_key = f"{header}|{signal}"
    last_sig = st.session_state.alert_state.get(header, None)
    
    if sig_key != last_sig:
        st.session_state.alert_state[header] = sig_key
        
        if "LRCF" in signal or "HRCF" in signal:
            icon = "💎" if "LRCF" in signal else "⚠️"
            formatted_desc = desc.replace(" | ", "\n")
            msg = f"{icon} **{header} SIGNAL**\n{formatted_desc}\n🔥 CHECK CHART"
            send_telegram_msg(msg)

# --- 8. MAIN EXECUTION ---
st.title(f"🏆 BONKER V5.3: STABLE ALERTS ({symbol})")

tabs = st.tabs(["Weekly", "Daily", "H4", "H1", "M30"])

if "run" not in st.session_state: st.session_state.run = True
if stop_btn: st.session_state.run = False

try:
    while st.session_state.run:
        df_long_raw, df_mid_raw, df_short_raw = fetch_hierarchical_data(symbol)
        
        if df_long_raw is not None and df_mid_raw is not None and df_short_raw is not None:
            # Structures
            df_w1, s_w1 = calculate_structure(resample_data(df_long_raw.copy(), "1wk"), sensitivity)
            df_d1, s_d1 = calculate_structure(df_long_raw.copy(), sensitivity)
            df_h4, s_h4 = calculate_structure(resample_data(df_mid_raw.copy(), "4h"), sensitivity)
            df_h1, s_h1 = calculate_structure(df_mid_raw.copy(), sensitivity)
            df_m30, s_m30 = calculate_structure(resample_data(df_short_raw.copy(), "30m"), sensitivity)
            df_m15, s_m15 = calculate_structure(resample_data(df_short_raw.copy(), "15m"), sensitivity)
            df_m5, s_m5 = calculate_structure(df_short_raw.copy(), sensitivity)

            # --- ANALYSIS ---
            with tabs[0]:
                sig, desc, col = analyze_hierarchy(df_w1, df_d1, df_h4, "W1", "D1", "H4")
                check_and_alert("WEEKLY SETUP", sig, desc)
                st.markdown(f"<div style='background:{col};padding:10px;border-radius:5px;text-align:center;'><h3>{sig}</h3><p>{desc}</p></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                # ADDED UNIQUE KEYS HERE
                with c1: st.plotly_chart(plot_candlestick(df_w1, "W1", s_w1), use_container_width=True, key="w1_setup")
                with c2: st.plotly_chart(plot_candlestick(df_d1, "D1", s_d1), use_container_width=True, key="w1_filter")
                with c3: st.plotly_chart(plot_candlestick(df_h4, "H4", s_h4), use_container_width=True, key="w1_trigger")

            with tabs[1]:
                sig, desc, col = analyze_hierarchy(df_d1, df_h4, df_h1, "D1", "H4", "H1")
                check_and_alert("DAILY SETUP", sig, desc)
                st.markdown(f"<div style='background:{col};padding:10px;border-radius:5px;text-align:center;'><h3>{sig}</h3><p>{desc}</p></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                # ADDED UNIQUE KEYS HERE
                with c1: st.plotly_chart(plot_candlestick(df_d1, "D1", s_d1), use_container_width=True, key="d1_setup")
                with c2: st.plotly_chart(plot_candlestick(df_h4, "H4", s_h4), use_container_width=True, key="d1_filter")
                with c3: st.plotly_chart(plot_candlestick(df_h1, "H1", s_h1), use_container_width=True, key="d1_trigger")

            with tabs[2]:
                sig, desc, col = analyze_hierarchy(df_h4, df_h1, df_m30, "H4", "H1", "M30")
                check_and_alert("H4 SETUP", sig, desc)
                st.markdown(f"<div style='background:{col};padding:10px;border-radius:5px;text-align:center;'><h3>{sig}</h3><p>{desc}</p></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                # ADDED UNIQUE KEYS HERE
                with c1: st.plotly_chart(plot_candlestick(df_h4, "H4", s_h4), use_container_width=True, key="h4_setup")
                with c2: st.plotly_chart(plot_candlestick(df_h1, "H1", s_h1), use_container_width=True, key="h4_filter")
                with c3: st.plotly_chart(plot_candlestick(df_m30, "M30", s_m30), use_container_width=True, key="h4_trigger")

            with tabs[3]:
                sig, desc, col = analyze_hierarchy(df_h1, df_m30, df_m15, "H1", "M30", "M15")
                check_and_alert("H1 SETUP", sig, desc)
                st.markdown(f"<div style='background:{col};padding:10px;border-radius:5px;text-align:center;'><h3>{sig}</h3><p>{desc}</p></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                # ADDED UNIQUE KEYS HERE
                with c1: st.plotly_chart(plot_candlestick(df_h1, "H1", s_h1), use_container_width=True, key="h1_setup")
                with c2: st.plotly_chart(plot_candlestick(df_m30, "M30", s_m30), use_container_width=True, key="h1_filter")
                with c3: st.plotly_chart(plot_candlestick(df_m15, "M15", s_m15), use_container_width=True, key="h1_trigger")

            with tabs[4]:
                sig, desc, col = analyze_hierarchy(df_m30, df_m15, df_m5, "M30", "M15", "M5")
                check_and_alert("M30 SETUP", sig, desc)
                st.markdown(f"<div style='background:{col};padding:10px;border-radius:5px;text-align:center;'><h3>{sig}</h3><p>{desc}</p></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                # ADDED UNIQUE KEYS HERE
                with c1: st.plotly_chart(plot_candlestick(df_m30, "M30", s_m30), use_container_width=True, key="m30_setup")
                with c2: st.plotly_chart(plot_candlestick(df_m15, "M15", s_m15), use_container_width=True, key="m30_filter")
                with c3: st.plotly_chart(plot_candlestick(df_m5, "M5", s_m5), use_container_width=True, key="m30_trigger")

            time.sleep(refresh_rate)
            st.rerun()

        else:
            st.error("❌ Data Fetch Error (Retrying in 10s)...")
            time.sleep(10)
            st.rerun()
            
except KeyboardInterrupt: pass
except Exception as e: st.error(f"Critical Error: {e}")
