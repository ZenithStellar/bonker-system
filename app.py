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
st.set_page_config(page_title="Bonker V3.3 (Strict Cycle)", layout="wide", page_icon="🏆")

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
    .metric-box { padding: 10px; border-radius: 5px; text-align: center; color: white; font-weight: bold; box-shadow: 0px 0px 5px rgba(255,255,255,0.1); }
    .bullish { background-color: #00C853; border-bottom: 4px solid #00E676; }
    .bearish { background-color: #D50000; border-bottom: 4px solid #FF5252; }
    .waiting { background-color: #37474F; border-bottom: 4px solid #90A4AE; }
    h1 { color: #FFD700; text-align: center; font-family: sans-serif; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #212121; border-radius: 5px; color: white; }
    .stTabs [aria-selected="true"] { background-color: #FFD700 !important; color: black !important; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- 3. SIDEBAR SETTINGS ---
st.sidebar.header("⚙️ Master Settings")

# --- 🟢 SECURE CREDENTIAL LOADING ---
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

# --- 🤖 TELEGRAM SETTINGS ---
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

# --- 🧠 CORE LOGIC ENGINE (STRICT VR/CF) ---
def analyze_strict_cycle(df_trend, df_entry, trend_state, type_label="ENTRY"):
    """
    STRICT LOGIC:
    1. Identify Trend Start.
    2. VR = Child breaks structure OPPOSITE to Trend.
    3. CF = Child breaks structure BACK to Trend (after VR).
    """
    # 1. Get Trend Start Time
    trend_start = get_trend_start_time(df_trend)
    
    # 2. Slice Entry Timeframe data (ONLY after Trend Started)
    df_slice = df_entry[df_entry.index >= trend_start].copy()
    
    if df_slice.empty: 
        return f"WAITING", "No Data", "#37474F"

    # 3. Create 'Blocks' of states (e.g. Bull-Bear-Bull)
    # This assigns a unique ID to each consecutive block of same-colored candles
    df_slice['group'] = (df_slice['State'] != df_slice['State'].shift()).cumsum()
    
    # 4. Identify State Logic
    opp_state = "BEARISH" if trend_state == "BULLISH" else "BULLISH"
    current_child_state = df_slice['State'].iloc[-1]
    
    # Find all blocks that were Opposite (VRs)
    vr_blocks = df_slice[df_slice['State'] == opp_state]['group'].unique()
    
    # --- SCENARIO A: NO VR YET ---
    # Trend started, but Child never went opposite. Just moved straight up/down.
    if len(vr_blocks) == 0:
        return f"⏳ WAITING VR", f"Waiting for {type_label} Pullback", "#FF6D00"
    
    # --- SCENARIO B: WE ARE IN A VR ---
    # The current state IS the opposite state.
    if current_child_state == opp_state:
        return f"⏳ WAITING CF", "VR Formed. Waiting CF.", "#FF6D00"
        
    # --- SCENARIO C: POTENTIAL CF (CONFIRMATION) ---
    # We are currently in Trend Direction (e.g. Bullish) AND we have seen a VR before.
    
    # Get the Last VR Block ID
    last_vr_id = vr_blocks[-1]
    
    # Get the ID of the current block
    current_block_id = df_slice['group'].iloc[-1]
    
    # Check strict sequence: Was the VERY LAST block a VR?
    # If Current ID = Last VR ID + 1, it means we JUST flipped.
    if current_block_id == last_vr_id + 1:
        return f"💎 {type_label} (CF)", "CF Formed. ENTRY NOW.", "#00C853"
    
    # If Current ID > Last VR ID + 1, it means we flipped a while ago and are continuing.
    else:
        return f"🚀 CONTINUATION", "Trend Continuing (Late)", "#558B2F"

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
    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    try: requests.post(url, data={"chat_id": tg_chat_id, "text": message}, timeout=5)
    except: pass

if "alert_state" not in st.session_state: st.session_state.alert_state = {}

def check_and_alert_custom(header_name, parent_tf_name, parent_state, main_sig, detail_sig):
    # Unique signature to detect change
    current_signature = f"{header_name}|{parent_state}|{main_sig}|{detail_sig}"
    last_signature = st.session_state.alert_state.get(header_name, None)
    
    if current_signature != last_signature:
        st.session_state.alert_state[header_name] = current_signature
        
        trend_dir = "Buy" if "BULLISH" in parent_state else "Sell"
        
        # --- ALERTS MATCHING STRICT LOGIC ---
        
        # 1. CF FORMED (ENTRY)
        if "CF" in main_sig and "FRESH" in main_sig or "ENTRY" in detail_sig:
            msg = f"💎 **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nCF Formed.\n🔥 ENTRY NOW"
        
        # 2. VR FORMED (WAITING)
        elif "WAITING CF" in main_sig or "VR Formed" in detail_sig:
             msg = f"⏳ **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nVR Formed.\nWaiting CF."
        
        # 3. NO VR YET
        elif "WAITING VR" in main_sig:
             msg = f"💤 **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nWaiting for VR (Pullback)."
        
        # 4. CONTINUATION
        elif "CONTINUATION" in main_sig:
             msg = f"🚀 **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nTrend Continuing (Late)."
        
        else:
            msg = f"ℹ️ **{header_name} UPDATE**\n{parent_tf_name} is {trend_dir}.\nStatus: {main_sig}"

        send_telegram_msg(msg)

# --- 8. MAIN EXECUTION ---
st.title(f"🏆 BONKER TRADING SYSTEM: {symbol}")

tab_swing, tab_intraday, tab_normal, tab_scalp, tab_hyper = st.tabs([
    "📊 Daily", "🎯 H4-M30", "🔹 Normal (H4-H1)", "⚡ H1-M15", "⚡ M30-M5"
])

if "run" not in st.session_state: st.session_state.run = True
if stop_btn: st.session_state.run = False

try:
    while st.session_state.run:
        df_d_raw, df_5m_raw = fetch_data(symbol)
        
        if df_d_raw is not None and df_5m_raw is not None:
            
            # --- ADAPTIVE SENSITIVITY ---
            # Standard Sensitivity for Trend (M30, H1, H4)
            sens_norm = sensitivity 
            # Stricter Sensitivity for Entry (M5, M15) to avoid noise
            sens_entry = sensitivity + 4 

            # --- CALCULATIONS ---
            df_d, s_d = calculate_structure(df_d_raw, sens_norm)
            df_h4, s_h4 = calculate_structure(resample_data(df_5m_raw.copy(), "4h"), sens_norm)
            df_h1, s_h1 = calculate_structure(resample_data(df_5m_raw.copy(), "1h"), sens_norm)
            df_m30, s_m30 = calculate_structure(resample_data(df_5m_raw.copy(), "30m"), sens_norm)
            
            # ENTRY TIME FRAMES (Strict)
            df_m15, s_m15 = calculate_structure(resample_data(df_5m_raw.copy(), "15m"), sens_entry)
            df_m5, s_m5 = calculate_structure(df_5m_raw.copy(), sens_entry)

            # --- LOGIC A: SWING ---
            sig_cas = "WAITING"
            bg_cas = "#37474F"
            detail_cas = "Monitor M30"
            if s_d == "BULLISH" and s_h4 == "BULLISH" and s_h1 == "BULLISH":
                if s_m30 == "BULLISH": sig_cas = "🚀 SWING BUY"; bg_cas = "#00C853"; detail_cas = "Full Alignment"
                else: sig_cas = "⏳ WAITING ENTRY"; bg_cas = "#FF6D00"; detail_cas = "VR Formed (M30)"
            elif s_d == "BEARISH" and s_h4 == "BEARISH" and s_h1 == "BEARISH":
                if s_m30 == "BEARISH": sig_cas = "🚀 SWING SELL"; bg_cas = "#D50000"; detail_cas = "Full Alignment"
                else: sig_cas = "⏳ WAITING ENTRY"; bg_cas = "#FF6D00"; detail_cas = "VR Formed (M30)"
            check_and_alert_custom("SWING (Daily)", "Daily", s_d, sig_cas, detail_cas)

            # --- LOGIC B: RICH SETUP (H4-M30) ---
            sig_intra, vr_status_intra, bg_intra = analyze_strict_cycle(df_h4, df_m30, s_h4, "M30 ENTRY")
            check_and_alert_custom("H4-M30", "H4", s_h4, sig_intra, vr_status_intra)

            # --- LOGIC C: NORMAL SEQUENCE (H4-H1) ---
            # Used strict cycle logic for consistency
            sig_norm, vr_status_norm, bg_norm = analyze_strict_cycle(df_h4, df_h1, s_h4, "H1 PULLBACK")
            check_and_alert_custom("NORMAL SEQ (H4-H1)", "H4", s_h4, sig_norm, vr_status_norm)

            # --- LOGIC D: SCALPER (H1-M15) ---
            sig_s, vr_status_s, bg_s = analyze_strict_cycle(df_h1, df_m15, s_h1, "M15 ENTRY")
            check_and_alert_custom("H1-M15", "H1", s_h1, sig_s, vr_status_s)

            # --- LOGIC E: HYPER SCALP (M30-M5) ---
            sig_h, vr_status_h, bg_h = analyze_strict_cycle(df_m30, df_m5, s_m30, "M5 ENTRY")
            check_and_alert_custom("M30-M5", "M30", s_m30, sig_h, vr_status_h)

            # --- RENDER UI ---
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
                with i_banner: st.markdown(f"<div style='background:{bg_intra};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_intra}</h2></div>", unsafe_allow_html=True)
                with col_i_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">H4 TREND<br>{s_h4}</div>', unsafe_allow_html=True)
                with col_i_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 ENTRY<br>{s_m30}<br><span style="font-size:12px">{vr_status_intra}</span></div>', unsafe_allow_html=True)
                with row_charts_i:
                    ig1, ig2 = st.columns(2)
                    with ig1: st.plotly_chart(plot_smart_chart(df_h4, "H4 Trend", s_h4), width="stretch")
                    with ig2: st.plotly_chart(plot_smart_chart(df_m30, "M30 Sequence", s_m30, tag=vr_status_intra), width="stretch")

            with tab_normal:
                n_banner = st.container()
                col_n_h4, col_n_h1, col_n_m30 = st.columns(3)
                n_charts = st.container()
                with n_banner: st.markdown(f"<div style='background:{bg_norm};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_norm}</h2></div>", unsafe_allow_html=True)
                with col_n_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">1. H4 TREND<br>{s_h4}</div>', unsafe_allow_html=True)
                with col_n_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">2. H1 PULLBACK<br>{s_h1}</div>', unsafe_allow_html=True)
                with col_n_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">3. M30 ENTRY<br>{s_m30}</div>', unsafe_allow_html=True)
                with n_charts:
                    ng1, ng2, ng3 = st.columns(3)
                    with ng1: st.plotly_chart(plot_smart_chart(df_h4, "Step 1: H4 Trend", s_h4), width="stretch")
                    with ng2: st.plotly_chart(plot_smart_chart(df_h1, "Step 2: H1 VR", s_h1), width="stretch")
                    with ng3: st.plotly_chart(plot_smart_chart(df_m30, "Step 3: M30 CF", s_m30), width="stretch")

            with tab_scalp:
                s_banner = st.container()
                col_s_h1, col_s_m15 = st.columns(2)
                s_charts = st.container()
                with s_banner: st.markdown(f"<div style='background:{bg_s};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_s}</h2></div>", unsafe_allow_html=True)
                with col_s_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">H1 TREND<br>{s_h1}</div>', unsafe_allow_html=True)
                with col_s_m15: st.markdown(f'<div class="metric-box {s_m15.lower()}">M15 ENTRY<br>{s_m15}<br><span style="font-size:12px">{vr_status_s}</span></div>', unsafe_allow_html=True)
                with s_charts:
                    sg1, sg2 = st.columns(2)
                    with sg1: st.plotly_chart(plot_smart_chart(df_h1, "H1 Trend", s_h1), width="stretch")
                    with sg2: st.plotly_chart(plot_smart_chart(df_m15, "M15 Sequence", s_m15, tag=vr_status_s), width="stretch")

            with tab_hyper:
                h_banner = st.container()
                col_h_m30, col_h_m5 = st.columns(2)
                h_charts = st.container()
                with h_banner: st.markdown(f"<div style='background:{bg_h};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_h}</h2></div>", unsafe_allow_html=True)
                with col_h_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 TREND<br>{s_m30}</div>', unsafe_allow_html=True)
                with col_h_m5: st.markdown(f'<div class="metric-box {s_m5.lower()}">M5 ENTRY<br>{s_m5}<br><span style="font-size:12px">{vr_status_h}</span></div>', unsafe_allow_html=True)
                with h_charts:
                    hg1, hg2 = st.columns(2)
                    with hg1: st.plotly_chart(plot_smart_chart(df_m30, "M30 Trend", s_m30), width="stretch")
                    with hg2: st.plotly_chart(plot_smart_chart(df_m5, "M5 Sequence", s_m5, tag=vr_status_h), width="stretch")

            time.sleep(refresh_rate)
            st.rerun()
        else:
            st.error(f"❌ Data Error for {symbol}. Retrying...")
            time.sleep(10)
            st.rerun()
except KeyboardInterrupt: pass
except Exception as e: st.error(f"Error: {e}")
