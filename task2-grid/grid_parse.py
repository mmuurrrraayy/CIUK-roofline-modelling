#!/usr/bin/env python3
"""Collect Benchmark_Grid sweep + likwid results into CSVs and a comparison table.

Usage: python3 grid_parse.py [results] [--task1 "../task1-dine/final-run/results"]
Reads   results/<arch>/sweep_*/t<T>_r<R>_<job>.{json,log}       (job_sweep.sh)
        results/<arch>/likwid_<mode>/t<T>_<GROUP>_<job>.{csv,json,log}  (job_likwid.sh)
Writes  results/grid_flops.csv    one row per (run, action, L): best (JSON) and mean-of-4-variants (log);
                                  runs without a JSON (killed at the time limit) are recovered from the log
        results/grid_memory.csv   Grid's own axpy / SU4 bandwidth tests
        results/likwid.csv        every likwid metric, aggregated over hwthreads, per region
        results/likwid_summary.md Grid vs likwid table (+ Task 1 STREAM / peak references)
Units: Grid flops are base 10; Grid's GBps is base 2 (GiB/s), converted here to GB/s (1e9) to match
likwid (MBytes = 1e6 B). Standard library only.
"""
import argparse, csv, glob, json, os, re, statistics
from collections import defaultdict

GIB = 1024 ** 3 / 1e9
LS = {"wilson": 1, "dwf": 12, "staggered": 1}   # 5th-dimension extent used in Grid's flop count
NCALL = 500                                     # timed DhopEO calls per kernel variant (DeoFlops.hpp)


def run_info(path):
    """'.../<arch>/<campaign>/t16_r2_123.json' -> dict(arch, campaign, threads, rep, job)."""
    campaign_dir = os.path.dirname(path)
    m = re.match(r"t(\d+)_(?:r(\d+)_)?(?:([A-Z][A-Z0-9_]*)_)?(\d+|local)\.\w+$", os.path.basename(path))
    return dict(arch=os.path.basename(os.path.dirname(campaign_dir)), campaign=os.path.basename(campaign_dir),
                threads=int(m.group(1)), rep=int(m.group(2) or 1), group=m.group(3) or "", job=m.group(4))


def parse_log(path):
    """Per-variant Deo Gflop/s and flops/site from the Grid log: {(action, L): dict(cases=[...], fps=...)},
    plus the thread count Grid reported (to catch OMP_NUM_THREADS not taking effect)."""
    out, action, L, threads = defaultdict(lambda: {"cases": []}), None, None, None
    if not os.path.exists(path):
        return out, None
    for line in open(path, errors="replace"):
        m = re.search(r"OpenMP threads\s*:\s*(\d+)", line)
        if m and threads is None:
            threads = int(m.group(1))
        if "dslash 4d vectorised" in line:
            action = None
            if "SU(3) fundamental" in line and "fp32" in line:
                action = ("dwf" if "Domain wall" in line else "staggered" if "Staggered" in line
                          else "wilson" if "Wilson" in line else None)
        m = re.search(r"on (\d+)\^4 local volume", line)
        if m:
            L = int(m.group(1))
        if action and L:
            m = re.search(r"Deo FlopsPerSite is\s+([0-9.eE+]+)", line)
            if m:
                out[(action, L)]["fps"] = float(m.group(1))
            m = re.search(r"Deo Gflop/s =\s+([0-9.]+)", line)
            if m:
                out[(action, L)]["cases"].append(float(m.group(1)))
    return out, threads


def grid_rows(json_path):
    """Flops and memory rows for one Benchmark_Grid run (JSON + matching .log)."""
    info = run_info(json_path)
    d = json.load(open(json_path))
    log, t_grid = parse_log(json_path[:-5] + ".log")
    if t_grid is not None and t_grid != info["threads"]:
        print(f"WARN: {json_path}: file says {info['threads']} threads, Grid used {t_grid}")
    flops = []
    for r in d.get("flops", {}).get("results", []):
        for key, action in (("Gflops_wilson", "wilson"), ("Gflops_dwf4", "dwf"), ("Gflops_staggered", "staggered")):
            if key in r:
                lg = log.get((action, r["L"]), {})
                flops.append(dict(info, L=r["L"], action=action, gflops_best=r[key],
                                  gflops_mean4=statistics.mean(lg["cases"]) if lg.get("cases") else "",
                                  fps=lg.get("fps", ""), source="json"))
    mem = [dict(info, bench=b, L=r["L"], size_MB=r["size_MB"], GBs=r["GBps"] * GIB, gflops=r["GFlops"])
           for b in ("axpy", "SU4") for r in d.get(b, [])]
    return flops, mem


def log_rows(log_path):
    """Flops rows from the log alone, for a run killed (e.g. by the 1 h limit) before Grid wrote its JSON.
    Grid's JSON value is the best of the 4 kernel variants, so best = max over the 4 logged cases (the
    log prints 1 decimal). (action, L) points with fewer than 4 cases were cut off mid-way and dropped."""
    info = run_info(log_path)
    log, t_grid = parse_log(log_path)
    if t_grid is not None and t_grid != info["threads"]:
        print(f"WARN: {log_path}: file says {info['threads']} threads, Grid used {t_grid}")
    rows, cut = [], []
    for (action, L), lg in sorted(log.items()):
        if len(lg["cases"]) < 4:
            cut.append(f"{action} L={L}")
            continue
        rows.append(dict(info, L=L, action=action, gflops_best=max(lg["cases"]),
                         gflops_mean4=statistics.mean(lg["cases"]), fps=lg.get("fps", ""), source="log"))
    print(f"NOTE: {log_path}: no JSON, recovered {len(rows)} (action, L) points from the log"
          + (f"; incomplete: {', '.join(cut)}" if cut else ""))
    return rows


def cells(line):
    """Split a likwid output line, either text ('| a | b |') or CSV ('a,b')."""
    line = line.strip()
    if line.startswith("|"):
        return [c.strip() for c in line.strip("|").split("|")]
    return [c.strip() for c in line.split(",")] if "," in line else []


def agg(name, vals):
    """Combine one metric over hwthreads the way likwid's STAT 'Sum' would for rates and volumes."""
    if re.search(r"MFLOP/s|MBytes/s|MUOPS/s|GBytes|MBytes\]", name):
        return sum(vals)
    if name.startswith("Runtime"):
        return max(vals)
    nz = [v for v in vals if v]
    return statistics.mean(nz) if nz else 0.0


def parse_likwid(path):
    """{region: {metric: value}} from a likwid-perfctr -o file (.csv or .txt). Only per-hwthread
    'Metric' tables are read (header 'Metric, HWThread 0, ...'); STAT tables are recomputed by agg()
    because they're absent when a single hwthread is measured. 'whole_run' if there are no markers."""
    res, region, header = defaultdict(dict), "whole_run", None
    for line in open(path, errors="replace"):
        m = re.search(r"Region (?!Info\b)([A-Za-z0-9_.\-]+)", line)
        if m:
            region, header = m.group(1), None
            continue
        c = cells(line)
        if not c:
            continue
        if c[0] in ("TABLE", "STRUCT", "Event", "Region Info") or c[0].startswith("+"):
            header = None
        if c[0] == "Metric":
            header = c if len(c) > 1 and re.match(r"(HWThread|Core|CPU)", c[1]) else None
            continue
        if header and len(c) >= len(header):
            vals = []
            for v in c[1:len(header)]:
                try:
                    vals.append(float(v))
                except ValueError:
                    pass                      # '-', 'nan' on hwthreads with no data
            if vals:
                res[region][c[0]] = agg(c[0], vals)
    return res


def pick(metrics, *patterns):
    for p in patterns:
        for k, v in metrics.items():
            if re.fullmatch(p, k):
                return v
    return None


def task1_refs(task1_dir, arch):
    """Node DRAM bandwidth (likwid triad_mem, GB/s) and SP peak (GFlop/s) from Task 1's bench.py CSVs."""
    rows = []
    for f in glob.glob(os.path.join(task1_dir, f"{arch}_*.csv")):
        rows += list(csv.DictReader(open(f)))
    med = lambda sel, col: (statistics.median(float(r[col]) for r in sel) if sel else None)
    node = [r for r in rows if r["workgroup"].startswith("N:")]
    bw = med([r for r in node if r["kernel"].startswith("triad_mem") and r["mbyte"]], "mbyte")
    pk = med([r for r in node if r["kernel"].startswith("peakflops_sp") and r["mflops"]], "mflops")
    return dict(dram_GBs=bw / 1e3 if bw else None, peak_sp_GFs=pk / 1e3 if pk else None)


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?", default="results")
    ap.add_argument("--task1", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "..", "task1-dine", "final-run", "results"))
    args = ap.parse_args()

    flops, mem = [], []
    for lg in sorted(glob.glob(os.path.join(args.results, "*", "sweep_*", "t*.log"))):
        j = lg[:-4] + ".json"
        try:
            f, m = grid_rows(j)
        except (OSError, json.JSONDecodeError, KeyError):
            f, m = log_rows(lg), []           # killed before the JSON was written: use the log
        flops += f
        mem += m
    write_csv(os.path.join(args.results, "grid_flops.csv"), flops)
    write_csv(os.path.join(args.results, "grid_memory.csv"), mem)

    # likwid: one file per (threads, group); merge groups of the same (arch, mode, threads, region).
    lk_rows, merged = [], defaultdict(dict)
    for c in sorted(glob.glob(os.path.join(args.results, "*", "likwid_*", "t*.csv"))):
        info = run_info(c)
        gridlog, _ = parse_log(c[:-4] + ".log")
        for region, metrics in parse_likwid(c).items():
            m = re.fullmatch(r"(wilson|dwf|staggered)_SU3_\w+?_L(\d+)", region)
            action, L = (m.group(1), int(m.group(2))) if m else (region, "")
            for k, v in metrics.items():
                lk_rows.append(dict(arch=info["arch"], mode=info["campaign"], threads=info["threads"],
                                    group=info["group"], region=region, action=action, L=L, metric=k, value=v))
            row = merged[(info["arch"], info["campaign"], info["threads"], region)]
            row.update(arch=info["arch"], mode=info["campaign"].replace("likwid_", ""),
                       threads=info["threads"], action=action, L=L)
            rt = pick(metrics, r"Runtime \(RDTSC\) \[s\]")
            hw = pick(metrics, r"SP \[?MFLOP/s\]?", r"DP \[?MFLOP/s\]?")
            bw = pick(metrics, r"Memory bandwidth \[MBytes/s\]", r"Memory bandwidth.*MBytes/s.*")
            ck = pick(metrics, r"Clock \[MHz\]")
            if hw is not None:
                row["hw_GFs"] = hw / 1e3
            if bw is not None:
                row["dram_GBs"] = bw / 1e3
            if ck is not None:
                row["clock_GHz"] = ck / 1e3
            lg = gridlog.get((action, L), {})
            if lg.get("cases"):
                row.setdefault("grid_mean4_GFs", statistics.mean(lg["cases"]))
            # Grid's nominal flops over the region's own runtime: should match grid_mean4_GFs if the
            # markers wrap exactly the timed loop (4 variants x 500 calls, half the sites per call).
            if rt and lg.get("fps") and isinstance(L, int):
                calls = 4 * NCALL
                row.setdefault("nominal_from_region_GFs", lg["fps"] * L ** 4 * LS[action] / 2 * calls / rt / 1e9)
    write_csv(os.path.join(args.results, "likwid.csv"), lk_rows)

    summary = sorted(merged.values(), key=lambda r: (r["arch"], r["mode"], str(r["action"]), str(r["L"]), r["threads"]))
    if summary:
        lines = ["# Grid vs likwid\n",
                 "Grid Gflop/s = mean of the 4 kernel variants (what a marker region averages over), nominal flop count.",
                 "HW = likwid FLOPS group (executed SP+DP flops on Zen2, which can't split precision).",
                 "AI = Grid nominal flops per DRAM byte. Bandwidths in GB/s (1e9 B/s).\n"]
        fmt = lambda v, p=1: f"{v:.{p}f}" if isinstance(v, (int, float)) else "-"
        refs = {a: task1_refs(args.task1, a) for a in {r["arch"] for r in summary}}
        for a, r in refs.items():
            lines.append(f"- {a}: Task 1 node DRAM triad_mem = {fmt(r['dram_GBs'])} GB/s, "
                         f"SP peak = {fmt(r['peak_sp_GFs'])} GFlop/s (from {args.task1})")
        lines += ["", "| arch | mode | region | T | Grid Gflop/s | nominal (region time) | HW GFlop/s | HW/Grid "
                  "| DRAM GB/s | % of Task 1 DRAM | AI flop/B | clock GHz |", "|" + "---|" * 12]
        for r in summary:
            g, hw, bw = r.get("grid_mean4_GFs"), r.get("hw_GFs"), r.get("dram_GBs")
            ref = refs[r["arch"]]["dram_GBs"]
            lines.append("| " + " | ".join([
                r["arch"], r["mode"], f"{r['action']} L={r['L']}", str(r["threads"]), fmt(g),
                fmt(r.get("nominal_from_region_GFs")), fmt(hw), fmt(hw / g if hw and g else None, 2), fmt(bw),
                fmt(100 * bw / ref if bw and ref else None, 0), fmt(g / bw if g and bw else None, 2),
                fmt(r.get("clock_GHz"), 2)]) + " |")
        p = os.path.join(args.results, "likwid_summary.md")
        open(p, "w").write("\n".join(lines) + "\n")
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
