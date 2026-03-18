# Issue 04 — Missing Dossier Data (34/34 Tickers)

**Severity:** HIGH  
**Root Cause:** All tickers show `executive_summary` = "Insufficient price data for pattern analysis" despite having 251+ price rows. The `bull_case`, `bear_case`, `key_catalysts`, and scorecard fields are all empty. The distilled data from `DataDistiller` isn't mapping to the correct dossier fields.

## Files to Investigate

### 1. `app/services/deep_analysis_service.py`

- `analyze_ticker()` (line 43): Check how distilled output from `DataDistiller` maps to `TickerDossier` fields
- Lines 149-155: `distill_price_action()`, `distill_fundamentals()`, `distill_risk()` — verify return values are stored in the right dossier fields

### 2. `app/services/data_distiller.py`

- `distill_price_action()` (line 40): Check the "Insufficient price data" threshold — is it requiring > 252 rows when only 251 are available?
- Verify the return format matches what `DeepAnalysisService` expects

### 3. `app/models/dossier.py`

- Check `TickerDossier` schema — are `executive_summary`, `bull_case`, `bear_case` populated from the distiller output or from a separate step?

## Investigation Steps (Before Code Changes)

1. Read `deep_analysis_service.py` lines 91-180 to trace the full data flow
2. Read `data_distiller.py` `distill_price_action()` to find the row count threshold
3. Read `dossier.py` model to see field definitions
4. Check the strategist audit: is it the strategist reading old dossiers, or are the dossiers genuinely empty?

## Verification

### Automated Tests

- `pytest tests/test_data_pipeline.py -v`
- `pytest tests/test_pipeline_diagnostic.py -v`
- New test: create a mock ticker with 251 price rows, run `analyze_ticker()`, assert `executive_summary` is non-empty and `bull_case`/`bear_case` contain financial data

### Manual Verification

- After fix, run one discovery+collection+analysis cycle
- Query DuckDB: `SELECT ticker, executive_summary, bull_case FROM ticker_dossiers ORDER BY created_at DESC LIMIT 5`
- Verify fields are populated with actual analysis text
