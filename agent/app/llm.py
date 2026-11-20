import json
import logging
import re

from langchain_aws import BedrockEmbeddings, ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from . import cache, config

log = logging.getLogger(__name__)

MODELS = {
    "reasoning": config.REASONING_MODEL,
    "rag": config.RAG_MODEL,
    "translation": config.TRANSLATION_MODEL,
}

chat_models = {}
embedder = None


def model_id(model):
    return model.split("/", 1)[1] if model.startswith("bedrock/") else model


def get_chat(model, temperature):
    key = (model, temperature)
    if key not in chat_models:
        chat_models[key] = ChatBedrock(
            model_id=model,
            region_name=config.AWS_REGION,
            temperature=temperature,
            beta_use_converse_api=True,
        )
    return chat_models[key]


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def ask(prompt_file, prompt_name, values, use_cache=True):
    system, template, temperature, role = config.load_prompt(prompt_file, prompt_name)
    user = template.format(**values)
    model = model_id(MODELS[role])

    key = cache.make_key("llm", model + "\n" + system + "\n" + user)
    # cache
    cached = cache.get(key) if use_cache else None
    if cached is not None:
        log.info("LLM cache hit for %s/%s", prompt_file, prompt_name)
        return cached

    try:
        response = get_chat(model, temperature).invoke([SystemMessage(content=system), HumanMessage(content=user)])
        text = response.content
        if isinstance(text, list):
            text = "".join(block.get("text", "") for block in text if isinstance(block, dict))
    except Exception as error:
        log.warning("LLM call %s/%s failed: %s", prompt_file, prompt_name, error)
        return None

    answer = parse_json(text)
    if answer is None:
        log.warning("LLM %s/%s did not return JSON: %s", prompt_file, prompt_name, text[:200])
        return None

    if use_cache:
        cache.put(key, answer)
    log.info("LLM %s/%s answered using %s", prompt_file, prompt_name, model)
    return answer


def embed(texts):
    global embedder
    if embedder is None:
        embedder = BedrockEmbeddings(model_id=model_id(config.EMBEDDING_MODEL), region_name=config.AWS_REGION)
    return embedder.embed_documents(texts)
