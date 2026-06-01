"""
Reconciliação entre despesas extraídas e movimentos do extrato do cartão.
Suporta extrato em Excel/CSV.
"""

import pandas as pd
import io
import re


STATEMENT_PROMPT = """
Este e um extrato bancario de cartao de credito ou debito.
Extrai TODOS os movimentos/transacoes e devolve um JSON com esta estrutura:
{
  "movimentos": [
    {
      "data": "DD/MM/AAAA",
      "descricao": "descricao do movimento",
      "valor": 0.00
    }
  ]
}

Regras:
- Inclui todos os movimentos, mesmo que repetidos
- O valor deve ser positivo (debito/pagamento)
- Ignora movimentos de credito (devolucoes, pagamentos da fatura) se nao forem relevantes
- Devolve APENAS o JSON
"""


def load_statement_from_pdf(pdf_bytes: bytes, api_key: str) -> pd.DataFrame:
    """Carrega extrato de PDF usando Gemini para interpretar."""
    client = genai.Client(api_key=api_key)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        first_page = pdf.pages[0]
        page_image = first_page.to_image(resolution=150)
        img_bytes = io.BytesIO()
        page_image.save(img_bytes, format="PNG")
        img_bytes.seek(0)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes.read(), mime_type="image/png"),
            STATEMENT_PROMPT,
        ],
    )

    text = re.sub(r"```(?:json)?", "", response.text).strip()
    data = json.loads(text)
    df = pd.DataFrame(data["movimentos"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce")
    return df


def load_statement_from_excel(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Carrega extrato de Excel ou CSV.
    Assume colunas: Data, Descricao/Descrição, Valor (flexível com nomes similares).
    """
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
    else:
        df = pd.read_excel(io.BytesIO(file_bytes))

    # Normaliza nomes das colunas
    df.columns = [_normalize_col(c) for c in df.columns]

    col_map = {}
    for col in df.columns:
        if re.search(r"data|date", col):
            col_map["data"] = col
        elif re.search(r"desc|movimento|comerciante|estabelecimento", col):
            col_map["descricao"] = col
        elif re.search(r"valor|amount|montante|debito|débito", col):
            col_map["valor"] = col

    df = df.rename(columns={v: k for k, v in col_map.items()})

    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], dayfirst=True, errors="coerce")
    if "valor" in df.columns:
        df["valor"] = (
            df["valor"]
            .astype(str)
            .str.replace(r"[^\d,.-]", "", regex=True)
            .str.replace(",", ".")
        )
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce").abs()

    cols = [c for c in ["data", "descricao", "valor"] if c in df.columns]
    return df[cols].dropna(subset=["valor"])


def reconcile(faturas_df: pd.DataFrame, extrato_df: pd.DataFrame, tolerancia: float = 0.05) -> dict:
    """
    Compara faturas com movimentos do extrato.
    Tolerância: diferença máxima de valor aceite (€).
    """
    faturas_df = faturas_df.copy()
    extrato_df = extrato_df.copy()

    faturas_df["valor_num"] = pd.to_numeric(faturas_df.get("valor_total", 0), errors="coerce").fillna(0)
    faturas_df["data_dt"] = pd.to_datetime(faturas_df.get("data", ""), dayfirst=True, errors="coerce")

    conciliadas = []
    sem_documento = []
    sem_extrato = []

    extrato_usado = [False] * len(extrato_df)

    for _, fatura in faturas_df.iterrows():
        match_idx = _find_best_match(fatura, extrato_df, extrato_usado, tolerancia)
        if match_idx is not None:
            extrato_usado[match_idx] = True
            mov = extrato_df.iloc[match_idx]
            conciliadas.append({
                "colaborador": fatura.get("colaborador", ""),
                "ficheiro": fatura.get("ficheiro", ""),
                "fornecedor": fatura.get("fornecedor", ""),
                "data_fatura": fatura.get("data", ""),
                "valor_fatura": fatura["valor_num"],
                "data_extrato": mov.get("data", ""),
                "descricao_extrato": mov.get("descricao", ""),
                "valor_extrato": mov.get("valor", ""),
                "estado": "Conciliada"
            })
        else:
            sem_extrato.append({
                "colaborador": fatura.get("colaborador", ""),
                "ficheiro": fatura.get("ficheiro", ""),
                "fornecedor": fatura.get("fornecedor", ""),
                "data_fatura": fatura.get("data", ""),
                "valor_fatura": fatura["valor_num"],
                "estado": "Sem movimento no extrato"
            })

    for i, mov in extrato_df.iterrows():
        if not extrato_usado[i]:
            sem_documento.append({
                "data_extrato": mov.get("data", ""),
                "descricao_extrato": mov.get("descricao", ""),
                "valor_extrato": mov.get("valor", ""),
                "estado": "Sem documento"
            })

    return {
        "conciliadas": pd.DataFrame(conciliadas),
        "sem_documento": pd.DataFrame(sem_documento),
        "sem_extrato": pd.DataFrame(sem_extrato),
    }


def _find_best_match(fatura, extrato_df, usado, tolerancia):
    """Encontra o movimento do extrato que melhor corresponde à fatura."""
    valor_fatura = fatura["valor_num"]
    data_fatura = fatura["data_dt"]

    best_idx = None
    best_score = float("inf")

    for i, mov in extrato_df.iterrows():
        if usado[i]:
            continue

        valor_mov = float(mov.get("valor", 0) or 0)
        diff_valor = abs(valor_fatura - valor_mov)

        if diff_valor > tolerancia and diff_valor > valor_fatura * 0.02:
            continue

        data_mov = mov.get("data")
        diff_dias = 0
        if pd.notna(data_fatura) and pd.notna(data_mov):
            diff_dias = abs((data_fatura - pd.Timestamp(data_mov)).days)

        score = diff_valor * 10 + diff_dias
        if score < best_score:
            best_score = score
            best_idx = i

    return best_idx


def _normalize_col(col: str) -> str:
    return str(col).lower().strip().replace(" ", "_").replace("ç", "c").replace("ã", "a").replace("ê", "e")
