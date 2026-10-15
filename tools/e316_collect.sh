#!/bin/bash
# ============================================================
# E316 CSI+IQ 采集脚本（板端执行，配合 PC 侧监听器）
#   用法（板端）: sh e316_collect.sh <DURATION> [PC_IP] [FLUSH_S]
#   默认: 25 秒采集, PC_IP=192.168.10.2, 排空 2 秒
#
# ★ 使用前请先在 PC（Windows 侧）启动监听（现拓扑 NAT，WSL 内收不到）:
#     python C:/OpenWiFi/tools/win_listen_iq.py C:/iq 60 --layout=iq_first
#   本脚本会先排空 FIFO 再正式采集并推 UDP，务必让监听窗口覆盖本脚本的执行时间。
#
# 关键技术点:
#   1. 必须用 SIGINT 停止（程序只注册了 SIGINT handler）。
#      用 timeout/kill(SIGTERM) 或 | head 都会强杀进程并使 DMA 通道永久卡死。
#   2. 输出行缓冲已在程序内用 setvbuf 处理，普通重定向即可拿到输出。
#   3. 采集前必须排空 FIFO：side_ch 模块加载后硬件就持续收包写 FIFO（深度仅
#      4096 word ≈ 8 条 record），若启动采集前耽搁过久，FIFO 里积压的旧 record
#      的 TSF 会是几分钟前的旧值，与实时新数据之间形成 TSF 大跳变（实测 127s）。
# ============================================================
DUR=${1:-25}; PCIP=${2:-192.168.10.2}; FLUSH_S=${3:-2}
cd /root/openwifi

echo "########## E316 CSI+IQ 采集 (${DUR}s -> $PCIP:4000) ##########"
printf "side_ch_ctl md5: "; md5sum side_ch_ctl | cut -d' ' -f1
printf "rh20 (采集前): "; ./side_ch_ctl rh20 2>&1 | grep -oE "val [0-9]+" | tail -1
echo

# ★ 采集前排空 FIFO：短跑 g100 把积压旧 record 推走。
#   推到 127.0.0.1（板端回环），不污染 PC 监听器；SIGINT 收尾避免 DMA 卡死。
echo "--- 排空 FIFO (${FLUSH_S}s, 推到回环) ---"
./side_ch_ctl g100 -s 127.0.0.1 > /tmp/side_ch_flush.txt 2>&1 &
FPID=$!
sleep $FLUSH_S
kill -INT $FPID
wait $FPID 2>/dev/null
printf "rh20 (排空后): "; ./side_ch_ctl rh20 2>&1 | grep -oE "val [0-9]+" | tail -1
echo

# ★ 正式采集（后台启动 + SIGINT 收尾，不要用 timeout）
./side_ch_ctl g100 -s $PCIP > /tmp/side_ch_out.txt 2>&1 &
SCPID=$!
sleep $DUR
kill -INT $SCPID
wait $SCPID 2>/dev/null

echo "--- 采集输出 ---"
cat /tmp/side_ch_out.txt
echo
printf -- "--- rh20 (采集后, 应 < 1000 表示正常排空): "
./side_ch_ctl rh20 2>&1 | grep -oE "val [0-9]+" | tail -1
printf -- "--- get_side_info WARNING 累计: "
dmesg | grep -c "get_side_info WARNING"
echo "########## 采集完成 ##########"
