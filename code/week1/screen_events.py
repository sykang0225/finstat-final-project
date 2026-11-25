"""Find KOSPI shock dates and audit whether the archive covers each event window."""

import argparse
import ast
from collections import Counter
import csv
from datetime import date
import gzip
import math
from pathlib import Path
from statistics import median
from urllib.parse import urlencode
from urllib.request import urlopen


START, END = "2024-03-27", "2026-09-02"
URL = "https://api.finance.naver.com/siseJson.naver?" + urlencode({
    "symbol": "KOSPI", "requestType": 1, "startTime": "20240325",
    "endTime": END.replace("-", ""), "timeframe": "day",
})


def download_benchmark():
    with urlopen(URL, timeout=30) as response:
        raw = response.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    rows = ast.literal_eval(text.strip())
    if rows[0][:5] != ["날짜", "시가", "고가", "저가", "종가"]:
        raise ValueError("Unexpected benchmark columns")
    result = {}
    for day, _, _, _, close, *_ in rows[1:]:
        day = str(day).strip()
        key = date(int(day[:4]), int(day[4:6]), int(day[6:8])).isoformat()
        if key in result or not math.isfinite(float(close)) or close <= 0:
            raise ValueError(f"Invalid benchmark value for {key}")
        result[key] = close
    return result


def screen(benchmark, total, usable, min_coverage, horizon):
    days = sorted(benchmark)
    if not days or days[0] >= START or days[-1] != END:
        raise ValueError("Benchmark needs a session before the study start and the requested end")
    returns = {d: 100 * (benchmark[d] / benchmark[days[i - 1]] - 1)
               for i, d in enumerate(days) if i and START <= d <= END}
    values = sorted(returns.values())
    cutoff = values[math.ceil(0.05 * len(values)) - 1]
    quality = {}
    for i, day in enumerate(days):
        if day not in returns:
            continue
        prior = [d for d in days[max(0, i - 20):i] if total[d] and usable[d]]
        expected_total = median(total[d] for d in prior) if len(prior) >= 5 else None
        expected_usable = median(usable[d] for d in prior) if len(prior) >= 5 else None
        if not total[day]:
            status = "missing"
        elif expected_total is None:
            status = "baseline_short"
        elif total[day] < min_coverage * expected_total or usable[day] < min_coverage * expected_usable:
            status = "low_coverage"
        else:
            status = "ok"
        quality[day] = (expected_total, expected_usable, status)

    events = []
    for i, day in enumerate(days):
        if day not in returns or returns[day] > cutoff or returns[day] >= 0:
            continue
        window = days[i - 1:i + horizon + 1]
        problems = [f"{d}:{quality.get(d, (None, None, 'out_of_scope'))[2]}"
                    for d in window if quality.get(d, (None, None, "out_of_scope"))[2] != "ok"]
        if len(window) != horizon + 2:
            problems.append("short_future_window")
        events.append([day, f"{returns[day]:.6f}", window[0], window[-1],
                       "excluded" if problems else "eligible", "|".join(problems)])
    return returns, cutoff, quality, events


def write(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--min-coverage", type=float, default=0.8)
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--benchmark", type=Path, help="optional saved kospi.csv")
    a = p.parse_args()
    if not 0 < a.min_coverage <= 1 or a.horizon < 1:
        p.error("Invalid coverage or horizon")
    if a.benchmark:
        with a.benchmark.open(encoding="utf-8-sig", newline="") as file:
            benchmark = {r["trade_date"]: float(r["close"]) for r in csv.DictReader(file)}
    else:
        benchmark = download_benchmark()
    total, usable = Counter(), Counter()
    with gzip.open(a.panel, "rt", encoding="utf-8", newline="") as file:
        for r in csv.DictReader(file):
            day = r["trade_date"]
            total[day] += 1
            usable[day] += bool(not r["conflict_fields"] and r["market"] in {"KOSPI", "KOSDAQ"}
                                and r["sector"] and r["previous_close_krw"])
    returns, cutoff, quality, events = screen(benchmark, total, usable, a.min_coverage, a.horizon)
    out = a.out.expanduser()
    out.mkdir(parents=True, exist_ok=False)
    write(out / "kospi.csv", ["trade_date", "close"], sorted(benchmark.items()))
    write(out / "sessions.csv", ["trade_date", "kospi_return_pct", "panel_rows", "classified_return_rows",
                                  "prior_median_panel_rows", "prior_median_classified_rows", "status"],
          ([d, f"{r:.6f}", total[d], usable[d], *quality[d]] for d, r in returns.items()))
    write(out / "events.csv", ["event_date", "kospi_return_pct", "window_start", "window_end",
                               "status", "exclusion_reasons"], events)
    counts = Counter(v[2] for v in quality.values())
    summary = "\n".join([
        "mode: screen_events",
        f"benchmark_sessions: {len(returns)}",
        f"archive_missing_sessions: {counts['missing']}",
        f"archive_low_coverage_sessions: {counts['low_coverage']}",
        f"archive_baseline_short_sessions: {counts['baseline_short']}",
        f"archive_dates_not_in_benchmark: {len(set(total) - set(benchmark))}",
        f"kospi_5pct_cutoff: {cutoff:.4f}%",
        f"candidate_events: {len(events)}",
        f"eligible_events: {sum(row[4] == 'eligible' for row in events)}",
        f"output: {out}",
    ]) + "\n"
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, end="")


if __name__ == "__main__":
    main()
