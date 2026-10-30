"""RAG Evaluation Harness: Retrieval Hit-Rate + Faithfulness scoring over golden dataset."""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.services.retrieval import retrieve

logger = logging.getLogger(__name__)

GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"


@dataclass
class EvalResult:
    id: str
    question: str
    category: str
    expected_doc: str
    expected_page: int
    hit_top1: bool
    hit_top3: bool
    hit_top6: bool
    retrieval_ms: int
    retrieved_docs: list[str]
    retrieved_pages: list[int]
    methods: list[str]
    citation_accuracy: float | None = None


def _simple_overlap_score(prediction: str, ground_truth: str) -> float:
    """Compute token-level F1 / overlap between prediction and ground truth."""
    pred_words = set(re.findall(r"\w+", prediction.lower()))
    gt_words = set(re.findall(r"\w+", ground_truth.lower()))
    if not gt_words or not pred_words:
        return 0.0
    common = pred_words.intersection(gt_words)
    precision = len(common) / len(pred_words)
    recall = len(common) / len(gt_words)
    if precision + recall == 0:
        return 0.0
    return round(2 * (precision * recall) / (precision + recall), 3)


async def run_evaluation(top_k: int = 6) -> dict:
    settings = get_settings()
    with open(GOLDEN_DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    results: list[EvalResult] = []
    print("\n=======================================================")
    print(f"DocuQuery RAG Evaluation ({len(cases)} Golden Q&A Pairs)")
    print("=======================================================\n")

    async with session_factory() as db:
        for idx, case in enumerate(cases, 1):
            t0 = time.perf_counter()
            try:
                sources, methods = await retrieve(db, case["question"], top_k=top_k)
            except Exception as e:
                logger.error("Retrieval failed for eval case %s: %s", case["id"], e)
                sources, methods = [], []
            took_ms = round((time.perf_counter() - t0) * 1000)

            retrieved_docs = [s.document_name for s in sources]
            retrieved_pages = [s.page for s in sources]

            expected_doc = (case.get("expected_doc") or "").lower()
            expected_page = case.get("expected_page")
            unanswerable = expected_doc == ""

            # Match criteria: expected doc name substring & exact or adjacent (+-1) page.
            # Defaults bind the loop iteration's values (B023-safe closure).
            def is_match(d_name: str, p_num: int, _doc=expected_doc, _page=expected_page) -> bool:
                doc_matched = bool(_doc) and (
                    _doc in d_name.lower() or d_name.lower() in _doc
                )
                page_matched = _page is not None and abs(p_num - _page) <= 1
                return doc_matched and page_matched

            hit_top1 = False
            hit_top3 = False
            hit_top6 = False
            citation_score = None

            if unanswerable:
                # Unanswerable questions are scored by the judge path, not by
                # retrieval hits; exclude them from the hit-rate denominators.
                pass
            elif sources:
                hit_top1 = is_match(sources[0].document_name, sources[0].page)
                hit_top3 = any(is_match(s.document_name, s.page) for s in sources[:3])
                hit_top6 = any(is_match(s.document_name, s.page) for s in sources[:top_k])
                # Citation accuracy: does the chunk behind the first matched
                # citation actually contain the ground-truth claim (token F1)?
                for s in sources[:top_k]:
                    if is_match(s.document_name, s.page):
                        # Containment: share of ground-truth claim tokens present
                        # in the cited chunk ("does the citation contain the claim").
                        gt_tokens = set(re.findall(r"\w+", case.get("ground_truth", "").lower()))
                        if gt_tokens:
                            chunk_tokens = set(re.findall(r"\w+", s.content.lower()))
                            citation_score = round(len(gt_tokens & chunk_tokens) / len(gt_tokens), 3)
                        break

            res = EvalResult(
                id=case["id"],
                question=case["question"],
                category=case["category"],
                expected_doc=case["expected_doc"],
                expected_page=case["expected_page"],
                hit_top1=hit_top1,
                hit_top3=hit_top3,
                hit_top6=hit_top6,
                retrieval_ms=took_ms,
                retrieved_docs=retrieved_docs,
                retrieved_pages=retrieved_pages,
                methods=methods,
                citation_accuracy=citation_score,
            )
            results.append(res)

            status_glyph = "[HIT]" if hit_top6 else "[MISS]"
            print(
                f"{status_glyph} {case['id']} | Top-1: {'Y' if hit_top1 else 'N'} | "
                f"Top-3: {'Y' if hit_top3 else 'N'} | Top-6: {'Y' if hit_top6 else 'N'} | "
                f"{took_ms}ms | {case['question'][:50]}..."
            )

    total = len(results)
    answerable = [r for r in results if r.expected_doc]
    unanswerable_count = total - len(answerable)
    a_total = len(answerable)
    answerable = [r for r in results if r.expected_doc]
    unanswerable_count = total - len(answerable)
    a_total = len(answerable)
    top1_rate = round(sum(1 for r in answerable if r.hit_top1) / a_total * 100, 1) if a_total else 0
    top3_rate = round(sum(1 for r in answerable if r.hit_top3) / a_total * 100, 1) if a_total else 0
    top6_rate = round(sum(1 for r in answerable if r.hit_top6) / a_total * 100, 1) if a_total else 0
    avg_latency = round(sum(r.retrieval_ms for r in results) / total, 1) if total else 0
    scored = [r.citation_accuracy for r in answerable if r.hit_top6 and r.citation_accuracy is not None]
    citation_accuracy = round(sum(scored) / len(scored) * 100, 1) if scored else 0.0
    min_hit6 = settings.eval_min_hit6
    gate_passed = (top6_rate / 100.0) >= min_hit6

    summary = {
        "total_cases": total,
        "answerable_cases": a_total,
        "unanswerable_cases": unanswerable_count,
        "hit_rate_top1_pct": top1_rate,
        "hit_rate_top3_pct": top3_rate,
        "hit_rate_top6_pct": top6_rate,
        "citation_accuracy_pct": citation_accuracy,
        "avg_retrieval_ms": avg_latency,
        "min_hit6_threshold": min_hit6,
        "gate_passed": gate_passed,
        "results": [r.__dict__ for r in results],
    }

    print("\n-------------------------------------------------------")
    print("EVALUATION SUMMARY")
    print("-------------------------------------------------------")
    print(f"Total Test Cases:    {total} ({a_total} answerable, {unanswerable_count} unanswerable)")
    print(f"Hit Rate @ Top-1:    {top1_rate}%")
    print(f"Hit Rate @ Top-3:    {top3_rate}%")
    print(f"Hit Rate @ Top-6:    {top6_rate}%")
    print(f"Citation Accuracy:   {citation_accuracy}% (ground-truth overlap of matched citations)")
    print(f"Avg Retrieval Time:  {avg_latency} ms")
    print(f"Gate (hit@6 >= {min_hit6}):     {'PASS' if gate_passed else 'FAIL'}")
    print("-------------------------------------------------------\n")

    return summary


if __name__ == "__main__":
    asyncio.run(run_evaluation())


# --- LLM-judged metrics (optional; EVAL_JUDGE_ENABLED=True) -------------------

_JUDGE_SYSTEM = (
    "You are a strict RAG quality judge. Given a question, retrieved document "
    "excerpts, and a generated answer, return ONLY one JSON object: "
    '{"faithfulness": <1-5 integer>, "answer_relevance": <1-5 integer>, '
    '"reason": "<one sentence>"}. '
    "faithfulness: 5 = every claim in the answer is directly supported by the "
    "excerpts; 1 = the answer contradicts or fabricates beyond them. "
    "answer_relevance: 5 = the answer directly addresses the question; 1 = "
    "unrelated or refuses without cause. No markdown fences."
)


async def run_judged_evaluation(top_k: int = 6, max_cases: int | None = None) -> dict:
    """Full answer-generation + LLM-judge pass: faithfulness, answer relevance,
    and refusal correctness for unanswerable cases. Much slower than the
    retrieval-only evaluation (one generation + one judgement per case)."""
    settings = get_settings()
    import openai

    with open(GOLDEN_DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)
    if max_cases:
        cases = cases[:max_cases]
    answerable = [c for c in cases if c.get("expected_doc")]

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    judge_model = settings.eval_judge_model or settings.llm_model
    client = openai.AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)

    faithfulness: list[float] = []
    relevance: list[float] = []
    refusal_correct = 0
    unanswerable_judged = 0
    judged = 0

    async with session_factory() as db:
        for case in cases:
            try:
                sources, _methods = await retrieve(db, case["question"], top_k=top_k)
            except Exception as exc:
                logger.error("Retrieval failed for %s: %s", case["id"], exc)
                continue
            excerpts = "\n\n".join(
                f"[{i}] ({s.document_name}, {s.source_label})\n{s.content[:800]}"
                for i, s in enumerate(sources, start=1)
            ) or "(no excerpts)"
            messages = [
                {"role": "system", "content": (
                    "Answer strictly from the numbered document excerpts. Cite "
                    "excerpts as [n]. If they do not contain the answer, reply "
                    "exactly: I don't have enough information in the uploaded "
                    "documents to answer that."
                )},
                {"role": "user", "content": f"CONTEXT:\n{excerpts}\n\nQUESTION: {case['question']}"},
            ]

            try:
                completion = await client.chat.completions.create(
                    model=settings.llm_model, messages=messages,
                    temperature=0.0, max_tokens=500, stream=False,
                )
                answer = (completion.choices[0].message.content or "").strip()
            except Exception as exc:
                logger.error("Generation failed for %s: %s", case["id"], exc)
                continue

            is_unanswerable = not case.get("expected_doc")
            refused = answer.startswith("I don't have enough information")
            if is_unanswerable:
                unanswerable_judged += 1
                if refused:
                    refusal_correct += 1
                continue

            try:
                verdict = await client.chat.completions.create(
                    model=judge_model,
                    messages=[
                        {"role": "system", "content": _JUDGE_SYSTEM},
                        {"role": "user", "content": (
                            f"QUESTION: {case['question']}\n\nEXCERPTS:\n{excerpts}\n\n"
                            f"ANSWER: {answer}"
                        )},
                    ],
                    temperature=0.0, max_tokens=200, stream=False,
                )
                scores = json.loads(
                    (verdict.choices[0].message.content or "{}").strip().strip("`")
                    .replace("json\n", "", 1)
                )
                faithfulness.append(float(scores.get("faithfulness", 0)))
                relevance.append(float(scores.get("answer_relevance", 0)))
                judged += 1
            except Exception as exc:
                logger.warning("Judging failed for %s: %s", case["id"], exc)

    await engine.dispose()
    summary = {
        "answerable_cases": len(answerable),
        "judged_cases": judged,
        "unanswerable_cases": unanswerable_judged,
        "refusal_correct_rate_pct": round(
            refusal_correct / unanswerable_judged * 100, 1
        ) if unanswerable_judged else 0.0,
        "faithfulness_avg_1to5": round(sum(faithfulness) / len(faithfulness), 2) if faithfulness else 0.0,
        "answer_relevance_avg_1to5": round(sum(relevance) / len(relevance), 2) if relevance else 0.0,
        "judge_model": judge_model,
    }
    print("\n===== JUDGED EVALUATION =====")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return summary
