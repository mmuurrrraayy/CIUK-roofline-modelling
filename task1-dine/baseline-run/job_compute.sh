#!/bin/bash
#SBATCH -J bench-compute
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:30:00
#SBATCH -o compute_%j.out
#SBATCH -e compute_%j.err

# Peak FLOP/s (DP+SP) + clock/FLOPS counters. Partition/account are given at submit time:
#   Dine   : sbatch -p bluefield1 -A <account> job_compute.sh zen2
#   Dine-2 : sbatch -p dine2 -A <account> job_compute.sh spr
ARCH=${1:?usage: sbatch -p <partition> -A <account> job_compute.sh zen2|spr}

module load likwid 2>/dev/null || echo "no likwid module; assuming likwid is on PATH"
module load python/3.13.6

python3 bench.py --arch "$ARCH" --suite compute --reps 5 --out results
