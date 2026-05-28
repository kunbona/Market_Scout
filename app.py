import streamlit as st
from datetime import datetime

from db.storage import (
    get_cls_news_by_source,
    get_policy_news_by_source,
    get_sector_flow_latest,
    get_lhb_data, get_zt_pool, get_dt_pool,
    get_research_reports,
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

# ─────────────────────────────────────────────────────────────────────────────
# TICKER BAR
# ─────────────────────────────────────────────────────────────────────────────
tcls = "g" if total_cls == "g" else ("r" if total_cls == "r" else "n")
p0_cls = "g" if p0 > 0 else ("r" if p0 < 0 else "n")

st.markdown(f"""
<div class="ticker-bar">
  <div class="ticker-item">
    <span class="ticker-label">涨停</span>
    <span class="ticker-val g">{len(zt)}</span>
  </div>
  <div class="ticker-item">
    <span class="ticker-label">跌停</span>
    <span class="ticker-val r">{len(dt)}</span>
  </div>
  <div class="ticker-item">
    <span class="ticker-label">连板最高</span>
    <span class="ticker-val y">{max_conn}</span>
  </div>
  <div class="ticker-item">
    <span class="ticker-label">领涨板块</span>
    <span class="ticker-val n">{top1_name}</span>
    <span class="ticker-chg {p0_cls}">{top1_pct}</span>
  </div>
  <div class="ticker-item">
    <span class="ticker-label">主力净流入</span>
    <span class="ticker-val {tcls}">{total_str}</span>
  </div>
  <div class="ticker-item">
    <span class="ticker-label">更新</span>
    <span class="ticker-val n">{now_str}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# NAVBAR
# ─────────────────────────────────────────────────────────────────────────────
_nb1, _nb2, _nb3 = st.columns([6, 1, 1])
with _nb1:
    st.markdown(f"""
    <div class="mr-nav">
      <span class="mr-logo"><div class="mr-dot"></div>Market Radar</span>
      <div class="mr-sep"></div>
      <span class="mr-kpi">涨停 <em class="g">{len(zt)}</em></span>
      <div class="mr-sep"></div>
      <span class="mr-kpi">跌停 <em class="r">{len(dt)}</em></span>
      <div class="mr-sep"></div>
      <span class="mr-kpi">连板 <em class="y">{max_conn}</em></span>
      <div class="mr-sep"></div>
      <span class="mr-kpi">净流入 <em class="{tcls}">{total_str}</em></span>
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

# ── 3. 市场数据 HTML — 3列布局 ──
data_col1 = data_col2 = data_col3 = ''

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
    data_col1 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#f97316"></span>行业资金流<span class="widget-header-count">top 10</span></div>{rows_html}</div>'

if zt:
    rows_html = ""
    for r in zt[:15]:
        code = _e(str(r.get("stock_code","")))
        name = _e(str(r.get("stock_name","")))
        cnt  = str(r.get("zt_count","1"))
        sec  = _e(str(r.get("sector","") or ""))
        rows_html += f'<div class="wr"><span class="wr-code">{code}</span><span class="wr-name">{name}</span><span class="wr-badge" style="background:#dcfce7;color:#16a34a">{cnt}板</span><span class="wr-tag">{sec}</span></div>'
    data_col2 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#22c55e"></span>涨停池<span class="widget-header-count">{len(zt)}</span></div>{rows_html}</div>'

if dt:
    rows_html = ""
    for r in dt[:10]:
        code = _e(str(r.get("stock_code","")))
        name = _e(str(r.get("stock_name","")))
        sec  = _e(str(r.get("sector","") or ""))
        rows_html += f'<div class="wr"><span class="wr-code">{code}</span><span class="wr-name">{name}</span><span class="wr-badge" style="background:#fee2e2;color:#dc2626">跌停</span><span class="wr-tag">{sec}</span></div>'
    data_col2 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#ef4444"></span>跌停池<span class="widget-header-count">{len(dt)}</span></div>{rows_html}</div>'

if lhb:
    rows_html = ""
    for r in lhb[:15]:
        code = _e(str(r.get("stock_code","")))
        name = _e(str(r.get("stock_name","")))
        nb   = float(r.get("net_buy",0) or 0)
        col_, _ = _pcolor(nb)
        sign = "+" if nb >= 0 else ""
        rows_html += f'<div class="wr"><span class="wr-code">{code}</span><span class="wr-name">{name}</span><span class="wr-val" style="color:{col_}">{sign}{nb:,.0f}万</span></div>'
    data_col3 += f'<div class="widget"><div class="widget-header"><span class="src-dot" style="background:#3b82f6"></span>龙虎榜<span class="widget-header-count">{len(lhb)}</span></div>{rows_html}</div>'

data_html = (
    f'<div class="col-wrap"><div class="col-header" style="--accent:#f59e0b"><span class="col-header-badge" style="background:#d97706;color:#fff">市场数据</span></div>'
    f'<div class="panel-3col">'
    f'<div class="panel-3col-col">{data_col1}</div>'
    f'<div class="panel-3col-col">{data_col2}</div>'
    f'<div class="panel-3col-col">{data_col3}</div>'
    f'</div></div>'
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

# ── 5. 组合成 panel tab 系统 ──
panels = [
    ("0", "财经快讯", "#ef4444", news_html),
    ("1", "政策动态", "#3b82f6", policy_html),
    ("2", "市场数据", "#d97706", data_html),
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

# 估算内容高度（财经快讯最高，按来源数估算）
_panel_height = max(len(CLS_SOURCES) * 600, 3000)
components.html(panel_full_html, height=_panel_height, scrolling=True)

