# RAG Part 5 — Wire RAG into Trading Pipeline

**Priority:** HIGH (this is the payoff — where RAG improves trading decisions)  
**Estimated effort:** Small  
**Dependencies:** Part 4 (RetrievalService)

## Goal

Plug the RAG retrieval into the trading decision prompt so the LLM receives relevant context from YouTube transcripts, news articles, and Reddit posts when making BUY/SELL/HOLD decisions. This is where all the preceding work comes together.

## Current Prompt Structure (in `TradingAgent._build_prompt()`)

```
TICKER: AAPL
PRICE: $185.50  |  TODAY: +1.2%
VOLUME: 52,000,000  |  AVG VOLUME: 48,000,000

TECHNICAL ANALYSIS:
RSI=62 | MACD=1.25 | SMA20=$182.00 | SMA50=$178.00

QUANT SIGNALS:
Conviction: 72% | Kelly: 8.5% | Sharpe: 1.45

NEWS DIGEST:                      ← Currently truncated to 300 chars
Apple beat earnings...

PORTFOLIO: Cash=$10,000  |  Total=$50,000  |  Max position=15%

QUANT VERDICT: BUY (conviction=72%)
RISK FLAGS: None
```

## After RAG Integration (new section added)

```
TICKER: AAPL
PRICE: $185.50  |  TODAY: +1.2%
...existing sections...

MARKET INTELLIGENCE (from collected data):          ← NEW
[YouTube: CNBC] Apple's services revenue grew 20% YoY, 
now representing 25% of total revenue. Analysts see this 
as a durable growth driver with 70%+ margins...

[News: Reuters] AAPL raised its dividend by 4% and 
announced a $90B buyback program, signaling management 
confidence in future cash flows...

[Reddit: r/stocks] Institutional positioning shows 
Berkshire added to AAPL in Q4. 13F filings confirm 
multiple hedge funds increasing positions...

PORTFOLIO: Cash=$10,000  |  Total=$50,000  |  Max position=15%
...rest of prompt...
```

## Files to Modify

### [MODIFY] `app/services/trading_pipeline_service.py`

In `_build_context()`, add RAG retrieval after existing context building:

```python
# After the existing dossier context block (~line 420):

# ── RAG context (retrieved from embedded data) ────────
from app.config import settings
if settings.RAG_ENABLED:
    try:
        from app.services.retrieval_service import RetrievalService
        rag_svc = RetrievalService()
        rag_text = await rag_svc.retrieve_for_trading(
            ticker,
            top_k=settings.RAG_TOP_K,
            max_chars=settings.RAG_MAX_CHARS,
        )
        if rag_text:
            context["rag_context"] = rag_text
            logger.info(
                "[TradingPipeline] RAG: retrieved %d chars for %s",
                len(rag_text), ticker,
            )
        else:
            context["rag_context"] = ""
    except Exception as exc:
        logger.warning(
            "[TradingPipeline] RAG retrieval failed for %s: %s",
            ticker, exc,
        )
        context["rag_context"] = ""
else:
    context["rag_context"] = ""
```

### [MODIFY] `app/services/trading_agent.py`

In `_build_prompt()`, add the RAG section between NEWS DIGEST and PORTFOLIO:

```python
# After the news section:
rag = ctx.get("rag_context", "")
if rag:
    parts.append(f"\nMARKET INTELLIGENCE (from collected data):\n{rag}")
```

### Context Budget Guard Update

The existing context budget guard in `TradingAgent.decide()` truncates `news_summary` when the prompt is too large. Update it to also truncate `rag_context`:

```python
if total_tokens > budget:
    # First: truncate rag_context (it's supplementary)
    rag = context.get("rag_context", "")
    if rag:
        overshoot = total_tokens - budget
        chars_to_cut = overshoot * 4
        if chars_to_cut >= len(rag):
            context["rag_context"] = ""  # Drop RAG entirely
        else:
            context["rag_context"] = rag[:len(rag) - chars_to_cut] + "\n[...truncated]"
        user_prompt = self._build_prompt(context)
        total_tokens = _llm.estimate_tokens(_SYSTEM_PROMPT + user_prompt)
    
    # Then: truncate news_summary if still over budget
    if total_tokens > budget:
        # ...existing news truncation logic...
```

**Key principle:** RAG context is truncated FIRST (before news_summary) because the core analysis data (technicals, quant signals, news digest) is more critical than supplementary intelligence.

### [MODIFY] `app/services/trading_agent.py` — System Prompt

Add a line to `_SYSTEM_PROMPT` to tell the LLM how to use the MARKET INTELLIGENCE section:

```python
# Add to _SYSTEM_PROMPT:
"If MARKET INTELLIGENCE is provided, use it to inform your analysis "
"but weigh quantitative signals (technicals, quant scorecard) more "
"heavily than qualitative intelligence."
```

## Feature Flag

RAG is behind `settings.RAG_ENABLED` (default: `True`). If false:

- No retrieval calls made
- `context["rag_context"]` = empty string
- No change to existing behavior

This allows easy rollback if RAG causes issues.

## Verification

### Tests

```python
@pytest.mark.asyncio
async def test_build_context_includes_rag(respx_mock, test_db, monkeypatch):
    """_build_context adds rag_context when RAG is enabled."""
    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    # Insert a test embedding
    ...
    pipeline = TradingPipelineService(...)
    ctx = await pipeline._build_context("AAPL", {})
    assert "rag_context" in ctx

@pytest.mark.asyncio
async def test_build_context_skips_rag_when_disabled(monkeypatch):
    """No RAG calls when RAG_ENABLED=False."""
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    pipeline = TradingPipelineService(...)
    ctx = await pipeline._build_context("AAPL", {})
    assert ctx["rag_context"] == ""

def test_build_prompt_includes_market_intelligence():
    """_build_prompt renders MARKET INTELLIGENCE section."""
    ctx = {
        "symbol": "AAPL", "last_price": 185,
        "rag_context": "[YouTube: CNBC] Apple earnings beat...",
        # ...other required fields...
    }
    prompt = TradingAgent._build_prompt(ctx)
    assert "MARKET INTELLIGENCE" in prompt
    assert "[YouTube: CNBC]" in prompt
```

### Run

```bash
pytest tests/test_trading_agent.py tests/test_trade_action.py -v
```

### Manual Verification

- Run a full trading loop with RAG_ENABLED=True
- Check artifact logs (cycle artifacts) for `rag_context` in the user prompt
- Verify the LLM's rationale references information from the RAG context
- Compare decision quality: run same tickers with RAG on vs off

## Done Criteria

- [ ] `rag_context` populated in `_build_context()`
- [ ] MARKET INTELLIGENCE section renders in LLM prompt
- [ ] Context budget guard truncates RAG first, then news
- [ ] Feature flag works (RAG_ENABLED=False skips everything)
- [ ] Existing tests still pass (no regression)
- [ ] ruff clean
