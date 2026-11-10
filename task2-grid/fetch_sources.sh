#!/bin/bash

set -euo pipefail
source "$(dirname "$0")/env.sh" zen2 
mkdir -p "$SRC"
cd "$SRC"

if [ ! -d Grid ]; then git clone https://github.com/paboyle/Grid.git; fi
( cd Grid && git checkout -q "$GRID_COMMIT" && { [ -f configure ] || ./bootstrap.sh; } )

if [ ! -d grid-benchmark ]; then git clone https://github.com/aportelli/grid-benchmark.git; fi
( cd grid-benchmark && git checkout -q "$GB_COMMIT" && { [ -f configure ] || ./bootstrap.sh; } )

if [ ! -d grid-benchmark-likwid ]; then
  cp -r grid-benchmark grid-benchmark-likwid
  ( cd grid-benchmark-likwid && git apply "$T2/likwid-markers.patch" )
fi

if [ ! -d lime-1.3.2 ]; then
  wget -q http://usqcd-software.github.io/downloads/c-lime/lime-1.3.2.tar.gz
  tar -xzf lime-1.3.2.tar.gz && rm lime-1.3.2.tar.gz
fi

echo "sources ready in $SRC:"
( cd Grid && echo "  Grid           $(git rev-parse --short HEAD)" )
( cd grid-benchmark && echo "  grid-benchmark $(git rev-parse --short HEAD)" )
( cd grid-benchmark-likwid && echo "  +likwid patch  $(git diff --stat | tail -1)" )
