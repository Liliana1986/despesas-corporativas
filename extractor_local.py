"""
Extração local de dados de faturas/recibos.
Usa pdfplumber (PDFs digitais) e Tesseract OCR (imagens/PDFs digitalizados).
Sem API keys, sem internet — tudo no computador.
"""

import re
import io
import pdfplumber
from PIL import Image
from pathlib import Path

# Tesseract é opcional — app funciona sem ele para PDFs digitais
try:
    import pytesseract
    import os

    # Detecta o executável do Tesseract no Windows automaticamente
    _tess_paths = [
        r"C:\Users\{}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe".format(os.environ.get("USERNAME", "")),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for _p in _tess_paths:
        if os.path.exists(_p):
            pytesseract.pytesseract.tesseract_cmd = _p
            break

    # Verifica se funciona
    pytesseract.get_tesseract_version()
    TESSERACT_OK = True
except Exception:
    TESSERACT_OK = False

try:
    from pdf2image import convert_from_bytes
    import os as _os
    # Configura o caminho do Poppler automaticamente
    _poppler_paths = [
        r"C:\poppler\poppler-26.02.0\Library\bin",
        r"C:\poppler\Library\bin",
        r"C:\Program Files\poppler\Library\bin",
        "/usr/bin",           # Linux (Streamlit Cloud)
        "/usr/local/bin",
    ]
    POPPLER_PATH = None
    for _p in _poppler_paths:
        _exe = "pdftoppm.exe" if _os.name == "nt" else "pdftoppm"
        if _os.path.exists(_os.path.join(_p, _exe)):
            POPPLER_PATH = _p if _os.name == "nt" else None  # Linux não precisa de path
            break
        elif _os.name != "nt" and _os.path.exists(_os.path.join(_p, "pdftoppm")):
            POPPLER_PATH = None  # Linux usa PATH do sistema
            break
    PDF2IMAGE_OK = True if _os.name != "nt" else POPPLER_PATH is not None
except ImportError:
    PDF2IMAGE_OK = False
    POPPLER_PATH = None


# ── NIFs/nomes a ignorar (são o cliente, não o fornecedor) ────────────────────
CLIENT_NIFS  = {"507413865", "516114905"}
CLIENT_NAMES = {"gotelecom", "gotelecom sa", "gotelecom s.a"}

# ── Padrões regex para faturas portuguesas ─────────────────────────────────────

RE_NIF = re.compile(
    r"(?:NIF|NIPC|Contribuinte|N\.º\s*Contrib\.?|Nif)[:\s#.]*(?:PT)?([1-9]\d{8})",
    re.IGNORECASE,
)
RE_NIF_BARE = re.compile(r"\b(?:PT)?([1-9]\d{8})\b")

RE_DATE = re.compile(
    r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})\b"
)

# Padrão de total — por ordem de prioridade (mais específico primeiro)
RE_TOTAL_STRICT = re.compile(
    r"TOTAL\s*[:\-]\s*([0-9]+[.,][0-9]{2})\s*€",
    re.IGNORECASE,
)
RE_TOTAL = re.compile(
    r"(?:total\s*a\s*pagar|total\s*com\s*iva|valor\s*total|montante\s*total|"
    r"total\s*fatura|total\s*factura|total\s*due|total\s*incl|"
    r"valor\s*entregue|montante\s*pago|valor\s*pago|"
    r"total\s*EUR|valor\s*EUR)"
    r"[:\s€EUR]*([0-9]+[.,][0-9]{2})",
    re.IGNORECASE,
)
RE_TOTAL_NEXTLINE = re.compile(
    r"(?:total\s*a\s*pagar|total\s*fatura|total\s*factura|valor\s*total)\s*\n\s*([0-9]+[.,][0-9]{2})",
    re.IGNORECASE,
)
# Padrão específico para "699,00 EUR" no final da fatura
RE_TOTAL_EUR = re.compile(
    r"([0-9]+[.,][0-9]{2})\s*EUR\s*$",
    re.IGNORECASE | re.MULTILINE,
)

RE_IVA_VALUE = re.compile(
    r"(?:iva|vat|imposto)[^0-9€\n]{0,20}?([0-9]+[.,][0-9]{2})\s*€?",
    re.IGNORECASE,
)

RE_IVA_RATE = re.compile(
    r"(?:iva|vat)\s*(?:a\s*)?(\d{1,2})\s*%",
    re.IGNORECASE,
)

RE_AMOUNT = re.compile(r"\b(\d{1,4}[.,]\d{2})\s*€?\b")

RE_INVOICE_NUM = re.compile(
    r"(?:fatura|factura|recibo|invoice|nro[:\s]*|n\.?[oº°]?\s*(?:fatura|ft|fs|fr)?)[:\s#]*"
    r"([A-Z]{1,4}[\s]?[A-Z0-9\/\-]{3,20})",
    re.IGNORECASE,
)


# ── Extração de texto ──────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrai texto de PDF digital com pdfplumber. Rápido e preciso."""
    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:3]:  # primeiras 3 páginas chegam
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n".join(text_parts)


def extract_text_from_image_bytes(image_bytes: bytes) -> str:
    """OCR numa imagem usando Tesseract."""
    if not TESSERACT_OK:
        return ""
    img = Image.open(io.BytesIO(image_bytes))
    # Tesseract com dicionário PT+EN
    try:
        return pytesseract.image_to_string(img, lang="por+eng")
    except Exception:
        try:
            return pytesseract.image_to_string(img, lang="eng")
        except Exception:
            return pytesseract.image_to_string(img)


def _preprocess_image(pil_img: Image.Image) -> Image.Image:
    """Pré-processamento para melhorar OCR: escala de cinzas, contraste, binarização."""
    from PIL import ImageFilter, ImageEnhance, ImageOps

    # Converte para escala de cinzas
    img = pil_img.convert("L")

    # Aumenta contraste
    img = ImageEnhance.Contrast(img).enhance(2.0)

    # Remove ruído
    img = img.filter(ImageFilter.MedianFilter(size=3))

    # Binarização adaptativa (threshold de Otsu simulado)
    import statistics
    pixels = list(img.getdata())
    threshold = statistics.median(pixels)
    img = img.point(lambda p: 255 if p > threshold else 0)

    return img


def _ocr_image(pil_img: Image.Image) -> str:
    """Aplica OCR numa imagem PIL com configuração optimizada para faturas."""
    # Configuração Tesseract: PSM 6 = bloco uniforme de texto
    custom_config = r"--psm 6 --oem 3"

    for lang in ["por+eng", "eng"]:
        try:
            text = pytesseract.image_to_string(pil_img, lang=lang, config=custom_config)
            if text.strip():
                return text
        except Exception:
            continue

    return pytesseract.image_to_string(pil_img)


def extract_text_from_pdf_ocr(pdf_bytes: bytes) -> str:
    """
    Converte páginas PDF em imagem usando pdfplumber (sem Poppler)
    e aplica Tesseract OCR com pré-processamento de imagem.
    """
    if not TESSERACT_OK:
        return ""

    texts = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:3]:
                try:
                    # Renderiza a alta resolução para melhor OCR
                    page_img = page.to_image(resolution=300)
                    img_buffer = io.BytesIO()
                    page_img.save(img_buffer, format="PNG")
                    img_buffer.seek(0)
                    pil_img = Image.open(img_buffer).convert("RGB")

                    # OCR na imagem original
                    text_original = _ocr_image(pil_img)

                    # OCR na imagem pré-processada (melhor para valores e texto pequeno)
                    pil_preprocessed = _preprocess_image(pil_img)
                    text_preprocessed = _ocr_image(pil_preprocessed)

                    # Combina os dois resultados — usa o que tiver mais conteúdo
                    text = text_original if len(text_original) >= len(text_preprocessed) else text_preprocessed

                    # Adiciona ambos para maximizar extracção de valores
                    combined = text_original + "\n" + text_preprocessed
                    if combined.strip():
                        texts.append(combined)

                except Exception:
                    continue
    except Exception:
        pass

    return "\n".join(texts)


# ── Parsing dos campos ─────────────────────────────────────────────────────────

def _parse_money(s: str) -> float:
    """Converte string de valor monetário para float."""
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _extract_date(text: str) -> str:
    """Extrai a data mais provável do documento."""
    matches = RE_DATE.findall(text)
    if not matches:
        return ""
    # Prefere datas com ano de 4 dígitos
    for d, m, y in matches:
        if len(y) == 4 and 2000 <= int(y) <= 2099:
            if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
                return f"{int(d):02d}/{int(m):02d}/{y}"
    # Fallback: primeira data encontrada
    d, m, y = matches[0]
    y = "20" + y if len(y) == 2 else y
    return f"{int(d):02d}/{int(m):02d}/{y}"


def _extract_total(text: str) -> float:
    """Extrai o valor total do documento."""
    def best(matches):
        values = [_parse_money(m) for m in matches]
        valid = [v for v in values if 0 < v < 50000]
        return max(valid) if valid else None

    # 1. "TOTAL: 78,50 €" — padrão mais específico
    r = best(RE_TOTAL_STRICT.findall(text))
    if r:
        return r

    # 2. "Total a pagar / valor entregue / valor pago" + valor
    r = best(RE_TOTAL.findall(text))
    if r:
        return r

    # 3. Valor na linha a seguir ao total
    r = best(RE_TOTAL_NEXTLINE.findall(text))
    if r:
        return r

    # 4. Padrão "699,00 EUR" no fim de linha
    r = best(RE_TOTAL_EUR.findall(text))
    if r:
        return r

    # 5. Fallback: maior valor monetário razoável
    amounts = [_parse_money(m) for m in RE_AMOUNT.findall(text)]
    amounts = [a for a in amounts if 0 < a < 50000]
    return max(amounts) if amounts else 0.0


def _extract_iva(text: str) -> dict:
    """Extrai taxa e valor de IVA."""
    result = {"taxa": "", "valor": ""}
    rate_match = RE_IVA_RATE.search(text)
    if rate_match:
        result["taxa"] = rate_match.group(1) + "%"
    val_match = RE_IVA_VALUE.search(text)
    if val_match:
        result["valor"] = _parse_money(val_match.group(1))
    return result


def _extract_nif(text: str) -> str:
    """Extrai NIF/NIPC do fornecedor (ignora NIFs do cliente)."""
    # Tenta com prefixo NIF/NIPC
    for m in RE_NIF.finditer(text):
        nif = m.group(1)
        if nif not in CLIENT_NIFS:
            return nif
    # Tenta NIFs sem prefixo
    nifs = RE_NIF_BARE.findall(text)
    valid = [n for n in nifs if n[0] in "123456789" and len(n) == 9 and n not in CLIENT_NIFS]
    return valid[0] if valid else ""


def _extract_supplier(text: str) -> str:
    """Extrai o nome do fornecedor."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Padrões que indicam linha do fornecedor
    company_indicators = re.compile(
        r"\b(lda|sa|lda\.|s\.a\.|unipessoal|sociedade|store|shop|"
        r"restaurante|hotel|cafe|supermercado|comercial|services|group)\b",
        re.IGNORECASE,
    )

    # Ignora linhas manuscritas típicas (ID:, DEP:, MERCADO:, iniciais)
    skip_patterns = re.compile(
        r"^(id\s*[:=]|dep\s*[:=]|mercado\s*[:=]|m\.\s*\w+|"
        r"atcud|qdwr|ahoo|fatura\s+simpl|contribuinte|nif\s*[:=]|"
        r"\d{4}-\d{2}-\d{2}|\d{1,2}[\/\.]\d{1,2}[\/\.]\d{2,4}|"
        r"[a-z]\.\s*[a-z])",
        re.IGNORECASE,
    )

    # 1. Procura linha com indicador de empresa
    for line in lines[:15]:
        if len(line) < 4 or skip_patterns.match(line):
            continue
        if company_indicators.search(line):
            # Remove caracteres OCR corrompidos do início
            clean = re.sub(r"^[^a-zA-Z0-9À-ÿ]+", "", line).strip()
            if len(clean) >= 4:
                return clean

    # 2. Procura linha que parece nome de estabelecimento (maiúsculas, sem números)
    for line in lines[:12]:
        if len(line) < 5 or skip_patterns.match(line):
            continue
        if re.match(r"^\d", line):  # começa com número
            continue
        # Linha maiúscula sem caracteres estranhos
        clean = re.sub(r"[^\w\s\-&\.À-ÿ]", "", line).strip()
        if len(clean) >= 5 and not re.match(r"^\d", clean):
            return clean

    return lines[0] if lines else ""


def _extract_description(text: str) -> str:
    """Infere descrição a partir do texto."""
    keywords = {
        "combustível": ["combustível", "gasolina", "gasóleo", "bp ", "galp", "repsol", "prio"],
        "alimentação": ["restaurante", "cafe", "café", "supermercado", "refeição", "almoço", "jantar", "mcdonald", "pizza"],
        "alojamento": ["hotel", "hostel", "alojamento", "airbnb", "booking"],
        "transporte": ["uber", "bolt", "taxi", "táxi", "comboio", "metro", "cp ", "rodoviária", "autocarros"],
        "telecomunicações": ["nos ", "meo ", "vodafone", "nowo", "internet", "telemóvel", "telefone"],
        "material escritório": ["staples", "fnac", "worten", "leroy", "papel", "toner", "cartouche"],
        "serviços": ["advogad", "contabil", "consult", "rendas", "renda"],
    }
    text_lower = text.lower()
    for desc, words in keywords.items():
        if any(w in text_lower for w in words):
            return desc.capitalize()
    return "Despesa"


def _extract_invoice_number(text: str) -> str:
    """Extrai número de fatura/recibo."""
    m = RE_INVOICE_NUM.search(text)
    return m.group(1).strip() if m else ""


# ── Função principal ───────────────────────────────────────────────────────────

def process_document(file_bytes: bytes, filename: str, colaborador: str = "") -> dict:
    """
    Processa um documento e devolve os dados extraídos.
    Funciona para PDFs digitais, PDFs digitalizados e imagens.
    """
    result = {
        "ficheiro": filename,
        "colaborador": colaborador,
        "fornecedor": "",
        "nif_fornecedor": "",
        "data": "",
        "numero_fatura": "",
        "valor_sem_iva": "",
        "iva_taxa": "",
        "iva_valor": "",
        "valor_total": "",
        "erro": None,
    }

    fname = filename.lower()

    try:
        if fname.endswith(".pdf"):
            # 1. Tenta extração direta (PDF digital)
            text = extract_text_from_pdf(file_bytes)

            # 2. Se texto insuficiente, tenta OCR
            if len(text.strip()) < 30:
                ocr_text = extract_text_from_pdf_ocr(file_bytes)
                if ocr_text.strip():
                    text = ocr_text
                elif not text.strip():
                    # Nenhum texto conseguido — indica aviso mas continua
                    result["erro"] = (
                        "PDF digitalizado: precisas do Poppler para OCR completo. "
                        "Descarrega em: github.com/oschwartz10612/poppler-windows/releases"
                    )
                    return result
                # Se pdfplumber extraiu alguma coisa, usa mesmo que curto

        elif fname.endswith((".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")):
            text = extract_text_from_image_bytes(file_bytes)
            if not text.strip():
                if not TESSERACT_OK:
                    result["erro"] = "Tesseract OCR não instalado. Necessário para processar imagens."
                else:
                    result["erro"] = "Não foi possível extrair texto da imagem."
                return result
        else:
            result["erro"] = f"Formato não suportado: {filename}"
            return result

        # Extrai campos
        result["fornecedor"] = _extract_supplier(text)
        result["nif_fornecedor"] = _extract_nif(text)
        result["data"] = _extract_date(text)
        result["numero_fatura"] = _extract_invoice_number(text)

        total = _extract_total(text)
        result["valor_total"] = total if total > 0 else ""

        iva = _extract_iva(text)
        result["iva_taxa"] = iva["taxa"]
        result["iva_valor"] = iva["valor"] if iva["valor"] else ""

        # Valor sem IVA
        if total > 0 and iva["valor"]:
            try:
                result["valor_sem_iva"] = round(total - float(iva["valor"]), 2)
            except Exception:
                result["valor_sem_iva"] = ""

    except Exception as e:
        result["erro"] = str(e)

    return result


def tesseract_available() -> bool:
    return TESSERACT_OK


def pdf2image_available() -> bool:
    """Verifica se Poppler está disponível no caminho configurado."""
    import os
    if POPPLER_PATH and os.path.exists(os.path.join(POPPLER_PATH, "pdftoppm.exe")):
        return True
    return PDF2IMAGE_OK
