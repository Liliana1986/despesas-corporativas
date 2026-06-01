"""
Gestão Automática de Despesas Corporativas
OCR local — sem API keys, dados ficam no computador da empresa.
"""

import re
import streamlit as st
import pandas as pd
from extractor_local import process_document, tesseract_available, pdf2image_available
from reconciler import load_statement_from_excel, reconcile
from report import generate_report


def _guess_colaborador(filename: str) -> str:
    name = filename.rsplit(".", 1)[0]
    parts = name.replace("_", " ").replace("-", " ").split()
    candidates = [p for p in parts if len(p) > 2 and p[0].isupper()]
    return " ".join(candidates[:2]) if candidates else ""


st.set_page_config(
    page_title="Despesas Corporativas",
    page_icon="🧾",
    layout="wide",
)

st.markdown("""
<style>
    .main-header {
        background: linear-gradient(90deg, #1F4E79, #2E86AB);
        padding: 1.5rem 2rem;
        border-radius: 10px;
        color: white;
        margin-bottom: 1.5rem;
    }
    .status-ok   { background:#d4edda; border-radius:5px; padding:0.4rem 0.8rem; margin:2px 0; }
    .status-warn { background:#fff3cd; border-radius:5px; padding:0.4rem 0.8rem; margin:2px 0; }
    div[data-testid="stDataFrameResizable"] td { font-size: 13px; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>🧾 Gestão de Despesas Corporativas</h1>
    <p>OCR local · Sem API keys · Dados ficam na empresa · Gotelecom SA</p>
</div>
""", unsafe_allow_html=True)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("ℹ️ Estado do sistema")

    if tesseract_available():
        st.markdown('<div class="status-ok">✅ Tesseract OCR instalado</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-warn">⚠️ Tesseract não instalado</div>', unsafe_allow_html=True)

    if pdf2image_available():
        st.markdown('<div class="status-ok">✅ Poppler disponível</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-warn">⚠️ Poppler não disponível</div>', unsafe_allow_html=True)

    st.markdown('<div class="status-ok">✅ pdfplumber (PDFs digitais)</div>', unsafe_allow_html=True)

    st.divider()
    st.subheader("Opções")
    tolerancia = st.number_input(
        "Tolerância reconciliação (€)",
        min_value=0.0, max_value=10.0, value=0.10, step=0.05,
    )

    st.divider()
    st.markdown("""
**Como usar:**
1. Carrega as faturas (PDF ou imagem)
2. Clica **Processar Faturas**
3. Valida e corrige os dados
4. Carrega o extrato do cartão (opcional)
5. Clica **Reconciliar**
6. Descarrega o relatório Excel
""")


# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["📄 Faturas e Recibos", "🏦 Extrato do Cartão"])


# ── Tab 1: Faturas ─────────────────────────────────────────────────────────────
with tab1:
    st.subheader("Carregar Faturas e Recibos")

    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded_files = st.file_uploader(
            "Arrasta ou seleciona os ficheiros",
            type=["pdf", "jpg", "jpeg", "png", "bmp", "tiff"],
            accept_multiple_files=True,
        )
    with col2:
        colaborador_global = st.text_input(
            "Colaborador (opcional)",
            placeholder="ex: Manuel Pombo",
        )

    if uploaded_files:
        st.info(f"{len(uploaded_files)} ficheiro(s) carregado(s).")

        if st.button("🚀 Processar Faturas", type="primary", use_container_width=True):
            resultados = []
            progress = st.progress(0, text="A processar documentos...")

            for i, file in enumerate(uploaded_files):
                progress.progress((i + 1) / len(uploaded_files),
                                   text=f"A processar: {file.name}")
                colaborador = colaborador_global or _guess_colaborador(file.name)
                result = process_document(
                    file_bytes=file.read(),
                    filename=file.name,
                    colaborador=colaborador,
                )
                resultados.append(result)

            progress.empty()
            st.session_state["faturas"] = pd.DataFrame(resultados)
            n_ok  = sum(1 for r in resultados if not r.get("observacoes"))
            n_av  = len(resultados) - n_ok
            if n_av == 0:
                st.success(f"✅ {n_ok} documento(s) processado(s) com sucesso!")
            else:
                st.warning(f"✅ {n_ok} processado(s) · ⚠️ {n_av} com aviso")


# ── Tabela editável ────────────────────────────────────────────────────────────
if "faturas" in st.session_state:
    df = st.session_state["faturas"].copy()
    st.divider()
    st.subheader("📊 Despesas Extraídas")

    # Métricas
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total documentos", len(df))
    col2.metric("Documentos válidos",
                (df["documento_valido"] == "Sim").sum() if "documento_valido" in df.columns else 0)
    total_val = pd.to_numeric(df.get("valor_total", pd.Series(dtype=float)), errors="coerce").sum()
    col3.metric("Valor total", f"{total_val:,.2f} €")
    col4.metric("Não validados",
                (df["documento_valido"] == "Não Validado").sum() if "documento_valido" in df.columns else 0)

    st.caption("Podes corrigir e validar directamente na tabela. Clica numa célula para editar.")

    # Colunas a mostrar na ordem pedida
    col_order = [
        "documento", "colaborador", "documento_valido", "fornecedor",
        "nif_fornecedor", "numero_documento", "data_documento",
        "iva", "valor_total", "observacoes"
    ]
    col_labels = {
        "documento":        "Documento",
        "colaborador":      "Colaborador",
        "documento_valido": "Doc. Válido",
        "fornecedor":       "Fornecedor",
        "nif_fornecedor":   "NIF Fornecedor",
        "numero_documento": "Número Documento",
        "data_documento":   "Data Documento",
        "iva":              "IVA",
        "valor_total":      "Valor Total (€)",
        "observacoes":      "Observações",
    }

    show_cols = [c for c in col_order if c in df.columns]

    edited = st.data_editor(
        df[show_cols].rename(columns=col_labels),
        use_container_width=True,
        height=420,
        num_rows="dynamic",
        column_config={
            "Doc. Válido": st.column_config.SelectboxColumn(
                "Doc. Válido",
                options=["Sim", "Não", "Não Validado"],
                required=True,
            ),
            "Valor Total (€)": st.column_config.NumberColumn(
                "Valor Total (€)",
                format="%.2f €",
                min_value=0,
            ),
        },
    )

    # Guarda edições (mapeia de volta para nomes originais)
    inv_labels = {v: k for k, v in col_labels.items()}
    edited.columns = [inv_labels.get(c, c) for c in edited.columns]
    st.session_state["faturas_editadas"] = edited


# ── Tab 2: Extrato ─────────────────────────────────────────────────────────────
with tab2:
    st.subheader("Carregar Extrato do Cartão")
    st.caption("Aceita ficheiros Excel (.xlsx, .xls) ou CSV exportados do banco.")

    extrato_file = st.file_uploader(
        "Arrasta o extrato (Excel ou CSV)",
        type=["xlsx", "xls", "csv"],
        key="extrato_uploader",
    )

    if extrato_file:
        if st.button("📥 Carregar Extrato", type="secondary", use_container_width=True):
            with st.spinner("A ler o extrato..."):
                try:
                    extrato_df = load_statement_from_excel(
                        extrato_file.read(), extrato_file.name)
                    st.session_state["extrato"] = extrato_df
                    st.success(f"✅ {len(extrato_df)} movimento(s) carregados!")
                except Exception as e:
                    st.error(f"Erro ao ler extrato: {e}")

    if "extrato" in st.session_state:
        st.dataframe(st.session_state["extrato"], use_container_width=True, height=300)


# ── Relatório Final ────────────────────────────────────────────────────────────
st.divider()
st.subheader("📥 Relatório Final")

faturas_para_usar = st.session_state.get(
    "faturas_editadas", st.session_state.get("faturas"))

col1, col2 = st.columns(2)

with col1:
    if faturas_para_usar is None:
        st.info("Processa as faturas primeiro.")
    elif "extrato" in st.session_state:
        if st.button("🔄 Reconciliar e Gerar Relatório", type="primary",
                     use_container_width=True):
            with st.spinner("A reconciliar..."):
                reconciliacao = reconcile(
                    faturas_para_usar,
                    st.session_state["extrato"],
                    tolerancia=tolerancia,
                )
                st.session_state["reconciliacao"] = reconciliacao
                c = reconciliacao["conciliadas"]
                s = reconciliacao["sem_documento"]
                e = reconciliacao["sem_extrato"]
                ca, cb, cc = st.columns(3)
                ca.metric("✅ Conciliadas", len(c))
                cb.metric("⚠️ Sem documento", len(s))
                cc.metric("❌ Sem extrato", len(e))
    else:
        if st.button("📊 Exportar só despesas", type="secondary",
                     use_container_width=True):
            st.session_state["reconciliacao"] = None

with col2:
    if faturas_para_usar is not None:
        excel_data = generate_report(
            faturas_para_usar,
            st.session_state.get("reconciliacao"),
        )
        st.download_button(
            label="⬇️ Descarregar Excel",
            data=excel_data,
            file_name="despesas_corporativas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )

# ── Detalhes reconciliação ─────────────────────────────────────────────────────
if st.session_state.get("reconciliacao"):
    rec = st.session_state["reconciliacao"]
    st.divider()
    with st.expander("Ver detalhes da reconciliação", expanded=True):
        ta, tb, tc = st.tabs(["✅ Conciliadas", "⚠️ Sem Documento", "❌ Sem Extrato"])
        with ta:
            st.dataframe(rec["conciliadas"], use_container_width=True) \
                if not rec["conciliadas"].empty else st.info("Nenhuma.")
        with tb:
            if not rec["sem_documento"].empty:
                st.dataframe(rec["sem_documento"], use_container_width=True)
                st.download_button(
                    "📧 Exportar lista para colaboradores",
                    data=rec["sem_documento"].to_csv(
                        index=False, sep=";").encode("utf-8-sig"),
                    file_name="movimentos_sem_documento.csv",
                    mime="text/csv",
                )
            else:
                st.success("Todos os movimentos têm documento.")
        with tc:
            st.dataframe(rec["sem_extrato"], use_container_width=True) \
                if not rec["sem_extrato"].empty \
                else st.success("Todos os documentos têm correspondência.")
