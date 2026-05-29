import streamlit as st
from datetime import datetime

from db.storage import (
    get_cls_news_by_source,
    get_policy_news_by_source,
    get_sector_flow_latest,
    get_lhb_data, get_zt_pool, get_dt_pool, get_zbgc_pool, get_strong_pool,
    get_research_reports,
    get_market_emotion_summary,
    get_lianzban_stats,
    get_sector_zt_density,
    get_concept_zt_density,
    get_sector_flow_accel,
    get_volume_breakout,
    get_lianzban_chain,
    get_research_activity,
    get_call_auction_stats,
    get_latest_emotion_date,
    get_turnover_stats,
    get_market_cap_dist,
    get_advance_decline,
    get_concept_flow_latest,
    get_market_pulse_latest,
    get_hot_rank_up_latest,
    get_northbound_flow_latest,
    get_xq_hot_latest,
)

st.set_page_config(page_title="Market Radar", layout="wide", initial_sidebar_state="collapsed")


if "scheduler_started" not in st.session_state:
    from scheduler import start_scheduler
    start_scheduler()
    st.session_state["scheduler_started"] = True

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
#MainMenu, footer, header, .stDeployButton { display: none !important; }
html, body, [class*="css"] { font-family: 'Inter', system-ui, sans-serif; }

/* ── RESET BROWSER LINK DEFAULTS ── */
a { color: inherit !important; text-decoration: none !important; }
a:visited { color: inherit !important; }
a:hover { text-decoration: none !important; }

/* ── PAGE ── */
.stApp { background: #f0f2f5 !important; }
.main .block-container { padding: 0 0 60px !important; max-width: 100% !important; }
div[data-testid="column"] { padding: 0 !important; }

/* ── TICKER BAR ── */
.ticker-bar {
  background: #fff;
  border: 1px solid #e2e4ea;
  border-radius: 12px;
  box-shadow: 0 1px 6px rgba(0,0,0,.08);
  margin: 8px 8px 4px;
  height: 30px;
  display: flex; align-items: center;
  padding: 0 14px; gap: 0; overflow: hidden;
}
.ticker-item {
  display: flex; align-items: center; gap: 6px;
  padding: 0 14px; border-right: 1px solid #e8eaef;
  white-space: nowrap; height: 100%;
}
.ticker-item:last-child { margin-left: auto; border-right: none; border-left: 1px solid #e8eaef; }
.ticker-label { font-size: 10px; color: #9ca3af; font-weight: 500; }
.ticker-val { font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600; }
.ticker-val.g { color: #16a34a; }
.ticker-val.r { color: #dc2626; }
.ticker-val.n { color: #6b7280; }
.ticker-val.y { color: #d97706; }
.ticker-chg { font-family: 'JetBrains Mono', monospace; font-size: 9.5px; }
.ticker-chg.g { color: #16a34a99; }
.ticker-chg.r { color: #dc262699; }

/* ── NAVBAR ── */
.mr-nav {
  background: #fff;
  border: 1px solid #e2e4ea;
  border-radius: 12px;
  box-shadow: 0 1px 6px rgba(0,0,0,.08);
  margin: 0 8px 8px;
  height: 44px;
  display: flex; align-items: center;
  padding: 0 18px; gap: 0;
  position: sticky; top: 8px; z-index: 300;
}
.mr-logo {
  font-size: 13px; font-weight: 700; color: #111827;
  letter-spacing: .02em; margin-right: 22px;
  display: flex; align-items: center; gap: 8px;
}
.mr-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #16a34a; box-shadow: 0 0 8px #16a34a66;
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
.mr-sep { width: 1px; height: 16px; background: #e2e4ea; margin: 0 13px; }
.mr-kpi { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #9ca3af; font-weight: 500; }
.mr-kpi em { font-style: normal; font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 600; }
.mr-kpi em.g { color: #16a34a; }
.mr-kpi em.r { color: #dc2626; }
.mr-kpi em.y { color: #d97706; }
.mr-spacer { flex: 1; }
.mr-live {
  display: flex; align-items: center; gap: 5px;
  font-size: 9px; font-weight: 700; color: #16a34a;
  letter-spacing: .14em; font-family: 'JetBrains Mono', monospace;
}
.mr-time { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #9ca3af; margin-left: 12px; }

/* ── COLUMN WRAP — outer container per column ── */
.col-wrap {
  background: #f0f2f5;
  border-radius: 12px;
  border: 1px solid #e2e4ea;
  box-shadow: 0 2px 8px rgba(0,0,0,.07);
  margin: 8px 4px;
  overflow: visible;
  padding-bottom: 8px;
}
/* ── 财经快讯2列来源布局 ── */
.news-2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
}
.news-2col-left, .news-2col-right {
  min-width: 0;
}
/* ── 研报 tab 纯CSS切换 ── */
.rc-tab-input { display: none; }
.rc-tabs {
  display: flex; gap: 6px;
  padding: 8px 8px 0;
}
.rc-tab-label {
  flex: 1; text-align: center;
  padding: 5px 0; border-radius: 20px;
  font-size: 11px; font-weight: 600; letter-spacing: .04em;
  cursor: pointer;
  border: 1px solid #e2e4ea; color: #9ca3af;
  background: #fff; transition: all .15s;
  user-select: none;
}
.rc-tab-input:checked + .rc-tab-label {
  background: #111827; color: #fff; border-color: #111827;
}
.rc-tab-content { display: none; padding-top: 8px; }
#rc-tab-0:checked ~ .rc-tab-contents .rc-tab-content[data-tab="0"] { display: block; }
#rc-tab-1:checked ~ .rc-tab-contents .rc-tab-content[data-tab="1"] { display: block; }
#rc-tab-2:checked ~ .rc-tab-contents .rc-tab-content[data-tab="2"] { display: block; }
#rc-tab-3:checked ~ .rc-tab-contents .rc-tab-content[data-tab="3"] { display: block; }

/* ── 主面板 tab 切换 ── */
.panel-tab-bar {
  display: flex; gap: 8px;
  padding: 8px 4px 12px;
  background: transparent;
}
.panel-tab-label {
  padding: 7px 24px; border-radius: 20px;
  font-size: 12px; font-weight: 700; letter-spacing: .03em;
  cursor: pointer; border: 1px solid #e2e4ea;
  color: #6b7280; background: #fff;
  transition: all .15s; user-select: none;
}
.panel-tab-label:hover { border-color: #9ca3af; color: #111827; }
.panel-tab-active {
  background: #111827 !important; color: #fff !important; border-color: #111827 !important;
}

/* ── COLUMN HEADER ── */
.col-header {
  position: sticky; top: 0; z-index: 10;
  background: #fff;
  border-bottom: 2px solid #e2e4ea;
  padding: 10px 14px;
  display: flex; align-items: center; gap: 10px;
  border-radius: 12px 12px 0 0;
}
.col-header-badge {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: 12px; font-weight: 700;
  letter-spacing: .04em;
  padding: 4px 12px;
  border-radius: 3px;
  color: #fff;
  background: var(--accent, #ef4444);
}
.col-header-badge::before {
  content: '';
  width: 6px; height: 6px; border-radius: 50%;
  background: rgba(255,255,255,.7);
  flex-shrink: 0;
}
.col-header-sub { font-size: 9px; color: #c0c4ce; margin-left: auto; font-family: 'JetBrains Mono', monospace; }

/* ── SOURCE SECTION SEPARATOR ── */
.src-label {
  display: flex; align-items: center; gap: 8px;
  padding: 14px 12px 6px;
  background: transparent;
}
.src-dot { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; }
.src-name { font-size: 12px; font-weight: 700; color: #111827; }

/* ── SOURCE BLOCK WRAPPER ── */
.src-block {
  margin: 0 8px 14px;
  border-radius: 0;
  overflow: visible;
  background: #f0f2f5;
  box-shadow: none;
}

/* ── FEATURED ITEM ── */
.ni-featured {
  padding: 16px 16px 14px;
  background: #fff;
  border-radius: 8px;
  border-bottom: none;
  cursor: default;
  transition: transform .2s cubic-bezier(.22,.68,0,1.2), box-shadow .2s ease;
  position: relative; z-index: 0;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  margin: 8px 8px 0;
}
.ni-featured:hover {
  transform: scale(1.02);
  box-shadow: 0 6px 24px rgba(0,0,0,.12);
  z-index: 10;
}
.ni-featured-src {
  font-size: 10px; font-weight: 700; letter-spacing: .06em;
  margin-bottom: 8px;
  font-family: 'JetBrains Mono', monospace;
}
.ni-featured-title, a.ni-featured-title {
  display: block;
  font-size: 16px; font-weight: 600; color: #000 !important;
  line-height: 1.5; letter-spacing: -.02em;
  transition: color .15s; cursor: pointer;
}
.ni-featured-title:hover, a.ni-featured-title:hover { color: #2563eb !important; }
.ni-featured-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; color: #9ca3af; margin-top: 10px; letter-spacing: .04em;
}

/* ── NEWS CARD (grid item) ── */
.ni {
  padding: 12px 14px 11px;
  background: #fff;
  cursor: default;
  transition: transform .2s cubic-bezier(.22,.68,0,1.2), box-shadow .2s ease;
  position: relative; z-index: 0;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
}
.ni:hover {
  transform: scale(1.03);
  box-shadow: 0 6px 24px rgba(0,0,0,.13);
  z-index: 10;
}
.ni-time { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: #9ca3af; letter-spacing: .04em; margin-bottom: 6px; display: block; }
.ni-title, a.ni-title {
  display: block;
  font-size: 13px; font-weight: 500; color: #000 !important;
  line-height: 1.6; letter-spacing: -.01em;
  transition: color .12s; cursor: pointer;
}
.ni-title:hover, a.ni-title:hover { color: #2563eb !important; }

/* ── GRID — 2-column symmetric ── */
.ni-grid {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 8px;
  background: #f0f2f5;
  border-radius: 0 0 10px 10px;
}

/* ── POLICY CARD ── */
.pc {
  padding: 12px 14px 11px;
  background: #fff;
  cursor: default;
  border-left: 2px solid transparent;
  border-bottom: 1px solid #eef0f3;
  transition: transform .2s cubic-bezier(.22,.68,0,1.2), box-shadow .2s ease, border-left-color .15s;
  position: relative; z-index: 0;
}
.pc:last-child { border-bottom: none; border-radius: 0 0 10px 10px; }
.pc:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 18px rgba(0,0,0,.11);
  border-left-color: var(--a);
  z-index: 10;
  border-radius: 0 6px 6px 0;
}
.pc-date { font-size: 10px; color: #9ca3af; font-family: 'JetBrains Mono', monospace; letter-spacing: .04em; margin-bottom: 6px; display: block; }
.pc-title {
  display: block;
  font-size: 13px; color: #000 !important; line-height: 1.6;
  transition: color .12s; cursor: pointer;
}
.pc-title:hover { color: #2563eb !important; }

/* ── RIGHT PANE WIDGETS ── */
.widget {
  background: transparent;
  padding: 4px 0 8px;
  border-bottom: 1px solid #e8eaef;
}
.widget:last-child { border-bottom: none; }
.widget-header {
  padding: 12px 12px 6px;
  font-size: 12px; font-weight: 700; color: #111827;
  display: flex; align-items: center; gap: 8px;
  background: transparent;
  letter-spacing: 0;
  text-transform: none;
}
.widget-header-count { color: #c0c4ce; font-family: 'JetBrains Mono', monospace; margin-left: auto; }
.wr {
  display: flex; align-items: center;
  padding: 7px 12px; gap: 8px;
  font-size: 12px; cursor: default;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  margin: 0 8px 6px;
  transition: transform .2s cubic-bezier(.22,.68,0,1.2), box-shadow .2s ease;
  position: relative; z-index: 0;
}
.wr:hover { transform: scale(1.03); box-shadow: 0 6px 24px rgba(0,0,0,.13); z-index: 10; }
.wr-rank { font-family: 'JetBrains Mono', monospace; font-size: 9px; color: #c0c4ce; width: 14px; flex-shrink: 0; }
.wr-code { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: #9ca3af; width: 52px; flex-shrink: 0; }
.wr-name { color: #374151; flex: 1; font-size: 12px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wr-val { font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600; flex-shrink: 0; white-space: nowrap; }
.wr-badge { font-size: 9px; font-family: 'JetBrains Mono', monospace; font-weight: 700; padding: 2px 6px; border-radius: 3px; flex-shrink: 0; }
.wr-bar-w { width: 28px; height: 2px; background: #e8eaef; border-radius: 1px; overflow: hidden; flex-shrink: 0; }
.wr-bar { height: 100%; border-radius: 1px; }
.wr-tag { font-size: 9px; color: #c0c4ce; max-width: 60px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex-shrink: 0; }

/* ── RESEARCH CARD ── */
.rc {
  padding: 10px 14px 9px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  margin: 0 8px 6px;
  cursor: default;
  transition: transform .2s cubic-bezier(.22,.68,0,1.2), box-shadow .2s ease;
  position: relative; z-index: 0;
}
.rc:hover { transform: scale(1.03); box-shadow: 0 6px 24px rgba(0,0,0,.13); z-index: 10; }
.rc-top { display: flex; align-items: center; gap: 6px; }
.rc-org { font-size: 9px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: #9ca3af; }
.rc-title, a.rc-title {
  display: block; text-decoration: none;
  font-size: 12px; font-weight: 500; color: #000 !important;
  line-height: 1.55; margin-top: 4px;
  transition: color .12s; cursor: pointer;
}
.rc-title:hover, a.rc-title:hover { color: #2563eb !important; }
.rc-foot { display: flex; align-items: center; gap: 6px; margin-top: 5px; }
.rc-stock { font-size: 9px; color: #9ca3af; font-family: 'JetBrains Mono', monospace; }
.rc-rating { font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 3px; font-family: 'JetBrains Mono', monospace; }
.rc-date { font-size: 9px; color: #c0c4ce; font-family: 'JetBrains Mono', monospace; margin-left: auto; letter-spacing: .04em; }
.rc-aim { font-size: 9px; color: #16a34a; font-family: 'JetBrains Mono', monospace; }

/* Streamlit overrides */
.stButton > button {
  font-family: 'Inter', sans-serif !important;
  background: #fff !important; color: #6b7280 !important;
  border: 1px solid #e2e4ea !important; border-radius: 20px !important;
  font-size: 10px !important; font-weight: 600 !important;
  padding: 3px 12px !important; min-height: 0 !important;
  letter-spacing: .04em !important; transition: all .15s !important;
}
.stButton > button:hover { color: #374151 !important; border-color: #c0c4ce !important; background: #f4f5f7 !important; }
.stCheckbox label p { font-size: 10px !important; color: #9ca3af !important; }
div[data-testid="stRadio"] { margin-bottom: 0 !important; }
div[data-testid="stRadio"] label p { font-size: 10px !important; color: #6b7280 !important; }
div[data-testid="stRadio"] label { padding: 0 6px 0 0 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _cls(s, n=30): return get_cls_news_by_source(s, n)
@st.cache_data(ttl=60)
def _pol(s, n=20): return get_policy_news_by_source(s, n)
@st.cache_data(ttl=60)
def _flows(t="industry"): return get_sector_flow_latest(t)
@st.cache_data(ttl=60)
def _zt(): return get_zt_pool()
@st.cache_data(ttl=60)
def _dt(): return get_dt_pool()
@st.cache_data(ttl=60)
def _lhb(): return get_lhb_data()
@st.cache_data(ttl=300)
def _research(qtype=None, n=40, today_only=False): return get_research_reports(qtype=qtype, limit=n, today_only=today_only)

@st.cache_data(ttl=300)
def _emotion_summary(): return get_market_emotion_summary()

@st.cache_data(ttl=300)
def _lianzban_stats(days=30): return get_lianzban_stats(days)

@st.cache_data(ttl=300)
def _sector_zt_density(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_sector_zt_density(d)

@st.cache_data(ttl=300)
def _concept_zt_density(date=None, top_n=15):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_concept_zt_density(d, top_n)

@st.cache_data(ttl=300)
def _sector_flow_accel(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_sector_flow_accel(d)

@st.cache_data(ttl=300)
def _call_auction(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_call_auction_stats(d)

@st.cache_data(ttl=300)
def _turnover_stats(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_turnover_stats(d)

@st.cache_data(ttl=300)
def _market_cap_dist(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_market_cap_dist(d)

@st.cache_data(ttl=300)
def _advance_decline(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_advance_decline(d)

@st.cache_data(ttl=60)
def _concept_flow_latest(top_n=30):
    return get_concept_flow_latest(top_n)

@st.cache_data(ttl=30)
def _market_pulse():
    rows = get_market_pulse_latest(n=1)
    return rows[0] if rows else {}

@st.cache_data(ttl=300)
def _zbgc(): return get_zbgc_pool()

@st.cache_data(ttl=300)
def _strong(): return get_strong_pool()

@st.cache_data(ttl=300)
def _hot_rank_up():
    return get_hot_rank_up_latest(20)

@st.cache_data(ttl=60)
def _northbound():
    return get_northbound_flow_latest()

@st.cache_data(ttl=3600)
def _xq_hot():
    return get_xq_hot_latest(25)

@st.cache_data(ttl=300)
def _lianzban_chain(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_lianzban_chain(d)

@st.cache_data(ttl=300)
def _volume_breakout(date=None):
    from datetime import datetime
    d = date or datetime.now().strftime("%Y-%m-%d")
    return get_volume_breakout(d)

def _t(s):
    s = (s or "").strip()
    if len(s) >= 16: return f"{s[5:10]} {s[11:16]}"
    if len(s) >= 10: return s[5:10]
    return "—"

def _d(s):
    s = (s or "").strip()
    if len(s) >= 16: return f"{s[5:10]} {s[11:16]}"
    if len(s) >= 10: return s[5:10]
    return "—"

def _e(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _pcolor(v):
    try:
        f = float(v)
        if f > 0: return "#22c55e", "g"
        if f < 0: return "#f43f5e", "r"
    except: pass
    return "#5a6075", "n"

# ─────────────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────────────
zt = _zt(); dt = _dt()
max_conn = max((r["zt_count"] for r in zt), default=0)
flows = _flows("industry")
lhb = _lhb()
zbgc   = _zbgc()
strong = _strong()
hot_up  = _hot_rank_up()
nb_flow = _northbound()
xq_hot  = _xq_hot()
now_str = datetime.now().strftime("%H:%M")

top1_name, top1_pct, total_str, total_cls, p0 = "—", "—", "—", "n", 0
if flows:
    r0 = flows[0]
    p0 = float(r0.get("change_pct", 0) or 0)
    top1_name = _e(str(r0.get("sector_name", "—")))
    top1_pct  = f"{p0:+.2f}%"
    total     = sum(float(r.get("main_inflow", 0) or 0) for r in flows)
    _, total_cls = _pcolor(total)
    total_str = f"{total/10000:.1f}亿" if abs(total) >= 10000 else f"{total:.0f}万"

tcls = "g" if total_cls == "g" else ("r" if total_cls == "r" else "n")
p0_cls = "g" if p0 > 0 else ("r" if p0 < 0 else "n")

# ─────────────────────────────────────────────────────────────────────────────
# NAVBAR
# ─────────────────────────────────────────────────────────────────────────────
_nb1, _nb2, _nb3 = st.columns([6, 1, 1])
with _nb1:
    st.markdown(f"""
    <div class="mr-nav">
      <span class="mr-logo"><div class="mr-dot"></div>Market Radar</span>
      <div class="mr-spacer"></div>
      <div class="mr-live"><div class="mr-dot" style="width:5px;height:5px;margin:0"></div>LIVE</div>
      <span class="mr-time">{now_str}</span>
    </div>
    """, unsafe_allow_html=True)
with _nb2:
    if st.button("↺ 刷新"):
        st.cache_data.clear(); st.rerun()
with _nb3:
    if st.checkbox("自动60s"):
        st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SOURCES CONFIG
# ─────────────────────────────────────────────────────────────────────────────
_CLS_ALL = [
    ("财联社",    "#ef4444"),
    ("金十数据",  "#f59e0b"),
    ("格隆汇",    "#06b6d4"),
    ("东方财富",  "#f97316"),
    ("同花顺",    "#22c55e"),
    ("第一财经",  "#3b82f6"),
    ("华尔街见闻","#a78bfa"),
]
_POL_ALL = [
    ("巨潮公告",  "#0ea5e9"),
    ("财新",      "#a78bfa"),
    ("发改委",    "#f97316"),
    ("证监会",    "#ef4444"),
    ("上交所问询","#3b82f6"),
    ("深交所问询","#06b6d4"),
    ("深交所公告","#22c55e"),
]
CLS_SOURCES    = [(s[0], s[1], s[0][:3]) for s in _CLS_ALL]
POLICY_SOURCES = [(s[0], s[1], s[0][:3]) for s in _POL_ALL]
QTYPE_LABELS = {0: "个股", 1: "行业", 2: "宏观", 3: "策略"}
QTYPE_COLORS = {0: "#3b82f6", 1: "#22c55e", 2: "#f97316", 3: "#a78bfa"}

# ═══════════════════════════════════════════════════════════════════════════
# MAIN PANEL — 四大模块可切换 panel
# ═══════════════════════════════════════════════════════════════════════════

# ── 1. 财经快讯 HTML ──
news_left = ''
news_cols = ['', '', '']
for src_idx, (src, accent, abbr) in enumerate(CLS_SOURCES):
    items = _cls(src, 9)
    if not items:
        continue
    fi = items[0]
    ft = _t(fi.get("pub_time","") or fi.get("created_at",""))
    flink = fi.get("link","")
    ftitle = _e(str(fi.get("title","")))
    title_a = f'<a href="{flink}" target="_blank" class="ni-featured-title">{ftitle}</a>' if flink else f'<span class="ni-featured-title">{ftitle}</span>'
    grid_html = '<div class="ni-grid">'
    for item in items[1:9]:
        t_ = _t(item.get("pub_time","") or item.get("created_at",""))
        title = _e(str(item.get("title","")))
        link = item.get("link","")
        title_tag = f'<a href="{link}" target="_blank" class="ni-title">{title}</a>' if link else f'<span class="ni-title">{title}</span>'
        grid_html += f'<div class="ni"><span class="ni-time">{t_}</span>{title_tag}</div>'
    grid_html += '</div>'
    src_block = f'<div class="src-label"><span class="src-dot" style="background:{accent}"></span><span class="src-name">{src}</span></div><div class="src-block"><div class="ni-featured"><div class="ni-featured-src" style="color:{accent}">{src}</div>{title_a}<div class="ni-featured-time">{ft}</div></div>{grid_html}</div>'
    news_cols[src_idx % 3] += src_block

news_html = (
    f'<div class="col-wrap">'
    f'<div class="col-header" style="--accent:#ef4444"><span class="col-header-badge" style="background:#ef4444">财经快讯</span><span class="col-header-sub">{len(CLS_SOURCES)} sources</span></div>'
    f'<div class="panel-3col">'
    f'<div class="panel-3col-col">{news_cols[0]}</div>'
    f'<div class="panel-3col-col">{news_cols[1]}</div>'
    f'<div class="panel-3col-col">{news_cols[2]}</div>'
    f'</div></div>'
)

# ── 2. 政策动态 HTML ──
pol_cols = ['', '', '']
for src_idx, (src, accent, abbr) in enumerate(POLICY_SOURCES):
    items = _pol(src, 9)
    if not items:
        continue
    fi = items[0]
    fdate = _d(fi.get("pub_time","") or fi.get("created_at",""))
    flink = fi.get("link","")
    ftitle = _e(str(fi.get("title","")))
    title_a = f'<a href="{flink}" target="_blank" class="ni-featured-title">{ftitle}</a>' if flink else f'<span class="ni-featured-title">{ftitle}</span>'
    grid_html = '<div class="ni-grid">'
    for item in items[1:9]:
        title = _e(str(item.get("title","")))
        link  = item.get("link","")
        date  = _d(item.get("pub_time","") or item.get("created_at",""))
        title_tag = f'<a href="{link}" target="_blank" class="ni-title">{title}</a>' if link else f'<span class="ni-title">{title}</span>'
        grid_html += f'<div class="ni"><span class="ni-time">{date}</span>{title_tag}</div>'
    grid_html += '</div>'
    pol_cols[src_idx % 3] += f'<div class="src-label"><span class="src-dot" style="background:{accent}"></span><span class="src-name">{src}</span></div><div class="src-block"><div class="ni-featured"><div class="ni-featured-src" style="color:{accent}">{src}</div>{title_a}<div class="ni-featured-time">{fdate}</div></div>{grid_html}</div>'

policy_html = (
    f'<div class="col-wrap"><div class="col-header" style="--accent:#3b82f6"><span class="col-header-badge" style="background:#3b82f6">政策动态</span><span class="col-header-sub">{len(POLICY_SOURCES)} sources</span></div>'
    f'<div class="panel-3col">'
    f'<div class="panel-3col-col">{pol_cols[0]}</div>'
    f'<div class="panel-3col-col">{pol_cols[1]}</div>'
    f'<div class="panel-3col-col">{pol_cols[2]}</div>'
    f'</div></div>'
)

# ── 3. 市场数据 + 市场情绪 合并面板 ──

# --- 实时行情区（AKShare 实时数据）---
rt_col1 = rt_col2 = rt_col3 = ''

if flows:
    max_abs = max(abs(float(r.get("main_inflow",0) or 0)) for r in flows) or 1
    rows_html = ""
    for i, r in enumerate(flows[:10]):
        name = _e(str(r.get("sector_name","")))
        pct  = float(r.get("change_pct",0) or 0)
        infl = float(r.get("main_inflow",0) or 0)
        col_, cls_ = _pcolor(pct)
        bw = int(abs(infl)/max_abs*24)
        bc = "#16a34a66" if infl >= 0 else "#dc262666"
        rows_html += f'<div class="wr"><span class="wr-rank">{i+1}</span><span class="wr-name">{name}</span><div class="wr-bar-w"><div class="wr-bar" style="width:{bw}px;background:{bc}"></div></div><span class="wr-val" style="color:{col_}">{pct:+.1f}%</span></div>'
    rt_col1 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#f97316"></span>行业资金流<span class="widget-header-count">top 10</span></div>{rows_html}</div>'

if zt:
    rows_html = ""
    for r in zt[:15]:
        code = _e(str(r.get("stock_code","")))
        name = _e(str(r.get("stock_name","")))
        cnt  = str(r.get("zt_count","1"))
        sec  = _e(str(r.get("sector","") or ""))
        # 首次封板时间：6位字符串如 092500，取前4位格式化为 09:25
        raw_time = str(r.get("first_zt_time","") or "")
        zt_time  = f"{raw_time[:2]}:{raw_time[2:4]}" if len(raw_time) >= 4 else raw_time
        # 炸板次数：>0 时显示红色小角标
        zb_n = int(r.get("zb_count") or 0)
        zb_tag = f'<span style="color:#ef4444;font-size:10px">炸{zb_n}</span>' if zb_n > 0 else ""
        # 封板资金：单位元 → 亿元（1位小数），None 时不显示
        seal_raw = r.get("seal_amount")
        seal_tag = f'<span class="wr-val" style="color:#6b7280">{seal_raw/1e8:.1f}亿</span>' if seal_raw else ""
        rows_html += (
            f'<div class="wr">'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-badge" style="background:#dcfce7;color:#16a34a">{cnt}板</span>'
            f'<span class="wr-tag">{zt_time}</span>'
            f'{zb_tag}'
            f'{seal_tag}'
            f'<span class="wr-tag">{sec}</span>'
            f'</div>'
        )
    rt_col2 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#22c55e"></span>涨停池<span class="widget-header-count">{len(zt)}</span></div>{rows_html}</div>'

if dt:
    rows_html = ""
    for r in dt[:10]:
        code = _e(str(r.get("stock_code","")))
        name = _e(str(r.get("stock_name","")))
        sec  = _e(str(r.get("sector","") or ""))
        rows_html += f'<div class="wr"><span class="wr-code">{code}</span><span class="wr-name">{name}</span><span class="wr-badge" style="background:#fee2e2;color:#dc2626">跌停</span><span class="wr-tag">{sec}</span></div>'
    rt_col2 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#ef4444"></span>跌停池<span class="widget-header-count">{len(dt)}</span></div>{rows_html}</div>'

if zbgc:
    rows_html = ""
    for r in zbgc[:10]:
        code = _e(str(r.get("stock_code","")))
        name = _e(str(r.get("stock_name","")))
        zb_n = int(r.get("zb_count") or 0)
        raw_t = str(r.get("first_zt_time","") or "")
        t_str = f"{raw_t[:2]}:{raw_t[2:4]}" if len(raw_t) >= 4 else raw_t
        amp   = r.get("amplitude")
        amp_s = f'{float(amp):.1f}%' if amp else ""
        sec   = _e(str(r.get("sector","") or ""))
        rows_html += (
            f'<div class="wr">'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-badge" style="background:#fef3c7;color:#d97706">炸{zb_n}</span>'
            f'<span class="wr-tag">{t_str}</span>'
            f'<span class="wr-tag">{amp_s}</span>'
            f'<span class="wr-tag">{sec}</span>'
            f'</div>'
        )
    rt_col2 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#f59e0b"></span>炸板池<span class="widget-header-count">{len(zbgc)}</span></div>{rows_html}</div>'

if lhb:
    rows_html = ""
    for r in lhb[:15]:
        code   = _e(str(r.get("stock_code","")))
        name   = _e(str(r.get("stock_name","")))
        nb     = float(r.get("net_buy",0) or 0)
        pct    = float(r.get("change_pct",0) or 0)
        interp = _e(str(r.get("interpret","") or "")[:12])
        col_nb, _ = _pcolor(nb)
        col_pc, _ = _pcolor(pct)
        sign   = "+" if nb >= 0 else ""
        rows_html += (
            f'<div class="wr">'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-val" style="color:{col_pc}">{pct:+.1f}%</span>'
            f'<span class="wr-tag" style="font-size:10px;color:#6b7280">{interp}</span>'
            f'<span class="wr-val" style="color:{col_nb}">{sign}{nb/1e4:,.0f}万</span>'
            f'</div>'
        )
    rt_col3 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#3b82f6"></span>龙虎榜<span class="widget-header-count">{len(lhb)}</span></div>{rows_html}</div>'

if strong:
    rows_html = ""
    for r in strong[:12]:
        code   = _e(str(r.get("stock_code","")))
        name   = _e(str(r.get("stock_name","")))
        pct    = float(r.get("change_pct") or 0)
        reason = _e(str(r.get("reason","") or ""))
        is_hi  = str(r.get("is_new_high","") or "")
        vr     = r.get("volume_ratio")
        vr_s   = f'{float(vr):.1f}x' if vr else ""
        hi_tag = '<span style="color:#ef4444;font-size:10px">新高</span>' if is_hi == "是" else ""
        col_, _ = _pcolor(pct)
        rows_html += (
            f'<div class="wr">'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-val" style="color:{col_}">{pct:+.1f}%</span>'
            f'{hi_tag}'
            f'<span class="wr-tag">{vr_s}</span>'
            f'<span class="wr-tag">{reason}</span>'
            f'</div>'
        )
    rt_col3 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#22c55e"></span>强势股<span class="widget-header-count">{len(strong)}</span></div>{rows_html}</div>'

if hot_up:
    fetch_t = hot_up[0].get("fetch_time","")[:16] if hot_up else ""
    rows_html = ""
    for r in hot_up[:15]:
        code  = _e(str(r.get("stock_code","")).replace("SZ","").replace("SH",""))
        name  = _e(str(r.get("stock_name","")))
        chg   = int(r.get("rank_change") or 0)
        rank  = int(r.get("current_rank") or 0)
        pct   = float(r.get("change_pct") or 0)
        col_, _ = _pcolor(pct)
        chg_color = "#16a34a" if chg > 0 else "#6b7280"
        rows_html += (
            f'<div class="wr">'
            f'<span class="wr-rank" style="color:{chg_color}">↑{chg}</span>'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-val" style="color:{col_}">{pct:+.1f}%</span>'
            f'<span class="wr-tag" style="color:#9ca3af">#{rank}</span>'
            f'</div>'
        )
    rt_col3 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#f97316"></span>人气飙升<span class="widget-header-count">{fetch_t}</span></div>{rows_html}</div>'

if nb_flow:
    north = [r for r in nb_flow if r.get("direction","") == "北向"]
    south = [r for r in nb_flow if r.get("direction","") == "南向"]
    rows_html = ""
    for r in north + south[:1]:  # 北向2条 + 南向合计1条
        chan   = _e(str(r.get("channel","")))
        direct = str(r.get("direction",""))
        net_b  = float(r.get("net_buy") or 0)
        net_i  = float(r.get("net_inflow") or 0)
        col_b, _ = _pcolor(net_b)
        col_i, _ = _pcolor(net_i)
        dir_color = "#3b82f6" if direct == "北向" else "#f97316"
        rows_html += (
            f'<div class="wr">'
            f'<span class="wr-name">{chan}</span>'
            f'<span class="wr-badge" style="background:{"#eff6ff" if direct=="北向" else "#fff7ed"};color:{dir_color}">{direct}</span>'
            f'<span class="wr-val" style="color:{col_b}">{net_b:+.1f}亿</span>'
            f'</div>'
        )
    rt_col3 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#3b82f6"></span>北向/南向资金<span class="widget-header-count">沪深港通</span></div>{rows_html}</div>'


cf = _concept_flow_latest(25)
if cf:
    fetch_t = cf[0].get("fetch_time","")[:16] if cf else ""
    cf_rows = ""
    max_net = max(abs(r.get("net_amount",0) or 0) for r in cf) or 1
    for i, r in enumerate(cf[:20]):
        concept   = _e(str(r.get("concept","")))
        net       = float(r.get("net_amount",0) or 0)
        change    = float(r.get("change_pct",0) or 0)
        lead      = _e(str(r.get("lead_stock","") or ""))
        lead_pct  = float(r.get("lead_pct",0) or 0)
        col_, _   = _pcolor(net)
        bw        = int(abs(net)/max_net*24)
        bc        = "#16a34a66" if net >= 0 else "#dc262666"
        sign      = "+" if net >= 0 else ""
        cf_rows  += (
            f'<div class="wr">'
            f'<span class="wr-rank">{i+1}</span>'
            f'<span class="wr-name">{concept}</span>'
            f'<div class="wr-bar-w"><div class="wr-bar" style="width:{bw}px;background:{bc}"></div></div>'
            f'<span class="wr-val" style="color:{col_}">{sign}{net:.1f}亿</span>'
            f'<span class="wr-tag" style="color:#6b7280">{lead} {lead_pct:+.1f}%</span>'
            f'</div>'
        )
    rt_col1 += (
        f'<div class="widget">'
        f'<div class="widget-header"><span class="src-dot" style="background:#8b5cf6"></span>'
        f'概念资金流<span class="widget-header-count">{fetch_t}</span></div>'
        f'{cf_rows}'
        f'</div>'
    )

if xq_hot:
    fetch_t = xq_hot[0].get("fetch_time","")[:16] if xq_hot else ""
    xq_rows = ""
    for r in xq_hot[:20]:
        rank   = int(r.get("rank") or 0)
        code   = _e(str(r.get("stock_code","")).replace("SZ","").replace("SH",""))
        name   = _e(str(r.get("stock_name","") or ""))
        follow = r.get("follow_cnt")
        fol_s  = f'{int(follow/1000)}k' if follow and follow >= 1000 else (str(int(follow)) if follow else "")
        xq_rows += (
            f'<div class="wr">'
            f'<span class="wr-rank">{rank}</span>'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-val" style="color:#6b7280">{fol_s}</span>'
            f'</div>'
        )
    rt_col1 += (
        f'<div class="widget">'
        f'<div class="widget-header"><span class="src-dot" style="background:#22c55e"></span>'
        f'雪球关注热度<span class="widget-header-count">{fetch_t}</span></div>'
        f'{xq_rows}'
        f'</div>'
    )

realtime_section = (
    '<div style="border-bottom:1px solid #e2e4ea;margin-bottom:0">'
    '<div style="font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9ca3af;padding:10px 14px 6px">实时行情</div>'
    '<div class="panel-3col" style="padding:0 0 12px">'
    f'<div class="panel-3col-col">{rt_col1}</div>'
    f'<div class="panel-3col-col">{rt_col2}</div>'
    f'<div class="panel-3col-col">{rt_col3}</div>'
    '</div></div>'
)

# ── 4. 研究报告 HTML ──
RATING_COLORS = {"买入": "#16a34a", "增持": "#4ade80", "中性": "#d97706", "减持": "#f87171", "卖出": "#dc2626"}

def _build_rc_html(reports):
    rc = ""
    for r in reports:
        title   = _e(str(r.get("title", "")))
        org     = _e(str(r.get("org_name", "") or ""))
        date    = str(r.get("publish_date", "") or "")[:10]
        rating  = str(r.get("rating", "") or "")
        aim     = str(r.get("aim_price", "") or "")
        scode   = str(r.get("stock_code", "") or "")
        sname   = _e(str(r.get("stock_name", "") or ""))
        url     = str(r.get("report_url", "") or "")
        r_color = RATING_COLORS.get(rating, "#3d4259")
        r_bg    = r_color + "18"
        rating_tag = f'<span class="rc-rating" style="color:{r_color};background:{r_bg}">{rating}</span>' if rating else ""
        stock_tag  = f'<span class="rc-stock">{scode} {sname}</span>' if scode else ""
        aim_tag    = f'<span class="rc-aim">→ {aim}</span>' if aim else ""
        title_tag  = f'<a href="{url}" target="_blank" class="rc-title">{title}</a>' if url else f'<span class="rc-title">{title}</span>'
        rc += f'<div class="rc"><div class="rc-top"><span class="rc-org">{org}</span>{rating_tag}<span class="rc-date">{date}</span></div>{title_tag}<div class="rc-foot">{stock_tag}<span style="flex:1"></span>{aim_tag}</div></div>'
    return rc if rc else '<div style="padding:16px;font-size:12px;color:#9ca3af">暂无今日研报</div>'

def _build_rc_3col(reports):
    if not reports:
        return '<div style="padding:16px;font-size:12px;color:#9ca3af">暂无今日研报</div>'
    cols = ['', '', '']
    for i, r in enumerate(reports):
        title   = _e(str(r.get("title", "")))
        org     = _e(str(r.get("org_name", "") or ""))
        date    = str(r.get("publish_date", "") or "")[:10]
        rating  = str(r.get("rating", "") or "")
        aim     = str(r.get("aim_price", "") or "")
        scode   = str(r.get("stock_code", "") or "")
        sname   = _e(str(r.get("stock_name", "") or ""))
        url     = str(r.get("report_url", "") or "")
        r_color = RATING_COLORS.get(rating, "#3d4259")
        r_bg    = r_color + "18"
        rating_tag = f'<span class="rc-rating" style="color:{r_color};background:{r_bg}">{rating}</span>' if rating else ""
        stock_tag  = f'<span class="rc-stock">{scode} {sname}</span>' if scode else ""
        aim_tag    = f'<span class="rc-aim">→ {aim}</span>' if aim else ""
        title_tag  = f'<a href="{url}" target="_blank" class="rc-title">{title}</a>' if url else f'<span class="rc-title">{title}</span>'
        cols[i % 3] += f'<div class="rc"><div class="rc-top"><span class="rc-org">{org}</span>{rating_tag}<span class="rc-date">{date}</span></div>{title_tag}<div class="rc-foot">{stock_tag}<span style="flex:1"></span>{aim_tag}</div></div>'
    return f'<div class="rc-3col"><div>{cols[0]}</div><div>{cols[1]}</div><div>{cols[2]}</div></div>'

QTYPE_DOT_COLORS = {0: "#3b82f6", 1: "#22c55e", 2: "#f97316", 3: "#a78bfa"}
rc_sections = ''
for qt, ql in QTYPE_LABELS.items():
    reports = _research(qtype=qt, n=30, today_only=True)
    if not reports:
        continue
    dot_color = QTYPE_DOT_COLORS[qt]
    rc_sections += f'<div class="src-label"><span class="src-dot" style="background:{dot_color}"></span><span class="src-name">{ql}</span></div>'
    rc_sections += _build_rc_3col(reports)

research_html = f'<div class="col-wrap"><div class="col-header" style="--accent:#8b5cf6"><span class="col-header-badge" style="background:#8b5cf6">研究报告</span></div>{rc_sections}</div>'

# ── 5. 市场情绪区（本地量价数据）+ 组合成 combined_data_html ──
try:
    # 收集数据
    # 用本地 parquet 数据的最新交易日（而非 DB 中的日期，两者可能不一致）
    try:
        from quant.loader import get_latest_trade_date as _get_ltd
        _latest_date = _get_ltd()
    except Exception:
        _latest_date = get_latest_emotion_date()
    _em = _emotion_summary()
    _lb_stats = _lianzban_stats(30)   # 30天历史
    _szt = _sector_zt_density(_latest_date) if _latest_date else []
    _czt = _concept_zt_density(_latest_date, 15) if _latest_date else []
    _sfa = _sector_flow_accel(_latest_date) if _latest_date else []
    _to_stats = _turnover_stats(_latest_date) if _latest_date else {}
    _mc_dist = _market_cap_dist(_latest_date) if _latest_date else {}
    _ad = _advance_decline(_latest_date) if _latest_date else {}
    _pulse = _market_pulse()
    _lc = _lianzban_chain(_latest_date) if _latest_date else []    # 连板链条明细
    _vb = _volume_breakout(_latest_date) if _latest_date else []   # 成交额异动

    # ── KPI 卡片行（8卡：原4 + 涨/跌家数、成交额/MA20、换手中位、市值偏好）──
    zt_total = _em.get("zt_total", "—")
    dt_total = _em.get("dt_total", "—")
    max_lb = min(_em.get("max_lianzban", 0) or 0, 30)
    zb_rate = _em.get("zb_rate", None)
    premium = _em.get("zt_yesterday_premium", None)
    tier1 = _em.get("tier_1", "—")
    tier2 = _em.get("tier_2", "—")
    tier3 = _em.get("tier_3", "—")
    tier4p = _em.get("tier_4plus", "—")
    adv_12 = _em.get("advance_1to2", None)

    zb_rate_str = f"{zb_rate:.1%}" if zb_rate is not None else "—"
    premium_str = f"{premium:+.2f}%" if premium is not None else "—"
    premium_color = "#16a34a" if (premium or 0) > 0 else ("#ef4444" if (premium or 0) < 0 else "#6b7280")
    adv_str = f"{adv_12:.1%}" if adv_12 is not None else "—"
    adv_color = "#16a34a" if (adv_12 or 0) > 0.5 else ("#f59e0b" if (adv_12 or 0) > 0.3 else "#ef4444")
    _em_date_str = f"数据截至 {_latest_date}" if _latest_date else "暂无计算数据"

    # 新 KPI：涨/跌家数
    _adv_cnt = _ad.get("advance_count", "—")
    _dec_cnt = _ad.get("decline_count", "—")
    _ad_ratio = _ad.get("ad_ratio", None)
    _ad_ratio_str = f"{_ad_ratio:.2f}" if _ad_ratio is not None else "—"
    _ad_color = "#16a34a" if (_ad_ratio or 0) >= 1.5 else ("#f59e0b" if (_ad_ratio or 0) >= 0.8 else "#ef4444")

    # 新 KPI：成交额 vs MA20
    _tot_amt = _ad.get("total_amount", None)
    _amt_ratio = _ad.get("amount_ratio", None)
    _tot_amt_str = f"{_tot_amt:.0f}亿" if _tot_amt else "—"
    _amt_ratio_str = f"{_amt_ratio:.2f}x" if _amt_ratio is not None else "—"
    _amt_color = "#16a34a" if (_amt_ratio or 0) >= 1.2 else ("#f59e0b" if (_amt_ratio or 0) >= 0.8 else "#ef4444")

    # 新 KPI：涨停换手中位
    _to_med = _to_stats.get("median_to", None)
    _to_high = _to_stats.get("high_count", 0) or 0
    _to_mid_v = _to_stats.get("mid_count", 0) or 0
    _to_low = _to_stats.get("low_count", 0) or 0
    _to_total = _to_high + _to_mid_v + _to_low
    _to_med_str = f"{_to_med:.1f}%" if _to_med is not None else "—"
    _to_high_pct = f"{_to_high/_to_total:.0%}" if _to_total > 0 else "—"

    # 新 KPI：涨停市值偏好
    _mc_mid = _mc_dist.get("mid_count", 0) or 0
    _mc_small = _mc_dist.get("small_count", 0) or 0
    _mc_large = _mc_dist.get("large_count", 0) or 0
    _mc_total = _mc_mid + _mc_small + _mc_large
    _mc_mid_pct = f"{_mc_mid/_mc_total:.0%}" if _mc_total > 0 else "—"
    _mc_small_pct = f"{_mc_small/_mc_total:.0%}" if _mc_total > 0 else "—"

    # 乐咕活跃度 KPI
    _real_zt = _pulse.get("real_zt") if _pulse.get("real_zt") is not None else zt_total
    _real_dt = _pulse.get("real_dt") if _pulse.get("real_dt") is not None else dt_total
    _legu_adv = _pulse.get("advance", None)
    _legu_dec = _pulse.get("decline", None)
    _legu_adv_str = f"涨{_legu_adv}" if _legu_adv is not None else f"涨{_adv_cnt}"
    _legu_dec_str = f"跌{_legu_dec}" if _legu_dec is not None else f"跌{_dec_cnt}"
    _activity = _pulse.get("activity", None)
    _act_str = f"{_activity:.1f}%" if _activity is not None else "—"
    _act_color = "#16a34a" if (_activity or 0) >= 50 else ("#f59e0b" if (_activity or 0) >= 35 else "#ef4444")

    _kpi_html = f'''<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:8px">
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">真实涨停 / 真实跌停</div>
    <div style="font-size:20px;font-weight:700;color:#16a34a">{_real_zt}<span style="font-size:12px;color:#ef4444;margin-left:6px">/ {_real_dt}</span></div>
    <div style="font-size:10px;color:#9ca3af;margin-top:3px">{_legu_adv_str}家 · {_legu_dec_str}家 · 含一字板共{zt_total}/{dt_total}</div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">炸板率</div>
    <div style="font-size:20px;font-weight:700;color:#f59e0b">{zb_rate_str}</div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">昨日涨停溢价</div>
    <div style="font-size:20px;font-weight:700;color:{premium_color}">{premium_str}</div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">最高连板 / 1→2晋级率</div>
    <div style="font-size:20px;font-weight:700;color:#8b5cf6">{max_lb}板<span style="font-size:12px;color:{adv_color};margin-left:6px">{adv_str}</span></div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">活跃度（乐咕）</div>
    <div style="font-size:20px;font-weight:700;color:{_act_color}">{_act_str}</div>
    <div style="font-size:10px;color:#9ca3af;margin-top:3px">A/D {_adv_cnt}/{_dec_cnt} · 比值{_ad_ratio_str}</div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">全市场成交额 / MA20比</div>
    <div style="font-size:16px;font-weight:700;color:{_amt_color}">{_tot_amt_str}<span style="font-size:11px;color:#9ca3af;margin-left:6px">{_amt_ratio_str}</span></div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">涨停换手中位 / 高换手占比</div>
    <div style="font-size:16px;font-weight:700;color:#3b82f6">{_to_med_str}<span style="font-size:11px;color:#9ca3af;margin-left:6px">高换手{_to_high_pct}</span></div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
    <div style="font-size:10px;color:#9ca3af;margin-bottom:4px">涨停市值偏好（中盘/小盘）</div>
    <div style="font-size:16px;font-weight:700;color:#f97316">{_mc_mid_pct}<span style="font-size:11px;color:#9ca3af;margin-left:4px">/ {_mc_small_pct}</span></div>
  </div>
</div>'''

    # ── 连板梯队 ──
    _tier_inner = f'''<div class="widget">
  <div class="widget-header"><span class="src-dot" style="background:#8b5cf6"></span>连板梯队<span class="widget-header-count">今日</span></div>
  <div style="display:flex;gap:8px;padding:4px 8px 8px">
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">1板</div><div style="font-size:18px;font-weight:700;color:#111827">{tier1}</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">2板</div><div style="font-size:18px;font-weight:700;color:#3b82f6">{tier2}</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">3板</div><div style="font-size:18px;font-weight:700;color:#8b5cf6">{tier3}</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">4板+</div><div style="font-size:18px;font-weight:700;color:#ef4444">{tier4p}</div>
    </div>
  </div>
</div>'''

    # ── 行业涨停密度 ──
    _szt_rows = ""
    for row in (_szt or [])[:15]:
        density = row.get("zt_density", 0) or 0
        zt_cnt = row.get("zt_count", 0) or 0
        industry = _e(str(row.get("industry", "")))
        bar_w = int(min(density * 300, 100))
        bar_color = "#ef4444" if density >= 0.10 else ("#f59e0b" if density >= 0.05 else "#22c55e")
        _szt_rows += f'<div class="wr"><span class="wr-name">{industry}</span><div class="wr-bar-w" style="width:100px"><div class="wr-bar" style="width:{bar_w}px;background:{bar_color}"></div></div><span class="wr-val" style="color:{bar_color}">{density:.1%}</span><span class="wr-tag">{zt_cnt}只</span></div>'
    _szt_inner = f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#ef4444"></span>行业涨停密度<span class="widget-header-count">≥10%共振</span></div>{_szt_rows if _szt_rows else "<div style=padding:16px;color:#9ca3af>暂无数据</div>"}</div>'

    # ── 概念涨停热度 ──
    _czt_rows = ""
    for row in (_czt or [])[:15]:
        concept = _e(str(row.get("concept", "")))
        zt_cnt = row.get("zt_count", 0) or 0
        _czt_rows += f'<div class="wr"><span class="wr-name">{concept}</span><span class="wr-val" style="color:#8b5cf6">{zt_cnt}只</span></div>'
    _czt_inner = f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#8b5cf6"></span>概念涨停热度<span class="widget-header-count">top 15</span></div>{_czt_rows if _czt_rows else "<div style=padding:16px;color:#9ca3af>暂无数据</div>"}</div>'

    # ── 换手率分层（新增，替换到第4列）──
    _to_bar_low  = int(_to_low  / _to_total * 100) if _to_total > 0 else 0
    _to_bar_mid  = int(_to_mid_v / _to_total * 100) if _to_total > 0 else 0
    _to_bar_high = int(_to_high / _to_total * 100) if _to_total > 0 else 0
    _to_inner = f'''<div class="widget">
  <div class="widget-header"><span class="src-dot" style="background:#3b82f6"></span>涨停换手分层<span class="widget-header-count">中位{_to_med_str}</span></div>
  <div style="padding:6px 8px 8px">
    <div class="wr"><span class="wr-name" style="width:60px">低(&lt;5%)</span><div class="wr-bar-w" style="width:80px"><div class="wr-bar" style="width:{_to_bar_low}px;background:#22c55e"></div></div><span class="wr-val">{_to_low}只</span></div>
    <div class="wr"><span class="wr-name" style="width:60px">中(5-20%)</span><div class="wr-bar-w" style="width:80px"><div class="wr-bar" style="width:{_to_bar_mid}px;background:#f59e0b"></div></div><span class="wr-val">{_to_mid_v}只</span></div>
    <div class="wr"><span class="wr-name" style="width:60px">高(≥20%)</span><div class="wr-bar-w" style="width:80px"><div class="wr-bar" style="width:{_to_bar_high}px;background:#ef4444"></div></div><span class="wr-val">{_to_high}只</span></div>
  </div>
</div>''' if _to_total > 0 else '<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#3b82f6"></span>涨停换手分层</div><div style="padding:16px;color:#9ca3af;font-size:12px">暂无数据</div></div>'

    # ── 市值分布（新增，用小饼图文字替代）──
    _mc_inner = f'''<div class="widget">
  <div class="widget-header"><span class="src-dot" style="background:#f97316"></span>涨停市值分布<span class="widget-header-count">{_mc_total}只涨停</span></div>
  <div style="display:flex;gap:6px;padding:6px 8px 8px">
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">&lt;50亿</div>
      <div style="font-size:16px;font-weight:700;color:#ef4444">{_mc_small}</div>
      <div style="font-size:10px;color:#9ca3af">{_mc_small_pct}</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">50-300亿</div>
      <div style="font-size:16px;font-weight:700;color:#f97316">{_mc_mid}</div>
      <div style="font-size:10px;color:#9ca3af">{_mc_mid_pct}</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.07)">
      <div style="font-size:10px;color:#9ca3af">≥300亿</div>
      <div style="font-size:16px;font-weight:700;color:#3b82f6">{_mc_large}</div>
      <div style="font-size:10px;color:#9ca3af">{_mc_dist.get("large_pct", 0) or 0:.0%}</div>
    </div>
  </div>
</div>''' if _mc_total > 0 else '<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#f97316"></span>涨停市值分布</div><div style="padding:16px;color:#9ca3af;font-size:12px">暂无数据</div></div>'

    # ── 机构资金加速度（全宽行）──
    _sfa_rows = ""
    for row in (_sfa or [])[:10]:
        industry = _e(str(row.get("industry", "")))
        acc = row.get("acceleration", None)
        acc_str = f"{acc:.2f}x" if acc is not None else "—"
        acc_color = "#16a34a" if (acc or 0) >= 1.5 else ("#f59e0b" if (acc or 0) >= 1.0 else "#ef4444")
        _sfa_rows += f'<div class="wr"><span class="wr-name">{industry}</span><span class="wr-val" style="color:{acc_color}">{acc_str}</span></div>'
    _sfa_full = f'<div style="padding:0 0 8px"><div class="widget-header"><span class="src-dot" style="background:#f97316"></span>机构资金加速度<span class="widget-header-count">3d/20d，按行业</span></div>{_sfa_rows if _sfa_rows else "<div style=padding:16px;color:#9ca3af>暂无数据</div>"}</div>'

    # ── 连板链条明细 widget ──
    _lc_rows = ""
    for r in [x for x in (_lc or []) if (x.get("lianzban_cnt") or 0) >= 2][:15]:
        code      = _e(str(r.get("stock_code", "")).replace("sz", "").replace("sh", "").upper())
        name      = _e(str(r.get("stock_name", "") or ""))
        cnt       = int(r.get("lianzban_cnt") or 0)
        is_zb     = bool(r.get("is_zb"))
        ind       = _e(str(r.get("industry", "") or ""))
        cnt_color = "#ef4444" if cnt >= 4 else ("#3b82f6" if cnt >= 3 else "#6b7280")
        zb_tag    = '<span style="color:#ef4444;font-size:10px">炸</span>' if is_zb else ""
        _lc_rows += (
            f'<div class="wr">'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-badge" style="background:#f3f4f6;color:{cnt_color};font-weight:700">{cnt}板</span>'
            f'{zb_tag}'
            f'<span class="wr-tag">{ind}</span>'
            f'</div>'
        )
    _lc_inner = (
        f'<div class="widget">'
        f'<div class="widget-header"><span class="src-dot" style="background:#8b5cf6"></span>'
        f'连板链条<span class="widget-header-count">2板+明细</span></div>'
        f'{_lc_rows if _lc_rows else "<div style=padding:16px;color:#9ca3af;font-size:12px>暂无数据</div>"}'
        f'</div>'
    )

    # ── 成交额异动 widget ──
    _vb_rows = ""
    for r in (_vb or [])[:15]:
        code        = _e(str(r.get("stock_code", "")).replace("sz", "").replace("sh", "").upper())
        name        = _e(str(r.get("stock_name", "") or ""))
        ratio       = float(r.get("ratio_5_20") or 0)
        amt         = float(r.get("amount_5d") or 0)
        ind         = _e(str(r.get("industry", "") or ""))
        amt_yi      = amt / 1e8
        ratio_color = "#ef4444" if ratio >= 3 else ("#f59e0b" if ratio >= 2 else "#6b7280")
        _vb_rows += (
            f'<div class="wr">'
            f'<span class="wr-code">{code}</span>'
            f'<span class="wr-name">{name}</span>'
            f'<span class="wr-val" style="color:{ratio_color}">{ratio:.1f}x</span>'
            f'<span class="wr-tag">{amt_yi:.2f}亿</span>'
            f'<span class="wr-tag">{ind}</span>'
            f'</div>'
        )
    _vb_inner = (
        f'<div class="widget">'
        f'<div class="widget-header"><span class="src-dot" style="background:#f97316"></span>'
        f'成交异动<span class="widget-header-count">5d/20d放量</span></div>'
        f'{_vb_rows if _vb_rows else "<div style=padding:16px;color:#9ca3af;font-size:12px>暂无数据</div>"}'
        f'</div>'
    )

    emotion_section = (
        '<div style="border-top:1px solid #e2e4ea;padding:0">'
        '<div style="font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9ca3af;padding:12px 14px 6px">市场情绪（基于本地日线）</div>'
        + _kpi_html
        + '<div class="panel-3col" style="padding:0">'
        f'<div class="panel-3col-col">{_tier_inner}</div>'
        f'<div class="panel-3col-col">{_szt_inner}</div>'
        f'<div class="panel-3col-col">{_czt_inner}</div>'
        '</div>'
        + '<div class="panel-3col" style="padding:0">'
        f'<div class="panel-3col-col">{_to_inner}</div>'
        f'<div class="panel-3col-col">{_mc_inner}</div>'
        f'<div class="panel-3col-col">{_sfa_full}</div>'
        '</div>'
        + '<div class="panel-3col" style="padding:0">'
        f'<div class="panel-3col-col">{_lc_inner}</div>'
        f'<div class="panel-3col-col">{_vb_inner}</div>'
        f'<div class="panel-3col-col"></div>'
        '</div>'
        + '</div>'
    )

except Exception as _em_err:
    _em_date_str = "数据加载失败"
    emotion_section = (
        '<div style="border-top:1px solid #e2e4ea;padding:40px;text-align:center;color:#9ca3af;font-size:13px">'
        '数据暂时不可用，请先运行数据计算<br><br>'
        f'<span style="font-size:11px;color:#f59e0b">{_e(str(_em_err))}</span>'
        '</div>'
    )

# ── 组合成完整的市场数据面板 ──
combined_data_html = (
    '<div class="col-wrap">'
    '<div class="col-header" style="--accent:#f59e0b">'
    '<span class="col-header-badge" style="background:#d97706;color:#fff">市场数据</span>'
    f'<span class="col-header-sub">{_em_date_str}</span>'
    '</div>'
    + realtime_section
    + emotion_section
    + '</div>'
)

panels = [
    ("0", "财经快讯", "#ef4444", news_html),
    ("1", "政策动态", "#3b82f6", policy_html),
    ("2", "市场数据", "#d97706", combined_data_html),
    ("3", "研究报告", "#8b5cf6", research_html),
]

panel_bar = '<div class="panel-tab-bar">' + ''.join(
    f'<div class="panel-tab-label{"  panel-tab-active" if pid=="0" else ""}" onclick="showPanel(\'{pid}\')" id="panel-btn-{pid}">{label}</div>'
    for pid, label, color, _ in panels
) + '</div>'

panel_contents = '<div class="panel-contents" id="panel-contents">' + ''.join(
    f'<div class="panel-content" id="panel-{pid}" style="display:{"block" if pid=="0" else "none"}">{html}</div>'
    for pid, _, _, html in panels
) + '</div>'

# st.markdown 过滤 <script>，用 components.html 渲染完整 HTML+JS+CSS
import streamlit.components.v1 as components

# 内联所有必要 CSS（从主CSS中提取面板相关样式）
inline_css = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{font-family:'Inter',system-ui,sans-serif;background:#f0f2f5;}
a{color:inherit!important;text-decoration:none!important}
a:visited{color:inherit!important}
.panel-tab-bar{display:flex;gap:8px;padding:8px 4px 12px;background:transparent}
.panel-tab-label{padding:7px 24px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.03em;cursor:pointer;border:1px solid #e2e4ea;color:#6b7280;background:#fff;transition:all .15s;user-select:none}
.panel-tab-label:hover{border-color:#9ca3af;color:#111827}
.panel-tab-active{background:#111827!important;color:#fff!important;border-color:#111827!important}
.col-wrap{background:#f0f2f5;border-radius:12px;border:1px solid #e2e4ea;box-shadow:0 2px 8px rgba(0,0,0,.07);margin:8px 4px;overflow:visible;padding-bottom:8px}
.col-header{background:#fff;border-bottom:2px solid #e2e4ea;padding:10px 14px;display:flex;align-items:center;gap:10px;border-radius:12px 12px 0 0}
.col-header-badge{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:700;letter-spacing:.04em;padding:4px 12px;border-radius:3px;color:#fff}
.col-header-badge::before{content:'';width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.7);flex-shrink:0}
.col-header-sub{font-size:9px;color:#c0c4ce;margin-left:auto;font-family:'JetBrains Mono',monospace}
.panel-3col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;align-items:start}
.panel-3col-col{min-width:0}
.news-2col{display:grid;grid-template-columns:1fr 1fr;gap:0}
.news-2col-left,.news-2col-right{min-width:0}
.rc-3col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 8px;padding:8px}
.src-label{display:flex;align-items:center;gap:8px;padding:14px 12px 6px;background:transparent}
.src-dot{width:5px;height:5px;border-radius:50%;flex-shrink:0}
.src-name{font-size:12px;font-weight:700;color:#111827}
.src-block{margin:0 8px 14px;background:#f0f2f5}
.ni-featured{padding:16px 16px 14px;background:#fff;border-radius:8px;cursor:default;box-shadow:0 1px 4px rgba(0,0,0,.07);margin:8px 8px 0;transition:transform .2s cubic-bezier(.22,.68,0,1.2),box-shadow .2s ease;position:relative;z-index:0}
.ni-featured:hover{transform:scale(1.03);box-shadow:0 6px 24px rgba(0,0,0,.13);z-index:10}
.ni-featured-src{font-size:10px;font-weight:700;letter-spacing:.06em;margin-bottom:8px;font-family:'JetBrains Mono',monospace}
.ni-featured-title,.ni-featured-title a{display:block;font-size:16px;font-weight:600;color:#000!important;line-height:1.5;letter-spacing:-.02em;cursor:pointer}
.ni-featured-title:hover,.ni-featured-title a:hover{color:#2563eb!important}
.ni-featured-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:#9ca3af;margin-top:10px;letter-spacing:.04em}
.ni-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px;background:#f0f2f5;border-radius:0 0 10px 10px}
.ni{padding:12px 14px 11px;background:#fff;cursor:default;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.07);transition:transform .2s cubic-bezier(.22,.68,0,1.2),box-shadow .2s ease;position:relative;z-index:0}
.ni:hover{transform:scale(1.03);box-shadow:0 6px 24px rgba(0,0,0,.13);z-index:10}
.ni-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:#9ca3af;letter-spacing:.04em;margin-bottom:5px;display:block}
.ni-title,.ni-title a,a.ni-title{display:block;font-size:13px;font-weight:500;color:#000!important;line-height:1.6;cursor:pointer}
.ni-title:hover,.ni-title a:hover,a.ni-title:hover{color:#2563eb!important}
.widget{background:transparent;padding:4px 0 8px;border-bottom:1px solid #e8eaef}
.widget:last-child{border-bottom:none}
.widget-header{padding:12px 12px 6px;font-size:12px;font-weight:700;color:#111827;display:flex;align-items:center;gap:8px;background:transparent}
.widget-header-count{color:#c0c4ce;font-family:'JetBrains Mono',monospace;margin-left:auto}
.wr{display:flex;align-items:center;padding:7px 12px;gap:8px;font-size:12px;cursor:default;background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.07);margin:0 8px 6px;transition:transform .2s cubic-bezier(.22,.68,0,1.2),box-shadow .2s ease;position:relative;z-index:0}
.wr:hover{transform:scale(1.03);box-shadow:0 6px 24px rgba(0,0,0,.13);z-index:10}
.wr-rank{font-family:'JetBrains Mono',monospace;font-size:9px;color:#c0c4ce;width:14px;flex-shrink:0}
.wr-code{font-family:'JetBrains Mono',monospace;font-size:10px;color:#9ca3af;width:52px;flex-shrink:0}
.wr-name{color:#374151;flex:1;font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wr-val{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;flex-shrink:0;white-space:nowrap}
.wr-badge{font-size:9px;font-family:'JetBrains Mono',monospace;font-weight:700;padding:2px 6px;border-radius:3px;flex-shrink:0}
.wr-bar-w{width:28px;height:2px;background:#e8eaef;border-radius:1px;overflow:hidden;flex-shrink:0}
.wr-bar{height:100%;border-radius:1px}
.wr-tag{font-size:9px;color:#c0c4ce;max-width:60px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0}
.rc{padding:10px 14px 9px;background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.07);margin:0 8px 6px;cursor:default;transition:transform .2s cubic-bezier(.22,.68,0,1.2),box-shadow .2s ease;position:relative;z-index:0}
.rc:hover{transform:scale(1.03);box-shadow:0 6px 24px rgba(0,0,0,.13);z-index:10}
.rc-top{display:flex;align-items:center;gap:6px}
.rc-org{font-size:9px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#9ca3af}
.rc-title,a.rc-title{display:block;text-decoration:none;font-size:12px;font-weight:500;color:#000!important;line-height:1.55;margin-top:4px;cursor:pointer}
.rc-title:hover,a.rc-title:hover{color:#2563eb!important}
.rc-foot{display:flex;align-items:center;gap:6px;margin-top:5px}
.rc-stock{font-size:9px;color:#9ca3af;font-family:'JetBrains Mono',monospace}
.rc-rating{font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;font-family:'JetBrains Mono',monospace}
.rc-date{font-size:9px;color:#c0c4ce;font-family:'JetBrains Mono',monospace;margin-left:auto;letter-spacing:.04em}
.rc-aim{font-size:9px;color:#16a34a;font-family:'JetBrains Mono',monospace}
.rc-tab-input{display:none}
.rc-tabs{display:flex;gap:6px;padding:8px 8px 0}
.rc-tab-label{flex:1;text-align:center;padding:5px 0;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.04em;cursor:pointer;border:1px solid #e2e4ea;color:#9ca3af;background:#fff;transition:all .15s;user-select:none}
.rc-tab-input:checked + .rc-tab-label{background:#111827;color:#fff;border-color:#111827}
.rc-tab-content{display:none;padding-top:8px}
#rc-tab-0:checked ~ .rc-tab-contents .rc-tab-content[data-tab="0"]{display:block}
#rc-tab-1:checked ~ .rc-tab-contents .rc-tab-content[data-tab="1"]{display:block}
#rc-tab-2:checked ~ .rc-tab-contents .rc-tab-content[data-tab="2"]{display:block}
#rc-tab-3:checked ~ .rc-tab-contents .rc-tab-content[data-tab="3"]{display:block}
.pc-date{font-size:10px;color:#9ca3af;font-family:'JetBrains Mono',monospace;letter-spacing:.04em;margin-bottom:6px;display:block}
.pc-title,.pc-title a,a.pc-title{display:block;font-size:12.5px;color:#000!important;line-height:1.6;cursor:pointer}
.pc-title:hover,a.pc-title:hover{color:#2563eb!important}
.em-section-title{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9ca3af;padding:12px 14px 6px}
.em-kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:8px}
.em-kpi-card{background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.07)}
.em-kpi-label{font-size:10px;color:#9ca3af;margin-bottom:4px}
.em-kpi-val{font-size:20px;font-weight:700}
.em-divider{border-bottom:1px solid #e2e4ea;margin:0 8px}
.rt-section-label{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9ca3af;padding:10px 14px 6px}
"""

panel_full_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{inline_css}</style></head>
<body>
{panel_bar}
{panel_contents}
<script>
function showPanel(pid) {{
    document.querySelectorAll('.panel-content').forEach(function(p) {{ p.style.display = 'none'; }});
    document.querySelectorAll('.panel-tab-label').forEach(function(b) {{ b.classList.remove('panel-tab-active'); }});
    var t = document.getElementById('panel-' + pid);
    if (t) t.style.display = 'block';
    var b = document.getElementById('panel-btn-' + pid);
    if (b) b.classList.add('panel-tab-active');
}}
</script>
</body></html>"""

# 本地量价数据计算按钮
_btn_col1, _btn_col2, _btn_spacer = st.columns([1, 1, 6])
with _btn_col1:
    if st.button("⚡ 计算今日数据", key="btn_compute_today"):
        _prog = st.progress(0, text="正在初始化...")
        _status = st.empty()
        try:
            from quant.daily_compute import (
                compute_market_emotion, compute_lianzban_stats,
                compute_sector_zt_density, compute_sector_flow_acceleration,
                compute_volume_breakout, compute_chip_status,
                compute_lianzban_chain, compute_research_activity,
                compute_concept_zt_density, compute_call_auction_stats,
                compute_turnover_stats, compute_market_cap_dist,
                compute_advance_decline,
            )
            from quant.loader import get_latest_trade_date
            _td = get_latest_trade_date()
            _tasks = [
                ("市场情绪指标", compute_market_emotion),
                ("连板梯队统计", compute_lianzban_stats),
                ("板块涨停密度", compute_sector_zt_density),
                ("资金流加速度", compute_sector_flow_acceleration),
                ("成交额异动",   compute_volume_breakout),
                ("筹码状态",     compute_chip_status),
                ("连板链条",     compute_lianzban_chain),
                ("机构调研热度", compute_research_activity),
                ("概念涨停密度", compute_concept_zt_density),
                ("集合竞价委比", compute_call_auction_stats),
                ("换手率分层",   compute_turnover_stats),
                ("市值分布",     compute_market_cap_dist),
                ("市场宽度",     compute_advance_decline),
            ]
            _results = []
            for _i, (_name, _fn) in enumerate(_tasks):
                _prog.progress(_i / len(_tasks), text=f"计算中：{_name}…")
                try:
                    _fn(_td)
                    _results.append(f"✓ {_name}")
                except Exception as _e:
                    _results.append(f"✗ {_name}：{_e}")
            _prog.progress(1.0, text="完成")
            _status.success(f"计算完成（{_td}）\n" + "　".join(_results))
            st.cache_data.clear()
            import time; time.sleep(0.8)
            st.rerun()
        except Exception as _e:
            _prog.empty()
            _status.error(f"计算失败：{_e}")
with _btn_col2:
    if st.button("📅 补算近30日", key="btn_compute_history"):
        _prog = st.progress(0, text="正在读取交易日历…")
        _status = st.empty()
        try:
            from quant.daily_compute import run_daily_compute
            import pandas as pd
            _df = pd.read_parquet("/mnt/ssd_1T/runist/data/Quant_Data/factors/stock/daily/涨停相关因子.parquet")
            _dates = sorted(_df["trade_date"].astype(str).unique())[-30:]
            for _i, _d in enumerate(_dates):
                _prog.progress((_i + 1) / len(_dates), text=f"计算 {_d}（{_i+1}/{len(_dates)}）…")
                run_daily_compute(_d)
            _prog.progress(1.0, text="补算完成")
            _status.success(f"已补算 {len(_dates)} 个交易日")
            st.cache_data.clear()
            import time; time.sleep(0.8)
            st.rerun()
        except Exception as _e:
            _prog.empty()
            _status.error(f"补算失败：{_e}")

# 估算内容高度（财经快讯最高，按来源数估算）
_panel_height = max(len(CLS_SOURCES) * 600, 3000)
components.html(panel_full_html, height=_panel_height, scrolling=True)

