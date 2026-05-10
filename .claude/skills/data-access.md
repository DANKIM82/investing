# Data Access Reference

All skills that need financial data should follow this reference. Read `design-system.md` (in this same directory) for formatting, analytical density, and styling conventions.

> **NOTE:** This is a Daloopa-free fork. Original Daloopa MCP calls have been replaced with `infra/free_data.py`, a wrapper using **yfinance** + **SEC EDGAR** (no API key, no signup). Data quality is materially below Daloopa — expect missing KPIs, no consensus estimates, US-domiciled tickers only, and weaker historical coverage. Treat outputs as learning/research drafts, not investment-grade.

---

## Section 1: Free Data Wrapper (`infra/free_data.py`)

This wrapper exposes 4 subcommands that mirror Daloopa MCP's 4 core functions. All commands output JSON to stdout.

| Operation | Command |
|---|---|
| Find company by ticker | `python infra/free_data.py companies TICKER` — returns `company_id`, `latest_calendar_quarter`, `cik`, sector, etc. |
| Find available series/metrics | `python infra/free_data.py series TICKER --keywords KEYWORD1,KEYWORD2` |
| Pull financial data | `python infra/free_data.py fundamentals TICKER --periods 2024Q1,2024Q2 --series revenue,net_income` |
| Search SEC filings | `python infra/free_data.py documents "QUERY" --tickers AAPL,MSFT --forms 10-K,10-Q` |

**Important:** the `company_id` returned is just the ticker itself (e.g. `"AAPL"`). Pass it to subsequent calls as the ticker argument.

**Available series IDs** (use these in the `--series` argument):

- **Income statement**: `revenue`, `cost_of_revenue`, `gross_profit`, `research_development`, `selling_general_admin`, `operating_expenses`, `operating_income`, `ebitda`, `interest_expense`, `pretax_income`, `tax_expense`, `net_income`, `diluted_eps`, `basic_eps`, `diluted_shares`, `basic_shares`
- **Balance sheet**: `cash_and_equivalents`, `current_assets`, `inventory`, `accounts_receivable`, `total_assets`, `current_liabilities`, `long_term_debt`, `total_debt`, `total_liabilities`, `total_equity`
- **Cash flow**: `operating_cash_flow`, `capex`, `free_cash_flow`, `dividends_paid`, `share_repurchases`

If a metric you need isn't listed, run `series TICKER` first to see what's actually available for that company.

**Operating KPIs (subscribers, ARR, GMV, DAU, etc.) are NOT available** through yfinance/SEC EDGAR in structured form. To capture company-specific KPIs, use the `documents` subcommand to search the latest 10-Q / 10-K and extract them from filing text. Note explicitly in the output when a KPI was extracted from text vs. structured data.

## Section 1.5: Period Determination

After `companies`, capture `latest_calendar_quarter`. Use it to calculate all period arrays:

| Skill Need | Calculation |
|---|---|
| Last 4 quarters | Work backward 4Q from `latest_calendar_quarter` |
| Last 8 quarters | Work backward 8Q from `latest_calendar_quarter` |
| Last 10 quarters | Work backward 10Q from `latest_calendar_quarter` |
| Last 4Q + YoY | 8 quarters: latest 4 + same 4 one year prior |
| Document search (recent) | Latest 2 quarters from `latest_calendar_quarter` |

Example: if `latest_calendar_quarter` = "2025Q4", last 8Q = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4"]

**NEVER assume the current calendar date determines the latest available quarter — always use the field returned by `companies`.**

### Fiscal Year Context

yfinance returns data on a calendar-quarter basis. The wrapper currently sets `fiscal_period` equal to `calendar_period` as an approximation. For companies with non-calendar fiscal years (e.g., Apple FY ends September), you'll need to mentally adjust labels — note the `fiscal_year_end_month` field in the `companies` response and re-label accordingly in your output. For multi-company comparison work (industry, comps), always use `calendar_period` to normalize.

## Section 2: External Market Data

Skills that need market-side data should gather:

| Data Need | What to Get |
|---|---|
| **Stock quote** | Current price, market cap, shares outstanding, beta |
| **Trading multiples** | Trailing P/E, Forward P/E, EV/EBITDA, P/S, P/B, dividend yield |
| **Historical prices** | OHLCV data for trend analysis (1-5 years) |
| **Peer multiples** | Side-by-side trading multiples for 5-10 comparable companies |
| **Risk-free rate** | 10Y Treasury yield (for WACC/DCF calculations) |

**Resolution order — use the first available source:**

1. **MCP tools** — Check available tools for any MCP server providing market data. Use whatever the user has configured.
2. **Infra scripts** — Use `infra/market_data.py` (yfinance + FRED-based; see Section 5).
3. **Web search** — If neither MCP nor infra is available, use web search for current price and key multiples.
4. **Defaults** — If no source available, use beta=1.0, risk-free rate=4.5% and note the limitation.

## Section 3: Consensus Estimates (Optional, Limited)

Free sources have **limited consensus data**. yfinance exposes:

- `yf.Ticker(TICKER).analyst_price_targets` — current/target/mean/high/low price targets
- `yf.Ticker(TICKER).recommendations` — analyst rating distribution over time
- `yf.Ticker(TICKER).earnings_estimate` — revenue/EPS estimates if available
- `yf.Ticker(TICKER).revenue_estimate` — revenue estimates if available

**Treat as "may be missing or stale".** If estimates aren't available, skip those sections and write "consensus data not available" rather than guessing. Don't fabricate beat/miss numbers — for those, use actuals from `fundamentals` and note that consensus comparison is unavailable.

## Section 4: Citation Requirements (MANDATORY)

**Every financial figure must include a citation link.** This is non-negotiable.

The `fundamentals` response gives you both `source` and `source_url` for every data point. Use them.

**For yfinance figures:**
```
[$X.XX million](https://finance.yahoo.com/quote/TICKER/financials)
```

**For SEC EDGAR document quotes (from `documents` subcommand):**
```
[quoted text](https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION/FILENAME)
```
The `documents` response gives you the full URL in the `url` field — use it directly.

**For computed metrics** (margins, growth rates, ratios), cite the underlying inputs. E.g., "gross margin of 42.3% [source: yfinance](URL)" where URL points to the financials page used.

**Honesty rule:** If a number can't be cited (you couldn't find it in either yfinance or SEC EDGAR), DO NOT make it up. Either omit it or write "n/a — not disclosed in available sources".

## Section 4.5: Firm Attribution

Every output must display "Prepared by {FIRM_NAME}":
- **Default**: "Personal Research" (this is a learning/side project, not a firm)
- **User override**: If the user specifies a firm name in their prompt, use that instead.
- **NEVER hallucinate** real firm names (Goldman, Morgan Stanley, JPM, etc.).

For HTML reports, the footer reads: `Prepared by {FIRM_NAME} | Data sourced from yfinance & SEC EDGAR`
For Word documents, include firm name on cover page and document headers.
For Excel models, include firm name on the cover/summary tab.
For pitch decks, include firm name on the cover slide and slide footers.

---

## Section 5: Infrastructure Tools (Project Repo)

The following tools are available in the project repo. If a script is missing, skip that step — the skill's core analysis works without it.

### Market Data Scripts (Fallback)

If no market-data MCP is available, use these:

| Operation | Command |
|---|---|
| Current quote (price, mkt cap, beta) | `python infra/market_data.py quote TICKER` |
| Trading multiples (P/E, EV/EBITDA, etc.) | `python infra/market_data.py multiples TICKER` |
| Historical OHLCV | `python infra/market_data.py history TICKER --period 2y` |
| Peer multiples comparison | `python infra/market_data.py peers TICKER1 TICKER2 ...` |
| Risk-free rate (10Y Treasury) | `python infra/market_data.py risk-free-rate` |

All output JSON to stdout.

### Charts

`python infra/chart_generator.py {chart_type} --data '{json}' --output path.png`

Chart types: `time-series`, `waterfall`, `football-field`, `pie`, `scenario-bar`, `dcf-sensitivity`

### Projections

`python infra/projection_engine.py --context input.json --output projections.json`

### HTML Report Output (Building Block Skills)

Building block skills generate styled HTML directly using the template in `design-system.md`. No external scripts needed — the HTML file IS the deliverable. Save to: `reports/{TICKER}_{skill}.html`

### Word / Excel / Comp Sheet Rendering

- Word: `python infra/docx_renderer.py --template templates/research_note.docx --context context.json --output output.docx`
- Excel models: `python infra/excel_builder.py --context context.json --output output.xlsx`
- Comp sheets: `python infra/comp_builder.py --context context.json --output output.xlsx`
- Context diffs: `python infra/report_differ.py --old old.json --new new.json --output diff.json`

---

## Section 6: Known Limitations (vs. Daloopa)

Be transparent about these in any report you generate:

1. **No segment/geographic breakdowns** in structured form — yfinance doesn't expose them. Pull from 10-Q text via `documents` or skip.
2. **No operating KPIs** in structured form — same as above.
3. **Historical depth limited** — yfinance typically gives 4-5 years of quarterly data, not 10+.
4. **Restated/amended figures** may not be reflected — yfinance shows current snapshot.
5. **International tickers** (LSE, Tokyo, Korea) have spotty coverage; SEC EDGAR is US-only.
6. **No fundamental_id-level audit trail** — citations link to the financials page, not the specific filing line item.

Always disclose data source limitations in the report's source line: `Source: yfinance + SEC EDGAR (free data; some metrics may be missing or restated)`.
