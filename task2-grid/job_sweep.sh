#!/bin/bash
#SBATCH -J grid-sweep
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 01:00:00
#SBATCH -o sweep_%j.out
#SBATCH -e sweep_%j.err


set -euo pipefail
ARCH=${1:?usage: job_sweep.sh zen2|spr [maxL] ["threads"] [reps] [close|spread]}
MAXL=${2:-32}
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
source ./env.sh "$ARCH"
[ -x "$BENCH" ] || { echo "no $BENCH: run job_build.sh $ARCH first"; exit 1; }
check_build_cpu

SOCKETS=$(lscpu | awk -F: '/^Socket\(s\)/{gsub(/ /,"",$2); print $2}')
CPS=$(lscpu | awk -F: '/^Core\(s\) per socket/{gsub(/ /,"",$2); print $2}')
CORES=$((SOCKETS * CPS))
DEFAULT_T=$(for t in 1 2 4 8 16 32 64 128 "$CPS" "$CORES"; do [ "$t" -le "$CORES" ] && echo "$t"; done | sort -nu | tr '\n' ' ')
THREADS=${3:-$DEFAULT_T}
REPS=${4:-1}
BIND=${5:-close}  
OUT=results/$ARCH/sweep_L${MAXL}_${BIND}
mkdir -p "$OUT"


LAUNCH=${LAUNCH:-}

FLAGS="--no-benchmark-flops-fp64 --no-benchmark-flops-sp4-2as --no-benchmark-flops-sp4-f
       --no-benchmark-flops-su4 --mpi 1.1.1.1 --max-L $MAXL ${EXTRA_ARGS:-}"

{
  echo "date: $(date -Is)"; echo "host: $(hostname)"; echo "job: ${SLURM_JOB_ID:-local}"
  echo "arch: $ARCH  simd: $SIMD  cores: $CORES ($SOCKETS x $CPS)  bind: $BIND  threads: $THREADS  reps: $REPS"
  echo "binary: $BENCH"; echo "flags: $FLAGS"; echo "launch: ${LAUNCH:-direct}"
  echo "grid commit: $GRID_COMMIT  grid-benchmark commit: $GB_COMMIT"
  lscpu; numactl -H 2>/dev/null || true
  command -v module >/dev/null && module list 2>&1 || true
} > "$OUT/meta_${SLURM_JOB_ID:-local}.txt"

for T in $THREADS; do
  for R in $(seq 1 "$REPS"); do
    tag="t${T}_r${R}_${SLURM_JOB_ID:-local}"

    if [ "$T" -gt "$CORES" ]; then places=threads; else places=cores; fi
    echo "== T=$T rep $R (OMP_PLACES=$places OMP_PROC_BIND=$BIND) $(date +%T)"
    t0=$(date +%s)

    OMP_NUM_THREADS=$T OMP_PLACES=$places OMP_PROC_BIND=$BIND OMP_DISPLAY_AFFINITY=true \
    OMP_AFFINITY_FORMAT="affinity: thread %0.3n on cpus %A" \
      $LAUNCH "$BENCH" $FLAGS --json-out "$OUT/$tag.json" > "$OUT/$tag.log" 2>&1 \
      || echo "WARN: T=$T rep $R exited $? (see $OUT/$tag.log)"
    grep -m1 "OpenMP threads" "$OUT/$tag.log" || echo "WARN: no 'OpenMP threads' line in $tag.log"
    grep -i "Failed to validate" "$OUT/$tag.log" && echo "WARN: Wilson correctness check FAILED for T=$T"
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('   comparison point (avg DWF L=24,32): %.1f Gflop/s' % d['flops']['comparison_point_Gflops'])" \
      "$OUT/$tag.json" 2>/dev/null || true
    [ -s "$OUT/$tag.json" ] || echo "WARN: no JSON for T=$T (killed?) - grid_parse.py will use the log"
    echo "   took $(( $(date +%s) - t0 )) s"
  done
done
echo "== sweep done -> $OUT   (next: python3 grid_parse.py results && python3 grid_plot.py results)"
