
import io
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote

import pandas as pd
import streamlit as st

# =============== ONE-TIME SETUP (only needed if you use file upload) ===============
# If you will UPLOAD the Excel (not use RAW URL), set your repo base once here:
DEFAULT_BASE_URL = ""  # e.g., "https://raw.githubusercontent.com/<user>/<repo>/<branch>"

# Optional map deps
try:
    import folium
    from streamlit_folium import st_folium
    HAVE_MAP = True
except Exception:
    HAVE_MAP = False

st.set_page_config(page_title="Geoportal — Auto Image URL", layout="wide")
st.title("📷 Geoportal de Metano — Auto-resolve de Imagens (sem Base URL manual)")

with st.sidebar:
    st.header("📁 Fonte dos Dados")
    excel_url = st.text_input("RAW URL do Excel (.xlsx) no GitHub (opcional):",
                              placeholder="https://raw.githubusercontent.com/<user>/<repo>/<branch>/bancodados.xlsx")
    uploaded = st.file_uploader("Ou faça upload do Excel (.xlsx)", type=["xlsx"])

def infer_base_url_from_excel(raw_url: str) -> str:
    """Infer base URL as the parent directory of the raw Excel file."""
    if raw_url and "raw.githubusercontent.com" in raw_url:
        u = raw_url.split("?", 1)[0].split("#", 1)[0]
        if "/" in u:
            return u.rsplit("/", 1)[0]
    return ""

@st.cache_data
def read_excel_from_url(url: str) -> Dict[str, pd.DataFrame]:
    import requests, io
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    xls = pd.ExcelFile(io.BytesIO(r.content), engine="openpyxl")
    return {sn: pd.read_excel(xls, sheet_name=sn, engine="openpyxl") for sn in xls.sheet_names}

@st.cache_data
def read_excel_from_bytes(file_bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(file_bytes, engine="openpyxl")
    return {sn: pd.read_excel(xls, sheet_name=sn, engine="openpyxl") for sn in xls.sheet_names}

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
    cols = list(df.columns)
    try:
        data_idx = cols.index("Data")
    except ValueError:
        data_idx = next((i for i, c in enumerate(cols) if str(c).strip().lower() == "data"), 3)

    date_cols = cols[data_idx:]
    pretty: Dict[str, str] = {}
    for c in date_cols:
        v = df.loc[0, c] if 0 in df.index else None
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
            try:
                dt = pd.to_datetime(str(c), dayfirst=True, errors="raise")
                label = dt.strftime("%Y-%m")
            except Exception:
                label = str(c)
        pretty[c] = label
    return date_cols, pretty

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

def resolve_image_target(path_str: str, base_url: str) -> Optional[str]:
    """Return a displayable URL:
       - Normalizes backslashes -> '/'
       - Strips leading './'
       - If relative and base_url provided -> join
       - URL-encodes unsafe chars
    """
    if path_str is None or (isinstance(path_str, float) and pd.isna(path_str)):
        return None
    s = str(path_str).strip()
    if not s:
        return None
    s = s.replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    if s.lower().startswith(("http://", "https://")):
        return quote(s, safe=":/._-%")
    if base_url.strip():
        left = base_url.rstrip("/")
        right = s.lstrip("/")
        full = f"{left}/{right}"
        return quote(full, safe=":/._-%")
    return None

# 1) Load data + auto base URL
book = {}
auto_base_url = ""
if excel_url.strip():
    auto_base_url = infer_base_url_from_excel(excel_url.strip())
    try:
        book = read_excel_from_url(excel_url.strip())
    except Exception as e:
        st.error(f"Falha ao baixar/ler o Excel da URL. Detalhe: {e}")
        st.stop()
elif uploaded is not None:
    try:
        book = read_excel_from_bytes(uploaded)
    except Exception as e:
        st.error(f"Falha ao ler o Excel enviado. Detalhe: {e}")
        st.stop()
else:
    st.info("Forneça o RAW URL do Excel ou faça upload do arquivo.")
    st.stop()

book = {name: normalize_cols(df.copy()) for name, df in book.items()}

# 2) Site/date selection
site = st.selectbox("Selecione o Site", sorted(book.keys()))
df_site = book[site]
date_cols, pretty = extract_dates_from_first_row(df_site)
ordered = sorted(date_cols, key=lambda c: pretty.get(c, str(c)))
labels = [pretty[c] for c in ordered]
selected_label = st.selectbox("Selecione a data", labels)
selected_col = ordered[labels.index(selected_label)]

# 3) Effective base URL
effective_base = auto_base_url if excel_url.strip() else DEFAULT_BASE_URL

# 4) Layout: image (hero) + details
left, right = st.columns([2,1])

with left:
    rec = build_record_for_month(df_site, selected_col)
    img = resolve_image_target(rec.get("Imagem"), effective_base)
    st.subheader(f"Imagem — {site} — {selected_label}")
    if img:
        st.image(img, use_column_width=True)
    else:
        st.error("Imagem não encontrada para essa data.")
        with st.expander("🔎 Diagnóstico"):
            st.write("- Valor lido na linha `Imagem`:", rec.get("Imagem"))
            st.write("- Base inferida do Excel RAW:", auto_base_url or "(não aplicável)")
            st.write("- DEFAULT_BASE_URL (upload):", DEFAULT_BASE_URL or "(vazio)")
            if rec.get("Imagem"):
                st.write("- URL que seria tentada (se base existir):")
                tmp = resolve_image_target(rec.get("Imagem"), auto_base_url or DEFAULT_BASE_URL or "")
                st.code(tmp or "(sem base URL)")

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

    if sat and pd.notna(sat):
        st.markdown(f"**Satélite:** {sat}")
    if obs and pd.notna(obs):
        st.markdown(f"**Observações:** {obs}")

    st.markdown("---")
    st.caption("Tabela completa (parâmetro → valor):")
    show_df = dfi[[selected_col]].copy()
    show_df.columns = ["Valor"]
    if "Imagem" in show_df.index:
        show_df = show_df.drop(index="Imagem")
    # Avoid PyArrow type issues: render as string
    show_df = show_df.applymap(lambda v: "" if (pd.isna(v)) else str(v))
    st.dataframe(show_df, use_container_width=True)
