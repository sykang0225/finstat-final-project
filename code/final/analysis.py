"""기말 발표 분석: WICS 10개 대분류 기준으로 공통 질문 1~4 결과를 한 파일(JSON)로 만든다.

입력은 학교 서버 아카이브에서 추출한 일별 종목 표(enriched/full/daily/*.csv.gz)와 날짜 품질표(sessions.csv).
규칙은 2주차 분석(code/week2)과 같다. 바뀐 것은 업종을 WICS 대분류로 묶은 것뿐이다.

질문 1  시가총액 비중: 시작일·종료일 대분류별 시총 합계와 비중, 월말 비중 추이(ETF 등 업종 없는 종목 제외),
        상위 30 종목의 진입·이탈과 시총 변화(종목 '개수'가 아니라 시총 '금액'으로 본다)
질문 2  대분류 시총가중 일간 수익률 → 누적 수익률, 업종 간 퍼짐(표준편차), 동일가중 비교
질문 3  대분류 간 평균 상관: 코스피 등락 절댓값 상위 10% 날(급변일) vs 나머지, 급변일 목록
질문 4  거래량 급증(직전 20거래일 거래량 가운데값의 2배·3배) 전후 수익률, 급증이 몰린 날

python code/final/analysis.py --daily data/enriched_daily --sessions data/week2/tables/sessions.csv \
  --kospi data/week1/kospi_reference.csv --start 2024-04-15 --end 2026-09-02 --out data/final/analysis.json
"""
import argparse, csv, gzip, io, json, math, statistics as st, sys, tempfile
from collections import defaultdict, Counter
from pathlib import Path
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "week2")]
from wics import wics, ORDER, WICS  # noqa: E402
from build_meeting import sector_summary  # noqa: E402

p = argparse.ArgumentParser()
for k in ("--daily", "--sessions", "--kospi", "--out"):
    p.add_argument(k, type=Path, required=True)
p.add_argument("--start", required=True)
p.add_argument("--end", required=True)
a = p.parse_args()


def rows_of(path):
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as h:
        return [r for r in csv.DictReader(h)
                if r["market"] in {"KOSPI", "KOSDAQ"} and r["parse_status"] == "ok" and not r["conflict_fields"]]


sessions_text = a.sessions.read_text(encoding="utf-8-sig")
sessions = list(csv.DictReader(io.StringIO(sessions_text)))
good = {r["trade_date"]: float(r["kospi_return_pct"]) for r in sessions if r["status"] == "ok"}
sess_index = {r["trade_date"]: i for i, r in enumerate(sessions)}
kospi = {r["trade_date"]: float(r["close"]) for r in csv.DictReader(open(a.kospi, encoding="utf-8-sig"))}
kd = sorted(kospi); prior_day = dict(zip(kd[1:], kd))
files = sorted(a.daily.glob("*.csv.gz"))

# ---------------------------------------------------------------- 한 번 읽으면서 필요한 값 모두 쌓기
sector_rows = []                       # 질문 2·3: 대분류 일간 수익률 (week2 sector_daily.csv 형식)
month_cap = {}                         # 질문 1: 월말 대분류 시총
vol_obs = defaultdict(list)            # 질문 4: 종목별 (세션 순번, 거래량, 종가, 전일종가, 날짜, 대분류)
snap = {}
prev_day, prev = None, {}
for f in files:
    day = f.name[:10]
    rows = rows_of(f)
    if day in (a.start, a.end):
        snap[day] = rows
    by = {r["code"]: r for r in rows if r["sector"]}
    if day in good:
        caps = defaultdict(int)
        for r in rows:
            w = wics(r["sector"])
            if w and r["market_cap_krw"]:
                caps[w] += int(r["market_cap_krw"])
        if day >= a.start:
            month_cap[day[:7]] = (day, dict(caps))      # 같은 달 마지막 품질 통과일로 덮어씀
        for r in by.values():
            if r["volume_shares"] and r["previous_close_krw"]:
                vol_obs[r["code"]].append((sess_index[day], int(r["volume_shares"]), int(r["close_krw"]),
                                           int(r["previous_close_krw"]), day, wics(r["sector"])))
    if day in good and prior_day.get(day) == prev_day:
        g = defaultdict(list)
        for code, r in by.items():
            o = prev.get(code)
            w = wics(r["sector"])
            if not w or not o or (o["sector"], o["market"]) != (r["sector"], r["market"]):
                continue
            if not r["previous_close_krw"] or float(r["previous_close_krw"]) != float(o["close_krw"]):
                continue
            v = 100 * (float(r["close_krw"]) / float(o["close_krw"]) - 1)
            if abs(v) <= 35:
                g[w].append((v, float(o["market_cap_krw"] or 0)))
        for w, vals in g.items():
            cs = [(v, c) for v, c in vals if c > 0]
            if len(vals) < 5 or len(cs) / len(vals) < .8:
                continue
            sector_rows.append(dict(trade_date=day, sector=w, members=len(vals),
                                    cap_sample_equal_pct=st.mean(v for v, _ in cs),
                                    cap_weighted_pct=sum(v * c for v, c in cs) / sum(c for _, c in cs),
                                    cap_members=len(cs)))
    prev_day, prev = day, by

# ---------------------------------------------------------------- 질문 1: 시총 금액과 비중
def ranking(rows):
    # 업종이 없는 행(ETF·ETN 등 미분류)은 회사 시총이 아니라서 뺀다
    rs = sorted((r for r in rows if r["market_cap_krw"] and wics(r["sector"])),
                key=lambda r: (-int(r["market_cap_krw"]), r["code"]))
    return [dict(code=r["code"], name=r["name"], sector=r["sector"] or "미분류", wics=wics(r["sector"]) or "미분류",
                 cap=int(r["market_cap_krw"]), rank=i) for i, r in enumerate(rs, 1)]


r0, r1 = ranking(snap[a.start]), ranking(snap[a.end])
i0, i1 = {r["code"]: r for r in r0}, {r["code"]: r for r in r1}
total = [sum(r["cap"] for r in r0), sum(r["cap"] for r in r1)]
by_w = [defaultdict(int), defaultdict(int)]
for k, rr in enumerate((r0, r1)):
    for r in rr:
        by_w[k][r["wics"]] += r["cap"]
JO = 1e12
q1_sectors = [dict(wics=w, cap=[round(by_w[0][w] / JO, 1), round(by_w[1][w] / JO, 1)],
                   share=[round(100 * by_w[0][w] / total[0], 1), round(100 * by_w[1][w] / total[1], 1)],
                   change_pct=round(100 * (by_w[1][w] / by_w[0][w] - 1), 1),
                   contrib_jo=round((by_w[1][w] - by_w[0][w]) / JO, 1))
              for w in ORDER]


def top_rows(rr, other):
    return [dict(r, cap_jo=round(r["cap"] / JO, 1),
                 other_rank=other.get(r["code"], {}).get("rank"),
                 other_cap_jo=round(other[r["code"]]["cap"] / JO, 1) if r["code"] in other else None)
            for r in rr[:30]]


top0, top1 = top_rows(r0, i1), top_rows(r1, i0)
entries = [r for r in top1 if not r["other_rank"] or r["other_rank"] > 30]
exits = [r for r in top0 if not r["other_rank"] or r["other_rank"] > 30]
for r in entries + exits:
    a_, b_ = (r["other_cap_jo"], r["cap_jo"]) if r in entries else (r["cap_jo"], r["other_cap_jo"])
    r["cap_change_pct"] = round(100 * (b_ / a_ - 1), 1) if a_ and b_ else None
top30_cap = [sum(r["cap"] for r in r0[:30]), sum(r["cap"] for r in r1[:30])]
# 시총 증가분이 어디서 왔나: 상위 10 종목 기여
gain = sorted(((r["name"], r["wics"], (r["cap"] - i0[r["code"]]["cap"]) / JO) for r in r1 if r["code"] in i0),
              key=lambda t: -t[2])
monthly = [dict(month=m, day=d, share={w: round(100 * c.get(w, 0) / sum(c.values()), 2) for w in ORDER},
                total_jo=round(sum(c.values()) / JO, 1))
           for m, (d, c) in sorted(month_cap.items())]
q1 = dict(dates=[a.start, a.end], n=[len(r0), len(r1)], total_jo=[round(t / JO, 1) for t in total],
          sectors=q1_sectors, top30_jo=[round(t / JO, 1) for t in top30_cap],
          top30_share=[round(100 * top30_cap[0] / total[0], 1), round(100 * top30_cap[1] / total[1], 1)],
          entries=entries, exits=exits, stayed=[r for r in top1 if r["other_rank"] and r["other_rank"] <= 30],
          top_gainers=[dict(name=n, wics=w, gain_jo=round(g, 1)) for n, w, g in gain[:10]],
          top_losers=[dict(name=n, wics=w, gain_jo=round(g, 1)) for n, w, g in gain[::-1][:10]],
          monthly=monthly)

# ---------------------------------------------------------------- 질문 2·3: week2 요약 함수 재사용
buf = io.StringIO()
wr = csv.DictWriter(buf, fieldnames=list(sector_rows[0])); wr.writeheader(); wr.writerows(sector_rows)
a.out.parent.mkdir(parents=True, exist_ok=True)
sector_zip = a.out.with_name("wics_sector_daily.zip")
with ZipFile(sector_zip, "w") as z:
    z.writestr("sector_daily.csv", buf.getvalue())
    z.writestr("sessions.csv", sessions_text)
S = sector_summary(sector_zip)
daily = defaultdict(dict)
for r in sector_rows:
    daily[r["trade_date"]][r["sector"]] = r["cap_weighted_pct"]
used = [d for d in sorted(daily) if all(w in daily[d] for w in S["sectors"])]
thr = S["threshold"]
shock = sorted(([d, round(good[d], 2),
                 max(S["sectors"], key=lambda w: daily[d][w]), round(max(daily[d].values()), 2),
                 min(S["sectors"], key=lambda w: daily[d][w]), round(min(daily[d].values()), 2)]
                for d in used if abs(good[d]) >= thr), key=lambda t: t[0])
h = len(S["returns"]) // 2
iqr = [(lambda q: q[2] - q[0])(st.quantiles(r[1], n=4, method="inclusive")) for r in S["returns"]]
q23 = dict(S, spread=[[r[0], round(math.sqrt(r[2]), 1)] for r in S["returns"]],
           spread_half=[round(math.sqrt(st.median(r[2] for r in S["returns"][:h])), 1),
                        round(math.sqrt(st.median(r[2] for r in S["returns"][h:])), 1)],
           iqr_half=[round(st.median(iqr[:h]), 1), round(st.median(iqr[h:]), 1)],
           shock_days=shock, kospi=[[d, kospi[d]] for d in kd if a.start <= d <= a.end])

# ---------------------------------------------------------------- 질문 4: week2 규칙 그대로 + 날짜·대분류별 집계
samples = {k: defaultdict(list) for k in (2, 3)}
per_day, per_w = Counter(), Counter()
for obs in vol_obs.values():
    seg = []
    for item in obs:
        idx, vol, close, prv = item[:4]
        if seg and (idx != seg[-1][0] + 1 or prv != seg[-1][2]):
            seg = []
        if not prv or abs(close / prv - 1) > .35:
            seg = []
            continue
        seg.append(item)
        if len(seg) < 41:
            continue
        pt = len(seg) - 21
        base = st.median(x[1] for x in seg[pt - 20:pt])
        cur = seg[pt]
        if base <= 0 or cur[1] < 2 * base:
            continue
        per_day[cur[4]] += 1
        per_w[cur[5]] += 1
        for cutoff, vals in samples.items():
            if cur[1] >= cutoff * base:
                vals[-5].append(100 * (cur[3] / seg[pt - 6][2] - 1))
                for hz in (5, 10, 20):
                    vals[hz].append(100 * (seg[pt + hz][2] / cur[2] - 1))
q4 = dict(results=[dict(cutoff=k, n=len(v[5]), returns=[round(st.median(v[x]), 3) for x in (-5, 5, 10, 20)])
                   for k, v in samples.items()],
          top_days=[[d, n, round(good[d], 2)] for d, n in per_day.most_common(10)],
          per_day_median=st.median(per_day.values()), by_wics=dict(per_w))

out = dict(wics=WICS, q1=q1, q23=q23, q4=q4)
a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("Q1 total", q1["total_jo"], "top30", q1["top30_jo"], q1["top30_share"])
print("Q2 days", S["days"], "spread", q23["spread_half"], "rho", S["rho"], "shock", len(shock))
print("Q4", q4["results"])
