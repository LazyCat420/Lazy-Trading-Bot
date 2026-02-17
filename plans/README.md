# Lazy Trading Bot — Plans Index

## Build Roadmap

| Phase | Plan | Status | Description |
|-------|------|--------|-------------|
| 0-7 | *(completed)* | ✅ Done | Core bot: config, models, collectors, agents, engine, pipeline, API |
| **8** | [Collectors & Agents Upgrade](collectors_and_agents_upgrade.md) | ✅ Done | Expand all 4 collectors and 4 agents to production depth |
| **9** | [Data Hardening](phase_8_data_hardening.md) | ✅ Done | YouTube 24h filter + channel list, yFinance verification |
| **10** | [Frontend Dashboard](phase_9_frontend_dashboard.md) | ✅ Done | Dark-themed dashboard with agent cards, charts, strategy editor |
| **11** | [Scheduling & Backtesting](phase_10_scheduling_backtesting.md) | 📋 Planned | APScheduler automation, historical backtesting, multi-ticker |
| **12** | [Ticker Discovery](01_ticker_discovery.md) | 🔜 Next | YouTube transcript + Reddit scraping to find trending tickers |
| **13** | [Automated Watchlist](02_automated_watchlist.md) | 📋 Planned | Auto-managed watchlist with scoring, aging, and rotation |
| **14** | [Trading Engine](03_trading_engine.md) | 📋 Planned | Paper/live trading with positions, triggers, portfolio mgmt |
| **15** | [Autonomous Scheduler](04_scheduler.md) | 📋 Planned | Full daily automation: pre-market → market hours → EOD |

## Quick Reference

**Current focus**: Phase 8 — expand collectors to pull maximum data, upgrade agents to analyze it deeply.

**Key files**:

- Config: `app/config.py` (all LLM URLs + paths)
- Pipeline: `app/services/pipeline_service.py` (orchestrator)
- Collectors: `app/collectors/` (yfinance, technical, news, youtube)
- Agents: `app/agents/` (technical, fundamental, sentiment, risk)
- Models: `app/models/` (market_data, agent_reports, decision)
- Prompts: `app/prompts/` (system prompts for each agent)
- Database: `app/database.py` (DuckDB schema)
