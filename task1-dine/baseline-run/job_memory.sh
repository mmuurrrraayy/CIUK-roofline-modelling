#!/bin/bash
#SBATCH -J bench-memory
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 01:00:00
#SBATCH -o memory_%j.out
#SBATCH -e memory_%j.err

# DRAM bandwidth + cache-level sweep. Partition/account are given at submit time:
#   Dine   : sbatch -p bluefield1 -A <account> job_memory.sh zen2
#   Dine-2 : sbatch -p dine2 -A <account> job_memory.sh spr
ARCH=${1:?usage: sbatch -p <partition> -A <account> job_memory.sh zen2|spr}

module load likwid 2>/dev/null || echo "no likwid module; assuming likwid is on PATH"
module load gcc 2>/dev/null || echo "no gcc module; assuming gcc is on PATH"
module load python/3.13.6

# Reference STREAM, built here so -march=native matches the compute node. 400M doubles = 3.2 GB/array,
# far beyond the L3 of either node (rule of thumb: arrays >= 4x total cache). -mcmodel=medium is
# needed for static arrays > 2 GB. bench.py runs ./stream if it exists.
gcc -O3 -march=native -fopenmp -mcmodel=medium -DSTREAM_ARRAY_SIZE=400000000 -DNTIMES=20 \
    stream/stream.c -o stream/stream || echo "STREAM build failed; bench.py will skip it"

python3 bench.py --arch "$ARCH" --suite memory --reps 5 --out results
