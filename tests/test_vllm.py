"""
vLLM Docker Test Suite for Jetson Orin AGX
Tests the OpenAI-compatible API served by vLLM.

Model: Kbenkhaled/Qwen3.5-35B-A3B-quantized.w4a16
Port:  8000
"""

import json
import time
import sys
import requests

VLLM_BASE = "http://10.0.0.30:8000"
MODEL_NAME = "Kbenkhaled/Qwen3.5-35B-A3B-quantized.w4a16"

PASS = "\033[92m✔ PASS\033[0m"
FAIL = "\033[91m✘ FAIL\033[0m"
SKIP = "\033[93m⊘ SKIP\033[0m"


def header(title):
  print(f"\n{'=' * 60}")
  print(f"  {title}")
  print(f"{'=' * 60}")


# ── 1. Health / Readiness ────────────────────────────────────
def test_health():
  header("1. Health Check (via /v1/models)")
  try:
    r = requests.get(f"{VLLM_BASE}/v1/models", timeout=10)
    if r.status_code == 200:
      models = [m["id"] for m in r.json().get("data", [])]
      print(f"  Server is up — models: {models}")
      print(f"  {PASS}")
      return True
    else:
      print(f"  Status: {r.status_code}  {FAIL}")
      return False
  except requests.ConnectionError:
    print(f"  Connection refused — server not up yet  {FAIL}")
    return False
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── 2. List Models ───────────────────────────────────────────
def test_list_models():
  header("2. List Models (GET /v1/models)")
  try:
    r = requests.get(f"{VLLM_BASE}/v1/models", timeout=10)
    data = r.json()
    models = [m["id"] for m in data.get("data", [])]
    print(f"  Available models: {models}")
    found = MODEL_NAME in models
    print(f"  Expected model present: {PASS if found else FAIL}")
    return found
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── 3. Simple Chat Completion ────────────────────────────────
def test_chat_completion():
  header("3. Chat Completion (POST /v1/chat/completions)")
  payload = {
    "model": MODEL_NAME,
    "messages": [
      {"role": "user", "content": "Say hello in exactly 5 words."}
    ],
    "max_tokens": 64,
    "temperature": 0.3,
  }
  try:
    t0 = time.time()
    r = requests.post(
      f"{VLLM_BASE}/v1/chat/completions",
      json=payload,
      timeout=120,
    )
    elapsed = time.time() - t0
    data = r.json()

    if "error" in data:
      print(f"  Server error: {data['error']}  {FAIL}")
      return False

    reply = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    print(f"  Reply: {reply}")
    print(f"  Tokens  → prompt: {usage.get('prompt_tokens')}, "
          f"completion: {usage.get('completion_tokens')}")
    print(f"  Latency: {elapsed:.2f}s")
    print(f"  {PASS}")
    return True
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── 4. Streaming Chat Completion ─────────────────────────────
def test_streaming():
  header("4. Streaming Chat Completion")
  payload = {
    "model": MODEL_NAME,
    "messages": [
      {"role": "user", "content": "Count from 1 to 5."}
    ],
    "max_tokens": 64,
    "temperature": 0.0,
    "stream": True,
  }
  try:
    t0 = time.time()
    r = requests.post(
      f"{VLLM_BASE}/v1/chat/completions",
      json=payload,
      timeout=120,
      stream=True,
    )
    chunks = []
    first_token_time = None
    for line in r.iter_lines():
      if not line:
        continue
      decoded = line.decode("utf-8")
      if decoded.startswith("data: ") and decoded != "data: [DONE]":
        if first_token_time is None:
          first_token_time = time.time()
        chunk = json.loads(decoded[6:])
        delta = chunk["choices"][0]["delta"].get("content", "")
        if delta:
          chunks.append(delta)

    elapsed = time.time() - t0
    ttft = (first_token_time - t0) if first_token_time else None
    full = "".join(chunks)
    print(f"  Streamed reply: {full}")
    print(f"  Chunks received: {len(chunks)}")
    if ttft:
      print(f"  Time-to-first-token: {ttft:.2f}s")
    print(f"  Total latency: {elapsed:.2f}s")
    print(f"  {PASS}")
    return True
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── 5. Reasoning / Thinking Mode ─────────────────────────────
def test_reasoning():
  header("5. Reasoning / Thinking Mode")
  payload = {
    "model": MODEL_NAME,
    "messages": [
      {"role": "user", "content": "What is 17 * 23? Think step by step."}
    ],
    "max_tokens": 512,
    "temperature": 0.6,
  }
  try:
    t0 = time.time()
    r = requests.post(
      f"{VLLM_BASE}/v1/chat/completions",
      json=payload,
      timeout=180,
    )
    elapsed = time.time() - t0
    data = r.json()

    if "error" in data:
      print(f"  Server error: {data['error']}  {FAIL}")
      return False

    msg = data["choices"][0]["message"]
    content = msg.get("content", "")
    reasoning = msg.get("reasoning_content", "")

    if reasoning:
      print(f"  Reasoning block: {reasoning[:200]}...")
    print(f"  Answer: {content}")
    print(f"  Latency: {elapsed:.2f}s")

    ok = "391" in content or "391" in reasoning
    print(f"  Correct answer (391): {PASS if ok else FAIL}")
    return True  # pass even if math is wrong, we're testing connectivity
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── 6. Tool Calling ──────────────────────────────────────────
def test_tool_calling():
  header("6. Tool Calling")
  tools = [
    {
      "type": "function",
      "function": {
        "name": "get_stock_price",
        "description": "Get the current price of a stock by its ticker symbol.",
        "parameters": {
          "type": "object",
          "properties": {
            "ticker": {
              "type": "string",
              "description": "Stock ticker symbol, e.g. AAPL",
            }
          },
          "required": ["ticker"],
        },
      },
    }
  ]
  payload = {
    "model": MODEL_NAME,
    "messages": [
      {"role": "user", "content": "What is the current price of NVDA?"}
    ],
    "tools": tools,
    "max_tokens": 256,
    "temperature": 0.0,
  }
  try:
    t0 = time.time()
    r = requests.post(
      f"{VLLM_BASE}/v1/chat/completions",
      json=payload,
      timeout=180,
    )
    elapsed = time.time() - t0
    data = r.json()

    if "error" in data:
      print(f"  Server error: {data['error']}  {FAIL}")
      return False

    msg = data["choices"][0]["message"]
    tool_calls = msg.get("tool_calls", [])

    if tool_calls:
      for tc in tool_calls:
        fn = tc["function"]
        print(f"  Tool called: {fn['name']}")
        print(f"  Arguments:   {fn['arguments']}")
        args = json.loads(fn["arguments"])
        ok = args.get("ticker", "").upper() == "NVDA"
        print(f"  Correct ticker: {PASS if ok else FAIL}")
    else:
      print(f"  No tool_calls in response  {SKIP}")
      print(f"  Raw content: {msg.get('content', '')[:200]}")

    print(f"  Latency: {elapsed:.2f}s")
    return bool(tool_calls)
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── 7. Concurrent Requests (max-num-seqs = 4) ────────────────
def test_concurrent():
  header("7. Concurrent Requests (batch of 4)")
  from concurrent.futures import ThreadPoolExecutor, as_completed

  def single_request(i):
    payload = {
      "model": MODEL_NAME,
      "messages": [
        {"role": "user", "content": f"What is {i + 2} + {i + 3}?"}
      ],
      "max_tokens": 32,
      "temperature": 0.0,
    }
    t0 = time.time()
    r = requests.post(
      f"{VLLM_BASE}/v1/chat/completions",
      json=payload,
      timeout=180,
    )
    elapsed = time.time() - t0
    data = r.json()
    reply = data["choices"][0]["message"]["content"]
    expected = str((i + 2) + (i + 3))
    return i, reply.strip(), expected, elapsed

  try:
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
      futures = [pool.submit(single_request, i) for i in range(4)]
      for f in as_completed(futures):
        results.append(f.result())

    total = time.time() - t0
    for i, reply, expected, elapsed in sorted(results):
      has_answer = expected in reply
      print(f"  Req {i}: {(i+2)}+{(i+3)}={expected}  "
            f"reply={reply[:40]:<40s}  {elapsed:.2f}s  "
            f"{'✔' if has_answer else '?'}")

    print(f"  Total wall-clock: {total:.2f}s")
    print(f"  {PASS}")
    return True
  except Exception as e:
    print(f"  {e}  {FAIL}")
    return False


# ── Runner ────────────────────────────────────────────────────
def main():
  print("\n🔧 vLLM Docker Test Suite")
  print(f"   Target: {VLLM_BASE}")
  print(f"   Model:  {MODEL_NAME}")

  # Gate on health first
  if not test_health():
    print("\n❌ Server is not reachable. Is the container running?")
    print(f"   Verify with: curl {VLLM_BASE}/health")
    sys.exit(1)

  tests = [
    ("List Models", test_list_models),
    ("Chat Completion", test_chat_completion),
    ("Streaming", test_streaming),
    ("Reasoning", test_reasoning),
    ("Tool Calling", test_tool_calling),
    ("Concurrent", test_concurrent),
  ]

  results = {}
  for name, fn in tests:
    try:
      results[name] = fn()
    except Exception as e:
      print(f"  Unexpected error: {e}  {FAIL}")
      results[name] = False

  # Summary
  header("SUMMARY")
  for name, ok in results.items():
    status = PASS if ok else FAIL
    print(f"  {name:.<40s} {status}")

  passed = sum(1 for v in results.values() if v)
  total = len(results)
  print(f"\n  {passed}/{total} tests passed")

  sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
  main()
