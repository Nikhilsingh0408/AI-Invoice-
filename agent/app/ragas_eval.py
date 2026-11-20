import logging
import math
import os
import json
import subprocess
import sys

from langchain_aws import BedrockEmbeddings, ChatBedrock

from . import config, database, llm, rag_runtime as rag

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

log = logging.getLogger(__name__)

# the tables are created here so the existing database does not need a reset
TABLES = """
CREATE TABLE IF NOT EXISTS audit.rag_test_questions (
    id         BIGSERIAL PRIMARY KEY,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit.ragas_results (
    id                BIGSERIAL PRIMARY KEY,
    run_id            INTEGER NOT NULL,
    run_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    question          TEXT NOT NULL,
    expected_answer   TEXT,
    rag_answer        TEXT,
    route             VARCHAR(16),
    faithfulness      NUMERIC(4,3),
    answer_relevancy  NUMERIC(4,3),
    context_precision NUMERIC(4,3),
    context_recall    NUMERIC(4,3)
);
"""

METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
LIVE_METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision"]

tables_ready = False
metrics = {}


def setup():
    global tables_ready
    if tables_ready:
        return
    database.run(TABLES)
    if database.fetch_one("SELECT count(*) AS n FROM audit.rag_test_questions")["n"] == 0:
        for item in config.read_yaml("rag_test_questions.yaml") or []:
            add_question(item["question"], item["answer"])
        log.info("Added the starter RAG test questions")
    tables_ready = True


def list_questions():
    setup()
    return database.fetch_all("SELECT id, question, answer FROM audit.rag_test_questions ORDER BY id")


def add_question(question, answer):
    database.run("INSERT INTO audit.rag_test_questions (question, answer) VALUES (%s, %s)", (question, answer))


def delete_question(question_id):
    setup()
    database.run("DELETE FROM audit.rag_test_questions WHERE id = %s", (question_id,))


def get_metrics():
    # ragas is imported here so the rest of the agent still starts if it is missing
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy

    if "reference" not in metrics:
        # a separate model object, because ragas changes its settings while scoring
        judge = LangchainLLMWrapper(ChatBedrock(model_id=llm.model_id(config.REASONING_MODEL),
                                                region_name=config.AWS_REGION, temperature=0.0,
                                                beta_use_converse_api=True))
        embeddings = LangchainEmbeddingsWrapper(
            BedrockEmbeddings(model_id=llm.model_id(config.EMBEDDING_MODEL), region_name=config.AWS_REGION))
        metrics["reference"] = {
            "faithfulness": Faithfulness(llm=judge),
            "answer_relevancy": ResponseRelevancy(llm=judge, embeddings=embeddings),
            "context_precision": LLMContextPrecisionWithReference(llm=judge),
            "context_recall": LLMContextRecall(llm=judge),
        }
    return metrics["reference"]


def get_live_metrics():
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

    if "live" not in metrics:
        judge = LangchainLLMWrapper(ChatBedrock(model_id=llm.model_id(config.REASONING_MODEL),
                                                region_name=config.AWS_REGION, temperature=0.0,
                                                beta_use_converse_api=True))
        embeddings = LangchainEmbeddingsWrapper(
            BedrockEmbeddings(model_id=llm.model_id(config.EMBEDDING_MODEL), region_name=config.AWS_REGION))
        metrics["live"] = {
            "faithfulness": Faithfulness(llm=judge),
            "answer_relevancy": ResponseRelevancy(llm=judge, embeddings=embeddings),
            "context_precision": LLMContextPrecisionWithoutReference(llm=judge),
        }
    return metrics["live"]


def score_answer(question, rag_answer, contexts, expected_answer):
    from ragas import SingleTurnSample

    sample = SingleTurnSample(user_input=question, response=rag_answer, retrieved_contexts=contexts,
                              reference=expected_answer)
    scores = {}
    for name, metric in get_metrics().items():
        try:
            value = float(metric.single_turn_score(sample))
            scores[name] = None if math.isnan(value) else round(value, 3)
        except Exception as error:
            log.warning("RAGAS %s failed for %r: %s", name, question[:60], error)
            scores[name] = None
    return scores


def score_live_answer_direct(question, rag_answer, contexts):
    from ragas import SingleTurnSample

    sample = SingleTurnSample(user_input=question, response=rag_answer, retrieved_contexts=contexts)
    scores = {}
    for name, metric in get_live_metrics().items():
        try:
            value = float(metric.single_turn_score(sample))
            scores[name] = round(value, 3) if math.isfinite(value) and 0 <= value <= 1 else None
        except Exception as error:
            log.warning("Live RAGAS %s failed for %r: %s", name, question[:60], error)
            scores[name] = None
    return scores


def score_live_answer(question, rag_answer, contexts):
    from . import cache

    key = cache.make_key("ragas-live", json.dumps(
        [question, rag_answer, contexts, config.REASONING_MODEL, config.EMBEDDING_MODEL], ensure_ascii=False))
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        finished = subprocess.run(
            [sys.executable, "-m", "app.ragas_eval", "--score-live"],
            input=json.dumps({"question": question, "answer": rag_answer, "contexts": contexts},
                             ensure_ascii=False),
            stdout=subprocess.PIPE, text=True, timeout=300,
        )
        if finished.returncode != 0:
            raise RuntimeError("RAGAS scoring subprocess failed")
        scores = json.loads(finished.stdout.strip().splitlines()[-1])
    except Exception as error:
        log.warning("Live RAGAS scoring failed for %r: %s", question[:60], error)
        scores = {name: None for name in LIVE_METRIC_NAMES}
    if any(value is not None for value in scores.values()):
        cache.put(key, scores)
    return scores


def evaluate_all():
    # ragas patches asyncio when it is imported, and that fails inside uvicorn's uvloop,
    # so the evaluation runs in its own python process (see the bottom of this file)
    finished = subprocess.run([sys.executable, "-m", "app.ragas_eval"], stdout=subprocess.PIPE,
                              text=True, timeout=1800)
    if finished.returncode != 0:
        raise RuntimeError("The RAGAS evaluation failed, see the agent logs")
    return int(finished.stdout.strip().splitlines()[-1])


def run_evaluation():
    questions = list_questions()
    run_id = database.fetch_one("SELECT coalesce(max(run_id), 0) + 1 AS n FROM audit.ragas_results")["n"]
    for row in questions:
        try:
            # the RAG graph is called directly so test runs are not saved as user questions
            result = rag.rag_graph.invoke({"question": row["question"]})
        except Exception as error:
            log.warning("RAG failed for %r: %s", row["question"][:60], error)
            result = {"answer": f"(the RAG failed: {error})", "route": None}
        contexts = [c["chunk_text"] for c in result.get("chunks", [])] or [result.get("context") or "(nothing found)"]
        try:
            scores = score_answer(row["question"], result["answer"], contexts, row["answer"])
        except Exception as error:
            log.warning("RAGAS could not score %r: %s", row["question"][:60], error)
            scores = {name: None for name in METRIC_NAMES}
        log.info("RAGAS run %d: %r -> %s", run_id, row["question"][:60], scores)
        database.run("""INSERT INTO audit.ragas_results
                        (run_id, question, expected_answer, rag_answer, route, faithfulness, answer_relevancy,
                         context_precision, context_recall)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (run_id, row["question"], row["answer"], result["answer"], result["route"],
                      scores["faithfulness"], scores["answer_relevancy"],
                      scores["context_precision"], scores["context_recall"]))
    return run_id


def list_runs():
    setup()
    return database.fetch_all("""SELECT run_id, min(run_at) AS run_at, count(*) AS questions,
                                        round(avg(faithfulness), 3) AS faithfulness,
                                        round(avg(answer_relevancy), 3) AS answer_relevancy,
                                        round(avg(context_precision), 3) AS context_precision,
                                        round(avg(context_recall), 3) AS context_recall
                                 FROM audit.ragas_results GROUP BY run_id ORDER BY run_id DESC""")


def run_results(run_id):
    setup()
    return database.fetch_all("""SELECT question, expected_answer, rag_answer, route, faithfulness,
                                        answer_relevancy, context_precision, context_recall
                                 FROM audit.ragas_results WHERE run_id = %s ORDER BY id""", (run_id,))


# started by evaluate_all() as: python -m app.ragas_eval
if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.argv[1:] == ["--score-live"]:
        payload = json.load(sys.stdin)
        print(json.dumps(score_live_answer_direct(payload["question"], payload["answer"],
                                                  payload.get("contexts", []))))
    else:
        database.open_pools()
        print(run_evaluation())
        database.close_pools()
