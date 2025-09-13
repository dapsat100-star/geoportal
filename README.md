# Geoportal de Monitoramento de Metano

Este repositório contém:
- `bancodados.xlsx`: base de dados (uma planilha por site, com parâmetros e linha `Imagem`).
- `images/`: imagens correspondentes aos meses.
- `geoportal_streamlit.py`: dashboard em Streamlit para visualização estilo geoportal.

## Como rodar localmente

1. Instale as dependências:

```bash
pip install streamlit pandas openpyxl requests streamlit-folium folium
```

2. Rode o aplicativo:

```bash
streamlit run geoportal_streamlit.py
```

3. Abra o link local que o Streamlit mostrar (normalmente `http://localhost:8501`).

## Como usar o app

- No campo **RAW URL do Excel**, cole o link bruto do Excel hospedado no GitHub.  
  Exemplo:
  ```
  https://raw.githubusercontent.com/<seu-usuário>/<repo>/<branch>/bancodados.xlsx
  ```

- No campo **Base URL**, informe:
  ```
  https://raw.githubusercontent.com/<seu-usuário>/<repo>/<branch>
  ```

- Selecione o site (sheet), depois a data. O app exibirá:
  - KPIs (Taxa de Metano, Incerteza, Vento)
  - Observações e Satélite
  - Mapa com localização do site
  - Imagem da linha `Imagem` (pluma)
  - Galeria de imagens por data

## Observação importante
- GitHub diferencia maiúsculas/minúsculas em pastas (`images/` ≠ `Images/`).
- Garanta que os nomes no Excel e no repositório sejam consistentes.
