# openwifi CSI+IQ 联合采集 —— WSL2 从零构建到上板部署全流程

> 本文把 [部署文档 §3] 进一步展开成"Windows WSL2 下一台新 Ubuntu，从装环境到 `scp` 上板"的**逐条可执行流程**。
> 任务对应《OpenWiFi-CSI-IQ同时采集-开发文档.md》**路线 A**（改了 `side_ch` RTL + 驱动，要重新综合 FPGA + 重编 `.ko`）。
> 硬前提：**Vivado/Vitis 2022.2（Linux 版）+ Viterbi 评估 license**；目标板 AntSDR E316（`BOARD_NAME=e310v2`，Zynq-7020，32 位）。
> macOS 的角色只是"编辑代码 + 传文件"，关掉这个文档前你只需要一样东西：**一台能跑 Ubuntu 的 Linux x86_64**——本文用 WSL2 提供它。

---

> ### ⚡ 快速通道：已合并为一站式脚本
>
> 本文 §2、§3、§5、§6、§7 的步骤（装依赖 → 装 Vitis → 内核树 → 驱动 → FPGA 综合）
> 已全部合并进 **`tools/build_env.sh`**，按 stage 依次执行即可：
>
> ```bash
> cd /mnt/c/OpenWiFi/tools
> ./build_env.sh deps          # §2 系统依赖
> ./build_env.sh submodules    # §4 子模块
> ./build_env.sh vitis-cfg     # §3 Vitis 配置（裁剪版，仅 Zynq-7000）
> ./build_env.sh vitis         # §3 Vitis 安装
> ./build_env.sh vitis-verify  # §3 安装校验
> ./build_env.sh kernel-fetch  # §5 内核树拉齐 2026_R1
> ./build_env.sh kernel-check  # §5 补丁预检
> ./build_env.sh kernel        # §5 构建内核树
> ./build_env.sh driver        # §6 编 7 个 .ko
> ./build_env.sh iprepo        # §7 IP repo
> ./build_env.sh synth         # §7 综合+实现
> ./build_env.sh bit           # §7 .xsa → .bit.bin
> ./build_env.sh boot          # §7 boot.bin
> ./build_env.sh all-check     # 全链路体检（随时可跑）
> ```
>
> 详细说明与 11 条踩坑记忆见 **`tools/README-build-env.md`**。
> 本文保留为**原理讲解与排障参考**；两者冲突时以脚本内的踩坑注释为准。

---

## 0. 你最终要产出哪两样东西（先建立心智模型）

| 产物 | 从哪来 | 干什么 |
|---|---|---|
| `system_top.bit.bin` | FPGA 综合 → boot_bin_gen | 新 bitstream（含联合模式 side_ch），热重载用 |
| `*.ko`（7 个，含 `side_ch.ko`） | `make_all.sh 32` 交叉编译 | 与新版 bitstream 配套的驱动 |

**不重编/不重烧**：内核 `uImage`、`.dtb`、SD 镜像——这些沿用板上现有的。`boot_bin_gen.sh` 会顺带产出 `BOOT.BIN`（在 `openwifi/kernel_boot/boards/e310v2/output_boot_bin/`），但热重载只用到 `system_top.bit.bin`，BOOT.BIN 无需烧写。所以流水线只有两条，耗时：**驱动分钟级，FPGA 1–3 小时**。

```
WSL2(Ubuntu 22.04)
├─ 驱动:  prepare_kernel.sh(一次,30-60min) → make_all.sh(增量,快) → *.ko
├─ FPGA:  create_ip_repo.sh → openwifi.tcl 综合(1-3h) → sdk_update → boot_bin_gen → bit.bin
└─ 部署:  Windows 侧 scp → 板上 wgd.sh → insmod side_ch.ko → side_ch_ctl
```

---

## 1. 安装 WSL2 + Ubuntu 22.04（Windows 侧，10 分钟）

在 Windows PowerShell（管理员）里：

```powershell
# 1) 启用 WSL（会自动装 WSL2 + 默认 Ubuntu）
wsl --install -d Ubuntu-22.04
# 若旧机器: 先确认虚拟机平台已开，然后
#   wsl --set-default-version 2
# 重启 Windows（首次安装需要）

# 2) 装完按提示设 Linux 用户名/密码（Unix 账号，需记住）

# 3) 更新内核（可选但稳妥）
wsl --update

# 4) 确认是 WSL2
wsl -l -v        # 应显示 VERSION 2；若是 1，执行 wsl --set-version Ubuntu-22.04 2
```

> WSL1 不行，Vivado/交叉编译必须 WSL2（真正的 Linux 内核）。

进到 Ubuntu shell：

```bash
wsl -d Ubuntu-22.04
sudo apt update && sudo apt upgrade -y
```

**配一个开发用户 + 固定路径**（后面所有命令都按这个来）：

```bash
mkdir -p ~/git                # 仓库与工程都放 Linux 文件系统！别放 /mnt/c
echo 'export XILINX_DIR=/opt/Xilinx' >> ~/.bashrc
echo 'export OPENWIFI_HW_IMG_DIR=~/git/openwifi-hw-img' >> ~/.bashrc
source ~/.bashrc
```

> ⚠️ 铁律：代码/工程一律放 `~/git/` 下的 ext4，**不要**放 `/mnt/c/...`（NTFS 挂载会让 Vivado/`git` 卡死、文件属性错乱）。

---

## 2. Ubuntu 系统依赖（10 分钟）

```bash
sudo apt install -y \
  build-essential gcc g++ make \
  flex bison libssl-dev \
  device-tree-compiler u-boot-tools \
  libtinfo6
# 老一点的 libtinfo5（有些脚本还依赖）：
sudo apt install -y libtinfo5 || echo "22.04 无 libtinfo5 则跳过（一般不影响）"
# 交叉工具链由 Vitis 2022.2 自带（arm-linux-gnueabihf-），不用单独装

# 常用调试工具（可选）
sudo apt install -y git git-lfs ssh rsync net-tools openssh-server
```

---

## 3. 安装 Vivado/Vitis 2022.2（WSL 内，约 15 GB 磁盘 / 15–30 分钟安装）

Vivado 的综合、`xsct`、`bootgen`、交叉编译 `settings64.sh` 全都要 **Linux 版**装进 WSL。

**3.1 下载 Linux 版安装包**

从 AMD/Xilinx 官网（需登录）下载 **Vivado & Vitis 2022.2 - Linux** 安装包：在线安装器（`Xilinx_Unified_2022.2_1014_2254_Lin64.bin`）约 300 MB（边下边装，实际占用 ~100 GB+），完整离线 DVD 镜像约 123 GB。可只装 Vitis 组件。

**3.2 安装**

```bash
chmod +x Xilinx_Unified_2022.2_1014_2254_Lin64.bin
sudo mkdir -p /opt/Xilinx
```

* 方式 A（批处理，推荐）——推荐直接用 `tools/build_env.sh vitis-cfg && tools/build_env.sh vitis`（配置模板已踩坑修正）；手写安装配置时注意：

> ★ 踩坑：`Edition` 键名必须是 **`Vitis Unified Software Platform`**，写 `Vitis 2022.2` 安装器不认（invalid edition）。详见 `tools/README-build-env.md` 踩坑 2。

```
Edition=Vitis Unified Software Platform
Product=Vitis
Destination=/opt/Xilinx
```
然后：
```bash
sudo ./Xilinx_Unified_2022.2_1014_2254_Lin64.bin -b Install -a XilinxEULA,3rdPartyEULA \
  -c /path/to/vivado_install_config.txt
```
* 方式 B（图形化）——WSLg（Win11）已自带显示，直接 `sudo ./Xilinx_Unified_2022.2_1014_2254_Lin64.bin` 走 GUI，勾选 **Vitis**（标签页勾 "Vitis" 含 Vivado）。

**3.3 license（Viterbi 评估 license）**

`openofdm_rx` 的 Viterbi 解码器需要评估/正式 license。获取 AMD 官网评估 license（.lic 文件，随账号发邮箱）。WSL 里加载：

```bash
# 永久方式：写进 bashrc
echo 'export XILINX_LIC_FILE=/path/to/XilinxVitis.lic' >> ~/.bashrc
# 或图形方式：开 vivado → Help → Manage License → Load License
source ~/.bashrc
```

**3.4 验证**

```bash
source /opt/Xilinx/Vitis/2022.2/settings64.sh
which vivado  xsct  bootgen  arm-linux-gnueabihf-gcc   # 前面几个必须能找到
vivado -version
```

---

## 4. 拉代码 + 把你 Mac 上的改动搬进来（20 分钟）

**4.1 WSL 里克隆三个仓库 + 两个子模块**

```bash
cd ~/git
git clone https://github.com/open-sdr/openwifi.git
git clone https://github.com/open-sdr/openwifi-hw.git
git clone https://github.com/open-sdr/openwifi-hw-img.git    # $OPENWIFI_HW_IMG_DIR
cd openwifi-hw
./get_ip_openofdm_rx.sh        # 拉 openofdm_rx 子模块（system.bd 依赖，缺了综合不过）
cd ../openwifi
git submodule update --init adi-linux       # 内核源码树(6.12)，约 3.4 GB（setup.sh 口径）
```

> 若 `adio-linux` 拉得慢，可 `git submodule update --init --depth 1 adi-linux` 再按 §5 完整 checkout。

**4.2 把你的改动搬进 WSL**

> ★ 本仓库已提供 `patches/` + `new-files/`，`./setup.sh` 会自动套用，无需下面的手工搬运流程。

你 Mac 工作区（`/Users/juicy/Projects/OpenWiFi/`）里有两个仓库没提交的改动。**任选一种**：

* 方案 1（推荐，最不易错）：Mac 上打个包 → 传进 WSL → 解压覆盖。
```bash
# —— Mac 侧 ——
cd /Users/juicy/Projects/OpenWiFi && tar czf openwifi_csi_iq.tar.gz \
  openwifi/driver/side_ch/side_ch.c openwifi/driver/side_ch/side_ch.h \
  openwifi/user_space/side_ch_ctl_src/csi_iq_display.py \
  openwifi-hw/ip/side_ch/src/side_ch.v openwifi-hw/ip/side_ch/src/side_ch_control.v \
  openwifi-hw/ip/side_ch/unit_test/side_ch_control_tb/
# （怎么传进 WSL 都行：scp 到 Windows 同网机器、U 盘、网盘；
#   同机的话直接把文件放进 \\wsl$\Ubuntu-22.04\home\<用户>\git\）
```
```bash
# —— WSL 侧 —— 解压覆盖到对应仓库
cd ~/git && tar xzf openwifi_csi_iq.tar.gz
git -C openwifi diff --stat        # 应看到 side_ch.c / side_ch.h 已改
git -C openwifi-hw diff --stat     # 应看到 side_ch.v / side_ch_control.v 已改
```

* 方案 2（提交后拉取）：Mac 上先把两个仓库的改动 commit（Conventional Commits），再 push 到你们私有远端，WSL 里 `git fetch && git merge`/`cherry-pick`。

---

## 5. 准备内核源码树（一次，30–60 分钟）——编 `.ko` 的前提

`.ko` 必须和板上内核（ADI 2026_R1，Linux 6.12）同 vermagic，所以要先把 `adi-linux` 备到 6.12 并 `modules_prepare`：

```bash
cd openwifi/user_space
source /opt/Xilinx/Vitis/2022.2/settings64.sh
./prepare_kernel.sh $XILINX_DIR 32      # fetch/checkout 2026_R1 + 4 个补丁 + oldconfig + prepare + uImage + modules
```

> 只做一次；之后增量很快。产出：`adi-linux/arch/arm/boot/uImage`、`adi-linux` 内各 `.ko`。
> 这步**不需要**上板，也不用拷 uImage 到板（咱们不动内核）。

---

## 6. 编驱动 → 7 个 `.ko`（分钟级）

```bash
cd openwifi/driver
./make_all.sh $XILINX_DIR 32
ls -la sdr.ko side_ch/side_ch.ko tx_intf/tx_intf.ko rx_intf/rx_intf.ko \
       openofdm_tx/openofdm_tx.ko openofdm_rx/openofdm_rx.ko xpu/xpu.ko
```

> `make_all.sh` 依次编七个模块，`side_ch` 走 `make_driver.sh`。它会生成 `driver/pre_def.h`、`driver/git_rev.h`（生成文件，别提交）。
> 若 later `insmod side_ch.ko` 报 `version magic`/symbol 错误 → 说明板上内核与 WSL 编的内核版本不一致，多半是没做 §5 或版本对不上。

---

## 7. 综合 FPGA → `system_top.bit.bin`（1–3 小时，license 必需的步骤）

你的改动是 **side_ch 的 RTL**，必须重综合（不是现成的 xsa）：

```bash
source /opt/Xilinx/Vitis/2022.2/settings64.sh

# 7.1 生成 IP 宏文件 + 刷新 ip_repo（把改过的 side_ch 收进工程）
cd openwifi-hw/boards/e310v2
../create_ip_repo.sh $XILINX_DIR
#    会生成 boards/e310v2/ip_config/*_pre_def.v、ip_repo/ 等（生成物，不提交）

# 7.2 顶层综合 + 实现 + 出 bitstream（主耗时，建议 nohup 挂着）
cd openwifi-hw/boards/e310v2
nohup vivado -mode batch -source ../openwifi.tcl > synth.log 2>&1 &
tail -f synth.log
#   → 结束后看末尾 "write_bitstream completed successfully"
#   → 产物 openwifi_e310v2/openwifi_e310v2.runs/impl_1/system_top.bit

# 7.3 归档 xsa 到 openwifi-hw-img（boot_bin_gen 用）
cd openwifi-hw/boards
./sdk_update.sh e310v2 $OPENWIFI_HW_IMG_DIR
#   → $OPENWIFI_HW_IMG_DIR/boards/e310v2/sdk/system_top.xsa

# 7.4 出 bit.bin（bootgen 把 .bit → .bit.bin）
cd openwifi/user_space
./boot_bin_gen.sh $XILINX_DIR e310v2 $OPENWIFI_HW_IMG_DIR/boards/e310v2/sdk/system_top.xsa
ls -la system_top.bit.bin       # 在 openwifi/user_space/ 下（热重载用这个）
ls -la ../kernel_boot/boards/e310v2/output_boot_bin/   # BOOT.BIN 被 boot_bin_gen.sh 移到此处
```

> 综合第一步要升/编子 IP 很慢（只一次）。`impl_1` 的并行度：**本仓库补丁已把 `openwifi.tcl` 改为 `-jobs 3`**（防 WSL 内存 OOM，见 README 改动清单）；若用未打补丁的上游文件则写死 `-jobs 8`，8G 内存 WSL 有 OOM 风险。
> 若中途失败先看 `synth.log`；常见是 license 未加载（Viterbi）、缺库、或 `openofdm_rx` 子模块没拉。

---

## 8. 打包 + 上板部署（Windows 侧，不用进 WSL）

**部署与板子（`192.168.10.122`）通信从 Windows 侧做更省心**（WSL2 默认 NAT，纯私网直连要么开 mirrored 要么绕一圈）。Windows 自带 `scp`/`ssh`：

```powershell
# 路径用 WSL 的网络路径 \\wsl$\Ubuntu-22.04\home\<你>\git\... 或从 WSL 里拷到 /mnt/c 再 scp

# 8.1 传 bitstream + 全部 .ko（wgd.sh 要求与它同目录）
scp \\wsl$\Ubuntu-22.04\home\<你>\git\openwifi\user_space\system_top.bit.bin root@192.168.10.122:openwifi/
scp \\wsl$\Ubuntu-22.04\home\<你>\git\openwifi\driver\*.ko root@192.168.10.122:openwifi/
scp \\wsl$\Ubuntu-22.04\home\<你>\git\openwifi\driver\{tx_intf,rx_intf,openofdm_tx,openofdm_rx,xpu,side_ch}\*.ko root@192.168.10.122:openwifi/
# (openwifi/ 目录里原本已有 ad9361_drv.ko 等，别删)

# 8.2 板上热重载（不重启，热换 bitstream + ko）
ssh root@192.168.10.122
cd ~/openwifi && ./wgd.sh

# 8.3 校验 bitstream ↔ .ko 配套（数值必须 == 新 openwifi-hw commit 前 7 位）
./sdrctl dev sdr0 get reg xpu 63

# 8.4 单独加载联合模式 side_ch（wgd.sh 不装它）
insmod ./side_ch.ko iq_len_init=440 num_eq_init=0 pre_trigger_init=280 combined_init=1
#    ↑ pre_trigger_init = C1 定标常数（2026-10-15 回环注入实测 280；早期估 256 偏小，
#     导致 IQ 窗口比包起点晚 24 采样、L-STF 前 24 采样被截）；必须与采集同 FFT_WIN_SHIFT

# 8.5 可选：地址过滤（allow-all 默认即 probe 设好）
#     只抓指定对端:  ./side_ch_ctl wh1h6001 && ./side_ch_ctl wh7h<对端MAC低32位>

# 8.6 开始 UDP 推送（目标 = 你的接收 PC 的静态 IP 192.168.10.2，Windows「以太网 2」）
./side_ch_ctl g10 -s 192.168.10.2
```

> 可替代：`cd openwifi/user_space && ./drv_and_fpga_package_gen.sh $OPENWIFI_HW_IMG_DIR $XILINX_DIR e310v2` 一次打出 `drv_and_fpga.tar.gz`，`scp` 到板上后 `./wgd.sh drv_and_fpga.tar.gz`。

---

## 9. 接收验证（你的 PC = 192.168.10.2）

```bash
# 需要 numpy/matplotlib；在 Windows 侧跑（NAT 下 WSL 网段是 172.23.x.x，绑不了 .2）
python csi_iq_display.py 0 440 --ip 192.168.10.2
```

逐条对《开发文档 §7》的板级清单：
- 回环 CSI 平坦（自发自收：`./sdrctl dev sdr0 set reg xpu 1 1` 解静音后再发 `inject_80211`）；
- 前导 IQ 与已知 L-STF/L-LTF 互相关峰在首采样处（C1 正确）；
- 过滤只出目标包；不匹配包零输出；
- 双 TSF 差距 ≤ 20 µs（脚本不报 WARNING）；
- 压力：`g1` + iperf3 5 分钟，`rh20` 读 reg20 不长期贴 4096；
- `tcpdump` TSF 与记录双 TSF 三方对齐抽查。

---

## 10. 常见问题速查

| 现象 | 排查 |
|---|---|
| `vivado` 找不到 | 没 `source /opt/Xilinx/Vitis/2022.2/settings64.sh`；或装在 Windows 侧（必须装 WSL 内 Linux 版） |
| `insmod side_ch.ko` vermagic 错 | §5 `prepare_kernel.sh` 没跑/版本不对；重编 |
| 综合 license 报错（Viterbi） | `export XILINX_LIC_FILE=<.lic>` 没生效；或 .lic 没下 |
| 综合卡在子 IP 很久 | 正常，首次编子 IP 最慢；`tail -f synth.log` 看进度 |
| `/mnt/c` 下构建巨慢 / git 报错 | 把仓库移到 `~/git`（§1 铁律） |
| WSL 能连板子（192.168.10.122）吗 | 能——WSL2 NAT 经 Windows 主机路由即可 ping/ssh 板子（`auto_ssh.py` 部署/采集正是经 WSL 走的）；只是 WSL 绑不了 `.2`，**UDP 监听必须在 Windows 侧**（§8） |
| 板上 `sdrctl get reg xpu 63` 与期望不符 | bitstream 与 .ko 不配套，重打包重发（§8） |

**10.1 （可选）开 WSL2 mirrored 网络，让 WSL 也能直连板子**

Windows 编辑 `C:\Users\<你>\.wslconfig`：
```ini
[wsl2]
networkingMode=mirrored
```
`wsl --shutdown` 后重启 WSL。此后 WSL 内可直接 `ssh root@192.168.10.122`。**非必需**（部署照 §8 走 Windows 侧即可）。

---

## 11. 总耗时与产出清单（最低配置参考）

| 阶段 | 时间 | 产物 |
|---|---|---|
| WSL2 + Ubuntu 依赖 | ~20 min | WSL2 环境 |
| Vivado/Vitis 2022.2 + license | 装 15–30 min + 下载「看网速」 | `/opt/Xilinx` |
| 拉代码 + 搬改动 + 子模块 | ~20 min | 三个仓库就绪 |
| `prepare_kernel.sh 32`（一次） | 30–60 min | adi-linux 6.12 可编树 |
| `make_all.sh $XILINX_DIR 32` | 几分钟（增量） | 7 个 `.ko` |
| `create_ip_repo`→`openwifi.tcl` 综合 | **1–3 h** | `system_top.bit` → `system_top.bit.bin` |
| `sdk_update` + `boot_bin_gen` | ~10 min | `system_top.bit.bin`（user_space/）、`BOOT.BIN`（kernel_boot/boards/e310v2/output_boot_bin/，无需烧写） |
| scp + wgd.sh + insmod + 验证 | ~20 min | 板上联合采集开跑 |

**别搞错的两点**
1. license 别漏：综合到 Viterbi 就停 = 基本 100% 是 license 或子模块缺失。
2. 只重编 kernel modules + bitstream，**全程不烧 SD、不动内核/uImage/dtb**：换 bitstream 靠 `wgd.sh` 热重载（前提是地址表/拓扑没变——本任务确实没变）。