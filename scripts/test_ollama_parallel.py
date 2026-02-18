"""Test Ollama parallelism — serial vs parallel LLM calls.

Reads connection settings from app/user_config/llm_config.json so it
uses the same Ollama instance as the trading bot.

Run from venv:
    python scripts/test_ollama_parallel.py
"""

import asyncio
import json
import time
from pathlib import Path

import aiohttp

# --- Load config from the actual bot config ---
CONFIG_PATH = Path(__file__).resolve().parent.parent / "app" / "user_config" / "llm_config.json"

if CONFIG_PATH.exists():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    OLLAMA_URL = cfg.get("ollama_url", "http://localhost:11434").rstrip("/")
    MODEL = cfg.get("model", "granite-turbo:latest")
else:
    OLLAMA_URL = "http://localhost:11434"
    MODEL = "granite-turbo:latest"

API_URL = f"{OLLAMA_URL}/api/chat"

# 7 completely DIFFERENT prompts — mirrors Phase 2 pipeline
# (1 question generator + 5 RAG answers + 1 dossier synthesis)
PROMPTS = [
    "In one sentence, what is a Z-score in finance?",
    "In one sentence, what is the Sortino ratio?",
    "In one sentence, what is the Calmar ratio?",
    "In one sentence, what is the Bollinger Band %B?",
    "In one sentence, what is the Kelly Criterion?",
    "In one sentence, what is Value at Risk (VaR)?",
    "In one sentence, what is the Omega ratio?",
]


async def call_ollama(
    session: aiohttp.ClientSession,
    prompt: str,
    label: str,
) -> tuple[str, float]:
    """Make one LLM call and return (answer, elapsed_seconds)."""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_ctx": 2048, "temperature": 0.1},
    }
    t0 = time.perf_counter()
    async with session.post(API_URL, json=payload) as resp:
        data = await resp.json()
    elapsed = time.perf_counter() - t0
    answer = data.get("message", {}).get("content", "ERROR")[:80]
    print(f"  [{label:>8}] {elapsed:5.2f}s — {answer}")
    return answer, elapsed


async def run_serial() -> float:
    """Run all prompts one at a time."""
    print("\n" + "=" * 60)
    print("TEST 1: SERIAL (one at a time)")
    print("=" * 60)
    t0 = time.perf_counter()
    times = []
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=300),
    ) as session:
        for i, prompt in enumerate(PROMPTS):
            _, elapsed = await call_ollama(session, prompt, f"Call {i + 1}")
            times.append(elapsed)
    wall = time.perf_counter() - t0
    avg = sum(times) / len(times)
    print(f"\n  ⏱️  Serial wall time: {wall:.2f}s  (avg per call: {avg:.2f}s)")
    return wall


async def run_parallel() -> float:
    """Run all prompts simultaneously."""
    print("\n" + "=" * 60)
    print("TEST 2: PARALLEL (all at once)")
    print("=" * 60)
    t0 = time.perf_counter()
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=300),
    ) as session:
        tasks = [
            call_ollama(session, prompt, f"Call {i + 1}")
            for i, prompt in enumerate(PROMPTS)
        ]
        results = await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0
    times = [r[1] for r in results]
    avg = sum(times) / len(times)
    print(f"\n  ⏱️  Parallel wall time: {wall:.2f}s  (avg per call: {avg:.2f}s)")
    return wall


async def main() -> None:
    print("🔧 Testing Ollama parallelism")
    print(f"   Model:   {MODEL}")
    print(f"   URL:     {API_URL}")
    print(f"   Prompts: {len(PROMPTS)} different questions")

    serial_time = await run_serial()

    # Small pause between tests
    print("\n   ⏳ 3s cooldown between tests...")
    await asyncio.sleep(3)

    parallel_time = await run_parallel()

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  Serial:   {serial_time:.2f}s")
    print(f"  Parallel: {parallel_time:.2f}s")
    speedup = serial_time / parallel_time if parallel_time > 0 else 0
    print(f"  Speedup:  {speedup:.1f}x faster")
    print(f"  Saved:    {serial_time - parallel_time:.1f}s")

    if speedup > 2.0:
        print("\n  ✅ EXCELLENT! Parallelism is working great!")
        print(f"     {len(PROMPTS)} parallel slots confirmed active.")
    elif speedup > 1.5:
        print("\n  ✅ GOOD! Parallelism is working.")
        print("     Some slots may be queuing — try increasing OLLAMA_NUM_PARALLEL.")
    elif speedup > 1.1:
        print("\n  🟡 PARTIAL. Only slight speedup detected.")
        print("     Check: OLLAMA_NUM_PARALLEL may be set too low.")
    else:
        print("\n  ❌ NO speedup. Requests are running serially.")
        print("     Fix: Set OLLAMA_NUM_PARALLEL=10 and restart ollama.")
        print("     On Jetson: sudo systemctl edit ollama")
        print("       [Service]")
        print('       Environment="OLLAMA_NUM_PARALLEL=10"')
        print("     Then: sudo systemctl daemon-reload && sudo systemctl restart ollama")


if __name__ == "__main__":
    asyncio.run(main())
