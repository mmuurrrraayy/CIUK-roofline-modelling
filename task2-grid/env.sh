
#   source env.sh zen2|spr
#   zen2 = Dine   (2x EPYC 7302, AVX2 - no AVX-512)
#   spr  = Dine-2 (Sapphire Rapids, AVX-512)
ARCH=${1:?usage: source env.sh zen2|spr}
T2=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Commits mandated by https://github.com/UKNNSS-Benchmarks/uknnss-benchmark-grid
GRID_COMMIT=e2d607f6c708362050ec26cbe89fc971a1a879c5
GB_COMMIT=c7457a85b6a0d9d1578838af11477cb41b1a5764

SRC=$T2/src                    
BUILD=$T2/build/$ARCH          # per-machine build trees
PREFIX=$T2/install/$ARCH       # lime/, grid/, gridbench/, gridbench-likwid/
BENCH=$PREFIX/gridbench/bin/Benchmark_Grid
BENCH_LIKWID=$PREFIX/gridbench-likwid/bin/Benchmark_Grid

case $ARCH in
  zen2) SIMD=AVX2 ;;
  spr)  SIMD=AVX512 ;;
  *) echo "env.sh: unknown arch '$ARCH' (zen2|spr)"; return 1 ;;
esac


COMPILER_MODULE=${COMPILER_MODULE:-gnu_comp/14.1.0}
MODULES=${MODULES:-"openmpi/5.0.3 python/3.13.6"}
LIKWID_MODULE=${LIKWID_MODULE:-likwid/master_020426}
if command -v module >/dev/null 2>&1; then
  module load "$COMPILER_MODULE" || echo "env.sh: compiler module '$COMPILER_MODULE' not loaded"
  for m in $MODULES $LIKWID_MODULE; do
    module load "$m" || echo "env.sh: module '$m' not loaded; assuming it's on PATH"
  done
fi

CPU_MODEL=$(lscpu | awk -F: '/^Model name/{gsub(/^ +/,"",$2); print $2}')
check_build_cpu() {
  local rec=$PREFIX/build_cpu.txt
  if [ -f "$rec" ] && [ "$(cat "$rec")" != "$CPU_MODEL" ]; then
    echo "ERROR: built on '$(cat "$rec")' but running on '$CPU_MODEL'; rebuild on this node type (job_build.sh $ARCH)"
    exit 1
  fi
}
