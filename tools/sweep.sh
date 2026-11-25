#!/bin/sh
# Balance sweep. Run from the project root:  sh tools/sweep.sh
# Each line is a full run of the simulator; numbers are averaged over 4 seeds.
set -e
cd "$(dirname "$0")/.."

echo "== 4000 ticks (~2.8 simulated days)"
python3 main.py --sim 4000 --seeds 4

echo
echo "== 12000 ticks (~8.3 simulated days)"
python3 main.py --sim 12000 --seeds 4

echo
echo "== a deliberately bad setting for contrast (base_cost too high: everyone dies)"
python3 main.py --sim 4000 --seeds 4 --set base_cost=0.35

echo
echo "== the first sweep, kept for the record: every strategy lost money"
echo "   (rate 0.022 / base 0.060: idle -28.6  lines -207.5  balanced -41.4  reckless -195.9)"
