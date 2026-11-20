import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from .. import config, database

log = logging.getLogger(__name__)

LABELS = {
    "invoice_number": ["invoice number", "invoice no", "invoice nr", "invoice #", "factura no", "factura n",
                       "numero de factura", "rechnungsnummer", "rechnung nr", "numero de facture", "facture no"],
    "invoice_date": ["invoice date", "date", "fecha", "rechnungsdatum", "datum", "date de facture"],
    "po_number": ["po reference", "po number", "po no", "po", "purchase order", "referencia de pedido",
                  "orden de compra", "pedido", "bestellnummer", "bestellung", "bon de commande"],
    "vendor_id": ["vendor id", "vendor code", "supplier id", "lieferantennummer", "codigo proveedor"],
    "vendor_name": ["vendor", "supplier", "proveedor", "lieferant", "fournisseur"],
    "currency": ["currency", "moneda", "wahrung", "devise"],
    "subtotal_amount": ["subtotal", "sub total", "sub-total", "zwischensumme", "base imponible",
                        "sous-total", "net amount"],
    "tax_amount": ["tax", "vat", "mwst", "ust", "iva", "tva", "impuesto", "mehrwertsteuer"],
    "total_amount": ["total amount due", "total amount", "total due", "grand total", "total", "amount due",
                     "gesamtbetrag", "importe total", "montant total"],
    "description": ["description", "descripcion", "beschreibung"],
    "quantity": ["quantity", "qty", "cantidad", "menge"],
    "unit_price": ["unit price", "precio unitario", "einzelpreis", "price"],
}

MONTHS = {
    "jan": 1, "january": 1, "enero": 1, "januar": 1, "janvier": 1,
    "feb": 2, "february": 2, "febrero": 2, "februar": 2, "fevrier": 2,
    "mar": 3, "march": 3, "marzo": 3, "marz": 3, "mars": 3,
    "apr": 4, "april": 4, "abril": 4, "avril": 4,
    "may": 5, "mayo": 5, "mai": 5,
    "jun": 6, "june": 6, "junio": 6, "juni": 6, "juin": 6,
    "jul": 7, "july": 7, "julio": 7, "juli": 7, "juillet": 7,
    "aug": 8, "august": 8, "agosto": 8, "aout": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9, "septembre": 9,
    "oct": 10, "october": 10, "octubre": 10, "oktober": 10, "octobre": 10,
    "nov": 11, "november": 11, "noviembre": 11, "novembre": 11,
    "dec": 12, "december": 12, "diciembre": 12, "dezember": 12, "dez": 12, "decembre": 12,
}

CURRENCY_CODES = ["USD", "EUR", "GBP", "INR", "JPY", "CHF", "CAD", "AUD", "CNY", "SGD"]

ITEM_CODE = re.compile(r"\b[A-Z]{2,5}-?\d{2,6}\b")


def fold(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def to_decimal(text):
    if text is None:
        return None
    numbers = re.findall(r"-?\d[\d.,]*", str(text))
    if not numbers:
        return None
    number = numbers[-1].rstrip(".,")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        before, _, after = number.rpartition(",")
        if len(after) <= 2 and number.count(",") == 1:
            number = before + "." + after
        else:
            number = number.replace(",", "")
    try:
        return Decimal(number)
    except InvalidOperation:
        return None


def to_date(text):
    if not text:
        return None
    t = fold(str(text)).replace(" de ", " ")
    try:
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", t)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if month > 12:
                day, month = month, day
            return date(year, month, day)
        m = re.search(r"(\d{1,2})[\s.-]+([a-z]+)\.?[\s-]+(\d{4})", t)
        if m and m.group(2) in MONTHS:
            return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
        m = re.search(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", t)
        if m and m.group(1) in MONTHS:
            return date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None
    return None


def label_values(lines, field):
    values = []
    for line in lines:
        if "|" in line:
            continue
        folded = fold(line)
        for label in LABELS[field]:
            if not folded.startswith(label):
                continue
            next_char = folded[len(label):len(label) + 1]
            if next_char.isalnum():
                continue
            if ":" in line:
                values.append(line.split(":", 1)[1].strip())
            else:
                values.append(line[len(label):].strip())
            break
    return values


def first_text(lines, field):
    for value in label_values(lines, field):
        if value:
            return value.split()[0] if field in ("invoice_number", "po_number", "vendor_id") else value
    return None


def first_amount(lines, field):
    for value in label_values(lines, field):
        amount = to_decimal(value)
        if amount is not None:
            return amount
    return None


def find_currency(lines, text, symbol_map):
    label = first_text(lines, "currency")
    if label:
        return label.upper()[:3]
    for code in CURRENCY_CODES:
        if re.search(rf"\b{code}\b", text):
            return code
    for symbol, code in symbol_map.items():
        if symbol in text:
            return code
    return None


def find_vendor_name(lines):
    name_lines = [line for line in lines if not label_values([line], "vendor_id")]
    name = first_text(name_lines, "vendor_name")
    if name:
        return name
    for line in lines[:5]:
        word = fold(line)
        if re.search(r"[a-z]{3}", word) and not word.startswith(("invoice", "factura", "rechnung", "facture")):
            return line.strip()
    return None


def is_number(cell):
    return re.fullmatch(r"[^\d]{0,3}-?\d[\d.,]*[^\d]{0,3}", cell.strip()) is not None


def find_line_items(lines):
    items = []

    for line in lines:
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 4 or not all(is_number(c) for c in cells[-3:]):
            continue
        text_cells = cells[:-3]
        code = None
        if ITEM_CODE.fullmatch(text_cells[0]):
            code = text_cells.pop(0)
        items.append({
            "item_code": code,
            "description": " ".join(text_cells) or None,
            "quantity": to_decimal(cells[-3]),
            "unit_price": to_decimal(cells[-2]),
            "line_total": to_decimal(cells[-1]),
        })

    if not items:
        row = re.compile(r"^([A-Za-z].*?)\s+(\d[\d.,]*)\s+(\d[\d.,]*)\s+(\d[\d.,]*)$")
        for line in lines:
            m = row.match(line.strip())
            if not m:
                continue
            qty, price, total = to_decimal(m.group(2)), to_decimal(m.group(3)), to_decimal(m.group(4))
            if qty is None or price is None or total is None:
                continue
            description = m.group(1).strip()
            code_match = ITEM_CODE.search(description)
            code = code_match.group(0) if code_match else None
            if code:
                description = description.replace(code, "").strip()
            items.append({"item_code": code, "description": description, "quantity": qty,
                          "unit_price": price, "line_total": total})

    if not items:
        qty = first_amount(lines, "quantity")
        price = first_amount(lines, "unit_price")
        if qty is not None and price is not None:
            items.append({"item_code": None, "description": first_text(lines, "description"),
                          "quantity": qty, "unit_price": price, "line_total": (qty * price).quantize(Decimal("0.01"))})

    for number, item in enumerate(items, start=1):
        item["line_number"] = number
    return items


def find_tax_rate(lines):
    for line in lines:
        folded = fold(line)
        if any(folded.startswith(label) for label in LABELS["tax_amount"]):
            match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", line)
            if match:
                return to_decimal(match.group(1))
    return None


def parse_text(text, symbol_map):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    po_number = first_text(lines, "po_number")
    if not po_number:
        match = re.search(r"\bPO-?\d+\b", text)
        po_number = match.group(0) if match else None

    return {
        "invoice_number": first_text(lines, "invoice_number"),
        "invoice_date": to_date(first_text(lines, "invoice_date")),
        "vendor_name": find_vendor_name(lines),
        "vendor_id": first_text(lines, "vendor_id"),
        "po_number": po_number,
        "currency": find_currency(lines, text, symbol_map),
        "subtotal_amount": first_amount(lines, "subtotal_amount"),
        "tax_amount": first_amount(lines, "tax_amount"),
        "total_amount": first_amount(lines, "total_amount"),
        "line_items": find_line_items(lines),
        "extras": {"tax_rate_percent": find_tax_rate(lines)},
    }


def parse_invoice(raw_text, translated_text, symbol_map):
    invoice = parse_text(raw_text, symbol_map)
    if translated_text:
        english = parse_text(translated_text, symbol_map)
        for field, value in english.items():
            if not invoice[field] and value:
                invoice[field] = value
    return invoice


HEADER_CORRECTION_FIELDS = frozenset({
    "invoice_number", "invoice_date", "vendor_name", "vendor_id", "po_number",
    "currency", "subtotal_amount", "tax_amount", "total_amount",
})
LINE_CORRECTION_FIELDS = frozenset({
    "item_code", "description", "quantity", "unit_price", "line_total",
})


def apply_corrections(invoice, corrections):
    applied = []
    for c in corrections:
        field = c["field_name"]
        allowed = LINE_CORRECTION_FIELDS if c["scope"] == "line_item" else HEADER_CORRECTION_FIELDS
        if c["scope"] not in ("header", "line_item") or field not in allowed:
            log.warning("Ignoring unsupported correction field %s", field)
            continue
        value = c["corrected_value"]
        if field in ("subtotal_amount", "tax_amount", "total_amount", "quantity", "unit_price", "line_total"):
            value = to_decimal(value)
        elif field == "invoice_date":
            value = to_date(value)
        if value is None:
            log.warning("Ignoring correction %s=%r, it could not be read", field, c["corrected_value"])
            continue

        if c["scope"] == "line_item":
            for item in invoice["line_items"]:
                if item["line_number"] == c["line_number"]:
                    item[field] = value
                    applied.append(c)
        elif field in invoice:
            invoice[field] = value
            applied.append(c)
    return invoice, applied


def parse_node(state):
    rules = config.load_rules()
    invoice = parse_invoice(state["raw_text"], state.get("translated_text"),
                            rules.get("currency_symbol_map", {}))

    applied = []
    # corrections
    if state.get("revalidation"):
        invoice, applied = apply_corrections(invoice, database.latest_corrections(state["invoice_id"]))
    else:
        database.save_fields(state["invoice_id"], invoice)

    log.info("Parsed %s: number=%s vendor=%s po=%s total=%s %s, %d line items",
             state["file_name"], invoice["invoice_number"], invoice["vendor_name"], invoice["po_number"],
             invoice["total_amount"], invoice["currency"], len(invoice["line_items"]))
    return {"invoice": invoice, "applied_corrections": applied}
