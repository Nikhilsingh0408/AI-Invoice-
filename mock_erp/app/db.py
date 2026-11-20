import json
import os
from urllib.parse import quote

from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DB_URL = "postgresql://{}:{}@{}:{}/{}".format(
    quote(os.environ["POSTGRES_USER"], safe=""),
    quote(os.environ["POSTGRES_PASSWORD"], safe=""),
    os.getenv("POSTGRES_HOST", "postgres"),
    os.getenv("POSTGRES_PORT", "5432"),
    quote(os.getenv("POSTGRES_DB", "invoice_auditor"), safe=""),
)

pool = ConnectionPool(DB_URL, min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row})


def fetch_all(sql, params=None):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql, params=None):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def reply(data):
    return JSONResponse(json.loads(json.dumps(data, default=str)))
