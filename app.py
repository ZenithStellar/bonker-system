import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import time
import warnings
import requests

# --- 1. CONFIGURATION ---
warnings.filterwarnings("ignore")
st.set_page_config(page_title="Bonker V3.5 (Multi-CF Logic)", layout="wide", page_icon="🏆")

# --- 🔐 KEYPASS SYSTEM ---
def check_password():
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
    .bullish { background-color: #00C853; border-bottom: 4px solid #00E676; }
    .bearish { background-color: #D50000; border-bottom: 4px solid #FF5252; }
    .waiting { background-color: #37474F; border-bottom: 4px solid #90A4AE; }
    h1 { color: #FFD700; text-align: center; font-family: sans-serif; }
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
refresh_rate = st.sidebar.slider("Refresh Speed (s)", 5, 300, 10) 
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
def fetch_data(symbol):
    try:
        df_5m = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=False, multi_level_index=False)
        df_daily = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=False, multi_level_index=False)
        if df_5m.empty or df_daily.empty: return None, None
        
        if isinstance(df_5m.columns, pd.MultiIndex): df_5m.columns = df_5m.columns.get_level_values(0)
        if isinstance(df_daily.columns, pd.MultiIndex): df_daily.columns = df_daily.columns.get_level_values(0)
        return df_daily, df_5m
    except: return None, None

def resample_data(df, tf):
    agg = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
    if tf == "15m": return df.resample("15min").agg(agg).dropna()
    if tf == "30m": return df.resample("30min").agg(agg).dropna()
    if tf == "1h": return df.resample("1h").agg(agg).dropna()
    if tf == "4h": return df.resample("4h").agg(agg).dropna()
    return df 

# --- 5. LOGIC: SMART STRUCTURE ---
def calculate_structure(df, lookback):
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
        local_max = df['Rolling_Max'].iloc[i] if i > lookback else high
        local_min = df['Rolling_Min'].iloc[i] if i > lookback else low
        
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
    if df.empty: return None
    current_state = df['State'].iloc[-1]
    for i in range(len(df)-1, -1, -1):
        if df['State'].iloc[i] != current_state:
            return df.index[i+1] if i+1 < len(df) else df.index[i]
    return df.index[0]

# --- 🧠 CORE LOGIC: SINGLE VR / MULTI CF ---
def analyze_strict_cycle(df_trend, df_entry, trend_state, type_label="ENTRY"):
    # 1. Start of Parent Trend
    trend_start = get_trend_start_time(df_trend)
    df_slice = df_entry[df_entry.index >= trend_start].copy()
    
    if df_slice.empty: return f"WAITING", "No Data", "#37474F", 0

    opp_state = "BEARISH" if trend_state == "BULLISH" else "BULLISH"
    df_slice['group'] = (df_slice['State'] != df_slice['State'].shift()).cumsum()
    
    # 2. Find ALL groups that were opposite (VRs)
    vr_groups = df_slice[df_slice['State'] == opp_state]['group'].unique()
    
    # IF NO VR EVER HAPPENED since Trend Started
    if len(vr_groups) == 0:
        return f"⏳ WAITING VR", "Waiting for First Pullback", "#FF6D00", 0
        
    # 3. Analyze from the FIRST VR onwards (Single Origin Logic)
    # We find the very first time M5 went against M30. Everything after is "The Zone".
    first_vr_group_id = vr_groups[0]
    first_vr_start_idx = df_slice[df_slice['group'] == first_vr_group_id].index[0]
    
    # Slice data starting from the First VR
    df_cycle = df_slice[df_slice.index >= first_vr_start_idx].copy()
    
    # 4. Check Current Status
    current_child_state = df_cycle['State'].iloc[-1]
    
    # Count how many Buy (Trend) blocks happened since that first VR
    cf_groups = df_cycle[df_cycle['State'] == trend_state]['group'].unique()
    cf_count = len(cf_groups)
    
    # LOGIC GATES
    if current_child_state == opp_state:
        # We are currently in a red candle (Pullback)
        return f"⏳ WAITING CF", "VR Formed. Waiting for Break.", "#FF6D00", cf_count
        
    elif current_child_state == trend_state:
        # We are currently Green (Entry)
        if cf_count == 1:
            return f"💎 {type_label}", "CF Formed (Origin). ENTRY.", "#00C853", 1
        else:
            # Logic: If CF count > 1, it means we entered, failed/pulled back, and entered again.
            return f"💎 {type_label}", f"CF Formed (Re-Entry #{cf_count}).", "#00C853", cf_count
    
    return "WAITING", "Calculating...", "#37474F", 0

# --- 6. PLOTTING ---
def plot_smart_chart(df, title, state, tag=None):
    df_slice = df.tail(60)
    fig = go.Figure()
    c = "#00E676" if state == "BULLISH" else "#FF5252"
    title_text = f"{title} ({state})"
    if tag: title_text += f"  |  {tag}"
    fig.add_trace(go.Scatter(x=df_slice.index, y=df_slice['Close'], mode='lines', name='Price', line=dict(color=c, width=1)))
    fig.update_layout(title=dict(text=title_text, font=dict(color="white")), height=250, margin=dict(t=30,b=0,l=0,r=0), 
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", 
                      xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#333"), font=dict(color="white"))
    return fig

# --- 7. CUSTOM TELEGRAM ALERTS ---
def send_telegram_msg(message):
    if not enable_tg or not tg_token or not tg_chat_id: return
    try: requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage", data={"chat_id": tg_chat_id, "text": message}, timeout=5)
    except: pass

if "alert_state" not in st.session_state: st.session_state.alert_state = {}

def check_and_alert_custom(header_name, parent_tf_name, parent_state, main_sig, detail_sig, attempt_num=0):
    current_signature = f"{header_name}|{parent_state}|{main_sig}|{detail_sig}"
    last_signature = st.session_state.alert_state.get(header_name, None)
    
    if current_signature != last_signature:
        st.session_state.alert_state[header_name] = current_signature
        trend_dir = "Buy" if "BULLISH" in parent_state else "Sell"
        
        attempt_text = f" (Re-Entry #{attempt_num})" if attempt_num > 1 else " (Origin)"
        
        # ALERT LOGIC MATCHING USER PREFERENCE
        if "CF Formed" in detail_sig or "ENTRY" in main_sig:
            msg = f"💎 **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nCF Formed{attempt_text}.\n🔥 ENTRY NOW"
        
        elif "WAITING CF" in main_sig:
            msg = f"⏳ **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nVR Formed.\nWaiting CF."
        
        elif "WAITING VR" in main_sig:
             msg = f"💤 **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nWaiting for VR (Pullback)."
        
        else:
            msg = f"ℹ️ **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nStatus: {main_sig}"

        send_telegram_msg(msg)

# --- 8. MAIN EXECUTION ---
st.title(f"🏆 BONKER TRADING SYSTEM: {symbol}")
tab_swing, tab_intraday, tab_normal, tab_scalp, tab_hyper = st.tabs(["📊 Daily", "🎯 H4-M30", "🔹 Normal", "⚡ H1-M15", "⚡ M30-M5"])

if "run" not in st.session_state: st.session_state.run = True
if stop_btn: st.session_state.run = False

try:
    while st.session_state.run:
        df_d_raw, df_5m_raw = fetch_data(symbol)
        
        if df_d_raw is not None and df_5m_raw is not None:
            # NOISE FILTER: M5/M15 need higher sensitivity to avoid fake breakouts
            sens_norm = sensitivity 
            sens_entry = sensitivity 

            df_d, s_d = calculate_structure(df_d_raw, sens_norm)
            df_h4, s_h4 = calculate_structure(resample_data(df_5m_raw.copy(), "4h"), sens_norm)
            df_h1, s_h1 = calculate_structure(resample_data(df_5m_raw.copy(), "1h"), sens_norm)
            df_m30, s_m30 = calculate_structure(resample_data(df_5m_raw.copy(), "30m"), sens_norm)
            df_m15, s_m15 = calculate_structure(resample_data(df_5m_raw.copy(), "15m"), sens_entry)
            df_m5, s_m5 = calculate_structure(df_5m_raw.copy(), sens_entry)

            # SWING
            sig_cas, bg_cas, detail_cas = "WAITING", "#37474F", "Monitor M30"
            if s_d == s_h4 == s_h1 == "BULLISH":
                if s_m30 == "BULLISH": sig_cas, bg_cas, detail_cas = "🚀 SWING BUY", "#00C853", "Full Alignment"
                else: sig_cas, bg_cas, detail_cas = "⏳ WAITING ENTRY", "#FF6D00", "VR Formed"
            elif s_d == s_h4 == s_h1 == "BEARISH":
                if s_m30 == "BEARISH": sig_cas, bg_cas, detail_cas = "🚀 SWING SELL", "#D50000", "Full Alignment"
                else: sig_cas, bg_cas, detail_cas = "⏳ WAITING ENTRY", "#FF6D00", "VR Formed"
            check_and_alert_custom("SWING", "Daily", s_d, sig_cas, detail_cas)

            # RICH
            sig_i, det_i, bg_i, att_i = analyze_strict_cycle(df_h4, df_m30, s_h4, "M30 ENTRY")
            check_and_alert_custom("H4-M30", "H4", s_h4, sig_i, det_i, att_i)

            # NORMAL
            sig_n, det_n, bg_n, att_n = analyze_strict_cycle(df_h4, df_h1, s_h4, "H1 ENTRY")
            check_and_alert_custom("NORMAL", "H4", s_h4, sig_n, det_n, att_n)

            # SCALP
            sig_s, det_s, bg_s, att_s = analyze_strict_cycle(df_h1, df_m15, s_h1, "M15 ENTRY")
            check_and_alert_custom("H1-M15", "H1", s_h1, sig_s, det_s, att_s)

            # HYPER
            sig_h, det_h, bg_h, att_h = analyze_strict_cycle(df_m30, df_m5, s_m30, "M5 ENTRY")
            check_and_alert_custom("M30-M5", "M30", s_m30, sig_h, det_h, att_h)

            # RENDER
            with tab_swing:
                c_banner = st.container()
                col_d, col_h4, col_h1, col_m30 = st.columns(4)
                row_charts_c = st.container()
                with c_banner: st.markdown(f"<div style='background:{bg_cas};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_cas}</h2></div>", unsafe_allow_html=True)
                with col_d: st.markdown(f'<div class="metric-box {s_d.lower()}">DAILY<br>{s_d}</div>', unsafe_allow_html=True)
                with col_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">H4<br>{s_h4}</div>', unsafe_allow_html=True)
                with col_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">H1<br>{s_h1}</div>', unsafe_allow_html=True)
                with col_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30<br>{s_m30}</div>', unsafe_allow_html=True)
                with row_charts_c:
                    g1, g2, g3, g4 = st.columns(4)
                    with g1: st.plotly_chart(plot_smart_chart(df_d, "Daily", s_d), width="stretch")
                    with g2: st.plotly_chart(plot_smart_chart(df_h4, "H4", s_h4), width="stretch")
                    with g3: st.plotly_chart(plot_smart_chart(df_h1, "H1", s_h1), width="stretch")
                    with g4: st.plotly_chart(plot_smart_chart(df_m30, "M30", s_m30), width="stretch")

            with tab_intraday:
                i_banner = st.container()
                col_i_h4, col_i_m30 = st.columns(2) 
                row_charts_i = st.container()
                with i_banner: st.markdown(f"<div style='background:{bg_i};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_i}</h2></div>", unsafe_allow_html=True)
                with col_i_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">H4 TREND<br>{s_h4}</div>', unsafe_allow_html=True)
                with col_i_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 ENTRY<br>{s_m30}<br><span style="font-size:12px">{det_i}</span></div>', unsafe_allow_html=True)
                with row_charts_i:
                    ig1, ig2 = st.columns(2)
                    with ig1: st.plotly_chart(plot_smart_chart(df_h4, "H4 Trend", s_h4), width="stretch")
                    with ig2: st.plotly_chart(plot_smart_chart(df_m30, "M30 Sequence", s_m30, tag=det_i), width="stretch")

            with tab_normal:
                n_banner = st.container()
                col_n_h4, col_n_h1 = st.columns(2)
                n_charts = st.container()
                with n_banner: st.markdown(f"<div style='background:{bg_n};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_n}</h2></div>", unsafe_allow_html=True)
                with col_n_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">1. H4 TREND<br>{s_h4}</div>', unsafe_allow_html=True)
                with col_n_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">2. H1 PULLBACK<br>{s_h1}<br><span style="font-size:12px">{det_n}</span></div>', unsafe_allow_html=True)
                with n_charts:
                    ng1, ng2 = st.columns(2)
                    with ng1: st.plotly_chart(plot_smart_chart(df_h4, "Step 1: H4 Trend", s_h4), width="stretch")
                    with ng2: st.plotly_chart(plot_smart_chart(df_h1, "Step 2: H1 CF", s_h1), width="stretch")

            with tab_scalp:
                s_banner = st.container()
                col_s_h1, col_s_m15 = st.columns(2)
                s_charts = st.container()
                with s_banner: st.markdown(f"<div style='background:{bg_s};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_s}</h2></div>", unsafe_allow_html=True)
                with col_s_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">H1 TREND<br>{s_h1}</div>', unsafe_allow_html=True)
                with col_s_m15: st.markdown(f'<div class="metric-box {s_m15.lower()}">M15 ENTRY<br>{s_m15}<br><span style="font-size:12px">{det_s}</span></div>', unsafe_allow_html=True)
                with s_charts:
                    sg1, sg2 = st.columns(2)
                    with sg1: st.plotly_chart(plot_smart_chart(df_h1, "H1 Trend", s_h1), width="stretch")
                    with sg2: st.plotly_chart(plot_smart_chart(df_m15, "M15 Sequence", s_m15, tag=det_s), width="stretch")

            with tab_hyper:
                h_banner = st.container()
                col_h_m30, col_h_m5 = st.columns(2)
                h_charts = st.container()
                with h_banner: st.markdown(f"<div style='background:{bg_h};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_h}</h2></div>", unsafe_allow_html=True)
                with col_h_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 TREND<br>{s_m30}</div>', unsafe_allow_html=True)
                with col_h_m5: st.markdown(f'<div class="metric-box {s_m5.lower()}">M5 ENTRY<br>{s_m5}<br><span style="font-size:12px">{det_h}</span></div>', unsafe_allow_html=True)
                with h_charts:
                    hg1, hg2 = st.columns(2)
                    with hg1: st.plotly_chart(plot_smart_chart(df_m30, "M30 Trend", s_m30), width="stretch")
                    with hg2: st.plotly_chart(plot_smart_chart(df_m5, "M5 Sequence", s_m5, tag=det_h), width="stretch")

            time.sleep(refresh_rate)
            st.rerun()
        else:
            st.error(f"❌ Data Error for {symbol}. Retrying...")
            time.sleep(10)
            st.rerun()
except KeyboardInterrupt: pass
except Exception as e: st.error(f"Error: {e}")
