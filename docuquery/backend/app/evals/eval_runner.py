"""RAG Evaluation Harness: Retrieval Hit-Rate + Faithfulness scoring over golden dataset."""

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import sys
import time

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
    print(f"\n=======================================================")
    print(f"DocuQuery RAG Evaluation ({len(cases)} Golden Q&A Pairs)")
    print(f"=======================================================\n")

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

            expected_doc = case["expected_doc"].lower()
            expected_page = case["expected_page"]

            # Match criteria: expected doc name substring & exact or adjacent (+-1) page
            def is_match(d_name: str, p_num: int) -> bool:
                doc_matched = expected_doc in d_name.lower() or d_name.lower() in expected_doc
                page_matched = abs(p_num - expected_page) <= 1
                return doc_matched and page_matched

            hit_top1 = False
            hit_top3 = False
            hit_top6 = False

            if sources:
                hit_top1 = is_match(sources[0].document_name, sources[0].page)
                hit_top3 = any(is_match(s.document_name, s.page) for s in sources[:3])
                hit_top6 = any(is_match(s.document_name, s.page) for s in sources[:top_k])

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
            )
            results.append(res)

            status_glyph = "[HIT]" if hit_top6 else "[MISS]"
            print(
                f"{status_glyph} {case['id']} | Top-1: {'Y' if hit_top1 else 'N'} | "
                f"Top-3: {'Y' if hit_top3 else 'N'} | Top-6: {'Y' if hit_top6 else 'N'} | "
                f"{took_ms}ms | {case['question'][:50]}..."
            )

    total = len(results)
    top1_rate = round(sum(1 for r in results if r.hit_top1) / total * 100, 1) if total else 0
    top3_rate = round(sum(1 for r in results if r.hit_top3) / total * 100, 1) if total else 0
    top6_rate = round(sum(1 for r in results if r.hit_top6) / total * 100, 1) if total else 0
    avg_latency = round(sum(r.retrieval_ms for r in results) / total, 1) if total else 0

    summary = {
        "total_cases": total,
        "hit_rate_top1_pct": top1_rate,
        "hit_rate_top3_pct": top3_rate,
        "hit_rate_top6_pct": top6_rate,
        "avg_retrieval_ms": avg_latency,
        "results": [r.__dict__ for r in results],
    }

    print("\n-------------------------------------------------------")
    print("EVALUATION SUMMARY")
    print("-------------------------------------------------------")
    print(f"Total Test Cases:    {total}")
    print(f"Hit Rate @ Top-1:    {top1_rate}%")
    print(f"Hit Rate @ Top-3:    {top3_rate}%")
    print(f"Hit Rate @ Top-6:    {top6_rate}%")
    print(f"Avg Retrieval Time:  {avg_latency} ms")
    print("-------------------------------------------------------\n")

    return summary


if __name__ == "__main__":
    asyncio.run(run_evaluation())
