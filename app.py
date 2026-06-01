"""
Gestão Automática de Despesas Corporativas
OCR local — sem API keys, sem internet, dados ficam no computador.
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
    .status-ok  { background:#d4edda; border-radius:5px; padding:0.4rem 0.8rem; margin:2px 0; }
    .status-warn{ background:#fff3cd; border-radius:5px; padding:0.4rem 0.8rem; margin:2px 0; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>🧾 Gestão de Despesas Corporativas</h1>
    <p>OCR local · Sem API keys · Dados ficam no computador da empresa</p>
</div>
""", unsafe_allow_html=True)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("ℹ️ Estado do sistema")

    if tesseract_available():
        st.markdown('<div class="status-ok">✅ Tesseract OCR instalado</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-warn">⚠️ Tesseract não instalado<br><small>PDFs digitais funcionam. Para imagens e PDFs digitalizados, instala o Tesseract.</small></div>', unsafe_allow_html=True)

    if pdf2image_available():
        st.markdown('<div class="status-ok">✅ pdf2image disponível</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-warn">⚠️ pdf2image não disponível<br><small>Instala Poppler para processar PDFs digitalizados.</small></div>', unsafe_allow_html=True)

    st.markdown('<div class="status-ok">✅ pdfplumber (PDFs digitais)</div>', unsafe_allow_html=True)

    st.divider()
    st.subheader("Opções")
    tolerancia = st.number_input(
        "Tolerância de valor na reconciliação (€)",
        min_value=0.0, max_value=10.0, value=0.10, step=0.05,
        help="Diferença máxima aceite entre fatura e movimento do extrato.",
    )

    st.divider()
    st.markdown("""
**Como usar:**
1. Carrega as faturas (PDF ou imagem)
2. Clica **Processar Faturas**
3. Revê e corrige os dados se necessário
4. Carrega o extrato do cartão (Excel/CSV)
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
            help="PDFs digitais funcionam sempre. Para imagens e PDFs digitalizados é necessário o Tesseract OCR.",
        )
    with col2:
        colaborador_global = st.text_input(
            "Colaborador (opcional)",
            placeholder="ex: João Silva",
            help="Preenche se todos os documentos são do mesmo colaborador.",
        )

    if uploaded_files:
        st.info(f"{len(uploaded_files)} ficheiro(s) carregado(s).")

        if st.button("🚀 Processar Faturas", type="primary", use_container_width=True):
            resultados = []
            progress = st.progress(0, text="A processar documentos...")

            for i, file in enumerate(uploaded_files):
                progress.progress((i + 1) / len(uploaded_files), text=f"A processar: {file.name}")
                colaborador = colaborador_global or _guess_colaborador(file.name)
                result = process_document(
                    file_bytes=file.read(),
                    filename=file.name,
                    colaborador=colaborador,
                )
                resultados.append(result)

            progress.empty()
            st.session_state["faturas"] = pd.DataFrame(resultados)
            n_erros = sum(1 for r in resultados if r.get("erro"))
            n_ok = len(resultados) - n_erros
            if n_erros == 0:
                st.success(f"✅ {n_ok} documento(s) processado(s) com sucesso!")
            else:
                st.warning(f"✅ {n_ok} processado(s) · ⚠️ {n_erros} com aviso (ver coluna Erro)")


# Mostra tabela editável com resultados
if "faturas" in st.session_state:
    df = st.session_state["faturas"]
    st.divider()
    st.subheader("📊 Despesas Extraídas")
    st.caption("Podes corrigir os valores diretamente na tabela antes de exportar.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total documentos", len(df))
    col2.metric("Com aviso", df["erro"].notna().sum() if "erro" in df.columns else 0)
    total_val = pd.to_numeric(df.get("valor_total", pd.Series(dtype=float)), errors="coerce").sum()
    col3.metric("Valor total", f"{total_val:,.2f} €")
    col4.metric("Sem valor", (df["valor_total"] == "").sum() if "valor_total" in df.columns else 0)

    edit_cols = [c for c in [
        "colaborador", "ficheiro", "fornecedor", "nif_fornecedor",
        "data", "numero_fatura", "valor_sem_iva",
        "iva_taxa", "iva_valor", "valor_total", "erro"
    ] if c in df.columns]

    edited_df = st.data_editor(
        df[edit_cols].rename(columns={
            "colaborador": "Colaborador",
            "ficheiro": "Ficheiro",
            "fornecedor": "Fornecedor",
            "nif_fornecedor": "NIF Fornecedor",
            "data": "Data",
            "numero_fatura": "Nº Documento",
            "valor_sem_iva": "Valor s/ IVA (€)",
            "iva_taxa": "Taxa IVA",
            "iva_valor": "Valor IVA (€)",
            "valor_total": "Valor Total (€)",
            "erro": "Aviso",
        }),
        use_container_width=True,
        height=400,
        num_rows="dynamic",
    )
    # Guarda edições
    edited_df.columns = edit_cols
    st.session_state["faturas_editadas"] = edited_df


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
                    extrato_df = load_statement_from_excel(extrato_file.read(), extrato_file.name)
                    st.session_state["extrato"] = extrato_df
                    st.success(f"✅ {len(extrato_df)} movimento(s) carregados!")
                except Exception as e:
                    st.error(f"Erro ao ler extrato: {e}")
                    st.info("Certifica-te que o ficheiro tem colunas de Data, Descrição e Valor.")

    if "extrato" in st.session_state:
        st.dataframe(st.session_state["extrato"], use_container_width=True, height=300)

        # Mapeamento de colunas (caso o Excel tenha nomes diferentes)
        with st.expander("Ajustar colunas do extrato"):
            cols_extrato = list(st.session_state["extrato"].columns)
            st.caption("Se a reconciliação não funcionar, confirma aqui qual coluna é o quê.")
            c1, c2, c3 = st.columns(3)
            with c1:
                col_data = st.selectbox("Coluna de Data", cols_extrato,
                    index=next((i for i, c in enumerate(cols_extrato) if "data" in c.lower() or "date" in c.lower()), 0))
            with c2:
                col_desc = st.selectbox("Coluna de Descrição", cols_extrato,
                    index=next((i for i, c in enumerate(cols_extrato) if "desc" in c.lower() or "mov" in c.lower()), min(1, len(cols_extrato)-1)))
            with c3:
                col_valor = st.selectbox("Coluna de Valor", cols_extrato,
                    index=next((i for i, c in enumerate(cols_extrato) if "valor" in c.lower() or "amount" in c.lower() or "mont" in c.lower()), min(2, len(cols_extrato)-1)))

            if st.button("Aplicar mapeamento"):
                ext = st.session_state["extrato"].copy()
                ext = ext.rename(columns={col_data: "data", col_desc: "descricao", col_valor: "valor"})
                ext["data"] = pd.to_datetime(ext["data"], dayfirst=True, errors="coerce")
                ext["valor"] = pd.to_numeric(
                    ext["valor"].astype(str).str.replace(r"[^\d,.-]", "", regex=True).str.replace(",", "."),
                    errors="coerce"
                ).abs()
                st.session_state["extrato"] = ext[["data", "descricao", "valor"]]
                st.success("Mapeamento aplicado!")
                st.rerun()


# ── Relatório e Reconciliação ──────────────────────────────────────────────────
st.divider()
st.subheader("📥 Relatório Final")

faturas_para_usar = st.session_state.get("faturas_editadas", st.session_state.get("faturas"))

col1, col2 = st.columns(2)

with col1:
    if faturas_para_usar is None:
        st.info("Processa as faturas primeiro para gerar o relatório.")
    elif "extrato" in st.session_state:
        if st.button("🔄 Reconciliar e Gerar Relatório", type="primary", use_container_width=True):
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

                col_a, col_b, col_c = st.columns(3)
                col_a.metric("✅ Conciliadas", len(c))
                col_b.metric("⚠️ Sem documento", len(s))
                col_c.metric("❌ Sem extrato", len(e))
    else:
        if st.button("📊 Exportar só despesas", type="secondary", use_container_width=True):
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
        tab_a, tab_b, tab_c = st.tabs(["✅ Conciliadas", "⚠️ Sem Documento", "❌ Sem Extrato"])
        with tab_a:
            if not rec["conciliadas"].empty:
                st.dataframe(rec["conciliadas"], use_container_width=True)
            else:
                st.info("Nenhuma despesa conciliada.")
        with tab_b:
            if not rec["sem_documento"].empty:
                st.dataframe(rec["sem_documento"], use_container_width=True)
                # Botão para exportar lista de falta por colaborador
                st.download_button(
                    "📧 Exportar lista para enviar aos colaboradores",
                    data=rec["sem_documento"].to_csv(index=False, sep=";").encode("utf-8-sig"),
                    file_name="movimentos_sem_documento.csv",
                    mime="text/csv",
                )
            else:
                st.success("Todos os movimentos têm documento associado.")
        with tab_c:
            if not rec["sem_extrato"].empty:
                st.dataframe(rec["sem_extrato"], use_container_width=True)
            else:
                st.success("Todos os documentos têm correspondência no extrato.")


