"""Compare recovery within each shock and create figures for the first presentation."""

import argparse
from collections import defaultdict
import csv
from pathlib import Path
from statistics import mean, median


def read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def compare(rows):
    by_event = defaultdict(list)
    for r in rows:
        if r["status"] in {"recovered", "not_recovered"}:
            by_event[r["event_date"]].append(r)
    result = []
    for day, group in sorted(by_event.items()):
        cut = median(float(r["day0_pct"]) for r in group)
        smaller = [r for r in group if float(r["day0_pct"]) >= cut]
        larger = [r for r in group if float(r["day0_pct"]) < cut]
        if not smaller or not larger:
            continue
        rate = lambda items: 100 * mean(r["status"] == "recovered" for r in items)
        less, more = rate(smaller), rate(larger)
        wait = lambda items: mean(int(r["recovery_day"]) if r["status"] == "recovered" else 10
                                  for r in items)
        result.append((day, cut, len(smaller), len(larger), less, more,
                       wait(smaller), wait(larger), less - more))
    return result


def figures(sectors, event_rows, comparisons, out):
    import warnings
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts = {font.name for font in font_manager.fontManager.ttflist}
    family = [name for name in ["Noto Sans CJK KR", "Malgun Gothic", "NanumGothic", "Apple SD Gothic Neo"] if name in fonts]
    plt.rcParams.update({"svg.fonttype": "none", "font.family": [*family, "DejaVu Sans"], "axes.unicode_minus": False})
    warnings.filterwarnings("ignore", message=r"Glyph .* missing from font")

    consistent = [s for s in sectors if int(s["n_events"]) >= 7]
    ranked = sorted(consistent, key=lambda r: float(r["mean_day0_pct"]))
    selected = {r["sector"]: r for r in (ranked[:5] + ranked[-5:])}
    bank = next((r for r in consistent if r["sector"] == "은행"), None)
    if bank:
        selected["은행"] = bank
    shown = sorted(selected.values(), key=lambda r: float(r["mean_day0_pct"]))
    if shown:
        fig, ax = plt.subplots(figsize=(10, 6.2))
        labels = [r["sector"] for r in shown]
        ax.barh(labels, [float(r["mean_day0_pct"]) for r in shown],
                color=["#0f766e" if n == "은행" else "#6b8ab5" for n in labels])
        ax.set(xlabel="평균 급락 당일 수익률 (%)", title="시장 급락 당일, 어느 업종이 덜 떨어졌나?")
        ax.grid(axis="x", alpha=.25)
        fig.subplots_adjust(left=.28, right=.96, bottom=.17, top=.90)
        fig.text(.28, .06, "7건 이상 관측한 업종 중 양끝 5개와 은행 · 종목별 동일 비중", fontsize=9)
        fig.savefig(out / "sector_day0.svg")
        plt.close(fig)

    losses = defaultdict(list)
    for row in event_rows:
        if row["status"] in {"recovered", "not_recovered"}:
            losses[row["sector"]].append(-float(row["day0_pct"]))
    scatter = [r for r in sectors if len(losses[r["sector"]]) >= 5 and r["recovery_rate"]]
    if scatter:
        fig, ax = plt.subplots(figsize=(9, 5.8))
        for r in scatter:
            x, y = mean(losses[r["sector"]]), 100 * float(r["recovery_rate"])
            is_bank = r["sector"] == "은행"
            ax.scatter(x, y, c="#0f766e" if is_bank else "#8097b4", s=92 if is_bank else 26,
                       alpha=1 if is_bank else .65, zorder=3 if is_bank else 2)
            if is_bank:
                ax.annotate("은행", (x, y), xytext=(7, 5), textcoords="offset points")
        ax.set(xlabel="하락한 사건의 당일 평균 낙폭 (%)", ylabel="10거래일 내 회복 비율 (%)",
               title="낙폭이 작았던 업종은 더 잘 회복했나?", ylim=(-3, 103))
        ax.grid(alpha=.25)
        fig.subplots_adjust(left=.14, right=.96, bottom=.20, top=.90)
        fig.text(.14, .07, "당일 하락이 5건 이상인 업종 · 회복률 분모는 당일 하락 건수", fontsize=9)
        fig.savefig(out / "sector_recovery.svg")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    diffs = [r[-1] for r in comparisons]
    ax.barh([r[0] for r in comparisons], diffs,
            color=["#0f766e" if v >= 0 else "#a44936" for v in diffs])
    ax.axvline(0, color="#334155", linewidth=.8)
    ax.axvline(mean(diffs), color="#334155", linestyle="--", linewidth=1.1,
               label=f"사건별 평균 {mean(diffs):+.1f}%p")
    ax.legend(loc="best")
    ax.set(xlabel="낙폭이 작은 그룹의 회복률 − 낙폭이 큰 그룹의 회복률 (%p)",
           title="같은 급락일에서 낙폭이 작은 업종이 더 잘 회복했나?")
    ax.grid(axis="x", alpha=.25)
    fig.subplots_adjust(left=.18, right=.96, bottom=.17, top=.90)
    fig.savefig(out / "within_event_recovery.svg")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recovery", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    rows = read(a.recovery / "sector_events.csv")
    sector_rows = read(a.recovery / "sector_summary.csv")
    comparisons = compare(rows)
    if not comparisons:
        raise ValueError("No usable within-event comparisons")
    a.out.mkdir(parents=True, exist_ok=False)
    with (a.out / "within_event.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_date", "median_day0_pct", "n_smaller_drop", "n_larger_drop",
                    "smaller_drop_recovery_pct", "larger_drop_recovery_pct",
                    "smaller_drop_capped_wait_days", "larger_drop_capped_wait_days", "difference_pp"])
        w.writerows(comparisons)
    diffs = [r[-1] for r in comparisons]
    try:
        figures(sector_rows, rows, comparisons, a.out)
        chart_status = " ".join(p.name for p in sorted(a.out.glob("*.svg"))) or "none"
    except ModuleNotFoundError as exc:
        if exc.name != "matplotlib":
            raise
        chart_status = "matplotlib not installed; CSV and summary saved"
    lines = ["mode: compare_recovery", f"compared_events: {len(diffs)}",
             f"mean_smaller_drop_recovery_pct: {mean(r[4] for r in comparisons):.2f}",
             f"mean_larger_drop_recovery_pct: {mean(r[5] for r in comparisons):.2f}",
             f"mean_difference_pp: {mean(diffs):+.2f}",
             f"mean_smaller_drop_capped_wait_days: {mean(r[6] for r in comparisons):.2f}",
             f"mean_larger_drop_capped_wait_days: {mean(r[7] for r in comparisons):.2f}",
             f"smaller_drop_higher: {sum(d > 1e-9 for d in diffs)}",
             f"larger_drop_higher: {sum(d < -1e-9 for d in diffs)}",
             f"ties: {sum(abs(d) <= 1e-9 for d in diffs)}",
             f"figures: {chart_status}", f"output: {a.out}"]
    summary = "\n".join(lines) + "\n"
    (a.out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, end="")


if __name__ == "__main__":
    main()
