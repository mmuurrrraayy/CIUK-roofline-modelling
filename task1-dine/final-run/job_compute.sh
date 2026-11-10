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

# Order matters: likwid aborts unless a compiler module is loaded first, and a bare `module load likwid`
# picks likwid/master_020426, so pin the version. Purge first so the job doesn't inherit login-shell modules.
module purge
module load cosma/2024                  # COSMA default base module (removed by purge)
module load gnu_comp/14.1.0
module load likwid/5.5.1
command -v likwid-bench >/dev/null || { echo "ERROR: likwid-bench not on PATH after module load"; exit 1; }
module load python/3.13.6 || echo "python/3.13.6 module failed; using system python3"

python3 bench.py --arch "$ARCH" --suite compute --reps 5 --out results
