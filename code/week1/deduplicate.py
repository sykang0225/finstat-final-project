"""Build a trade-date/ticker panel from compressed archive observations."""

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
import gzip
from pathlib import Path
from tempfile import TemporaryDirectory


COMPARE = ("close_krw", "previous_close_krw", "market", "sector")
META = ("snapshot_count", "conflict_fields")


def rank(row):
    hour, minute = map(int, row["snapshot_time_kst"].split(":"))
    return row["archive_date"], -(hour * 60 + minute)


def build(source, out):
    out.mkdir(parents=True, exist_ok=False)
    totals = Counter()
    trade_days, tickers = set(), set()
    with TemporaryDirectory(dir=out) as temp:
        parts = Path(temp)
        with ExitStack() as stack:
            inp = stack.enter_context(gzip.open(source, "rt", encoding="utf-8", newline=""))
            reader = csv.DictReader(inp)
            columns = reader.fieldnames or []
            required = {"trade_date", "archive_date", "code", "snapshot_time_kst", *COMPARE}
            if not required.issubset(columns):
                raise ValueError("Missing expected columns in the input")
            partitions = {}
            for row in reader:
                month = row["trade_date"][:7]
                if month not in partitions:
                    file = stack.enter_context(gzip.open(parts / f"{month}.gz", "wt", encoding="utf-8", newline=""))
                    partitions[month] = csv.DictWriter(file, columns)
                    partitions[month].writeheader()
                partitions[month].writerow(row)
                totals["input_rows"] += 1

        with gzip.open(out / "panel.csv.gz", "wt", encoding="utf-8", newline="") as panel, \
             (out / "conflicts.csv").open("w", encoding="utf-8", newline="") as conflicts:
            writer = csv.DictWriter(panel, [*columns, *META])
            writer.writeheader()
            audit = csv.writer(conflicts)
            audit.writerow(["trade_date", "code", "field", "observed_values", "chosen_archive_date", "chosen_value"])
            for part in sorted(parts.glob("*.gz")):
                groups = {}
                with gzip.open(part, "rt", encoding="utf-8", newline="") as file:
                    for row in csv.DictReader(file):
                        groups.setdefault((row["trade_date"], row["code"]), []).append(row)
                for (trade_date, code), rows in sorted(groups.items()):
                    chosen = min(rows, key=rank)
                    flags = []
                    for field in COMPARE:
                        values = sorted({row[field] for row in rows if row[field]})
                        if len(values) > 1:
                            flags.append(field)
                            audit.writerow([trade_date, code, field, " | ".join(values),
                                            chosen["archive_date"], chosen[field]])
                            totals[f"conflict_{field}"] += 1
                    writer.writerow({**chosen, "snapshot_count": len(rows), "conflict_fields": "|".join(flags)})
                    trade_days.add(trade_date)
                    tickers.add(code)
                    totals["unique_rows"] += 1
                    totals["duplicate_rows_removed"] += len(rows) - 1
                    totals["groups_with_conflicts"] += bool(flags)
                    totals["usable_without_conflict"] += bool(
                        not flags and chosen["market"] in {"KOSPI", "KOSDAQ"}
                        and chosen["sector"] and chosen["previous_close_krw"])
                print(f"{part.stem}: {len(groups):,} unique rows", flush=True)

    keys = ("input_rows", "unique_rows", "duplicate_rows_removed", "groups_with_conflicts",
            *(f"conflict_{f}" for f in COMPARE), "usable_without_conflict")
    lines = ["mode: deduplicate", *(f"{k}: {totals[k]:,}" for k in keys),
             f"distinct_trade_days: {len(trade_days):,}", f"distinct_tickers: {len(tickers):,}",
             f"trade_range: {min(trade_days)} ~ {max(trade_days)}" if trade_days else "trade_range: empty",
             f"output: {out}"]
    summary = "\n".join(lines) + "\n"
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.source.expanduser(), args.out.expanduser())
