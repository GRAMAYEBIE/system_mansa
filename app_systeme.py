"""
Vue Système J+1 — Mansa Bank
============================
Source des activations : Google Sheets (onglet "Brut"), alimenté automatiquement par mail.
Source de l'effectif AVD (Prévu / Déployé) : fichier Excel "base agents" DataSurvey Africa.

CONÇU POUR 100+ UTILISATEURS SIMULTANÉS :
- Toutes les requêtes réseau passent par un cache PARTAGÉ (st.cache_data) : le Sheet
  n'est retéléchargé qu'une fois toutes les BRUT_TTL secondes, peu importe le nombre
  de personnes connectées.
- En cas d'échec réseau (Google indisponible, timeout...), l'app retombe sur la
  dernière donnée valide connue (st.cache_resource) au lieu d'afficher une page vide
  ou de planter pour tout le monde.
- Le rafraîchissement automatique ne recharge QUE la zone de données (st.fragment),
  pas toute la page, pour rester fluide avec beaucoup de monde connecté.
- Toute erreur inattendue est interceptée pour afficher un message propre plutôt
  qu'un crash brut.

Dépendances : pip install streamlit pandas requests openpyxl
(streamlit-autorefresh n'est plus nécessaire : st.fragment(run_every=...) le remplace,
 il faut Streamlit >= 1.37 — sinon l'app bascule automatiquement sur un mode compatible)
"""

from datetime import datetime, timedelta, date, timezone
from io import StringIO

import pandas as pd
import requests
import streamlit as st

# ============================================================================
# CONFIGURATION — à adapter
# ============================================================================

GOOGLE_SHEET_ID = "1ukz_C93NfaaPPyJKyNHeIzM3iQ1GwDOZemub_D_uWKA"
SHEET_TAB = "Brut"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_TAB}"
BRUT_TTL = 90  # secondes — fraîcheur des activations
FETCH_TIMEOUT = 15  # secondes — au-delà, on abandonne cette tentative
FETCH_RETRIES = 3

# ⚠️ À VÉRIFIER : posez le fichier à côté de ce script et ajustez le chemin,
# OU renseignez BASE_XLSX_URL (lien direct/raw) si le fichier est hébergé en ligne.
BASE_XLSX_PATH = "BASE_DE_DONNEE_AGENTS_ACTIVATEURS_DATASURVEY_AFRICA.xlsx"
BASE_XLSX_URL = ""
BASE_TTL = 3600

# ⚠️ À VÉRIFIER : noms de colonnes réels dans le fichier de base (détection auto par mots-clés).
BASE_CODE_KEYWORDS = ["code"]
BASE_STATUT_KEYWORDS = ["statut", "status", "deploy", "déploy"]
BASE_DEPLOYED_VALUES = ["déployé", "deploye", "deployed", "oui", "actif", "true", "1"]

# --- Définition des "semaines" (cycle dynamique, ne nécessite aucune maintenance) ---
FIRST_WEEK_START = date(2026, 8, 15)
FIRST_WEEK_END = date(2026, 8, 23)
REGULAR_WEEK_START = date(2026, 8, 24)


def get_week_bounds(ref_date: date) -> tuple[date, date]:
    if ref_date < REGULAR_WEEK_START:
        return FIRST_WEEK_START, FIRST_WEEK_END
    offset_weeks = (ref_date - REGULAR_WEEK_START).days // 7
    start = REGULAR_WEEK_START + timedelta(days=offset_weeks * 7)
    return start, start + timedelta(days=6)


def get_previous_week_bounds(ref_date: date) -> tuple[date, date]:
    this_start, _ = get_week_bounds(ref_date)
    return get_week_bounds(this_start - timedelta(days=1))


# ============================================================================
# STYLE
# ============================================================================
st.set_page_config(page_title="Vue Système J+1 — Mansa Bank", page_icon="⚙️", layout="wide")

st.markdown(
    """
    <style>
    .stApp { background-color: #F6F7FB; color: #111827; }
    [data-testid="stMetric"] {
        background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px;
        padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    [data-testid="stMetricLabel"] { color: #6B7280 !important; font-weight: 600; text-transform: uppercase; font-size: 0.72rem !important; }
    [data-testid="stMetricValue"] { color: #0B1E33 !important; font-weight: 800; font-size: 1.4rem !important; }
    .team-header-box { background-color: #0A192F; color: #FFFFFF; padding: 12px 20px; border-radius: 10px 10px 0 0; font-weight: 700; font-size: 1.05rem; }
    .section-title { border-left: 5px solid #C9A227; padding-left: 12px; margin-top: 25px; margin-bottom: 15px; font-size: 1.15rem; font-weight: 700; color: #111827; }
    .stale-banner { background:#FEF3C7; border:1px solid #F59E0B; border-radius:8px; padding:8px 14px; font-size:0.85rem; margin-bottom:10px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================================
# STOCKAGE PARTAGÉ (survit aux reruns et est commun à TOUTES les sessions) —
# permet de garder la dernière donnée valide si Google Sheets répond mal.
# ============================================================================


class SharedStore:
    def __init__(self):
        self.brut_df: pd.DataFrame = pd.DataFrame()
        self.last_success: datetime | None = None
        self.last_error: str | None = None


@st.cache_resource
def get_store() -> SharedStore:
    return SharedStore()


# ============================================================================
# CHARGEMENT — ACTIVATIONS (avec retries + fallback sur dernière donnée valide)
# ============================================================================


def _fetch_csv_with_retries(url: str) -> pd.DataFrame:
    last_exc = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            return pd.read_csv(StringIO(resp.text))
        except Exception as e:  # noqa: BLE001 — on veut juste réessayer puis remonter
            last_exc = e
    raise last_exc


@st.cache_data(ttl=BRUT_TTL, show_spinner=False)
def load_and_process_brut() -> pd.DataFrame:
    store = get_store()
    try:
        raw = _fetch_csv_with_retries(SHEET_URL)
    except Exception as e:  # noqa: BLE001
        store.last_error = str(e)
        if not store.brut_df.empty:
            return store.brut_df  # on sert la dernière donnée connue plutôt que de casser la page
        return pd.DataFrame()

    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]

    df["agence"] = df.get("agence", pd.Series(dtype=str)).fillna("Non attribuée").astype(str).str.strip()
    df["code_parrainage"] = df.get("code_parrainage", pd.Series(dtype=str)).fillna("Inconnu").astype(str).str.strip().str.upper()
    df["parrain_nom"] = df.get("parrain_nom", pd.Series(dtype=str)).fillna("Non renseigné").astype(str).str.strip()

    df["date_parrainage_parsed"] = pd.to_datetime(df.get("date_parrainage"), errors="coerce", dayfirst=True)
    df["date_only"] = df["date_parrainage_parsed"].dt.date

    df["is_true"] = df.get("a_transacte", pd.Series(dtype=str)).astype(str).str.strip().str.lower().eq("true")

    wallet_raw = df.get("wallet", pd.Series(dtype=str)).astype(str).str.upper()
    df["acquisition"] = wallet_raw.apply(lambda v: "Wallet 2" if "2" in v else ("Wallet 1" if "1" in v else "Autre"))
    df["wallet1_true"] = df["is_true"] & (df["acquisition"] == "Wallet 1")
    df["wallet2_true"] = df["is_true"] & (df["acquisition"] == "Wallet 2")

    store.brut_df = df
    store.last_success = datetime.now(timezone.utc)
    store.last_error = None
    return df


# ============================================================================
# CHARGEMENT — BASE AVD (Prévu / Déployé)
# ============================================================================


def _find_col(columns, keywords):
    for c in columns:
        if any(k in str(c).lower() for k in keywords):
            return c
    return None


@st.cache_data(ttl=BASE_TTL, show_spinner=False)
def load_base_avd() -> dict:
    try:
        source = BASE_XLSX_URL if BASE_XLSX_URL else BASE_XLSX_PATH
        base_df = pd.read_excel(source)
    except Exception:
        return {"total_prevu": None, "total_deploye": None, "ok": False}

    base_df.columns = [str(c).strip() for c in base_df.columns]
    code_col = _find_col(base_df.columns, BASE_CODE_KEYWORDS)
    statut_col = _find_col(base_df.columns, BASE_STATUT_KEYWORDS)

    if code_col:
        base_df = base_df.dropna(subset=[code_col])

    total_prevu = len(base_df)
    if statut_col:
        statut_norm = base_df[statut_col].astype(str).str.strip().str.lower()
        total_deploye = int(statut_norm.isin(BASE_DEPLOYED_VALUES).sum())
    else:
        total_deploye = None

    return {"total_prevu": total_prevu, "total_deploye": total_deploye, "ok": True}


# ============================================================================
# ZONE DE DONNÉES — en fragment auto-rafraîchi (ne recharge QUE cette zone,
# pas toute la page, ce qui reste fluide même avec beaucoup de monde connecté)
# ============================================================================

_fragment_decorator = getattr(st, "fragment", None)


def _render_dashboard():
    try:
        df = load_and_process_brut()
        store = get_store()

        if df.empty:
            st.warning("⚠️ Aucune donnée disponible pour le moment. Nouvelle tentative automatique en cours...")
            return

        if store.last_error:
            st.markdown(
                f"<div class='stale-banner'>⚠️ Connexion Google Sheets instable — "
                f"affichage de la dernière donnée valide ({store.last_success:%H:%M:%S} UTC).</div>",
                unsafe_allow_html=True,
            )

        base_avd = load_base_avd()

        today_utc = datetime.now(timezone.utc).date()
        week_start, week_end = get_week_bounds(today_utc)
        prev_week_start, prev_week_end = get_previous_week_bounds(today_utc)

        with st.sidebar:
            st.markdown("### ⚡ Filtres")
            option_periode = st.radio(
                "Période (basée sur la date de parrainage) :",
                options=["Aujourd'hui", "Cette semaine", "Semaine passée", "Toutes les dates"],
                index=0,
                key="option_periode",
            )
            st.caption(f"Cette semaine : {week_start:%d/%m} → {week_end:%d/%m}")
            st.caption(f"Semaine passée : {prev_week_start:%d/%m} → {prev_week_end:%d/%m}")
            st.markdown("---")
            agences_list = ["Toutes"] + sorted(df["agence"].unique().tolist())
            selected_agence = st.selectbox("Filtrer par Agence / Équipe :", options=agences_list, key="agence_filter")
            if st.button("🔄 Forcer le rafraîchissement", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

        fdf = df
        if option_periode == "Aujourd'hui":
            fdf = fdf[fdf["date_only"] == today_utc]
        elif option_periode == "Cette semaine":
            fdf = fdf[(fdf["date_only"] >= week_start) & (fdf["date_only"] <= week_end)]
        elif option_periode == "Semaine passée":
            fdf = fdf[(fdf["date_only"] >= prev_week_start) & (fdf["date_only"] <= prev_week_end)]
        if selected_agence != "Toutes":
            fdf = fdf[fdf["agence"] == selected_agence]

        st.markdown("# ⚙️ Vue Système J+1 — Mansa Bank")
        st.caption(
            f"Filtrage actif : **{option_periode}**"
            + (f" · Agence : **{selected_agence}**" if selected_agence != "Toutes" else "")
        )

        activations_periode = int(fdf["is_true"].sum())
        avd_actifs = fdf.loc[fdf["is_true"], "code_parrainage"].nunique()

        if activations_periode > 0:
            best_agence = fdf.groupby("agence")["is_true"].sum().idxmax()
            best_agent_series = fdf.groupby(["code_parrainage", "parrain_nom"])["is_true"].sum().sort_values(ascending=False)
            best_agent = best_agent_series.index[0][1]
        else:
            best_agence, best_agent = "—", "—"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("AVD PRÉVU", base_avd["total_prevu"] if base_avd["ok"] else "—")
        c2.metric("AVD DÉPLOYÉ", base_avd["total_deploye"] if base_avd["ok"] and base_avd["total_deploye"] is not None else "—")
        c3.metric("AVD ACTIF (période)", avd_actifs)
        c4.metric("ACTIVATIONS (période)", activations_periode)

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("MEILLEURE ÉQUIPE", best_agence)
        c6.metric("MEILLEUR AGENT", best_agent)
        c7.metric("ACQUISITION WALLET 1", int(fdf["wallet1_true"].sum()))
        c8.metric("ACQUISITION WALLET 2", int(fdf["wallet2_true"].sum()))

        if not base_avd["ok"]:
            st.info(
                f"ℹ️ Fichier de base introuvable ({BASE_XLSX_URL or BASE_XLSX_PATH}). "
                "Placez-le à côté du script ou renseignez BASE_XLSX_URL."
            )

        st.markdown("<h3 class='section-title'>🏆 Top 5 des meilleurs agents</h3>", unsafe_allow_html=True)
        if activations_periode == 0:
            st.info("Aucune activation sur la période sélectionnée.")
        else:
            top5 = (
                fdf[fdf["is_true"]]
                .groupby(["code_parrainage", "parrain_nom", "agence"])["is_true"]
                .sum()
                .reset_index()
                .rename(columns={"is_true": "Activations", "code_parrainage": "Code agent", "parrain_nom": "Nom & Prénoms", "agence": "Agence"})
                .sort_values("Activations", ascending=False)
                .head(5)
            )
            top5.insert(0, "Rang", range(1, len(top5) + 1))
            st.dataframe(top5, use_container_width=True, hide_index=True)

        st.markdown("<h3 class='section-title'>Répartition par équipe & détails des AVD</h3>", unsafe_allow_html=True)
        if fdf.empty:
            st.info("Aucune donnée ne correspond aux critères de filtre sélectionnés.")
        else:
            for ag in sorted(fdf["agence"].unique()):
                ag_df = fdf[fdf["agence"] == ag]
                team_activations = int(ag_df["is_true"].sum())
                st.markdown(
                    f"<div class='team-header-box'>🏷️ Équipe <b>{ag}</b> — <b>{team_activations} activation(s)</b></div>",
                    unsafe_allow_html=True,
                )
                if team_activations == 0:
                    st.caption("Aucune activation pour cette équipe sur la période.")
                    continue

                agent_summary = (
                    ag_df.groupby(["code_parrainage", "parrain_nom"])
                    .agg(Activations=("is_true", "sum"), Wallet_1=("wallet1_true", "sum"), Wallet_2=("wallet2_true", "sum"))
                    .reset_index()
                )
                agent_summary = agent_summary[agent_summary["Activations"] > 0]
                agent_summary["% de l'équipe"] = ((agent_summary["Activations"] / team_activations) * 100).round(1).astype(str) + " %"
                agent_summary = agent_summary.sort_values("Activations", ascending=False)

                st.dataframe(
                    agent_summary.rename(columns={"code_parrainage": "Code agent", "parrain_nom": "Nom & Prénoms", "Wallet_1": "Wallet 1", "Wallet_2": "Wallet 2"}),
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("<br>", unsafe_allow_html=True)

        st.markdown(
            "<div style='text-align:center; color:#9CA3AF; font-size:0.8rem; margin-top:40px;'>"
            "FAIT PAR AYEBIE GRAM MESCHAC — DATA SCIENTIST/DATA ENGINEER — "
            "MSc DATA SCIENCE AND ANALYTICS - ACITY</div>",
            unsafe_allow_html=True,
        )

    except Exception as e:  # noqa: BLE001 — dernier filet de sécurité : jamais de page blanche/crash brut
        st.error("Une erreur inattendue est survenue lors de l'affichage. Nouvelle tentative dans quelques secondes.")
        with st.expander("Détail technique (pour le support)"):
            st.exception(e)


if _fragment_decorator is not None:
    _render_dashboard = _fragment_decorator(run_every=f"{BRUT_TTL}s")(_render_dashboard)
    _render_dashboard()
else:
    # Compatibilité Streamlit < 1.37 : on retombe sur un rafraîchissement pleine page.
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=BRUT_TTL * 1000, key="auto_reload")
    except ImportError:
        st.caption("Astuce : `pip install streamlit-autorefresh` ou mettez Streamlit à jour (≥1.37) pour l'auto-refresh.")
    _render_dashboard()
