#!/bin/bash
#SBATCH -J bench-probe
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:05:00
#SBATCH -o probe_%j.out
#SBATCH -e probe_%j.err

# Quick hardware probe to fill in/verify ARCHS in bench.py and the figures in theoretical_peaks.md.
#   Dine   : sbatch -p bluefield1 -A <account> job_probe.sh
#   Dine-2 : sbatch -p dine2 -A <account> job_probe.sh
set -x
type module                             # "not found" => batch shell lacks module init (use #!/bin/bash -l)
module load gnu_comp/14.1.0             # likwid module requires a compiler module first
module load likwid/5.5.1 || echo "likwid/5.5.1 module failed; assuming likwid is on PATH"
module list 2>&1
module show likwid/5.5.1 2>&1 | grep -E 'prereq|depends_on|PATH'   # prerequisites + what PATH it adds

hostname
lscpu                                   # threads/core (SMT), base/max MHz, cache sizes, flags (model/sockets/cores are in the docs)
grep -o -w 'avx2\|fma\|avx512f\|amx_tile' /proc/cpuinfo | sort | uniq -c
if command -v likwid-topology >/dev/null; then HAVE_LIKWID=1; else HAVE_LIKWID=0; echo "likwid NOT available: skipping likwid-* steps"; fi
[ $HAVE_LIKWID = 1 ] && likwid-topology -c -g   # caches, NUMA domains, physical-core CPU IDs
lscpu -e=CPU,CORE,SOCKET,NODE | head -n 5   # topology fallback
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>&1
numactl -H 2>/dev/null                  # NUMA domains and memory per domain (NPS / SNC setting)
dmidecode -t memory 2>/dev/null | grep -E 'Configured Memory Speed|Size: [0-9]' | sort | uniq -c   # DIMM speed; usually needs root
if [ $HAVE_LIKWID = 1 ]; then
  likwid-bench -a | grep -E 'peakflops|_mem_|^ *(load|copy|stream|triad)_'
  likwid-perfctr -a | grep -E 'CLOCK|FLOPS|MEM'   # available counter groups

  # Hardware-counter access (needed for the CLOCK / FLOPS_DP cross-check). Exit 0 and a "Clock [MHz]" line = OK.
  likwid-perfctr -c 0 -g CLOCK sleep 1; echo "perfctr exit: $?"
  ls -l $(which likwid-accessD 2>/dev/null) 2>&1
fi
ls -l /dev/cpu/0/msr 2>&1; lsmod | grep -w msr
cat /proc/sys/kernel/perf_event_paranoid
command -v perf gcc icx mpicc python3 2>&1   # what's available for a non-likwid fallback
