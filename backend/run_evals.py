"""Eval harness: runs the golden set against the real agent and reports a
broad set of metrics — not just PASS/FAIL, but retrieval-quality, faithfulness,
citation compliance, and efficiency, broken out by question category.

True retrieval precision/recall would need chunk-level relevance labels that
don't exist for this golden set — that's a real, separate annotation task.
What's below are the metrics computable from what the system already
produces: correctness (LLM-judge), a retrieval-precision *proxy* (the
reranker's own relevance judgment on what it actually retrieved, logged by
retrieval.py), faithfulness (a second judge call checking the answer against
retrieved context, reimplementing Ragas's faithfulness idea without needing
the ragas package — see run_ragas.py for why that package can't be installed
here), answer relevancy, and efficiency (rounds, search-budget usage, latency).
"""

import asyncio
import logging
import os
import re
import time

import pandas as pd
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_deepseek import ChatDeepSeek

from graph import graph_builder
from prompts import SYSTEM_PROMPT

load_dotenv()

judge_llm = ChatDeepSeek(model="deepseek-chat", temperature=0, api_key=os.getenv("HUGIN"))

CITATION_RE = re.compile(r"\[Source:.*?\|\s*Year:.*?\]")
BUDGET_CAPPED_MARKER = "Search budget for this question has been used"


class RetrievalCapture(logging.Handler):
    """Captures the reranker/Pinecone scores retrieval.py already logs for
    every search, so we get a retrieval-quality signal without touching
    production code or needing new labeled data."""

    def __init__(self):
        super().__init__()
        self.rerank_scores = []
        self.pinecone_best_scores = []

    def emit(self, record):
        try:
            if record.msg.startswith("rerank query="):
                self.rerank_scores.append(list(record.args[2]))
            elif record.msg.startswith("search_knowledge_base query="):
                self.pinecone_best_scores.append(float(record.args[2]))
        except Exception:
            pass

    def reset(self):
        self.rerank_scores = []
        self.pinecone_best_scores = []


_capture = RetrievalCapture()
logging.getLogger("retrieval").addHandler(_capture)
logging.getLogger("retrieval").setLevel(logging.INFO)


async def run_janus(question: str):
    """Runs the actual agent (no API server needed) and captures everything
    needed for the metrics below, not just the final answer."""
    graph = graph_builder.compile()
    inputs = {"messages": [SYSTEM_PROMPT, HumanMessage(content=question)]}

    final_output = ""
    contexts = []
    num_rounds = 0
    num_kb_calls = 0
    budget_capped = False

    async for chunk in graph.astream(inputs, stream_mode="values"):
        last_msg = chunk["messages"][-1]
        if last_msg.type == "ai" and last_msg.tool_calls:
            num_rounds += 1
            num_kb_calls += sum(1 for tc in last_msg.tool_calls if tc["name"] == "search_knowledge_base")
        elif last_msg.type == "tool":
            contexts.append(str(last_msg.content))
            if BUDGET_CAPPED_MARKER in str(last_msg.content):
                budget_capped = True
        elif last_msg.type == "ai" and not last_msg.tool_calls:
            final_output = last_msg.content

    return {
        "answer": final_output,
        "contexts": contexts,
        "num_rounds": num_rounds,
        "num_kb_calls": num_kb_calls,
        "budget_capped": budget_capped,
    }


async def grade_correctness(question, janus_answer, ground_truth) -> bool:
    prompt = f"""
    You are a strict technical judge for Formula 1 regulations.

    QUESTION: {question}
    GROUND TRUTH: {ground_truth}
    CANDIDATE ANSWER: {janus_answer}

    Evaluation Criteria:
    1. Does the Candidate Answer contain the core facts from the Ground Truth?
    2. Are the specific numbers (e.g., 350 kW, 3000 MJ/h) correct?
    3. Ignore extra conversational filler.

    Respond ONLY with "PASS" or "FAIL".
    """
    response = await judge_llm.ainvoke([HumanMessage(content=prompt)])
    return response.content.strip().upper().startswith("PASS")


async def judge_faithfulness(answer: str, contexts: list) -> bool:
    """Reimplements the idea behind Ragas's faithfulness metric directly:
    does the answer make a specific factual claim not backed by what was
    actually retrieved (search_knowledge_base OR search_web results, both
    captured as tool-message context)? If no tool was called at all — some
    web_search questions are answered from the model's own general
    knowledge without a search — there's no context to check groundedness
    against, so it's counted as faithful by default rather than penalized."""
    if not contexts:
        return True
    # 60000 chars, not 12000: comparison questions run 2-4 searches, each
    # producing several thousand characters of context on its own — the
    # smaller cap was silently truncating away the very context a
    # cross-year claim depended on, then flagging that claim as
    # unsupported. Confirmed by inspecting the flagged answers directly:
    # they were well-grounded and even self-aware about real gaps
    # ("the 2025 DRS sporting regulation specifics weren't fully
    # captured") — not fabricating anything.
    joined = "\n---\n".join(contexts)[:60000]
    prompt = f"""
    RETRIEVED CONTEXT:
    {joined}

    GENERATED ANSWER:
    {answer}

    Does the GENERATED ANSWER make any specific factual claim (a number, a rule,
    a name) that is NOT supported by the RETRIEVED CONTEXT above? Minor
    phrasing or summarization is fine — only flag claims with no support in
    the context at all.

    IMPORTANT: a computed comparison, delta, or ratio (e.g. "increased from
    120 kW to 350 kW", "nearly threefold increase", "30 kg reduction") is
    FAITHFUL as long as the individual numbers it's built from each appear in
    the context, even if the comparison phrase itself isn't stated verbatim.
    Only flag a claim as unfaithful if it introduces a specific fact, number,
    or name that does not appear anywhere in the context at all.

    Respond with a single word: "FAITHFUL" or "UNFAITHFUL".
    """
    response = await judge_llm.ainvoke([HumanMessage(content=prompt)])
    return "UNFAITHFUL" not in response.content.strip().upper()


async def judge_relevancy(question: str, answer: str) -> bool:
    prompt = f"""
    QUESTION: {question}
    ANSWER: {answer}

    Does the ANSWER directly address what was asked in the QUESTION, regardless
    of whether it is factually correct? Respond with a single word: "RELEVANT"
    or "IRRELEVANT".
    """
    response = await judge_llm.ainvoke([HumanMessage(content=prompt)])
    return "IRRELEVANT" not in response.content.strip().upper()


def retrieval_precision_proxy(rerank_scores: list) -> float | None:
    """Fraction of all reranked chunks (across every search this question
    triggered) that the cross-encoder itself scored as relevant (>0). Not
    true precision (no human relevance labels exist for this golden set) —
    a proxy computed from a signal the system already produces and, until
    now, only used for sorting."""
    all_scores = [s for batch in rerank_scores for s in batch]
    if not all_scores:
        return None
    return sum(1 for s in all_scores if s > 0) / len(all_scores)


async def main():
    print("STARTING JANUS 2.0 EVALUATION RUN...")

    df = pd.read_csv("test_data/f1_golden.csv")
    results = []

    for index, row in df.iterrows():
        q = row["question"]
        truth = row["ground_truth"]
        category = row.get("category", "rule")
        difficulty = row.get("difficulty", "")

        print(f"\n[Test {index + 1}/{len(df)}] ({category}/{difficulty}) {q}")

        _capture.reset()
        t0 = time.perf_counter()
        try:
            run = await run_janus(q)
        except Exception as e:
            run = {
                "answer": f"ERROR: {e}",
                "contexts": [],
                "num_rounds": 0,
                "num_kb_calls": 0,
                "budget_capped": False,
            }
        elapsed = time.perf_counter() - t0

        answer = run["answer"]
        correct = await grade_correctness(q, answer, truth)
        faithful = await judge_faithfulness(answer, run["contexts"])
        relevant = await judge_relevancy(q, answer)
        has_citation = bool(CITATION_RE.search(answer))
        precision_proxy = retrieval_precision_proxy(_capture.rerank_scores)

        print(
            f"   correctness={'PASS' if correct else 'FAIL'} "
            f"faithful={faithful} relevant={relevant} citation={has_citation} "
            f"rounds={run['num_rounds']} kb_calls={run['num_kb_calls']} "
            f"budget_capped={run['budget_capped']} "
            f"precision_proxy={precision_proxy} latency={elapsed:.1f}s"
        )

        results.append({
            "question": q,
            "category": category,
            "difficulty": difficulty,
            "generated_answer": answer,
            "ground_truth": truth,
            "correct": correct,
            "faithful": faithful,
            "relevant": relevant,
            "has_citation": has_citation,
            "num_rounds": run["num_rounds"],
            "num_kb_calls": run["num_kb_calls"],
            "budget_capped": run["budget_capped"],
            "retrieval_precision_proxy": precision_proxy,
            "latency_seconds": round(elapsed, 2),
        })

    results_df = pd.DataFrame(results)
    results_df.to_csv("test_data/eval_report.csv", index=False)

    print("\n" + "=" * 60)
    print("OVERALL")
    print("=" * 60)
    print(f"Correctness (accuracy):     {results_df['correct'].mean() * 100:.1f}%")
    print(f"Faithfulness:               {results_df['faithful'].mean() * 100:.1f}%")
    print(f"Answer relevancy:           {results_df['relevant'].mean() * 100:.1f}%")
    print(f"Citation compliance:        {results_df['has_citation'].mean() * 100:.1f}%")
    non_null_precision = results_df["retrieval_precision_proxy"].dropna()
    if len(non_null_precision):
        print(f"Retrieval precision proxy:  {non_null_precision.mean() * 100:.1f}%")
    print(f"Avg rounds per question:    {results_df['num_rounds'].mean():.1f}")
    print(f"Avg KB searches/question:   {results_df['num_kb_calls'].mean():.1f}")
    print(f"Budget-cap hit rate:        {results_df['budget_capped'].mean() * 100:.1f}%")
    print(f"Avg latency:                {results_df['latency_seconds'].mean():.1f}s")

    print("\n" + "=" * 60)
    print("BY CATEGORY")
    print("=" * 60)
    by_cat = results_df.groupby("category").agg(
        n=("question", "count"),
        accuracy=("correct", "mean"),
        faithfulness=("faithful", "mean"),
        relevancy=("relevant", "mean"),
        avg_rounds=("num_rounds", "mean"),
        avg_latency=("latency_seconds", "mean"),
    )
    print(by_cat.to_string())

    print("\nReport saved to test_data/eval_report.csv")


if __name__ == "__main__":
    asyncio.run(main())
