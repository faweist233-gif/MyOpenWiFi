#!/bin/bash
# ============================================================
# one_click_collect.sh — E316 CSI+IQ 联合采集一键脚本（PC 侧，Git Bash 执行）
#
# 用法:
#   bash one_click_collect.sh [CHANNEL] [DURATION] [OUT_ROOT] [--skip-deploy]
#   默认: ch11, 采集 25s, 输出 C:/iq
#
# 完整流程:
#   [0] 等板上线   [1] 部署板端   [2] Windows 监听   [3] 采集(排空+推UDP)   [4] 收尾
#
# 依赖:
#   - Windows 内置 OpenSSH 客户端（ssh/scp）+ 板端公钥认证
#     （一次性把本机公钥装入板端，见操作手册 §八；未装时 ssh 会交互问密码）
#   - Windows Python（跑 win_listen_iq.py 监听器）
#   - 板子 root@192.168.10.122 / openwifi，PC 侧网卡 192.168.10.2
#
# 数据落盘: <OUT_ROOT>/iq_<时间戳>/{iq_<时间戳>.bin 原始流, iq_<时间戳>_csi.csv CSI宽表}
#
# 说明:
#   - 采集前排空 FIFO（e316_collect.sh 已内置），消除陈旧积压导致的 TSF 跳变
#   - 监听用 win_listen_iq.py --layout=iq_first（RTL 已修复为 [IQ][CSI] 同包对齐）
# ============================================================
CH=${1:-11}
DUR=${2:-25}
OUT=${3:-"C:/iq"}
SKIP_DEPLOY=false
for a in "$@"; do [ "$a" = "--skip-deploy" ] && SKIP_DEPLOY=true; done

PCIP=192.168.10.2
BOARD=root@192.168.10.122
TOOLS="/c/OpenWiFi/tools"
# Windows Python（优先 managed 版本，回退系统 python）
PY="/c/Users/fawei/.workbuddy/binaries/python/versions/3.13.12/python.exe"
[ -f "$PY" ] || PY="python"

echo "========== E316 CSI+IQ 一键采集 (ch=$CH, ${DUR}s -> $PCIP:4000) =========="

# ---- [0/4] 清理残留监听 + 等板上线 ----
echo "[0/4] 清理残留监听 + 等待板子上线..."
# 清理上次未退出的 win_listen_iq.py（避免 4000 端口被占）
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'win_listen_iq' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null || true

UP=false
for i in $(seq 1 24); do
  if timeout 5 bash -c "echo > /dev/tcp/192.168.10.122/22" 2>/dev/null; then
    UP=true; echo "  板子已上线"; break
  fi
  sleep 10
done
$UP || { echo "✗ 板子 240s 内未上线，退出"; exit 1; }

# ---- [1/4] 部署 ----
if $SKIP_DEPLOY; then
  echo "[1/4] 跳过部署（--skip-deploy，假定板子已就绪）"
else
  echo "[1/4] 部署板端（ADC 修复 + 模块重载 + monitor + side_ch）..."
  scp -o StrictHostKeyChecking=accept-new "$TOOLS/e316_deploy.sh" root@192.168.10.122:/tmp/
  ssh -o StrictHostKeyChecking=accept-new root@192.168.10.122 "sh /tmp/e316_deploy.sh $CH"
fi

# ---- [2/4] 启动 Windows 监听 ----
LISTEN_S=$((DUR + 15))
echo "[2/4] 启动 Windows 监听（${LISTEN_S}s -> $OUT）..."
"$PY" "$TOOLS/win_listen_iq.py" "$OUT" "$LISTEN_S" --layout=iq_first &
LPID=$!
sleep 2   # 等监听 bind 完成

# ---- [3/4] 采集 ----
echo "[3/4] 板端采集（排空 2s + 采集 ${DUR}s）..."
scp -o StrictHostKeyChecking=accept-new "$TOOLS/e316_collect.sh" root@192.168.10.122:/tmp/
ssh -o StrictHostKeyChecking=accept-new root@192.168.10.122 "sh /tmp/e316_collect.sh $DUR $PCIP"

# ---- [4/4] 收尾 ----
echo "[4/4] 等待监听收尾..."
wait $LPID 2>/dev/null
echo "========== 完成，数据在 $OUT（最新 iq_* 目录）=========="
