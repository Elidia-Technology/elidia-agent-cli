"""Consensus mode — runs 2-3 models in parallel, compares, and synthesizes.

Produces a higher-quality answer by getting independent opinions from
different model vendors, then using an LLM to evaluate agreement and
produce a final synthesis.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)

DEFAULT_CONSENSUS_MODELS = [
    "claude-sonnet-5",
    "deepseek-chat",
    "gpt-5",
]

SYNTHESIS_PROMPT = (
    "You are comparing responses from {count} different AI models to the same question.\n\n"
    "User's question: {question}\n\n"
    "{responses}\n\n"
    "Analyze the responses:\n"
    "1. Where do they AGREE? What facts or recommendations are shared?\n"
    "2. Where do they DISAGREE? What are the different perspectives?\n"
    "3. Which response has the strongest reasoning or evidence?\n\n"
    "Produce a FINAL SYNTHESIZED ANSWER that:\n"
    "- Takes the best points from each response\n"
    "- Notes any significant disagreements the user should know about\n"
    "- Is more complete and accurate than any single response\n\n"
    "Do not mention model names or say 'Model 1 said...'. Just give the best answer."
)

SYNTHESIZER_MODEL = "deepseek-chat"


@dataclass
class ModelResponse:
    model: str
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    error: str = ""


@dataclass
class ConsensusResult:
    synthesis: str
    responses: list[ModelResponse]
    agreement: str  # "high", "partial", "low"
    confidence: float  # 0.0-1.0
    model_count: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    elapsed_ms: int = 0


async def run_consensus(
    client: AiUtilsClient,
    messages: list[ChatMessage],
    models: list[str] | None = None,
    synthesizer_model: str = SYNTHESIZER_MODEL,
    max_tokens: int | None = None,
) -> ConsensusResult:
    logger.debug(f"Entered into run_consensus: models={models}")
    start = time.monotonic()

    model_list = models or DEFAULT_CONSENSUS_MODELS[:2]
    model_list = model_list[:3]

    tasks = [
        _query_model(client, messages, model, max_tokens)
        for model in model_list
    ]
    responses: list[ModelResponse] = await asyncio.gather(*tasks)

    successful = [r for r in responses if not r.error]
    if not successful:
        return ConsensusResult(
            synthesis="All models failed to respond. Please try again.",
            responses=responses,
            agreement="none",
            confidence=0.0,
            model_count=0,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    if len(successful) == 1:
        return ConsensusResult(
            synthesis=successful[0].content,
            responses=responses,
            agreement="single",
            confidence=0.5,
            model_count=1,
            total_tokens_in=successful[0].tokens_in,
            total_tokens_out=successful[0].tokens_out,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    user_question = messages[-1].content if messages else ""

    response_text = "\n\n".join(
        f"--- Response {i + 1} ---\n{r.content}"
        for i, r in enumerate(successful)
    )

    synth_prompt = SYNTHESIS_PROMPT.replace("{count}", str(len(successful)))
    synth_prompt = synth_prompt.replace("{question}", user_question[:2000])
    synth_prompt = synth_prompt.replace("{responses}", response_text[:12000])

    try:
        synth_response = await client.chat_completion(
            messages=[ChatMessage(role="user", content=synth_prompt)],
            model=synthesizer_model,
            temperature=0.3,
            max_tokens=max_tokens,
        )
        synthesis = synth_response.content
    except Exception as e:
        logger.warning(f"Synthesis LLM failed: {e}")
        synthesis = successful[0].content

    agreement = _assess_agreement(successful)

    total_in = sum(r.tokens_in for r in responses) + (synth_response.tokens_in if synthesis != successful[0].content else 0)
    total_out = sum(r.tokens_out for r in responses) + (synth_response.tokens_out if synthesis != successful[0].content else 0)

    return ConsensusResult(
        synthesis=synthesis,
        responses=responses,
        agreement=agreement,
        confidence=_agreement_to_confidence(agreement),
        model_count=len(successful),
        total_tokens_in=total_in,
        total_tokens_out=total_out,
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )


async def _query_model(
    client: AiUtilsClient,
    messages: list[ChatMessage],
    model: str,
    max_tokens: int | None = None,
) -> ModelResponse:
    logger.debug(f"Entered into _query_model: model={model}")
    start = time.monotonic()
    try:
        response = await client.chat_completion(
            messages=messages,
            model=model,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        return ModelResponse(
            model=model,
            content=response.content,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as e:
        logger.warning(f"Model {model} failed: {e}")
        return ModelResponse(
            model=model,
            content="",
            error=str(e),
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )


def _assess_agreement(responses: list[ModelResponse]) -> str:
    logger.debug(f"Entered into _assess_agreement: count={len(responses)}")
    if len(responses) < 2:
        return "single"

    contents = [r.content.lower() for r in responses]

    word_sets = [set(c.split()) for c in contents]

    if len(word_sets) >= 2:
        pairs_overlap: list[float] = []
        for i in range(len(word_sets)):
            for j in range(i + 1, len(word_sets)):
                union = word_sets[i] | word_sets[j]
                intersection = word_sets[i] & word_sets[j]
                if union:
                    pairs_overlap.append(len(intersection) / len(union))

        avg_overlap = sum(pairs_overlap) / len(pairs_overlap) if pairs_overlap else 0

        if avg_overlap > 0.4:
            return "high"
        elif avg_overlap > 0.2:
            return "partial"
        else:
            return "low"

    return "partial"


def _agreement_to_confidence(agreement: str) -> float:
    logger.debug(f"Entered into _agreement_to_confidence: agreement={agreement}")
    return {"high": 0.9, "partial": 0.7, "low": 0.5, "single": 0.5, "none": 0.0}.get(agreement, 0.5)
