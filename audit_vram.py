#!/usr/bin/env python3
"""Phase 3: Standalone VRAM Boundary Test

Bypasses FastAPI, React, and all application logic.
Talks directly to Ollama to find the exact context ceiling.

Usage (run ON THE JETSON):
    python3 audit_vram.py
    python3 audit_vram.py --model gemma3:27b
    python3 audit_vram.py --model qwen3-vl:32b --url http://10.0.0.30:11434

Results are printed to stdout AND saved to audit_results.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# ── Phase 2 (inline): OS memory audit ────────────────────────
def audit_os_memory() -> dict:
    """Read /proc/meminfo and ulimit to check OS-level limits."""
    info: dict = {}

    # /proc/meminfo
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    key = line.split(":")[0].strip()
                    if key in (
                        "MemTotal",
                        "MemFree",
                        "MemAvailable",
                        "SwapTotal",
                        "SwapFree",
                        "Mlocked",
                    ):
                        parts = line.split()
                        kb = int(parts[1])
                        info[key] = f"{kb / (1024**2):.2f} GB ({kb} kB)"
        except Exception as e:
            info["meminfo_error"] = str(e)
    else:
        info["meminfo"] = "NOT AVAILABLE (not Linux)"

    # ulimit -l (max locked memory)
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        if soft == resource.RLIM_INFINITY:
            info["ulimit_memlock_soft"] = "unlimited"
        else:
            info["ulimit_memlock_soft"] = f"{soft / (1024**2):.2f} GB"
        if hard == resource.RLIM_INFINITY:
            info["ulimit_memlock_hard"] = "unlimited"
        else:
            info["ulimit_memlock_hard"] = f"{hard / (1024**2):.2f} GB"
    except Exception:
        info["ulimit_memlock"] = "UNKNOWN (resource module unavailable)"

    return info


# ── Phase 3: Boundary test ───────────────────────────────────
def test_context(url: str, model: str, ctx: int, timeout: float = 180.0) -> dict:
    """Load model at a specific context size and measure VRAM."""
    result: dict = {"ctx": ctx, "status": "unknown"}

    # 1. Unload completely
    try:
        httpx.post(
            f"{url}/api/generate",
            json={
                "model": model,
                "prompt": "",
                "keep_alive": "0",
                "stream": False,
            },
            timeout=30.0,
        )
        time.sleep(3)  # Let Ollama fully release
    except Exception:
        pass

    # 2. Attempt load
    start = time.time()
    try:
        resp = httpx.post(
            f"{url}/api/generate",
            json={
                "model": model,
                "prompt": "Say hi",
                "stream": False,
                "keep_alive": "10m",
                "options": {
                    "num_ctx": ctx,
                    "num_gpu": 999,
                },
            },
            timeout=timeout,
        )
        elapsed = time.time() - start
        resp.raise_for_status()

        # 3. Check /api/ps for VRAM usage
        ps = httpx.get(f"{url}/api/ps", timeout=10.0).json()
        for m in ps.get("models", []):
            if model in m.get("name", ""):
                vram = m.get("size_vram", 0)
                vram_gb = vram / (1024**3)
                result.update(
                    {
                        "status": "success",
                        "elapsed_s": round(elapsed, 1),
                        "size_vram": vram,
                        "vram_gb": round(vram_gb, 2),
                    }
                )
                print(
                    f"  ✅ ctx={ctx:>6,}  |  "
                    f"VRAM={vram_gb:>6.2f} GB  |  "
                    f"Time={elapsed:.1f}s"
                )
                return result

        # Model loaded but not in /api/ps (unusual)
        result.update(
            {
                "status": "success_no_ps",
                "elapsed_s": round(elapsed, 1),
            }
        )
        print(
            f"  ⚠️  ctx={ctx:>6,}  |  Loaded in {elapsed:.1f}s but /api/ps has no data"
        )

    except httpx.TimeoutException:
        elapsed = time.time() - start
        result.update(
            {
                "status": "timeout",
                "elapsed_s": round(elapsed, 1),
            }
        )
        print(
            f"  ❌ ctx={ctx:>6,}  |  "
            f"TIMEOUT after {elapsed:.0f}s "
            f"(Ollama likely stuck in internal retry loop)"
        )

    except httpx.HTTPStatusError as e:
        elapsed = time.time() - start
        result.update(
            {
                "status": "http_error",
                "http_code": e.response.status_code,
                "elapsed_s": round(elapsed, 1),
            }
        )
        print(f"  ❌ ctx={ctx:>6,}  |  HTTP {e.response.status_code} in {elapsed:.1f}s")

    except Exception as e:
        elapsed = time.time() - start
        result.update(
            {
                "status": "error",
                "error": str(e)[:200],
                "elapsed_s": round(elapsed, 1),
            }
        )
        print(f"  ❌ ctx={ctx:>6,}  |  Error: {str(e)[:100]}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="VRAM Boundary Audit — find exact context ceiling"
    )
    parser.add_argument(
        "--model",
        default="gemma3:27b",
        help="Model name (default: gemma3:27b)",
    )
    parser.add_argument(
        "--url",
        default="http://10.0.0.30:11434",
        help="Ollama base URL",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Timeout per load attempt in seconds (default: 180)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  VRAM BOUNDARY AUDIT")
    print(f"  Model:   {args.model}")
    print(f"  Ollama:  {args.url}")
    print(f"  Timeout: {args.timeout}s per attempt")
    print("=" * 60)

    # ── Phase 2: OS audit ──
    print("\n📋 PHASE 2: OS Memory Audit")
    print("-" * 40)
    os_info = audit_os_memory()
    for k, v in os_info.items():
        print(f"  {k}: {v}")

    # ── Check Ollama is reachable ──
    try:
        tags = httpx.get(f"{args.url}/api/tags", timeout=5.0).json()
        models = [m["name"] for m in tags.get("models", [])]
        print(f"\n  Ollama models: {models}")
        if args.model not in models:
            # Try partial match
            matches = [m for m in models if args.model.split(":")[0] in m]
            if matches:
                print(f"  ⚠️  Exact match not found. Using: {matches[0]}")
                args.model = matches[0]
            else:
                print(f"  ❌ Model '{args.model}' not found!")
                sys.exit(1)
    except Exception as e:
        print(f"\n  ❌ Cannot reach Ollama at {args.url}: {e}")
        sys.exit(1)

    # ── Phase 3: Boundary test ──
    contexts = [2048, 4096, 8192, 16384, 24576, 32768, 49152, 65536, 98304, 131072]

    print("\n📊 PHASE 3: Context Boundary Test")
    print("-" * 60)
    print(f"  {'CTX':>8}  |  {'VRAM':>10}  |  {'Time':>8}  |  Status")
    print("-" * 60)

    results = []
    last_success_ctx = 0

    for ctx in contexts:
        result = test_context(args.url, args.model, ctx, args.timeout)
        results.append(result)

        if result["status"] == "success":
            last_success_ctx = ctx
        else:
            print(
                f"\n  🛑 HARD LIMIT: Model fails between "
                f"{last_success_ctx:,} and {ctx:,} context tokens."
            )
            break

    # ── Summary ──
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Last successful ctx: {last_success_ctx:,}")
    if last_success_ctx > 0:
        success_results = [r for r in results if r["status"] == "success"]
        if success_results:
            max_vram = max(r.get("vram_gb", 0) for r in success_results)
            print(f"  Peak VRAM used:      {max_vram:.2f} GB")
    print(f"  Total tests run:     {len(results)}")

    # ── Save results ──
    output = {
        "model": args.model,
        "url": args.url,
        "os_info": os_info,
        "results": results,
        "last_success_ctx": last_success_ctx,
    }
    out_path = "audit_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
