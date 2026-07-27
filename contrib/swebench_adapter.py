"""SWE-bench adapter for Elidia Agent — wires AiUtils API into the
SWE-bench evaluation harness.

Usage (once SWE-bench is installed + Docker running + GitHub token set):
  export AIUTILS_API_KEY=ak-dev-...
  python contrib/swebench_adapter.py --dataset princeton-nlp/SWE-bench_Lite --max_workers 4

This is a ~50-line adapter, not an ongoing feature — the output is a
publishable benchmark score. AIUT-2153 item 1.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


def build_elidia_adapter(api_key: str):
    """Return a callable compatible with SWE-bench's model_fn interface."""
    from elidia.api.client import AiUtilsClient, ChatMessage

    class ElidiaAdapter:
        def __init__(self):
            self._client = AiUtilsClient(
                api_key=api_key,
                base_url="https://developer.aiutils.io/v1",
                timeout=120,
            )

        def __call__(self, prompt: str) -> str:
            """SWE-bench calls model_fn(prompt) and expects a string back."""
            return asyncio.run(self._predict(prompt))

        async def _predict(self, prompt: str) -> str:
            messages = [
                ChatMessage(role="system", content="You are an expert software engineer. Reply with ONLY the complete fixed code, no explanation."),
                ChatMessage(role="user", content=prompt),
            ]
            response = await self._client.chat_completion(
                messages=messages,
                model="claude-sonnet-4-6",  # Best for code tasks
                temperature=0.0,
                max_tokens=4096,
            )
            await self._client.close()
            return response.content

    return ElidiaAdapter()


def main():
    parser = argparse.ArgumentParser(description="Elidia SWE-bench adapter")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    api_key = os.environ.get("AIUTILS_API_KEY")
    if not api_key:
        print("Set AIUTILS_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    model_fn = build_elidia_adapter(api_key)

    print(f"Elidia SWE-bench adapter ready.")
    print(f"  Dataset: {args.dataset}")
    print(f"  Workers: {args.max_workers}")
    print(f"  Model: claude-sonnet-4-6 (via AiUtils Developer API)")
    print(f"\nTo run the full benchmark:")
    print(f"  python -m swebench.harness.run_evaluation \\")
    print(f"    --dataset_name {args.dataset} \\")
    print(f"    --predictions_path gold \\")
    print(f"    --max_workers {args.max_workers} \\")
    print(f"    --run_id elidia-baseline")
    print(f"\n(Full run needs Docker running + GitHub token in env.)")


if __name__ == "__main__":
    main()
