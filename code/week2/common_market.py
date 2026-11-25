"""Compute market-cap rank and volume-event summaries from enriched archive extracts."""

import argparse
from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path
from statistics import median


def read(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def rankings(path):
    rows = [r for r in read(path) if r["market"] in {"KOSPI", "KOSDAQ"}
            and r["parse_status"] == "ok" and r["market_cap_krw"] and not r["conflict_fields"]]
    rows.sort(key=lambda r: (-int(r["market_cap_krw"]), r["code"]))
    return [dict(code=r["code"], name=r["name"], sector=r["sector"] or "미분류",
                 cap=int(r["market_cap_krw"]), rank=i)
            for i, r in enumerate(rows, 1)]


def rank_summary(first, last):
    start, end = rankings(first), rankings(last)
    old = {r["code"]: r for r in start}
    new = {r["code"]: r for r in end}
    if min(len(old), len(new)) < 30:
        raise ValueError("Both snapshots require at least 30 eligible tickers.")
    moves = defaultdict(list)
    for code in old.keys() & new.keys():
        row = old[code]
        if row["sector"] != "미분류" and row["sector"] == new[code]["sector"]:
            moves[row["sector"]].append(abs(row["rank"] - new[code]["rank"]))
    movement = sorted((dict(sector=k, n=len(v), shift=median(v)) for k, v in moves.items() if len(v) >= 5),
                      key=lambda r: (-r["shift"],r["sector"]))
    return dict(dates=[p.name[:10] for p in (first,last)], sizes=[len(old),len(new)],
                retained=sum(new.get(r["code"], {}).get("rank", 999999) <= 30 for r in start[:30]),
                current=[{**r, "prior": old.get(r["code"],{}).get("rank")} for r in end[:30]],
                movement=movement)


def volume_summary(paths, sessions):
    index = {r["trade_date"]: i for i,r in enumerate(read(sessions))}
    groups = defaultdict(list)
    for path in paths:
        day = path.name[:10]
        if day not in index:
            continue
        for r in read(path):
            if r["parse_status"] != "ok" or r["conflict_fields"] or r["market"] not in {"KOSPI","KOSDAQ"}:
                continue
            if not r["sector"] or not r["volume_shares"] or not r["previous_close_krw"]:
                continue
            groups[r["code"]].append((index[day], int(r["volume_shares"]),
                                        int(r["close_krw"]), int(r["previous_close_krw"])))
    samples = {k: defaultdict(list) for k in (2,3)}
    for observations in groups.values():
        segment = []
        for item in observations:
            day, volume, close, prev = item
            if segment and (day != segment[-1][0]+1 or prev != segment[-1][2]):
                segment = []
            if not prev or abs(close/prev-1) > .35:
                segment = []
                continue
            segment.append(item)
            if len(segment) < 41:
                continue
            point = len(segment)-21
            baseline = median(r[1] for r in segment[point-20:point])
            current = segment[point]
            if baseline <= 0 or current[1] < 2*baseline:
                continue
            for cutoff, values in samples.items():
                if current[1] >= cutoff*baseline:
                    values[-5].append(100*(current[3]/segment[point-6][2]-1))
                    for horizon in (5,10,20):
                        values[horizon].append(100*(segment[point+horizon][2]/current[2]-1))
    return dict(dates=[paths[0].name[:10],paths[-1].name[:10]], days=len(paths),
                results=[dict(cutoff=k, n=len(v[5]), returns=[median(v[h]) if v[h] else None for h in (-5,5,10,20)])
                         for k,v in samples.items()])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--daily",type=Path,required=True)
    p.add_argument("--sessions",type=Path,required=True)
    p.add_argument("--first",type=Path)
    p.add_argument("--last",type=Path)
    p.add_argument("--earnings",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args = p.parse_args()
    valid = {r["trade_date"] for r in read(args.sessions) if r["status"]=="ok"}
    paths = [p for p in sorted(args.daily.glob("*.csv.gz")) if p.name[:10] in valid]
    if not paths:
        raise ValueError("No eligible date files")
    earnings = list(read(args.earnings))
    quarterly = [r for r in earnings if r["frequency"]=="quarterly"]
    preferred = {"005930","000660","105560","005380","055550"}
    examples = sorted((r for r in quarterly if r["code"] in preferred),
                      key=lambda r: (r["first_actual_archive_date"],r["code"]),reverse=True)
    payload = dict(ranks=rank_summary(args.first or paths[0],args.last or paths[-1]),
                   volume=volume_summary(paths,args.sessions),
                   earnings=dict(n=len(quarterly), examples=(examples or quarterly)[:5]),
                   last_checkpoint=paths[-1].name[:10])
    args.out.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding="utf-8")


if __name__=="__main__":
    main()
