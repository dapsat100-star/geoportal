
import io
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote

import pandas as pd
import streamlit as st

# ===================== CONFIGURE AQUI =====================
# Coloque aqui o endereço RAW do seu repositório (pasta raiz do repo):
# Ex.: "https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPO/SUA_BRANCH"
DEFAULT_BASE_URL = "https://raw.githubusercontent.com/dapsat100-star/geoportal/main"
# =========================================================

# Opcional: dependências de mapa (o app continua mesmo sem elas)
try:
    import folium
    from streamlit_folium import st_folium
    HAVE_MAP = True
except Exception:
    HAVE_MAP = False

st.set_page_config(page_title="Geoportal — Auto-imagem (base fixa)", layout="wide")
st.title("📷 Geoportal de Metano — Imagem automática (upload do Excel)")

with st.sidebar:
    st.header("📁 Suba o Excel")
    uploaded = st.file_uploader("Upload do Excel (.xlsx)", type=["xlsx"])
    st.caption("O app vai montar as URLs das figuras como "
               f"`{DEFAULT_BASE_URL}/images/<arquivo>` automaticamente.")

@st.cache_data
def read_excel_from_bytes(file_bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(file_bytes, engine="openpyxl")
    return {sn: pd.read_excel(xls, sheet_name=sn, engine="openpyxl") for sn in xls.sheet_names}

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    # Garante nomes canônicos
    cols = list(df.columns)
    if cols:
        cols[0] = "Parametro"  # força a primeira coluna a ser 'Parametro' (mesmo que venha 'Unnamed: 0')
    normed = []
    for i, c in enumerate(cols):
        s = str(c).strip()
        if s.lower() in ("lat", "latitude"):
            normed.append("Lat")
        elif s.lower() in ("long", "lon", "longitude"):
            normed.append("Long")
        else:
            normed.append(s)
    df.columns = normed
    return df

def extract_dates_from_first_row(df: pd.DataFrame) -> Tuple[List[str], Dict[str, str]]:
    cols = list(df.columns)
    # detecta a coluna 'Data' (ou posição 3 como fallback)
    try:
        data_idx = cols.index("Data")
    except ValueError:
        data_idx = 3 if len(cols) > 3 else 0
    date_cols = cols[data_idx:]
    pretty = {}
    for c in date_cols:
        v = df.loc[0, c] if 0 in df.index else None
        label = None
        if pd.notna(v):
            for dayfirst in (True, False):
                try:
                    dt = pd.to_datetime(v, dayfirst=dayfirst, errors="raise")
                    label = dt.strftime("%Y-%m-%d")
                    break
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

def resolve_image_target(path_str: str) -> Optional[str]:
    """Monta URL final da imagem usando DEFAULT_BASE_URL."""
    if path_str is None or (isinstance(path_str, float) and pd.isna(path_str)):
        return None
    s = str(path_str).strip()
    if not s:
        return None
    s = s.replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    # Se já for URL absoluta, usa direto
    if s.lower().startswith(("http://", "https://")):
        return quote(s, safe=":/._-%")
    # Caso contrário, trata como relativo ao repo (images/...)
    left = DEFAULT_BASE_URL.rstrip("/")
    right = s.lstrip("/")
    full = f"{left}/{right}"
    return quote(full, safe=":/._-%")

# === Fluxo principal ===
if uploaded is None:
    st.info("Faça o upload do seu Excel (`.xlsx`) no painel lateral.")
    st.stop()

# Lê o workbook inteiro
try:
    book = read_excel_from_bytes(uploaded)
except Exception as e:
    st.error(f"Falha ao ler o Excel enviado. Detalhe: {e}")
    st.stop()

# Normaliza colunas
book = {name: normalize_cols(df.copy()) for name, df in book.items()}

# Seleção de site e data
site = st.selectbox("Selecione o Site", sorted(book.keys()))
df_site = book[site]
date_cols, pretty = extract_dates_from_first_row(df_site)
ordered = sorted(date_cols, key=lambda c: pretty.get(c, str(c)))
labels = [pretty[c] for c in ordered]
selected_label = st.selectbox("Selecione a data", labels)
selected_col = ordered[labels.index(selected_label)]

# Layout: imagem destaque + detalhes + opcional mapa
left, right = st.columns([2,1])

with left:
    rec = build_record_for_month(df_site, selected_col)
    img = resolve_image_target(rec.get("Imagem"))
    st.subheader(f"Imagem — {site} — {selected_label}")
    if img:
        st.image(img, use_container_width=True)
    else:
        st.error("Imagem não encontrada para essa data.")
        with st.expander("🔎 Diagnóstico"):
            st.write("- Valor na linha `Imagem`:", rec.get("Imagem"))
            st.write("- DEFAULT_BASE_URL:", DEFAULT_BASE_URL or "(vazio)")
            if rec.get("Imagem"):
                tmp = resolve_image_target(rec.get("Imagem"))
                st.write("- URL tentada:")
                st.code(tmp or "(sem URL)")

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
    # Evita erro do PyArrow: renderiza como string
    show_df = show_df.applymap(lambda v: "" if (pd.isna(v)) else str(v))
    st.dataframe(show_df, use_container_width=True)
