#!/bin/bash
#SBATCH -J grid-likwid
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 01:00:00
#SBATCH -o likwid_%j.out
#SBATCH -e likwid_%j.err

# Hardware counters on selected Benchmark_Grid configurations.
# Jobs are capped at 1 h, so submit one (threads, group) per job with submit_likwid.sh:
#   bash submit_likwid.sh zen2 marker "1 16 32" 32 "MEM FLOPS_SP"
# or directly: sbatch -p <partition> -A <account> job_likwid.sh <zen2|spr> [marker|whole] [threads] [maxL] [group]
# marker: instrumented build (likwid-markers.patch) - one region per action and L (dwf_SU3_SINGLE_L32,
#         wilson_..., staggered_...) around exactly the timed 500-call loop, so the counter rates are
#         directly comparable with Grid's Gflop/s for that L. Grid's memory/SU4/comms tests and the
#         Wilson check are skipped (they are outside every region) to stay inside the hour.
# whole : plain build, the full UKNNSS command, counters over the entire run (setup, axpy/SU4 memory
#         tests, every L, ...); only a rough average - shown to illustrate why markers matter.
# One likwid run per group (MEM and FLOPS_SP need different counters; likwid can multiplex groups with
# -T, but separate runs keep each group's numbers exact). Outputs: results/<arch>/likwid_<mode>/
set -euo pipefail
ARCH=${1:?usage: job_likwid.sh zen2|spr [marker|whole] ["threads"] [maxL] ["groups"]}
MODE=${2:-marker}
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
source ./env.sh "$ARCH"
case $MODE in
  marker) BIN=$BENCH_LIKWID; M=-m; SKIP="--no-benchmark-memory --no-benchmark-su4 --no-benchmark-comms --no-check-wilson" ;;
  whole)  BIN=$BENCH; M=; SKIP= ;;
  *) echo "mode must be marker or whole"; exit 1 ;;
esac
[ -x "$BIN" ] || { echo "no $BIN: run job_build.sh $ARCH first (needs likwid for the marker build)"; exit 1; }
check_build_cpu
command -v likwid-perfctr >/dev/null || { echo "likwid-perfctr not on PATH: the $LIKWID_MODULE module failed to load (compiler module must load first, see env.sh)"; exit 1; }


CPUS=$(lscpu -p=CPU,CORE,SOCKET | grep -v '^#' | sort -t, -k2,2n -k1,1n | awk -F, '!seen[$2]++ {print $3","$1}' \
       | sort -t, -k1,1n -k2,2n | cut -d, -f2 | paste -sd,)
CORES=$(echo "$CPUS" | tr ',' '\n' | wc -l)
CPS=$(lscpu | awk -F: '/^Core\(s\) per socket/{gsub(/ /,"",$2); print $2}')
THREADS=${3:-"1 $CPS $CORES"}
MAXL=${4:-32}
GROUPS_WANTED=${5:-"MEM FLOPS_SP"}
OUT=results/$ARCH/likwid_$MODE
mkdir -p "$OUT"

# Threads are pinned by OpenMP to ONE hwthread each
AVAIL=$(likwid-perfctr -a 2>/dev/null || true)
FLAGS="--no-benchmark-flops-fp64 --no-benchmark-flops-sp4-2as --no-benchmark-flops-sp4-f
       --no-benchmark-flops-su4 --mpi 1.1.1.1 --max-L $MAXL $SKIP ${EXTRA_ARGS:-}"

{
  echo "date: $(date -Is)"; echo "host: $(hostname)"; echo "mode: $MODE  binary: $BIN"
  echo "cores (socket 0 first): $CPUS"; echo "threads: $THREADS  maxL: $MAXL  groups: $GROUPS_WANTED"
  echo "flags: $FLAGS"
  likwid-perfctr -v 2>&1 | head -2; likwid-topology -c 2>/dev/null | head -40 || true
  echo "$AVAIL"
} > "$OUT/meta_${SLURM_JOB_ID:-local}.txt"

for T in $THREADS; do
  [ "$T" -le "$CORES" ] || { echo "WARN: T=$T > $CORES physical cores; skipping"; continue; }
  MEAS=$(echo "$CPUS" | cut -d, -f1-"$T")              # the T cores this run uses = the ones measured
  PLACES=$(echo "$MEAS" | sed 's/[0-9]\+/{&}/g')
  for G in $GROUPS_WANTED; do
    if ! echo "$AVAIL" | awk '{print $1}' | grep -qx "$G"; then
      echo "WARN: group $G not available on this CPU (likwid-perfctr -a); skipping"; continue
    fi
    tag="t${T}_${G}_${SLURM_JOB_ID:-local}"
    echo "== T=$T group=$G measured cpus=$MEAS $(date +%T)"
    OMP_NUM_THREADS=$T OMP_PLACES="$PLACES" OMP_PROC_BIND=close OMP_DISPLAY_AFFINITY=true \
    OMP_AFFINITY_FORMAT="affinity: thread %0.3n on cpus %A" \
      likwid-perfctr ${LIKWID_PERFCTR_FLAGS:-} $M -c "$MEAS" -g "$G" -o "$OUT/$tag.csv" \
        "$BIN" $FLAGS --json-out "$OUT/$tag.json" > "$OUT/$tag.log" 2>&1 \
      || echo "WARN: exited $? (see $OUT/$tag.log)"
    grep -m1 "OpenMP threads" "$OUT/$tag.log" || true
    grep -i "not in the list\|ERROR" "$OUT/$tag.log" | head -3 || true
    used=$(grep -o "affinity: thread [0-9]* on cpus [0-9,-]*" "$OUT/$tag.log" | awk '{print $NF}' | sort -un | paste -sd, || true)
    echo "   threads ran on cpus: ${used:-?}"
    for c in ${used//,/ }; do
      echo ",$MEAS," | grep -q ",$c," || echo "WARN: a thread ran on cpu $c, which likwid did not measure"
    done
  done
done
echo "== likwid done -> $OUT   (next: python3 grid_parse.py results && python3 grid_plot.py results)"
