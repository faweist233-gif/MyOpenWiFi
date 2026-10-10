#!/bin/bash
# ============================================================
# E316 CSI+IQ 部署脚本（板端执行）—— 只部署，不采集
#   用法（板端）: sh e316_deploy.sh <CHANNEL>
#   默认信道 11
# 说明: 修复 ADC 绑定 -> 加载模块 -> monitor 模式 -> 全新加载 side_ch
#       跑完后板子处于"待采集"状态，再用 e316_collect.sh 采集
# ============================================================
CH=${1:-11}
cd /root/openwifi
echo "########## E316 CSI+IQ 部署 (ch=$CH) ##########"

echo "[STEP1] cf_axi_adc 绑定检查/修复"
if [ ! -e /sys/bus/platform/drivers/cf_axi_adc/79020000.cf-ad9361-lpc ]; then
  echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind 2>&1
  echo "  rebind rc=$?"
fi
ls /sys/bus/platform/drivers/cf_axi_adc/ | grep 79020000 && echo "  ADC bound OK"
echo

echo "[STEP2] 彻底重载模块组（★ 确保所有 DMA 通道复位）"
rmmod side_ch 2>/dev/null
rmmod sdr xpu openofdm_rx openofdm_tx rx_intf tx_intf 2>/dev/null
rmmod xilinx_dma 2>/dev/null
sleep 2
insmod xilinx_dma.ko; echo "  xilinx_dma rc=$?"
for M in tx_intf rx_intf openofdm_tx openofdm_rx xpu sdr; do
  insmod $M.ko 2>/dev/null; printf "  %-12s rc=%d\n" "$M" "$?"
done
sleep 2
echo

echo "[STEP3] monitor 模式 ch$CH"
ip link set sdr0 down 2>/dev/null
iw dev sdr0 set type monitor 2>/dev/null
ip link set sdr0 up
iw dev sdr0 set channel $CH 2>/dev/null
sleep 2
iw dev sdr0 info | grep -E "type|channel"
echo

echo "[STEP4] 空口流量检测 (5s)"
R0=$(cat /sys/class/net/sdr0/statistics/rx_packets)
sleep 5
R1=$(cat /sys/class/net/sdr0/statistics/rx_packets)
printf "  rx_packets delta=%d" $((R1-R0))
[ $((R1-R0)) -eq 0 ] && echo "  ← ⚠ 此信道无流量，换 ch1/6/11"
echo

echo "[STEP5] 加载 side_ch.ko（联合采集）"
insmod side_ch.ko iq_len_init=440 num_eq_init=0 combined_init=1 pre_trigger_init=256 2>&1
echo "  rc=$?"
sleep 2
printf "  rh20 (硬件 FIFO): "; ./side_ch_ctl rh20 2>&1 | grep -oE "val [0-9]+" | tail -1
echo
echo "########## 部署完成，可执行 e316_collect.sh 采集 ##########"
