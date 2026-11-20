import json
import logging
from decimal import Decimal

from .. import config, database, erp_client, llm
from ..rules import action_for, deviation_pct, finding
from .parser import fold

log = logging.getLogger(__name__)


def same_text(a, b):
    a, b = fold(a or "").strip(), fold(b or "").strip()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def find_po_line(item, po_lines):
    for line in po_lines:
        if item["item_code"] and item["item_code"].upper() == line["item_code"].upper():
            return line
    for line in po_lines:
        if same_text(item["description"], line["description"]):
            return line
    return None


def compare_with_erp(invoice, rules):
    findings = []
    tolerances = rules.get("tolerances", {})
    erp = {"vendor": None, "purchase_order": None}

    if invoice["vendor_id"]:
        vendor = erp_client.get_vendor(invoice["vendor_id"])
    elif invoice["vendor_name"]:
        vendor = erp_client.find_vendor_by_name(invoice["vendor_name"])
    else:
        vendor = None
    erp["vendor"] = vendor
    if vendor is None:
        findings.append(finding("business", "vendor", action_for(rules, "unknown_vendor_action"),
                                f"Vendor '{invoice['vendor_id'] or invoice['vendor_name']}' is not in the ERP vendor list.",
                                invoice_value=invoice["vendor_id"] or invoice["vendor_name"]))

    po = erp_client.get_purchase_order(invoice["po_number"]) if invoice["po_number"] else None
    erp["purchase_order"] = po
    if po is None:
        findings.append(finding("business", "po_number", action_for(rules, "po_not_found_action"),
                                f"Purchase order '{invoice['po_number']}' was not found in the ERP.",
                                invoice_value=invoice["po_number"]))
        return findings, erp

    if vendor and vendor["vendor_id"] != po["vendor_id"]:
        findings.append(finding("business", "vendor_id", action_for(rules, "vendor_mismatch_action"),
                                f"PO {po['po_number']} belongs to {po['vendor_id']}, not to {vendor['vendor_id']}.",
                                invoice_value=vendor["vendor_id"], erp_value=po["vendor_id"]))

    if invoice["currency"] and po["currency"] and invoice["currency"] != po["currency"]:
        findings.append(finding("business", "currency", action_for(rules, "invalid_currency_action"),
                                f"Invoice is in {invoice['currency']} but the PO is in {po['currency']}.",
                                invoice_value=invoice["currency"], erp_value=po["currency"]))

    expected_tax = Decimal(0)
    tax_known = True
    for item in invoice["line_items"]:
        n = item["line_number"]
        po_line = find_po_line(item, po["line_items"])
        if po_line is None:
            findings.append(finding("business", f"line_{n}.item", action_for(rules, "item_not_on_po_action"),
                                    f"Line {n} ({item['item_code'] or item['description']}) is not on PO {po['po_number']}.",
                                    invoice_value=item["item_code"] or item["description"], line_number=n))
            tax_known = False
            continue

        checks = [
            ("qty", item["quantity"], po_line["qty"], "quantity_difference_percent", "quantity_mismatch_action"),
            ("unit_price", item["unit_price"], po_line["unit_price"], "price_difference_percent", "price_mismatch_action"),
        ]
        for name, invoice_value, erp_value, tolerance_key, policy in checks:
            if invoice_value is None:
                continue
            erp_value = Decimal(str(erp_value))
            deviation = deviation_pct(invoice_value, erp_value)
            allowed = Decimal(str(tolerances.get(tolerance_key, 0)))
            if deviation > allowed:
                findings.append(finding("business", f"line_{n}.{name}", action_for(rules, policy),
                                        f"Line {n} {name}: invoice {invoice_value}, PO {erp_value} "
                                        f"({deviation}% off, {allowed}% allowed).",
                                        invoice_value=invoice_value, erp_value=erp_value,
                                        deviation_pct=deviation, line_number=n))

        sku = erp_client.get_sku(po_line["item_code"])
        if sku is None or item["line_total"] is None:
            tax_known = False
        else:
            expected_tax += item["line_total"] * Decimal(str(sku["gst_rate"])) / 100

    stated_tax = invoice["tax_amount"]
    if tax_known and stated_tax is not None and invoice["line_items"]:
        expected_tax = expected_tax.quantize(Decimal("0.01"))
        deviation = deviation_pct(stated_tax, expected_tax)
        allowed = Decimal(str(tolerances.get("tax_difference_percent", 0)))
        if deviation > allowed:
            findings.append(finding("business", "tax_amount", action_for(rules, "tax_mismatch_action"),
                                    f"Tax is {stated_tax} but the SKU tax rates give {expected_tax}.",
                                    invoice_value=stated_tax, erp_value=expected_tax, deviation_pct=deviation))
    return findings, erp


def business_validation_node(state):
    rules = config.load_rules()
    invoice = state["invoice"]

    try:
        findings, erp = compare_with_erp(invoice, rules)
    except erp_client.ErpUnavailable as error:
        log.error("ERP is not reachable: %s", error)
        database.mark_failed(state["invoice_id"], str(error))
        database.add_file_event(state["file_name"], "failed", state["checksum"], str(error), state["invoice_id"])
        return {"stop": "erp unavailable"}

    if erp["vendor"]:
        database.save_vendor_id(state["invoice_id"], erp["vendor"]["vendor_id"])

    summary = "The invoice matches the purchase order." if not findings \
        else f"{len(findings)} difference(s) from the ERP records."
    risk = "low" if not findings else "high" if any(f["action"] == "reject" for f in findings) else "medium"
    answer = llm.ask("business_validation", "assess_erp_match", {
        "invoice": json.dumps(invoice, default=str, indent=2),
        "erp": json.dumps(erp, default=str, indent=2),
        "findings": json.dumps(findings, default=str, indent=2),
    })
    if answer:
        summary = answer.get("summary") or summary
        if answer.get("risk_level") in ("low", "medium", "high"):
            risk = answer["risk_level"]

    database.add_file_event(state["file_name"], "validated", state["checksum"],
                            "Data and ERP checks completed", state["invoice_id"])
    log.info("Business validation for %s: %d finding(s), risk %s", state["file_name"], len(findings), risk)
    return {"business_findings": findings, "erp": erp, "business_summary": summary, "risk_level": risk}
