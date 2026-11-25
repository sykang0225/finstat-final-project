"""Read the course HTML archive in place. Python 3.9+, no dependencies."""

import argparse
import csv
import gzip
from collections import Counter
from datetime import date
from html import escape, unescape
from pathlib import Path
import random
import re
import sys


FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}")
STOCK_FILE = re.compile(r"(?:.+_)?([0-9A-Z]{6})\.html", re.I)
SCRIPTS = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
TAGS = re.compile(r"<[^>]*>")
TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S)
HEADER = re.compile(
    r"(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일"
    r"\s*(?P<hour>\d{1,2})\s*시\s*(?P<minute>\d{1,2})\s*분\s*기준\s*"
    r"(?P<status>.{0,45}?)\s*종목명\s*(?P<name>.{1,100}?)\s*종목코드\s*"
    r"(?P<code>[0-9A-Z]{6})\s*(?P<market>.{0,45}?)\s*현재가\s*"
    r"(?P<close>\d[\d,]*)",
    re.I,
)
PREVIOUS = re.compile(r"전일가\s*(\d[\d,]*)")
SECTOR = re.compile(r"업종명\s*[:：]\s*([^|｜()]{1,100}?)\s*(?=[|｜)]|재무정보)")
FIELDS = (
    "archive_date", "trade_date", "snapshot_time_kst", "code", "name", "market",
    "sector", "close_krw", "previous_close_krw", "reported_base_return_pct",
)
STATS = (
    "archive_date", "all_html", "stock_named_html", "attempted", "read_ok",
    "read_error", "decode_error", "header_missing", "code_mismatch", "not_closed",
    "invalid_date", "future_trade_date", "invalid_close", "valid_close",
    "missing_sector", "missing_previous_close", "usable_market_sector_return",
)


def normalize(html):
    return " ".join(unescape(TAGS.sub(" ", html)).split())


def parse(raw, archive_date, code):
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            page = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None, "decode_error"
    body = normalize(SCRIPTS.sub(" ", page))
    header = HEADER.search(body[:20000])
    if header is None:
        return None, "header_missing"
    if header["code"].upper() != code:
        return None, "code_mismatch"
    if re.sub(r"\s+", "", header["status"]) != "장마감":
        return None, "not_closed"
    try:
        traded = date(*(int(header[key]) for key in ("year", "month", "day"))).isoformat()
        hour, minute = int(header["hour"]), int(header["minute"])
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("invalid clock")
    except ValueError:
        return None, "invalid_date"
    if traded > archive_date:
        return None, "future_trade_date"
    price = int(header["close"].replace(",", ""))
    if not price:
        return None, "invalid_close"
    prior = PREVIOUS.search(body[header.end():header.end() + 250])
    prev = int(prior[1].replace(",", "")) if prior else 0
    market_label = re.sub(r"\s+", "", header["market"]).upper()
    market = {
        "코스피": "KOSPI", "KOSPI": "KOSPI",
        "코스닥": "KOSDAQ", "KOSDAQ": "KOSDAQ", "코넥스": "KONEX", "KONEX": "KONEX",
    }.get(market_label, market_label)
    sector_match = SECTOR.search(body)
    sector = sector_match[1].strip() if sector_match else ""
    if sector in ("-", "N/A", "없음"):
        sector = ""
    return {
        "archive_date": archive_date, "trade_date": traded,
        "snapshot_time_kst": f"{hour:02d}:{minute:02d}", "code": code,
        "name": header["name"], "market": market, "sector": sector,
        "close_krw": price, "previous_close_krw": prev or "",
        "reported_base_return_pct": f"{100 * (price / prev - 1):.6f}" if prev else "",
    }, None


def coverage_plot(rows, path):
    width, height = 960, 450
    x0, x1, y0, y1 = 83, 920, 95, 365
    peak = max(1, max(r["all_html"] for r in rows))
    step = max(1, (peak + 4) // 5)
    scale = step * 5
    start = date.fromisoformat(rows[0]["archive_date"])
    stop = date.fromisoformat(rows[-1]["archive_date"])
    span = max(1, (stop - start).days)
    xpos = lambda r: x0 + (x1 - x0) * (date.fromisoformat(r["archive_date"]) - start).days / span
    ypos = lambda n: y1 - (y1 - y0) * n / scale
    def label(x, y, content, size=13, anchor="start", color="#334155"):
        return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{escape(str(content))}</text>'

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="날짜별 자료 범위">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="Noto Sans CJK KR, Malgun Gothic, Apple SD Gothic Neo, sans-serif">',
           label(x0, 40, "교수 아카이브: 날짜별 자료 범위", 25, color="#0f172a")]
    for n in range(0, scale + 1, step):
        y = ypos(n)
        svg += [f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e2e8f0"/>',
                label(x0 - 10, y + 4, f"{n:,}", anchor="end")]
    for key, color, legend, lx in [
        ("all_html", "#64748b", "발견한 HTML", 83),
        ("valid_close", "#2563eb", "종가 추출 성공", 257),
        ("usable_market_sector_return", "#0f766e", "시장·업종·수익률 확인", 455),
    ]:
        points = " ".join(f"{xpos(r):.2f},{ypos(r[key]):.2f}" for r in rows)
        svg += [f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>',
                f'<circle cx="{lx + 4}" cy="69" r="4" fill="{color}"/>', label(lx + 16, 73, legend)]
    for idx in sorted({i * (len(rows) - 1) // 5 for i in range(6)}):
        r = rows[idx]
        svg.append(label(xpos(r), y1 + 27, r["archive_date"], anchor="middle"))
    svg += [label(x0, 427, "가로축은 보관 폴더 날짜입니다. 같은 거래일의 재수집은 날짜별 집계에 각각 포함됩니다."),
            "</g></svg>"]
    path.write_text("\n".join(svg), encoding="utf-8")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", type=Path, default=Path("/mnt/data/stock"))
    cli.add_argument("--out", type=Path, required=True, help="new output directory")
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true")
    mode.add_argument("--full", action="store_true")
    args = cli.parse_args()
    source, out = args.source.resolve(), args.out.expanduser().resolve()
    if source == out or source in out.parents:
        cli.error("output cannot be inside the source archive")
    folders = sorted(p for p in source.iterdir() if FOLDER.fullmatch(p.name) and p.is_dir())
    if not folders:
        cli.error("no dated archive folders found")
    if args.pilot:
        folders = [p for i, p in enumerate(folders) if
                   i in {j * (len(folders) - 1) // 8 for j in range(9)}
                   or p.name in {"2025-06-06", "2026-09-02"}]
    out.mkdir(parents=True, exist_ok=False)
    totals, daily, examples = Counter(), [], Counter()
    tickers, traded_days = set(), set()
    with gzip.open(out / "observations.csv.gz", "wt", encoding="utf-8", newline="") as data, \
         (out / "failure_examples.csv").open("w", encoding="utf-8", newline="") as errors:
        writer = csv.DictWriter(data, FIELDS)
        writer.writeheader()
        ew = csv.writer(errors)
        ew.writerow(["archive_date", "code", "reason"])
        for i, folder in enumerate(folders, 1):
            paths = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".html" and p.is_file())
            stocks = [p for p in paths if STOCK_FILE.fullmatch(p.name)]
            c = Counter(all_html=len(paths), stock_named_html=len(stocks))
            if args.pilot:
                fixed = [p for p in stocks if p.stem.rsplit("_", 1)[-1] in {"105560", "005930"}]
                other = [p for p in stocks if p not in fixed]
                stocks = fixed + random.Random(folder.name).sample(other, min(24, len(other)))
            for p in stocks:
                c["attempted"] += 1
                code = p.stem.rsplit("_", 1)[-1].upper()
                try:
                    raw = p.read_bytes()
                except OSError:
                    row, reason = None, "read_error"
                else:
                    c["read_ok"] += 1
                    row, reason = parse(raw, folder.name, code)
                if reason:
                    c[reason] += 1
                    if examples[reason] < 30:
                        ew.writerow([folder.name, code, reason])
                        examples[reason] += 1
                    continue
                writer.writerow(row)
                tickers.add(code)
                traded_days.add(row["trade_date"])
                c["valid_close"] += 1
                c["missing_sector"] += not row["sector"]
                c["missing_previous_close"] += not row["previous_close_krw"]
                c["usable_market_sector_return"] += bool(
                    row["sector"] and row["previous_close_krw"] and row["market"] in {"KOSPI", "KOSDAQ"})
            daily.append({"archive_date": folder.name, **{k: c[k] for k in STATS[1:]}})
            totals.update(c)
            if args.pilot or i % 10 == 0 or i == len(folders):
                print(f"[{i}/{len(folders)}] {folder.name} HTML {c['all_html']:,} | "
                      f"attempt {c['attempted']:,} | close {c['valid_close']:,} | "
                      f"sector+return {c['usable_market_sector_return']:,}", flush=True)
    with (out / "coverage.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, STATS)
        writer.writeheader()
        writer.writerows(daily)
    if args.full:
        coverage_plot(daily, out / "coverage.svg")
    lines = [f"mode: {'pilot' if args.pilot else 'full'}", f"source: {source}",
             f"archive_range: {folders[0].name} ~ {folders[-1].name}",
             f"date_folders: {len(folders):,}", f"distinct_close_tickers: {len(tickers):,}",
             f"distinct_trade_days: {len(traded_days):,}"]
    lines.extend(f"{key}: {totals[key]:,}" for key in STATS[1:])
    lines.append("Rows are archive observations; duplicate trade_date/code pairs can remain.")
    summary = "\n".join(lines) + "\n"
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary + f"output: {out}", flush=True)
    if not totals["valid_close"]:
        print("No matching close rows. Inspect failure_examples.csv before a full run.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
