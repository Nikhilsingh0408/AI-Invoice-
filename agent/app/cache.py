import hashlib
import json
import logging

import redis

from . import config

log = logging.getLogger(__name__)

client = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, decode_responses=True,
                     socket_connect_timeout=3, socket_timeout=3)


def make_key(prefix, text):
    return prefix + ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def get(key):
    try:
        value = client.get(key)
        return json.loads(value) if value else None
    except Exception as error:
        log.warning("Redis read failed: %s", error)
        return None


def put(key, value):
    try:
        client.set(key, json.dumps(value, default=str), ex=config.REDIS_TTL_SECONDS)
    except Exception as error:
        log.warning("Redis write failed: %s", error)


def ping():
    try:
        return client.ping()
    except Exception:
        return False
