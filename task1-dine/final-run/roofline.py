#!/usr/bin/env python3
"""Draw roofline models for Dine (zen2) and Dine-2 (spr) from the bench.py likwid-bench results.

Usage: python3 roofline.py [--results results] [--out roofline.png]
                           [--point "label:arch:FLOP_per_byte:GFLOPs"] ...
Reads every <arch>_<suite>_<job>.csv under --results (subfolders included), takes the median over
reps, and builds the ceilings:
  compute : peakflops DP and SP, full node (N: workgroup)
  memory  : DRAM = load_* on the full node; L1/L2/L3 = best load_* rate on the cache-sweep plateau
            of each level (size ranges in CACHE, read off the sweep tables in comparison.md)
Theoretical ceilings (base clock x FLOP/cycle, channels x MT/s x 8 B) are drawn dotted for reference.
--point adds measured kernels (e.g. Grid) to a panel. Writes the plot and <out>.csv with the ceilings.
Needs matplotlib.
"""
import argparse, csv, glob, os, re, statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# Spec figures. zen2: AMD EPYC 7302 (16 cores, 3.0 GHz base, 2x256-bit FMA, 8x DDR4-3200 per socket).
# spr: Intel Xeon Gold 6430 (32 cores, 2.1 GHz base, 2x512-bit FMA, 8x DDR5-4400 per socket, Intel ARK).
SPEC = {
    "zen2": dict(name="Dine: 2x AMD EPYC 7302 (Zen2)", cores=32, base_ghz=3.0, dp_per_cycle=16,
                 channels=16, mts=3200),
    "spr": dict(name="Dine-2: 2x Intel Xeon Gold 6430 (SPR)", cores=64, base_ghz=2.1, dp_per_cycle=32,
                channels=16, mts=4400),
}

# Node-total working-set ranges (kB) whose best load bandwidth is taken as that cache level's roof.
# zen2: L1 1 MB, L2 16 MB, L3 256 MB in total; spr: L1 3 MB, L2 128 MB, L3 120 MB (non-inclusive).
# The spr L3 plateau is barely resolved by the sweep (it overlaps the L2->DRAM transition): approximate.
CACHE = {
    "zen2": {"L1": (0, 1_000), "L2": (2_000, 8_000), "L3": (32_000, 128_000)},
    "spr": {"L1": (0, 2_000), "L2": (8_000, 64_000), "L3": (160_000, 256_000)},
}

# Categorical slots (fixed order) for the bandwidth roofs; compute roofs and text stay in ink colours.
COLORS = {"DRAM": "#2a78d6", "L3": "#eb6834", "L2": "#1baf7a", "L1": "#6250d6"}
POINT_COLOR = "#e34948"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8f8d87", "#e6e5e0"


def size_kb(size):
    num, unit = re.fullmatch(r"(\d+)(kB|MB|GB)", size).groups()
    return int(num) * {"kB": 1, "MB": 1000, "GB": 1000000}[unit]


def load(results):
    """-> rows[arch] = {(phase, kernel, workgroup, size): [values in GFLOP/s or GB/s]}"""
    rows = defaultdict(lambda: defaultdict(list))
    for path in glob.glob(os.path.join(results, "**", "*.csv"), recursive=True):
        parts = os.path.basename(path).split("_")
        if len(parts) < 3 or parts[0] not in SPEC or parts[1] not in ("compute", "memory"):
            continue
        metric = "mflops" if parts[1] == "compute" else "mbyte"
        with open(path) as f:
            for r in csv.DictReader(f):
                if r.get(metric):
                    rows[parts[0]][(r["phase"], r["kernel"], r["workgroup"], r["size"])].append(float(r[metric]) / 1000)
    return rows


def ceilings(arch, rows):
    """Measured roofs for one node: {'DP': GFLOP/s, 'SP': ..., 'DRAM': GB/s, 'L1': ..., ...}"""
    med = {k: statistics.median(v) for k, v in rows.items()}
    c = {}
    for (ph, k, wg, size), v in med.items():
        if ph == "peak" and wg.startswith("N:"):
            c["SP" if "_sp_" in k else "DP"] = v
        elif ph == "dram" and wg.startswith("N:") and k.startswith("load_"):
            c["DRAM"] = v
    for level, (lo, hi) in CACHE[arch].items():
        bw = [v for (ph, k, wg, size), v in med.items()
              if ph == "sweep" and k.startswith("load_") and lo <= size_kb(size) <= hi]
        if bw:
            c[level] = max(bw)
    return c


def spec(arch):
    s = SPEC[arch]
    return {"DP": s["cores"] * s["base_ghz"] * s["dp_per_cycle"], "DRAM": s["channels"] * s["mts"] * 8 / 1000}


def panel(ax, arch, c, points, xs):
    s = SPEC[arch]
    top = c.get("SP", c["DP"])
    # Bandwidth roofs: diagonal, each stopped where it meets the SP compute roof.
    for level in ("L1", "L2", "L3", "DRAM"):
        if level not in c:
            continue
        bw = c[level]
        x_end = top / bw
        seg = [x for x in xs if x <= x_end] + [x_end]
        ax.plot(seg, [bw * x for x in seg], color=COLORS[level], lw=2 if level == "DRAM" else 1.5,
                label=f"{level}  {bw:,.0f} GB/s", solid_capstyle="round")
    # Compute roofs: horizontal from the DRAM ridge point.
    for prec, ls in (("DP", "-"), ("SP", "--")):
        if prec in c:
            ridge = c[prec] / c["DRAM"]
            ax.plot([ridge, xs[-1]], [c[prec]] * 2, color=INK, lw=2, ls=ls,
                    label=f"{prec} peak  {c[prec]:,.0f} GFLOP/s")
            ax.plot([ridge], [c[prec]], "o", color=INK, ms=4)
            ax.annotate(f"{prec} ridge {ridge:.1f} FLOP/B", (ridge, c[prec]), xytext=(6, -14),
                        textcoords="offset points", fontsize=8, color=INK2)
    # Theoretical (spec) roof for comparison.
    t = spec(arch)
    ax.plot(xs, [min(t["DP"], t["DRAM"] * x) for x in xs], color=MUTED, lw=1, ls=":",
            label=f"spec: {t['DP']:,.0f} GFLOP/s DP @ base, {t['DRAM']:,.0f} GB/s")
    for label, a, ai, gf in points:
        if a == arch:
            ax.plot([ai], [gf], "o", color=POINT_COLOR, ms=8, mec="white", mew=1.5, zorder=5)
            ax.annotate(label, (ai, gf), xytext=(7, 4), textcoords="offset points", fontsize=8, color=INK)
    ax.set_title(s["name"], fontsize=11, color=INK, loc="left")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=10)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}" if x >= 1 else f"1/{1 / x:g}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:,.0f}"))
    ax.set_xlabel("Arithmetic intensity (FLOP/byte)", color=INK2)
    ax.grid(True, which="major", color=GRID, lw=0.8)
    ax.tick_params(colors=INK2, which="both")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.legend(loc="lower right", fontsize=8, frameon=False, labelcolor=INK2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="roofline.png")
    ap.add_argument("--point", action="append", default=[],
                    help='"label:arch:FLOP_per_byte:GFLOPs", e.g. "Grid L32 64t:spr:1.0:550"')
    args = ap.parse_args()

    points = []
    for p in args.point:
        label, arch, ai, gf = p.rsplit(":", 3)
        points.append((label, arch, float(ai), float(gf)))

    rows = load(args.results)
    archs = [a for a in SPEC if a in rows]
    if not archs:
        raise SystemExit(f"no <arch>_<suite>_<job>.csv files for {list(SPEC)} under {args.results}")
    ceils = {a: ceilings(a, rows[a]) for a in archs}

    xs = [2 ** (e / 4) for e in range(-6 * 4, 6 * 4 + 1)]   # 1/64 .. 64 FLOP/B
    fig, axes = plt.subplots(1, len(archs), figsize=(6.5 * len(archs), 5.5), sharey=True, squeeze=False)
    for ax, a in zip(axes[0], archs):
        panel(ax, a, ceils[a], points, xs)
    axes[0][0].set_ylabel("Attainable performance (GFLOP/s)", color=INK2)
    fig.suptitle("Roofline from likwid-bench (median of 5 reps, full node)", fontsize=12, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(args.out, dpi=200, facecolor="white")

    out_csv = os.path.splitext(args.out)[0] + ".csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arch", "ceiling", "measured", "unit", "spec", "measured/spec"])
        for a in archs:
            t = spec(a)
            for k, v in ceils[a].items():
                unit = "GFLOP/s" if k in ("DP", "SP") else "GB/s"
                ref = t.get(k) if k in ("DP", "DRAM") else (2 * t["DP"] if k == "SP" else None)
                w.writerow([a, k, f"{v:.1f}", unit, f"{ref:.1f}" if ref else "", f"{v / ref:.2f}" if ref else ""])
            w.writerow([a, "ridge DP (FLOP/B)", f"{ceils[a]['DP'] / ceils[a]['DRAM']:.2f}", "",
                        f"{t['DP'] / t['DRAM']:.2f}", ""])
    print(open(out_csv).read())
    print(f"wrote {args.out} and {out_csv}")


if __name__ == "__main__":
    main()
