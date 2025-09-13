
import io
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

# ===================== CONFIGURE AQUI =====================
DEFAULT_BASE_URL = "https://raw.githubusercontent.com/dapsat100-star/geoportal/main"
# =========================================================

try:
    import folium
    from streamlit_folium import st_folium
    HAVE_MAP = True
except Exception:
    HAVE_MAP = False

st.set_page_config(page_title="Geoportal — Imagem + Séries (melhoradas)", layout="wide")
st.title("📷 Geoportal de Metano — Imagem automática + Séries do Site (melhoradas)")

with st.sidebar:
    st.header("📁 Suba o Excel")
    uploaded = st.file_uploader("Upload do Excel (.xlsx)", type=["xlsx"])
    st.caption(f"As URLs das figuras serão montadas como `{DEFAULT_BASE_URL}/images/<arquivo>`.")

@st.cache_data
def read_excel_from_bytes(file_bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(file_bytes, engine="openpyxl")
    return {sn: pd.read_excel(xls, sheet_name=sn, engine="openpyxl") for sn in xls.sheet_names}

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = list(df.columns)
    if cols:
        cols[0] = "Parametro"
    normed = []
    for c in cols:
        s = str(c).strip()
        if s.lower() in ("lat","latitude"): normed.append("Lat")
        elif s.lower() in ("long","lon","longitude"): normed.append("Long")
        else: normed.append(s)
    df.columns = normed
    return df

def extract_dates_from_first_row(df: pd.DataFrame) -> Tuple[List[str], Dict[str, str], List[pd.Timestamp]]:
    cols = list(df.columns)
    try:
        data_idx = cols.index("Data")
    except ValueError:
        data_idx = 3 if len(cols) > 3 else 0
    date_cols = cols[data_idx:]
    pretty = {}
    dates_ts = []
    for c in date_cols:
        v = df.loc[0, c] if 0 in df.index else None
        label = None
        ts = pd.NaT
        if pd.notna(v):
            for dayfirst in (True, False):
                try:
                    dt = pd.to_datetime(v, dayfirst=dayfirst, errors="raise")
                    label = dt.strftime("%Y-%m-%d")
                    ts = pd.to_datetime(label)
                    break
                except Exception:
                    pass
        if not label:
            try:
                dt = pd.to_datetime(str(c), dayfirst=True, errors="raise")
                label = dt.strftime("%Y-%m")
                ts = pd.to_datetime(label + "-01", errors="coerce")
            except Exception:
                label = str(c)
                ts = pd.NaT
        pretty[c] = label
        dates_ts.append(ts)
    return date_cols, pretty, dates_ts

def build_record_for_month(df: pd.DataFrame, date_col: str) -> Dict[str, Optional[str]]:
    dfi = df.copy()
    if dfi.columns[0] != "Parametro":
        dfi.columns = ["Parametro"] + list(dfi.columns[1:])
    dfi["Parametro"] = dfi["Parametro"].astype(str).str.strip()
    dfi = dfi.set_index("Parametro", drop=True)
    rec = {param: dfi.loc[param, date_col] for param in dfi.index}
    lat_val = df["Lat"].dropna().iloc[0] if "Lat" in df.columns and df["Lat"].notna().any() else None
    lon_val = df["Long"].dropna().iloc[0] if "Long" in df.columns and df["Long"].notna().any() else None
    rec["_lat"] = lat_val
    rec["_long"] = lon_val
    return rec

def resolve_image_target(path_str: str) -> Optional[str]:
    if path_str is None or (isinstance(path_str, float) and pd.isna(path_str)):
        return None
    s = str(path_str).strip()
    if not s: return None
    s = s.replace("\\","/")
    if s.startswith("./"): s = s[2:]
    if s.lower().startswith(("http://","https://")): return s
    return f"{DEFAULT_BASE_URL.rstrip('/')}/{s.lstrip('/')}"

# ===== Charts helpers =====
def build_taxa_series(dfi: pd.DataFrame, date_cols_sorted: list, dates_ts_sorted: list) -> pd.DataFrame:
    idx_map = {i.lower(): i for i in dfi.index}
    key = idx_map.get("taxa metano")
    rows = []
    if key is not None:
        for i, col in enumerate(date_cols_sorted):
            val = dfi.loc[key, col] if col in dfi.columns else None
            try:
                num = float(pd.to_numeric(val))
            except Exception:
                num = None
            if pd.notna(num) and pd.notna(dates_ts_sorted[i]):
                rows.append({"date": dates_ts_sorted[i], "value": float(num)})
    return pd.DataFrame(rows)

def line_chart(ax, series_df: pd.DataFrame, title: str):
    ax.plot(series_df["date"], series_df["value"], marker="o", linewidth=2)
    ax.set_title(title)
    ax.set_xlabel("Data")
    ax.set_ylabel("Taxa de Metano")
    ax.grid(True, linestyle="--", alpha=0.4)
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")

def monthly_boxplot_with_line(ax, series_df: pd.DataFrame, title: str):
    # Agrupa por mês (YYYY-MM) e faz boxplot por mês na ordem temporal
    if series_df.empty:
        ax.text(0.5, 0.5, "Sem dados suficientes", ha="center", va="center")
        return
    series_df = series_df.copy()
    series_df["month"] = series_df["date"].dt.to_period("M").dt.to_timestamp()
    groups = series_df.groupby("month")["value"].apply(list).reset_index()

    # Posições no eixo X correspondendo aos meses ordenados
    months = groups["month"].tolist()
    positions = list(range(1, len(months)+1))

    # Boxplot por mês (um único plot)
    ax.boxplot(groups["value"].tolist(), positions=positions)
    # Sobrepõe a linha dos valores médios por mês (mesmo eixo, um único plot)
    means = [pd.Series(v).mean() for v in groups["value"]]
    ax.plot(positions, means, marker="o", linewidth=2)

    ax.set_title(title)
    ax.set_xlabel("Mês")
    ax.set_ylabel("Taxa de Metano")
    ax.set_xticks(positions)
    ax.set_xticklabels([m.strftime("%Y-%m") for m in months], rotation=30, ha="right")
    ax.grid(True, linestyle="--", alpha=0.4)

# ===== Main =====
if uploaded is None:
    st.info("Faça o upload do seu Excel (`.xlsx`) no painel lateral.")
    st.stop()

try:
    book = read_excel_from_bytes(uploaded)
except Exception as e:
    st.error(f"Falha ao ler o Excel enviado. Detalhe: {e}")
    st.stop()

book = {name: normalize_cols(df.copy()) for name, df in book.items()}

site = st.selectbox("Selecione o Site", sorted(book.keys()))
df_site = book[site]
date_cols, pretty, dates_ts = extract_dates_from_first_row(df_site)

order_idx = sorted(range(len(date_cols)), key=lambda i: (pd.Timestamp.min if pd.isna(dates_ts[i]) else dates_ts[i]))
date_cols_sorted = [date_cols[i] for i in order_idx]
labels_sorted = [pretty[date_cols[i]] for i in order_idx]
dates_ts_sorted = [dates_ts[i] for i in order_idx]

selected_label = st.selectbox("Selecione a data", labels_sorted)
selected_col = date_cols_sorted[labels_sorted.index(selected_label)]

left, right = st.columns([2,1])

with left:
    rec = build_record_for_month(df_site, selected_col)
    img = resolve_image_target(rec.get("Imagem"))
    st.subheader(f"Imagem — {site} — {selected_label}")
    if img:
        st.image(img, use_container_width=True)
    else:
        st.error("Imagem não encontrada para essa data.")

    # Mapa opcional
    if HAVE_MAP and (rec.get("_lat") is not None and rec.get("_long") is not None):
        with st.expander("🗺️ Mostrar mapa (opcional)", expanded=False):
            m = folium.Map(location=[float(rec["_lat"]), float(rec["_long"])], zoom_start=13, tiles="OpenStreetMap")
            folium.Marker([float(rec["_lat"]), float(rec["_long"])], tooltip=site).add_to(m)
            st_folium(m, height=400, use_container_width=True)

with right:
    st.subheader("Detalhes do Registro")
    dfi = df_site.copy()
    if dfi.columns[0] != "Parametro":
        dfi.columns = ["Parametro"] + list(dfi.columns[1:])
    dfi["Parametro"] = dfi["Parametro"].astype(str).str.strip()
    dfi = dfi.set_index("Parametro", drop=True)

    def getv(name):
        for cand in (name, name.capitalize(), name.title(), name.replace("ç","c").replace("á","a")):
            if cand in dfi.index:
                return dfi.loc[cand, selected_col]
        return None
    taxa = getv("Taxa Metano")
    inc = getv("Incerteza")
    vento = getv("Velocidade do Vento")
    obs = getv("Observacoes do Operador") or getv("Observações do Operador")
    sat = getv("Satelite") or getv("Satélite")

    k1, k2, k3 = st.columns(3)
    k1.metric("Taxa Metano", f"{taxa}" if pd.notna(taxa) else "—")
    k2.metric("Incerteza", f"{inc}" if pd.notna(inc) else "—")
    k3.metric("Vento", f"{vento}" if pd.notna(vento) else "—")

    st.markdown("---")
    st.caption("Tabela completa (parâmetro → valor):")
    show_df = dfi[[selected_col]].copy()
    show_df.columns = ["Valor"]
    if "Imagem" in show_df.index:
        show_df = show_df.drop(index="Imagem")
    show_df = show_df.applymap(lambda v: "" if (pd.isna(v)) else str(v))
    st.dataframe(show_df, use_container_width=True)

    # ====== SÉRIE TEMPORAL (limpa) ======
    st.markdown("---")
    st.subheader("Série temporal — Taxa de Metano (site)")
    series_df = build_taxa_series(dfi, date_cols_sorted, dates_ts_sorted)
    if not series_df.empty:
        fig1, ax1 = plt.subplots()
        line_chart(ax1, series_df, "Taxa de Metano ao longo do tempo")
        st.pyplot(fig1)
    else:
        st.info("Sem valores numéricos para a série temporal.")

    # ====== BOXPLOTS POR MÊS + LINHA DE MÉDIAS ======
    st.subheader("Boxplots por mês + média mensal (site)")
    if not series_df.empty:
        fig2, ax2 = plt.subplots()
        monthly_boxplot_with_line(ax2, series_df, "Distribuição mensal e média")
        st.pyplot(fig2)
    else:
        st.info("Sem dados suficientes para boxplots mensais.")
