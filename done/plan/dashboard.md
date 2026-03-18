# Dashboard Feature — Checklist

## Phase 1: Backend APIs (Session 1)
- [x] `GET /api/dashboard/summary` — aggregate stats across all bots
- [x] `GET /api/dashboard/bots/compare` — per-bot cards with equity data
- [x] `GET /api/dashboard/activity` — unified trade log across all bots
- [x] `GET /api/dashboard/institutional/movers` — top hedge fund buys/sells

## Phase 2: Frontend Dashboard Page (Session 1-2)
- [ ] Add "Dashboard" nav item to sidebar (above Watchlist)
- [ ] Add `/dashboard` route
- [ ] Summary bar: total portfolio, P&L, trades, win rate
- [ ] Bot comparison cards: P&L, positions, equity sparkline, recent trades
- [ ] Recent activity feed: buy/sell log across all bots
- [ ] Institutional movers: top buys and sells this quarter

## Phase 3: Cross-Bot Analytics (Session 3)
- [ ] Equity curve overlay chart (all bots same axes)
- [ ] Per-bot trade timeline (who bought/sold what when)
- [ ] Win rate comparison bar chart
- [ ] Drawdown comparison chart

## Phase 4: Hedge Fund Signal Integration (Session 4-5)
- [ ] Surface institutional consensus/overlap on dashboard
- [ ] Allow bots to query hedge fund holdings during analysis
- [ ] Cross-reference bot positions vs institutional positions
- [ ] Build "follow the money" signal source for the pipeline
