import datetime
import json
from collections import defaultdict


class EventLedger:
    def __init__(self):
        self.events = []

    def append_event(self, iso_date: str, amount: float, category: str, note: str = "") -> None:
        try:
            datetime.datetime.strptime(iso_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid date format: {iso_date}")
        cat = category.strip()
        if not cat:
            raise ValueError("Category cannot be empty")
        self.events.append({
            "date": iso_date,
            "amount": amount,
            "category": cat,
            "note": note
        })

    def monthly_totals_by_category(self, year: int, month: int) -> dict[str, float]:
        totals = defaultdict(float)
        for event in self.events:
            dt = datetime.datetime.strptime(event["date"], "%Y-%m-%d")
            if dt.year == year and dt.month == month:
                totals[event["category"]] += event["amount"]
        return dict(sorted(totals.items()))

    def export_summary_json(self) -> str:
        monthly = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        for event in self.events:
            dt = datetime.datetime.strptime(event["date"], "%Y-%m-%d")
            y = dt.year
            m = dt.month
            c = event["category"]
            monthly[y][m][c] += event["amount"]
        monthly_by_category = {}
        for y in sorted(monthly.keys()):
            monthly_by_category[y] = {}
            for m in sorted(monthly[y].keys()):
                monthly_by_category[y][m] = dict(sorted(monthly[y][m].items()))
        summary = {
            "events": self.events,
            "monthly_by_category": monthly_by_category
        }
        return json.dumps(summary, indent=2)
