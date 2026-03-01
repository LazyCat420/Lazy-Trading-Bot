"""VRAM Estimation Diagnostic — gathers all data needed to predict OOM.

Run: venv\\Scripts\\python.exe scripts\\vram_diagnostic.py

This script does NOT load any models into VRAM. It only queries
Ollama's metadata APIs and nvidia-smi to gather the numbers we need
to build a pure-math VRAM estimator.
"""

import asyncio
import json
import subprocess
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

BASE_URL = "http://10.0.0.30:11434"


async def main() -> None:
    print("=" * 60)
    print("VRAM ESTIMATION DIAGNOSTIC")
    print("=" * 60)

    # ── 1. GPU info from nvidia-smi ──────────────────────────────
    print("\n[1] GPU VRAM from nvidia-smi")
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    name, total, used, free = parts[:4]
                    print(f"  GPU: {name}")
                    print(f"  Total: {total} MiB ({int(total)/1024:.1f} GiB)")
                    print(f"  Used:  {used} MiB ({int(used)/1024:.1f} GiB)")
                    print(f"  Free:  {free} MiB ({int(free)/1024:.1f} GiB)")
        else:
            print(f"  nvidia-smi failed: {result.stderr.strip()}")
    except FileNotFoundError:
        print("  nvidia-smi not found — trying jtop/tegrastats...")
        # Jetson fallback: read from /sys
        try:
            with open("/sys/kernel/debug/nvmap/iovmm/maps", "r") as f:
                print(f"  Jetson NVMAP: {f.read()[:200]}")
        except Exception:
            print("  Could not read GPU memory info")
    except Exception as e:
        print(f"  Error: {e}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # ── 2. Available models + file sizes ─────────────────────
        print("\n[2] Ollama models (/api/tags)")
        try:
            resp = await client.get(f"{BASE_URL}/api/tags")
            resp.raise_for_status()
            models_data = resp.json().get("models", [])
            for m in models_data:
                name = m.get("name", "?")
                size_bytes = m.get("size", 0)
                size_gb = size_bytes / (1024 ** 3)
                print(f"  {name}: {size_gb:.2f} GiB on disk ({size_bytes:,} bytes)")
        except Exception as e:
            print(f"  Error: {e}")
            return

        # ── 3. Currently loaded models + VRAM ────────────────────
        print("\n[3] Currently loaded models (/api/ps)")
        try:
            resp = await client.get(f"{BASE_URL}/api/ps")
            resp.raise_for_status()
            ps_models = resp.json().get("models", [])
            if ps_models:
                for m in ps_models:
                    name = m.get("name", "?")
                    size_vram = m.get("size_vram", 0)
                    size = m.get("size", 0)
                    vram_gb = size_vram / (1024 ** 3)
                    print(f"  {name}: VRAM={vram_gb:.2f} GiB ({size_vram:,} bytes)")
                    print(f"    Total size: {size / (1024 ** 3):.2f} GiB")
            else:
                print("  No models currently loaded")
        except Exception as e:
            print(f"  Error: {e}")

        # ── 4. Model architecture details ────────────────────────
        print("\n[4] Model architecture details (/api/show)")
        model_to_inspect = input("  Which model to inspect? [default: first in list]: ").strip()
        if not model_to_inspect and models_data:
            model_to_inspect = models_data[0]["name"]

        if model_to_inspect:
            try:
                resp = await client.post(
                    f"{BASE_URL}/api/show",
                    json={"name": model_to_inspect},
                )
                resp.raise_for_status()
                show_data = resp.json()
                model_info = show_data.get("model_info", {})

                # Find architecture prefix
                arch = model_info.get("general.architecture", "unknown")
                print(f"\n  Architecture: {arch}")

                # Extract the KV-cache-relevant fields
                kv_fields = {}
                for key, val in sorted(model_info.items()):
                    if any(x in key for x in [
                        "block_count", "head_count", "embedding_length",
                        "context_length", "key_length", "value_length",
                        "head_count_kv", "parameter_count",
                    ]):
                        kv_fields[key] = val
                        print(f"  {key}: {val}")

                # ── Calculate KV cache per token ──
                block_count = 0
                head_count_kv = 0
                head_dim = 0
                context_length = 0

                for key, val in model_info.items():
                    if "block_count" in key and isinstance(val, int):
                        block_count = val
                    if "head_count_kv" in key and isinstance(val, int):
                        head_count_kv = val
                    if "key_length" in key and isinstance(val, int):
                        head_dim = val
                    if "context_length" in key and isinstance(val, int):
                        context_length = val

                # If no explicit key_length, derive from embedding_length/head_count
                if head_dim == 0:
                    embed_len = 0
                    head_count = 0
                    for key, val in model_info.items():
                        if "embedding_length" in key and isinstance(val, int):
                            embed_len = val
                        if key.endswith("attention.head_count") and isinstance(val, int):
                            head_count = val
                    if embed_len and head_count:
                        head_dim = embed_len // head_count

                print(f"\n  ── KV Cache Estimation ──")
                print(f"  Layers (block_count):    {block_count}")
                print(f"  KV Heads (head_count_kv): {head_count_kv}")
                print(f"  Head Dim (key_length):    {head_dim}")
                print(f"  Max Context:              {context_length}")

                if block_count and head_count_kv and head_dim:
                    # KV bytes per token = 2 (K+V) * layers * kv_heads * head_dim * 2 (FP16)
                    kv_bytes_per_token = 2 * block_count * head_count_kv * head_dim * 2
                    print(f"\n  KV bytes/token: {kv_bytes_per_token:,} bytes")

                    # Get model weight size from /api/tags
                    model_weight_bytes = 0
                    for m in models_data:
                        if m["name"] == model_to_inspect or m["name"].split(":")[0] == model_to_inspect.split(":")[0]:
                            model_weight_bytes = m.get("size", 0)
                            break

                    model_weight_gb = model_weight_bytes / (1024 ** 3)
                    print(f"  Model weights:  {model_weight_gb:.2f} GiB")

                    print(f"\n  ── VRAM Predictions ──")
                    for ctx in [2048, 4096, 8192, 16384, 32768, 60000, 65536, 80000, 100000, 128000]:
                        if ctx > context_length and context_length > 0:
                            break
                        kv_gb = (kv_bytes_per_token * ctx) / (1024 ** 3)
                        total_gb = model_weight_gb + kv_gb
                        marker = " ← max ctx" if ctx == context_length else ""
                        print(f"    ctx={ctx:>7,}: KV={kv_gb:.2f} GiB, Total={total_gb:.2f} GiB{marker}")
                else:
                    print("  ⚠ Could not find all architecture fields for KV calculation")
                    print(f"  Full model_info keys: {list(model_info.keys())}")

            except Exception as e:
                print(f"  Error: {e}")
                import traceback
                traceback.print_exc()

    print("\n" + "=" * 60)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
