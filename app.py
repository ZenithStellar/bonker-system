import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import time
import warnings

# --- 1. CONFIGURATION ---
warnings.filterwarnings("ignore")
st.set_page_config(page_title="Bonker Trading System V2.2", layout="wide", page_icon="🏆")

# --- 🔐 KEYPASS SYSTEM ---
def check_password():
    """Returns `True` if the user had the correct password."""
    
    # SET YOUR PASSWORD HERE
    correct_password = st.secrets["PASSWORD"] 

    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    # Login UI
    st.markdown("<h1 style='text-align: center; color: #FFD700;'>🔒 SYSTEM LOCKED</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center;'>Please enter the access key to view the dashboard.</p>", unsafe_allow_html=True)
    
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
    st.stop()  # STOPS EXECUTION HERE IF NOT LOGGED IN

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
refresh_rate = st.sidebar.slider("Refresh Speed (s)", 5, 60, 10)
sensitivity = st.sidebar.number_input("Structure Sensitivity", min_value=2, max_value=10, value=3)

# Logout Button
if st.sidebar.button("🔒 LOCK SYSTEM"):
    st.session_state.password_correct = False
    st.rerun()

stop_btn = st.sidebar.button("🟥 STOP DATA ENGINE")

# --- 4. DATA ENGINE ---
def fetch_data(symbol):
    try:
        # Fetching 5m data to support M5 strategy. 
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
    res_list = []
    sup_list = []
    
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
        res_list.append(curr_res)
        sup_list.append(curr_sup)
        
    df['State'] = state_list
    df['Active_Res'] = res_list
    df['Active_Sup'] = sup_list
    return df, df['State'].iloc[-1]

def get_trend_start_time(df):
    """Finds the timestamp when the current trend (State) started."""
    if df.empty: return None
    current_state = df['State'].iloc[-1]
    for i in range(len(df)-1, -1, -1):
        if df['State'].iloc[i] != current_state:
            return df.index[i+1] if i+1 < len(df) else df.index[i]
    return df.index[0]

def analyze_sequence(df_child, anchor_time, current_state):
    """
    Analyzes sequence with Strict VR -> CF ordering.
    Returns: Valid(Bool), Message(Str), Setup_Tag(Str)
    """
    if anchor_time is None: return False, "No Data", None
    
    # 1. Filter Data AFTER Anchor Start
    df_slice = df_child[df_child.index >= anchor_time].copy()
    if df_slice.empty: return False, "Wait for Data", None
    
    # 2. VR Check: Find FIRST occurrence of Opposite State
    opp_state = "BEARISH" if current_state == "BULLISH" else "BULLISH"
    vr_matches = df_slice[df_slice['State'] == opp_state]
    
    if vr_matches.empty:
        return False, "⚠️ MOMENTUM (No Pullback Yet)", "❌ NO VR"

    # Get timestamp of the FIRST VR (Pullback)
    first_vr_time = vr_matches.index[0]
    
    # 3. Analyze entries happening AFTER the first VR started
    # CRITICAL: We only look for Buy Signals (CF) that happened AFTER the pullback began
    df_post_vr = df_slice[df_slice.index >= first_vr_time].copy()
    
    # Count how many distinct matching blocks occurred since the VR appeared
    df_post_vr['block_id'] = (df_post_vr['State'] != df_post_vr['State'].shift()).cumsum()
    matching_blocks = df_post_vr[df_post_vr['State'] == current_state]['block_id'].unique()
    count = len(matching_blocks)
    
    # 4. Status Determination
    is_currently_matching = (df_slice['State'].iloc[-1] == current_state)
    
    if is_currently_matching:
        # We are ACTIVE in a trade
        if count == 1:
            return True, "🎯 ENTRY: ORIGIN", "🎯 ORIGIN"
        else:
            return True, f"🚀 ENTRY: CONTINUATION ({count})", "🚀 CONTINUATION"
    else:
        # We are in PULLBACK (Waiting)
        if count == 0:
            return False, "⏳ VR ACTIVE (Waiting for ORIGIN)", "⏳ WAITING ORIGIN"
        else:
            # If count > 0, it means we HAD a signal before, but it failed.
            return False, f"⏳ VR ACTIVE (Origin Failed → Next: CONT)", "⏳ WAITING CONT."

# --- 6. PLOTTING ---
def plot_smart_chart(df, title, state, tag=None):
    df_slice = df.tail(60)
    fig = go.Figure()
    c = "#00E676" if state == "BULLISH" else "#FF5252"
    
    # Construct Title
    title_text = f"{title} ({state})"
    if tag: title_text += f"  |  {tag}"

    # CHANGED: Width set to 1 (Thinner)
    fig.add_trace(go.Scatter(x=df_slice.index, y=df_slice['Close'], mode='lines', name='Price', line=dict(color=c, width=1)))
    
    # HIDDEN: Support and Resistance lines are commented out
    # fig.add_trace(go.Scatter(x=df_slice.index, y=df_slice['Active_Res'], mode='lines', name='Res', line=dict(color='#2962FF', width=1, dash='dot')))
    # fig.add_trace(go.Scatter(x=df_slice.index, y=df_slice['Active_Sup'], mode='lines', name='Sup', line=dict(color='#FF6D00', width=1, dash='dot')))
    
    fig.update_layout(
        title=dict(text=title_text, font=dict(color="white")), 
        height=250, margin=dict(t=30,b=0,l=0,r=0), 
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", 
        xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#333"), 
        font=dict(color="white"), xaxis_rangeslider_visible=False
    )
    return fig

# --- 7. MAIN EXECUTION ---
st.title(f"🏆 BONKER TRADING SYSTEM: {symbol}")

# --- TABS ---
tab_swing, tab_intraday, tab_scalp, tab_hyper = st.tabs([
    "📊 Daily Deploy (D-H4-H1-M30)", 
    "🎯 Rich Setup (H4-M30)", 
    "⚡ Scalping Sequence (H1-M15)",
    "⚡ Hyper Scalp (M30-M5)"
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
        # FETCH 5 MIN DATA
        df_d_raw, df_5m_raw = fetch_data(symbol)
        
        if df_d_raw is not None and df_5m_raw is not None:
            
            # --- CALCULATIONS ---
            df_d, s_d = calculate_structure(df_d_raw, sensitivity)
            
            # Resample 5m raw to higher timeframes
            df_h4, s_h4 = calculate_structure(resample_data(df_5m_raw.copy(), "4h"), sensitivity)
            df_h1, s_h1 = calculate_structure(resample_data(df_5m_raw.copy(), "1h"), sensitivity)
            df_m30, s_m30 = calculate_structure(resample_data(df_5m_raw.copy(), "30m"), sensitivity)
            df_m15, s_m15 = calculate_structure(resample_data(df_5m_raw.copy(), "15m"), sensitivity)
            df_m5, s_m5 = calculate_structure(df_5m_raw.copy(), sensitivity)

            # --- LOGIC A: SWING ---
            sig_cas = "WAITING"
            bg_cas = "#37474F"
            if s_d == "BULLISH" and s_h4 == "BULLISH" and s_h1 == "BULLISH":
                if s_m30 == "BULLISH": sig_cas = "🚀 SWING BUY (Full Alignment)"; bg_cas = "#00C853"
                else: sig_cas = "⏳ TREND UP - Waiting for M30 Breakout"; bg_cas = "#FF6D00"
            elif s_d == "BEARISH" and s_h4 == "BEARISH" and s_h1 == "BEARISH":
                if s_m30 == "BEARISH": sig_cas = "🚀 SWING SELL (Full Alignment)"; bg_cas = "#D50000"
                else: sig_cas = "⏳ TREND DOWN - Waiting for M30 Breakout"; bg_cas = "#FF6D00"

            # --- LOGIC B: INTRADAY (H4 -> M30) ---
            sig_intra = "WAITING"
            bg_intra = "#37474F"
            vr_status_text = "Checking..."
            tag_intra = None
            
            h4_start_time = get_trend_start_time(df_h4)
            
            if s_h4 == "BULLISH":
                is_valid, msg, tag = analyze_sequence(df_m30, h4_start_time, "BULLISH")
                vr_status_text = msg; tag_intra = tag
                
                if is_valid:
                    if "ORIGIN" in msg: sig_intra = "🎯 INTRADAY BUY (Origin CF)"; bg_intra = "#00C853"
                    else: sig_intra = "🚀 INTRADAY BUY (Continuation CF)"; bg_intra = "#558B2F"
                elif "VR ACTIVE" in msg: sig_intra = msg; bg_intra = "#FF6D00"
                else: sig_intra = msg; bg_intra = "#37474F"

            elif s_h4 == "BEARISH":
                is_valid, msg, tag = analyze_sequence(df_m30, h4_start_time, "BEARISH")
                vr_status_text = msg; tag_intra = tag
                
                if is_valid:
                    if "ORIGIN" in msg: sig_intra = "🎯 INTRADAY SELL (Origin CF)"; bg_intra = "#D50000"
                    else: sig_intra = "🚀 INTRADAY SELL (Continuation CF)"; bg_intra = "#C62828"
                elif "VR ACTIVE" in msg: sig_intra = msg; bg_intra = "#FF6D00"
                else: sig_intra = msg; bg_intra = "#37474F"

            # --- LOGIC C: SCALPER (H1 -> M15) ---
            sig_s = "WAITING"
            bg_s = "#37474F"
            s_vr_text = ""
            tag_scalp = None
            
            h1_start_time = get_trend_start_time(df_h1)

            if s_h1 == "BULLISH":
                is_valid_s, msg_s, tag_s = analyze_sequence(df_m15, h1_start_time, "BULLISH")
                s_vr_text = msg_s; tag_scalp = tag_s
                
                if is_valid_s:
                    if "ORIGIN" in msg_s: sig_s = "⚡ SCALP BUY (Origin)"; bg_s = "#00C853"
                    else: sig_s = "⚡ SCALP BUY (Continuation)"; bg_s = "#558B2F"
                elif "VR ACTIVE" in msg_s: sig_s = msg_s; bg_s = "#FF6D00"
                else: sig_s = msg_s; bg_s = "#37474F"
            
            elif s_h1 == "BEARISH":
                is_valid_s, msg_s, tag_s = analyze_sequence(df_m15, h1_start_time, "BEARISH")
                s_vr_text = msg_s; tag_scalp = tag_s
                
                if is_valid_s:
                    if "ORIGIN" in msg_s: sig_s = "⚡ SCALP SELL (Origin)"; bg_s = "#D50000"
                    else: sig_s = "⚡ SCALP SELL (Continuation)"; bg_s = "#C62828"
                elif "VR ACTIVE" in msg_s: sig_s = msg_s; bg_s = "#FF6D00"
                else: sig_s = msg_s; bg_s = "#37474F"

            # --- LOGIC D: HYPER SCALP (M30 -> M5) ---
            sig_h = "WAITING"
            bg_h = "#37474F"
            h_vr_text = ""
            tag_hyper = None
            
            m30_start_time = get_trend_start_time(df_m30)

            if s_m30 == "BULLISH":
                is_valid_h, msg_h, tag_h = analyze_sequence(df_m5, m30_start_time, "BULLISH")
                h_vr_text = msg_h; tag_hyper = tag_h
                
                if is_valid_h:
                    if "ORIGIN" in msg_h: sig_h = "💎 HYPER BUY (Origin)"; bg_h = "#00C853"
                    else: sig_h = "🚀 HYPER BUY (Continuation)"; bg_h = "#558B2F"
                elif "VR ACTIVE" in msg_h: sig_h = msg_h; bg_h = "#FF6D00"
                else: sig_h = msg_h; bg_h = "#37474F"
            
            elif s_m30 == "BEARISH":
                is_valid_h, msg_h, tag_h = analyze_sequence(df_m5, m30_start_time, "BEARISH")
                h_vr_text = msg_h; tag_hyper = tag_h
                
                if is_valid_h:
                    if "ORIGIN" in msg_h: sig_h = "💎 HYPER SELL (Origin)"; bg_h = "#D50000"
                    else: sig_h = "🚀 HYPER SELL (Continuation)"; bg_h = "#C62828"
                elif "VR ACTIVE" in msg_h: sig_h = msg_h; bg_h = "#FF6D00"
                else: sig_h = msg_h; bg_h = "#37474F"

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
            with col_i_h4: st.markdown(f'<div class="metric-box {s_h4.lower()}">H4 SETUP<br>{s_h4}</div>', unsafe_allow_html=True)
            with col_i_m30: 
                st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 TRIGGER<br>{s_m30}<br><span style="font-size:12px">{vr_status_text}</span></div>', unsafe_allow_html=True)
            with row_charts_i:
                ig1, ig2 = st.columns(2)
                with ig1: st.plotly_chart(plot_smart_chart(df_h4, "H4 Trend", s_h4), width="stretch")
                with ig2: st.plotly_chart(plot_smart_chart(df_m30, "M30 Trigger", s_m30, tag=tag_intra), width="stretch")

            # --- RENDER SCALP ---
            with s_banner: st.markdown(f"<div style='background:{bg_s};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_s}</h2></div>", unsafe_allow_html=True)
            with col_s_h1: st.markdown(f'<div class="metric-box {s_h1.lower()}">H1 TREND<br>{s_h1}</div>', unsafe_allow_html=True)
            with col_s_m15: 
                st.markdown(f'<div class="metric-box {s_m15.lower()}">M15 ENTRY<br>{s_m15}<br><span style="font-size:12px">{s_vr_text}</span></div>', unsafe_allow_html=True)
            with s_charts:
                sg1, sg2 = st.columns(2)
                with sg1: st.plotly_chart(plot_smart_chart(df_h1, "H1 Trend", s_h1), width="stretch")
                with sg2: st.plotly_chart(plot_smart_chart(df_m15, "M15 Trigger", s_m15, tag=tag_scalp), width="stretch")

            # --- RENDER HYPER SCALP ---
            with h_banner: st.markdown(f"<div style='background:{bg_h};padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;'><h2 style='color:white;margin:0;'>{sig_h}</h2></div>", unsafe_allow_html=True)
            with col_h_m30: st.markdown(f'<div class="metric-box {s_m30.lower()}">M30 TREND<br>{s_m30}</div>', unsafe_allow_html=True)
            with col_h_m5: 
                st.markdown(f'<div class="metric-box {s_m5.lower()}">M5 ENTRY<br>{s_m5}<br><span style="font-size:12px">{h_vr_text}</span></div>', unsafe_allow_html=True)
            with h_charts:
                hg1, hg2 = st.columns(2)
                with hg1: st.plotly_chart(plot_smart_chart(df_m30, "M30 Trend", s_m30), width="stretch")
                with hg2: st.plotly_chart(plot_smart_chart(df_m5, "M5 Trigger", s_m5, tag=tag_hyper), width="stretch")

            time.sleep(refresh_rate)
            st.rerun()
        else:
            st.error(f"❌ Data Error for {symbol}. Try 'GC=F' or 'GLD'.")
            time.sleep(10)
            st.rerun()
except: pass
