#!/usr/bin/env python3
"""Strong-scaling plots from the Benchmark_Grid thread sweeps.

Usage: python3 grid_plot.py [results]
Reads  results/<arch>/sweep_L*_close/t<T>_r<R>_<job>.json   (job_sweep.sh)
Writes results/grid_strong_scaling.csv   arch, action, L, threads, gflops, speedup, efficiency
       results/grid_strong_scaling.png   rows = L (24, 32), columns = action; Gflop/s vs threads (log-log)
       results/grid_speedup.png          same grid, speedup vs 1 thread with the ideal line
Every sweep run executes all L <= --max-L, so the L=24 data come from the same runs as L=32.
Values are Grid's own reported Gflop/s (best of the 4 kernel variants).
"""
import csv, glob, json, os, re, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = sys.argv[1] if len(sys.argv) > 1 else "results"
NODE = {"zen2": ("Dine (2x EPYC 7302, 32 cores)", "#2a6fbb", 32), "spr": ("Dine-2 (2x Xeon 6430, 64 cores)", "#d9731a", 64)}
KEY = {"wilson": "Gflops_wilson", "dwf": "Gflops_dwf4", "staggered": "Gflops_staggered"}
TITLE = {"wilson": "Wilson", "dwf": "Domain-wall (DWF, Ls=12)", "staggered": "Staggered"}
LS = (24, 32)

data = {}   # (arch, action, L, T) -> Gflop/s
for p in sorted(glob.glob(f"{root}/*/sweep_L32_close/t*_r*_*.json")):
    arch = os.path.basename(os.path.dirname(os.path.dirname(p)))
    T = int(re.match(r"t(\d+)_", os.path.basename(p)).group(1))
    for r in json.load(open(p))["flops"]["results"]:
        for a, k in KEY.items():
            if k in r:
                data[(arch, a, r["L"], T)] = r[k]

rows = []
for (arch, a, L, T), g in sorted(data.items()):
    base = data.get((arch, a, L, 1))
    if base:
        rows.append(dict(arch=arch, action=a, L=L, threads=T, gflops=round(g, 2), speedup=round(g / base, 2),
                         efficiency=round(g / base / T, 3)))
with open(f"{root}/grid_strong_scaling.csv", "w", newline="") as f:
    w = csv.DictWriter(f, rows[0].keys()); w.writeheader(); w.writerows(rows)


def draw(metric, ylabel, fname, ideal):
    fig, axs = plt.subplots(len(LS), 3, figsize=(13, 7.5), sharex=True)
    for i, L in enumerate(LS):
        for j, a in enumerate(KEY):
            ax = axs[i][j]
            for arch, (label, col, cores) in NODE.items():
                pts = sorted((r["threads"], r[metric]) for r in rows if r["arch"] == arch and r["action"] == a and r["L"] == L)
                if pts:
                    ax.plot(*zip(*pts), "o-", color=col, lw=2, ms=6, label=label, mfc=col, mec="white", mew=1.2)
                    ax.axvline(cores, color=col, ls=":", lw=1, alpha=.5)
            if ideal:
                ax.plot([1, 64], [1, 64], color="#888", ls="--", lw=1, label="ideal")
            ax.set_xscale("log", base=2); ax.set_yscale("log", base=2)
            ax.set_xticks([1, 2, 4, 8, 16, 32, 64]); ax.set_xticklabels([1, 2, 4, 8, 16, 32, 64])
            ax.set_title(f"{TITLE[a]}, L={L}", fontsize=10); ax.grid(alpha=.25)
            if j == 0: ax.set_ylabel(ylabel)
            if i == len(LS) - 1: ax.set_xlabel("OpenMP threads")
            for s in ("top", "right"): ax.spines[s].set_visible(False)
    h, l = axs[0][0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=3, frameon=False)
    fig.text(0.5, 0.005, "dotted vertical lines: full-node physical core count (one socket holds 16 cores on Dine, 32 on Dine-2)",
             ha="center", fontsize=8, color="#555")
    fig.tight_layout(rect=(0, 0.02, 1, 0.94)); fig.savefig(f"{root}/{fname}", dpi=150); plt.close(fig)


draw("gflops", "Grid-reported Gflop/s", "grid_strong_scaling.png", False)
draw("speedup", "speedup vs 1 thread", "grid_speedup.png", True)
print(f"{len(rows)} points -> {root}/grid_strong_scaling.{{csv,png}}, grid_speedup.png")
