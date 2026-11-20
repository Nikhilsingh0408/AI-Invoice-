import json
import logging
from datetime import date
from decimal import Decimal

from .. import config, llm
from ..rules import action_for, deviation_pct, finding

log = logging.getLogger(__name__)

CENT = Decimal("0.01")

HEADER_FIELDS = {
    "invoice_no": "invoice_number",
    "invoice_date": "invoice_date",
    "vendor_id": "vendor_id",
    "currency": "currency",
    "total_amount": "total_amount",
}
LINE_FIELDS = {
    "item_code": "item_code",
    "description": "description",
    "qty": "quantity",
    "unit_price": "unit_price",
    "total": "line_total",
}


def check_required_fields(invoice, rules):
    findings = []
    missing = []
    action = action_for(rules, "missing_field_action")
    required = rules.get("required_fields", {})

    for rule_name in required.get("header", []):
        field = HEADER_FIELDS.get(rule_name, rule_name)
        value = invoice.get(field)
        if field == "vendor_id" and not value:
            value = invoice.get("vendor_name")
        if value in (None, ""):
            missing.append(rule_name)
            findings.append(finding("data", rule_name, action, f"Required field '{rule_name}' was not found."))

    if not invoice["line_items"]:
        missing.append("line_items")
        findings.append(finding("data", "line_items", action, "No line items could be read from the invoice."))

    for item in invoice["line_items"]:
        for rule_name in required.get("line_item", []):
            field = LINE_FIELDS.get(rule_name, rule_name)
            if item.get(field) in (None, ""):
                name = f"line_{item['line_number']}.{rule_name}"
                missing.append(name)
                findings.append(finding("data", name, action, f"Line {item['line_number']} has no '{rule_name}'.",
                                        line_number=item["line_number"]))
    return findings, missing


def check_currency(invoice, rules):
    accepted = rules.get("accepted_currencies", [])
    currency = invoice.get("currency")
    if not currency or currency in accepted:
        return []
    return [finding("data", "currency", action_for(rules, "invalid_currency_action"),
                    f"Currency {currency} is not in the accepted list {accepted}.",
                    invoice_value=currency, erp_value=", ".join(accepted))]


def check_totals(invoice, rules):
    findings = []
    action = action_for(rules, "total_mismatch_action")

    lines_sum = Decimal(0)
    for item in invoice["line_items"]:
        qty, price, total = item["quantity"], item["unit_price"], item["line_total"]
        if total is not None:
            lines_sum += total
        if qty is None or price is None or total is None:
            continue
        expected = (qty * price).quantize(CENT)
        if abs(expected - total) > CENT:
            findings.append(finding("data", f"line_{item['line_number']}.total", action,
                                    f"Line {item['line_number']}: {qty} x {price} = {expected}, but the invoice says {total}.",
                                    invoice_value=total, erp_value=expected,
                                    deviation_pct=deviation_pct(total, expected), line_number=item["line_number"]))

    subtotal = invoice["subtotal_amount"]
    tax = invoice["tax_amount"]
    total = invoice["total_amount"]

    if subtotal is not None and invoice["line_items"] and abs(subtotal - lines_sum) > CENT:
        findings.append(finding("data", "subtotal", action,
                                f"Line items add up to {lines_sum}, but the subtotal is {subtotal}.",
                                invoice_value=subtotal, erp_value=lines_sum,
                                deviation_pct=deviation_pct(subtotal, lines_sum)))

    if total is not None:
        base = subtotal if subtotal is not None else lines_sum
        expected_total = base + (tax or 0)
        if abs(total - expected_total) > CENT:
            findings.append(finding("data", "total_amount", action,
                                    f"Subtotal {base} + tax {tax or 0} = {expected_total}, but the total is {total}.",
                                    invoice_value=total, erp_value=expected_total,
                                    deviation_pct=deviation_pct(total, expected_total)))
    return findings


def check_tax(invoice, rules):
    rate = invoice["extras"].get("tax_rate_percent")
    subtotal, tax = invoice["subtotal_amount"], invoice["tax_amount"]
    if rate is None or subtotal is None or tax is None:
        return []
    expected = (subtotal * Decimal(str(rate)) / 100).quantize(CENT)
    if abs(tax - expected) <= CENT:
        return []
    return [finding("data", "tax_amount", action_for(rules, "tax_mismatch_action"),
                    f"{rate}% of {subtotal} is {expected}, but the tax on the invoice is {tax}.",
                    invoice_value=tax, erp_value=expected, deviation_pct=deviation_pct(tax, expected))]


def check_consistency(invoice, rules):
    findings = []
    action = action_for(rules, "inconsistent_field_action")
    if invoice["invoice_date"] and invoice["invoice_date"] > date.today():
        findings.append(finding("data", "invoice_date", action,
                                f"The invoice date {invoice['invoice_date']} is in the future.",
                                invoice_value=invoice["invoice_date"]))
    for field in ["subtotal_amount", "tax_amount", "total_amount"]:
        if invoice[field] is not None and invoice[field] < 0:
            findings.append(finding("data", field, action, f"{field} is negative ({invoice[field]}).",
                                    invoice_value=invoice[field]))
    for item in invoice["line_items"]:
        if item["quantity"] is not None and item["quantity"] <= 0:
            findings.append(finding("data", f"line_{item['line_number']}.qty", action,
                                    f"Line {item['line_number']} has quantity {item['quantity']}.",
                                    invoice_value=item["quantity"], line_number=item["line_number"]))
    return findings


def data_validation_node(state):
    rules = config.load_rules()
    invoice = state["invoice"]

    findings, missing = check_required_fields(invoice, rules)
    findings += check_currency(invoice, rules)
    findings += check_totals(invoice, rules)
    findings += check_tax(invoice, rules)
    findings += check_consistency(invoice, rules)

    summary = "All required fields are present and the totals add up." if not findings \
        else f"{len(findings)} data problem(s) found."
    try:
        answer = llm.ask("data_validation", "assess_data_quality", {
            "invoice": json.dumps(invoice, default=str, indent=2),
            "findings": json.dumps(findings, default=str, indent=2),
        })
    except FileNotFoundError as error:
        log.warning("Data validation prompt unavailable; using rule-based summary: %s", error)
        answer = None
    if answer and answer.get("summary"):
        summary = answer["summary"]

    log.info("Data validation for %s: %d finding(s)", state["file_name"], len(findings))
    return {"data_findings": findings, "missing_fields": missing, "data_summary": summary}
