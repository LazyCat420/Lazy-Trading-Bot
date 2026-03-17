"""
WorkflowAssembler — Python port of Prism's WorkflowAssembler.js

Takes raw LLM audit-log entries from a single pipeline cycle and assembles
a visual node-graph with:
  • Input nodes  (system prompt + user context)
  • Model nodes  (the LLM call for each agent step)
  • Output nodes (the model's response)
  • Connections   (input→model→output, chaining between sequential steps)
  • nodeResults   (actual content for each node)

The assembled graph is persisted in the `pipeline_workflows` table so the
frontend can render a Prism-style interactive workflow viewer.
"""

from __future__ import annotations
import json, uuid, logging
from datetime import datetime

log = logging.getLogger("lazy_trader")

# ── Layout constants (px) ─────────────────────────────────────
STEP_WIDTH = 900
INPUT_X = 0
MODEL_X = 350
OUTPUT_X = 650

# Known pipeline steps in execution order
STEP_ORDER = [
    "discovery",
    "ticker_filter",
    "scoreboard",
    "analyst_technical",
    "analyst_fundamental",
    "analyst_sentiment",
    "analyst_risk",
    "analyst_smart_money",
    "contradiction_pass",
    "thesis_synthesis",
    "final_decision",
    "trade_execution",
]


def _step_sort_key(step_name: str) -> int:
    """Return a sort index for a step name so the graph is ordered logically."""
    name = step_name.lower().replace(" ", "_")
    for i, known in enumerate(STEP_ORDER):
        if known in name or name in known:
            return i
    return 999


def assemble_graph(audit_logs: list[dict]) -> dict:
    """
    Build a visual workflow graph from a list of LLM audit-log dicts.

    Each log must contain:
      - agent_step, model, system_prompt, user_context, raw_response,
        tokens_used, execution_time_ms, created_at

    Returns: { nodes, connections, node_results, node_statuses, meta }
    """
    if not audit_logs:
        return {
            "nodes": [],
            "connections": [],
            "node_results": {},
            "node_statuses": {},
            "meta": {},
        }

    # Sort by known pipeline order, then by created_at
    sorted_logs = sorted(
        audit_logs,
        key=lambda l: (
            _step_sort_key(l.get("agent_step", "")),
            l.get("created_at", ""),
        ),
    )

    nodes = []
    connections = []
    node_results = {}
    node_statuses = {}

    # Parallel analyst steps get laid out on the same Y band
    parallel_groups = {}  # step_sort_key -> list of logs
    for entry in sorted_logs:
        key = _step_sort_key(entry.get("agent_step", ""))
        parallel_groups.setdefault(key, []).append(entry)

    col_index = 0
    prev_model_ids = []

    for sort_key in sorted(parallel_groups.keys()):
        group = parallel_groups[sort_key]
        group_model_ids = []

        for lane, entry in enumerate(group):
            step = entry.get("agent_step", "unknown")
            prefix = f"s{col_index}_{lane}"
            base_x = 80 + col_index * STEP_WIDTH
            base_y = 80 + lane * 280
            sys_prompt = entry.get("system_prompt", "")
            user_ctx = entry.get("user_context", "")
            response = entry.get("raw_response", "")
            model_name = entry.get("model", "unknown")
            tokens = entry.get("tokens_used", 0)
            exec_ms = entry.get("execution_time_ms", 0)
            created = entry.get("created_at", "")

            # ── 1. Input: System Prompt ──
            sys_id = f"{prefix}_sys"
            if sys_prompt:
                nodes.append({
                    "id": sys_id,
                    "nodeType": "input",
                    "modality": "text",
                    "label": "System Prompt",
                    "content": sys_prompt[:500],
                    "fullContent": sys_prompt,
                    "position": {"x": base_x + INPUT_X, "y": base_y},
                })

            # ── 2. Input: User Context ──
            user_id = f"{prefix}_user"
            if user_ctx:
                nodes.append({
                    "id": user_id,
                    "nodeType": "input",
                    "modality": "text",
                    "label": "User Context",
                    "content": user_ctx[:500],
                    "fullContent": user_ctx,
                    "position": {"x": base_x + INPUT_X, "y": base_y + 140},
                })

            # ── 3. Model Node (the LLM call) ──
            model_id = f"{prefix}_model"
            nodes.append({
                "id": model_id,
                "nodeType": "model",
                "label": step,
                "modelName": model_name,
                "tokens": tokens,
                "durationMs": exec_ms,
                "createdAt": created,
                "position": {"x": base_x + MODEL_X, "y": base_y + 70},
            })
            group_model_ids.append(model_id)

            # Wire inputs → model
            if sys_prompt:
                connections.append({
                    "id": f"{prefix}_sys_to_model",
                    "source": sys_id,
                    "target": model_id,
                    "sourcePort": "text",
                    "targetPort": "system",
                })
            if user_ctx:
                connections.append({
                    "id": f"{prefix}_user_to_model",
                    "source": user_id,
                    "target": model_id,
                    "sourcePort": "text",
                    "targetPort": "user",
                })

            # ── 4. Output Viewer ──
            viewer_id = f"{prefix}_viewer"
            nodes.append({
                "id": viewer_id,
                "nodeType": "viewer",
                "label": "Output",
                "content": response[:500] if response else "",
                "fullContent": response,
                "position": {"x": base_x + OUTPUT_X, "y": base_y + 70},
            })
            connections.append({
                "id": f"{prefix}_model_to_viewer",
                "source": model_id,
                "target": viewer_id,
                "sourcePort": "text",
                "targetPort": "text",
            })

            # Results + statuses
            node_statuses[model_id] = "done"
            node_statuses[viewer_id] = "done"
            node_results[model_id] = {"text": response, "tokens": tokens, "duration_ms": exec_ms}
            node_results[viewer_id] = {"text": response}

            # ── 5. Chain from previous column's model(s) ──
            for prev_id in prev_model_ids:
                connections.append({
                    "id": f"chain_{prev_id}_to_{model_id}",
                    "source": prev_id,
                    "target": model_id,
                    "sourcePort": "text",
                    "targetPort": "chain",
                })

        prev_model_ids = group_model_ids
        col_index += 1

    # ── Meta ──
    tickers = list({e.get("ticker", "") for e in sorted_logs if e.get("ticker")})
    models = list({e.get("model", "") for e in sorted_logs if e.get("model")})
    total_tokens = sum(e.get("tokens_used", 0) for e in sorted_logs)
    total_ms = sum(e.get("execution_time_ms", 0) for e in sorted_logs)

    return {
        "nodes": nodes,
        "connections": connections,
        "node_results": node_results,
        "node_statuses": node_statuses,
        "meta": {
            "tickers": tickers,
            "models": models,
            "total_tokens": total_tokens,
            "total_duration_ms": total_ms,
            "step_count": len(sorted_logs),
        },
    }


def save_workflow(cycle_id: str, audit_logs: list[dict]) -> str | None:
    """Assemble and persist a workflow for the given cycle. Returns workflow ID."""
    from app.database import get_db

    try:
        graph = assemble_graph(audit_logs)
        if not graph["nodes"]:
            return None

        wf_id = str(uuid.uuid4())[:12]
        meta = graph["meta"]
        db = get_db()

        db.execute(
            """
            INSERT INTO pipeline_workflows
                (id, cycle_id, tickers, models, node_count, connection_count,
                 total_tokens, total_duration_ms, status, nodes, connections,
                 node_results, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
            """,
            [
                wf_id,
                cycle_id,
                ", ".join(meta.get("tickers", [])),
                ", ".join(meta.get("models", [])),
                len(graph["nodes"]),
                len(graph["connections"]),
                meta.get("total_tokens", 0),
                meta.get("total_duration_ms", 0),
                "completed",
                json.dumps(graph["nodes"]),
                json.dumps(graph["connections"]),
                json.dumps(graph["node_results"]),
            ],
        )
        log.info(f"[Workflow] Saved workflow {wf_id} for cycle {cycle_id} "
                 f"({len(graph['nodes'])} nodes, {meta.get('total_tokens', 0)} tokens)")
        return wf_id

    except Exception as e:
        log.error(f"[Workflow] Failed to save workflow for cycle {cycle_id}: {e}")
        return None
