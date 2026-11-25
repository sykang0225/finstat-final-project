"""Enrich the existing daily trade panel from server HTML, with daily checkpoints."""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
from itertools import groupby
import json
from pathlib import Path
import re
import sqlite3

from archive_extract import HEADER, STOCK_FILE, normalize


BLIND = re.compile(r'<dl\b[^>]*class=["\']blind["\'][^>]*>(.*?)</dl>', re.I | re.S)
DD = re.compile(r"<dd\b[^>]*>(.*?)</dd>", re.I | re.S)
CAP = re.compile(r'<em\b[^>]*id=["\']_market_sum["\'][^>]*>(.*?)</em>', re.I | re.S)
PBR = re.compile(r'<em\b[^>]*id=["\']_pbr["\'][^>]*>(.*?)</em>', re.I | re.S)
PERIOD = re.compile(r"(20\d\d)\.(0[1-9]|1[0-2])(?:\s*\(\s*(E)\s*\))?", re.I)
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)
METRICS = {
    "매출액": "revenue", "영업이익": "operating_profit", "영업이익률": "operating_margin",
    "ROE(지배주주)": "roe", "ROE": "roe", "부채비율": "debt_ratio",
    "EPS(원)": "eps", "BPS(원)": "bps", "주당배당금(원)": "dividend_per_share",
    "PBR(배)": "pbr",
}


def number(value):
    clean = re.sub(r"[\s,%]", "", value).replace("−", "-")
    return clean if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", clean) else None


def cap_won(value):
    value = re.sub(r"[\s,]", "", value)
    units = re.fullmatch(r"(?:(\d+)조)?(\d+)?억?", value)
    if units and any(units.groups()):
        return (int(units[1] or 0)*10_000 + int(units[2] or 0))*100_000_000 or None
    return None


def financial_table(page):
    marker = page.find("최근 분기 실적")
    if marker < 0:
        return []
    end = page.find("</table>", marker)
    if end < 0:
        return []
    section = page[marker:end]
    rows = [[normalize(cell) for cell in CELL.findall(row)] for row in ROW.findall(section)]
    periods = next(([PERIOD.fullmatch(cell) for cell in row] for row in rows
                    if row and all(PERIOD.fullmatch(cell) for cell in row)), [])
    if len(periods) <= 4:
        return []
    records = [{"frequency": "annual" if i < 4 else "quarterly",
                "period": f"{m[1]}-{m[2]}", "estimated": bool(m[3])}
               for i, m in enumerate(periods)]
    for row in rows:
        if len(row) != len(periods) + 1:
            continue
        metric = METRICS.get(re.sub(r"\s+", "", row[0]))
        if metric:
            for record, value in zip(records, row[1:]):
                record[metric] = number(value)
    return [r for r in records if any(r.get(m) is not None for m in METRICS.values())]


def fetch(path, trade_date, code):
    raw = path.read_bytes()
    try:
        page = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        page = raw.decode("cp949")
    header = None
    items = []
    for block in BLIND.finditer(page):
        items = [normalize(cell) for cell in DD.findall(block[1])]
        header = HEADER.search(" ".join(items))
        if header:
            break
    if not header:
        return "header_missing", None, None, None, []
    actual = "-".join(header[k].zfill(2) for k in ("year", "month", "day"))
    if actual != trade_date or header["code"].upper() != code or "".join(header["status"].split()) != "장마감":
        return "header_mismatch", None, None, None, []
    volume = next((number(s.removeprefix("거래량")) for s in items if s.startswith("거래량")), None)
    if volume is not None and not re.fullmatch(r"\d+", volume):
        volume = None
    cap, pbr = CAP.search(page), PBR.search(page)
    return ("ok", int(volume) if volume is not None else None,
            cap_won(normalize(cap[1])) if cap else None,
            number(normalize(pbr[1])) if pbr else None, financial_table(page))


def export_changes(db, out):
    columns = ("code", "frequency", "period", "previous_observed_date", "first_actual_observed_date",
               "estimate_observed_date", "operating_profit_estimate", "operating_profit_actual",
               "previous_archive_date", "first_actual_archive_date")
    with (out/"earnings_transitions.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        query = "SELECT code, trade_date, archive_date, payload FROM finance ORDER BY code, trade_date"
        for code, versions in groupby(db.execute(query), key=lambda r: r[0]):
            seen_actual, estimates = set(), {}
            previous_date = previous_archive = None
            for _, day, archive, payload in versions:
                for record in json.loads(payload):
                    key = record["frequency"], record["period"]
                    value = record.get("operating_profit")
                    if value is None or key in seen_actual:
                        continue
                    if record["estimated"]:
                        estimates[key] = (day, value)
                    else:
                        seen_actual.add(key)
                        if key in estimates:
                            estimate_day, expected = estimates[key]
                            writer.writerow((code, *key, previous_date, day, estimate_day, expected,
                                             value, previous_archive, archive))
                previous_date, previous_archive = day, archive


def run(args):
    args.out.mkdir(parents=True, exist_ok=True)
    prices = args.out/"daily"
    prices.mkdir(exist_ok=True)
    with sqlite3.connect(args.out/"financials.sqlite") as db:
        db.executescript("""
          CREATE TABLE IF NOT EXISTS finance(
            code TEXT, trade_date TEXT, archive_date TEXT, payload TEXT,
            PRIMARY KEY(code, trade_date));
          CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, audit TEXT);
        """)
        done = dict(db.execute("SELECT day, audit FROM days"))
        last = {}
        for code, payload in db.execute("SELECT code, payload FROM finance ORDER BY trade_date"):
            last[code] = hashlib.sha256(payload.encode()).digest()
        with gzip.open(args.panel, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = [*reader.fieldnames, "volume_shares", "market_cap_krw", "pbr", "parse_status"]
            for day, group in groupby(reader, key=lambda r: r["trade_date"]):
                if day < args.start or day in done:
                    continue
                if day > args.end:
                    break
                counts, folders = Counter(), {}
                target = prices/f"{day}.csv.gz"
                pending = prices/f".{day}.tmp"
                with db, gzip.open(pending, "wt", encoding="utf-8", newline="") as file:
                    writer = csv.DictWriter(file, fields)
                    writer.writeheader()
                    for row in group:
                        code, archive = row["code"], row["archive_date"]
                        folder = args.source/archive
                        path = folder/f"{code}.html"
                        if archive not in folders:
                            named = {}
                            for candidate in sorted(folder.glob("*_*.html")):
                                match = STOCK_FILE.fullmatch(candidate.name)
                                if match:
                                    named.setdefault(match[1].upper(), candidate)
                            folders[archive] = named
                        named_path = folders[archive].get(code)
                        if not path.is_file() and named_path:
                            path = named_path
                        elif named_path and path.is_file():
                            counts["alternate_filename"] += 1
                        try:
                            status, volume, cap, pbr, financial = fetch(path, day, code)
                            if status != "ok" and named_path and named_path != path:
                                status, volume, cap, pbr, financial = fetch(named_path, day, code)
                        except (OSError, UnicodeError) as error:
                            status, volume, cap, pbr, financial = type(error).__name__, None, None, None, []
                        counts["rows"] += 1
                        counts[status] += 1
                        counts["volume"] += volume is not None
                        counts["cap"] += cap is not None
                        counts["pbr"] += pbr is not None
                        counts["financial_table"] += bool(financial)
                        if financial:
                            payload = json.dumps(financial, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                            digest = hashlib.sha256(payload.encode()).digest()
                            if last.get(code) != digest:
                                db.execute("INSERT INTO finance VALUES (?,?,?,?)", (code, day, archive, payload))
                                last[code] = digest
                                counts["financial_versions"] += 1
                        writer.writerow(dict(row, volume_shares=volume, market_cap_krw=cap, pbr=pbr,
                                             parse_status=status))
                    done[day] = json.dumps(counts, sort_keys=True)
                    db.execute("INSERT INTO days VALUES (?,?)", (day, done[day]))
                    file.close()
                    pending.replace(target)
                if len(done) % 10 == 0 or len(done) <= 5:
                    print(day, len(done), dict(counts), flush=True)
        audit = [{"trade_date": day, **json.loads(values)} for day, values in sorted(done.items())]
        if not audit:
            raise ValueError("No panel days in selected range")
        keys = sorted({key for row in audit for key in row} - {"trade_date"})
        with (args.out/"coverage.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, ["trade_date", *keys], restval=0)
            writer.writeheader()
            writer.writerows(audit)
        export_changes(db, args.out)
        totals = Counter()
        for counts in done.values():
            totals.update(json.loads(counts))
        print("COMPLETE", len(done), dict(totals), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("/mnt/data/stock"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start", default="0000")
    parser.add_argument("--end", default="9999")
    run(parser.parse_args())
