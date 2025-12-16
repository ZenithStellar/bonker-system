import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import time
import warnings
import requests  # <--- NEW IMPORT

# --- 1. CONFIGURATION ---
warnings.filterwarnings("ignore")
st.set_page_config(page_title="Bonker Trading System V2.7", layout="wide", page_icon="🏆")

# --- 🔐 KEYPASS SYSTEM ---
def check_password():
    """Returns `True` if the user had the correct password."""
    correct_password = st.secrets["PASSWORD"] 

    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    st.markdown("<h1 style='text-align: center; color: #FFD700;'>🔒 SYSTEM LOCKED</h1>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password_input = st.text_input("Access Key", type="password")
        if st.button("UNLOCK SYSTEM", use_container_width=True):
            if password_input == correct_password:
                st.session_state.password_correct = True
                st.rerun()
            else:
                st.error("❌ Access Denied: Incorrect Key")
    return False

if not check_password():
    st.stop()

# --- 2. CSS STYLING ---
st.markdown("""
    <style>
    /* Metric Boxes */
    .metric-box { padding: 10px; border-radius: 5px; text-align: center; color: white; font-weight: bold; box-shadow: 0px 0px 5px rgba(255,255,255,0.1); }
    .bullish { background-color: #00C853; border-bottom: 4px solid #00E676; }
    .bearish { background-color: #D50000; border-bottom: 4px solid #FF5252; }
    .waiting { background-color: #37474F; border-bottom: 4px solid #90A4AE; }
    
    /* Headers & Tabs */
    h1 { color: #FFD700; text-align: center; font-family: sans-serif; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #212121; border-radius: 5px; color: white; }
    .stTabs [aria-selected="true"] { background-color: #FFD700 !important; color: black !important; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- 3. SIDEBAR SETTINGS ---
st.sidebar.header("⚙️ Master Settings")
symbol = st.sidebar.text_input("Symbol", value="GC=F") 
st.sidebar.caption("Use **GC=F** (Futures) or **XAUUSD=X** (Spot).")
refresh_rate = st.sidebar.slider("Refresh Speed (s)", 5, 300, 10) # Increased min default to avoid spam logic checks
sensitivity = st.sidebar.number_input("Structure Sensitivity", min_value=1, max_value=10, value=2)

# --- 🤖 TELEGRAM SETTINGS (NEW) ---
st.sidebar.markdown("---")
st.sidebar.header("📱 Telegram Alerts")
tg_token = st.sidebar.text_input("Bot Token", type="password", help="Get from @BotFather")
tg_chat_id = st.sidebar.text_input("Chat ID", help="Get from @userinfobot")
enable_tg = st.sidebar.checkbox("Enable Notifications", value=False)

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

def count_signals_since(df_target, start_time, target_state):
    """Checks for cycle completion."""
    df_slice = df_target[df_target.index >= start_time].copy()
    if df_slice.empty: return 0, False
    
    df_slice['block'] = (df_slice['State'] != df_slice['State'].shift()).cumsum()
    matching_blocks = df_slice[df_slice['State'] == target_state]['block'].unique()
    
    count = len(matching_blocks)
    is_currently_active = (df_slice['State'].iloc[-1] == target_state)
    
    return count, is_currently_active

def analyze_strict_cycle(df_trend, df_entry, trend_state, type_label="ENTRY"):
    trend_start = get_trend_start_time(df_trend)
    df_slice = df_entry[df_entry.index >= trend_start].copy()
    
    if df_slice.empty: return f"WAITING", "No Data", "#37474F"

    opp_state = "BEARISH" if trend_state == "BULLISH" else "BULLISH"
    df_slice['group'] = (df_slice['State'] != df_slice['State'].shift()).cumsum()
    vr_groups = df_slice[df_slice['State'] == opp_state]['group'].unique()
    
    if len(vr_groups) == 0:
        return f"⏳ WAITING VR", f"Parent Trending -> Wait {type_label} Pullback", "#FF6D00"
    
    last_vr_id = vr_groups[-1]
    last_vr_start_idx = df_slice[df_slice['group'] == last_vr_id].index[0]
    df_cycle = df_slice[df_slice.index >= last_vr_start_idx].copy()
    
    current_child_state = df_cycle['State'].iloc[-1]
    conf_groups = df_cycle[df_cycle['State'] == trend_state]['group'].unique()
    count_cf = len(conf_groups)
    
    if current_child_state == opp_state:
        return f"⏳ WAITING {type_label}", "Active VR (Loading...)", "#FF6D00"
        
    elif count_cf == 1:
        if current_child_state == trend_state:
            return f"💎 {type_label} (Fresh)", "Fresh Break of Latest VR", "#00C853"
        else:
            return f"⏳ WAITING {type_label}", "Pullback Active", "#FF6D00"
    else:
        if current_child_state == trend_state:
            return f"🚀 CONTINUATION", f"Push #{count_cf} after VR", "#558B2F"
        else:
            return f"⏳ WAITING {type_label}", "New VR Starting...", "#FF6D00"

# --- 6. PLOTTING ---
def plot_smart_chart(df, title, state, tag=None):
    df_slice = df.tail(60)
    fig = go.Figure()
    c = "#00E676" if state == "BULLISH" else "#FF5252"
    
    title_text = f"{title} ({state})"
    if tag: title_text += f"  |  {tag}"

    fig.add_trace(go.Scatter(x=df_slice.index, y=df_slice['Close'], mode='lines', name='Price', line=dict(color=c, width=1)))
    
    fig.update_layout(
        title=dict(text=title_text, font=dict(color="white")), 
        height=250, margin=dict(t=30,b=0,l=0,r=0), 
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", 
        xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#333"), 
        font=dict(color="white"), xaxis_rangeslider_visible=False
    )
    return fig

# --- 7. TELEGRAM FUNCTIONALITY (NEW) ---
def send_telegram_msg(message):
    if not enable_tg or not tg_token or not tg_chat_id:
        return
    
    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    try:
        data = {"chat_id": tg_chat_id, "text": message}
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

# State tracker for alerts to avoid spamming
if "alert_state" not in st.session_state:
    st.session_state.alert_state = {}

def check_and_alert(strategy_name, main_status, sub_status):
    """
    Checks if the combined status has changed. 
    If changed, updates state and sends Telegram msg.
    """
    # Create a unique signature for the current state
    current_signature = f"{main_status} || {sub_status}"
    
    # Retrieve last known signature
    last_signature = st.session_state.alert_state.get(strategy_name, None)
    
    # If it's different, update and alert
    if current_signature != last_signature:
        st.session_state.alert_state[strategy_name] = current_signature
        
        # Only send alert if it's not the very first run (optional, removes startup spam)
        # But for now we send on change.
        
        # Format the message nicely
        emoji = "🔔"
        if "ENTRY" in main_status or "FRESH" in main_status: emoji = "💎"
        if "CONTINUATION" in main_status: emoji = "🚀"
        if "WAITING VR" in main_status: emoji = "⏳"
        
        msg = (
            f"{emoji} **{strategy_name} UPDATE**\n"
            f"Symbol: {symbol}\n"
            f"Status: {main_status}\n"
            f"Detail: {sub_status}"
        )
        send_telegram_msg(msg)

# --- 8. MAIN EXECUTION ---
st.title(f"🏆 BONKER TRADING SYSTEM: {symbol}")

# --- TABS ---
tab_swing, tab_intraday, tab_normal, tab_scalp, tab_hyper = st.tabs([
    "📊 Daily Deploy", 
    "🎯 Rich Setup", 
    "🔹 Normal Sequence", 
    "⚡ Scalping",
    "⚡ Hyper Scalp"
])

# Layout Containers
with tab_swing:
    c_banner = st.container()
    col_d, col_h4, col_h1, col_m30 = st.columns(4)
    row_charts_c = st.container()

with tab_intraday:
    i_banner = st.container()
    col_i_h4, col_i_m30 = st.columns(2) 
    row_charts_i = st.container()

with tab_normal:
    n_banner = st.container()
    col_n_h4, col_n_h1, col_n_m30 = st.columns(3)
    n_charts = st.container()

with tab_scalp:
    s_banner = st.container()
    col_s_h1, col_s_m15 = st.columns(2)
    s_charts = st.container()

with tab_hyper:
    h_banner = st.container()
    col_h_m30, col_h_m5 = st.columns(2)
    h_charts = st.container()

if "run" not in st.session_state: st.session_state.run = True
if stop_btn: st.session_state.run = False

try:
    while st.session_state.run:
        df_d_raw, df_5m_raw = fetch_data(symbol)
        
        if df_d_raw is not None and df_5m_raw is not None:
            
            # --- CALCULATIONS ---
            df_d, s_d = calculate_structure(df_d_raw, sensitivity)
            df_h4, s_h4 = calculate_structure(resample_data(df_5m_raw.copy(), "4h"), sensitivity)
            df_h1, s_h1 = calculate_structure(resample_data(df_5m_raw.copy(), "1h"), sensitivity)
            df_m30, s_m30 = calculate_structure(resample_data(df_5m_raw.copy(), "30m"), sensitivity)
            df_m15, s_m15 = calculate_structure(resample_data(df_5m_raw.copy(), "15m"), sensitivity)
            df_m5, s_m5 = calculate_structure(df_5m_raw.copy(), sensitivity)

            # --- LOGIC A: SWING ---
            sig_cas = "WAITING"
            bg_cas = "#37474F"
            detail_cas = "Monitor M30 Breakout"
            if s_d == "BULLISH" and s_h4 == "BULLISH" and s_h1 == "BULLISH":
                if s_m30 == "BULLISH": 
                    sig_cas = "🚀 SWING BUY (Full Alignment)" 
                    bg_cas = "#00C853"
                    detail_cas = "All TFs Aligned Bullish"
                else: 
                    sig_cas = "⏳ TREND UP - Waiting M30" 
                    bg_cas = "#FF6D00"
                    detail_cas = "M30 is Bearish (Retracement)"
            elif s_d == "BEARISH" and s_h4 == "BEARISH" and s_h1 == "BEARISH":
                if s_m30 == "BEARISH": 
                    sig_cas = "🚀 SWING SELL (Full Alignment)" 
                    bg_cas = "#D50000"
                    detail_cas = "All TFs Aligned Bearish"
                else: 
                    sig_cas = "⏳ TREND DOWN - Waiting M30" 
                    bg_cas = "#FF6D00"
                    detail_cas = "M30 is Bullish (Retracement)"
            
            # CHECK ALERT SWING
            check_and_alert("SWING (Daily)", sig_cas, detail_cas)

            # --- LOGIC B: INTRADAY (RICH SETUP) ---
            sig_intra, vr_status_text, bg_intra = analyze_strict_cycle(df_h4, df_m30, s_h4, "M30 ENTRY")
            
            # CHECK ALERT INTRADAY
            # This captures VR formation because 'vr_status_text' changes from "Parent Trending" to "Active VR"
            check_and_alert("RICH SETUP (Intraday)", sig_intra, vr_status_text)

            # --- LOGIC NEW: NORMAL SEQUENCE (H4 -> H1 -> M30) ---
            sig_norm = "WAITING"
            bg_norm = "#37474F"
            norm_note = "Scanning..."
            
            if s_h4 == "BULLISH":
                if s_h1 == "BEARISH": 
                    h1_start = get_trend_start_time(df_h1)
                    count, active = count_signals_since(df_m30, h1_start, "BULLISH")
                    norm_note = f"H1 Pullback Active ({count} M30 Cycles)"
                    if count == 0: sig_norm = "⏳ WAITING M30 ENTRY"; bg_norm = "#FF6D00" 
                    elif count == 1 and active: sig_norm = "🎯 FRESH BUY (Origin)"; bg_norm = "#00C853"
                    elif count >= 1 and not active: sig_norm = "⏳ PULLBACK IN PROGRESS"; bg_norm = "#37474F"
                    else: sig_norm = "⚠️ CYCLE FINISHED (Late)"; bg_norm = "#455A64"; norm_note = "Cycle complete."
                else: sig_norm = "⏳ WAITING H1 PULLBACK"; norm_note = "H1 is Bullish (No Discount)"
            elif s_h4 == "BEARISH":
                if s_h1 == "BULLISH":
                    h1_start = get_trend_start_time(df_h1)
                    count, active = count_signals_since(df_m30, h1_start, "BEARISH")
                    norm_note = f"H1 Pullback Active ({count} M30 Cycles)"
                    if count == 0: sig_norm = "⏳ WAITING M30 ENTRY"; bg_norm = "#FF6D00"
                    elif count == 1 and active: sig_norm = "🎯 FRESH SELL (Origin)"; bg_norm = "#D50000"
                    elif count >= 1 and not active: sig_norm = "⏳ PULLBACK IN PROGRESS"; bg_norm = "#37474F"
                    else: sig_norm = "⚠️ CYCLE FINISHED (Late)"; bg_norm = "#455A64"; norm_note = "Cycle complete."
                else: sig_norm = "⏳ WAITING H1 PULLBACK"; norm_note = "H1 is Bearish (No Discount)"
            
            # CHECK ALERT NORMAL
            check_and_alert("NORMAL SEQ (H4-H1-M30)", sig_norm, norm_note)

            # --- LOGIC C: SCALPER (H1 -> M15 Strict) ---
            sig_s, s_vr_text, bg_s = analyze_strict_cycle(df_h1, df_m15, s_h1, "M15 ENTRY")
            check_and_alert("SCALPER (H1-M15)", sig_s, s_vr_text)

            # --- LOGIC D: HYPER SCALP (M30 -> M5 Strict) ---
            sig_h, h_vr_text, bg_h = analyze_strict_cycle(df_m30, df_m5, s_m30, "M5 ENTRY")
            check_and_alert("HYPER SCALP (M30-M5)", sig_h, h_vr_text)

            # --- RENDER SWING ---
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

            # --- RENDER INTRADAY ---
            with i_banner: st.markdown(f"<div style='background:{bg_intra};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_intra}</h2></div>", unsafe_allow_html=True)
            with col_i_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">H4 TREND<br>{s_h4}</div>', unsafe_allow_html=True)
            with col_i_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 ENTRY<br>{s_m30}<br><span style="font-size:12px">{vr_status_text}</span></div>', unsafe_allow_html=True)
            with row_charts_i:
                ig1, ig2 = st.columns(2)
                with ig1: st.plotly_chart(plot_smart_chart(df_h4, "H4 Trend", s_h4), width="stretch")
                with ig2: st.plotly_chart(plot_smart_chart(df_m30, "M30 Sequence", s_m30, tag=vr_status_text), width="stretch")

            # --- RENDER NORMAL SEQUENCE ---
            with n_banner: st.markdown(f"<div style='background:{bg_norm};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_norm}</h2></div>", unsafe_allow_html=True)
            with col_n_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">1. H4 TREND<br>{s_h4}</div>', unsafe_allow_html=True)
            with col_n_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">2. H1 PULLBACK<br>{s_h1}</div>', unsafe_allow_html=True)
            with col_n_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">3. M30 ENTRY<br>{s_m30}<br><span style="font-size:12px">{norm_note}</span></div>', unsafe_allow_html=True)
            with n_charts:
                ng1, ng2, ng3 = st.columns(3)
                with ng1: st.plotly_chart(plot_smart_chart(df_h4, "Step 1: H4 Trend", s_h4), width="stretch")
                with ng2: st.plotly_chart(plot_smart_chart(df_h1, "Step 2: H1 VR", s_h1), width="stretch")
                with ng3: st.plotly_chart(plot_smart_chart(df_m30, "Step 3: M30 CF", s_m30), width="stretch")

            # --- RENDER SCALP ---
            with s_banner: st.markdown(f"<div style='background:{bg_s};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_s}</h2></div>", unsafe_allow_html=True)
            with col_s_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">H1 TREND<br>{s_h1}</div>', unsafe_allow_html=True)
            with col_s_m15: st.markdown(f'<div class="metric-box {s_m15.lower()}">M15 ENTRY<br>{s_m15}<br><span style="font-size:12px">{s_vr_text}</span></div>', unsafe_allow_html=True)
            with s_charts:
                sg1, sg2 = st.columns(2)
                with sg1: st.plotly_chart(plot_smart_chart(df_h1, "H1 Trend", s_h1), width="stretch")
                with sg2: st.plotly_chart(plot_smart_chart(df_m15, "M15 Sequence", s_m15, tag=s_vr_text), width="stretch")

            # --- RENDER HYPER SCALP ---
            with h_banner: st.markdown(f"<div style='background:{bg_h};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_h}</h2></div>", unsafe_allow_html=True)
            with col_h_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 TREND<br>{s_m30}</div>', unsafe_allow_html=True)
            with col_h_m5: st.markdown(f'<div class="metric-box {s_m5.lower()}">M5 ENTRY<br>{s_m5}<br><span style="font-size:12px">{h_vr_text}</span></div>', unsafe_allow_html=True)
            with h_charts:
                hg1, hg2 = st.columns(2)
                with hg1: st.plotly_chart(plot_smart_chart(df_m30, "M30 Trend", s_m30), width="stretch")
                with hg2: st.plotly_chart(plot_smart_chart(df_m5, "M5 Sequence", s_m5, tag=h_vr_text), width="stretch")

            time.sleep(refresh_rate)
            st.rerun()
        else:
            st.error(f"❌ Data Error for {symbol}. Try 'GC=F' or 'GLD'.")
            time.sleep(10)
            st.rerun()
except KeyboardInterrupt:
    st.write("Stopped")
except Exception as e:
    st.error(f"System Error: {e}")
