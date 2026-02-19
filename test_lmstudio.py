"""Diagnostic: writes results to JSON for reliable reading."""
import asyncio
import json
import httpx

LM_URL = "http://100.98.210.120:1234"
MODEL = "ibm/granite-3.2-8b"

async def main():
    results = {}
    
    # Test 1: List models
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{LM_URL}/v1/models")
            models = [m.get("id", "") for m in r.json().get("data", [])] if r.status_code < 400 else []
            results["test1_models"] = {"status": r.status_code, "models": models, "match": MODEL in models}
    except Exception as e:
        results["test1_models"] = {"error": str(e)}

    # Test 2: Minimal call (no response_format)
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{LM_URL}/v1/chat/completions", json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "Say hello in 5 words"}],
                "temperature": 0.1,
            })
            if r.status_code >= 400:
                results["test2_minimal"] = {"status": r.status_code, "error": r.text[:300]}
            else:
                results["test2_minimal"] = {"status": r.status_code, "content": r.json()["choices"][0]["message"]["content"][:100]}
    except Exception as e:
        results["test2_minimal"] = {"error": str(e)}

    # Test 3: With response_format (expect 400)
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{LM_URL}/v1/chat/completions", json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "Say hello"}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            })
            if r.status_code >= 400:
                results["test3_response_format"] = {"status": r.status_code, "error": r.text[:300]}
            else:
                results["test3_response_format"] = {"status": r.status_code, "content": "UNEXPECTED OK"}
    except Exception as e:
        results["test3_response_format"] = {"error": str(e)}

    # Test 4: With max_tokens
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{LM_URL}/v1/chat/completions", json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "Say hello"}],
                "temperature": 0.1,
                "max_tokens": 100,
            })
            if r.status_code >= 400:
                results["test4_max_tokens"] = {"status": r.status_code, "error": r.text[:300]}
            else:
                results["test4_max_tokens"] = {"status": r.status_code, "content": r.json()["choices"][0]["message"]["content"][:100]}
    except Exception as e:
        results["test4_max_tokens"] = {"error": str(e)}

    # Write results as JSON
    with open("diag_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=True)

asyncio.run(main())
