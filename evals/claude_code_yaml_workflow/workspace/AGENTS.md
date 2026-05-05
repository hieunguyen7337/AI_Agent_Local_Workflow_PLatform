# Stress workspace: EventLedger (greenfield)

This directory is an **isolated sandbox**. Do not assume files from the parent monorepo exist here unless you create them. Prefer creating everything **from scratch** under this folder.

## Goal

Implement a small **event ledger** library for personal finance–style tracking: append dated entries with an amount and category, then query **monthly totals per category** and export a JSON summary.

## Layout (you create these)

| Path | Purpose |
|------|---------|
| `src/event_ledger/__init__.py` | Export `EventLedger` (and any helpers you need). |
| `src/event_ledger/ledger.py` | Core implementation. |
| `tests/test_ledger.py` | `pytest` tests covering behavior below. |

Optional: `README.md` in `src/event_ledger/` if you want internal module docs (not required).

## Functional requirements

1. **`EventLedger`**
   - `append_event(iso_date: str, amount: float, category: str, note: str = "") -> None`
     - `iso_date` format `YYYY-MM-DD`. Reject invalid dates with `ValueError`.
     - `amount` may be negative (expense) or positive (income).
     - `category` non-empty string after strip; empty → `ValueError`.
   - `monthly_totals_by_category(year: int, month: int) -> dict[str, float]`
     - Month is 1–12. Sum **amount** per **category** for that calendar month only.
     - Return keys sorted alphabetically for determinism (tests may rely on ordering of items when comparing dict equality isn’t enough—use sorted items).
   - `export_summary_json() -> str`
     - One JSON object (pretty-print optional) containing:
       - `"events"`: list of all events in insertion order, each `{"date","amount","category","note"}`.
       - `"monthly_by_category"`: nested dict `year -> month -> {category: total}` for every month that has at least one event.

2. **Persistence (in-memory only)**  
   Store events in memory; no SQLite/files required for the core API. Tests run in one process.

3. **Tests** (`tests/test_ledger.py`)

   - Append valid events and assert monthly totals.
   - Invalid date / empty category raises `ValueError`.
   - `export_summary_json` round-trips structure (parse JSON and check keys).

## Commands (for your final answer)

- Run tests from **this workspace root**:  
  `python -m pytest tests/ -q`  
  (If `pytest` is not installed in the active env, say so in the final answer.)

## Scope

Stay inside this workspace. Do not modify repository files outside this directory during the stress run.
