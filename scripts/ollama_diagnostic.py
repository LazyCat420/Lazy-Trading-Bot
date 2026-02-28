"""Ollama diagnostic -- tests the exact payload the bot sends.

Run with: venv\\Scripts\\python.exe scripts/ollama_diagnostic.py
"""

import asyncio
import sys
import time

import httpx

# Force UTF-8 on Windows
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


async def main() -> None:
    base_url = "http://10.0.0.30:11434"
    model = "granite-turbo:latest"

    print("=" * 60)
    print("OLLAMA DIAGNOSTIC")
    print("=" * 60)

    # Test 1: Can we reach Ollama at all?
    print("\n[Test 1] Reaching Ollama API...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            print(f"  OK - Ollama reachable. Models: {models}")
            if model not in models:
                print(f"  WARN - Model '{model}' NOT in list!")
                return
        except Exception as e:
            print(f"  FAIL - Cannot reach Ollama: {e}")
            return

    # Test 2: Simple chat with DEFAULT context (no num_ctx override)
    print("\n[Test 2] Chat with DEFAULT context size (no num_ctx)...")
    payload_default = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with one word: working"},
            {"role": "user", "content": "Test"},
        ],
        "stream": False,
        "options": {"temperature": 0.3},
    }
    await _test_chat(base_url, payload_default, label="default ctx")

    # Test 3: Chat with num_ctx=8192 (the code default)
    print("\n[Test 3] Chat with num_ctx=8192...")
    payload_8k = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with one word: working"},
            {"role": "user", "content": "Test"},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 8192},
    }
    await _test_chat(base_url, payload_8k, label="num_ctx=8192")

    # Test 4: Chat with num_ctx=100000 (what the user has configured!)
    print("\n[Test 4] Chat with num_ctx=100000 (CURRENT CONFIG)...")
    payload_100k = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with one word: working"},
            {"role": "user", "content": "Test"},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 100000},
    }
    await _test_chat(base_url, payload_100k, label="num_ctx=100000")

    print("\n" + "=" * 60)
    print("DIAGNOSIS COMPLETE")
    print("=" * 60)


async def _test_chat(base_url: str, payload: dict, label: str) -> None:
    """Send a chat request and report the result."""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        ) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            elapsed = time.perf_counter() - t0
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                tokens = data.get("eval_count", 0)
                print(
                    f"  OK - {label}: {elapsed:.1f}s, "
                    f"response='{content[:50]}', tokens={tokens}"
                )
            else:
                print(f"  FAIL - {label}: HTTP {resp.status_code} in {elapsed:.1f}s")
                print(f"     Body: {resp.text[:200]}")
    except httpx.ReadTimeout:
        elapsed = time.perf_counter() - t0
        print(f"  FAIL - {label}: TIMEOUT after {elapsed:.1f}s")
        print("     >>> This context size is TOO LARGE for this model/GPU!")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"  FAIL - {label}: ERROR after {elapsed:.1f}s -- {e}")


if __name__ == "__main__":
    asyncio.run(main())
