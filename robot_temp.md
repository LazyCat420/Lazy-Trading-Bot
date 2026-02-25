# LLM Temperature Research for Trading Bot

## Your Current Setup

- **Current temp:** `0.3` (hardcoded default in `bot_registry.py` and `database.py`)
- **Model:** granite-turbo (via Ollama)  
- **Output format:** JSON (structured decisions like BUY/SELL/HOLD)

## TL;DR Conclusion

> **Don't raise the temperature to make the bot trade more.** The reason the bot isn't trading enough is almost certainly **prompt engineering or conviction thresholds** — NOT temperature. Raising temp will just make the JSON output break more often, which will cause *fewer* trades (crashed pipeline = no trade at all).

---

## What Temperature Actually Does

Temperature scales the probability distribution of the next token:

- **Temp 0.0** → Always picks the highest-probability token (greedy decoding). Deterministic, repetitive.
- **Temp 0.3** → Slight variation, still heavily favoring most-probable tokens. ← **You are here**
- **Temp 0.7** → Balanced — coherent but more diverse. "Standard creative" range.
- **Temp 1.0** → Model's raw probability distribution, no scaling. Noticeably varied outputs.
- **Temp 1.5+** → Increasingly chaotic. High hallucination rate. JSON output frequently malformed.

---

## Research Findings

### 1. Hallucination Rate vs Temperature

| Temperature | Hallucination Risk | JSON Reliability | Notes |
|---|---|---|---|
| 0.0–0.3 | **Low** | **High** | Most deterministic. Occasionally gets "stuck" on complex JSON structures. |
| 0.4–0.7 | **Low–Medium** | **High** | Sweet spot for natural language. JSON still mostly reliable. |
| 0.8–1.0 | **Medium** | **Moderate** | Starts introducing creative token choices. JSON may have field drift. |
| 1.0–1.5 | **High** | **Low** | Rapid deterioration. Hallucinated fields, broken JSON, incoherent reasoning. |
| 1.5+ | **Very High** | **Unusable** | Essentially random output in most models. |

**Key finding (arXiv 2025):** Performance is relatively stable from 0.0 to ~1.0, then **rapidly collapses** after 1.0–1.5. The "mutation temperature" (where things break) is higher for larger models — smaller models like granite-turbo are more sensitive.

**IBM's own recommendation for Granite 4 models:** *"Generally perform best with temperature set to 0."*

### 2. Temperature and Tool/Function Calling

A 2025 benchmark found:

- **At temp 0.0**, one model actually **failed to make tool calls** at all — it was too deterministic and wouldn't deviate from its "safe" path
- **At temp 0.2–0.5**, tool calling worked reliably
- **Above 1.0**, tool call parameter extraction had a **2%+ variance** even with identical inputs
- **Structured JSON modes** (like Ollama's `"format": "json"`) significantly reduce temperature's impact since the model is constrained to valid JSON tokens

**Your bot uses `"format": "json"` in Ollama calls**, which helps a LOT. This constrains the output to valid JSON syntax regardless of temperature — but it doesn't prevent hallucinated *content* inside valid JSON (e.g., inventing a ticker symbol that doesn't exist, or flipping BUY to HOLD for no reason).

### 3. Temperature and Financial Decision-Making

Research on LLM trading agents shows:

- **Low temp (0–0.3):** Conservative decisions. Tends to predict minor fluctuations. May default to HOLD too often.
- **Higher temp (0.7–1.0):** More "adventurous" predictions (bigger swings). **BUT**: also more contradictions, more reasoning drift, and higher risk of factually wrong analysis.
- Studies found LLMs at higher temps are **"overly conservative in bull markets and overly aggressive in bear markets"** — the opposite of what you want.

### 4. Prompt Engineering > Temperature

A 2025 study on open-source LLMs found:
> **"Prompt design and system instructions have a more substantial influence on factual accuracy and hallucination behavior than temperature settings."**

This means: if you want the bot to trade more aggressively, you'll get **much better results** by adjusting the system prompt (e.g., lowering conviction thresholds, or reframing the decision criteria) than by cranking the temperature.

---

## Specific Recommendations for Your Bot

### ❌ Don't Do This

- Raising temp above 0.7 for trading decisions
- Using different temps for different pipeline stages without testing
- Assuming higher temp = more trades (it just = more randomness)

### ✅ Do This Instead

| Approach | Why It Works |
|---|---|
| **Keep temp at 0.3** | Your JSON pipeline works. Don't break it. |
| **Lower conviction thresholds** in prompts | If the bot needs 80% conviction to BUY, try 65%. This is surgical and testable. |
| **Adjust the strategist prompt** | Tell the LLM to be more aggressive, or that HOLD is the worst outcome. Prompt > temperature. |
| **A/B test with temp 0.5** | If you really want to try higher temp, 0.5 is the safest experiment. Still within the "stable zone" for Granite. |
| **Use temp 0.7 ONLY for sentiment/discovery** | Non-critical stages (Reddit LLM filter, transcript scanning) can tolerate more creativity. Trading decisions should stay low. |

### 🧪 If You Want to Experiment

Try a **split temperature approach**:

```
Discovery/filtering stages:  temp 0.5–0.7  (more creative = finds more tickers)
Analysis/dossier stages:     temp 0.3      (needs accuracy)
Trading decisions:           temp 0.1–0.3  (needs precision and reliable JSON)
```

This gives you "more aggressive discovery" without risking broken trade JSON.

---

## Sources

- arXiv 2025: *"Systematic evaluation of temperature's impact on LLMs across capabilities"*
- arXiv 2025: *"Temperature and hallucination rate in LLMs"*
- IBM Granite 4 Documentation: *"Best practices for temperature settings"*
- Berkeley Function Calling Leaderboard (BFCL) 2024–2025
- TradingAgents Framework (GitHub, 2025): *"Trading performance varies based on model temperature"*
- ACL Anthology 2025: *"Correlation between sampling temperature and hallucination probability"*
