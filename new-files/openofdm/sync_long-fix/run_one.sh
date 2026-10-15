#!/bin/bash
# ============================================================
# 单例仿真: run_one.sh <case> <variant(base|fix)>
# 由 Windows 侧 sim_loop.ps1 逐例调用;WSL 崩溃重启后循环自动重试。
# 结果追加到 NTFS 上的 /mnt/c/OpenWiFi/openofdm/sim_summary.txt
# ============================================================
set -u
CASE="${1:?}"; VAR="${2:?}"
cd "$HOME/openofdm-sim/verilog" || exit 9
SUMF=/mnt/c/OpenWiFi/openofdm/sim_summary.txt
DEF_STIM="../testing_inputs/conducted/dot11a_24mbps_qos_data_e4_90_7e_15_2a_16_e8_de_27_90_6e_42.txt"

if [ "$CASE" = "default" ]; then
  STIM="$DEF_STIM"
else
  STIM="../testing_inputs/conducted/$CASE.txt"
fi
[ -f "$STIM" ] || { echo "$(date +%H:%M:%S) ${VAR}_${CASE} STIM_MISSING" >> "$SUMF"; exit 3; }

if [ "$VAR" = "base" ]; then
  cp sync_long.v.orig sync_long.v
else
  cp sync_long.v.fix sync_long.v
fi

name="${VAR}_${CASE}"
echo "$(date +%H:%M:%S) START $name" >> "$SUMF"

sed "s|^\`define SAMPLE_FILE .*|\`define SAMPLE_FILE \"$STIM\"|; \
     s|^\`define NUM_SAMPLE .*|\`define NUM_SAMPLE 3000|; \
     s|localparam RAM_SIZE = 1<<25|localparam RAM_SIZE = 1<<12|" \
    dot11_tb.v > "tb_$name.v"

if ! iverilog -v -c dot11_modules.list -o "out_$name.out" -DDEBUG_PRINT \
    "tb_$name.v" > "compile_$name.log" 2>&1; then
  echo "$(date +%H:%M:%S) $name COMPILE_FAIL" >> "$SUMF"
  exit 4
fi

rm -f sim_out/*.txt
if ! timeout 600 vvp -n "out_$name.out" > "sim_$name.log" 2>&1; then
  echo "$(date +%H:%M:%S) $name VVP_FAIL" >> "$SUMF"
  exit 5
fi

d=$(grep -c ' 1$' sim_out/sync_long_frame_detected.txt)
s=$(tr -d '\n' < sim_out/signal_out.txt)
ne=$(( ${#s} / 26 ))
echo "$(date +%H:%M:%S) $name DONE detections=$d signal_events=$ne signal=[$s]" >> "$SUMF"
mkdir -p "/mnt/c/OpenWiFi/openofdm/sim_results_$name"
cp sim_out/*.txt "/mnt/c/OpenWiFi/openofdm/sim_results_$name/" 2>/dev/null
exit 0
