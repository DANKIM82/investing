#!/usr/bin/env python3
"""
free_data.py - Daloopa-equivalent wrapper using free data sources.

Mimics Daloopa MCP's 4 core functions using yfinance + SEC EDGAR.
No API key, no signup. SEC EDGAR requires a User-Agent (set SEC_USER_AGENT env var).

CLI usage (called from Claude Code skills):
    python infra/free_data.py companies AAPL
    python infra/free_data.py series AAPL --keywords revenue
    python infra/free_data.py fundamentals AAPL --periods 2024Q1,2024Q2 --series revenue,net_income
    python infra/free_data.py documents "AI revenue" --tickers AAPL,MSFT

Output: JSON to stdout. Diagnostics/warnings go to stderr.

Mapping to Daloopa MCP:
    discover_companies         -> companies
    discover_company_series    -> series
    get_company_fundamentals   -> fundamentals
    search_documents           -> documents
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime
from typing import Any

import requests
import yfinance as yf

# SEC EDGAR REQUIRES a User-Agent identifying you. Override via env var.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Personal Research project@example.com",
)
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


# ============================================================================
# Helpers
# ============================================================================

def _eprint(msg: str) -> None:
    """Warnings go to stderr to keep stdout clean for JSON output."""
    print(msg, file=sys.stderr)


def _calendar_quarter_from_date(d) -> str:
    """date -> '2024Q3'"""
    if hasattr(d, "date"):
        d = d.date()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


# Cached CIK lookup table
_CIK_CACHE: dict[str, str] | None = None


def _ticker_to_cik(ticker: str) -> str | None:
    """Look up SEC CIK for a ticker. Returns 10-digit zero-padded string."""
    global _CIK_CACHE
    if _CIK_CACHE is None:
        try:
            r = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=SEC_HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            raw = r.json()
            _CIK_CACHE = {
                entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
                for entry in raw.values()
            }
        except Exception as e:
            _eprint(f"[warn] CIK table load failed: {e}")
            _CIK_CACHE = {}
    return _CIK_CACHE.get(ticker.upper())


def _format_value(v: float, sid: str) -> str:
    """Format a number per design-system.md conventions."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if "eps" in sid:
        return f"${v:.2f}"
    if "shares" in sid:
        if abs(v) >= 1e9:
            return f"{v/1e9:.2f}bn shares"
        return f"{v/1e6:,.0f}mm shares"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}bn"
    if abs(v) >= 1e6:
        return f"${v/1e6:,.0f}mm"
    return f"${v:,.0f}"


# ============================================================================
# Series catalog: standardized IDs -> yfinance row labels
# ============================================================================

SERIES_CATALOG: dict[str, dict[str, Any]] = {
    # Income Statement
    "revenue":             {"labels": ["Total Revenue", "Operating Revenue"],                 "category": "income_statement"},
    "cost_of_revenue":     {"labels": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],      "category": "income_statement"},
    "gross_profit":        {"labels": ["Gross Profit"],                                       "category": "income_statement"},
    "research_development":{"labels": ["Research And Development"],                           "category": "income_statement"},
    "selling_general_admin":{"labels": ["Selling General And Administration"],                "category": "income_statement"},
    "operating_expenses":  {"labels": ["Operating Expense", "Total Operating Expenses"],      "category": "income_statement"},
    "operating_income":    {"labels": ["Operating Income"],                                   "category": "income_statement"},
    "ebitda":              {"labels": ["EBITDA", "Normalized EBITDA"],                        "category": "income_statement"},
    "interest_expense":    {"labels": ["Interest Expense"],                                   "category": "income_statement"},
    "pretax_income":       {"labels": ["Pretax Income"],                                      "category": "income_statement"},
    "tax_expense":         {"labels": ["Tax Provision"],                                      "category": "income_statement"},
    "net_income":          {"labels": ["Net Income", "Net Income Common Stockholders"],       "category": "income_statement"},
    "diluted_eps":         {"labels": ["Diluted EPS"],                                        "category": "income_statement"},
    "basic_eps":           {"labels": ["Basic EPS"],                                          "category": "income_statement"},
    "diluted_shares":      {"labels": ["Diluted Average Shares"],                             "category": "income_statement"},
    "basic_shares":        {"labels": ["Basic Average Shares"],                               "category": "income_statement"},

    # Balance Sheet
    "cash_and_equivalents":{"labels": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], "category": "balance_sheet"},
    "current_assets":      {"labels": ["Current Assets"],                                     "category": "balance_sheet"},
    "inventory":           {"labels": ["Inventory"],                                          "category": "balance_sheet"},
    "accounts_receivable": {"labels": ["Receivables", "Accounts Receivable"],                 "category": "balance_sheet"},
    "total_assets":        {"labels": ["Total Assets"],                                       "category": "balance_sheet"},
    "current_liabilities": {"labels": ["Current Liabilities"],                                "category": "balance_sheet"},
    "long_term_debt":      {"labels": ["Long Term Debt"],                                     "category": "balance_sheet"},
    "total_debt":          {"labels": ["Total Debt"],                                         "category": "balance_sheet"},
    "total_liabilities":   {"labels": ["Total Liabilities Net Minority Interest"],            "category": "balance_sheet"},
    "total_equity":        {"labels": ["Stockholders Equity", "Total Equity Gross Minority Interest"], "category": "balance_sheet"},

    # Cash Flow
    "operating_cash_flow": {"labels": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], "category": "cash_flow"},
    "capex":               {"labels": ["Capital Expenditure", "Net PPE Purchase And Sale"],   "category": "cash_flow"},
    "free_cash_flow":      {"labels": ["Free Cash Flow"],                                     "category": "cash_flow"},
    "dividends_paid":      {"labels": ["Cash Dividends Paid"],                                "category": "cash_flow"},
    "share_repurchases":   {"labels": ["Repurchase Of Capital Stock"],                        "category": "cash_flow"},
}


def _find_row(df, candidate_labels: list[str]):
    """Find a row in a yfinance DataFrame matching any candidate label."""
    if df is None or df.empty:
        return None
    for label in candidate_labels:
        if label in df.index:
            return df.loc[label]
    # Case-insensitive fallback
    lower_idx = {str(i).lower(): i for i in df.index}
    for label in candidate_labels:
        if label.lower() in lower_idx:
            return df.loc[lower_idx[label.lower()]]
    return None


# ============================================================================
# Subcommand: companies (replaces discover_companies)
# ============================================================================

def cmd_companies(args) -> dict[str, Any]:
    ticker = args.ticker.upper()
    yt = yf.Ticker(ticker)

    try:
        info = yt.info or {}
    except Exception as e:
        _eprint(f"[warn] yfinance info fetch failed: {e}")
        info = {}

    cik = _ticker_to_cik(ticker)

    # Latest reported quarter from quarterly income statement
    latest_cal_q = None
    fy_end_month = None
    try:
        qis = yt.quarterly_income_stmt
        if qis is not None and not qis.empty:
            latest = max(qis.columns)
            latest_cal_q = _calendar_quarter_from_date(latest)
    except Exception as e:
        _eprint(f"[warn] could not infer latest quarter: {e}")

    if "lastFiscalYearEnd" in info:
        try:
            fy_end_date = datetime.fromtimestamp(info["lastFiscalYearEnd"]).date()
            fy_end_month = fy_end_date.month
        except Exception:
            pass

    return {
        "results": [{
            "company_id": ticker,                          # Use ticker as ID throughout
            "name": info.get("longName") or info.get("shortName") or ticker,
            "ticker": ticker,
            "cik": cik,
            "latest_calendar_quarter": latest_cal_q,
            "latest_fiscal_quarter": latest_cal_q,         # Approximation; treat as same for now
            "fiscal_year_end_month": fy_end_month,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "exchange": info.get("exchange"),
            "currency": info.get("currency", "USD"),
            "description": info.get("longBusinessSummary"),
        }]
    }


# ============================================================================
# Subcommand: series (replaces discover_company_series)
# ============================================================================

def cmd_series(args) -> dict[str, Any]:
    ticker = args.ticker.upper()
    keywords = []
    if args.keywords:
        keywords = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]

    out = []
    for sid, meta in SERIES_CATALOG.items():
        if keywords:
            haystack = f"{sid} {' '.join(meta['labels'])}".lower()
            if not any(k in haystack for k in keywords):
                continue
        out.append({
            "series_id": sid,
            "label": meta["labels"][0],
            "category": meta["category"],
            "yfinance_aliases": meta["labels"],
        })

    return {"company_id": ticker, "series": out, "total": len(out)}


# ============================================================================
# Subcommand: fundamentals (replaces get_company_fundamentals)
# ============================================================================

def cmd_fundamentals(args) -> dict[str, Any]:
    ticker = args.ticker.upper()
    periods = []
    if args.periods:
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    series_ids = list(SERIES_CATALOG.keys())
    if args.series:
        series_ids = [s.strip() for s in args.series.split(",") if s.strip()]

    yt = yf.Ticker(ticker)
    try:
        qis = yt.quarterly_income_stmt
        qbs = yt.quarterly_balance_sheet
        qcf = yt.quarterly_cashflow
    except Exception as e:
        _eprint(f"[error] yfinance statements fetch failed: {e}")
        return {"company_id": ticker, "data": [], "error": str(e)}

    data_points = []

    for sid in series_ids:
        meta = SERIES_CATALOG.get(sid)
        if not meta:
            _eprint(f"[warn] unknown series '{sid}', skipping")
            continue

        labels = meta["labels"]
        # NOTE: pandas Series can't be evaluated for truthiness with `or`,
        # so we check each statement explicitly.
        row = _find_row(qis, labels)
        if row is None:
            row = _find_row(qbs, labels)
        if row is None:
            row = _find_row(qcf, labels)

        if row is None:
            _eprint(f"[warn] series '{sid}' not found in yfinance data for {ticker}")
            continue

        for col_date, value in row.items():
            if value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue

            period_date = col_date.date() if hasattr(col_date, "date") else col_date
            cal_q = _calendar_quarter_from_date(period_date)

            if periods and cal_q not in periods:
                continue

            try:
                value_num = float(value)
            except (TypeError, ValueError):
                continue

            unit = "USD"
            if "eps" in sid:
                unit = "USD/share"
            elif "shares" in sid:
                unit = "shares"

            data_points.append({
                "id": f"{ticker}_{sid}_{cal_q}",        # Synthetic citation ID
                "series_id": sid,
                "label": labels[0],
                "category": meta["category"],
                "calendar_period": cal_q,
                "fiscal_period": cal_q,                 # Approximation
                "period_end_date": str(period_date),
                "value": value_num,
                "value_formatted": _format_value(value_num, sid),
                "unit": unit,
                "source": "yfinance",
                "source_url": f"https://finance.yahoo.com/quote/{ticker}/financials",
            })

    # Sort: series_id, then chronological
    data_points.sort(key=lambda d: (d["series_id"], d["calendar_period"]))

    return {
        "company_id": ticker,
        "periods_requested": periods,
        "series_requested": series_ids,
        "data": data_points,
        "total": len(data_points),
    }


# ============================================================================
# Subcommand: documents (replaces search_documents)
# ============================================================================

def cmd_documents(args) -> dict[str, Any]:
    query = args.query
    tickers = []
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    forms = (args.forms or "10-K,10-Q,8-K").split(",")

    ciks = []
    for t in tickers:
        c = _ticker_to_cik(t)
        if c:
            ciks.append(c)  # SEC EDGAR full-text search uses 10-digit zero-padded CIKs
        else:
            _eprint(f"[warn] CIK not found for {t}")

    params = {
        "q": f'"{query}"',
        "forms": ",".join(forms),
    }
    if ciks:
        params["ciks"] = ",".join(ciks)

    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
            headers=SEC_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        _eprint(f"[error] SEC EDGAR full-text search failed: {e}")
        return {"query": query, "documents": [], "error": str(e)}

    hits = raw.get("hits", {}).get("hits", [])
    docs = []
    for h in hits[: args.limit]:
        src = h.get("_source", {})
        doc_id = h.get("_id", "")
        # _id format: "<accession-no>:<file>"
        if ":" in doc_id:
            acc_dashed, fname = doc_id.split(":", 1)
        else:
            acc_dashed, fname = doc_id, ""
        acc_clean = acc_dashed.replace("-", "")
        ciks_in_doc = src.get("ciks", [])
        cik_for_url = ciks_in_doc[0] if ciks_in_doc else ""

        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_for_url}/"
            f"{acc_clean}/{fname}"
        )

        snippet_parts = h.get("highlight", {}).get("description", [])
        snippet = " ... ".join(snippet_parts) if snippet_parts else src.get("file_description", "")

        docs.append({
            "document_id": acc_dashed,
            "title": (src.get("display_names") or ["Unknown"])[0],
            "form": src.get("form", ""),
            "filed_date": src.get("file_date", ""),
            "tickers": src.get("tickers", []),
            "ciks": ciks_in_doc,
            "url": url,
            "snippet": snippet,
        })

    return {
        "query": query,
        "total": raw.get("hits", {}).get("total", {}).get("value", 0),
        "returned": len(docs),
        "documents": docs,
    }


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Daloopa-equivalent wrapper using yfinance + SEC EDGAR (free).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("companies", help="Look up company by ticker (= discover_companies)")
    p.add_argument("ticker", help="Ticker e.g. AAPL")

    p = sub.add_parser("series", help="List available series for a company (= discover_company_series)")
    p.add_argument("ticker")
    p.add_argument("--keywords", help="Comma-separated filter keywords")

    p = sub.add_parser("fundamentals", help="Pull financial data (= get_company_fundamentals)")
    p.add_argument("ticker")
    p.add_argument("--periods", help="Comma-separated quarters e.g. 2024Q1,2024Q2")
    p.add_argument("--series", help="Comma-separated series IDs e.g. revenue,net_income")

    p = sub.add_parser("documents", help="Search SEC filings (= search_documents)")
    p.add_argument("query", help="Search phrase")
    p.add_argument("--tickers", help="Comma-separated tickers to filter")
    p.add_argument("--forms", help="Comma-separated forms (default: 10-K,10-Q,8-K)")
    p.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    handlers = {
        "companies": cmd_companies,
        "series": cmd_series,
        "fundamentals": cmd_fundamentals,
        "documents": cmd_documents,
    }

    result = handlers[args.cmd](args)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()