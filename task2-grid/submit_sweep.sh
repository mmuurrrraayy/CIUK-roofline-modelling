#!/bin/bash
# Submit the strong-scaling sweep as one 1-hour job per thread count (the queue's time limit).
#   bash submit_sweep.sh <zen2|spr> [maxL] ["threads"] [close|spread]
#   e.g. bash submit_sweep.sh zen2 32 "1 2 4 8 16 32" close
# All jobs write into the same results/<arch>/sweep_L<maxL>_<bind>/ directory.
set -euo pipefail
ARCH=${1:?usage: submit_sweep.sh zen2|spr [maxL] ["threads"] [close|spread]}
MAXL=${2:-32}
THREADS=${3:-"1 2 4 8 16 32"}
BIND=${4:-close}
cd "$(dirname "$0")"
for T in $THREADS; do
  sbatch -p "${PART:-ciuk-dine2}" -A "${ACCT:?set ACCT=<your slurm account>}" -J "grid-L${MAXL}-t${T}" \
    job_sweep.sh "$ARCH" "$MAXL" "$T" 1 "$BIND"
done
