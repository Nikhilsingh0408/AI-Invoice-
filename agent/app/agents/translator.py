import logging
import math

from langdetect import DetectorFactory, detect

from .. import cache, config, database, llm

log = logging.getLogger(__name__)

DetectorFactory.seed = 0


def detect_language(text):
    try:
        return detect(text[:3000])
    except Exception:
        return "en"


def translation_node(state):
    text = state["raw_text"]
    language = detect_language(text)

    if language == "en":
        database.save_translation(state["invoice_id"], "en", None, 1.0)
        return {"language": "en", "translated_text": None, "translation_confidence": 1.0}

    key = cache.make_key("translation", language + "\n" + text)
    # cache
    cached = cache.get(key)
    if cached:
        log.info("Translation of %s taken from Redis", state["file_name"])
        translated, confidence = cached["translated_text"], cached["confidence"]
    else:
        answer = llm.ask("translation", "translate", {"source_language": language, "text": text[:12000]},
                         use_cache=False)
        if answer and answer.get("translated_text"):
            translated = answer["translated_text"]
            try:
                confidence = float(answer.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = round(min(max(confidence, 0.0), 1.0), 3) if math.isfinite(confidence) else 0.0
        else:
            translated, confidence = None, 0.0

        threshold = config.load_rules()["validation_policies"].get("auto_approve_confidence_threshold", 0.95)
        if translated and confidence >= threshold:
            cache.put(key, {"translated_text": translated, "confidence": confidence})
        else:
            log.warning("Translation confidence %s for %s is below %s - not cached, human review needed",
                        confidence, state["file_name"], threshold)

    database.save_translation(state["invoice_id"], language, translated, confidence)
    log.info("Invoice %s: language=%s, translation confidence=%s", state["file_name"], language, confidence)
    return {"language": language, "translated_text": translated, "translation_confidence": confidence}
