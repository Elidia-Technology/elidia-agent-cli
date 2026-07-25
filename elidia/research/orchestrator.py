"""YOYO research orchestrator — 5-agent pipeline for deep research.

Pipeline stages:
  1. Decomposer — breaks question into sub-questions
  2. Searcher   — searches web/MCP sources for each sub-question
  3. Analyzer   — extracts key findings from search results
  4. Synthesizer — structures findings into a coherent report
  5. Verifier   — fact-checks claims and flags unsupported statements
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)

DECOMPOSE_PROMPT = (
    "You are a research question decomposer. Break the following research "
    "question into 3-6 specific, searchable sub-questions.\n\n"
    "Rules:\n"
    "- Each sub-question should be independently searchable on the web.\n"
    "- Cover different angles of the original question.\n"
    "- Be specific enough to get useful search results.\n\n"
    "Return ONLY a JSON array of strings:\n"
    '["sub-question 1", "sub-question 2", ...]\n\n'
    "Research question: {question}\n\nJSON:"
)

ANALYZE_PROMPT = (
    "You are a research analyst. Extract the key findings from these search "
    "results for the research question.\n\n"
    "Research question: {question}\n\n"
    "Search results:\n{results}\n\n"
    "Extract:\n"
    "1. Key facts (with source URLs for citation)\n"
    "2. Statistics and data points\n"
    "3. Expert opinions or analysis\n"
    "4. Areas of agreement and disagreement\n"
    "5. Gaps in available information\n\n"
    "Return a structured analysis in markdown. Include inline citations [source URL]."
)

SYNTHESIZE_PROMPT = (
    "You are a research synthesizer. Compile the following analyses into a "
    "comprehensive, well-structured research report.\n\n"
    "Original question: {question}\n\n"
    "Sub-question analyses:\n{analyses}\n\n"
    "Create a report with:\n"
    "- Executive summary (2-3 sentences)\n"
    "- Key findings (organized by theme)\n"
    "- Supporting evidence (with inline citations)\n"
    "- Limitations and gaps\n"
    "- Conclusion\n"
    "- Sources list\n\n"
    "Use markdown formatting. Cite sources inline with [title](url)."
)

VERIFY_PROMPT = (
    "You are a research verifier. Review this research report for accuracy.\n\n"
    "Report:\n{report}\n\n"
    "Check:\n"
    "1. Are claims supported by the cited sources?\n"
    "2. Are there logical inconsistencies?\n"
    "3. Are important caveats or limitations missing?\n"
    "4. Are there unsupported generalizations?\n\n"
    "Return a JSON object:\n"
    '{{"verified": true/false, "issues": ["issue 1", ...], '
    '"confidence": 0.0-1.0, "suggestions": ["suggestion 1", ...]}}\n\nJSON:'
)


@dataclass
class SearchResult:
    query: str
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""


@dataclass
class ResearchState:
    question: str
    sub_questions: list[str] = field(default_factory=list)
    search_results: list[SearchResult] = field(default_factory=list)
    analysis: str = ""
    report: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)
    elapsed_ms: int = 0
    total_tokens: int = 0


@dataclass
class ResearchEvent:
    kind: str  # "decompose", "search", "analyze", "synthesize", "verify", "error", "done"
    data: Any = None


class ResearchOrchestrator:
    """Runs the YOYO 5-agent research pipeline."""

    def __init__(
        self,
        client: AiUtilsClient,
        search_fn: Any = None,
        model: str = "deepseek-chat",
        max_results_per_query: int = 5,
    ) -> None:
        logger.debug("Entered into ResearchOrchestrator.__init__")
        self._client = client
        self._search_fn = search_fn
        self._model = model
        self._max_results = max_results_per_query

    async def run(self, question: str) -> AsyncIterator[ResearchEvent]:
        logger.debug(f"Entered into ResearchOrchestrator.run: q={question[:80]!r}")
        start = time.monotonic()
        state = ResearchState(question=question)

        sub_questions = await self._decompose(question)
        state.sub_questions = sub_questions
        yield ResearchEvent(kind="decompose", data={
            "sub_questions": sub_questions,
            "count": len(sub_questions),
        })

        if self._search_fn:
            search_tasks = [self._search_fn(sq) for sq in sub_questions]
            all_results = await asyncio.gather(*search_tasks, return_exceptions=True)
            for i, result_set in enumerate(all_results):
                if isinstance(result_set, Exception):
                    logger.warning(f"Search failed for sub-question {i}: {result_set}")
                    continue
                if isinstance(result_set, list):
                    for r in result_set:
                        if isinstance(r, SearchResult):
                            state.search_results.append(r)
                        elif isinstance(r, dict):
                            state.search_results.append(SearchResult(
                                query=sub_questions[i],
                                title=r.get("title", ""),
                                url=r.get("url", ""),
                                snippet=r.get("snippet", ""),
                                source=r.get("source", ""),
                            ))
        else:
            for sq in sub_questions:
                state.search_results.append(SearchResult(
                    query=sq,
                    snippet=f"(search not available — sub-question logged: {sq})",
                ))

        yield ResearchEvent(kind="search", data={
            "total_results": len(state.search_results),
            "queries": len(sub_questions),
        })

        analysis = await self._analyze(question, state.search_results)
        state.analysis = analysis
        yield ResearchEvent(kind="analyze", data={
            "analysis_length": len(analysis),
        })

        report = await self._synthesize(question, analysis)
        state.report = report
        yield ResearchEvent(kind="synthesize", data={
            "report_length": len(report),
        })

        verification = await self._verify(report)
        state.verification = verification
        yield ResearchEvent(kind="verify", data=verification)

        if not verification.get("verified", True) and verification.get("suggestions"):
            enhanced_prompt = (
                f"Improve this report based on the following feedback:\n\n"
                f"Report:\n{report}\n\n"
                f"Issues: {json.dumps(verification.get('issues', []))}\n"
                f"Suggestions: {json.dumps(verification.get('suggestions', []))}\n\n"
                f"Return the improved report."
            )
            try:
                improved = await self._client.chat_completion(
                    messages=[ChatMessage(role="user", content=enhanced_prompt)],
                    model=self._model,
                    temperature=0.3,
                )
                state.report = improved.content
            except Exception as e:
                logger.warning(f"Report improvement failed: {e}")

        state.elapsed_ms = int((time.monotonic() - start) * 1000)
        state.sources = _extract_sources(state.search_results)

        yield ResearchEvent(kind="done", data={
            "report": state.report,
            "sources": state.sources,
            "verification": state.verification,
            "elapsed_ms": state.elapsed_ms,
            "sub_questions": len(state.sub_questions),
            "search_results": len(state.search_results),
        })

    async def _decompose(self, question: str) -> list[str]:
        logger.debug(f"Entered into _decompose: q={question[:80]!r}")
        prompt = DECOMPOSE_PROMPT.replace("{question}", question[:4000])
        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._model,
                temperature=0.2,
                max_tokens=500,
            )
            s = response.content.strip()
            a, b = s.find("["), s.rfind("]")
            if a != -1 and b != -1:
                parsed = json.loads(s[a : b + 1])
                if isinstance(parsed, list):
                    return [str(q) for q in parsed if q][:6]
        except Exception as e:
            logger.warning(f"Decomposition failed: {e}")
        return [question]

    async def _analyze(self, question: str, results: list[SearchResult]) -> str:
        logger.debug(f"Entered into _analyze: results={len(results)}")
        results_text = "\n\n".join(
            f"**{r.title or r.query}**\n"
            f"URL: {r.url}\n"
            f"{r.snippet}"
            for r in results
            if r.snippet
        )

        if not results_text:
            results_text = "(No search results available. Analyze based on general knowledge.)"

        prompt = ANALYZE_PROMPT.replace("{question}", question[:2000])
        prompt = prompt.replace("{results}", results_text[:8000])

        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._model,
                temperature=0.3,
                max_tokens=4000,
            )
            return response.content
        except Exception as e:
            logger.warning(f"Analysis failed: {e}")
            return f"Analysis could not be completed: {e}"

    async def _synthesize(self, question: str, analysis: str) -> str:
        logger.debug(f"Entered into _synthesize: analysis_len={len(analysis)}")
        prompt = SYNTHESIZE_PROMPT.replace("{question}", question[:2000])
        prompt = prompt.replace("{analyses}", analysis[:10000])

        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._model,
                temperature=0.3,
                max_tokens=6000,
            )
            return response.content
        except Exception as e:
            logger.warning(f"Synthesis failed: {e}")
            return f"Synthesis could not be completed: {e}"

    async def _verify(self, report: str) -> dict[str, Any]:
        logger.debug(f"Entered into _verify: report_len={len(report)}")
        prompt = VERIFY_PROMPT.replace("{report}", report[:8000])

        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._model,
                temperature=0.0,
                max_tokens=1000,
            )
            s = response.content.strip()
            a, b = s.find("{"), s.rfind("}")
            if a != -1 and b != -1:
                return json.loads(s[a : b + 1])
        except Exception as e:
            logger.warning(f"Verification failed: {e}")

        return {"verified": True, "issues": [], "confidence": 0.5, "suggestions": []}


def _extract_sources(results: list[SearchResult]) -> list[dict[str, str]]:
    logger.debug(f"Entered into _extract_sources: count={len(results)}")
    seen: set[str] = set()
    sources: list[dict[str, str]] = []
    for r in results:
        if r.url and r.url not in seen:
            seen.add(r.url)
            sources.append({
                "title": r.title or r.query,
                "url": r.url,
                "source": r.source,
            })
    return sources
