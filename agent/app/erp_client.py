import httpx

from . import config


class ErpUnavailable(Exception):
    pass


def get(path, params=None):
    try:
        response = httpx.get(config.MOCK_ERP_URL + path, params=params, timeout=15)
    except httpx.RequestError as error:
        raise ErpUnavailable(f"Cannot reach mock-erp at {config.MOCK_ERP_URL}: {error}")
    if response.status_code == 404:
        return None
    if response.status_code >= 500:
        raise ErpUnavailable(f"mock-erp returned {response.status_code}")
    response.raise_for_status()
    return response.json()


def get_purchase_order(po_number):
    return get(f"/po/{po_number}")


def get_vendor(vendor_id):
    return get(f"/vendor/{vendor_id}")


def get_sku(item_code):
    return get(f"/sku/{item_code}")


def find_vendor_by_name(name):
    vendors = get("/vendors", {"name": name}) or []
    return vendors[0] if vendors else None


def is_healthy():
    try:
        return httpx.get(config.MOCK_ERP_URL + "/health", timeout=5).status_code == 200
    except httpx.RequestError:
        return False
