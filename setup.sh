#!/usr/bin/env bash
###############################################################################
# setup.sh —— 从零拉齐 openwifi CSI+IQ 联合采集工作环境（跨平台：Linux / WSL2）
#
# 本仓库只保存「改动」与「工具」，不含上游全量代码（上游 4GB，其中 adi-linux
# 内核树就是 3.4GB）。本脚本负责：
#   1. 从官方仓库克隆 openwifi / openwifi-hw 到工作目录
#   2. 拉取必要子模块（adi-linux 内核树、openofdm_rx 等）
#   3. 把本仓库 patches/ 下的改动套用到上游代码
#   4. 拷贝新增源码文件（csi_iq_display.py、unit_test/）
#   5. 拷贝 tools/ 工具脚本
#   6. 运行环境体检
#
# 用法：
#   ./setup.sh                      # 全部执行（默认工作目录 ~/git）
#   ./setup.sh --work-dir /opt/ow   # 指定工作目录
#   ./setup.sh --skip-submodules    # 跳过 3.4G 子模块拉取
#   ./setup.sh --patch-only         # 只套 patch（已有上游代码时）
#
# 前置：git、bash、联网；可选：Xilinx Vitis 2022.2（构建用，见 README）
###############################################################################

set -u

# ---------------------------------------------------------------- 配置
WORK_DIR="${HOME}/git"
OW_REMOTE="https://github.com/open-sdr/openwifi.git"
HW_REMOTE="https://github.com/open-sdr/openwifi-hw.git"

# 上游基线（patch 是基于此 commit 生成的；clone 后必须切到它，否则 patch 打不上）
# 若上游 master 更新导致打不上，见 README「版本对齐」手动回退到对应 commit
OW_BASE="2f112f0"   # openwifi@master 生成 patch 时的 commit
HW_BASE="d047d79"   # openwifi-hw@master 生成 patch 时的 commit

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_DIR="$SCRIPT_DIR/patches"
TOOLS_SRC="$SCRIPT_DIR/tools"

SKIP_SUBMODULES=0
PATCH_ONLY=0

grn() { printf '\033[32m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
hdr() { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }
die() { red "FAIL: $*"; exit 1; }

# ---------------------------------------------------------------- 参数
while [ $# -gt 0 ]; do
  case "$1" in
    --work-dir)        WORK_DIR="$2"; shift 2 ;;
    --work-dir=*)      WORK_DIR="${1#*=}"; shift ;;
    --skip-submodules) SKIP_SUBMODULES=1; shift ;;
    --patch-only)      PATCH_ONLY=1; shift ;;
    -h|--help)         sed -n '2,30p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) die "未知参数: $1（用 --help 看用法）" ;;
  esac
done

OW_DIR="$WORK_DIR/openwifi"
HW_DIR="$WORK_DIR/openwifi-hw"

hdr "配置"
echo "  工作目录 : $WORK_DIR"
echo "  openwifi : $OW_DIR"
echo "  openwifi-hw: $HW_DIR"
echo "  仓库根   : $SCRIPT_DIR"

mkdir -p "$WORK_DIR"

# ---------------------------------------------------------------- 1. 克隆
# 把仓库切到 patch 基线的 commit（保证 patch 能可靠打上）
checkout_base() {
  local dir="$1" base="$2" name="$3"
  ( cd "$dir" || exit 1
    if [ "$(git rev-parse HEAD 2>/dev/null)" = "$base" ]; then
      grn "  [OK]   $name 已在基线 $base"
      return 0
    fi
    if git cat-file -e "${base}^{commit}" 2>/dev/null; then
      git checkout "$base" && grn "  [OK]   $name 已切到基线 $base"
    else
      ylw "  本地无 $base，尝试 fetch origin ..."
      git fetch origin "$base" 2>/dev/null && git checkout "$base" \
        && grn "  [OK]   $name 已切到基线 $base" \
        || red "  [失败] $name 无法切到基线 $base（网络受限？patch 可能打不上）"
    fi
  )
}

clone_repo() {
  local url="$1" dir="$2" name="$3" base="$4"
  if [ -d "$dir/.git" ]; then
    grn "  [跳过] $name 已存在（$dir）"
  else
    ylw "  克隆 $name ..."
    git clone "$url" "$dir" || die "克隆 $name 失败"
    grn "  [OK]   $name"
  fi
  checkout_base "$dir" "$base" "$name"
}

if [ "$PATCH_ONLY" -eq 0 ]; then
  hdr "1. 克隆上游仓库"
  clone_repo "$OW_REMOTE" "$OW_DIR" "openwifi" "$OW_BASE"
  clone_repo "$HW_REMOTE" "$HW_DIR" "openwifi-hw" "$HW_BASE"
else
  hdr "1. 跳过克隆（--patch-only）"
  checkout_base "$OW_DIR" "$OW_BASE" "openwifi"
  checkout_base "$HW_DIR" "$HW_BASE" "openwifi-hw"
fi

[ -d "$OW_DIR/.git" ] || die "$OW_DIR 不是 git 仓库，请先去掉 --patch-only"
[ -d "$HW_DIR/.git" ] || die "$HW_DIR 不是 git 仓库"

# ---------------------------------------------------------------- 2. 子模块
if [ "$SKIP_SUBMODULES" -eq 0 ]; then
  hdr "2. 拉取子模块（adi-linux 内核树约 3.4GB，耗时较长）"
  ylw "  若只做 FPGA/驱动开发可加 --skip-submodules 跳过"
  ( cd "$OW_DIR" && git submodule update --init --depth 1 adi-linux ) \
    && grn "  [OK]   openwifi/adi-linux" || ylw "  [警告] adi-linux 拉取失败（驱动构建会需要）"
  ( cd "$HW_DIR" && git submodule update --init --depth 1 ip/openofdm_rx ) \
    && grn "  [OK]   openwifi-hw/ip/openofdm_rx" || ylw "  [警告] openofdm_rx 拉取失败"
  # adi-hdl：FPGA 综合需要
  ( cd "$HW_DIR" && git submodule update --init --depth 1 adi-hdl ) \
    && grn "  [OK]   openwifi-hw/adi-hdl" || ylw "  [警告] adi-hdl 拉取失败（FPGA 综合会需要）"
else
  hdr "2. 跳过子模块（--skip-submodules）"
fi

# ---------------------------------------------------------------- 3. 应用 patch
apply_patch() {
  local repo="$1" patch="$2" name="$3"
  [ -f "$patch" ] || { ylw "  [跳过] 找不到 $patch"; return; }
  ( cd "$repo" || exit 1
    if git apply --check "$patch" 2>/dev/null; then
      git apply "$patch" && grn "  [OK]   $name patch 已应用"
    elif git apply --reverse --check "$patch" 2>/dev/null; then
      grn "  [跳过] $name patch 已应用过"
    else
      red "  [失败] $name patch 无法应用 —— 上游版本可能已变，见 README「版本对齐」"
      return 1
    fi
  )
}

hdr "3. 应用改动 patch"
apply_patch "$OW_DIR" "$PATCH_DIR/openwifi-side_ch.patch"    "openwifi"
apply_patch "$HW_DIR" "$PATCH_DIR/openwifi-hw-side_ch.patch" "openwifi-hw"

# ---------------------------------------------------------------- 4. 新增源码文件
hdr "4. 拷贝新增源码文件"
NEW_SRC="$SCRIPT_DIR/new-files"
if [ -d "$NEW_SRC" ]; then
  # 保持相对路径结构
  ( cd "$NEW_SRC" && find . -type f -print0 ) | while IFS= read -r -d '' f; do
    rel="${f#./}"
    case "$rel" in
      openwifi/*)    dst="$OW_DIR/${rel#openwifi/}" ;;
      openwifi-hw/*) dst="$HW_DIR/${rel#openwifi-hw/}" ;;
      *) ylw "  [跳过] 未知归属: $rel"; continue ;;
    esac
    mkdir -p "$(dirname "$dst")"
    cp -f "$NEW_SRC/$rel" "$dst" && grn "  [OK]   $rel"
  done
else
  ylw "  [跳过] 无 new-files/ 目录"
fi

# ---------------------------------------------------------------- 5. 工具脚本
hdr "5. 拷贝工具脚本到 \$HOME/tools"
if [ -d "$TOOLS_SRC" ]; then
  mkdir -p "$HOME/tools"
  cp -f "$TOOLS_SRC"/* "$HOME/tools/" 2>/dev/null || true
  grn "  [OK]   工具已放到 $HOME/tools/（$(ls "$TOOLS_SRC" | wc -l) 个文件）"
else
  ylw "  [跳过] 无 tools/ 目录"
fi

# ---------------------------------------------------------------- 6. 体检
hdr "6. 环境体检"
echo "  --- 代码改动是否就位 ---"
for f in "$OW_DIR/driver/side_ch/side_ch.c" "$HW_DIR/ip/side_ch/src/side_ch_control.v"; do
  if grep -q "combined_init\|csi_iq_combined" "$f" 2>/dev/null; then
    grn "  [OK]   $f"
  else
    red "  [缺失] $f 中未找到联合采集改动"
  fi
done

echo "  --- 子模块 ---"
[ -e "$OW_DIR/adi-linux/.git" ] && grn "  [OK]   adi-linux" || ylw "  [待拉] adi-linux"
[ -n "$(ls -A "$HW_DIR/ip/openofdm_rx" 2>/dev/null)" ] && grn "  [OK]   openofdm_rx" || ylw "  [待拉] openofdm_rx"

echo "  --- 工具链（构建才需要）---"
if [ -f /opt/Xilinx/Vitis/2022.2/settings64.sh ]; then
  grn "  [OK]   Vitis 2022.2"
else
  ylw "  [待装] Vitis 2022.2（见 README，或用 tools/build_env.sh vitis）"
fi

hdr "完成"
cat <<EOF
  代码已就位：
    $OW_DIR      （openwifi，已含联合采集改动）
    $HW_DIR      （openwifi-hw，已含联合采集改动）

  下一步：
    # 构建环境（装 Vitis / 编内核 / 编驱动 / 综合 FPGA）
    $HOME/tools/build_env.sh all-check
    $HOME/tools/build_env.sh deps
    $HOME/tools/build_env.sh vitis-cfg && $HOME/tools/build_env.sh vitis
    $HOME/tools/build_env.sh kernel && $HOME/tools/build_env.sh driver
    $HOME/tools/build_env.sh iprepo && $HOME/tools/build_env.sh synth

    # 板端部署与采集（详见 docs/ 下操作手册）
    $HOME/tools/e316_deploy.sh 11
    $HOME/tools/e316_collect.sh 25 192.168.10.2
EOF
