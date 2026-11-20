import logging

from langgraph.types import interrupt

from .. import database

log = logging.getLogger(__name__)


def human_review_node(state):
    # pause
    response = interrupt({"invoice_id": state["invoice_id"], "reason": state["report"]["reason"]})

    log.info("Auditor answered '%s' for invoice %s", response["action"], state["invoice_id"])
    return {
        "human_action": response["action"],
        "review_comment": response.get("comment"),
        "revalidation": True,
        "human_checked": True,
    }


def decision_node(state):
    decision = state["human_action"]
    database.save_decision(state["invoice_id"], decision, state.get("review_comment"))
    database.add_file_event(state["file_name"], "approved" if decision == "approve" else "rejected",
                            state["checksum"], state.get("review_comment"),
                            state["invoice_id"])
    return {"recommendation": decision}
