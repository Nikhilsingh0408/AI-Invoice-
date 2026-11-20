import os
from pathlib import Path
from urllib.parse import quote

import yaml

POSTGRES_URL = "postgresql://{}:{}@{}:{}/{}".format(
    quote(os.environ["POSTGRES_USER"], safe=""),
    quote(os.environ["POSTGRES_PASSWORD"], safe=""),
    os.getenv("POSTGRES_HOST", "postgres"),
    os.getenv("POSTGRES_PORT", "5432"),
    quote(os.getenv("POSTGRES_DB", "invoice_auditor"), safe=""),
)

QDRANT_URL = "http://{}:{}".format(os.getenv("QDRANT_HOST", "qdrant"), os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = "invoice_chunks"

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_TTL_SECONDS = 86400

MOCK_ERP_URL = os.getenv("MOCK_ERP_URL", "http://mock-erp:8000")

INCOMING_DIR = Path(os.getenv("INCOMING_DIR", "/data/incoming"))
PROCESSED_DIR = Path(os.getenv("PROCESSED_DIR", "/data/processed"))
PENDING_DIR = Path(os.getenv("PENDING_DIR", "/data/pending"))
REJECTED_DIR = Path(os.getenv("REJECTED_DIR", "/data/rejected"))
FAILED_DIR = Path(os.getenv("FAILED_DIR", "/data/failed"))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "/reports"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))

AWS_REGION = os.getenv("AWS_REGION_NAME", "us-east-1")
REASONING_MODEL = os.getenv("LLM_REASONING_MODEL", "global.amazon.nova-2-lite-v1:0")
RAG_MODEL = os.getenv("LLM_RAG_MODEL", "global.amazon.nova-2-lite-v1:0")
TRANSLATION_MODEL = os.getenv("LLM_TRANSLATION_MODEL", "global.amazon.nova-2-lite-v1:0")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "cohere.embed-english-v3")

OCR_LANGUAGES = "eng+spa+deu+fra"
WATCH_INTERVAL_SECONDS = 5

RAG_SCORE_THRESHOLD = 0.7
RAG_MAX_RETRIES = 3
RAG_TOP_K = 6

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def read_yaml(name):
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Config file {path} is missing. Is the config folder mounted?")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_rules():
    return read_yaml("rules.yaml")


def load_prompt(file_name, prompt_name):
    prompts = read_yaml(f"prompts/{file_name}.yaml")
    personas = read_yaml("personas.yaml")
    prompt = prompts[prompt_name]
    persona = personas[prompt["persona"]]
    return persona["system"], prompt["user"], persona.get("temperature", 0.0), persona.get("model_role", "reasoning")
