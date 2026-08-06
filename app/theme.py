"""
theme.py — one token-driven design system for CFO Desk's in-app views.

The whole palette lives in two dicts (DARK / LIGHT). Every CSS rule references
CSS custom properties (var(--x)), so switching the palette is just swapping the
:root block — the selectors, spacing, and layout never change. That keeps the
dark theme (the primary look) intact while making a light mode a clean add-on.

Public entry points, called once from main.py after login:

    inject_theme(mode)     — global CSS (fonts, surfaces, tabs, KPI cards,
                             dividers, inputs, dataframes, sidebar). Styles
                             native Streamlit widgets, so all tabs benefit.
    render_topbar(user)    — branded "CFO Desk" header bar.
    apply_plotly_theme(m)  — dark/light Plotly template + default, so charts
                             share the palette on a transparent canvas.
"""
from __future__ import annotations

import streamlit as st

# ── Palettes ─────────────────────────────────────────────────────────────────
# Keys map 1:1 to CSS custom properties (--<key>). Brightened text tones vs the
# first pass, for readability on both backgrounds.
DARK = {
    "ink":        "#f2f0e7",
    "ink-dim":    "#cfcabd",
    "muted":      "#b7b3a8",   # KPI micro-labels, axis text, inactive tabs
    "faint":      "#a9a69d",   # captions
    "bg":         "#0a0a0d",
    "glow":       "#17171e",   # subtle top radial highlight
    "surface":    "#121216",
    "surface-2":  "#191920",   # raised inputs / hover
    "border":     "#26262e",
    "border-2":   "#34343e",
    "scroll":     "#2c2c36",
    "gold":       "#cdad63",
    "gold-hi":    "#d9b74a",
    "good":       "#9cc06a",
    "bad":        "#d6795e",
    "metric-sub": "#d6d1c5",   # KPI sub-label — high contrast, warm
    "sel":        "rgba(205,173,99,0.26)",
}
LIGHT = {
    "ink":        "#1d1b17",
    "ink-dim":    "#45423a",
    "muted":      "#6a675d",
    "faint":      "#7b7870",
    "bg":         "#f4f2ea",
    "glow":       "#ffffff",
    "surface":    "#ffffff",
    "surface-2":  "#efece2",
    "border":     "#e5e1d5",
    "border-2":   "#d6d1c3",
    "scroll":     "#d9d4c7",
    "gold":       "#9a7823",
    "gold-hi":    "#b18a2b",
    "good":       "#4f7a2c",
    "bad":        "#b0492f",
    "metric-sub": "#57534a",
    "sel":        "rgba(154,120,35,0.18)",
}

# Named tokens re-exported for the Plotly template (dark values).
INK, INK_DIM, MUTED = DARK["ink"], DARK["ink-dim"], DARK["muted"]
GOLD, GOLD_HI, GOOD, BAD = DARK["gold"], DARK["gold-hi"], DARK["good"], DARK["bad"]
SURFACE_2, BORDER = DARK["surface-2"], DARK["border"]


def _root_vars(t: dict) -> str:
    """Build a :root block from a token dict."""
    body = "; ".join(f"--{k}: {v}" for k, v in t.items())
    return "<style>:root { " + body + "; }</style>"


# ── Global CSS body (palette-agnostic — every colour is a var) ───────────────
_CSS_BODY = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ── Base ─────────────────────────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
    background: radial-gradient(1100px 520px at 50% -12%, var(--glow) 0%, transparent 62%),
                var(--bg) !important;
}
[data-testid="stAppViewContainer"] *, [data-testid="stSidebar"] * {
    font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
}
/* The wildcard rule above clobbers Streamlit's Material Symbols icon font,
   which makes icons render as their raw ligature name (e.g.
   "keyboard_double_arrow_right"). Restore the icon font on those spans so
   they render as glyphs again. */
[data-testid="stIconMaterial"],
span.material-symbols-outlined,
span.material-symbols-rounded,
span.material-symbols-sharp {
    font-family: 'Material Symbols Rounded','Material Symbols Outlined',
                 'Material Symbols Sharp' !important;
    font-weight: normal !important;
    font-style: normal !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    white-space: nowrap !important;
    word-wrap: normal !important;
    direction: ltr !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-feature-settings: 'liga' !important;
    -webkit-font-smoothing: antialiased !important;
}
[data-testid="stAppViewContainer"], [data-testid="stSidebar"] { color: var(--ink); }
header[data-testid="stHeader"] { background: transparent !important; }
#MainMenu { visibility: hidden; }
[data-testid="stAppViewContainer"] .block-container,
[data-testid="stMainBlockContainer"] { padding-top: 1.4rem !important; max-width: 1500px; }

/* ── Branded topbar ───────────────────────────────────────────────────── */
.app-topbar {
    display:flex; align-items:flex-end; justify-content:space-between;
    gap:24px; padding: 4px 2px 16px 2px;
    border-bottom: 1px solid var(--border); margin-bottom: 22px;
}
.app-brand { display:flex; align-items:center; gap:16px; min-width:0; }
.app-brand-mark { font-size:22px; font-weight:700; letter-spacing:.5px; color:var(--ink); white-space:nowrap; line-height:1; }
.app-brand-mark span { font-weight:400; letter-spacing:.5px; color:var(--gold); margin-left:8px; }
.app-brand-rule { width:1px; height:26px; background:var(--border-2); }
.app-brand-tag { color:var(--muted); font-size:11px; letter-spacing:2.4px; text-transform:uppercase; white-space:nowrap; }
@media (max-width:640px){ .app-brand-tag, .app-brand-rule { display:none; } }

/* ── Headings & captions ──────────────────────────────────────────────── */
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 { color:var(--ink) !important; font-weight:600 !important; letter-spacing:.2px !important; }
[data-testid="stMarkdownContainer"] h4 { color:var(--ink) !important; font-weight:600 !important; font-size:1.02rem !important; letter-spacing:.3px !important; margin-bottom:.35rem !important; }
[data-testid="stMarkdownContainer"] .sec-dia { color:var(--gold) !important; }
[data-testid="stCaptionContainer"] p, [data-testid="stCaptionContainer"] { color:var(--faint) !important; letter-spacing:.2px; }

/* ── Dividers ─────────────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"] hr { border:none !important; border-top:1px solid var(--border) !important; margin:1.15rem 0 !important; opacity:1 !important; }

/* ── Tabs ─────────────────────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:30px !important; border-bottom:1px solid var(--border) !important; background:transparent !important; }
[data-testid="stTabs"] [data-baseweb="tab"] { background:transparent !important; padding:10px 0 !important; height:auto !important; }
[data-testid="stTabs"] [data-baseweb="tab"] p { color:var(--muted) !important; font-size:13.5px !important; font-weight:500 !important; letter-spacing:.4px !important; }
[data-testid="stTabs"] [data-baseweb="tab"]:hover p { color:var(--ink-dim) !important; }
[data-testid="stTabs"] [aria-selected="true"] p { color:var(--ink) !important; font-weight:600 !important; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color:var(--gold) !important; height:2px !important; }
[data-testid="stTabs"] [data-baseweb="tab-border"] { display:none !important; }

/* ── Metric → KPI card (signature element) ────────────────────────────── */
[data-testid="stMetric"] {
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:15px 17px 13px 17px; position:relative; overflow:hidden;
    transition:border-color .18s ease, transform .18s ease;
}
[data-testid="stMetric"]::before {
    content:""; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg, var(--gold), transparent 72%);
    opacity:.65; transition:opacity .18s ease;
}
[data-testid="stMetric"]:hover { border-color:var(--border-2); transform:translateY(-1px); }
[data-testid="stMetric"]:hover::before { opacity:1; }
[data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] {
    color:var(--muted) !important; font-size:10.5px !important; font-weight:600 !important;
    letter-spacing:1.4px !important; text-transform:uppercase !important;
}
[data-testid="stMetricValue"] {
    color:var(--ink) !important; font-family:'JetBrains Mono', monospace !important;
    font-size:1.62rem !important; font-weight:600 !important; letter-spacing:-.5px;
}
[data-testid="stMetricDelta"],
[data-testid="stMetricDelta"] div,
[data-testid="stMetricDelta"] p { color:var(--metric-sub) !important; font-size:.8rem !important; font-weight:500 !important; }
[data-testid="stMetricDelta"] svg { display:none; }

/* ── Custom KPI tiles (executive overview strip) — colored sub-labels ─── */
.kpi-tile {
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:15px 17px 14px 17px; position:relative; overflow:hidden; height:100%;
    transition:border-color .18s ease, transform .18s ease;
}
.kpi-tile::before {
    content:""; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg, var(--gold), transparent 72%);
    opacity:.65; transition:opacity .18s ease;
}
.kpi-tile:hover { border-color:var(--border-2); transform:translateY(-1px); }
.kpi-tile:hover::before { opacity:1; }
.kpi-tile-label {
    color:var(--muted); font-size:10.5px; font-weight:600; letter-spacing:1.4px;
    text-transform:uppercase; display:flex; align-items:center; gap:5px;
}
.kpi-tile-info { color:var(--faint); font-size:11px; cursor:help; font-style:normal; }
.kpi-tile-value {
    color:var(--ink); font-family:'JetBrains Mono', monospace; font-size:1.62rem;
    font-weight:600; letter-spacing:-.5px; margin:7px 0 4px 0;
}
.kpi-tile-sub { font-size:.8rem; font-weight:500; }
.kpi-tile-sub.neutral { color:var(--metric-sub); }
.kpi-tile-sub.good    { color:var(--good); }
.kpi-tile-sub.bad     { color:var(--bad); }

/* ── Inputs / selects ─────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"] [data-baseweb="input"],
[data-testid="stAppViewContainer"] [data-baseweb="select"] > div,
[data-testid="stAppViewContainer"] [data-baseweb="base-input"],
[data-testid="stAppViewContainer"] textarea {
    background-color:var(--surface-2) !important; border:1px solid var(--border) !important; border-radius:9px !important;
}
[data-testid="stAppViewContainer"] input, [data-testid="stAppViewContainer"] textarea { color:var(--ink) !important; }
[data-testid="stAppViewContainer"] [data-baseweb="input"]:focus-within,
[data-testid="stAppViewContainer"] [data-baseweb="select"] > div:focus-within,
[data-testid="stAppViewContainer"] [data-baseweb="base-input"]:focus-within {
    border-color:var(--gold) !important; box-shadow:0 0 0 3px var(--sel) !important;
}
[data-testid="stAppViewContainer"] [data-baseweb="radio"] div[aria-checked="true"] { background-color:var(--gold) !important; border-color:var(--gold) !important; }

/* ── Secondary buttons (FAB & login excluded) ─────────────────────────── */
[data-testid="stButton"] > button:not([title="CFO Copilot Assistant"]) {
    background:var(--surface) !important; color:var(--ink-dim) !important;
    border:1px solid var(--border) !important; border-radius:9px !important;
    font-weight:500 !important; letter-spacing:.3px !important; transition:all .16s ease !important;
}
[data-testid="stButton"] > button:not([title="CFO Copilot Assistant"]):hover {
    border-color:var(--gold) !important; color:var(--ink) !important; background:var(--surface-2) !important;
}
[data-testid="stButton"] > button[kind="primary"]:not([title="CFO Copilot Assistant"]) {
    background:var(--gold) !important; color:#14110a !important; border:0 !important; font-weight:600 !important;
}

/* ── Expander / dataframe / sidebar / alerts ──────────────────────────── */
[data-testid="stExpander"] { border:1px solid var(--border) !important; border-radius:10px !important; background:var(--surface) !important; }
[data-testid="stExpander"] summary:hover { color:var(--gold) !important; }
[data-testid="stDataFrame"] { border:1px solid var(--border) !important; border-radius:10px !important; overflow:hidden; }
[data-testid="stSidebar"] { background:var(--surface) !important; border-right:1px solid var(--border) !important; }
[data-testid="stAlert"] { border-radius:9px !important; border-width:1px !important; }

/* ── Scrollbar / selection ────────────────────────────────────────────── */
::-webkit-scrollbar { width:10px; height:10px; }
::-webkit-scrollbar-track { background:var(--bg); }
::-webkit-scrollbar-thumb { background:var(--scroll); border-radius:6px; }
::-webkit-scrollbar-thumb:hover { background:var(--gold); }
::selection { background:var(--sel); color:var(--ink); }
</style>
"""


def inject_theme(mode: str = "dark") -> None:
    """Inject the design system in the chosen palette. Call once after login."""
    tokens = LIGHT if mode == "light" else DARK
    st.markdown(_root_vars(tokens), unsafe_allow_html=True)
    st.markdown(_CSS_BODY, unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
#  BRANDED TOPBAR
# ═════════════════════════════════════════════════════════════════════════════
def render_topbar(user: dict | None = None, status_text: str = "") -> None:
    """Branded header bar. Replaces the old st.title + caption + rule."""
    st.markdown(
        """
        <div class="app-topbar">
          <div class="app-brand">
            <div class="app-brand-mark">CFO<span>Desk</span></div>
            <div class="app-brand-rule"></div>
            <div class="app-brand-tag">AI-Powered Finance Intelligence Platform</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  PLOTLY TEMPLATE
# ═════════════════════════════════════════════════════════════════════════════
def apply_plotly_theme(mode: str = "dark") -> None:
    """Register a Plotly template matching the palette and make it default.

    Fixes charts that never set a background (they'd render white on dark).
    Figures that set their own colours still win — this supplies the canvas.
    """
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
    except Exception:
        return

    t = LIGHT if mode == "light" else DARK
    grid = "rgba(0,0,0,0.07)" if mode == "light" else "rgba(255,255,255,0.05)"
    hover_bg = "#ffffff" if mode == "light" else "#1d1d24"

    tmpl = go.layout.Template()
    tmpl.layout = go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, 'Segoe UI', sans-serif", color=t["ink"], size=12),
        colorway=[t["gold"], t["good"], t["bad"], t["gold-hi"], t["muted"], t["ink-dim"]],
        title=dict(font=dict(color=t["ink"], size=13)),
        xaxis=dict(gridcolor=grid, zerolinecolor=grid,
                   linecolor=t["border"], tickfont=dict(color=t["muted"])),
        yaxis=dict(gridcolor=grid, zerolinecolor=grid,
                   linecolor=t["border"], tickfont=dict(color=t["muted"])),
        legend=dict(font=dict(color=t["ink-dim"], size=11)),
        hoverlabel=dict(bgcolor=hover_bg, bordercolor=t["gold"],
                        font=dict(color=t["ink"], size=13, family="Inter, sans-serif")),
        margin=dict(l=10, r=10, t=30, b=10),
    )
    pio.templates["cfo_theme"] = tmpl
    pio.templates.default = "cfo_theme"
