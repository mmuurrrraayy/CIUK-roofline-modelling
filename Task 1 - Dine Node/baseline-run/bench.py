re#!/usr/bin/env python3
"""Run likwid-bench sweeps on a Durham testbed node, save raw output + a CSV of parsed results.

Usage: python3 bench.py --arch zen2|spr --suite compute|memory [--reps 5] [--out results]
  zen2 = Dine   (2x EPYC 7302, AVX2)
  spr  = Dine-2 (Sapphire Rapids, 64 cores, AVX-512)
Standard library only.
"""
import argparse, csv, datetime, json, os, re, statistics, subprocess

# Per-machine settings. Verify against likwid-topology / `likwid-bench -a` on the node.
ARCHS = {
    "zen2": dict(
        cores=32, per_socket=16, isa="avx",           # likwid-bench kernel suffix: *_avx
        peak_dp="peakflops_avx_fma", peak_sp="peakflops_sp_avx_fma",
        bw_suffix="avx",
        dram_node="4GB", dram_socket="2GB",
        cache_sizes=["256kB", "512kB", "1MB", "4MB", "8MB", "16MB", "32MB", "64MB", "128MB", "256MB", "512MB", "1GB", "4GB"],
    ),
    # laptop smoke test only (WSL2, i5-1135G7, 4 cores): AVX2 kernels, small arrays
    "local": dict(
        cores=4, per_socket=4, isa="avx",
        peak_dp="peakflops_avx_fma", peak_sp="peakflops_sp_avx_fma",
        bw_suffix="avx",
        dram_node="1GB", dram_socket="1GB",
        cache_sizes=["32kB", "256kB", "1MB", "4MB", "8MB", "32MB", "128MB", "1GB"],
    ),
    "spr": dict(
        cores=64, per_socket=32, isa="avx512",
        peak_dp="peakflops_avx512_fma", peak_sp="peakflops_sp_avx512_fma",
        bw_suffix="avx512",
        dram_node="8GB", dram_socket="4GB",
        cache_sizes=["2MB", "8MB", "32MB", "64MB", "128MB", "256MB", "512MB", "1GB", "4GB", "8GB"],
    ),
}


def sh(cmd, raw, check=True):
    """Run a command, append its output to the raw log, return stdout."""
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    raw.write(f"\n$ {cmd}\n(exit {p.returncode})\n{p.stdout}{p.stderr}")
    raw.flush()
    if check and p.returncode:
        print(f"WARN: '{cmd}' exited {p.returncode}")
    return p.stdout


def parse_topo(out):
    """hwthread -> (socket, core) from the likwid-topology 'HWThread Thread Core [Die] Socket Available' table."""
    topo = {}
    for l in out.splitlines():
        cols = l.split()
        if len(cols) >= 5 and cols[-1] == "*" and all(c.isdigit() for c in cols[:-1]):
            topo[int(cols[0])] = (int(cols[-2]), int(cols[2]))
    return topo


def hwthreads(out):
    """hwthreads likwid-bench put its worker threads on (from 'Global Thread N running on hwthread M')."""
    return [int(c) for c in re.findall(r"Global Thread \d+ running on hwthread (\d+)", out)]


def shared_core(a, cpus):
    """True if two of the hwthreads are SMT siblings on the same physical core."""
    cores = [a["topo"].get(c, c) for c in cpus]
    return len(set(cores)) < len(cores)


def calibrate_pin(a, raw, tpc):
    """Find the -W suffix that puts one thread per physical core. likwid's order of hwthreads within a
    domain differs between systems (siblings adjacent, or all physical cores first), so try no stride,
    then a stride of threads-per-core, and keep the first that gives distinct cores."""
    if not a["topo"]:
        print("WARN: could not parse likwid-topology; thread placement is unchecked")
        return ""
    dom, n = ("S0" if a["cores"] > a["per_socket"] else "N"), a["per_socket"]
    for pin in ["", f":1:{tpc}"] if tpc and tpc > 1 else [""]:
        cpus = hwthreads(sh(f"likwid-bench -t {a['peak_dp']} -i 1 -W {dom}:{16 * n}kB:{n}{pin}", raw))
        if len(cpus) == n and not shared_core(a, cpus):
            print(f"pinning: -W {dom}:...:{n}{pin} -> hwthreads {sorted(cpus)}, one per physical core")
            return pin
    print("WARN: no -W stride gave one thread per physical core; results will include SMT sharing")
    return ""


def verify(a, raw):
    """Warn if the ARCHS entry doesn't match this node. Returns (CPU model name, threads per core)."""
    info = {k.strip(): v.strip() for k, v in
            (l.split(":", 1) for l in sh("lscpu", raw).splitlines() if ":" in l)}
    num = lambda k: int(info[k]) if info.get(k, "").isdigit() else None
    sockets, cps, tpc = num("Socket(s)"), num("Core(s) per socket"), num("Thread(s) per core")
    if sockets and cps and (sockets * cps != a["cores"] or cps != a["per_socket"]):
        print(f"WARN: config says {a['cores']} cores / {a['per_socket']} per socket, "
              f"lscpu says {sockets} x {cps}. Fix ARCHS before trusting results.")
    if tpc and tpc > 1:
        print(f"NOTE: SMT is on ({tpc} threads/core); benchmarks use {a['cores']} threads, one per physical core")
    kernels = sh("likwid-bench -a", raw, check=False)
    for k in (a["peak_dp"], a["peak_sp"], f"triad_mem_{a['bw_suffix']}"):
        if not re.search(rf"^\s*{re.escape(k)}\s", kernels, re.M):
            print(f"WARN: kernel {k} not listed by likwid-bench -a")
    model = info.get("Model name", "?")
    print(f"CPU: {model}, {sockets} sockets x {cps} cores, {tpc} threads/core")
    return model, tpc


def parse(out):
    """Pull the headline numbers from likwid-bench output (lines like 'MFlops/s:   1234.5')."""
    vals = {}
    for key, name in [("MFlops/s", "mflops"), ("MByte/s", "mbyte"), ("Time", "time_s")]:
        m = re.search(re.escape(key) + r":?\s+([0-9.eE+-]+)", out)
        if m:
            vals[name] = float(m.group(1))
    return vals


def size_kb(size):
    """'1GB' -> 1000000 (likwid sizes are decimal: 16kB = 16000 B)."""
    num, unit = re.fullmatch(r"(\d+)(kB|MB|GB)", size).groups()
    return int(num) * {"kB": 1, "MB": 1000, "GB": 1000000}[unit]


def node_wg(a, size, threads):
    """Whole-node workgroup as one -W per socket, so placement doesn't depend on how likwid orders
    its N domain. `size` is the node total, split evenly (in kB, so 1GB doesn't round down to 0GB);
    threads likewise. a['pin'] (from calibrate_pin) keeps one thread per physical core."""
    sk = a["cores"] // a["per_socket"]
    if sk == 1:
        return f"N:{size}:{threads}{a['pin']}"
    return " -W ".join(f"S{i}:{size_kb(size) // sk}kB:{threads // sk}{a['pin']}" for i in range(sk))


def point(writer, raw, a, phase, kernel, workgroup, threads, size, reps, metric, wg=None):
    """Run one likwid-bench configuration `reps` times, write one CSV row per rep.
    `workgroup` is the label stored in the CSV; `wg` (default: label + pin suffix) is what is passed to -W.
    `phase` (peak/dram/sweep) tells compare.py which table the row belongs to.
    `metric` ("mflops" or "mbyte") is the value summarised on stdout. Returns the hwthreads used."""
    runs, cpus = [], []
    for r in range(reps):
        out = sh(f"likwid-bench -t {kernel} -W {wg or workgroup + a['pin']}", raw)
        cpus = hwthreads(out)
        if shared_core(a, cpus):
            print(f"WARN: {kernel} {workgroup} put threads on SMT siblings: hwthreads {sorted(cpus)}")
        v = parse(out)
        if not v:
            print(f"WARN: nothing parsed for {kernel} {workgroup}")
            continue
        writer.writerow({"phase": phase, "kernel": kernel, "workgroup": workgroup, "threads": threads,
                         "size": size, "rep": r, **v})
        runs.append(v.get(metric, 0))
    if runs:
        print(f"{kernel:24s} {workgroup:16s} median={statistics.median(runs):12.1f} "
              f"min={min(runs):.1f} max={max(runs):.1f}")
    return cpus


def compute(w, raw, reps, a):
    # 1, 2, 4, ... up to one socket on S0, then the whole node. 16 kB/thread stays in L1.
    steps = [t for t in (1, 2, 4, 8, 16, 32) if t < a["per_socket"]] + [a["per_socket"]]
    n = a["cores"]
    for kern in (a["peak_dp"], a["peak_sp"]):
        for t in steps:
            point(w, raw, a, "peak", kern, f"S0:{16 * t}kB:{t}", t, f"{16 * t}kB", reps, "mflops")
        used = point(w, raw, a, "peak", kern, f"N:{16 * n}kB:{n}", n, f"{16 * n}kB", reps, "mflops",
                     wg=node_wg(a, f"{16 * n}kB", n))
        if kern == a["peak_dp"]:
            cpus = ",".join(map(str, sorted(used))) or f"N:0-{n - 1}"
    # Clock / FLOP counters around the full-node DP run, on the hwthreads it actually used (needs MSR
    # access, see job_probe.sh). -m on both sides (Marker API) restricts counting to the benchmark loop,
    # so allocation/initialisation don't dilute the rate; compare.py checks it against likwid-bench's own MFlops/s.
    for group in ("CLOCK", "FLOPS_DP"):
        sh(f"likwid-perfctr -m -c {cpus} -g {group} likwid-bench -m -t {a['peak_dp']} "
           f"-W {node_wg(a, f'{16 * n}kB', n)}", raw)


def memory(w, raw, reps, a):
    n, s, b = a["cores"], a["per_socket"], a["bw_suffix"]
    # *_mem_* kernels use non-temporal stores: no write-allocate, comparable to STREAM with streaming stores.
    for kern in (f"load_{b}", f"copy_{b}", f"stream_{b}", f"triad_{b}",
                 f"copy_mem_{b}", f"stream_mem_{b}", f"triad_mem_{b}"):
        point(w, raw, a, "dram", kern, f"N:{a['dram_node']}:{n}", n, a["dram_node"], reps, "mbyte",
              wg=node_wg(a, a["dram_node"], n))
        point(w, raw, a, "dram", kern, f"S0:{a['dram_socket']}:{s}", s, a["dram_socket"], reps, "mbyte")
    if os.access("stream/stream", os.X_OK):
        out = sh(f"OMP_NUM_THREADS={n} OMP_PROC_BIND=spread OMP_PLACES=cores stream/stream", raw)
        print("STREAM best MB/s: " + ", ".join(f"{k}={v}" for k, v in
              re.findall(r"^(Copy|Scale|Add|Triad):\s+([0-9.]+)", out, re.M)))
    else:
        print("no stream/stream binary, skipping STREAM")
    for kern in (f"load_{b}", f"triad_{b}"):
        for sz in a["cache_sizes"]:
            point(w, raw, a, "sweep", kern, f"N:{sz}:{n}", n, sz, reps, "mbyte", wg=node_wg(a, sz, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=list(ARCHS), required=True)
    ap.add_argument("--suite", choices=["compute", "memory"], required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    tag = f"{args.arch}_{args.suite}_{os.environ.get('SLURM_JOB_ID', 'local')}"
    with open(f"{args.out}/{tag}.raw.txt", "w") as raw:
        a = dict(ARCHS[args.arch], topo=parse_topo(sh("hostname; date; lscpu; likwid-topology -c -g", raw, check=False)))
        model, tpc = verify(a, raw)
        a["pin"] = calibrate_pin(a, raw, tpc)
        meta = {"arch": args.arch, "config": {k: v for k, v in a.items() if k != "topo"},
                "suite": args.suite, "reps": args.reps, "cpu": model,
                "date": datetime.datetime.now().isoformat(),
                "host": sh("hostname", raw).strip(),
                "likwid": sh("likwid-perfctr -v 2>&1", raw, check=False).strip()}
        with open(f"{args.out}/{tag}.meta.json", "w") as mf:
            json.dump(meta, mf, indent=2)
        with open(f"{args.out}/{tag}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, ["phase", "kernel", "workgroup", "threads", "size", "rep",
                                   "mflops", "mbyte", "time_s"])
            w.writeheader()
            (compute if args.suite == "compute" else memory)(w, raw, args.reps, a)


if __name__ == "__main__":
    main()
