#!/bin/bash
#SBATCH -J grid-build
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 02:00:00
#SBATCH -o build_%j.out
#SBATCH -e build_%j.err

set -euo pipefail
ARCH=${1:?usage: sbatch -p <partition> -A <account> job_build.sh zen2|spr}
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
source ./env.sh "$ARCH"
NJ=${SLURM_CPUS_ON_NODE:-$(nproc)}
[ -d "$SRC/Grid" ] || { echo "no sources: run 'bash fetch_sources.sh' on the login node first"; exit 1; }
mkdir -p "$BUILD" "$PREFIX"
echo "$CPU_MODEL" > "$PREFIX/build_cpu.txt"

echo "== $(hostname), $(lscpu | grep 'Model name'), SIMD=$SIMD, $(mpicxx --version | head -1)"
if [ ! -f "$PREFIX/lime/lib/liblime.a" ]; then
  mkdir -p "$BUILD/lime" && cd "$BUILD/lime"
  "$SRC/lime-1.3.2/configure" --prefix="$PREFIX/lime" CFLAGS='-O2 -fPIE'
  make -j"$NJ" && make install
fi

if [ ! -x "$PREFIX/grid/bin/grid-config" ]; then
  mkdir -p "$BUILD/grid" && cd "$BUILD/grid"
  [ -f Makefile ] || CXX=mpicxx CXXFLAGS="-mfma" "$SRC/Grid/configure" --prefix="$PREFIX/grid" \
      --enable-simd="$SIMD" --enable-comms=mpi-auto --enable-alloc-align=4k --enable-shm=shmget \
      --enable-gparity=no --enable-fermion-reps=no --enable-zmobius=no \
      --with-lime="$PREFIX/lime" --with-gmp="$PREFIX/deps" --with-mpfr="$PREFIX/deps" --with-fftw="$PREFIX/deps"
  make grid-config
  make -j"$NJ" -C Grid
  make install -C Grid
  mkdir -p "$PREFIX/grid/bin" && cp grid-config "$PREFIX/grid/bin/"
fi
"$PREFIX/grid/bin/grid-config" --cxxflags

build_bench() {  
  local src=$1 name=$2; shift 2
  if [ ! -x "$PREFIX/$name/bin/Benchmark_Grid" ]; then
    mkdir -p "$BUILD/$name" && cd "$BUILD/$name"
    [ -f Makefile ] || env CXX="$("$PREFIX/grid/bin/grid-config" --cxx)" "$@" \
        "$src/configure" --with-grid="$PREFIX/grid" --prefix="$PREFIX/$name"
    make -j"$NJ" && make install
  fi
}
build_bench "$SRC/grid-benchmark" gridbench

if command -v likwid-perfctr >/dev/null; then
  LW=${LIKWID_PREFIX:-$(dirname "$(dirname "$(command -v likwid-perfctr)")")}
  if [ -f "$LW/include/likwid-marker.h" ]; then
    build_bench "$SRC/grid-benchmark-likwid" gridbench-likwid \
        CXXFLAGS="-DLIKWID_PERFMON -I$LW/include" LDFLAGS="-L$LW/lib -Wl,-rpath,$LW/lib" LIBS="-llikwid"
  else
    echo "WARN: $LW/include/likwid-marker.h not found (set LIKWID_PREFIX); skipping instrumented build"
  fi
else
  echo "WARN: likwid not on PATH; skipping instrumented build (job_likwid.sh whole-run mode still works)"
fi

for b in "$BENCH" "$BENCH_LIKWID"; do
  [ -x "$b" ] || continue
  echo "$b: $(objdump -d "$b" | grep -c '%zmm') zmm / $(objdump -d "$b" | grep -c '%ymm') ymm instructions"
done
[ "$SIMD" = AVX2 ] && echo "(expect 0 zmm on zen2 - any zmm would be an illegal instruction on EPYC 7302)"
echo "== build done: $BENCH"
