
import io
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

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

st.set_page_config(page_title="Geoportal — Plotly", layout="wide")
st.title("📷 Geoportal de Metano — versão Plotly (clean + interativo)")

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

@st.cache_data
def read_excel_from_bytes(file_bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(file_bytes, engine="openpyxl")
    return {sn: pd.read_excel(xls, sheet_name=sn, engine="openpyxl") for sn in xls.sheet_names}

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = list(df.columns)
    if cols: cols[0] = "Parametro"
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
    labels, stamps = {}, []
    for c in date_cols:
        v = df.loc[0, c] if 0 in df.index else None
        label, ts = None, pd.NaT
        if pd.notna(v):
            for dayfirst in (True, False):
                try:
                    dt = pd.to_datetime(v, dayfirst=dayfirst, errors="raise"); label = dt.strftime("%Y-%m-%d"); ts = pd.to_datetime(label); break
                except Exception: pass
        if not label:
            try:
                dt = pd.to_datetime(str(c), dayfirst=True, errors="raise"); label = dt.strftime("%Y-%m"); ts = pd.to_datetime(label + "-01", errors="coerce")
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

# Helpers para série temporal
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

# === Fluxo principal ===
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

    def getv(name):
        for cand in (name, name.capitalize(), name.title(), name.replace("ç","c").replace("á","a")):
            if cand in dfi.index: return dfi.loc[cand, selected_col]
        return None
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

if not series.empty:
    # Linha principal
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=series["date"], y=series["value"],
        mode="lines+markers", name="Taxa Metano"
    ))

    # Banda P10–P90 (opcional, calculada global na série agregada)
    if show_conf and len(series) >= 3:
        p10 = series["value"].quantile(0.10)
        p90 = series["value"].quantile(0.90)
        fig_line.add_trace(go.Scatter(
            x=pd.concat([series["date"], series["date"][::-1]]),
            y=pd.concat([pd.Series([p90]*len(series)), pd.Series([p10]*len(series))[::-1]]),
            fill='toself', opacity=0.15, line=dict(width=0), name="P10–P90"
        ))

    # Tendência linear (OLS simples)
    if show_trend and len(series) >= 2:
        x = (series["date"] - series["date"].min()).dt.days.values.astype(float)
        y = series["value"].values.astype(float)
        coeffs = np.polyfit(x, y, 1); line = np.poly1d(coeffs)
        fig_line.add_trace(go.Scatter(
            x=series["date"], y=line(x), mode="lines", name="Tendência", line=dict(dash="dash")
        ))

    fig_line.update_layout(
        template="plotly_white",
        xaxis_title="Data",
        yaxis_title="Taxa de Metano",
        margin=dict(l=10, r=10, t=30, b=10),
        height=380
    )
    st.plotly_chart(fig_line, use_container_width=True)
else:
    st.info("Sem dados numéricos para a série temporal.")

# Boxplots mensais + média mensal
st.markdown("### Boxplots por mês + média mensal (site)")
if not series_raw.empty:
    dfm = series_raw.copy()
    dfm["month"] = dfm["date"].dt.to_period("M").dt.to_timestamp()
    order_months = sorted(dfm["month"].unique())
    # Boxplots por mês (uma trace por mês)
    fig_box = go.Figure()
    for m in order_months:
        vals = dfm.loc[dfm["month"] == m, "value"]
        fig_box.add_trace(go.Box(y=vals, name=m.strftime("%Y-%m"), boxmean="sd"))

    # Linha da média mensal
    mean_by_month = dfm.groupby("month")["value"].mean().reindex(order_months)
    fig_box.add_trace(go.Scatter(
        x=[m.strftime("%Y-%m") for m in order_months],
        y=mean_by_month.values,
        mode="lines+markers",
        name="Média mensal"
    ))

    fig_box.update_layout(
        template="plotly_white",
        yaxis_title="Taxa de Metano",
        margin=dict(l=10, r=10, t=30, b=10),
        height=420,
        boxmode="group"
    )
    st.plotly_chart(fig_box, use_container_width=True)
else:
    st.info("Sem dados suficientes para boxplots mensais.")
import streamlit.components.v1 as components

##########################################################################################################################
# ======= PDF ROBUSTO (client-side com Plotly.toImage + jsPDF) =======
import streamlit as st
from streamlit_js_eval import streamlit_js_eval

# Exponha valores que quer no cabeçalho do PDF (aproveita suas métricas)
def _safe(v):
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

m_taxa   = _safe(getv('Taxa Metano'))
m_inc    = _safe(getv('Incerteza'))
m_vento  = _safe(getv('Velocidade do Vento'))

# Elementos invisíveis só para o JS ler
st.markdown(
    f"""
    <div id="pdf_site" style="display:none">{site}</div>
    <div id="pdf_data" style="display:none">{selected_label}</div>
    <div id="pdf_taxa" style="display:none">{m_taxa}</div>
    <div id="pdf_inc" style="display:none">{m_inc}</div>
    <div id="pdf_vento" style="display:none">{m_vento}</div>
    """,
    unsafe_allow_html=True
)

st.markdown("### 📄 Exportar")
st.caption("Gera um PDF com título, métricas e os gráficos (sem Kaleido).")
if st.button("📄 Gerar PDF (robusto)", type="primary", use_container_width=True):
    streamlit_js_eval(js_expressions=r"""
(async () => {
  // Carrega jsPDF se necessário
  if (!window.jspdf) {
    await new Promise((res,rej)=>{
      const s=document.createElement('script');
      s.src='https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js';
      s.onload=res; s.onerror=rej; document.head.appendChild(s);
    });
  }
  // Garante Plotly global (normalmente já existe no Streamlit)
  if (!window.Plotly) {
    await new Promise((res,rej)=>{
      const s=document.createElement('script');
      s.src='https://cdn.plot.ly/plotly-2.29.1.min.js';
      s.onload=res; s.onerror=rej; document.head.appendChild(s);
    });
  }
  const { jsPDF } = window.jspdf;

  // Lê os dados para o cabeçalho
  const txt = (id)=> (document.getElementById(id)?.textContent || "—");
  const site  = txt('pdf_site');
  const data  = txt('pdf_data');
  const taxa  = txt('pdf_taxa');
  const inc   = txt('pdf_inc');
  const vento = txt('pdf_vento');

  // Encontra os gráficos Plotly da página (primeiros dois)
  const plots = Array.from(document.querySelectorAll('.js-plotly-plot')).slice(0, 2);
  // Exporta cada um para PNG (alta resolução)
  const images = [];
  for (const el of plots) {
    try {
      const url = await window.Plotly.toImage(el, {format:'png', width:1400, height:800, scale:2});
      images.push(url);
    } catch(e) {
      console.error('Falha ao exportar gráfico:', e);
    }
  }

  // Monta PDF A4
  const pdf = new jsPDF('p','pt','a4'); // 595x842 pt
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const margin = 36;
  let y = margin;

  // Cabeçalho
  pdf.setFont('helvetica','bold'); pdf.setFontSize(16);
  pdf.text('Geoportal de Metano — Relatório', margin, y); y += 18;
  pdf.setFont('helvetica','normal'); pdf.setFontSize(10);
  pdf.setTextColor(120);
  pdf.text(`Site: ${site}   |   Data: ${data}`, margin, y); y += 16;
  pdf.setTextColor(0);

  // Métricas
  pdf.setFont('helvetica','bold'); pdf.setFontSize(12);
  pdf.text('Métricas', margin, y); y += 16;
  pdf.setFont('helvetica','normal'); pdf.setFontSize(11);
  const linhas = [
    `• Taxa Metano: ${taxa}`,
    `• Incerteza: ${inc}`,
    `• Velocidade do Vento: ${vento}`
  ];
  for (const ln of linhas) { pdf.text(ln, margin, y); y += 14; }
  y += 6;

  // Insere imagens dos gráficos
  for (let i=0; i<images.length; i++) {
    const img = images[i];
    // carrega imagem para medir
    const im = new Image(); im.src = img;
    await new Promise((r, rr)=>{
      im.onload = r; im.onerror = rr;
    });
    const iw = im.naturalWidth, ih = im.naturalHeight;
    const maxW = pageW - 2*margin;
    const maxH = pageH - 2*margin - y;
    let w = maxW, h = ih * (w/iw);
    if (h > maxH) { h = maxH; w = iw * (h/ih); }

    // quebra de página se não couber
    if (y + h > pageH - margin) { pdf.addPage(); y = margin; }

    pdf.addImage(img, 'PNG', (pageW - w)/2, y, w, h, undefined, 'FAST');
    y += h + 12;

    if (i === 0 && images.length > 1) {
      pdf.setFont('helvetica','bold'); pdf.setFontSize(11);
      if (y + 18 > pageH - margin) { pdf.addPage(); y = margin; }
      pdf.text('Boxplots por mês + média mensal', margin, y);
      y += 14;
    }
  }

  // Rodapé
  pdf.setFont('helvetica','normal'); pdf.setFontSize(8); pdf.setTextColor(120);
  pdf.text('© Geoportal — Relatório gerado no navegador (Plotly.toImage + jsPDF)', pageW - margin, pageH - 10, {align:'right'});

  // Download
  const blob = pdf.output('blob');
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `relatorio_geoportal_${site}_${data}.pdf`.replace(/\s+/g,'_');
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  return "ok";
})()
""", key="make_pdf_plotly_toimage")
# ======= FIM PDF ROBUSTO =======
















