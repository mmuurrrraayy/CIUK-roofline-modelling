#!/usr/bin/env python3
"""Place Grid's kernels on the likwid-bench roofline (Task 1) using the likwid-perfctr marker runs (Task 2).

Usage: python3 grid_roofline.py [--results results] [--task1 ../task1-dine/final-run] [--out grid_roofline.png]
Reads results/<arch>/likwid_marker/t<T>_<GROUP>_<job>.csv (+ the matching .json Grid wrote) and pairs the
FLOPS_SP and MEM runs of the same arch and thread count. Per marker region (action x L):
  FLOPs  = FLOPS_SP counters, summed over threads (spr: FP_ARITH_INST_RETIRED_* x lanes, zen2: RETIRED_SSE_AVX_FLOPS_ALL)
  bytes  = MEM group 'Memory data volume' (DRAM traffic from the memory controllers)
  AI     = FLOPs / bytes  -- valid because both runs do the same work (instruction counts are checked)
  time   = longest thread's RDTSC runtime of the region (4 kernel cases x ncall, as Grid times them)
Writes <out>.csv with every region and draws full-node runs on the Task 1 ceilings (roofline.py).
Needs matplotlib.
"""
import argparse, csv, glob, json, os, re, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# Lanes per FP_ARITH_INST_RETIRED event (Intel counts an FMA twice, so lanes = FLOPs per instruction).
SPR_LANES = {"FP_ARITH_INST_RETIRED_SCALAR_SINGLE": 1, "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE": 4,
             "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE": 8, "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE": 16}
ACTIONS = {"wilson": "Wilson", "dwf": "DWF (Ls=16)", "staggered": "Staggered"}
GRID_KEY = {"wilson": "Gflops_wilson", "dwf": "Gflops_dwf4", "staggered": "Gflops_staggered"}
# Categorical slots in fixed order for the three actions; roofs are drawn in greys so they don't compete.
COLORS = {"wilson": "#2a78d6", "dwf": "#eb6834", "staggered": "#1baf7a"}
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8f8d87", "#e6e5e0"
ROOF_GREYS = {"L1": "#c9c8c2", "L2": "#b4b3ad", "L3": "#9f9e98", "DRAM": "#52514e"}


def num(s):
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_marker(path):
    """-> {region: {'raw': {event: sum}, 'metric': {metric: (sum, min, max, avg)}}} from the STAT tables."""
    regions, region, table = defaultdict(lambda: {"raw": defaultdict(float), "metric": {}}), None, None
    with open(path) as f:
        for row in csv.reader(f):
            if not row:
                continue
            if row[0] == "TABLE":
                region, table = row[1].removeprefix("Region "), row[2]
                continue
            if row[0] == "STRUCT":
                table = None
            if table == "Group 1 Raw STAT" and row[0].endswith(" STAT"):
                regions[region]["raw"][row[0][:-5]] += num(row[2])        # MBOX0..11 etc. summed
            elif table == "Group 1 Metric STAT" and row[0].endswith(" STAT"):
                regions[region]["metric"][row[0][:-5]] = tuple(num(x) for x in row[1:5])
    return regions


def grid_json(path):
    """Grid's own Gflop/s per (action, L) from the --json-out of the same run."""
    with open(path) as f:
        res = json.load(f)["flops"]["results"]
    return {(a, r["L"]): r[k] for r in res for a, k in GRID_KEY.items()}


def collect(results):
    runs = defaultdict(dict)   # (arch, threads) -> {group: (regions, grid)}
    for path in glob.glob(os.path.join(results, "*", "likwid_marker", "t*_*.csv")):
        m = re.match(r"t(\d+)_(FLOPS_SP|MEM)_\d+\.csv$", os.path.basename(path))
        if not m or os.path.getsize(path) == 0:
            continue
        arch = os.path.basename(os.path.dirname(os.path.dirname(path)))
        js = path[:-4] + ".json"
        runs[(arch, int(m.group(1)))][m.group(2)] = (parse_marker(path), grid_json(js) if os.path.exists(js) else {})
    rows = []
    for (arch, t), g in sorted(runs.items()):
        if not {"FLOPS_SP", "MEM"} <= g.keys():
            print(f"skip {arch} {t} threads: needs both FLOPS_SP and MEM runs, have {sorted(g)}")
            continue
        (fl, grid), (mem, _) = g["FLOPS_SP"], g["MEM"]
        for region in fl:
            m = re.match(r"(wilson|dwf|staggered)_\w+?_L(\d+)$", region)
            if not m or region not in mem:
                continue
            action, L = m.group(1), int(m.group(2))
            raw, met = fl[region]["raw"], fl[region]["metric"]
            if arch == "spr":
                flops = sum(raw.get(e, 0) * n for e, n in SPR_LANES.items())
                instr = ("INSTR_RETIRED_ANY",) * 2
            else:
                flops = raw.get("RETIRED_SSE_AVX_FLOPS_ALL", 0)
                instr = ("RETIRED_INSTRUCTIONS",) * 2
            nbytes = mem[region]["metric"]["Memory data volume [GBytes]"][0] * 1e9
            t_fl = met["Runtime (RDTSC) [s]"][2]
            t_mem = mem[region]["metric"]["Runtime (RDTSC) [s]"][2]
            lk = flops / t_fl / 1e9
            gr = grid.get((action, L))
            rows.append(dict(arch=arch, threads=t, action=action, L=L, gflop=flops / 1e9, dram_gb=nbytes / 1e9,
                             ai=flops / nbytes if nbytes else float("inf"),
                             likwid_gflops=lk, grid_gflops=gr, likwid_over_grid=lk / gr if gr else None,
                             dram_gbs=nbytes / t_mem / 1e9,
                             instr_ratio=mem[region]["raw"][instr[1]] / raw[instr[0]] if raw.get(instr[0]) else None))
    return rows


def roofs(ax, c, xs):
    top = c["SP"]
    for level in ("L1", "L2", "L3", "DRAM"):
        if level in c:
            bw = c[level]
            seg = [x for x in xs if x <= top / bw] + [top / bw]
            ax.plot(seg, [bw * x for x in seg], color=ROOF_GREYS[level], lw=2 if level == "DRAM" else 1.2, zorder=1)
            x_lab = xs[0] * 1.15
            ax.annotate(f"{level} {bw:,.0f} GB/s", (x_lab, bw * x_lab), xytext=(0, 4), textcoords="offset points",
                        fontsize=7.5, color=INK2, rotation=0)
    for prec, ls in (("DP", ":"), ("SP", "-")):
        ridge = c[prec] / c["DRAM"]
        ax.plot([ridge, xs[-1]], [c[prec]] * 2, color=INK2, lw=1.5 if prec == "DP" else 2, ls=ls, zorder=1)
        ax.annotate(f"{prec} peak {c[prec]:,.0f}", (xs[-1], c[prec]), xytext=(-4, 4), textcoords="offset points",
                    fontsize=7.5, color=INK2, ha="right")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--task1", default=os.path.join("..", "task1-dine", "final-run"),
                    help="folder holding roofline.py and the likwid-bench results/")
    ap.add_argument("--out", default="grid_roofline.png")
    args = ap.parse_args()

    sys.path.insert(0, args.task1)
    import roofline   # Task 1 ceilings: same parsing and medians as roofline.py
    bench = roofline.load(os.path.join(args.task1, "results"))
    ceil = {a: roofline.ceilings(a, bench[a]) for a in roofline.SPEC if a in bench}

    rows = collect(args.results)
    if not rows:
        raise SystemExit(f"no paired FLOPS_SP + MEM marker runs under {args.results}")
    for r in rows:
        c = ceil[r["arch"]]
        roof = min(c["SP"], c["DRAM"] * r["ai"])
        r["dram_roof_gflops"], r["pct_of_dram_roof"] = roof, 100 * r["likwid_gflops"] / roof
        r["pct_of_dram_bw"] = 100 * r["dram_gbs"] / c["DRAM"]

    out_csv = os.path.splitext(args.out)[0] + ".csv"
    cols = ["arch", "threads", "action", "L", "gflop", "dram_gb", "ai", "likwid_gflops", "grid_gflops",
            "likwid_over_grid", "dram_gbs", "pct_of_dram_bw", "dram_roof_gflops", "pct_of_dram_roof", "instr_ratio"]
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in sorted(rows, key=lambda r: (r["arch"], r["threads"], r["action"], r["L"])):
            w.writerow([f"{r[k]:.3g}" if isinstance(r[k], float) else ("" if r[k] is None else r[k]) for k in cols])
    print(open(out_csv).read())

    # Plot the full-node run of each arch (most threads available).
    full = {a: max(r["threads"] for r in rows if r["arch"] == a) for a in {r["arch"] for r in rows}}
    archs = [a for a in roofline.SPEC if a in full]
    ais = [r["ai"] for r in rows]
    lo = 2 ** (int(min(ais + [0.25])).bit_length() - 3) if min(ais) >= 1 else 1 / 8
    xs = [lo * 2 ** (e / 4) for e in range(0, 4 * 12 + 1)]
    xs = [x for x in xs if x <= max(64, max(ais) * 2)]
    fig, axes = plt.subplots(1, len(archs), figsize=(6.5 * len(archs), 5.5), sharey=True, squeeze=False)
    for ax, a in zip(axes[0], archs):
        roofs(ax, ceil[a], xs)
        for action in ACTIONS:
            pts = sorted((r for r in rows if r["arch"] == a and r["threads"] == full[a] and r["action"] == action),
                         key=lambda r: r["L"])
            ax.plot([r["ai"] for r in pts], [r["likwid_gflops"] for r in pts], "-o", color=COLORS[action], lw=1,
                    ms=7, mec="white", mew=1.2, label=ACTIONS[action], zorder=3)
            for r in pts:
                ax.annotate(f"L{r['L']}", (r["ai"], r["likwid_gflops"]), xytext=(5, -10), textcoords="offset points",
                            fontsize=7.5, color=INK2)
        ax.set_title(f"{roofline.SPEC[a]['name']}, {full[a]} threads", fontsize=11, color=INK, loc="left")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log", base=10)
        ax.set_xlim(xs[0], xs[-1])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}" if x >= 1 else f"1/{1 / x:g}"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:,.0f}"))
        ax.set_xlabel("Arithmetic intensity, SP FLOP per DRAM byte (likwid-perfctr)", color=INK2)
        ax.grid(True, which="major", color=GRID, lw=0.8)
        ax.tick_params(colors=INK2, which="both")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(MUTED)
        ax.legend(loc="lower right", fontsize=8.5, frameon=False, labelcolor=INK2)
    axes[0][0].set_ylabel("SP performance (GFLOP/s, hardware counters)", color=INK2)
    fig.suptitle("Grid Dirac operators (FP32) on the likwid-bench roofline", fontsize=12, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(args.out, dpi=200, facecolor="white")
    print(f"wrote {args.out} and {out_csv}")


if __name__ == "__main__":
    main()
