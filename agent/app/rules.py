from decimal import Decimal

SEVERITY = {"reject": "high", "manual_review": "medium", "flag": "low"}


def action_for(rules, policy_name):
    return rules.get("validation_policies", {}).get(policy_name, "manual_review")


def finding(source, field_name, action, explanation, invoice_value=None, erp_value=None,
            deviation_pct=None, line_number=None):
    return {
        "source": source,
        "field_name": field_name,
        "line_number": line_number,
        "invoice_value": None if invoice_value is None else str(invoice_value),
        "erp_value": None if erp_value is None else str(erp_value),
        "deviation_pct": deviation_pct,
        "action": action,
        "severity": SEVERITY.get(action, "medium"),
        "explanation": explanation,
    }


def deviation_pct(invoice_value, expected_value):
    if expected_value == 0:
        return Decimal(0) if invoice_value == 0 else Decimal(100)
    return round(abs(invoice_value - expected_value) / abs(expected_value) * 100, 4)


def decide(discrepancies, translation_confidence, rules, human_checked=False):
    actions = [d["action"] for d in discrepancies]
    threshold = float(rules.get("validation_policies", {}).get("auto_approve_confidence_threshold", 0.95))

    if "reject" in actions:
        return "failed", "reject", "at least one finding has the action 'reject'"
    if actions:
        return "partial", "review", f"{len(actions)} finding(s) need a human to check them"
    if not human_checked and translation_confidence is not None and translation_confidence < threshold:
        return "passed", "review", f"translation confidence {translation_confidence} is below {threshold}"
    return "passed", "approve", "no discrepancies and translation confidence is high enough"
