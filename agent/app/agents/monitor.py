import hashlib
import logging
from pathlib import Path

from .. import config, database

log = logging.getLogger(__name__)

FILE_TYPES = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
}


def file_checksum(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def list_incoming_files():
    if not config.INCOMING_DIR.exists():
        return []
    files = []
    for path in sorted(config.INCOMING_DIR.iterdir()):
        if not path.is_symlink() and path.is_file() and not path.name.startswith(".") and path.suffix.lower() != ".json":
            files.append(path)
    return files


def monitor_node(state):
    path = Path(state["file_path"])
    name = path.name

    file_type = FILE_TYPES.get(path.suffix.lower())
    if file_type is None:
        database.add_file_event(name, "failed", detail=f"Unsupported file type {path.suffix}")
        return {"stop": "unsupported file type"}

    checksum = file_checksum(path)
    database.add_file_event(name, "detected", checksum=checksum, detail=f"type={file_type}")

    # PostgreSQL is authoritative: cached IDs may refer to failed or removed invoices.
    existing = database.find_by_checksum(checksum)
    # retry
    if existing and existing["processing_status"] == "failed":
        log.info("Retrying failed invoice %s from %s", existing["invoice_id"], name)
        database.update_file_path(existing["invoice_id"], str(path))
        database.set_thread_id(existing["invoice_id"], state["thread_id"])
        return {"invoice_id": str(existing["invoice_id"]), "checksum": checksum, "file_type": file_type}

    if existing:
        log.info("%s was already audited as invoice %s, skipping", name, existing["invoice_id"])
        database.add_file_event(name, "duplicate", checksum=checksum, invoice_id=existing["invoice_id"],
                                detail=f"Same content as {existing['file_name']}")
        return {"stop": "duplicate", "checksum": checksum, "invoice_id": str(existing["invoice_id"])}

    invoice_id = database.create_invoice(str(path), name, file_type, checksum, state["thread_id"])
    log.info("New invoice %s from %s (%s)", invoice_id, name, file_type)
    return {"invoice_id": invoice_id, "checksum": checksum, "file_type": file_type}
