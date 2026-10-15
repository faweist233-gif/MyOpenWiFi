#!/bin/bash
# ============================================================
# sync_long 修复验证仿真矩阵 v2(WSL 内后台运行)
# 改进: 每例 timeout、带时间戳日志、统计 signal 事件数
# ============================================================
set -u
cd "$HOME/openofdm-sim/verilog" || exit 1

cp /mnt/c/OpenWiFi/openofdm/testing_inputs/conducted/weak1.txt \
   /mnt/c/OpenWiFi/openofdm/testing_inputs/conducted/weak2.txt \
   /mnt/c/OpenWiFi/openofdm/testing_inputs/conducted/noise.txt \
   /mnt/c/OpenWiFi/openofdm/testing_inputs/conducted/cfo80k.txt \
   /mnt/c/OpenWiFi/openofdm/testing_inputs/conducted/cw_jam.txt \
   ../testing_inputs/conducted/ 2>/dev/null

[ -f sync_long.v.fix ] || cp sync_long.v sync_long.v.fix

DEF_STIM="../testing_inputs/conducted/dot11a_24mbps_qos_data_e4_90_7e_15_2a_16_e8_de_27_90_6e_42.txt"

run_case() {
  name="$1"; stim="$2"; ns="$3"
  echo "$(date +%H:%M:%S) START $name" >> summary.txt
  sed "s|^\`define SAMPLE_FILE .*|\`define SAMPLE_FILE \"$stim\"|; \
       s|^\`define NUM_SAMPLE .*|\`define NUM_SAMPLE $ns|" \
      dot11_tb.v > "tb_$name.v"
  if ! iverilog -v -c dot11_modules.list -o "out_$name.out" -DDEBUG_PRINT \
      "tb_$name.v" > "compile_$name.log" 2>&1; then
    echo "$(date +%H:%M:%S) $name COMPILE_FAIL" >> summary.txt
    return
  fi
  rm -f sim_out/*.txt
  if timeout 900 vvp -n "out_$name.out" > "sim_$name.log" 2>&1; then
    d=$(grep -c ' 1$' sim_out/sync_long_frame_detected.txt)
    s=$(tr -d '\n' < sim_out/signal_out.txt)
    ne=$(( ${#s} / 26 ))
    echo "$(date +%H:%M:%S) $name DONE detections=$d signal_events=$ne signal=[$s]" \
        >> summary.txt
  else
    echo "$(date +%H:%M:%S) $name VVP_TIMEOUT_OR_FAIL" >> summary.txt
  fi
  mkdir -p "results_$name"
  cp sim_out/*.txt "results_$name/" 2>/dev/null
}

: > summary.txt
echo "$(date +%H:%M:%S) matrix start" >> summary.txt

# ---------- 基线(原版 sync_long) ----------
cp sync_long.v.orig sync_long.v
for c in default weak1 weak2 noise cfo80k cw_jam; do
  if [ "$c" = "default" ]; then
    run_case "base_$c" "$DEF_STIM" 3000
  else
    run_case "base_$c" "../testing_inputs/conducted/$c.txt" 3000
  fi
done

# ---------- 修复版(sync_long Fix A) ----------
cp sync_long.v.fix sync_long.v
for c in default weak1 weak2 noise cfo80k cw_jam; do
  if [ "$c" = "default" ]; then
    run_case "fix_$c" "$DEF_STIM" 3000
  else
    run_case "fix_$c" "../testing_inputs/conducted/$c.txt" 3000
  fi
done

echo "$(date +%H:%M:%S) ALL_DONE" >> summary.txt
