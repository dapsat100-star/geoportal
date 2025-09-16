# app_streamlit_geoportal.py
import io
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

# ===================== CONFIGURE AQUI =====================
DEFAULT_BASE_URL = "https://raw.githubusercontent.com/dapsat100-star/geoportal/main"
LOGO_REL_PATH    = "images/logomavipe.jpeg"   # ajuste se necessário
# =========================================================

# Mapa (opcional)
try:
    import folium
    from streamlit_folium import st_folium
    HAVE_MAP = True
except Exception:
    HAVE_MAP = False

# PDF deps
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from urllib.request import urlopen

st.set_page_config(page_title="Geoportal — Plotly", layout="wide")
st.title("📷 Geoportal de Metano — versão Plotly (clean + interativo)")

# ================= Sidebar =================
with st.sidebar:
    st.header("📁 Suba o Excel")
    uploaded = st.file_uploader("Upload do Excel (.xlsx)", type=["xlsx"])
    st.caption(f"As URLs das figuras serão montadas como `{DEFAULT_BASE_URL}/images/<arquivo>` automaticamente.")
    st.markdown("---")
    with st.expander("⚙️ Opções de série temporal"):
        freq = st.selectbox("Frequência", ["Diário","Semanal","Mensal","Trimestral"], index=2)
        agg = st.selectbox("Agregação", ["média","mediana","máx","mín"], index=0)
        smooth = st.selectbox("Suavização", ["Nenhuma","Média móvel","Exponencial (EMA)"], index=0)
        window = st.slider("Janela/Span", 3, 90, 7, step=1)
        show_trend = st.checkbox("Mostrar tendência linear", value=False)
        show_conf = st.checkbox("Mostrar banda P10–P90", value=False)

# ================= Helpers =================
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
        if s.lower() in ("lat","latitude"):
            normed.append("Lat")
        elif s.lower() in ("long","lon","longitude"):
            normed.append("Long")
        else:
            normed.append(s)
    df.columns = normed
    return df

def extract_dates_from_first_row(df: pd.DataFrame) -> Tuple[List[str], Dict[str, str], List[pd.Timestamp]]:
    cols = list(df.columns)
    try:
        data_idx = cols.index("Data")
    except ValueError:
        data_idx = 3 if len(cols) > 3 else 0
    date_cols = cols[data_idx:]
    labels, stamps = {}, []
    for c in date_cols:
        v = df.loc[0, c] if 0 in df.index else None
        label, ts = None, pd.NaT
        if pd.notna(v):
            for dayfirst in (True, False):
                try:
                    dt = pd.to_datetime(v, dayfirst=dayfirst, errors="raise")
                    label = dt.strftime("%Y-%m-%d"); ts = pd.to_datetime(label); break
                except Exception:
                    pass
        if not label:
            try:
                dt = pd.to_datetime(str(c), dayfirst=True, errors="raise")
                label = dt.strftime("%Y-%m"); ts = pd.to_datetime(label + "-01", errors="coerce")
            except Exception:
                label = str(c); ts = pd.NaT
        labels[c] = label; stamps.append(ts)
    return date_cols, labels, stamps

def build_record_for_month(df: pd.DataFrame, date_col: str) -> Dict[str, Optional[str]]:
    dfi = df.copy()
    if dfi.columns[0] != "Parametro":
        dfi.columns = ["Parametro"] + list(dfi.columns[1:])
    dfi["Parametro"] = dfi["Parametro"].astype(str).str.strip()
    dfi = dfi.set_index("Parametro", drop=True)
    rec = {param: dfi.loc[param, date_col] for param in dfi.index}
    rec["_lat"] = df["Lat"].dropna().iloc[0] if "Lat" in df.columns and df["Lat"].notna().any() else None
    rec["_long"] = df["Long"].dropna().iloc[0] if "Long" in df.columns and df["Long"].notna().any() else None
    return rec

def resolve_image_target(path_str: str) -> Optional[str]:
    if path_str is None or (isinstance(path_str, float) and pd.isna(path_str)): return None
    s = str(path_str).strip()
    if not s: return None
    s = s.replace("\\","/"); s = s[2:] if s.startswith("./") else s
    if s.lower().startswith(("http://","https://")): return s
    return f"{DEFAULT_BASE_URL.rstrip('/')}/{s.lstrip('/')}"

def extract_series(dfi: pd.DataFrame, date_cols_sorted, dates_ts_sorted, row_name="Taxa Metano"):
    idx_map = {i.lower(): i for i in dfi.index}
    key = idx_map.get(row_name.lower())
    rows = []
    if key is not None:
        for i, col in enumerate(date_cols_sorted):
            val = dfi.loc[key, col] if col in dfi.columns else None
            try: num = float(pd.to_numeric(val))
            except Exception: num = None
            ts = dates_ts_sorted[i]
            if pd.notna(num) and pd.notna(ts):
                rows.append({"date": ts, "value": float(num)})
    s = pd.DataFrame(rows)
    if not s.empty: s = s.sort_values("date").reset_index(drop=True)
    return s

def resample_and_smooth(s: pd.DataFrame, freq_code: str, agg: str, smooth: str, window: int):
    if s.empty: return s
    s2 = s.set_index("date").asfreq("D")
    agg_fn = {"média":"mean","mediana":"median","máx":"max","mín":"min"}[agg]
    out = getattr(s2.resample(freq_code), agg_fn)().dropna().reset_index()
    if smooth == "Média móvel":
        out["value"] = out["value"].rolling(window=window, min_periods=1).mean()
    elif smooth == "Exponencial (EMA)":
        out["value"] = out["value"].ewm(span=window, adjust=False).mean()
    return out

# =============== Fluxo principal ===============
if uploaded is None:
    st.info("Faça o upload do seu Excel (`.xlsx`) no painel lateral.")
    st.stop()

try:
    book = read_excel_from_bytes(uploaded)
except Exception as e:
    st.error(f"Falha ao ler o Excel enviado. Detalhe: {e}")
    st.stop()

book = {name: normalize_cols(df.copy()) for name, df in book.items()}

# Seleção de site e data
site = st.selectbox("Selecione o Site", sorted(book.keys()))
df_site = book[site]
date_cols, labels, stamps = extract_dates_from_first_row(df_site)
order = sorted(range(len(date_cols)), key=lambda i: (pd.Timestamp.min if pd.isna(stamps[i]) else stamps[i]))
date_cols_sorted = [date_cols[i] for i in order]
labels_sorted = [labels[date_cols[i]] for i in order]
stamps_sorted = [stamps[i] for i in order]

selected_label = st.selectbox("Selecione a data", labels_sorted)
selected_col = date_cols_sorted[labels_sorted.index(selected_label)]

# Layout
left, right = st.columns([2,1])

with left:
    rec = build_record_for_month(df_site, selected_col)
    img = resolve_image_target(rec.get("Imagem"))
    st.subheader(f"Imagem — {site} — {selected_label}")
    if img:
        st.image(img, use_container_width=True)
    else:
        st.error("Imagem não encontrada para essa data.")
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

    # -------- getv robusto (ignora acentos + aceita aliases) --------
    import unicodedata, re
    def _norm_txt(s: str) -> str:
        if s is None: return ""
        s = unicodedata.normalize("NFKD", str(s))
        s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s
    _index_norm = {_norm_txt(ix): ix for ix in dfi.index}

    def getv(name: str, *aliases):
        keys = [_norm_txt(name)] + [_norm_txt(a) for a in aliases]
        for k in keys:
            if k in _index_norm:
                return dfi.loc[_index_norm[k], selected_col]
        # fallback: início equivalente
        for nk, orig in _index_norm.items():
            if any(nk.startswith(k) for k in keys if k):
                return dfi.loc[orig, selected_col]
        return None
    # ----------------------------------------------------------------

    k1, k2, k3 = st.columns(3)
    k1.metric("Taxa Metano", f"{getv('Taxa Metano')}" if pd.notna(getv('Taxa Metano')) else "—")
    k2.metric("Incerteza", f"{getv('Incerteza')}" if pd.notna(getv('Incerteza')) else "—")
    k3.metric("Vento", f"{getv('Velocidade do Vento')}" if pd.notna(getv('Velocidade do Vento')) else "—")

    st.markdown("---")
    st.caption("Tabela completa (parâmetro → valor):")
    table_df = dfi[[selected_col]].copy()
    table_df.columns = ["Valor"]
    if "Imagem" in table_df.index: table_df = table_df.drop(index="Imagem")
    table_df = table_df.applymap(lambda v: "" if (pd.isna(v)) else str(v))
    st.dataframe(table_df, use_container_width=True)

# --------- Gráficos (Plotly) ---------
st.markdown("### Série temporal — Taxa de Metano (site)")

series_raw = extract_series(dfi, date_cols_sorted, stamps_sorted)
freq_code = {"Diário":"D","Semanal":"W","Mensal":"M","Trimestral":"Q"}[freq]
series = resample_and_smooth(series_raw, freq_code, agg, smooth, window)

fig_line = None
if not series.empty:
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(x=series["date"], y=series["value"], mode="lines+markers", name="Taxa Metano"))
    if show_conf and len(series) >= 3:
        p10 = series["value"].quantile(0.10); p90 = series["value"].quantile(0.90)
        fig_line.add_trace(go.Scatter(
            x=pd.concat([series["date"], series["date"][::-1]]),
            y=pd.concat([pd.Series([p90]*len(series)), pd.Series([p10]*len(series))[::-1]]),
            fill='toself', opacity=0.15, line=dict(width=0), name="P10–P90"
        ))
    if show_trend and len(series) >= 2:
        x = (series["date"] - series["date"].min()).dt.days.values.astype(float)
        yvals = series["value"].values.astype(float)
        coeffs = np.polyfit(x, yvals, 1); line = np.poly1d(coeffs)
        fig_line.add_trace(go.Scatter(x=series["date"], y=line(x), mode="lines", name="Tendência", line=dict(dash="dash")))
    fig_line.update_layout(template="plotly_white", xaxis_title="Data", yaxis_title="Taxa de Metano",
                           margin=dict(l=10, r=10, t=30, b=10), height=380)
    st.plotly_chart(fig_line, use_container_width=True)
else:
    st.info("Sem dados numéricos para a série temporal.")

# Boxplots + média mensal
st.markdown("### Boxplots por mês + média mensal (site)")
fig_box = None
if not series_raw.empty:
    dfm = series_raw.copy()
    dfm["month"] = dfm["date"].dt.to_period("M").dt.to_timestamp()
    order_months = sorted(dfm["month"].unique())
    fig_box = go.Figure()
    for m in order_months:
        vals = dfm.loc[dfm["month"] == m, "value"]
        fig_box.add_trace(go.Box(y=vals, name=m.strftime("%Y-%m"), boxmean="sd"))
    mean_by_month = dfm.groupby("month")["value"].mean().reindex(order_months)
    fig_box.add_trace(go.Scatter(x=[m.strftime("%Y-%m") for m in order_months],
                                 y=mean_by_month.values, mode="lines+markers", name="Média mensal"))
    fig_box.update_layout(template="plotly_white", yaxis_title="Taxa de Metano",
                          margin=dict(l=10, r=10, t=30, b=10), height=420, boxmode="group")
    st.plotly_chart(fig_box, use_container_width=True)
else:
    st.info("Sem dados suficientes para boxplots mensais.")

# ===================== PDF: helpers =====================
def _image_reader_from_url(url: str):
    try:
        with urlopen(url, timeout=10) as resp:
            img = ImageReader(resp); w, h = img.getSize(); return img, w, h
    except Exception:
        return None, 0, 0

def _draw_logo_top_right(c, page_w, page_h, margin, logo_img, lw, lh, max_w=80, max_h=40):
    if not logo_img: return
    scale = min(max_w / lw, max_h / lh)
    lw_s, lh_s = lw * scale, lh * scale
    c.drawImage(logo_img, page_w - margin - lw_s, page_h - margin - lh_s,
                width=lw_s, height=lh_s, mask='auto')

def build_report_pdf(site, date, taxa, inc, vento, img_url, fig1, fig2,
                     logo_rel_path: str = LOGO_REL_PATH,
                     satellite: Optional[str] = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4; margin = 40; y = H - margin

    # logo 1x
    logo_url = f"{DEFAULT_BASE_URL.rstrip('/')}/{logo_rel_path.lstrip('/')}"
    logo_img, logo_w, logo_h = _image_reader_from_url(logo_url)

    # página 1
    _draw_logo_top_right(c, W, H, margin, logo_img, logo_w, logo_h)

    # Cabeçalho
    c.setFont("Helvetica-Bold", 16); c.drawString(margin, y, "Relatório Geoportal de Metano"); y -= 20
    c.setFont("Helvetica", 10); c.drawString(margin, y, f"Site: {site}  |  Data: {date}"); y -= 12
    c.drawString(margin, y, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}"); y -= 30

    # Métricas
    def _s(v): return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Métricas"); y -= 16
    c.setFont("Helvetica", 11)
    for t in (f"• Taxa Metano: {_s(taxa)}",
              f"• Incerteza: {_s(inc)}",
              f"• Velocidade do Vento: {_s(vento)}",
              f"• Satélite: {_s(satellite)}"):
        c.drawString(margin, y, t); y -= 14
    y -= 12

    # Imagem do Excel
    if img_url:
        main_img, iw, ih = _image_reader_from_url(img_url)
        if main_img:
            max_w, max_h = W - 2*margin, 200
            s = min(max_w/iw, max_h/ih); w, h = iw*s, ih*s
            if y - h < margin: c.showPage(); y = H - margin; _draw_logo_top_right(c, W, H, margin, logo_img, logo_w, logo_h)
            c.drawImage(main_img, margin, y - h, width=w, height=h, mask='auto'); y -= h + 18

    # Gráfico 1
    if fig1 is not None:
        try:
            png1 = fig1.to_image(format="png", width=1400, height=800, scale=2, engine="kaleido")
            img1 = ImageReader(io.BytesIO(png1)); iw, ih = img1.getSize()
            s = min((W - 2*margin)/iw, 260/ih); w, h = iw*s, ih*s
            if y - h < margin: c.showPage(); y = H - margin; _draw_logo_top_right(c, W, H, margin, logo_img, logo_w, logo_h)
            c.drawImage(img1, margin, y - h, width=w, height=h, mask='auto'); y -= h + 18
        except Exception as e:
            c.setFont("Helvetica", 9); c.drawString(margin, y, f"[Falha gráfico 1: {e}]"); y -= 14

    # Gráfico 2
    if fig2 is not None:
        try:
            png2 = fig2.to_image(format="png", width=1400, height=900, scale=2, engine="kaleido")
            img2 = ImageReader(io.BytesIO(png2)); iw2, ih2 = img2.getSize()
            s2 = min((W - 2*margin)/iw2, 260/ih2); w2, h2 = iw2*s2, ih2*s2
            if y - h2 < margin: c.showPage(); y = H - margin; _draw_logo_top_right(c, W, H, margin, logo_img, logo_w, logo_h)
            c.drawImage(img2, margin, y - h2, width=w2, height=h2, mask='auto'); y -= h2 + 12
        except Exception as e:
            c.setFont("Helvetica", 9); c.drawString(margin, y, f"[Falha gráfico 2: {e}]"); y -= 14

    # Rodapé
    c.setFont("Helvetica", 8); c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawRightString(W - margin, margin/2, "© Geoportal — Relatório gerado automaticamente")
    c.setFillColorRGB(0,0,0)

    c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()

# ===================== Exportar PDF (UI) =====================
taxa      = getv("Taxa Metano")
inc       = getv("Incerteza")
vento     = getv("Velocidade do Vento")
satellite = getv("Satelite", "Satélite", "Satellite", "Sat")  # aliases
img_url   = resolve_image_target(rec.get("Imagem"))

st.markdown("---")
st.subheader("📄 Exportar PDF")
st.caption("Gera um PDF com logo, cabeçalho, métricas (inclui Satélite), imagem e gráficos atuais.")

if st.button("Gerar PDF (dados + gráficos)", type="primary", use_container_width=True):
    pdf_bytes = build_report_pdf(
        site=site, date=selected_label, taxa=taxa, inc=inc, vento=vento,
        img_url=img_url, fig1=fig_line, fig2=fig_box,
        logo_rel_path=LOGO_REL_PATH, satellite=satellite
    )
    st.download_button(
        label="⬇️ Baixar PDF",
        data=pdf_bytes,
        file_name=f"relatorio_geoportal_{site}_{selected_label}.pdf".replace(" ", "_"),
        mime="application/pdf",
        use_container_width=True
    )
