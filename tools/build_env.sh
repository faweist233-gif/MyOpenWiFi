#!/bin/bash
###############################################################################
# build_env.sh —— openwifi / AntSDR E316 构建环境一站式工具（WSL 内运行）
#
# 本脚本由 27 个历史探索脚本合并而成，去掉了每次踩坑留下的重复与临时逻辑，
# 只保留"能跑通"的最终路径 + 关键踩坑注释。
#
# 合并来源（原脚本已归档，可删）：
#   子模块    setup_submodules.sh
#   Vitis     install_vitis.sh / install_vitis_offline.sh / install_vitis_offline2.sh
#             fix_install_config.sh / verify_vitis.sh / check_wsl_deps.sh
#             fix_locale.sh / check_repos.sh
#   内核      fetch_kernel_2026r1.sh / check_kernel_patches.sh / build_kernel.sh
#   驱动      build_driver.sh / verify_driver.sh
#   FPGA      run_ip_repo_gen.sh / run_vivado_synth.sh / precheck_fpga_chain.sh
#             build_side_ch_ip.sh / run_side_ch_synth.sh / apply_side_ch_changes.sh
#             make_bit_bin.sh / run_bootgen.sh
#   License   install_viterbi_license.sh / get_hostid.sh / get_hostid2.sh
#             license_apply_info.sh / collect_license_info.sh
#
# ---------------------------------------------------------------------------
# 用法（在 WSL Ubuntu-22.04 里）：
#
#   chmod +x tools/build_env.sh
#
#   ./build_env.sh deps            # 0. 检查并安装 Ubuntu 构建依赖
#   ./build_env.sh submodules      # 1. 拉取 openwifi / openwifi-hw 必要子模块
#   ./build_env.sh vitis-cfg       # 2a. 生成裁剪后的 Vitis 安装配置
#   ./build_env.sh vitis           # 2b. 离线安装 Vitis 2022.2
#   ./build_env.sh vitis-verify    # 2c. 验证 Vitis 安装完整性
#   ./build_env.sh kernel-fetch    # 3a. adi-linux 拉齐到 2026_R1 分支
#   ./build_env.sh kernel-check    # 3b. 预检 4 个内核补丁能否干净应用
#   ./build_env.sh kernel          # 3c. 构建内核树（默认 -j4）
#   ./build_env.sh driver          # 4.  构建 7 个 openwifi 驱动模块
#   ./build_env.sh driver-verify   # 4b. 验证驱动架构 / 签名 / vermagic
#   ./build_env.sh iprepo          # 5a. 生成 FPGA IP repo
#   ./build_env.sh synth           # 5b. 综合 + 实现 + 生成 bitstream（产出 .xsa）
#   ./build_env.sh bit             # 5c. 从 .xsa 解出 .bit 并转 .bit.bin
#   ./build_env.sh boot            # 5d. 生成 boot.bin
#   ./build_env.sh license-hostid  # 6a. 生成 Host ID 用于申请 Viterbi license
#   ./build_env.sh license         # 6b. 安装 Viterbi license（需 sudo）
#   ./build_env.sh all-check       # 全部前置条件体检（不写盘）
#
# 常用环境变量（可覆盖）：
#   HOME_GIT=~/git            openwifi 仓库根
#   XILINX_DIR=/opt/Xilinx
#   JOBS=4                    编译并行度（WSL 8G 内存建议 ≤4）
#   BOARD=e310v2
# ---------------------------------------------------------------------------
###############################################################################

set -u

# ------------------------------------------------------------------ 基础配置
HOME_GIT="${HOME_GIT:-$HOME/git}"
XILINX_DIR="${XILINX_DIR:-/opt/Xilinx}"
JOBS="${JOBS:-4}"
BOARD="${BOARD:-e310v2}"

OW="$HOME_GIT/openwifi"
HW="$HOME_GIT/openwifi-hw"
KDIR="$OW/adi-linux"

KVER_BRANCH="2026_R1"                       # adi-linux 目标分支（Linux 6.12）
KERNEL_PATCHES="axi_hdmi_crtc.patch ad9361_v6_12.patch ad9361_private.patch ad9361_conv.patch"

# ------------------------------------------------------------------ 输出着色
red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
hdr()  { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }
die()  { red "FAIL: $*"; exit 1; }

# ===========================================================================
# 0. deps —— Ubuntu 构建依赖
# ===========================================================================
do_deps() {
  hdr "0. 检查 / 安装 Ubuntu 构建依赖"
  local pkgs="build-essential gcc g++ make flex bison libssl-dev device-tree-compiler u-boot-tools libtinfo6"
  local missing=""
  for p in $pkgs; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      grn "  [OK]   $p"
    else
      red  "  [MISS] $p"; missing="$missing $p"
    fi
  done

  # 交叉工具链应由 Vitis 提供，不装系统包
  if command -v arm-linux-gnueabihf-gcc >/dev/null 2>&1; then
    grn "  [OK]   arm-linux-gnueabihf-gcc -> $(command -v arm-linux-gnueabihf-gcc)"
  else
    ylw "  [待装] arm-linux-gnueabihf-gcc（由 Vitis 提供，装完 Vitis 后 source settings64.sh）"
  fi

  if [ -n "$missing" ]; then
    ylw "缺失:$missing"
    read -r -p "  现在 apt 安装这些依赖？(需要 sudo 密码) (y/N) " a
    if [ "$a" = y ] || [ "$a" = Y ]; then
      sudo apt update && sudo apt install -y $missing
    fi
  else
    grn "  依赖齐备。"
  fi

  # 老安装器可能需要的 32 位辅助库（装不上不影响）
  ldconfig -p | grep -q "libncurses.so.5" || \
    sudo apt install -y libncurses5 libtinfo5 2>/dev/null || \
    ylw "  libncurses5/libtinfo5 在 22.04 源中不可用，跳过（一般不影响 Vivado/Vitis）"
  # GUI 依赖（离线批处理安装可不要）
  dpkg -s libgtk2.0-0 >/dev/null 2>&1 || \
    sudo apt install -y libgtk2.0-0 libcanberra-gtk-module 2>/dev/null || true

  # locale：安装器对非 UTF-8 locale 偶发异常
  if [ "$(locale 2>/dev/null | head -1 | cut -d= -f2)" != "C.UTF-8" ] && \
     ! locale -a 2>/dev/null | grep -q "^en_US.utf8"; then
    ylw "  建议确保 C.UTF-8 / en_US.UTF-8 可用；如需可手动 sudo dpkg-reconfigure locales"
  fi
  grn "  deps 阶段完成。"
}

# ===========================================================================
# 1. submodules —— 拉取必要子模块
# ===========================================================================
do_submodules() {
  hdr "1. 拉取必要子模块"
  # openwifi-hw/ip/openofdm_rx —— FPGA IP（openofdm）
  if [ -d "$HW" ]; then
    if [ -n "$(ls -A "$HW/ip/openofdm_rx" 2>/dev/null)" ]; then
      grn "  [OK]   openwifi-hw/ip/openofdm_rx 已存在"
    else
      ( cd "$HW" && git submodule update --init --depth 1 ip/openofdm_rx ) \
        && grn "  [OK]   openwifi-hw/ip/openofdm_rx 拉取完成" \
        || red  "  [FAIL] openofdm_rx 拉取失败"
    fi
  else
    red "  [FAIL] $HW 不存在"
  fi

  # openwifi/adi-linux —— 内核树（体积大）
  if [ -d "$OW" ]; then
    if [ -n "$(ls -A "$KDIR" 2>/dev/null)" ]; then
      grn "  [OK]   openwifi/adi-linux 已存在"
    else
      ylw "  adi-linux 是完整内核树（1-2GB），耗时较长..."
      ( cd "$OW" && git submodule update --init --depth 1 adi-linux ) \
        && grn "  [OK]   adi-linux 拉取完成" \
        || red  "  [FAIL] adi-linux 拉取失败"
    fi
  else
    red "  [FAIL] $OW 不存在"
  fi

  ylw "  说明：ad9361/adi-hdl 等待用子模块按需再拉；本次构建只需上面两个。"
}

# ===========================================================================
# 2a. vitis-cfg —— 生成裁剪后的安装配置
# ===========================================================================
do_vitis_cfg() {
  hdr "2a. 生成 Vitis 安装配置（为 E316/Zynq-7020 裁剪）"
  mkdir -p "$HOME/.Xilinx"
  local cfg="$HOME/.Xilinx/install_config.txt"
  local bak="$HOME/.Xilinx/install_config.txt.orig"

  # 若已有 ConfigGen 原始输出，先备份
  if [ -f "$cfg" ] && [ ! -f "$bak" ]; then
    cp "$cfg" "$bak" && grn "  已备份原始配置 -> $bak"
  fi

  cat > "$cfg" <<'EOF'
#### Vitis Unified Software Platform Install Configuration ####
#### 为 AntSDR E316 (Zynq-7020) 裁剪：只装 Zynq-7000 相关 ####
Edition=Vitis Unified Software Platform

Product=Vitis

# Path where Xilinx software will be installed.
Destination=/opt/Xilinx

# Choose the Products/Devices the you would like to install.
# E316 是 Zynq-7020：只需 Zynq-7000；DocNav 是文档浏览器，顺手装。
# 其余 UltraScale+/Versal/Alveo/Kria/Model Composer 全部去掉。
Modules=Zynq-7000:1,DocNav:1,Artix-7:0,Kintex-7:0,Virtex-7:0,Spartan-7:0,Kintex UltraScale:0,Kintex UltraScale+:0,Virtex UltraScale:0,Virtex UltraScale+:0,Virtex UltraScale+ 58G:0,Virtex UltraScale+ HBM:0,Virtex UltraScale+ HBM ES:0,Artix UltraScale+:0,Zynq UltraScale+ MPSoC:0,Zynq UltraScale+ RFSoC:0,Versal AI Core Series:0,Versal AI Core Series ES1:0,Versal AI Edge Series:0,Versal AI Edge Series ES1:0,Versal Prime Series:0,Versal Prime Series ES1:0,Versal Premium Series:0,Versal Premium Series ES1:0,Versal HBM Series ES1:0,Versal Net Series ES1:0,Engineering Sample Devices for Custom Platforms:0,Vitis Model Composer(Xilinx Toolbox for MATLAB and Simulink. Includes the functionality of System Generator for DSP):0,Vitis IP Cache (Enable faster on-boarding for new users):0,Install devices for Alveo and Xilinx edge acceleration platforms:0,Install Devices for Kria SOMs and Starter Kits:0

# Choose the post install scripts you'd like to run as part of the finalization step.
InstallOptions=

## Shortcuts and File associations ##
CreateProgramGroupShortcuts=1
ProgramGroupFolder=Xilinx Design Tools
CreateShortcutsForAllUsers=0
CreateDesktopShortcuts=1
CreateFileAssociation=1
EnableDiskUsageOptimization=1
EOF

  grn "  配置已写入 $cfg"
  echo "  --- 关键行 ---"
  grep -E "^Edition=|^Product=|^Destination=" "$cfg"
  echo "  --- 选中器件（值为 1）---"
  grep "^Modules=" "$cfg" | tr ',' '\n' | grep ":1$"

  ylw "  ★ 踩坑记忆：Edition 必须是 'Vitis Unified Software Platform'，"
  ylw "    早前手写的 'Vitis 2022.2' 安装器不认，会报 invalid edition。"
  ylw "    最稳妥是用安装器自带 ConfigGen 生成模板再裁剪："
  ylw "      cd ~/xilinx_installer/Xilinx_Unified_2022.2_1014_8888 && ./xsetup -b ConfigGen"
}

# ===========================================================================
# 2b. vitis —— 离线安装 Vitis
# ===========================================================================
do_vitis() {
  hdr "2b. 离线安装 Vitis 2022.2"
  local instdir="$HOME/xilinx_installer/Xilinx_Unified_2022.2_1014_8888"
  local cfg="$HOME/.Xilinx/install_config.txt"
  local log="$HOME/vitis_install_$(date +%Y%m%d_%H%M%S).log"

  # ★ 关键：绝不能以 root 运行！否则 ~/.Xilinx 归 root，Vitis 无法写缓存，
  #   导入 .xsa 会静默卡在 "Loading Platform"。
  [ "$(id -u)" -eq 0 ] && die "不要用 root 运行安装器。请以普通用户执行（脚本内部会用 sudo 建目录）。"

  [ -f "$instdir/xsetup" ] || die "找不到 $instdir/xsetup，请先解压离线包到该目录。"
  [ -f "$cfg" ] || die "找不到 $cfg，请先执行：./build_env.sh vitis-cfg"

  # payload 完整性（官方离线包应为 1033 片 .xz）
  local n; n=$(find "$instdir/payload" -name '*.xz' 2>/dev/null | wc -l)
  echo "  payload 分片: $n / 1033"
  if [ "$n" -lt 1000 ]; then
    die "离线包不完整（可能仍在拷贝中）"
  fi

  local free; free=$(df -BG / | awk 'NR==2{gsub("G","",$4); print $4}')
  echo "  根分区可用: ${free}GB（完整安装约需 180GB+）"
  [ "$free" -lt 100 ] && ylw "  警告：空间可能不足"

  # 跨文件系统拷贝会丢可执行权限
  chmod +x "$instdir/xsetup"
  find "$instdir/bin"     -name '*.sh' -exec chmod +x {} \; 2>/dev/null
  find "$instdir/scripts" -name '*.sh' -exec chmod +x {} \; 2>/dev/null

  sudo mkdir -p "$XILINX_DIR" && sudo chown "$USER:$USER" "$XILINX_DIR"

  echo "  日志: $log"
  hdr "  开始安装（30-90 分钟，请勿中断）"
  ( cd "$instdir" && ./xsetup -a XilinxEULA,3rdPartyEULA -b Install -c "$cfg" 2>&1 | tee "$log" )
  local rc=${PIPESTATUS[0]}

  if [ -d "$XILINX_DIR/Vitis/2022.2" ]; then
    grn "  ★ 安装成功: $XILINX_DIR/Vitis/2022.2"
  else
    ylw "  批处理安装返回码 $rc，且未发现安装目录。"
    ylw "  改用 GUI 安装：cd $instdir && export DISPLAY=\${DISPLAY:-:0} && ./xsetup"
    ylw "  GUI 里勾选 Vitis（⚠ 不要选 Vitis HLS，它不含 Vivado）→ 路径 $XILINX_DIR → 同意协议。"
  fi
  grn "  下一步：./build_env.sh vitis-verify"
}

# ===========================================================================
# 2c. vitis-verify —— 验证 Vitis 安装
# ===========================================================================
do_vitis_verify() {
  hdr "2c. 验证 Vitis 2022.2 安装"
  local settings="$XILINX_DIR/Vitis/2022.2/settings64.sh"

  for d in "$XILINX_DIR/Vitis/2022.2" "$XILINX_DIR/Vivado/2022.2"; do
    [ -d "$d" ] && grn "  [OK]   $d" || red "  [缺失] $d"
  done

  [ -f "$settings" ] || die "找不到 $settings —— Vitis 未装或路径不对"
  # shellcheck disable=SC1090
  source "$settings" && grn "  已 source settings64.sh"

  local missing=0
  for cmd in vivado xsct bootgen arm-linux-gnueabihf-gcc; do
    if command -v "$cmd" >/dev/null 2>&1; then
      grn "  [OK]   $cmd -> $(command -v "$cmd")"
    else
      red "  [缺失] $cmd"; missing=$((missing+1))
    fi
  done

  command -v vivado >/dev/null 2>&1 && vivado -version 2>&1 | head -2

  if [ "$missing" -eq 0 ]; then
    grn "  ★★ 全部就绪，可进入内核/驱动构建 ★★"
  else
    red "  有 $missing 个命令缺失 —— 安装不完整"
    ylw "  最常见原因：安装时只勾了 Vivado（或 Vitis HLS），没勾 Vitis。重跑安装器补勾 Vitis。"
  fi
}

# ===========================================================================
# 3a. kernel-fetch —— adi-linux 拉齐 2026_R1
# ===========================================================================
do_kernel_fetch() {
  hdr "3a. adi-linux 拉齐到 $KVER_BRANCH"
  [ -d "$KDIR/.git" ] || die "$KDIR 不是 git 仓库"

  # ★ 踩坑：prepare_kernel.sh 内部会硬性 `git checkout 2026_R1`，
  #   而子模块 pinned commit 是 2019_R1 的浅克隆 —— 分支不存在会直接失败。
  #   所以必须先把 2026_R1 拉下来并解除 shallow。
  ( cd "$KDIR" \
    && echo "  当前 HEAD: $(git log --oneline -1)" \
    && git fetch --depth 1 origin "$KVER_BRANCH" \
    && git checkout -B "$KVER_BRANCH" "origin/$KVER_BRANCH" \
    && ( git fetch --unshallow 2>/dev/null || true ) \
    && echo "  拉齐后 HEAD: $(git log --oneline -1)" ) \
    && grn "  [OK] $KVER_BRANCH 就绪" \
    || red "  [FAIL] 拉取 $KVER_BRANCH 失败"
}

# ===========================================================================
# 3b. kernel-check —— 预检内核补丁
# ===========================================================================
do_kernel_check() {
  hdr "3b. 预检 4 个内核补丁"
  local pdir="$OW/kernel_boot"
  ( cd "$KDIR" || exit 1
    echo "  HEAD: $(git log --oneline -1)"
    echo "  工作区改动: $(git status --porcelain | wc -l) 项"
    local fail=0
    for p in $KERNEL_PATCHES; do
      if [ ! -f "$pdir/$p" ]; then red "  [缺失] $p"; fail=1; continue; fi
      if git apply --check "$pdir/$p" 2>/dev/null; then
        grn "  [可应用] $p"
      else
        red "  [冲突]   $p"; fail=1
      fi
    done
    [ "$fail" -eq 0 ] && grn "  全部补丁可干净应用。" \
                      || ylw "  有补丁冲突，先 git reset --hard 再重试。"
  )
}

# ===========================================================================
# 3c. kernel —— 构建内核树
# ===========================================================================
do_kernel() {
  hdr "3c. 构建 openwifi 内核树（ARM 32 位 / Zynq-7020, -j$JOBS）"
  # ★ 踩坑：prepare_kernel.sh 里硬编码 `make -j12`，WSL 8G 内存必触发 OOM。
  #   这里临时把并行度改小（改副本，跑完恢复），并全程记日志。
  local prep="$OW/user_space/prepare_kernel.sh"
  [ -f "$prep" ] || die "找不到 $prep"

  free -h | head -2
  local tmp="$prep.buildenv.$$"
  sed "s/make -j12/make -j$JOBS/g" "$prep" > "$tmp"
  chmod +x "$tmp"
  ylw "  已用 -j$JOBS 替换 prepare_kernel.sh 中的 -j12（临时副本，不改原文件）"

  ( cd "$OW/user_space" && source "$XILINX_DIR/Vitis/2022.2/settings64.sh" && ./"$(basename "$tmp")" "$XILINX_DIR" 32 ) \
    2>&1 | tee "$HOME/kernel_build.log" | tail -40
  local rc=${PIPESTATUS[0]}
  rm -f "$tmp"

  # 产物校验
  for f in "$KDIR/arch/arm/boot/uImage" "$KDIR/include/config/kernel.release"; do
    [ -f "$f" ] && grn "  [OK]   $f" || ylw "  [缺失] $f"
  done
  grn "  内核构建结束（rc=$rc），日志: ~/kernel_build.log"
}

# ===========================================================================
# 4. driver —— 构建驱动
# ===========================================================================
do_driver() {
  hdr "4. 构建 openwifi 驱动（7 个 .ko, -j$JOBS）"
  [ -f "$KDIR/.config" ] || die "内核树未构建。请先执行：./build_env.sh kernel"
  [ -f "$KDIR/Module.symvers" ] || die "缺 Module.symvers，内核树不完整。"

  ( cd "$OW/driver" && source "$XILINX_DIR/Vitis/2022.2/settings64.sh" \
      && make -j"$JOBS" ) \
    2>&1 | tee "$HOME/driver_build.log" | tail -30
  local rc=${PIPESTATUS[0]}

  hdr "  产物"
  find "$OW/driver" -name '*.ko' -printf '  %s\t%p\n' 2>/dev/null | sort
  grn "  驱动构建结束（rc=$rc），日志: ~/driver_build.log"
}

# ===========================================================================
# 4b. driver-verify
# ===========================================================================
do_driver_verify() {
  hdr "4b. 验证驱动模块"
  local od="$XILINX_DIR/Vitis/2022.2/gnu/aarch32/lin/gcc-arm-linux-gnueabi/bin/arm-linux-gnueabihf-objdump"
  local d="$OW/driver"

  echo "  --- 架构 ---"
  for ko in openofdm_tx/openofdm_tx.ko openofdm_rx/openofdm_rx.ko tx_intf/tx_intf.ko \
            rx_intf/rx_intf.ko xpu/xpu.ko side_ch/side_ch.ko sdr.ko; do
    local f="$d/$ko"
    if [ -f "$f" ]; then
      printf '  %-26s %s\n' "$(basename "$ko")" "$("$od" -f "$f" 2>/dev/null | grep architecture: | head -1)"
    else
      printf '  %-26s [缺失]\n' "$(basename "$ko")"
    fi
  done
  echo "  --- side_ch 联合采集参数 ---"
  [ -f "$d/side_ch/side_ch.ko" ] && modinfo "$d/side_ch/side_ch.ko" 2>/dev/null | grep '^parm'
  echo "  --- vermagic（须与板上内核一致）---"
  for f in $(find "$d" -name '*.ko' 2>/dev/null | sort); do
    printf '  %-30s %s\n' "$(basename "$f")" "$(modinfo "$f" 2>/dev/null | awk '/^vermagic/{print $2}')"
  done
}

# ===========================================================================
# 5a. iprepo
# ===========================================================================
do_iprepo() {
  hdr "5a. 生成 FPGA IP repo"
  local dir="$HW/boards/$BOARD"
  [ -d "$dir" ] || die "$dir 不存在"
  [ -f "$XILINX_DIR/Vitis/2022.2/settings64.sh" ] || die "缺 Vitis settings64.sh"
  ( cd "$dir" && "$HW/boards/create_ip_repo.sh" "$XILINX_DIR" 2>&1 | tee "$HOME/ip_repo_gen.log" | tail -40 )
  grn "  IP repo 结束，产物: $dir/ip_repo/，日志: ~/ip_repo_gen.log"
}

# ===========================================================================
# 5b. synth
# ===========================================================================
do_synth() {
  hdr "5b. Vivado 综合 + 实现 + bitstream（-j$JOBS）"
  local dir="$HW/boards/$BOARD"
  local tcl="$dir/openwifi.tcl"
  [ -d "$dir/ip_repo" ] || die "ip_repo 未生成，请先：./build_env.sh iprepo"
  [ -f "$tcl" ] || die "找不到 $tcl"

  # ★ 踩坑：openwifi.tcl 写死 impl_1 -jobs 8，WSL 内存扛不住，临时改小
  local tmp="$tcl.buildenv.$$"
  sed "s/impl_1 -jobs 8/impl_1 -jobs $JOBS/g" "$tcl" > "$tmp"
  ( cd "$dir" && source "$XILINX_DIR/Vitis/2022.2/settings64.sh" \
      && vivado -mode batch -source "$(basename "$tmp")" \
      2>&1 | tee "$HOME/vivado_synth.log" | tail -40 )
  rm -f "$tmp"

  local xsa="$dir/openwifi_$BOARD/system_top.xsa"
  [ -f "$xsa" ] && grn "  [OK] $xsa" || ylw "  未找到 .xsa，看日志: ~/vivado_synth.log"
}

# ===========================================================================
# 5c. bit
# ===========================================================================
do_bit() {
  hdr "5c. .xsa -> system_top.bit -> .bit.bin"
  local xsa="$HW/boards/$BOARD/openwifi_$BOARD/system_top.xsa"
  [ -f "$xsa" ] || die "找不到 $xsa"
  ( cd "$OW/user_space" || exit 1
    source "$XILINX_DIR/Vitis/2022.2/settings64.sh" >/dev/null 2>&1
    python3 - "$xsa" <<'PY'
import sys, zipfile, shutil, os
xsa = sys.argv[1]
with zipfile.ZipFile(xsa) as z, z.open('system_top.bit') as f, open('system_top.bit','wb') as o:
    shutil.copyfileobj(f, o)
print("  解压 system_top.bit 完成")
PY
    [ -f fpga_bit_to_bin.bif ] || die "缺 fpga_bit_to_bin.bif"
    rm -f ./system_top.bit.bin
    bootgen -image fpga_bit_to_bin.bif -arch zynq -process_bitstream bin -w
    ls -l ./system_top.bit ./system_top.bit.bin 2>/dev/null
  )
}

# ===========================================================================
# 5d. boot
# ===========================================================================
do_boot() {
  hdr "5d. 生成 boot.bin"
  local xsa="$HW/boards/$BOARD/openwifi_$BOARD/system_top.xsa"
  [ -f "$xsa" ] || die "找不到 $xsa"
  ( cd "$OW/user_space" || exit 1
    ./boot_bin_gen.sh "$XILINX_DIR" "$BOARD" "$xsa" > "$HOME/boot_bin_gen.log" 2>&1
    echo "  退出码: $?"
    tail -40 "$HOME/boot_bin_gen.log"
    find "$OW/kernel_boot/boards/$BOARD/output_boot_bin" -type f 2>/dev/null -printf '  %s bytes  %p\n'
  )
}

# ===========================================================================
# 6a. license-hostid
# ===========================================================================
do_license_hostid() {
  hdr "6a. 收集 Viterbi license 申请信息"
  local lm="$XILINX_DIR/Vivado/2022.2/bin/unwrapped/lnx64.o/lmutil"
  local ld=/lib64/ld-linux-x86-64.so.2
  if [ -x "$lm" ]; then
    echo "  lmutil lmhostid        : $("$lm" lmhostid 2>&1 | head -1)"
    echo "  lmutil lmhostid -ether : $("$lm" lmhostid -ether 2>&1 | head -1)"
  elif [ -x "$ld" ]; then
    ylw "  lmutil 直接执行失败，用显式 loader 绕过："
    "$ld" "$lm" lmhostid 2>&1 | head -5
  else
    red "  找不到 lmutil"
  fi
  echo "  --- 所有网卡 MAC（候选 Host ID）---"
  ip link 2>/dev/null | grep -oP '(?<=link/ether )[0-9a-f:]{17}'
  ylw "  ★ 注意：WSL 的 eth0 MAC 每次冷启动都会变，不能用它申请 license。"
  ylw "    应使用宿主机 Windows 物理网卡的 MAC（如 WLAN），它是稳定的。"
  echo "  申请信息：产品 Viterbi Decoder (LogiCORE IP)，Part EF-DI-VITERBI-SITE，版本 9.1"
}

# ===========================================================================
# 6b. license
# ===========================================================================
do_license() {
  hdr "6b. 安装 Viterbi license（需 sudo）"
  local src="$HOME/Xilinx.lic"
  local destdir="$XILINX_DIR/licenses"
  local profile=/etc/profile.d/xilinx-license.sh
  [ -f "$src" ] || die "找不到 $src"
  sudo mkdir -p "$destdir"
  sudo cp -f "$src" "$destdir/Xilinx.lic"
  sudo tee "$profile" >/dev/null <<EOF
export XILINXD_LICENSE_FILE=$destdir/Xilinx.lic
export LM_LICENSE_FILE=$destdir/Xilinx.lic
EOF
  grn "  license 已安装到 $destdir/Xilinx.lic"
  grn "  环境变量写入 $profile（新终端生效，或 source 它）"
}

# ===========================================================================
# all-check
# ===========================================================================
do_all_check() {
  hdr "全链路体检（只读，不写盘）"
  local pass=0 fail=0 warn=0
  chk() { if eval "$2"; then grn "  [OK]   $1"; pass=$((pass+1)); else red "  [FAIL] $1"; fail=$((fail+1)); fi; }
  chkw(){ if eval "$2"; then grn "  [OK]   $1"; pass=$((pass+1)); else ylw "  [WARN] $1"; warn=$((warn+1)); fi; }

  echo "  --- 仓库 ---"
  chk  "openwifi 仓库 ($OW)"      "[ -d '$OW' ]"
  chk  "openwifi-hw 仓库 ($HW)"   "[ -d '$HW' ]"
  # 注意：子模块的 .git 是「文件」（指向 ../.git/modules/adi-linux），不是目录，
  # 所以必须用 -e 判断，用 -d 会误报缺失。
  chk  "adi-linux 内核树"          "[ -e '$KDIR/.git' ]"
  chkw "openofdm_rx 子模块"        "[ -n \"\$(ls -A '$HW/ip/openofdm_rx' 2>/dev/null)\" ]"

  echo "  --- 工具链 ---"
  chk  "Vitis settings64.sh"      "[ -f '$XILINX_DIR/Vitis/2022.2/settings64.sh' ]"
  chk  "Vivado settings64.sh"     "[ -f '$XILINX_DIR/Vivado/2022.2/settings64.sh' ]"
  chk  "交叉编译器 (gcc)"          "[ -x '$XILINX_DIR/Vitis/2022.2/gnu/aarch32/lin/gcc-arm-linux-gnueabi/bin/arm-linux-gnueabihf-gcc' ]"
  chk  "bootgen"                  "[ -x '$XILINX_DIR/Vitis/2022.2/bin/bootgen' ]"

  echo "  --- 内核树状态 ---"
  chkw "内核 .config"              "[ -f '$KDIR/.config' ]"
  chkw "Module.symvers"           "[ -f '$KDIR/Module.symvers' ]"
  chkw "uImage"                   "[ -f '$KDIR/arch/arm/boot/uImage' ]"

  echo "  --- 驱动产物 ---"
  for ko in openofdm_tx openofdm_rx tx_intf rx_intf xpu side_ch; do
    chkw "$ko.ko" "[ -f '$OW/driver/$ko/$ko.ko' ]"
  done
  chkw "sdr.ko"                   "[ -f '$OW/driver/sdr.ko' ]"

  echo "  --- FPGA 产物 ---"
  chkw "ip_repo"                  "[ -d '$HW/boards/$BOARD/ip_repo' ]"
  chkw "system_top.xsa"           "[ -f '$HW/boards/$BOARD/openwifi_$BOARD/system_top.xsa' ]"

  echo "  --- license ---"
  chkw "Viterbi license"          "[ -f '$XILINX_DIR/licenses/Xilinx.lic' ]"

  hdr "  结果: $pass 通过 / $warn 警告 / $fail 失败"
}

# ===========================================================================
# 分发
# ===========================================================================
case "${1:-}" in
  deps)           do_deps ;;
  submodules)     do_submodules ;;
  vitis-cfg)      do_vitis_cfg ;;
  vitis)          do_vitis ;;
  vitis-verify)   do_vitis_verify ;;
  kernel-fetch)   do_kernel_fetch ;;
  kernel-check)   do_kernel_check ;;
  kernel)         do_kernel ;;
  driver)         do_driver ;;
  driver-verify)  do_driver_verify ;;
  iprepo)         do_iprepo ;;
  synth)          do_synth ;;
  bit)            do_bit ;;
  boot)           do_boot ;;
  license-hostid) do_license_hostid ;;
  license)        do_license ;;
  all-check)      do_all_check ;;
  *)
    sed -n '2,50p' "$0" | sed 's/^# \?//'
    exit 2
    ;;
esac
