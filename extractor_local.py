"""
Extração local de dados de faturas/recibos.
Regras de negócio:
  - Cliente fixo: Gotelecom SA / NIF 516114905
  - Fornecedor: entidade emitente (NIF diferente de 516114905)
  - Campos: Documento Válido, Fornecedor, NIF Fornecedor,
            Número Documento, Data Documento, IVA, Valor Total
"""

import re
import io
import pdfplumber
from PIL import Image
from pathlib import Path

# ── Tesseract ──────────────────────────────────────────────────────────────────
try:
    import pytesseract
    import os as _os
    _tess_paths = [
        r"C:\Users\{}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe".format(
            _os.environ.get("USERNAME", "")),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for _p in _tess_paths:
        if _os.path.exists(_p):
            pytesseract.pytesseract.tesseract_cmd = _p
            break
    pytesseract.get_tesseract_version()
    TESSERACT_OK = True
except Exception:
    TESSERACT_OK = False

# ── pdf2image / Poppler ────────────────────────────────────────────────────────
try:
    from pdf2image import convert_from_bytes
    import os as _os
    _poppler_paths = [
        r"C:\poppler\poppler-26.02.0\Library\bin",
        r"C:\poppler\Library\bin",
        r"C:\Program Files\poppler\Library\bin",
        "/usr/bin",
        "/usr/local/bin",
    ]
    POPPLER_PATH = None
    for _p in _poppler_paths:
        _exe = "pdftoppm.exe" if _os.name == "nt" else "pdftoppm"
        if _os.path.exists(_os.path.join(_p, _exe)):
            POPPLER_PATH = _p if _os.name == "nt" else None
            break
        elif _os.name != "nt" and _os.path.exists(_os.path.join(_p, "pdftoppm")):
            POPPLER_PATH = None
            break
    PDF2IMAGE_OK = True if _os.name != "nt" else POPPLER_PATH is not None
except ImportError:
    PDF2IMAGE_OK = False
    POPPLER_PATH = None


# ── Constantes do cliente ──────────────────────────────────────────────────────
CLIENT_NIF    = "507413865"   # NIF Gotelecom SA
CLIENT_NIFS   = {"507413865", "516114905"}  # NIFs conhecidos (principal + alternativo)
CLIENT_NAMES  = {"gotelecom", "gotelecom sa", "gotelecom s.a", "gotelecom, sa"}


# ── Regex ──────────────────────────────────────────────────────────────────────

# NIF português válido (9 dígitos, começa por 1-9)
RE_NIF_LABELED = re.compile(
    r"(?:NIF|NIPC|Contribuinte|N\.º\s*Contrib\.?|Nif)[:\s#.]*(?:PT)?([1-9]\d{8})",
    re.IGNORECASE,
)
RE_NIF_BARE = re.compile(r"\b(?:PT)?([1-9]\d{8})\b")

# Data — vários formatos
RE_DATE_LABELED = re.compile(
    r"(?:data(?:\s+da?\s+(?:fatura|factura|emiss[aã]o|documento))?|"
    r"invoice\s*date|issue\s*date|emitido\s*em)[:\s]*"
    r"(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{2}[\/\-\.]\d{2})",
    re.IGNORECASE,
)
RE_DATE_BARE = re.compile(
    r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})\b"
)

# Número de fatura — padrões válidos
RE_INVOICE_LABELED = re.compile(
    r"(?:fatura\s*(?:recibo|simplificada)?|factura|recibo|invoice(?:\s*n[o°º]?)?|"
    r"n[oº°]?\s*(?:fatura|doc(?:umento)?)|doc(?:umento)?\s*n[oº°]?|"
    r"FT|FR|FS|RC|NC)\s*[:\s#\/]?\s*"
    r"((?:FT|FR|FS|RC|NC|INV|REC)[\/\s\-]?[A-Z0-9]{1,10}[\/\-][0-9]{1,10}"
    r"|[A-Z]{1,4}[\s]?[A-Z0-9]{1,6}[\/\-][0-9]{4,10}"
    r"|INV[\-][0-9]{4}[\-][0-9]{3,8})",
    re.IGNORECASE,
)
# Padrão directo para formatos conhecidos: FT 2026/123, FT4/4566, FR2026/789
RE_INVOICE_DIRECT = re.compile(
    r"\b((?:FT|FR|FS|RC|NC|REC|INV|VD)[A-Z0-9\/\s\-]{3,25}\/[0-9]{3,10})\b",
    re.IGNORECASE,
)

# Números a NÃO confundir com nº fatura
RE_EXCLUDE_INVOICE = re.compile(
    r"(?:iban|bic|swift|n[oº]?\s*cliente|n[oº]?\s*contribuinte|"
    r"n[oº]?\s*encomenda|refer[eê]ncia\s*(?:mb|multibanco|pagamento)|"
    r"capital\s*social)",
    re.IGNORECASE,
)

# Valor total
RE_TOTAL_PATTERNS = [
    re.compile(r"total\s*a\s*pagar[:\s€]*([0-9]+[.,][0-9]{2})", re.IGNORECASE),
    re.compile(r"valor\s*total[:\s€]*([0-9]+[.,][0-9]{2})", re.IGNORECASE),
    re.compile(r"total\s*(?:final|documento|geral|com\s*iva)[:\s€]*([0-9]+[.,][0-9]{2})", re.IGNORECASE),
    re.compile(r"grand\s*total[:\s€]*([0-9]+[.,][0-9]{2})", re.IGNORECASE),
    re.compile(r"amount\s*due[:\s€]*([0-9]+[.,][0-9]{2})", re.IGNORECASE),
    re.compile(r"TOTAL[:\s-]+([0-9]+[.,][0-9]{2})\s*€", re.IGNORECASE),
    re.compile(r"valor\s*entregue[:\s€]*([0-9]+[.,][0-9]{2})", re.IGNORECASE),
    re.compile(r"([0-9]+[.,][0-9]{2})\s*EUR\s*$", re.IGNORECASE | re.MULTILINE),
]

# IVA
RE_IVA_RATE  = re.compile(r"(?:iva|vat|tax)[^0-9\n]{0,15}?(\d{1,2})\s*%", re.IGNORECASE)
RE_IVA_VALUE = re.compile(
    r"(?:valor\s*(?:do\s*)?iva|iva|vat|tax)[^0-9€\n]{0,20}?([0-9]+[.,][0-9]{2})\s*(?:€|EUR)?",
    re.IGNORECASE,
)
RE_IVA_LABELED = re.compile(
    r"Valor\s*IVA[:\s€]*([0-9]+[.,][0-9]{2})\s*EUR",
    re.IGNORECASE,
)

RE_AMOUNT = re.compile(r"\b(\d{1,4}[.,]\d{2})\b")


# ── Utilidades ─────────────────────────────────────────────────────────────────

def _parse_money(s: str) -> float:
    s = str(s).replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _valid_amount(v: float) -> bool:
    return 0.01 < v < 50000.0


# ── OCR ────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    texts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:3]:
            t = page.extract_text()
            if t:
                texts.append(t)
    return "\n".join(texts)


def _preprocess(pil_img):
    from PIL import ImageFilter, ImageEnhance
    img = pil_img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    import statistics
    pixels = list(img.getdata())
    thr = statistics.median(pixels)
    return img.point(lambda p: 255 if p > thr else 0)


def _ocr(pil_img: Image.Image) -> str:
    cfg = r"--psm 6 --oem 3"
    for lang in ["por+eng", "eng"]:
        try:
            t = pytesseract.image_to_string(pil_img, lang=lang, config=cfg)
            if t.strip():
                return t
        except Exception:
            continue
    return ""


def extract_text_from_pdf_ocr(pdf_bytes: bytes) -> str:
    if not TESSERACT_OK:
        return ""
    texts = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:3]:
                try:
                    page_img = page.to_image(resolution=300)
                    buf = io.BytesIO()
                    page_img.save(buf, format="PNG")
                    buf.seek(0)
                    pil = Image.open(buf).convert("RGB")
                    t1 = _ocr(pil)
                    t2 = _ocr(_preprocess(pil))
                    combined = t1 + "\n" + t2
                    if combined.strip():
                        texts.append(combined)
                except Exception:
                    continue
    except Exception:
        pass
    return "\n".join(texts)


def extract_text_from_image(image_bytes: bytes) -> str:
    if not TESSERACT_OK:
        return ""
    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        t1 = _ocr(pil)
        t2 = _ocr(_preprocess(pil))
        return t1 + "\n" + t2
    except Exception:
        return ""


# ── Extracção de campos ────────────────────────────────────────────────────────

def _check_document_valid(text: str) -> str:
    """
    PASSO 1 — Verifica se o documento pertence à Gotelecom SA.
    Prioridade: NIF 516114905 (critério principal)
    Sim          = NIF 516114905 encontrado em qualquer parte do documento
    Sim          = Nome Gotelecom SA encontrado (se NIF não legível)
    Não Validado = nenhuma referência encontrada
    """
    # Verifica texto limpo (remove espaços/pontos/traços)
    text_clean = re.sub(r"[\s\-\.]", "", text)
    for nif in CLIENT_NIFS:
        if nif in text_clean:
            return "Sim"

    # Normaliza confusões comuns do OCR: S→5, O→0, I→1, l→1
    text_norm = (text_clean
                 .replace("S", "5").replace("s", "5")
                 .replace("O", "0").replace("o", "0")
                 .replace("I", "1").replace("l", "1"))
    for nif in CLIENT_NIFS:
        if nif in text_norm:
            return "Sim"

    # Pesquisa fuzzy: aceita 1 dígito errado em 9 (tolerância OCR)
    all_nifs = re.findall(r"[0-9]{9}", text_clean + text_norm)
    for found in all_nifs:
        for client_nif in CLIENT_NIFS:
            diffs = sum(a != b for a, b in zip(found, client_nif))
            if diffs <= 1:
                return "Sim"

    # Critério secundário — nome da empresa
    for name in CLIENT_NAMES:
        if name in text.lower():
            return "Sim"

    return "Não Validado"


def _extract_supplier_nif(text: str) -> str:
    """
    PASSO 2 — NIF do fornecedor (primeiro NIF que não seja o do cliente).
    """
    # Com prefixo NIF/NIPC
    for m in RE_NIF_LABELED.finditer(text):
        nif = m.group(1)
        if nif not in CLIENT_NIFS:
            return nif
    for nif in RE_NIF_BARE.findall(text):
        if nif not in CLIENT_NIFS:
            return nif
    return ""


def _extract_supplier_name(text: str, supplier_nif: str) -> str:
    """
    PASSO 2 — Nome do fornecedor: entidade no cabeçalho, não é o cliente.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Indicadores de empresa
    company_re = re.compile(
        r"\b(lda|lda\.|s\.?a\.?|unipessoal|sociedade|store|shop|"
        r"restaurante|hotel|caf[eé]|supermercado|comercial|"
        r"services|group|tecnologia|informatica|equip)\b",
        re.IGNORECASE,
    )
    # Padrões a ignorar
    skip_re = re.compile(
        r"^(atcud|qdwr|data|ref|nif|nipc|contribuinte|fatura|factura|"
        r"recibo|invoice|total|iva|vat|\d{1,2}[\/\.]\d{1,2}|rua\s|"
        r"avenida|largo|praca|apartado|codigo\s*postal|\d{4}-\d{3})",
        re.IGNORECASE,
    )
    client_re = re.compile("|".join(CLIENT_NAMES), re.IGNORECASE)

    # 1. Linha com indicador de empresa, que não seja cliente
    for line in lines[:20]:
        if len(line) < 4 or skip_re.match(line) or client_re.search(line):
            continue
        if company_re.search(line):
            clean = re.sub(r"[^\w\s\-&\.,ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]", "", line).strip()
            if len(clean) >= 4:
                return clean

    # 2. Se temos NIF do fornecedor, procura o nome nas linhas próximas
    if supplier_nif:
        for i, line in enumerate(lines):
            if supplier_nif in line:
                # Olha para as 3 linhas anteriores
                for prev in reversed(lines[max(0, i-3):i]):
                    if len(prev) >= 4 and not skip_re.match(prev) and not client_re.search(prev):
                        clean = re.sub(r"[^\w\s\-&\.,ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]", "", prev).strip()
                        if len(clean) >= 4:
                            return clean

    # 3. Fallback: primeira linha razoável que não seja cliente
    for line in lines[:10]:
        if len(line) < 4 or skip_re.match(line) or client_re.search(line):
            continue
        if re.match(r"^\d", line):
            continue
        clean = re.sub(r"[^\w\s\-&\.,ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]", "", line).strip()
        if len(clean) >= 4:
            return clean

    return ""


def _extract_date(text: str) -> str:
    """PASSO 3 — Data de emissão do documento."""
    # Com prefixo (mais fiável)
    m = RE_DATE_LABELED.search(text)
    if m:
        raw = m.group(1)
        return _normalise_date(raw)

    # Sem prefixo — primeira data com ano de 4 dígitos
    for d, mo, y in RE_DATE_BARE.findall(text):
        if len(y) == 4 and 2000 <= int(y) <= 2099:
            if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                return f"{int(d):02d}/{int(mo):02d}/{y}"

    # Formato AAAA-MM-DD
    m2 = re.search(r"\b(20\d{2})[\/\-\.](\d{2})[\/\-\.](\d{2})\b", text)
    if m2:
        return f"{m2.group(3)}/{m2.group(2)}/{m2.group(1)}"

    return ""


def _normalise_date(raw: str) -> str:
    for sep in ["/", "-", "."]:
        parts = raw.split(sep)
        if len(parts) == 3:
            a, b, c = parts
            if len(c) == 4:
                return f"{int(a):02d}/{int(b):02d}/{c}"
            elif len(a) == 4:
                return f"{int(c):02d}/{int(b):02d}/{a}"
    return raw


def _extract_invoice_number(text: str) -> str:
    """
    PASSO 4 — Número da fatura.
    Prioridade: padrões FT/FR/FS/INV directos, depois com prefixo textual.
    Exclui NIFs, IBANs, referências MB, nº cliente.
    """
    lines = text.split("\n")

    # Procura nas primeiras 30 linhas (zona do cabeçalho)
    header = "\n".join(lines[:30])

    # 1. Padrão directo forte: FT VD202609211AAA003/0000626
    for m in RE_INVOICE_DIRECT.finditer(header):
        candidate = m.group(1).strip()
        # Verifica que não está numa linha de exclusão
        line_ctx = _get_line(text, m.start())
        if not RE_EXCLUDE_INVOICE.search(line_ctx):
            return candidate

    # 2. Com prefixo textual
    for m in RE_INVOICE_LABELED.finditer(header):
        candidate = m.group(1).strip()
        line_ctx = _get_line(text, m.start())
        if not RE_EXCLUDE_INVOICE.search(line_ctx):
            return candidate

    # 3. Tenta no documento completo
    for m in RE_INVOICE_DIRECT.finditer(text):
        candidate = m.group(1).strip()
        line_ctx = _get_line(text, m.start())
        if not RE_EXCLUDE_INVOICE.search(line_ctx):
            return candidate

    return ""


def _get_line(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)]


def _extract_total(text: str) -> float:
    """PASSO 5 — Valor total."""
    def best(matches):
        vals = [_parse_money(m) for m in matches]
        valid = [v for v in vals if _valid_amount(v)]
        return max(valid) if valid else None

    for pattern in RE_TOTAL_PATTERNS:
        r = best(pattern.findall(text))
        if r:
            return r

    # Fallback: maior valor razoável no documento
    vals = [_parse_money(m) for m in RE_AMOUNT.findall(text) if _valid_amount(_parse_money(m))]
    return max(vals) if vals else 0.0


def _extract_iva(text: str) -> dict:
    """PASSO 6 — Taxa e valor do IVA."""
    result = {"taxa": "", "valor": ""}

    # Valor explícito "Valor IVA: 130,71 EUR"
    m = RE_IVA_LABELED.search(text)
    if m:
        result["valor"] = _parse_money(m.group(1))

    if not result["valor"]:
        m = RE_IVA_VALUE.search(text)
        if m:
            v = _parse_money(m.group(1))
            if _valid_amount(v):
                result["valor"] = v

    m = RE_IVA_RATE.search(text)
    if m:
        result["taxa"] = m.group(1) + "%"

    return result


# ── Função principal ────────────────────────────────────────────────────────────

def process_document(file_bytes: bytes, filename: str, colaborador: str = "") -> dict:
    """Processa um documento e devolve os campos estruturados."""
    result = {
        "documento":          filename,
        "colaborador":        colaborador,
        "documento_valido":   "Não Validado",
        "fornecedor":         "",
        "nif_fornecedor":     "",
        "numero_documento":   "",
        "data_documento":     "",
        "iva":                "",
        "valor_total":        "",
        "observacoes":        "",
    }

    fname = filename.lower()

    try:
        # Extrai texto
        if fname.endswith(".pdf"):
            text = extract_text_from_pdf(file_bytes)
            if len(text.strip()) < 30:
                text = extract_text_from_pdf_ocr(file_bytes)
                if not text.strip():
                    result["observacoes"] = "PDF digitalizado — instala Tesseract para OCR"
                    return result
        elif fname.endswith((".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")):
            text = extract_text_from_image(file_bytes)
            if not text.strip():
                result["observacoes"] = "Não foi possível extrair texto da imagem"
                return result
        else:
            result["observacoes"] = f"Formato não suportado: {filename}"
            return result

        # PASSO 1 — Validar documento
        result["documento_valido"] = _check_document_valid(text)

        # PASSO 2 — Fornecedor
        nif = _extract_supplier_nif(text)
        result["nif_fornecedor"] = nif
        result["fornecedor"]     = _extract_supplier_name(text, nif)

        # PASSO 4 — Número documento (antes da data para aproveitar contexto do cabeçalho)
        result["numero_documento"] = _extract_invoice_number(text)

        # PASSO 3 — Data
        result["data_documento"] = _extract_date(text)

        # PASSO 5 — Valor total
        total = _extract_total(text)
        result["valor_total"] = total if total > 0 else ""

        # PASSO 6 — IVA
        iva = _extract_iva(text)
        if iva["taxa"] and iva["valor"]:
            result["iva"] = f"{iva['taxa']} ({iva['valor']} €)"
        elif iva["taxa"]:
            result["iva"] = iva["taxa"]
        elif iva["valor"]:
            result["iva"] = f"{iva['valor']} €"

    except Exception as e:
        result["observacoes"] = str(e)

    return result


def tesseract_available() -> bool:
    return TESSERACT_OK


def pdf2image_available() -> bool:
    import os
    if _os.name != "nt":
        return PDF2IMAGE_OK
    return POPPLER_PATH is not None and os.path.exists(
        os.path.join(POPPLER_PATH, "pdftoppm.exe"))
