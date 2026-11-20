import logging
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .db import fetch_all, fetch_one, pool, reply

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mock-erp")

app = FastAPI(title="Mock ERP Service")


@app.on_event("startup")
def startup():
    pool.open(wait=True, timeout=30)
    vendors = fetch_one("SELECT count(*) AS n FROM erp.vendors")["n"]
    if vendors == 0:
        raise RuntimeError("erp.vendors is empty - the seed SQL did not run")
    log.info("mock-erp is ready, %s vendors loaded", vendors)


@app.on_event("shutdown")
def shutdown():
    pool.close()


@app.get("/health")
def health():
    try:
        fetch_one("SELECT 1")
        return {"status": "healthy"}
    except Exception as error:
        return JSONResponse({"status": "unhealthy", "detail": str(error)}, status_code=503)


@app.get("/vendor/{vendor_id}")
def get_vendor(vendor_id: str):
    vendor = fetch_one("SELECT * FROM erp.vendors WHERE vendor_id = %s", (vendor_id,))
    if vendor is None:
        raise HTTPException(404, f"Vendor {vendor_id} not found")
    return reply(vendor)


@app.get("/vendors")
def find_vendors(name: str):
    return reply(fetch_all("SELECT * FROM erp.vendors WHERE vendor_name ILIKE %s ORDER BY vendor_id",
                           (f"%{name.strip()}%",)))


@app.get("/po/{po_number}")
def get_purchase_order(po_number: str):
    po = fetch_one("SELECT * FROM erp.purchase_orders WHERE po_number = %s", (po_number,))
    if po is None:
        raise HTTPException(404, f"Purchase order {po_number} not found")

    po["line_items"] = fetch_all(
        """SELECT line_number, item_code, description, qty, unit_price, currency
           FROM erp.po_line_items WHERE po_number = %s ORDER BY line_number""",
        (po_number,),
    )
    currencies = {line["currency"] for line in po["line_items"]}
    po["currency"] = currencies.pop() if len(currencies) == 1 else None
    return reply(po)


@app.get("/sku/{item_code}")
def get_sku(item_code: str):
    sku = fetch_one("SELECT * FROM erp.sku_master WHERE item_code = %s", (item_code,))
    if sku is None:
        raise HTTPException(404, f"SKU {item_code} not found")
    return reply(sku)
