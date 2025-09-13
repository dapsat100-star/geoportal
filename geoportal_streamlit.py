
import io
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import pandas as pd
import streamlit as st

# Optional mapping feature (uses internet tiles), guarded import
try:
    import folium
    from streamlit_folium import st_folium
    HAVE_MAP = True
except Exception:
    HAVE_MAP = False

st.set_page_config(page_title="Geoportal de Metano (Streamlit)", layout="wide")
st.markdown(
    "<div style='background:#3b82f6;color:white;padding:10px 16px;border-radius:8px;margin-bottom:10px;display:flex;align-items:center;gap:12px'>"
    "<img src='https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/dap.svg' onerror='this.style.display=\"none\"' width='24'/>"
    "<span style='font-size:18px;font-weight:600'>Sistema de Monitoramento de Metano por Satélite</span>"
    "</div>",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("📁 Fonte dos Dados")
    excel_url = st.text_input("RAW URL do Excel (.xlsx) no GitHub (opcional):",
                              placeholder="https://raw.githubusercontent.com/<user>/<repo>/<branch>/dados/planilha.xlsx")
    uploaded = st.file_uploader("Ou faça upload do Excel (.xlsx)", type=["xlsx"])
    base_url = st.text_input("Base URL para imagens (obrigatório se ImagePath for relativo):",
                             placeholder="https://raw.githubusercontent.com/<user>/<repo>/<branch>")

@st.cache_data
def read_excel_from_url(url: str) -> Dict[str, pd.DataFrame]:
    import requests, io
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    xls = pd.ExcelFile(io.BytesIO(r.content))
    return {sn: pd.read_excel(xls, sheet_name=sn) for sn in xls.sheet_names}

@st.cache_data
def read_excel_from_bytes(file_bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(file_bytes)
    return {sn: pd.read_excel(xls, sheet_name=sn) for sn in xls.sheet_names}

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = []
    for i, c in enumerate(df.columns):
        s = str(c).strip()
        if i == 0 and s.lower() in ("parametro", "parâmetro"):
            cols.append("Parametro")
        elif s.lower() in ("lat", "latitude"):
            cols.append("Lat")
        elif s.lower() in ("long", "lon", "longitude"):
            cols.append("Long")
        else:
            cols.append(c)
    df.columns = cols
    return df

def extract_dates_from_first_row(df: pd.DataFrame) -> Tuple[List[str], Dict[str, str]]:
    """
    Given the sheet structure:
      - column 0: Parametro
      - optional: Lat, Long
      - columns from 'Data' onwards: each *column* corresponds to a date,
        but the header is not a date; the *first row* has the date value.
    Returns (date_columns, pretty_label_map[col] -> 'YYYY-MM-DD' or 'YYYY-MM').
    """
    # find index of first "Data" column
    cols = list(df.columns)
    try:
        data_idx = cols.index("Data")
    except ValueError:
        # fallback: find a column named similar to "Data"
        data_idx = next((i for i, c in enumerate(cols) if str(c).strip().lower() == "data"), 3)

    date_cols = cols[data_idx:]
    pretty: Dict[str, str] = {}
    for c in date_cols:
        try:
            v = df.loc[0, c]
        except Exception:
            v = None
        # normalize
        label = None
        if pd.notna(v):
            try:
                dt = pd.to_datetime(v, dayfirst=True, errors="raise")
                label = dt.strftime("%Y-%m-%d")
            except Exception:
                s = str(v).strip()
                try:
                    dt = pd.to_datetime(s, dayfirst=True, errors="raise")
                    label = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
        if not label:
            # fallback to column name
            try:
                dt = pd.to_datetime(str(c), dayfirst=True, errors="raise")
                label = dt.strftime("%Y-%m")
            except Exception:
                label = str(c)
        pretty[c] = label
    # keep only columns that actually look like dates (have a pretty different than raw header)
    return date_cols, pretty

def build_record_for_month(df: pd.DataFrame, date_col: str) -> Dict[str, Optional[str]]:
    # index by parametro
    dfi = df.copy()
    if dfi.columns[0] != "Parametro":
        dfi.columns = ["Parametro"] + list(dfi.columns[1:])
    dfi["Parametro"] = dfi["Parametro"].astype(str).str.strip()
    dfi = dfi.set_index("Parametro", drop=True)
    rec = {}
    for param in dfi.index:
        rec[param] = dfi.loc[param, date_col]
    # lat/long (from the sheet columns, first non-null)
    lat_val = df["Lat"].dropna().iloc[0] if "Lat" in df.columns and df["Lat"].notna().any() else None
    lon_val = df["Long"].dropna().iloc[0] if "Long" in df.columns and df["Long"].notna().any() else None
    rec["_lat"] = lat_val
    rec["_long"] = lon_val
    return rec

def resolve_image_target(path_str: str, base_url: str) -> Optional[str]:
    if path_str is None or (isinstance(path_str, float) and pd.isna(path_str)):
        return None
    s = str(path_str).strip()
    if not s:
        return None
    if s.lower().startswith(("http://", "https://")):
        return s
    if base_url.strip():
        return f"{base_url.rstrip('/')}/{s.lstrip('/')}"
    return None

# Load workbook
book: Dict[str, pd.DataFrame] = {}
if excel_url.strip():
    try:
        book = read_excel_from_url(excel_url.strip())
        st.success("Excel carregado via URL.")
    except Exception as e:
        st.error(f"Falha ao baixar/ler o Excel da URL. Detalhe: {e}")
        st.stop()
elif uploaded is not None:
    try:
        book = read_excel_from_bytes(uploaded)
        st.success("Excel carregado via upload.")
    except Exception as e:
        st.error(f"Falha ao ler o Excel enviado. Detalhe: {e}")
        st.stop()
else:
    st.info("Forneça o RAW URL do Excel ou faça upload do arquivo.")
    st.stop()

# Normalize columns in all sheets
book = {name: normalize_cols(df.copy()) for name, df in book.items()}

# Site selector (one sheet per site)
site = st.selectbox("Selecione o Site", sorted(book.keys()))
df_site = book[site]

# Identify date columns from first row values
date_cols, pretty = extract_dates_from_first_row(df_site)

# Build sidebar date range and list
with st.sidebar:
    st.header("🗓️ Filtro de Datas")
    # Sorted by date label
    ordered = sorted(date_cols, key=lambda c: pretty.get(c, str(c)))
    # Range selection (start/end indices)
    start_idx = 0
    end_idx = len(ordered) - 1
    # date labels for display
    labels = [pretty[c] for c in ordered]
    # pick a single date to preview
    selected_label = st.selectbox("Escolha a data", labels)
    selected_col = ordered[labels.index(selected_label)]

    st.markdown("---")
    st.caption("Pré-visualizações")
    for c in ordered:
        rec = build_record_for_month(df_site, c)
        img = resolve_image_target(rec.get("Imagem"), base_url)
        sat = rec.get("Satelite") or rec.get("Satélite")
        dt_label = pretty[c]
        if img:
            st.image(img, use_column_width=True)
        st.write(f"**{dt_label}**")
        if sat and pd.notna(sat):
            st.caption(f"Satélite: {sat}")
        st.checkbox("Selecionar", key=f"sel_{c}", value=(c==selected_col))

# Main layout: map + details
left, right = st.columns([2,1])

with left:
    st.subheader(f"Mapa — {site}")
    rec_sel = build_record_for_month(df_site, selected_col)
    lat = rec_sel.get("_lat")
    lon = rec_sel.get("_long")
    if HAVE_MAP and (lat is not None and lon is not None):
        m = folium.Map(location=[float(lat), float(lon)], zoom_start=13, tiles="OpenStreetMap")
        folium.Marker([float(lat), float(lon)], tooltip=site).add_to(m)
        st_folium(m, height=520, use_container_width=True)
    else:
        st.info("Mapa indisponível (faltando Lat/Long ou dependências).")
    st.subheader("Figura (pluma)")
    img = resolve_image_target(rec_sel.get("Imagem"), base_url)
    show_plume = st.toggle("PLUME", value=True)
    if show_plume and img:
        st.image(img, use_column_width=True, caption=f"{pretty[selected_col]} — {site}")
    elif show_plume:
        st.info("Sem imagem para esta data.")

with right:
    st.subheader("Detalhes do Registro")
    # Show a small table of parameters for the selected date
    dfi = df_site.copy()
    if dfi.columns[0] != "Parametro":
        dfi.columns = ["Parametro"] + list(dfi.columns[1:])
    dfi["Parametro"] = dfi["Parametro"].astype(str).str.strip()
    dfi = dfi.set_index("Parametro", drop=True)
    col = selected_col
    # Try to format common params
    def getv(name):
        for cand in (name, name.capitalize(), name.title(), name.replace("ç","c").replace("á","a")):
            if cand in dfi.index:
                return dfi.loc[cand, col]
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

    if sat and pd.notna(sat):
        st.markdown(f"**Satélite:** {sat}")
    if obs and pd.notna(obs):
        st.markdown(f"**Observações:** {obs}")

    st.markdown("---")
    st.caption("Tabela completa (parâmetro → valor):")
    show_df = dfi[[col]].copy()
    show_df.columns = ["Valor"]
    st.dataframe(show_df, use_container_width=True)
