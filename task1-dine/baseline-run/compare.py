#!/usr/bin/env python3
"""Compare bench.py results with the theoretical peaks, per node and Dine vs Dine-2.

Usage: python3 compare.py [--results results]
Reads results/<arch>_<suite>_<job>.csv (+ .raw.txt for the likwid-perfctr CLOCK / FLOPS_DP runs,
+ .meta.json), prints markdown and writes it to results/comparison.md. Standard library only.
Formulas and hardware figures: see theoretical_peaks.md.
"""
import argparse, csv, glob, json, os, re, statistics
from collections import defaultdict

# Hardware figures for the theoretical peaks. spr values are UNVERIFIED (check with job_probe.sh).
# boost_ghz is the vendor max boost: an upper bound, reached on few cores; all-core load runs lower.
HW = {
    "zen2": dict(name="Dine (2x EPYC 7302)", sockets=2, cores=32, base_ghz=3.0, boost_ghz=3.3,
                 dp_per_cycle=16, channels=16, mts=3200),
    # laptop smoke test; bandwidth assumes ~LPDDR4x-4266 on a 128-bit bus, so treat as approximate
    "local": dict(name="Local (i5-1135G7, WSL2)", sockets=1, cores=4, base_ghz=2.4, boost_ghz=4.2,
                  dp_per_cycle=16, channels=2, mts=4266),
    # boost_ghz is a placeholder too: take "Max Turbo Frequency" from Intel ARK once the SKU is known
    "spr": dict(name="Dine-2 (Sapphire Rapids, unverified)", sockets=2, cores=64, base_ghz=2.0, boost_ghz=3.0,
                dp_per_cycle=32, channels=16, mts=4800),
}

# DRAM traffic / likwid-reported bytes for kernels with normal stores: each stored line is first read
# (write-allocate), which likwid doesn't count. Factor = (loads + 2*stores) / (loads + stores), with
# loads/stores per update as printed by likwid-bench: load 1/0, copy 1/1, stream 2/1, triad 3/1
# (likwid's triad is a = b + c*d, four streams, unlike STREAM's three). *_mem_* kernels use
# non-temporal stores and skip the read, so their factor is 1.
WA = {"load": 1.0, "copy": 3 / 2, "stream": 4 / 3, "triad": 5 / 4}
# Same for the STREAM benchmark, assuming the compiler emitted normal stores (gcc's default).
WA_STREAM = {"Copy": 3 / 2, "Scale": 3 / 2, "Add": 4 / 3, "Triad": 4 / 3}


def wa_factor(kernel):
    return 1.0 if "_mem" in kernel else WA[kernel.split("_")[0]]


def peak_gflops(hw, threads, ghz, sp):
    return threads * ghz * hw["dp_per_cycle"] * (2 if sp else 1)


def peak_gbs(hw, socket_only):
    bw = hw["channels"] * hw["mts"] * 8 / 1000
    return bw / hw["sockets"] if socket_only else bw


def stat(block, metric, col):
    """Value from a likwid-perfctr STAT table row: | <metric> STAT | Sum | Min | Max | Avg |, col 0..3."""
    m = re.search(r"\|\s*" + re.escape(metric) + r" STAT\s*\|" + r"\s*([0-9.]+)\s*\|" * 4, block)
    return float(m.group(col + 1)) if m else None


def counters(raw_text):
    """Measured clock (GHz) and the FLOPS_DP cross-check from the likwid-perfctr runs in a raw log."""
    c = {}
    for block in raw_text.split("\n$ "):
        if not block.startswith("likwid-perfctr"):
            continue
        if "-g CLOCK" in block:
            mhz = stat(block, "Clock [MHz]", 3)
            if mhz:
                c["ghz"] = mhz / 1000
        elif "-g FLOPS_DP" in block:
            ctr = stat(block, "DP [MFLOP/s]", 0)
            bench = re.search(r"MFlops/s:\s+([0-9.]+)", block)
            if ctr and bench:
                c["ctr_gflops"], c["bench_gflops"] = ctr / 1000, float(bench.group(1)) / 1000
                c["marker"] = " -m " in block
    return c


def load(results):
    """-> data[arch][suite][(phase, kernel, workgroup, threads, size)] = [values], ctrs[arch] = counters()"""
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    ctrs = {}
    for path in sorted(glob.glob(os.path.join(results, "*.csv"))):
        arch, suite = os.path.basename(path).split("_")[:2]
        if arch not in HW:
            continue
        metric = "mflops" if suite == "compute" else "mbyte"
        with open(path) as f:
            rows = list(csv.DictReader(f))
        if rows and "phase" not in rows[0]:
            # CSVs from before the phase column: rows are in run order, and the cache sweep starts at the
            # first load_* row with the first sweep size.
            meta = path[:-4] + ".meta.json"
            first = json.load(open(meta))["config"]["cache_sizes"][0] if os.path.exists(meta) else None
            phase = "peak" if suite == "compute" else "dram"
            for r in rows:
                if suite == "memory" and r["kernel"].startswith("load_") and r["size"] == first:
                    phase = "sweep"
                r["phase"] = phase
        for r in rows:
            if r[metric]:
                key = (r["phase"], r["kernel"], r["workgroup"], int(r["threads"]), r["size"])
                data[arch][suite][key].append(float(r[metric]) / 1000)   # -> GFLOP/s, GB/s
        raw = path[:-4] + ".raw.txt"
        if suite == "compute" and os.path.exists(raw):
            ctrs[arch] = counters(open(raw).read())
    return data, ctrs


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def report(data, ctrs, results):
    md, summary = [], {}
    for arch, hw in HW.items():
        if arch not in data:
            continue
        d, c = data[arch], ctrs.get(arch, {})
        ghz = c.get("ghz")
        md.append(f"## {hw['name']}\n")
        md.append(f"Clock: base {hw['base_ghz']} GHz, max boost {hw['boost_ghz']} GHz, measured under full-node DP load: "
                  f"{f'{ghz:.2f} GHz' if ghz else 'n/a (no CLOCK output found)'}\n")
        s = summary[arch] = {}

        rows = []
        for (ph, k, wg, t, size), v in sorted(d["compute"].items(), key=lambda x: (x[0][1], x[0][2][0] == "N", x[0][3])):
            sp = "_sp_" in k
            med = statistics.median(v)
            pk_base = peak_gflops(hw, t, hw["base_ghz"], sp)
            pk_boost = peak_gflops(hw, t, hw["boost_ghz"], sp)
            pk_meas = peak_gflops(hw, t, ghz, sp) if ghz else None
            rows.append([k, wg, t, f"{med:.1f}", f"{min(v):.1f}-{max(v):.1f}", f"{med / t:.1f}",
                         f"{pk_base:.0f}", f"{100 * med / pk_base:.0f}%", f"{pk_boost:.0f}", f"{100 * med / pk_boost:.0f}%",
                         f"{100 * med / pk_meas:.0f}%" if pk_meas else "-"])
            if wg.startswith("N:"):
                s["sp" if sp else "dp"] = med
        if rows:
            md.append("### Compute (peakflops)\n")
            md.append(table(["kernel", "workgroup", "threads", "GFLOP/s (median)", "min-max", "GFLOP/s/thread",
                             "peak @base", "% @base", "peak @boost", "% @boost", "% @measured clk"], rows) + "\n")
            md.append("Above 100 % @base means the cores were boosting; % @boost is a lower bound on efficiency, "
                      "since max boost is rarely held on all cores.\n")
        if "ctr_gflops" in c:
            s["ctr"] = c["ctr_gflops"]
            md.append(f"FLOPS_DP cross-check (full node, DP): hardware counters {c['ctr_gflops']:.1f} GFLOP/s, "
                      f"likwid-bench {c['bench_gflops']:.1f} GFLOP/s, ratio {c['ctr_gflops'] / c['bench_gflops']:.2f}"
                      + ("" if c["marker"] else " (no Marker API: counters include setup time, so expect < 1)") + "\n")
        elif d["compute"]:
            md.append("FLOPS_DP cross-check: n/a (no likwid-perfctr FLOPS_DP output; check MSR access)\n")

        rows = []
        for (ph, k, wg, t, size), v in sorted(d["memory"].items(), key=lambda x: x[0][1:]):
            if ph != "dram":
                continue
            med = statistics.median(v)
            traffic = med * wa_factor(k)
            pk = peak_gbs(hw, wg.startswith("S"))
            rows.append([k, wg, f"{med:.1f}", f"x{wa_factor(k):.2f}", f"{traffic:.1f}", f"{pk:.1f}", f"{100 * traffic / pk:.0f}%"])
            if wg.startswith("N:"):
                s["bw"] = max(s.get("bw", 0), traffic)
        if rows:
            md.append("### DRAM bandwidth\n")
            md.append(table(["kernel", "workgroup", "likwid GB/s", "write-allocate", "DRAM traffic GB/s",
                             "spec GB/s", "% of spec"], rows) + "\n")
            md.append("% of spec uses DRAM traffic: likwid's figure plus the write-allocate reads it doesn't count. "
                      "If a CPU avoids write-allocate in hardware (e.g. Sapphire Rapids, for some full-line stores), "
                      "the non-`_mem` rows over-state traffic; trust the `_mem` rows there.\n")

        for raw in glob.glob(os.path.join(results, f"{arch}_memory_*.raw.txt")):
            st = re.findall(r"^(Copy|Scale|Add|Triad):\s+([0-9.]+)", open(raw).read(), re.M)
            if st:
                pk = peak_gbs(hw, False)
                md.append("STREAM (best rate; DRAM traffic incl. write-allocate, % of spec): " + ", ".join(
                    f"{k} {float(v) / 1000:.1f} GB/s -> {float(v) / 1000 * WA_STREAM[k]:.1f} GB/s "
                    f"({100 * float(v) / 1000 * WA_STREAM[k] / pk:.0f}%)" for k, v in st) + "\n")

        sweep = defaultdict(dict)
        for (ph, k, wg, t, size), v in d["memory"].items():
            if ph == "sweep" and wg.startswith("N:"):
                sweep[size][k.split("_")[0]] = statistics.median(v)
        if sweep:
            mult = {"kB": 1e3, "MB": 1e6, "GB": 1e9}
            order = sorted(sweep, key=lambda z: float(z[:-2]) * mult[z[-2:]])
            md.append("### Cache sweep (full node)\n")
            md.append(table(["total size", "load GB/s", "triad GB/s"],
                            [[z] + [f"{sweep[z][k]:.0f}" if k in sweep[z] else "-" for k in ("load", "triad")]
                             for z in order]) + "\n")
            md.append("likwid-reported bytes (no write-allocate correction): in cache, the extra reads hit cache, not DRAM.\n")

    if summary:
        rows = []
        for arch, s in summary.items():
            hw = HW[arch]
            pk, bw = peak_gflops(hw, hw["cores"], hw["base_ghz"], False), peak_gbs(hw, False)
            pkb = peak_gflops(hw, hw["cores"], hw["boost_ghz"], False)
            rows.append([hw["name"], f"{pk:.0f}", f"{pkb:.0f}", f"{s.get('dp', 0):.0f}",
                         f"{s['ctr']:.0f}" if s.get("ctr") else "-", f"{bw:.0f}", f"{s.get('bw', 0):.0f}",
                         f"{pk / bw:.2f}", f"{s['dp'] / s['bw']:.2f}" if s.get("dp") and s.get("bw") else "-"])
        md.append("## Summary\n")
        md.append(table(["node", "peak DP @base", "peak DP @boost", "measured DP", "counter DP", "spec GB/s",
                         "best DRAM traffic GB/s", "balance spec (FLOP/B)", "balance measured (FLOP/B)"], rows) + "\n")
        if "zen2" in summary and "spr" in summary:
            a, b = summary["zen2"], summary["spr"]
            if a.get("dp") and b.get("dp"):
                md.append(f"Dine-2 / Dine: DP x{b['dp'] / a['dp']:.2f}"
                          + (f", bandwidth x{b['bw'] / a['bw']:.2f}" if a.get("bw") and b.get("bw") else "") + "\n")
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    args = ap.parse_args()
    md = "# likwid-bench: measured vs theoretical\n\n" + report(*load(args.results), args.results)
    print(md)
    with open(os.path.join(args.results, "comparison.md"), "w") as f:
        f.write(md)


if __name__ == "__main__":
    main()
