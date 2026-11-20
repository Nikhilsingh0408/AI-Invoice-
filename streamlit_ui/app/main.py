import html
import os
import time
from urllib.parse import quote

import httpx
import psycopg
import streamlit as st
import streamlit.components.v1 as components
from psycopg.rows import dict_row

AGENT_URL = os.getenv("AGENT_URL", "http://agent:8000")
DB_URL = "postgresql://{}:{}@{}:{}/{}".format(
    os.environ["POSTGRES_USER"],
    os.environ["POSTGRES_PASSWORD"],
    os.getenv("POSTGRES_HOST", "postgres"),
    os.getenv("POSTGRES_PORT", "5432"),
    os.getenv("POSTGRES_DB", "invoice_auditor"),
)

st.set_page_config(
    page_title="Invoice Auditor",
    page_icon="▦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
      --ink: #182638;
      --muted: #526174;
      --line: #e4e9ef;
      --canvas: #f5f7fa;
      --surface: #ffffff;
      --navy: #f1f4f7;
      --navy-soft: #e3e9ef;
      --teal: #0d6868;
      --teal-soft: #e4f2f1;
      --amber: #80500a;
      --amber-soft: #fff2d8;
      --red: #963232;
      --red-soft: #ffedeb;
      --green: #21613d;
      --green-soft: #e7f3eb;
    }
    html, body { font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .stApp { background: var(--canvas); color: var(--ink); }
    .stApp { font-size:16px; }
    .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button { font-size:1rem; }
    [data-testid="stMarkdownContainer"] p { font-size:1rem; line-height:1.45; }
    [data-testid="stCaptionContainer"] { color:#526174; font-size:.9rem; line-height:1.4; }
    [data-testid="stWidgetLabel"] p { color:#26384b; font-size:.95rem; font-weight:650; }
    [data-testid="stHeader"] { background: rgba(245,247,250,.92); }
    [data-testid="stToolbar"] { right: 1rem; }
    .block-container { max-width: none !important; padding: .45rem 1.2rem 1rem; }
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
      background: var(--navy) !important; color: #111827 !important;
    }
    section[data-testid="stSidebar"] {
      width:17rem !important; min-width:17rem !important; max-width:17rem !important;
      border-right:1px solid #d5dde6;
    }
    section[data-testid="stSidebar"] > div { padding: .45rem .5rem; }
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding:.35rem .45rem .6rem !important; }
    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.48rem; }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] span,
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] *,
    section[data-testid="stSidebar"] [data-testid="stRadio"] label,
    section[data-testid="stSidebar"] [data-testid="stRadio"] label span,
    section[data-testid="stSidebar"] [data-testid="stRadio"] label p {
      color: #111827 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stFileUploader"],
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] > div,
    section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"],
    section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"],
    section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] > div {
      box-sizing:border-box; width:100%; min-width:0; max-width:100%; white-space:normal;
      overflow-wrap:anywhere; word-break:break-word;
    }
    section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] { padding:.55rem; min-height:5rem; }
    section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button {
      width:auto; min-height:2rem; white-space:nowrap; overflow-wrap:normal;
    }
    section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] small {
      white-space:normal; overflow-wrap:anywhere;
    }
    section[data-testid="stSidebar"] [data-testid="stRadio"] > label { display:none; }
    section[data-testid="stSidebar"] [role="radiogroup"] { gap: .15rem; }
    section[data-testid="stSidebar"] [role="radiogroup"] label {
      width:100%; min-height:2.45rem; border:1px solid transparent; border-radius:7px;
      padding:.4rem .55rem; margin:0; transition:background .15s ease; white-space:nowrap; color:#111827 !important;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:#e3e9ef; }
    section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked),
    section[data-testid="stSidebar"] [role="radiogroup"] label:has([aria-checked="true"]),
    section[data-testid="stSidebar"] [role="radiogroup"] label[data-checked="true"],
    section[data-testid="stSidebar"] [role="radiogroup"] [role="radio"][aria-checked="true"] {
      background:#0d6868 !important; border-color:#0d6868 !important; color:#fff !important;
      box-shadow:inset 3px 0 #85d1c9;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label p,
    section[data-testid="stSidebar"] [role="radiogroup"] label span { margin:0; font-size:.96rem; line-height:1.25; font-weight:650; color:#111827 !important; white-space:nowrap; }
    section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p,
    section[data-testid="stSidebar"] [role="radiogroup"] label:has([aria-checked="true"]) p { color:#fff !important; }
    .brand-name { color:#111827 !important; font-size:1.2rem; font-weight:800; letter-spacing:-.01em; padding:.25rem 0; }
    .page-title { color:var(--ink); font-size:1.85rem; font-weight:750; line-height:1.16; margin:0; }
    .page-description { color:var(--muted); font-size:.94rem; margin:.1rem 0 .35rem; }
    .section-title { color:var(--ink); font-size:1.2rem; font-weight:700; margin:.15rem 0 .12rem; }
    .section-description { color:var(--muted); font-size:.92rem; margin:0 0 .35rem; }
    .subtle-label { color:#526174; font-size:.82rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
    .status-badge { display:inline-flex; align-items:center; gap:.35rem; width:max-content; max-width:100%;
      border-radius:999px; padding:.28rem .62rem; font-size:.84rem; line-height:1.2; font-weight:700;
      white-space:nowrap; overflow:visible; }
    .status-approved { color:var(--green); background:var(--green-soft); }
    .status-rejected, .status-failed { color:var(--red); background:var(--red-soft); }
    .status-pending_review { color:var(--amber); background:var(--amber-soft); }
    .status-detected, .status-extracted, .status-other { color:#52657a; background:#edf1f5; }
    .quiet-note { color:var(--muted); font-size:.9rem; }
    div[data-testid="stMetric"] { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:.7rem .9rem; box-shadow:0 1px 2px rgba(24,38,56,.025); }
    div[data-testid="stMetricLabel"] p { color:var(--muted); font-size:.88rem; font-weight:650; }
    div[data-testid="stMetricValue"] { color:var(--ink); font-size:1.65rem; font-weight:750; }
    div[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); border-radius:10px; background:var(--surface); }
    .stButton > button { width:100%; border-radius:7px; min-height:2.4rem; padding:.45rem .65rem;
      font-weight:650; line-height:1.25; border-color:#cbd5df; white-space:normal; overflow-wrap:anywhere; }
    .stButton > button[kind="primary"] { background:var(--teal); border-color:var(--teal); }
    .stButton > button[kind="primary"]:hover { background:#0f6966; border-color:#0f6966; }
    .stTextInput input, .stTextArea textarea, [data-baseweb="select"] > div { border-radius:8px; }
    [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"], [data-testid="stAlert"] {
      min-width:0; overflow-wrap:break-word;
    }
    [data-testid="stCode"] pre, pre, code { white-space:pre-wrap !important; overflow-wrap:anywhere; word-break:break-word; }
    [data-testid="stTextArea"] textarea { line-height:1.55; font-size:1rem; }
    [data-testid="stVerticalBlockBorderWrapper"] { min-width:0; }
    [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
    [data-testid="stTabs"] [role="tab"] { font-weight:650; white-space:nowrap; }
    [data-testid="stChatMessage"] { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:.15rem .35rem; }
    [data-testid="stChatInput"] { padding:.25rem 0 !important; }
    [data-testid="stChatInput"] textarea { min-height:2.8rem !important; font-size:1rem !important; }
    [data-testid="stForm"] { padding:.45rem .6rem; border:1px solid var(--line); border-radius:9px; background:#fff; }
    @media (max-width: 900px) {
      .block-container { padding:.6rem .7rem 1.2rem; }
      .page-title { font-size:1.65rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

STATUS_WORD = {
    "pending_review": "Needs review",
    "approved": "Approved",
    "rejected": "Rejected",
    "failed": "Failed",
    "detected": "In progress",
    "extracted": "In progress",
}
STATUS_FILTERS = ["All statuses", "pending_review", "approved", "rejected", "failed", "detected", "extracted"]
PAGE_INFO = {
    "Overview": ("Workspace", "A live view of invoice throughput, review work, and recent activity."),
    "Review queue": ("Human review", "Resolve flagged invoices while keeping each correction and decision auditable."),
    "Invoices": ("Invoice library", "Search processed invoices and inspect reports, extracted text, and correction history."),
    "Ask": ("Invoice assistant", "Ask questions across the audit trail or focus on one invoice."),
}


def query(sql, params=None):
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchall()


def get_invoices(search=""):
    like = f"%{search}%"
    return query(
        """SELECT invoice_id::text, invoice_number, vendor_name, po_number, currency, total_amount,
                  processing_status, file_name
           FROM audit.invoice_audit
           WHERE %s = '' OR invoice_number ILIKE %s OR vendor_name ILIKE %s OR po_number ILIKE %s
           ORDER BY processed_at""",
        (search, like, like, like),
    )


def get_invoice(invoice_id):
    rows = query("SELECT *, invoice_id::text AS invoice_id FROM audit.invoice_audit WHERE invoice_id = %s",
                 (invoice_id,))
    return rows[0] if rows else None


def get_line_items(invoice_id):
    return query(
        """SELECT line_number, item_code, description, quantity, unit_price, line_total
           FROM audit.invoice_line_items WHERE invoice_id = %s ORDER BY line_number""",
        (invoice_id,),
    )


def get_discrepancies(invoice_id):
    return query(
        """SELECT d.severity, d.field_name, d.explanation FROM audit.discrepancies d
           JOIN audit.invoice_audit a ON a.invoice_id = d.invoice_id
           WHERE d.invoice_id = %s AND d.run_number = a.run_number ORDER BY d.id""",
        (invoice_id,),
    )


def get_history(invoice_id):
    return query(
        """SELECT corrected_at, scope, line_number, field_name, original_value, corrected_value
           FROM audit.human_feedback WHERE invoice_id = %s ORDER BY corrected_at DESC""",
        (invoice_id,),
    )


def get_corrections(invoice_id):
    return query(
        """SELECT DISTINCT ON (scope, line_number, field_name) scope, line_number, field_name,
                  corrected_value
           FROM audit.human_feedback
           WHERE invoice_id = %s AND scope IN ('header', 'line_item')
           ORDER BY scope, line_number, field_name, corrected_at DESC""",
        (invoice_id,),
    )


def get_file_events():
    return query(
        """SELECT occurred_at, file_name, event_type, detail FROM audit.file_events
           ORDER BY occurred_at DESC LIMIT 200"""
    )


def call_agent(path, body=None, timeout=300):
    try:
        response = httpx.post(AGENT_URL + path, json=body or {}, timeout=timeout)
    except httpx.RequestError as error:
        st.error(f"Could not reach the agent: {error}")
        return None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        st.error(detail)
        return None
    return response.json()


def agent_request(method, path, timeout=60):
    try:
        response = httpx.request(method, AGENT_URL + path, timeout=timeout)
    except httpx.RequestError as error:
        st.error(f"Could not reach the agent: {error}")
        return None
    if response.status_code >= 400:
        st.error(response.text)
        return None
    return response.json()


def show(value):
    return "-" if value is None or value == "" else str(value)


def as_text(value):
    return "" if value is None else str(value)


def title_of(invoice):
    return invoice["invoice_number"] or invoice["file_name"]


def status_badge(status):
    safe_status = status if status in STATUS_WORD else "other"
    label = html.escape(STATUS_WORD.get(status, status or "Unknown"))
    return f'<span class="status-badge status-{safe_status}">{label}</span>'


def page_header(title, description):
    content = f'<h1 class="page-title">{html.escape(title)}</h1>'
    if description:
        content += f'<p class="page-description">{html.escape(description)}</p>'
    st.markdown(content, unsafe_allow_html=True)


def section_heading(title, description=None):
    st.markdown(f'<div class="section-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if description:
        st.markdown(f'<div class="section-description">{html.escape(description)}</div>', unsafe_allow_html=True)


def invoice_counts(invoices):
    counts = {key: 0 for key in ("pending_review", "approved", "rejected", "failed")}
    for invoice in invoices:
        status = invoice["processing_status"]
        if status in counts:
            counts[status] += 1
    return counts


def metric_row(invoices):
    counts = invoice_counts(invoices)
    columns = st.columns(4, gap="medium")
    metrics = [
        ("To review", counts["pending_review"], "Requires an auditor"),
        ("Approved", counts["approved"], "Completed successfully"),
        ("Rejected", counts["rejected"], "Flagged by validation"),
        ("Failed", counts["failed"], "Needs attention"),
    ]
    for column, (label, value, help_text) in zip(columns, metrics):
        column.metric(label, value, help=help_text)


def header(invoices):
    page_header("Overview", PAGE_INFO["Overview"][1])
    metric_row(invoices)


def correction_form(invoice, items, key_prefix="correction"):
    invoice_id = invoice["invoice_id"]
    latest = {
        (c["scope"], c["line_number"], c["field_name"]): c["corrected_value"]
        for c in get_corrections(invoice_id)
    }
    header_fields = [
        "invoice_number", "invoice_date", "vendor_name", "vendor_id", "po_number", "currency",
        "subtotal_amount", "tax_amount", "total_amount",
    ]
    line_fields = ["item_code", "description", "quantity", "unit_price", "line_total"]

    st.caption("Edit any value that needs correction, then save it before re-checking.")
    st.markdown("**Invoice details**")
    edited_header = {}
    header_columns = st.columns(3, gap="medium")
    for index, field in enumerate(header_fields):
        initial = as_text(latest.get(("header", None, field), invoice[field]))
        edited_header[field] = header_columns[index % 3].text_input(
            field.replace("_", " ").title(), value=initial,
            key=f"{key_prefix}_header_{invoice_id}_{field}",
        )

    edited_lines = {}
    if items:
        st.markdown("**Line items**")
        for item in items:
            line_number = item["line_number"]
            with st.container(border=True):
                st.markdown(f"**Line {line_number}**")
                compact_fields = ["item_code", "quantity", "unit_price", "line_total"]
                cols = st.columns([1.1, 1, 1, 1], gap="medium")
                for col, field in zip(cols, compact_fields):
                    initial = as_text(latest.get(("line_item", line_number, field), item[field]))
                    edited_lines[(line_number, field)] = col.text_input(
                        field.replace("_", " ").title(), value=initial,
                        key=f"{key_prefix}_line_{invoice_id}_{line_number}_{field}",
                    )
                initial = as_text(latest.get(("line_item", line_number, "description"), item["description"]))
                edited_lines[(line_number, "description")] = st.text_area(
                    "Description", value=initial, height=128,
                    key=f"{key_prefix}_line_{invoice_id}_{line_number}_description",
                )

    if st.button("Save changes", key=f"{key_prefix}_save_{invoice_id}"):
        changes = []
        for field in header_fields:
            old_value = as_text(latest.get(("header", None, field), invoice[field]))
            new_value = edited_header[field]
            if new_value.strip() != old_value:
                changes.append({
                    "scope": "header", "line_number": None, "field_name": field,
                    "corrected_value": new_value.strip(),
                    "original_value": as_text(invoice[field]) or None,
                })
        for item in items:
            line_number = item["line_number"]
            for field in line_fields:
                old_value = as_text(latest.get(("line_item", line_number, field), item[field]))
                new_value = edited_lines[(line_number, field)]
                if new_value.strip() != old_value:
                    changes.append({
                        "scope": "line_item", "line_number": line_number, "field_name": field,
                        "corrected_value": new_value.strip(),
                        "original_value": as_text(item[field]) or None,
                    })
        if not changes:
            st.info("Nothing was changed.")
            return
        saved = 0
        for change in changes:
            if call_agent("/feedback", {"invoice_id": invoice_id, **change}):
                saved += 1
        st.toast(f"Saved {saved} correction(s). Press Re-check to validate again.")
        st.rerun()


def send_review(invoice_id, action, comment=None):
    with st.spinner("Updating invoice..."):
        result = call_agent("/review", {"invoice_id": invoice_id, "action": action, "comment": comment or None})
    if result:
        if result["status"] == "pending_review":
            st.toast(f"Still needs review: {result.get('reason')}")
        else:
            st.toast(f"Invoice {STATUS_WORD.get(result['status'], result['status'])}")
        st.rerun()


def review_card(invoice):
    if not invoice:
        st.warning("This invoice is no longer available. Refresh the page and select it again.")
        return
    invoice_id = invoice["invoice_id"]
    report = invoice["report_json"] or {}
    issues = get_discrepancies(invoice_id)

    st.markdown(f"### {html.escape(title_of(invoice))}")
    st.markdown(status_badge(invoice["processing_status"]), unsafe_allow_html=True)
    st.caption(f"{show(invoice['vendor_name'])} · Invoice date {show(invoice['invoice_date'])} · "
               f"{show(invoice['total_amount'])} {show(invoice['currency'])}")
    st.markdown("**Review reason**")
    st.write(report.get("reason") or "The workflow requested a human check.")

    actions = st.columns(3, gap="small")
    if actions[0].button("Re-check", key=f"recheck_{invoice_id}", type="primary", use_container_width=True):
        send_review(invoice_id, "recheck")
    if actions[1].button("Approve", key=f"approve_{invoice_id}", use_container_width=True):
        send_review(invoice_id, "approve")
    if actions[2].button("Reject", key=f"reject_{invoice_id}", use_container_width=True):
        send_review(invoice_id, "reject")

    if issues:
        with st.expander(f"Discrepancies ({len(issues)})"):
            for issue in issues:
                st.markdown(f"**{show(issue['field_name']).replace('_', ' ').title()} · "
                            f"{show(issue['severity']).title()}**")
                st.write(issue["explanation"] or "No additional explanation was provided.")
    else:
        st.caption("No discrepancy rows were recorded for this run.")

    with st.expander("Correct extracted values"):
        correction_form(invoice, get_line_items(invoice_id), key_prefix="review")


def review_queue_page(invoices):
    page_header("Review queue", "")
    waiting = [invoice for invoice in invoices if invoice["processing_status"] == "pending_review"]
    if not waiting:
        st.success("The review queue is clear.")
        return
    options = [invoice["invoice_id"] for invoice in reversed(waiting)]
    selected_id = st.session_state.get("review_invoice")
    if selected_id not in options:
        selected_id = options[0]
    if st.session_state.get("review_invoice_selector") not in options:
        st.session_state["review_invoice_selector"] = selected_id
    labels = {
        invoice["invoice_id"]: f"{title_of(invoice)} · {show(invoice['vendor_name'])} · "
                               f"{show(invoice['total_amount'])} {show(invoice['currency'])}"
        for invoice in waiting
    }
    selected_id = st.selectbox(
        f"Select invoice to review · {len(waiting)} waiting", options, index=options.index(selected_id),
        format_func=lambda invoice_id: labels[invoice_id], key="review_invoice_selector",
    )
    st.session_state.review_invoice = selected_id
    review_card(get_invoice(selected_id))


def invoice_detail(invoice):
    if not invoice:
        st.warning("This invoice is no longer available.")
        return
    invoice_id = invoice["invoice_id"]
    st.markdown(f"### {html.escape(title_of(invoice))}")
    st.markdown(status_badge(invoice["processing_status"]), unsafe_allow_html=True)
    st.caption(f"{show(invoice['vendor_name'])} · PO {show(invoice['po_number'])} · "
               f"{show(invoice['invoice_date'])} · Source language {show(invoice['source_language'])}")
    if invoice["error_message"]:
        with st.container(border=True):
            st.error(f"Previous workflow error: {invoice['error_message']}")
            if invoice["raw_extracted_text"]:
                st.caption("The extracted text is saved. Re-check the invoice using the current configuration.")
                if st.button("Retry invoice", key=f"retry_failed_{invoice_id}", type="primary"):
                    send_review(invoice_id, "recheck", "Retried from invoice details")

    report_tab, edit_tab = st.tabs(["Report", "Change values & history"])
    with report_tab:
        try:
            response = httpx.get(f"{AGENT_URL}/invoices/{invoice_id}/report.html", timeout=20)
        except httpx.RequestError:
            response = None
        if response is not None and response.status_code == 200:
            components.html(response.text, height=860, scrolling=True)
        else:
            st.info("No report is available for this invoice yet.")
        st.markdown("#### Extracted text")
        st.markdown("**Original extracted text**")
        st.text_area("Extracted text", value=invoice["raw_extracted_text"] or "No extracted text is available.",
                     height=320, disabled=True, key=f"raw_text_{invoice_id}", label_visibility="collapsed")
        if invoice["translated_text"]:
            st.markdown("**English translation**")
            st.text_area("English translation", value=invoice["translated_text"], height=320, disabled=True,
                         key=f"translated_text_{invoice_id}", label_visibility="collapsed")
    with edit_tab:
        if not invoice["raw_extracted_text"]:
            st.info("Nothing was read from this file, so there are no extracted values to correct.")
        else:
            correction_form(invoice, get_line_items(invoice["invoice_id"]), key_prefix="invoice")
            if st.button("Re-check invoice", key=f"recheck_detail_{invoice_id}", type="primary"):
                send_review(invoice_id, "recheck")
        st.divider()
        section_heading("Correction history")
        history = get_history(invoice_id)
        if history:
            for entry in history:
                with st.container(border=True):
                    st.markdown(f"**{entry['field_name'].replace('_', ' ').title()}** · "
                                f"{show(entry['scope'])} · {str(entry['corrected_at'])[:19]}")
                    st.caption(f"Original: {show(entry['original_value'])}")
                    st.write(f"Corrected: {show(entry['corrected_value'])}")
        else:
            st.info("No corrections or decisions have been recorded yet.")


def overview_page(invoices):
    header(invoices)
    waiting = [invoice for invoice in invoices if invoice["processing_status"] == "pending_review"]
    section_heading("Needs your attention", "Invoices that need an auditor’s review.")
    if not waiting:
        st.success("There are no invoices waiting for review.")
    else:
        for invoice in reversed(waiting[:5]):
            with st.container(border=True):
                row = st.columns([4, 1], vertical_alignment="center", gap="small")
                row[0].markdown(f"**{html.escape(title_of(invoice))}**")
                row[0].caption(f"{show(invoice['vendor_name'])} · PO {show(invoice['po_number'])} · "
                               f"{show(invoice['total_amount'])} {show(invoice['currency'])}")
                row[1].button(
                    "Review", key=f"overview_review_{invoice['invoice_id']}", use_container_width=True,
                        on_click=navigate_to, args=("Human review", "review_invoice", invoice["invoice_id"]),
                )
        if len(waiting) > 5:
            st.button("Open full review queue", key="overview_all_reviews",
                      on_click=navigate_to, args=("Human review", None, None))

    st.divider()
    section_heading("Recent activity", "Latest invoice processing events.")
    events = get_file_events()[:8]
    if not events:
        st.info("No file activity has been recorded yet.")
    for event in events:
        with st.container(border=True):
            row = st.columns([1.4, 4], vertical_alignment="center", gap="small")
            row[0].caption(str(event["occurred_at"])[:19])
            row[1].markdown(f"**{html.escape(show(event['file_name']))}** · "
                            f"{html.escape(show(event['event_type']))}")
            if event["detail"]:
                st.write(event["detail"])

    st.info("To add an invoice, place a PDF, DOCX, or image in `data/incoming/`. The agent watches that folder.")


def invoices_page(invoices):
    page_header("Invoices", PAGE_INFO["Invoices"][1])
    search_col, status_col = st.columns([3, 1.2], gap="medium")
    search = search_col.text_input("Search invoices", placeholder="Invoice number, vendor, or PO")
    status_choice = status_col.selectbox(
        "Status", STATUS_FILTERS, format_func=lambda value: "All statuses" if value == "All statuses"
        else STATUS_WORD.get(value, value),
    )
    filtered = get_invoices(search) if search else invoices
    if status_choice != "All statuses":
        filtered = [invoice for invoice in filtered if invoice["processing_status"] == status_choice]

    st.caption(f"{len(filtered)} invoice(s)")
    if not filtered:
        st.info("No invoices match these filters.")
        return
    options = [invoice["invoice_id"] for invoice in reversed(filtered)]
    labels = {
        invoice["invoice_id"]: f"{title_of(invoice)} · {show(invoice['vendor_name'])} · "
                               f"PO {show(invoice['po_number'])} · {STATUS_WORD.get(invoice['processing_status'], invoice['processing_status'])}"
        for invoice in filtered
    }
    selected_id = st.session_state.get("open_invoice")
    if selected_id not in options:
        selected_id = options[0]
    if st.session_state.get("invoice_selector") not in options:
        st.session_state["invoice_selector"] = selected_id
    selected_id = st.selectbox(
        "Select an invoice", options, index=options.index(selected_id),
        format_func=lambda invoice_id: labels[invoice_id], key="invoice_selector",
    )
    st.session_state.open_invoice = selected_id
    st.divider()
    invoice_detail(get_invoice(selected_id))


def ask_chat_page(invoices):
    st.markdown(
        """<style>
        [data-testid="stChatMessageAvatarUser"],
        [data-testid="stChatMessageAvatarAssistant"] { display: none; }
        </style>""",
        unsafe_allow_html=True,
    )
    st.session_state.setdefault("chat", [])
    choices = {"All invoices": None}
    for row in invoices:
        choices[f"{title_of(row)} · {show(row['vendor_name'])}"] = row["invoice_id"]
    scope_col, hint_col = st.columns([1, 2], gap="medium")
    about = scope_col.selectbox("Search within", list(choices))
    hint_col.caption("Choose one invoice for a focused answer, or search across the full audit record.")

    for message in st.session_state.chat:
        with st.chat_message(message["role"]):
            st.markdown(message["text"])
            if message["role"] == "assistant":
                with st.expander("Response details", expanded=False):
                    st.caption("Backend response metadata, not the model's internal reasoning.")
                    details = message.get("response_details")
                    if details is None:
                        details = {"processing_summary": message.get("note"),
                                   "sources": message.get("sources", []),
                                   "ragas_scores": message.get("ragas_scores", {})}
                        st.caption("Only saved metadata is available for this earlier response.")
                    st.json(details)
            if "ragas_scores" in message:
                score_cols = st.columns(3, gap="small")
                labels = [("faithfulness", "Faithfulness"), ("answer_relevancy", "Answer relevance"),
                          ("context_precision", "Context precision")]
                for col, (key, label) in zip(score_cols, labels):
                    value = message["ragas_scores"].get(key)
                    col.metric(label, "—" if value is None else f"{float(value):.2f}")
                st.caption("RAGAS scores · 0–1, higher is better. Context recall requires a reference answer.")
                if message.get("ragas_note"):
                    st.caption(message["ragas_note"])
                elif all(value is None for value in message["ragas_scores"].values()):
                    st.caption("RAGAS scoring is unavailable for this answer.")
            if message.get("sources"):
                st.caption("Sources: " + ", ".join(map(str, message["sources"])))

    with st.form("ask_question_form", clear_on_submit=True, border=True):
        prompt_col, action_col = st.columns([5, 1], vertical_alignment="bottom", gap="small")
        question = prompt_col.text_input(
            "Your question", placeholder="Ask about an invoice, discrepancy, vendor, or audit outcome"
        )
        submitted = action_col.form_submit_button("Ask", type="primary", use_container_width=True)
    if not submitted:
        question = None
    if question:
        st.session_state.chat.append({"role": "user", "text": question})
        with st.spinner("Searching records and scoring with RAGAS..."):
            result = call_agent("/query", {"question": question, "invoice_id": choices[about]}, timeout=600)
        if result:
            st.session_state.chat.append({
                "role": "assistant", "text": result.get("answer", "No answer was returned."),
                "response_details": {key: result[key] for key in
                                     ("route", "attempts", "sources", "scores", "accepted", "ragas_scores", "ragas_note")
                                     if key in result},
                "sources": result.get("sources", []),
                "ragas_scores": result.get("ragas_scores", {}),
                "ragas_note": result.get("ragas_note"),
            })
        st.rerun()


def sidebar_controls():
    stages = {"detected": "File received", "extracted": "Text extracted",
              "validated": "Checks complete", "audited": "Audit complete",
              "approved": "Approved", "rejected": "Rejected", "completed": "Complete",
              "duplicate": "Duplicate", "pending_review": "Ready for review", "failed": "Failed"}
    with st.sidebar.expander("Upload invoices"):
        uploaded = st.file_uploader(
            "Select files", type=["pdf", "docx", "png", "jpg", "jpeg", "tif", "tiff"],
            accept_multiple_files=True, key="sidebar_invoice_upload",
            help="PDF, DOCX, PNG, JPG, or TIFF. You can select multiple files.",
        )
        if st.button("Upload invoices", type="primary", key="sidebar_upload_submit",
                     disabled=not uploaded, use_container_width=True):
            for file in uploaded:
                try:
                    response = httpx.post(
                        f"{AGENT_URL}/uploads", files={"file": (file.name, file.getvalue(), file.type)}, timeout=60
                    )
                    response.raise_for_status()
                    name = response.json()["file_name"]
                    bar = st.progress(0, text=f"Uploading {file.name}")
                    for _ in range(180):
                        poll = httpx.get(
                            f"{AGENT_URL}/uploads/{quote(name, safe='')}/status", timeout=10
                        )
                        poll.raise_for_status()
                        current = poll.json()
                        events = current.get("events", [])
                        st.session_state.setdefault("upload_timelines", {})[name] = {
                            "original_name": file.name, "status": current["event_type"],
                        }
                        if current["event_type"] in {"duplicate", "completed", "pending_review", "failed"}:
                            bar.progress(100, text=f"{file.name} · {stages.get(current['event_type'], 'Complete')}")
                            break
                        bar.progress(min(95, len(events) * 20),
                                     text=f"{file.name} · {stages.get(current['event_type'], 'Processing')}")
                        if not events:
                            bar.progress(0, text=f"{file.name} · Waiting to start")
                        time.sleep(1)
                    else:
                        bar.progress(95, text=f"{file.name} · Still processing")
                except (httpx.HTTPError, ValueError) as error:
                    st.error(f"Could not upload {file.name}: {error}")

        timelines = list(st.session_state.get("upload_timelines", {}).items())
        if timelines:
            st.caption("Recent uploads")
            for _, timeline in reversed(timelines[-3:]):
                st.caption(f"{timeline['original_name']} · {stages.get(timeline['status'], timeline['status'])}")

    with st.sidebar.expander("Reset data"):
        if st.button("Reset all data", key="sidebar_reset_data"):
            st.session_state.confirm_reset = True
        if st.session_state.get("confirm_reset"):
            st.warning("This permanently deletes all invoices and audit history.")
            confirm_col, cancel_col = st.columns(2, gap="small")
            if confirm_col.button("Confirm", type="primary", key="sidebar_confirm_reset",
                                  use_container_width=True):
                result = agent_request("POST", "/reset", timeout=180)
                if result:
                    st.session_state.confirm_reset = False
                    st.session_state.chat = []
                    st.session_state.upload_timelines = {}
                    st.session_state.pop("open_invoice", None)
                    st.session_state.reset_complete = True
                    st.rerun()
            if cancel_col.button("Cancel", key="sidebar_cancel_reset", use_container_width=True):
                st.session_state.confirm_reset = False
                st.rerun()


def ask_page(invoices):
    page_header("Ask", "Ask questions across invoices and see RAGAS scores for each answer.")
    ask_chat_page(invoices)


def navigate_to(page_name, state_key=None, state_value=None):
    st.session_state.navigation = page_name
    if state_key:
        st.session_state[state_key] = state_value
        if state_key == "review_invoice":
            st.session_state["review_invoice_selector"] = state_value


try:
    all_invoices = get_invoices()
except Exception as error:
    st.error(f"The audit database is not reachable: {error}")
    st.stop()


@st.fragment(run_every="5s")
def watch_for_changes():
    row = query(
        """SELECT (SELECT count(*) FROM audit.invoice_audit) AS invoices,
                  (SELECT max(processed_at) FROM audit.invoice_audit) AS processed,
                  (SELECT max(occurred_at) FROM audit.file_events) AS events"""
    )[0]
    latest = f"{row['invoices']}-{row['processed']}-{row['events']}"
    if "last_seen" not in st.session_state:
        st.session_state.last_seen = latest
    elif latest != st.session_state.last_seen:
        st.session_state.last_seen = latest
        st.rerun()


if st.session_state.get("navigation") == "Review queue":
    st.session_state.navigation = "Human review"

with st.sidebar:
    st.markdown('<div class="brand-name">Invoice Auditor</div>', unsafe_allow_html=True)
    if st.session_state.pop("reset_complete", False):
        st.success("All invoice data was removed.")
    page = st.radio(
        "Workspace navigation",
        ["Overview", "Invoices", "Human review", "Ask"],
        label_visibility="collapsed",
        key="navigation",
    )
    sidebar_controls()

watch_for_changes()

if page == "Overview":
    overview_page(all_invoices)
elif page == "Human review":
    review_queue_page(all_invoices)
elif page == "Invoices":
    invoices_page(all_invoices)
elif page == "Ask":
    ask_page(all_invoices)
