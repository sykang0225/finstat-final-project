"""Measure sector drawdown and recovery on non-overlapping KOSPI event windows."""

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import math
from pathlib import Path
from statistics import mean, median


def read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        out = csv.writer(f)
        out.writerow(fields)
        out.writerows(rows)


def choose(events, days):
    occupied, chosen, audit = set(), [], []
    loc = {d: i for i, d in enumerate(days)}
    for r in sorted(events, key=lambda x: (float(x["kospi_return_pct"]), x["event_date"])):
        day = r["event_date"]
        if r["status"] != "eligible":
            audit.append([day, r["kospi_return_pct"], r["status"], r["exclusion_reasons"]])
            continue
        window = days[loc[r["window_start"]]:loc[r["window_end"]] + 1]
        if len(window) != 12 or window[1] != day:
            raise ValueError(f"Expected T-1 through T+10: {day}")
        if occupied.intersection(window):
            audit.append([day, r["kospi_return_pct"], "overlap", "stronger event window already selected"])
            continue
        occupied.update(window)
        chosen.append((day, window))
        audit.append([day, r["kospi_return_pct"], "selected", ""])
    return sorted(chosen), sorted(audit)


def parse_observation(r):
    if r["conflict_fields"] or r["market"] not in {"KOSPI", "KOSDAQ"} or not r["sector"]:
        return None
    try:
        close = int(r["close_krw"])
        prior = int(r["previous_close_krw"]) if r["previous_close_krw"] else None
    except ValueError:
        return None
    if close <= 0 or (prior is not None and prior <= 0):
        return None
    return r["market"], r["sector"], close, prior


def calculate(event, window, records, min_members):
    initial = records.get(window[0], {})
    paths = defaultdict(list)
    counts = Counter()
    for code, (market, sector, close, _) in initial.items():
        counts[sector] += 1
        performance = []
        level = 1.0
        for day in window[1:]:
            observation = records.get(day, {}).get(code)
            if not observation or observation[:2] != (market, sector):
                break
            _, _, current, previous = observation
            # Exclude discontinuities instead of treating a changed base as a loss or gain.
            if previous != close:
                break
            daily = current / previous - 1
            if not math.isfinite(daily) or abs(daily) > 0.35:
                break
            level *= 1 + daily
            performance.append(level)
            close = current
        if len(performance) == 11:
            paths[sector].append(performance)

    output, quality = [], []
    for sector in sorted(counts):
        cohort = paths[sector]
        n = len(cohort)
        quality.append([event, sector, counts[sector], n, counts[sector] - n, n >= min_members])
        if n < min_members:
            continue
        series = [mean(levels) for levels in zip(*cohort)]
        drawdown = series[0] < 1 - 1e-10
        recovered = next((i for i, v in enumerate(series[1:], 1) if v >= 1 - 1e-10), None) if drawdown else None
        status = "no_initial_drop" if not drawdown else "recovered" if recovered is not None else "not_recovered"
        output.append([event, sector, n, 100*(series[0]-1), 100*(series[-1]-1),
                       recovered if recovered is not None else "", status])
    return output, quality


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", type=Path, required=True)
    p.add_argument("--screen", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--min-members", type=int, default=5)
    a = p.parse_args()
    if a.min_members < 1:
        p.error("--min-members must be positive")
    days = [r["trade_date"] for r in read(a.screen / "sessions.csv")]
    events, audit = choose(read(a.screen / "events.csv"), sorted(days))
    if not events:
        raise ValueError("No selected events")
    needed = {d for _, w in events for d in w}
    records = defaultdict(dict)
    with gzip.open(a.panel, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["trade_date"] in needed and (obs := parse_observation(r)):
                records[r["trade_date"]][r["code"]] = obs

    all_rows, quality, grouped = [], [], defaultdict(list)
    for event, window in events:
        rows, coverage = calculate(event, window, records, a.min_members)
        all_rows.extend(rows)
        quality.extend(coverage)
        for r in rows:
            grouped[r[1]].append(r)

    summaries = []
    for sector, rows in sorted(grouped.items()):
        drops = [r for r in rows if r[-1] != "no_initial_drop"]
        recovered = [r[5] for r in drops if r[-1] == "recovered"]
        summaries.append([sector, len(rows), len(drops), len(recovered),
                          mean(r[3] for r in rows), mean(r[4] for r in rows),
                          len(recovered)/len(drops) if drops else "",
                          median(recovered) if recovered else ""])

    a.out.mkdir(parents=True, exist_ok=False)
    write(a.out / "event_selection.csv", ["event_date", "kospi_return_pct", "status", "reason"], audit)
    write(a.out / "cohort_coverage.csv", ["event_date", "sector", "baseline_members",
                                          "retained_members", "excluded_members", "meets_min_members"], quality)
    write(a.out / "sector_events.csv", ["event_date", "sector", "n_members", "day0_pct",
                                       "day10_pct", "recovery_day", "status"], all_rows)
    write(a.out / "sector_summary.csv", ["sector", "n_events", "n_initial_drops", "n_recovered",
                                         "mean_day0_pct", "mean_day10_pct", "recovery_rate",
                                         "median_days_among_recovered"], summaries)
    counts = Counter(r[-1] for r in all_rows)
    lines = ["mode: measure_recovery", f"nonoverlapping_events: {len(events)}",
             f"overlap_exclusions: {sum(row[2] == 'overlap' for row in audit)}",
             f"sectors: {len(grouped)}", f"sector_event_rows: {len(all_rows)}",
             f"no_initial_drop: {counts['no_initial_drop']}",
             f"recovered: {counts['recovered']}", f"not_recovered: {counts['not_recovered']}"]
    bank = next((r for r in summaries if r[0] == "은행"), None)
    if bank:
        lines.append(f"bank: {bank[1]} events, {bank[2]} initial drops, {bank[3]} recovered, "
                     f"mean day0 {bank[4]:.3f}%, mean day10 {bank[5]:.3f}%")
    lines.append(f"output: {a.out}")
    summary = "\n".join(lines) + "\n"
    (a.out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, end="")


if __name__ == "__main__":
    main()
