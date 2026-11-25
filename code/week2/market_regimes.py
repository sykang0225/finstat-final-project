"""Compare Korean equity sectors across KOSPI regimes using archived observations."""

import argparse
from collections import Counter, defaultdict
import csv
import gzip
from itertools import groupby
import math
from pathlib import Path
import re
from statistics import mean, median, quantiles

from archive_extract import STOCK_FILE, HEADER, SCRIPTS, normalize


SECTORS = (
    "반도체와반도체장비", "IT서비스", "자동차", "자동차부품", "조선", "화학",
    "철강", "건설", "제약", "식품", "가스유틸리티", "은행",
)
CAP = re.compile(r"""<em\b[^>]*\bid\s*=\s*["']_market_sum["'][^>]*>(.*?)</em\s*>\s*(?:억\s*)?원""", re.I | re.S)
DEBT = re.compile(r"<th\b[^>]*>\s*(?:<strong>\s*)?부채비율\s*(?:</strong>\s*)?</th>(.*?)</tr>", re.I | re.S)
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
CELL = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.I | re.S)
PERIOD = re.compile(r"(20\d\d)\.(0[1-9]|1[0-2])(\s*\(\s*E\s*\))?", re.I)


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_page(path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return ""


def fundamentals(page, trade_date, code):
    header = HEADER.search(normalize(SCRIPTS.sub(" ", page[:100000]))[:20000])
    if not header or header["code"].upper() != code or "".join(header["status"].split()) != "장마감":
        return None, None, None
    actual = "-".join(header[key].zfill(2) for key in ("year", "month", "day"))
    if actual != trade_date:
        return None, None, None
    cap_match = CAP.search(page)
    cap = None
    if cap_match:
        value = re.sub(r"[\s,]", "", normalize(cap_match[1]))
        units = re.fullmatch(r"(?:(\d+)조)?(\d+)?억?", value)
        if units and any(units.groups()):
            cap = (int(units[1] or 0)*10000 + int(units[2] or 0))*100_000_000 or None
    debt_match = DEBT.search(page)
    if debt_match:
        cells = CELL.findall(debt_match[1])
        prefix = page[max(0, debt_match.start()-30000):debt_match.start()]
        for row in reversed(ROW.findall(prefix)):
            dates = [PERIOD.fullmatch(normalize(cell)) for cell in CELL.findall(row)]
            dates = [item for item in dates if item]
            if len(dates) != len(cells) or len(dates) <= 4:
                continue
            for value, period in reversed(list(zip(cells[4:], dates[4:]))):
                month = f"{period[1]}-{period[2]}"
                clean = normalize(value).replace(",", "").rstrip("%").strip()
                if not period[3] and month < trade_date[:7] and re.fullmatch(r"\d+(?:\.\d+)?", clean):
                    return cap, float(clean), month
            break
    return cap, None, None


def load_fundamentals(rows, source, day, audit):
    lookup = None
    result = {}
    for code, row in rows.items():
        if row["archive_date"] != day:
            audit["archive_date_mismatch"] += 1
            continue
        folder = source / day
        path = folder / f"{code}.html"
        if not path.is_file():
            if lookup is None:
                lookup = {m[1].upper(): f for f in folder.glob("*.html") if (m := STOCK_FILE.fullmatch(f.name))}
            path = lookup.get(code)
        if path is None:
            audit["file_missing"] += 1
            continue
        try:
            cap, debt, period = fundamentals(read_page(path), day, code)
        except OSError:
            audit["read_error"] += 1
            continue
        audit["cap_found" if cap else "cap_missing"] += 1
        audit["debt_found" if debt is not None else "debt_missing"] += 1
        result[code] = cap, debt, period
    return result


def panel_days(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        groups = groupby(csv.DictReader(file), key=lambda row: row["trade_date"])
        for day, group in groups:
            rows = {r["code"]: r for r in group if r["sector"] in SECTORS
                    and r["market"] in {"KOSPI", "KOSDAQ"} and not r["conflict_fields"]}
            yield day, rows


def run(args):
    benchmark = read(args.benchmark)
    closes = {r["trade_date"]: float(r["close"]) for r in benchmark}
    dates = sorted(closes)
    prior_days = dict(zip(dates[1:], dates))
    returns = {d: 100*(closes[d]/closes[prior_days[d]]-1) for d in dates[1:]}
    low, _, _, high = quantiles(
        [r for d, r in returns.items() if "2024-03-27" <= d <= "2026-09-02"],
        n=5, method="inclusive",
    )
    picked = {r["event_date"] for r in read(args.events) if r["status"] == "selected"}
    audit = Counter()
    prior_date, prior, prior_fundamentals = None, {}, {}
    sector_daily, market_daily = [], []
    progress_month = None
    for day, rows in panel_days(args.panel):
        if day < prior_days.get(args.start, args.start):
            continue
        if day > args.end:
            break
        groups = defaultdict(list)
        if day >= args.start and prior_days.get(day) == prior_date:
            for code, row in rows.items():
                old = prior.get(code)
                if not old or (old["sector"], old["market"]) != (row["sector"], row["market"]):
                    continue
                if row["previous_close_krw"] != old["close_krw"]:
                    continue
                value = 100*(int(row["close_krw"])/int(old["close_krw"])-1)
                if abs(value) <= 35:
                    cap, debt, _ = prior_fundamentals.get(code, (None, None, None))
                    groups[row["sector"]].append((value, cap, debt))
            market = returns[day]
            regime = "하락" if market <= low else "상승" if market >= high else "중간"
            daily = []
            for sector, values in sorted(groups.items()):
                if len(values) < 5:
                    continue
                cap_sample = [(r, c) for r, c, _ in values if c]
                debts = [d for _, _, d in values if d is not None]
                cap_total = sum(c for _, c in cap_sample)
                coverage = len(cap_sample)/len(values)
                row = dict(trade_date=day, regime=regime, sector=sector, members=len(values),
                           equal_pct=mean(r for r, _, _ in values),
                           cap_sample_equal_pct=mean(r for r, _ in cap_sample) if cap_sample else None,
                           cap_weighted_pct=sum(r*c for r, c in cap_sample)/cap_total if coverage >= .8 else None,
                           observed_cap_krw=cap_total or None, cap_members=len(cap_sample),
                           debt_members=len(debts), median_debt_pct=median(debts) if len(debts) >= 5 and len(debts)/len(values) >= .8 else None)
                daily.append(row)
            sector_daily.extend(daily)
            if len(daily) == len(SECTORS):
                equal = [r["equal_pct"] for r in daily]
                weighted = [r["cap_weighted_pct"] for r in daily]
                market_daily.append(dict(trade_date=day, regime=regime, kospi_pct=market,
                    equal_gap_pp=max(equal)-min(equal), equal_leader=max(daily,key=lambda r:r["equal_pct"])["sector"],
                    cap_gap_pp=max(weighted)-min(weighted) if None not in weighted else None,
                    cap_leader=max(daily,key=lambda r:r["cap_weighted_pct"])["sector"] if None not in weighted else None))
        prior_date, prior = day, rows
        prior_fundamentals = load_fundamentals(rows, args.archive, day, audit) if args.archive else {}
        if day[:7] != progress_month:
            print(day, "sector-day rows:", len(sector_daily), flush=True)
            progress_month = day[:7]
    args.out.mkdir(parents=True, exist_ok=False)
    write(args.out/"sector_daily.csv", sector_daily)
    write(args.out/"market_daily.csv", market_daily)
    write(args.out/"event_fundamentals.csv", [r for r in sector_daily if r["trade_date"] in picked])
    write(args.out/"audit.csv", [dict(lower_cutoff_pct=low, upper_cutoff_pct=high, **audit)])
    print(f"Complete: {len(market_daily)} common dates, {len(sector_daily)} sector-days", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start", default="2024-03-27")
    parser.add_argument("--end", default="2026-09-02")
    run(parser.parse_args())
