"""
Vue Système J+1 — Mansa Bank (Autonome)
Source de données : Google Sheets (Onglet: Brut)
"""

import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Vue Système J+1 — Mansa Bank", page_icon="⚙️", layout="wide")

# =============================================================================
# CONFIGURATION & VARIABLES PAR DÉFAUT
# =============================================================================
AUTO_RELOAD_MS = 300000  # 5 minutes
GOOGLE_SHEET_ID = "1ukz_C93NfaaPPyJKyNHeIzM3iQ1GwDOZemub_D_uWKA"
# Connexion directe par le nom d'onglet 'Brut'
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Brut"

# URL Kobo Form Enrôlement
KOBO_ENROLLMENT_URL = "https://kf.kobotoolbox.org/api/v2/assets/aAUn3mJ59PzY2YQAnpX42S/data.json"

# Charte Graphique Mansa Bank
T = {
    "bg": "#F6F7FB",
    "card_bg": "#FFFFFF",
    "text": "#111827",
    "muted": "#6B7280",
    "border": "#E5E7EB",
    "accent": "#C9A227",
    "primary": "#0B1E33",
    "secondary": "#1E3A5F",
    "success": "#0E9F6E",
    "danger": "#E02424"
}

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: {T['bg']}; color: {T['text']}; }}
    [data-testid="stMetric"] {{
        background: {T['card_bg']}; border: 1px solid {T['border']}; border-radius: 16px;
        padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    [data-testid="stMetricLabel"] {{ color: {T['muted']} !important; font-weight: 600; text-transform: uppercase; font-size: 0.72rem !important; }}
    [data-testid="stMetricValue"] {{ color: {T['primary']} !important; font-weight: 800; font-size: 1.4rem !important; }}
    h1, h2, h3, h4 {{ color: {T['text']} !important; font-weight: 700; }}
    .section-title {{ border-left: 5px solid {T['accent']}; padding-left: 12px; margin-top: 25px; margin-bottom: 15px; font-size: 1.1rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st_autorefresh(interval=AUTO_RELOAD_MS, key="auto_reload_sys")

# =============================================================================
# CHARGEMENT DES DONNÉES
# =============================================================================
@st.cache_data(ttl=300)
def load_sheet_brut():
    try:
        df_sheet = pd.read_csv(SHEET_URL)
        return df_sheet
    except Exception as e:
        st.error(f"Erreur de chargement de la feuille Google Sheet (Onglet 'Brut') : {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def load_enrollment_data():
    try:
        headers = {"Authorization": "Token 309d43509b55dbfa0f3a61bbccceae6cfdaebc42"}
        res = requests.get(KOBO_ENROLLMENT_URL, headers=headers, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            df = pd.DataFrame(results)
            
            code_col = next((c for c in df.columns if "code" in c.lower()), None)
            name_col = next((c for c in df.columns if "nom" in c.lower() or "prenom" in c.lower()), None)
            team_col = next((c for c in df.columns if "equipe" in c.lower() or "team" in c.lower()), None)
            role_col = next((c for c in df.columns if "role" in c.lower() or "fonction" in c.lower()), None)
            
            res_df = pd.DataFrame()
            res_df["code_parrainage"] = df[code_col].astype(str).str.strip().str.upper() if code_col else ""
            res_df["nom_prenoms"] = df[name_col] if name_col else ""
            res_df["equipe"] = df[team_col] if team_col else "Non assignée"
            res_df["role"] = df[role_col] if role_col else "Commercial"
            return res_df
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

df_raw = load_sheet_brut()
enr_df = load_enrollment_data()

if df_raw.empty:
    st.warning("⚠️ Aucune donnée disponible dans l'onglet 'Brut' de Google Sheets.")
    st.stop()

# =============================================================================
# PREPARATION ET TRAITEMENT
# =============================================================================
df = df_raw.copy()

date_col = next((c for c in df.columns if any(k in c.lower() for k in ["date", "created", "time"])), None)
code_col = next((c for c in df.columns if any(k in c.lower() for k in ["code", "parrainage", "agent"])), None)
trans_col = next((c for c in df.columns if any(k in c.lower() for k in ["transaction", "trans", "statut", "status"])), None)
wallet_col = next((c for c in df.columns if any(k in c.lower() for k in ["acquisition", "wallet", "offre", "type"])), None)
equipe_col_raw = next((c for c in df.columns if "equipe" in c.lower() or "team" in c.lower()), None)

# 1. Dates et calcul des semaines de parrainage
if date_col:
    df["date_parsed"] = pd.to_datetime(df[date_col], errors="coerce")
    df["date_only"] = df["date_parsed"].dt.date
else:
    df["date_only"] = pd.NaT

def get_parrainage_week(dt):
    if pd.isna(dt):
        return "Hors Période"
    day = dt.day
    if 15 <= day <= 23:
        return "Semaine 1 (15-23)"
    elif 24 <= day <= 30:
        return "Semaine 2 (24-30)"
    elif day >= 31 or day <= 6:
        return "Semaine 3 (31-06)"
    elif 7 <= day <= 14:
        return "Semaine 4 (07-14)"
    return "Autre Semaine"

df["semaine_parrainage"] = df["date_parsed"].apply(get_parrainage_week) if date_col else "N/A"

# 2. Code Parrainage
CODE_PATTERN = re.compile(r"^[0-9A-F]{6}$")

def extract_code(val):
    if not isinstance(val, str) or not val.strip():
        return None
    raw = val.strip().upper()
    if "60DDE0" in raw.replace("O", "0"):
        return "60DDE0"
    candidates = [p for p in re.split(r"[/\-\s]+", raw) if CODE_PATTERN.match(p)]
    return candidates[-1] if candidates else None

df["code_parrainage"] = df[code_col].apply(extract_code) if code_col else None
df["code_display"] = df["code_parrainage"].fillna("Non identifié")

# 3. Croisement Kobo
if not enr_df.empty:
    is_sup = enr_df["role"].fillna("").str.lower().str.contains("superviseur", na=False)
    commerciaux_df = enr_df[~is_sup].drop_duplicates(subset="code_parrainage", keep="last")
    codes_enrolles = set(commerciaux_df["code_parrainage"].dropna().unique())

    df = df.merge(
        commerciaux_df[["code_parrainage", "nom_prenoms", "equipe"]],
        on="code_parrainage", how="left"
    )
    if equipe_col_raw and "equipe_x" in df.columns:
        df["equipe"] = df["equipe_y"].fillna(df["equipe_x"]).fillna("Non assignée")
    else:
        df["equipe"] = df["equipe"].fillna("Non assignée")
    df["nom_prenoms"] = df["nom_prenoms"].fillna(df["code_display"])
else:
    codes_enrolles = set()
    df["equipe"] = df[equipe_col_raw] if equipe_col_raw else "Non assignée"
    df["nom_prenoms"] = df["code_display"]

# 4. Statut Transaction
if trans_col:
    df["transaction_bool"] = df[trans_col].astype(str).str.lower().str.contains("true|1|oui|success", na=False)
else:
    df["transaction_bool"] = True

# 5. Acquisition / Wallet
if wallet_col:
    df["wallet_clean"] = df[wallet_col].astype(str).str.strip().str.upper()
    df["wallet_clean"] = df["wallet_clean"].apply(
        lambda x: "Wallet 2" if "2" in x else ("Wallet 1" if "1" in x else "Autre Wallet")
    )
else:
    df["wallet_clean"] = "Wallet 1"

# =============================================================================
# BARRE LATÉRALE - FILTRES
# =============================================================================
with st.sidebar:
    st.markdown("### ⚡ Filtres Système")
    
    periode_opt = st.radio(
        "Sélectionnez la période :",
        options=["Aujourd'hui", "Cette semaine", "Semaine dernière", "Toutes les données"],
        index=0
    )

    all_teams = ["Toutes"] + sorted(list(df["equipe"].dropna().unique()))
    selected_team = st.selectbox("Filtrer par équipe :", options=all_teams)

    all_codes = ["Tous"] + sorted(list(df["code_display"].dropna().unique()))
    selected_code = st.selectbox("Filtrer par Code Parrainage :", options=all_codes)

    if st.button("🔄 Rafraîchir les données", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# =============================================================================
# FILTRAGE DYNAMIQUE
# =============================================================================
today = datetime.now(timezone.utc).date()

if periode_opt == "Aujourd'hui":
    fdf = df[df["date_only"] == today]
elif periode_opt == "Cette semaine":
    curr_week = get_parrainage_week(pd.Timestamp(today))
    fdf = df[df["semaine_parrainage"] == curr_week]
elif periode_opt == "Semaine dernière":
    prev_week_date = today - timedelta(days=7)
    prev_week = get_parrainage_week(pd.Timestamp(prev_week_date))
    fdf = df[df["semaine_parrainage"] == prev_week]
else:
    fdf = df.copy()

if selected_team != "Toutes":
    fdf = fdf[fdf["equipe"] == selected_team]

if selected_code != "Tous":
    fdf = fdf[fdf["code_display"] == selected_code]

# =============================================================================
# AFFICHAGE DASHBOARD
# =============================================================================
st.markdown("# ⚙️ Vue Système J+1 — Mansa Bank")
st.caption(f"Données réelles (Google Sheet: **Brut**) · Période : **{periode_opt}**")

codes_actifs = set(fdf["code_parrainage"].dropna().unique())
codes_matches = codes_actifs & codes_enrolles
codes_non_enrolles = codes_actifs - codes_enrolles

eff_prevu = len(codes_enrolles)
eff_deploye = len(codes_matches)
eff_non_enrolle = len(codes_non_enrolles)

top_eq = fdf["equipe"].value_counts().idxmax() if not fdf.empty else "—"
best_agent = fdf.groupby(["code_display", "nom_prenoms"]).size()
best_agent_label = "—"
if not best_agent.empty:
    (b_code, b_name), _ = best_agent.idxmax(), best_agent.max()
    best_agent_label = f"{b_name} ({b_code})"

st.markdown("<h3 class='section-title'>KPI Système de Base</h3>", unsafe_allow_html=True)
m1, m2, m3, m4 = st.columns(4)
m1.metric("AVD PRÉVU", eff_prevu)
m2.metric("AVD DÉPLOYÉ", eff_deploye)
m3.metric("AVD ACTIF ENRÔLÉ", eff_deploye)
m4.metric("AVD NON ENRÔLÉ ACTIF", eff_non_enrolle)

m5, m6, m7 = st.columns(3)
m5.metric("MEILLEURE ÉQUIPE", top_eq)
m6.metric("MEILLEUR AGENT", best_agent_label)
m7.metric("ACTIVATIONS TOTALES", len(fdf))

st.markdown("<h3 class='section-title'>Répartition par Équipe & Acquisitions (Wallet 1 / Wallet 2)</h3>", unsafe_allow_html=True)

if not fdf.empty:
    eq_group = fdf.groupby("equipe").agg(
        Total_Activations=("equipe", "count"),
        Trans_True=("transaction_bool", lambda x: x.sum()),
        Trans_False=("transaction_bool", lambda x: (~x).sum()),
        Wallet_1=("wallet_clean", lambda x: (x == "Wallet 1").sum()),
        Wallet_2=("wallet_clean", lambda x: (x == "Wallet 2").sum())
    ).reset_index()

    eq_group["% Part"] = ((eq_group["Total_Activations"] / len(fdf)) * 100).round(1).astype(str) + " %"

    col_graph, col_table = st.columns([1, 1])

    with col_graph:
        fig_w = px.bar(
            fdf, x="equipe", color="wallet_clean",
            title="Répartition des Acquisitions par Wallet",
            color_discrete_map={"Wallet 1": T["accent"], "Wallet 2": T["secondary"], "Autre Wallet": T["muted"]},
            barmode="stack"
        )
        fig_w.update_layout(paper_bgcolor=T["card_bg"], plot_bgcolor=T["card_bg"], height=330, margin=dict(t=30, b=10, l=10, r=10))
        st.plotly_chart(fig_w, use_container_width=True)

    with col_table:
        st.dataframe(
            eq_group.rename(columns={
                "equipe": "Équipe",
                "Total_Activations": "Activations",
                "Trans_True": "True",
                "Trans_False": "False"
            }),
            use_container_width=True,
            hide_index=True
        )
else:
    st.info("Aucune donnée disponible pour cette sélection.")

st.markdown("<h3 class='section-title'>Top 5 Meilleurs Agents</h3>", unsafe_allow_html=True)
top5 = (
    fdf.groupby(["code_display", "nom_prenoms"]).agg(
        Activations=("code_display", "count"),
        Validees=("transaction_bool", lambda x: x.sum())
    ).reset_index().sort_values("Activations", ascending=False).head(5)
)

medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
if not top5.empty:
    cols = st.columns(len(top5))
    for i, (_, row) in enumerate(top5.iterrows()):
        with cols[i]:
            st.markdown(
                f"""<div style="background:{T['card_bg']}; border:1px solid {T['border']}; border-radius:12px;
                padding:14px; text-align:center;">
                <div style="font-size:1.5rem;">{medals[i]}</div>
                <b>{row['nom_prenoms']}</b><br>
                <small style="color:{T['muted']}">{row['code_display']}</small><br>
                <span style="color:{T['accent']}; font-weight:800; font-size:1.2rem;">{row['Activations']} Act.</span><br>
                <small style="color:{T['success']}"><b>{row['Validees']}</b> trans. True</small>
                </div>""",
                unsafe_allow_html=True,
            )
