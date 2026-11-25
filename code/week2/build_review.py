"""Make a standalone offline interview page from the school server's result ZIP."""

import argparse
from collections import Counter, defaultdict
import csv
from html import escape
import io
import json
import math
from pathlib import Path
from statistics import median, mean
from zipfile import ZipFile

from market_regimes import SECTORS


HERE = Path(__file__).resolve().parent


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def read(path):
    return rows(path.read_text(encoding="utf-8-sig"))


def number(row, key):
    return float(row[key]) if row.get(key) else None


def ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for i in order[start:end]:
            out[i] = (start + end - 1) / 2
        start = end
    return out


def spearman(pairs):
    x, y = map(ranks, zip(*pairs))
    cx, cy = mean(x), mean(y)
    numerator = sum((a-cx)*(b-cy) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a-cx)**2 for a in x) * sum((b-cy)**2 for b in y))
    return numerator/denominator if denominator else None


def summarize(daily, by_day):
    result = []
    for regime in ("하락", "중간", "상승"):
        subset = [r for r in daily if r["regime"] == regime]
        paired = [r for r in subset if r["cap_gap_pp"]]
        leaders = Counter(r["equal_leader"] for r in subset)
        cap_leaders = Counter(r["cap_leader"] for r in paired)
        same_leaders = Counter()
        paired_equal = []
        matches = 0
        for row in paired:
            base = {r["sector"]: float(r["cap_sample_equal_pct"]) for r in by_day[row["trade_date"]]}
            paired_equal.append(max(base.values())-min(base.values()))
            leader = max(sorted(base), key=base.get)
            same_leaders[leader] += 1
            matches += leader == row["cap_leader"]
        result.append(dict(regime=regime, n=len(subset), paired=len(paired),
            gap=median(float(r["equal_gap_pp"]) for r in subset) if subset else None,
            equal_paired=median(paired_equal) if paired else None,
            weighted=median(float(r["cap_gap_pp"]) for r in paired) if paired else None,
            same=matches,
            leaders=leaders.most_common(3), cap_leaders=cap_leaders.most_common(3),
            paired_leaders=same_leaders.most_common(3)))
    return result


def save_figure(summary, path):
    maximum = max([1, *(r[key] for r in summary for key in ("equal_paired", "weighted") if r[key] is not None)])
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="390" viewBox="0 0 900 390" role="img" aria-label="국면별 업종 수익률 격차">',
           '<rect width="900" height="390" fill="white"/>',
           '<g font-family="Malgun Gothic, Noto Sans CJK KR, sans-serif" fill="#17283c">',
           '<text x="27" y="43" font-size="25" font-weight="bold">시장 국면에 따른 업종 간 수익률 격차</text>',
           '<text x="27" y="73" font-size="15" fill="#52677d">12개 업종의 일별 최고–최저 차이 중앙값 · 시총이 확인된 동일 종목 표본</text>']
    for index, row in enumerate(summary):
        y = 111 + index*76
        svg.append(f'<text x="27" y="{y+25}" font-size="17">{escape(row["regime"])} · {row["paired"]}일</text>')
        for offset, key, name, color in ((0, "equal_paired", "동일가중", "#4276a8"),
                                         (25, "weighted", "시총가중", "#13796a")):
            value = row[key]
            width = 508*value/maximum if value is not None else 0
            label = f"{name} {value:.2f}%p" if value is not None else f"{name} · 자료 부족"
            svg.append(f'<rect x="141" y="{y+offset}" width="{width:.2f}" height="18" rx="4" fill="{color}"/>')
            svg.append(f'<text x="{151+width:.2f}" y="{y+offset+15}" font-size="14">{label}</text>')
    svg.append('<text x="27" y="369" font-size="14" fill="#52677d">자료: 교수님 네이버금융 HTML 아카이브 · 시총 기준: 직전 거래일</text></g></svg>')
    path.write_text("\n".join(svg), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--week1", type=Path, required=True, help="Folder containing original week-one CSVs")
    parser.add_argument("--out", type=Path, default=HERE/"week2_interview.html")
    parser.add_argument("--figure", type=Path, help="Optional standalone figure for the weekly submission")
    args = parser.parse_args()
    with ZipFile(args.results) as archive:
        def result(name):
            entries = [item for item in archive.namelist() if Path(item).name == name]
            if len(entries) != 1:
                raise ValueError(f"Expected one {name} in the school server ZIP")
            return rows(archive.read(entries[0]).decode("utf-8-sig"))
        market = result("market_daily.csv")
        daily = result("sector_daily.csv")
        audit, = result("audit.csv")
        sessions = result("sessions.csv")

    by_day = defaultdict(list)
    for row in daily:
        by_day[row["trade_date"]].append(row)
    acceptable = {r["trade_date"] for r in sessions if r["status"] == "ok"}
    session_dates = sorted(r["trade_date"] for r in sessions)
    prior = dict(zip(session_dates[1:], session_dates))
    excluded_days = sum(r["trade_date"] not in acceptable for r in market)
    market = [r for r in market if r["trade_date"] in acceptable]
    market_rows = {r["trade_date"]: r for r in market}
    selected = dict(sorted((r["event_date"], float(r["kospi_return_pct"]))
                           for r in read(args.week1/"event_selection.csv") if r["status"] == "selected"))
    context = {r["event_date"]: r for r in read(HERE/"event_context.csv")}
    if set(context) != set(selected):
        raise ValueError("News source dates differ from selected event dates")
    events = []
    for row in read(args.week1/"sector_events.csv"):
        sector, day = row["sector"], row["event_date"]
        if sector not in SECTORS:
            continue
        fundamental = next((r for r in by_day[day] if r["sector"] == sector), {})
        start, end = float(row["day0_pct"]), float(row["day10_pct"])
        cap = number(fundamental, "observed_cap_krw")
        events.append(dict(sector=sector, day=day, n=int(row["n_members"]), start=start, end=end,
            rebound=100*((1+end/100)/(1+start/100)-1), status=row["status"],
            recovered=row["recovery_day"], cap=cap/1e12 if cap is not None else None,
            cap_date=prior.get(day), cap_n=fundamental.get("cap_members"), cap_total=fundamental.get("members")))
    if len(events) != len(selected)*len(SECTORS):
        raise ValueError("Fixed-sector event coverage does not match expected counts")
    event_summary = {}
    for day in selected:
        dropped = [r for r in events if r["day"] == day and r["status"] != "no_initial_drop"]
        event_summary[day] = dict(dropped=len(dropped),
            recovered=sum(r["status"] == "recovered" for r in dropped),
            rho=spearman([(r["start"], r["rebound"]) for r in dropped]) if len(dropped) >= 3 else None)

    debt = []
    for day, group in by_day.items():
        if day not in market_rows:
            continue
        market_row = market_rows[day]
        bucket = "급락" if float(market_row["kospi_pct"]) <= -3.8638 else market_row["regime"]
        if bucket not in {"급락", "중간"}:
            continue
        pairs = [(float(r["median_debt_pct"]), float(r["equal_pct"])) for r in group
                 if r["sector"] != "은행" and r["median_debt_pct"]]
        if len(pairs) == len(SECTORS)-1 and (rho := spearman(pairs)) is not None:
            debt.append(dict(day=day, regime=bucket, rho=rho))
    years = sorted({r["trade_date"][:4] for r in market})
    summaries = {year: summarize([r for r in market if r["trade_date"].startswith(year)], by_day)
                 for year in years}
    payload = dict(events=events, context=context, selected=selected, event_summary=event_summary, yearly=summaries,
                   summary=summarize(market, by_day), debt=debt,
                   cutoff=[float(audit["lower_cutoff_pct"]),float(audit["upper_cutoff_pct"])],
                   audit=audit, excluded_days=excluded_days,
                   sectors=SECTORS, dates=[market[0]["trade_date"],market[-1]["trade_date"]])
    template = (HERE/"review_template.html").read_text(encoding="utf-8")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    args.out.write_text(template.replace("/* DATA */", encoded), encoding="utf-8")
    if args.figure:
        save_figure(payload["summary"], args.figure)
    print("common days", len(market), "sectors", len(SECTORS), "event rows", len(events))
    print("regime summary", payload["summary"])
    print("debt correlations", {key: (len(v), median(v)) for key in ("급락","중간")
          if (v := [r["rho"] for r in debt if r["regime"] == key])})
    print("audit", audit)


if __name__ == "__main__":
    main()
