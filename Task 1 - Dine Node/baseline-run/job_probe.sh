#!/bin/bash
#SBATCH -J bench-probe
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:05:00
#SBATCH -o probe_%j.out
#SBATCH -e probe_%j.err

# Quick hardware probe to fill in/verify ARCHS in bench.py and the figures in theoretical_peaks.md.
#   Dine   : sbatch -p bluefield1 -A do009 job_probe.sh
#   Dine-2 : sbatch -p dine2 -A <account> job_probe.sh
module load likwid 2>/dev/null || echo "no likwid module; assuming likwid is on PATH"

set -x
hostname
lscpu                                   # model name, sockets, cores/socket, threads/core, base/max MHz, flags
grep -o -w 'avx2\|fma\|avx512f\|amx_tile' /proc/cpuinfo | sort | uniq -c
likwid-topology -c -g                   # caches, NUMA domains, physical-core CPU IDs
numactl -H 2>/dev/null                  # NUMA domains and memory per domain (NPS / SNC setting)
grep MemTotal /proc/meminfo
dmidecode -t memory 2>/dev/null | grep -E 'Configured Memory Speed|Size: [0-9]' | sort | uniq -c   # DIMM speed; usually needs root
likwid-bench -a | grep -E 'peakflops|_mem_|^ *(load|copy|stream|triad)_'
likwid-perfctr -a | grep -E 'CLOCK|FLOPS|MEM'   # available counter groups

# Hardware-counter access (needed for the CLOCK / FLOPS_DP cross-check). Exit 0 and a "Clock [MHz]" line = OK.
likwid-perfctr -c 0 -g CLOCK sleep 1; echo "perfctr exit: $?"
ls -l /dev/cpu/0/msr 2>&1; lsmod | grep -w msr; ls -l $(which likwid-accessD 2>/dev/null) 2>&1
cat /proc/sys/kernel/perf_event_paranoid
