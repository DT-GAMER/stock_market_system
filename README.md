# EquityKobo

EquityKobo is an investment research platform that helps long-term investors identify high-quality Nigerian companies trading at attractive valuations. Rather than predicting stock prices, it collects verifiable financial data, evaluates businesses, ranks opportunities, and documents investment decisions through a transparent research process.

It is designed for investors who want to buy businesses, not chase ticker symbols. The system does not place trades and does not give automatic financial advice. It helps you collect evidence, review sources, score companies, understand risks, and decide what deserves further research.

## Product Goal

EquityKobo answers one practical question:

> Which Nigerian companies deserve my attention before I invest?

EquityKobo combines:

- Business fundamentals: earnings, revenue, cash flow, margins, and balance sheet strength
- Market data: price, volume, liquidity, and NGX market-rule checks
- Valuation analysis: P/E, dividend yield, sector-aware valuation signals, and fair-value research inputs
- Research provenance: source tracking, review status, and audit logs for important data
- Sector-aware scoring: banks, telecoms, industrials, agriculture, consumer goods, and general companies
- Portfolio tracking: positions, exposure, dividends received, and decision history

The system is intentionally conservative. NGX Pulse market data is treated as trusted for prices and market overview, while manual entries, uploaded reports, and AI-extracted fundamentals remain unreviewed until you approve them. Low-confidence records reduce the usefulness of scanner output.

## Investment Philosophy

EquityKobo is built on five principles:

- Buy businesses, not ticker symbols.
- Every important metric should be traceable to its source.
- Every investment decision should have an explanation.
- Companies should be compared within their sectors.
- The system should support investor judgment, never replace it.

## Core Capabilities

### Research Data Foundation

- Company registry for NGX-listed companies
- CSV imports for prices, financial statements, and dividends
- Source document tracking
- Trusted NGX Pulse market-data ingestion
- Uploaded annual/quarterly report storage
- Reviewed/unreviewed flags for key records
- Pending review queue and audit logs

### Report and AI Extraction Workflow

- Upload local PDF reports
- Extract readable text from PDFs
- Send report text to DeepSeek for structured extraction drafts
- Store raw model responses and parsed JSON
- Apply extraction drafts into financial statements only as unreviewed records

DeepSeek is used only to assist extraction. It does not approve data and does not make final investment decisions.

### Market Scanner

- Calculates financial ratios such as P/E, ROE, net margin, debt-to-equity, cash-flow conversion, revenue growth, profit growth, and dividend yield
- Scores companies across quality, valuation, growth, dividend strength, and risk
- Uses sector-aware scoring profiles
- Applies hard rejection rules for missing EPS, negative EPS, weak confidence, severe leverage, and other red flags
- Produces ranked scan results

### Dividend Research

- Validates dividend import files
- Tracks dividend history by company
- Calculates trailing dividend yield
- Estimates payout ratio and dividend cover where EPS exists
- Ranks dividend candidates with warning flags

### Portfolio Tracking

- Records buy, sell, and dividend transactions
- Derives current positions from transaction history
- Calculates average cost, cost basis, market value, unrealized gain/loss, and dividends received
- Shows sector allocation
- Warns about single-stock and sector concentration

### Decision Journal

- Stores investment thesis, risks, and decision label per company
- Supports decision labels such as `BUY`, `WATCH`, `HOLD`, `SELL`, `AVOID`, and `RESEARCH`
- Builds company research briefs combining notes, scanner score, portfolio position, and checklist items

### Investment Rules Engine

EquityKobo classifies companies using familiar investor language:

- Growth stock
- Value stock
- Dividend stock
- Blue-chip candidate
- Penny stock
- Sector-specific stock

It also applies a practical pre-buy checklist:

- Is the company making money and growing?
- Do I understand the business?
- Does it have something unique?
- Is the price fair compared to earnings?
- Can I hold it for 5+ years?

### NGX Market Rules

EquityKobo includes NGX-specific market-rule checks:

- Daily `+/-10%` price band status
- Limit up, limit down, near limit, or normal classification
- Previous close, latest close, upper limit, and lower limit
- Price-movement group
- Minimum volume required to move official price
- Tick size
- Warnings when price movement needs extra care

Current thresholds used by the system:

```text
Group A: price >= N1,000, minimum 10,000 units, tick N0.10
Group B: price >= N500 and < N1,000, minimum 50,000 units, tick N0.05
Group C: price < N500, minimum 100,000 units, tick N0.01
```

### Watchlists, Alerts, Digests, and Exports

- Create focused watchlists such as Banks, Dividend Targets, or Buy Candidates
- Create alert rules for price, score, status, and portfolio weight
- Evaluate alerts and store triggered events
- Generate a weekly research digest
- Export research outputs as CSV

Supported CSV exports:

```text
portfolio_positions
alert_events
latest_scores
dividend_candidates
watchlists
digest_actions
```

## Typical Workflow

1. Import or create the company universe.
2. Sync NGX Pulse prices and market overview.
3. Upload company reports and extract text where useful.
4. Use DeepSeek to create extraction drafts from report text.
5. Review and approve important imported/extracted records.
6. Run the market scanner.
7. Check dividend candidates, investment rules, and NGX market-rule status.
8. Write a research note before buying.
9. Record portfolio transactions after investing.
10. Monitor alerts and weekly digest actions.
11. Export CSV snapshots when needed.

## Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL via `DATABASE_URL`
- DeepSeek-compatible LLM extraction client
- NGX Pulse market-data integration

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn ngx_research.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

Set DeepSeek config in `.env`:

```bash
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
NGXPULSE_API_KEY=your_key_here
NGXPULSE_BASE_URL=https://www.ngxpulse.ng
```

## Useful Endpoints

```text
POST /auth/signup
POST /auth/login
GET /auth/me
POST /auth/logout
GET /companies
GET /prices
GET /financial-statements
GET /dividends
GET /ratios
GET /scores
POST /scans/run
GET /scans/latest
GET /sources
POST /sources
POST /reports/upload
GET /reports
POST /reports/{report_id}/extract-text
GET /reports/{report_id}/text
POST /llm/extraction-drafts/from-text
POST /reports/{report_id}/extraction-drafts
GET /llm/extraction-drafts
POST /llm/extraction-drafts/{draft_id}/apply
GET /review/pending
POST /review/{record_type}/{record_id}/approve
POST /review/{record_type}/{record_id}/flag
GET /coverage/source
GET /coverage/source/{symbol}
POST /prices/validate-import
GET /prices/latest
GET /prices/{symbol}/history
GET /prices/liquidity
POST /dividends/validate-import
GET /dividends/{symbol}/history
GET /dividends/candidates
POST /portfolio/transactions
GET /portfolio/transactions
GET /portfolio/summary
GET /portfolio/exit-intelligence
POST /research/notes
GET /research/notes
POST /research/goals
GET /research/goals
GET /research/{symbol}/brief
GET /rules/investment
GET /rules/investment/{symbol}
GET /rules/ngx/{symbol}
POST /watchlists
GET /watchlists
GET /watchlists/{watchlist_id}
GET /watchlists/{watchlist_id}/intelligence
POST /watchlists/{watchlist_id}/items
DELETE /watchlists/{watchlist_id}/items/{symbol}
POST /alerts/rules
GET /alerts/rules
POST /alerts/rules/{rule_id}/activate
POST /alerts/rules/{rule_id}/deactivate
POST /alerts/evaluate
GET /alerts/events
POST /alerts/events/{event_id}/acknowledge
POST /alerts/events/{event_id}/dismiss
GET /digest/weekly
GET /exports/{dataset}.csv
GET /integrations/ngxpulse/market
POST /integrations/ngxpulse/sync/stocks
POST /integrations/ngxpulse/sync/prices/{symbol}
```

## Example Commands

Create an account:

```bash
curl -X POST "http://127.0.0.1:8000/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"email":"investor@example.com","password":"strongpass123","full_name":"EquityKobo Investor"}'
```

Login:

```bash
curl -X POST "http://127.0.0.1:8000/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"investor@example.com","password":"strongpass123"}'
```

Use the returned token:

```bash
curl "http://127.0.0.1:8000/auth/me" \
  -H "Authorization: Bearer your_token_here"
```

Run the daily NGX Pulse sync job:

```bash
equitykobo-sync daily-market
```

This syncs the latest NGX Pulse stock snapshot, refreshes the market scan, and evaluates alert rules.

Run the same job for only selected symbols:

```bash
equitykobo-sync daily-market --symbol GTCO --symbol ZENITHBANK --days 5
```

Run the scanner:

```bash
curl -X POST "http://127.0.0.1:8000/scans/run"
curl "http://127.0.0.1:8000/scans/latest"
```

Sync real NGX Pulse prices:

```bash
curl "http://127.0.0.1:8000/integrations/ngxpulse/market"
curl -X POST "http://127.0.0.1:8000/integrations/ngxpulse/sync/stocks"
curl -X POST "http://127.0.0.1:8000/integrations/ngxpulse/sync/prices/GTCO?days=2"
```

Create an investment goal for a holding:

```bash
curl -X POST "http://127.0.0.1:8000/research/goals" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "GTCO",
    "goal_type": "capital_gain",
    "reason": "Buy for capital gain after confirming valuation and earnings quality.",
    "target_return_percent": 35,
    "review_date": "2026-12-31",
    "sell_rule": "Review for profit-taking when target return is reached or thesis weakens."
  }'
```

Review smart watchlist entry signals:

```bash
curl "http://127.0.0.1:8000/watchlists/1/intelligence"
```

Review portfolio sell/hold signals:

```bash
curl "http://127.0.0.1:8000/portfolio/exit-intelligence"
```

## Scheduled Sync

For local automation, run the sync job from cron after the Nigerian market closes. Example:

```cron
30 16 * * 1-5 cd /home/dtgamer/Work/stock_market_system && . .venv/bin/activate && equitykobo-sync daily-market >> data/equitykobo-sync.log 2>&1
```

If cron runs outside your normal shell environment, include `DATABASE_URL` or make sure `.env` is available:

```cron
30 16 * * 1-5 cd /home/dtgamer/Work/stock_market_system && . .venv/bin/activate && equitykobo-sync daily-market >> data/equitykobo-sync.log 2>&1
```

Validate and import prices:

```bash
curl -X POST "http://127.0.0.1:8000/prices/validate-import" \
  -F "file=@samples/prices_phase8.csv"

curl -X POST "http://127.0.0.1:8000/imports/prices" \
  -F "file=@samples/prices_phase8.csv"
```

Extract text from an uploaded PDF:

```bash
curl -X POST "http://127.0.0.1:8000/reports/1/extract-text"
curl "http://127.0.0.1:8000/reports/1/text"
```

Create a DeepSeek extraction draft from pasted report text:

```bash
curl -X POST "http://127.0.0.1:8000/llm/extraction-drafts/from-text" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GTCO",
    "source_document_id":1,
    "report_text":"Paste annual or quarterly report text here"
  }'
```

Record a research decision:

```bash
curl -X POST "http://127.0.0.1:8000/research/notes" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GTCO",
    "note_date":"2026-07-28",
    "decision":"WATCH",
    "thesis":"Strong banking franchise, but I want source-reviewed latest results before adding more.",
    "risks":"High existing exposure to financial services."
  }'
```

Inspect investment and NGX market rules:

```bash
curl "http://127.0.0.1:8000/rules/investment/GTCO"
curl "http://127.0.0.1:8000/rules/ngx/GTCO"
```

Record portfolio activity:

```bash
curl -X POST "http://127.0.0.1:8000/portfolio/transactions" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GTCO",
    "transaction_date":"2026-07-28",
    "transaction_type":"BUY",
    "quantity":500,
    "price_per_share":102.5,
    "fees":500,
    "notes":"Initial position"
  }'
```

Generate the weekly digest:

```bash
curl "http://127.0.0.1:8000/digest/weekly?limit=10"
```

Export research outputs:

```bash
curl "http://127.0.0.1:8000/exports/portfolio_positions.csv"
curl "http://127.0.0.1:8000/exports/latest_scores.csv?limit=20"
curl "http://127.0.0.1:8000/exports/dividend_candidates.csv"
curl "http://127.0.0.1:8000/exports/alert_events.csv"
curl "http://127.0.0.1:8000/exports/watchlists.csv"
curl "http://127.0.0.1:8000/exports/digest_actions.csv"
```

## CSV Import Formats

Sample files live in `samples/`.

### prices.csv

```csv
symbol,trade_date,close_price,open_price,high_price,low_price,volume,value_traded,source_name,source_url
GTCO,2026-07-27,101.5,100,103,99,1200000,121800000,Manual Upload,
```

### financial_statements.csv

```csv
symbol,period_end,period_type,currency,revenue,profit_after_tax,total_assets,total_liabilities,total_equity,cash_flow_operations,eps,source_name,source_url
GTCO,2025-12-31,FY,NGN,2500000000000,700000000000,14500000000000,12200000000000,2300000000000,900000000000,23.4,Annual Report,
```

### dividends.csv

```csv
symbol,declared_date,ex_dividend_date,payment_date,amount_per_share,currency,source_name,source_url
GTCO,2026-03-01,2026-03-15,2026-04-01,8.03,NGN,Dividend Notice,
```

## Starter Company Universe

Start with 20 liquid and familiar NGX names:

- GTCO
- ZENITHBANK
- ACCESSCORP
- UBA
- FIDELITYBK
- MTNN
- AIRTELAFRI
- DANGCEM
- BUACEMENT
- WAPCO
- NESTLE
- BUAFOODS
- NB
- UNILEVER
- PRESCO
- OKOMUOIL
- SEPLAT
- ARADEL
- TRANSCORP
- AIICO

## Implementation History

- Phase 1: data foundation and manual/semi-manual imports
- Phase 2: deterministic ratio calculation and opportunity scanning
- Phase 3: review and approval workflows
- Phase 4: real data intake, report uploads, and source-linked financial entry
- Phase 5: DeepSeek-assisted extraction drafts
- Phase 6: local PDF text extraction
- Phase 7: source coverage tracking
- Phase 8: price validation, latest prices, history, and liquidity
- Phase 9: dividend analysis
- Phase 10: sector-aware scanner upgrades and hard rejection rules
- Phase 11: portfolio tracker
- Phase 12: investment decision journal
- Phase 13: reserved for dashboard UI
- Phase 14: watchlist management
- Phase 15: alert rules and evaluation
- Phase 16: weekly research digest
- Phase 17: CSV exports
- Phase 18: investment classification, buy-checklist, and NGX market-rule checks

## Vision

To become the research operating system for long-term equity investors, starting with the Nigerian Exchange and expanding to global markets.

## Important Note

EquityKobo is not financial advice and does not place trades. It is a private decision-support system. Review all imported, extracted, and calculated data before using it for real investment decisions.
