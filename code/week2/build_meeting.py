"""Build an offline presentation from the archive summaries and prior sector panel."""
import argparse
from collections import defaultdict
import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from statistics import correlation, mean, median, pvariance, sqrt
from itertools import combinations
from zipfile import ZipFile
from common_market import rankings


def sector_summary(path):
    def read(name):
        with ZipFile(path) as archive:
            member, = [n for n in archive.namelist() if Path(n).name == name]
            return list(csv.DictReader(io.StringIO(archive.read(member).decode("utf-8-sig"))))

    good = {r["trade_date"]:float(r["kospi_return_pct"]) for r in read("sessions.csv") if r["status"]=="ok"}
    groups = defaultdict(dict)
    equal_groups = defaultdict(dict)
    counts = defaultdict(dict)
    for r in read("sector_daily.csv"):
        day = r["trade_date"]
        if day in good and r["cap_weighted_pct"]:
            groups[day][r["sector"]] = float(r["cap_weighted_pct"])
            equal_groups[day][r["sector"]] = float(r["cap_sample_equal_pct"])
            counts[day][r["sector"]] = int(r["cap_members"])
    sectors = sorted({s for values in groups.values() for s in values})
    if len(sectors) < 2:  # 고정 12업종 외에 전 업종 기준(code/final/all_sectors.py) 입력도 받는다
        raise ValueError("Need at least two comparison sectors")
    days = sorted(day for day,values in groups.items() if len(values)==len(sectors))
    levels = [1.0]*len(sectors)
    equal_levels = [1.0]*len(sectors)
    monthly_levels = [1.0]*len(sectors)
    points = []
    equal_points = []
    monthly_spread = []
    for i, day in enumerate(days):
        levels = [level*(1+groups[day][sector]/100) for level,sector in zip(levels,sectors)]
        equal_levels = [level*(1+equal_groups[day][sector]/100)
                        for level,sector in zip(equal_levels,sectors)]
        monthly_levels = [level*(1+groups[day][sector]/100)
                          for level,sector in zip(monthly_levels,sectors)]
        pct = [round((v-1)*100,3) for v in levels]
        if i+1 == len(days) or day[:7] != days[i+1][:7]:
            points.append([day,pct,round(pvariance(pct),2)])
            equal_pct = [round((v-1)*100,3) for v in equal_levels]
            equal_points.append([day,equal_pct,round(pvariance(equal_pct),2)])
            monthly_spread.append([day, sqrt(pvariance([(v-1)*100 for v in monthly_levels]))])
            monthly_levels = [1.0]*len(sectors)

    def matrix(subset):
        series = [[groups[day][s] for day in subset] for s in sectors]
        return [[round(correlation(a,b),4) for b in series] for a in series]

    def average(mat):
        return mean(mat[i][j] for i,j in combinations(range(len(sectors)),2))

    threshold = sorted(abs(good[d]) for d in days)[int(.9*len(days))]
    volatile = [d for d in days if abs(good[d]) >= threshold]
    normal = [d for d in days if abs(good[d]) < threshold]
    all_matrix, fast_matrix, normal_matrix = (matrix(d) for d in (days,volatile,normal))
    wider_threshold = sorted(abs(good[d]) for d in days)[int(.8*len(days))]
    wider_fast = [d for d in days if abs(good[d]) >= wider_threshold]
    wider_normal = [d for d in days if abs(good[d]) < wider_threshold]
    roll = [[days[i-1],average(matrix(days[i-60:i]))] for i in [*range(60,len(days),10),len(days)]]
    sizes = {s: [counts[days[0]][s], median(counts[d][s] for d in days), counts[days[-1]][s]]
             for s in sectors}
    return dict(sectors=sectors, dates=[days[0],days[-1]], days=len(days), returns=points, sizes=sizes,
                correlations=[all_matrix,fast_matrix,normal_matrix], rho=[average(m) for m in (all_matrix,fast_matrix,normal_matrix)],
                groups=[len(days),len(volatile),len(normal)], threshold=threshold, roll=roll,
                equal_returns=equal_points, monthly_spread=monthly_spread,
                wider=dict(threshold=wider_threshold, n=len(wider_fast),
                           fast=average(matrix(wider_fast)), normal=average(matrix(wider_normal))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sectors",type=Path,required=True)
    parser.add_argument("--market",type=Path)
    parser.add_argument("--rank-endpoints",type=Path,help="ZIP with the two enriched daily files")
    parser.add_argument("--final",action="store_true",help="Label data as the completed archive extraction")
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    market = json.loads(args.market.read_text(encoding="utf-8")) if args.market else None
    if market:
        market["final"] = args.final
    if args.rank_endpoints:
        with ZipFile(args.rank_endpoints) as archive, TemporaryDirectory() as cache:
            snapshots = []
            for day in market["ranks"]["dates"]:
                member, = [name for name in archive.namelist() if Path(name).name == f"{day}.csv.gz"]
                path = Path(cache)/f"{day}.csv.gz"
                path.write_bytes(archive.read(member))
                snapshots.append(rankings(path))
            old, new = snapshots
            new_by_code = {r["code"]:r for r in new}
            old_codes = {r["code"] for r in old}
            common_new = [r for r in new if r["code"] in old_codes]
            common_old = [r for r in old if r["code"] in new_by_code]
            market["ranks"]["common_retained"] = len(
                {r["code"] for r in common_old[:30]} & {r["code"] for r in common_new[:30]}
            )
            common_positions = {r["code"]:(i, r["sector"]) for i,r in enumerate(common_new, 1)}
            movements = defaultdict(list)
            for initial, stock in enumerate(common_old, 1):
                end_rank, end_sector = common_positions[stock["code"]]
                if stock["sector"] == end_sector != "미분류":
                    movements[end_sector].append((abs(initial-end_rank), initial, end_rank, stock["name"]))
            cutoff = (len(common_old)+9)//10
            changes = []
            for sector, items in movements.items():
                if len(items) < 5:
                    continue
                large = sum(d >= cutoff for d, *_ in items)
                nearest = min(items, key=lambda r: (abs(r[0]-cutoff), r[3]))
                changes.append(dict(sector=sector, large=large, n=len(items), example=nearest,
                                    rate=100*large/len(items)))
            market["ranks"]["movement_rates"] = sorted(changes,key=lambda r:(-r["rate"],r["sector"]))[:5]
            market["ranks"]["common_n"] = len(common_old)
            market["ranks"]["movement_cutoff"] = cutoff
            market["ranks"]["exits"] = [
                {**r,"current":new_by_code.get(r["code"],{}).get("rank")}
                for r in old[:30] if new_by_code.get(r["code"],{}).get("rank",float("inf"))>30
            ]
            market["ranks"]["sector_sizes"] = {
                sector:[sum(r["sector"]==sector for r in snap) for snap in snapshots]
                for sector in sorted({r["sector"] for snap in snapshots for r in snap})
            }
    data = dict(sector=sector_summary(args.sectors), market=market)
    template = Path(__file__).with_name("meeting_template.html").read_text(encoding="utf-8")
    args.out.write_text(template.replace("/* DATA */",json.dumps(data,ensure_ascii=False,allow_nan=False).replace("<","\\u003c")),encoding="utf-8")
    s = data["sector"]
    print(s["dates"],s["days"],s["groups"],s["rho"], "variance median by half",
          median(r[2] for r in s["returns"][:len(s["returns"])//2]),
          median(r[2] for r in s["returns"][len(s["returns"])//2:]))


if __name__=="__main__":
    main()
