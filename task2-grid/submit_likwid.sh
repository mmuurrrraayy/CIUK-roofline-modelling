#!/bin/bash
# Submit likwid-perfctr runs as one 1-hour job per (thread count, group).
#   bash submit_likwid.sh <zen2|spr> [marker|whole] ["threads"] [maxL] ["groups"]
#   e.g. bash submit_likwid.sh zen2 marker "1 16 32" 32 "MEM FLOPS_SP"

set -euo pipefail
ARCH=${1:?usage: submit_likwid.sh zen2|spr [marker|whole] ["threads"] [maxL] ["groups"]}
MODE=${2:-marker}
THREADS=${3:-"1 16 32"}
MAXL=${4:-32}
GROUPS_WANTED=${5:-"MEM FLOPS_SP"}
cd "$(dirname "$0")"
for T in $THREADS; do
  for G in $GROUPS_WANTED; do
    sbatch -p "${PART:-ciuk-dine2}" -A "${ACCT:?set ACCT=<your slurm account>}" -J "lw-${MODE}-t${T}-${G}" \
      job_likwid.sh "$ARCH" "$MODE" "$T" "$MAXL" "$G"
  done
done
