# OpenWiFi CSI+IQ 联合采集 —— 人工执行清单（本机实测版）

> 本文是《OpenWiFi-CSI-IQ-WSL构建部署流程.md》的**本机适配版**。
> 原文档假设"Mac 编辑 + WSL2 编译"双机流程，且路径为 `/Users/juicy/...`。
> 本文按**当前这台机器**（Windows 10/11 + Ubuntu-22.04 WSL2）的实测状态重写，并明确标出**哪些必须你人工做、哪些可以让助手代跑**。
>
> 实测时间：2026-09-29 19:11（GMT+8）

---

## 0. 本机现状快照（已实测，非推测）

| 检查项 | 命令 | 实测结果 | 判定 |
|---|---|---|---|
| WSL2 可用 | `wsl.exe -l -v` | `Ubuntu-22.04  Running  2` | ✅ 就绪 |
| 发行版 | `cat /etc/os-release` | Ubuntu 22.04.5 LTS (Jammy) | ✅ 符合要求 |
| WSL2 内核 | `cat /proc/version` | 6.18.33.2-microsoft-standard-WSL2 | ✅ |
| WSL 用户 | `whoami` | `faweist`，HOME=`/home/faweist` | ⚠️ 新装环境，家目录近乎空白 |
| WSL 磁盘 | `df -h /` | `/dev/sdd 1007G / 已用 1.7G / 可用 954G` | ✅ 空间充足 |
| CPU / 内存 | `nproc` / `free -h` | 8 核 / 7.7 GiB 总量 | ⚠️ 内存偏紧，见 §6 建议 |
| sudo | `sudo -n true` | `a password is required` | ⚠️ 每条 sudo 都要输密码 |
| 联网 | `curl -sI https://github.com` | `HTTP/2 200` | ✅ 通 |
| **Xilinx 工具链** | `ls /opt` | **空目录** | ❌ **缺失（核心阻塞）** |
| `vivado/xsct/bootgen/arm-linux-gnueabihf-gcc` | `which ...` | **全部无输出** | ❌ **缺失** |
| Viterbi license | — | 无 | ❌ **缺失（综合必需）** |
| `~/git` 仓库 | `ls ~/git` | `No such file or directory` | ❌ 未拉取 |
| 系统依赖 | — | 未装 build-essential / dtc / libssl-dev 等 | ❌ 未装 |
| `C:/OpenWiFi/openwifi-hw/ip/openofdm_rx` | `ls` | **空** | ❌ 综合必失败 |
| `C:/OpenWiFi/openwifi/adi-linux` | `ls` | **空** | ❌ 编 .ko 必失败 |
| `C:/OpenWiFi` 是否 git 仓库 | `git status` | `not a git repository` | ⚠️ 是拷贝快照，非 clone |

### 源码改动核查（已确认在位）

| 文件 | 位置 | 改动标识 | 实测 |
|---|---|---|---|
| `openwifi/driver/side_ch/side_ch.c` | 23.2 KB | `combined` 出现 **12** 次 | ✅ 在位 |
| `openwifi/driver/side_ch/side_ch.h` | 1.8 KB | — | ✅ 在位 |
| `openwifi-hw/ip/side_ch/src/side_ch.v` | 16.5 KB | `:369 .csi_iq_combined(slv_reg3[1])` | ✅ 在位 |
| `openwifi-hw/ip/side_ch/src/side_ch_control.v` | 38.2 KB | `csi_iq_combined` 出现 **11** 处 | ✅ 在位 |
| `openwifi/user_space/side_ch_ctl_src/csi_iq_display.py` | — | — | ✅ 在位 |

驱动侧关键参数（`side_ch.c` 实测行号）：

```c
:34  static int iq_len_init = 0;      // iq_len>0 → IQ 采集，CSI 关闭
:36  static int combined_init = 0;    // >0 → CSI+IQ 联合采集 (csi_iq_combined=1)
:381     if (combined_init)
:586     if (iq_len_init>8187) { ... }   // 上限校验
```

> **结论：代码改动无需重做，可直接进入构建阶段。** 阻塞点 100% 在工具链与环境。

---

## 1. 阻塞点总结：编译做不了，卡在哪

一句话：**FPGA 综合与内核模块交叉编译都依赖 Xilinx Vitis 2022.2 自带的工具链，而本机没有。**

原文档 §7 那句"license 别漏，综合到 Viterbi 就停 = 基本 100% 是 license 或子模块缺失"说明了要害：`openofdm_rx` 的 Viterbi 解码器是 **license 保护 IP**，若缺 license 综合会中断；license 绑定你的 AMD 账号，**只能你本人申请和放置**。

> ⚠️ **但请注意 §2.1.0 的实测发现**：`openwifi-hw/README.md` 官方表格明确标注 **e310v2 为「NO need」**（不需要 Vivado license）。这意味着本板型**大概率可以先跳过 license 申请直接综合**，省掉一个前置环节。运行阶段的 2 小时停摆问题仍存在（见 §2.1.0）。

同理，`arm-linux-gnueabihf-gcc` 这个交叉编译器是 Vitis 自带的（文档 §2 明确写了"交叉工具链由 Vitis 2022.2 自带，不用单独装"），没有 Vitis 就没有它，`.ko` 也就编不出来。

所以下面的清单分成三类：
- 🔴 **你必须人工做**（需账号/大文件/交互式 GUI）
- 🟡 **助手可代跑**（命令确定、无需账号）
- ⚪ **可选/建议**（性能与稳定性）

---

## 2. 🔴 阶段一：你本人必须完成的步骤

### 2.1 申请 Viterbi 评估 license（★先读 2.1.0，可能根本不用申请★）

#### 2.1.0 重要：你的目标板 e310v2 官方标注「**NO need**」

实测 `openwifi-hw/README.md:25-42` 的 `BOARD_NAME|Description|Vivado license` 表格：

| BOARD_NAME | Vivado license |
|---|---|
| zc706_fmcs2 | Need |
| **e310v2** | **NO need** ← 你的目标板 |
| antsdr / antsdr_e200 / neptunesdr / LibreSDR | NO need |
| adrv9361z7035 / zcu102_fmcs2 / rfsoc4x2 | Need |

**这说明 e310v2 这条构建链路官方保证不需要额外 license 就能综合过。**

**为什么文档还提 license？** 因为 `openwifi/README.md:116` 有 Note 2：Viterbi 解码器在**运行约 2 小时后会停止工作**（Xilinx 评估 license 的行为），处理方式是重载 bitstream 或断上电。同理 `openwifi/doc/README.md:185` 把 `rx 20` 读数恒为 3 定义为 Viterbi 停摆的标志。

即分两个阶段：

- **综合/生成 bitstream**：e310v2 标注 NO need，理论上不申请也能编出来。
- **板上运行时**：评估 license 内嵌在 bitstream 里，约 2 小时后解码器停摆，需 `wgd.sh` 热重载或断电重启。

> ⚠️ **但本任务有变数**：`OpenWiFi-CSI-IQ-WSL构建部署流程.md` §7 和 §10 把"综合 license 报错（Viterbi）"列为已知失败项，并断言"综合到 Viterbi 就停 = 基本 100% 是 license"。这与官方 NO need 标注存在冲突，可能是本项目用的 `openwifi-hw` 版本或分支行为不同。
>
> **建议策略：先不申请，直接跑综合**（§3.6）。等真要跑几小时长实验时，再决定是否需要正式/评估 license。这样能省掉最长的等待环节。若综合阶段真的报 Viterbi license 错误，再回下面流程。

#### 2.1.1 需要时的申请流程（已核对官方页面）

官方入口：[Xilinx Viterbi Decoder 产品页](https://www.xilinx.com/products/intellectual-property/viterbi_decoder.html)（现跳转 AMD 站点）

| 项 | 实测值 |
|---|---|
| Part Number | `EF-DI-VITERBI-SITE` |
| 支持的器件 | Artix 7 / Kintex 7 / Virtex 7 / **Zynq 7000** ← e310v2 属此列 ✅ |
| 设计工具支持 | Vivado Software, ISE Design Suite |
| License 类型 | Core License Agreement |

**操作步骤**（官方流程 = 产品页点 `Evaluate`）：

1. **注册/登录 AMD 账号**
   访问 [amd.com](https://www.amd.com/) 注册。已有的 Xilinx 账号通常可直登。

2. **打开产品页，点左侧 `Evaluate`**
   → https://www.xilinx.com/products/intellectual-property/viterbi_decoder.html
   页面左侧栏有 **Evaluate** 按钮，这是评估 license 的入口（不要在别处找 Licensing 页）。

3. **填写表单，创建 license**
   - 表单里有一行 **Viterbi Decoder** 复选框，**默认已勾选**；若没勾要手动勾上
   - 一路 `Next` → `Agree` 走完协议

4. **填机器信息（关键，别填错）**
   - 选择**操作系统版本**：Ubuntu 22.04（选 Linux 64-bit 类目）
   - 填**以太网 MAC 地址**（**必填**，license 绑定 MAC）

   本机 WSL2 的 MAC 我**已帮你查好**：

   ```
   eth0 MAC = 00:15:5d:42:53:95
   ```

   申请表单里 MAC 格式通常要求写成 `00155d425395`（去掉冒号）或 `00:15:5d:42:53:95`，按表单提示填。

   > ⚠️ **WSL2 的 MAC 可能不固定**！`00:15:5d:` 开头是微软虚拟网卡前缀，WSL 重启后末段可能变化。若之后 license 报失效，回来重新确认 MAC 并重新生成 license。
   > 想固定它：Windows 侧 `C:\Users\fawei\.wslconfig` 加 `[wsl2] macAddress=00155d425395`，`wsl --shutdown` 后生效。

5. **下载 license 文件**
   提交后页面**左下角有下载箭头图标**，点它下载 `.lic` 文件。
   > 注意：license 是**立即在网页上生成并下载**的，不是"发到邮箱等审批"——所以这条其实不慢，我先前说的"审批要等"不准确，这里更正。

6. **加载到 Vivado**
   - 方式 A（GUI）：Vivado → `Help` → `Manage License` → `Load License` → 选 `.lic`
   - 方式 B（命令行，本方案推荐）：
     ```bash
     # 放进 WSL 并写死环境变量
     cp /mnt/c/Users/fawei/Downloads/*.lic ~/Xilinx.lic
     echo 'export XILINX_LIC_FILE=/home/faweist/Xilinx.lic' >> ~/.bashrc
     source ~/.bashrc
     ```
   - 导入成功后**重启 Vivado**。

7. **验证 license 生效**
   ```bash
   # 综合后看是否有 Viterbi 相关 license 报错
   grep -i "license\|viterbi" ~/git/openwifi-hw/boards/e310v2/synth.log | head -20
   ```

> **评估 license 的两个已知限制**（用于预期管理）：
> 1. **约 2 小时后 Viterbi 解码器停摆**——这是板上运行时的行为，不是综合时。判定：`./sdrctl dev sdr0 get reg rx 20` 输出一直不变（恒为 3）。处理：`./wgd.sh` 重载 bitstream 或断电重启。
> 2. 评估 license 是 **Full System Hardware Evaluation** 类型——能生成 bitstream 并在板上跑，但有时限。

### 2.2 下载 Vitis 2022.2 Linux 安装包

- 地址：AMD 官网 **Downloads → Vivado → 2022.2 → Linux Self Extracting Web Installer**
- 文件名形如 `Xilinx_Unified_2022.2_1014_2254_Lin64.bin`
- 体积：**在线安装器约 300 MB**（边下边装，实际占用 ~100 GB+），完整 DVD 镜像约 123 GB
- 安装时**只需勾选 Vitis**（Vitis 已含 Vivado 与全部 arm 交叉工具链）

> 💡 **强烈建议**：把 `.bin` 放到 **WSL 的 ext4 里**（如 `~/Downloads/`），不要放 `/mnt/c`。
> 原因见原文档 §1 铁律：NTFS 挂载点会让大量小文件 I/O 极慢，安装过程可能从 30 分钟拖到数小时。
>
> ```bash
> # 下载到 Windows 后，搬进 WSL ext4
> wsl -d Ubuntu-22.04 -- bash -lc "mkdir -p ~/Downloads && cp /mnt/c/Users/fawei/Downloads/Xilinx_Unified_*.bin ~/Downloads/ && ls -lh ~/Downloads/"
> ```

### 2.3 在 WSL 里安装 Vitis（交互式，需你确认）

```bash
# 进 WSL
wsl -d Ubuntu-22.04

# 1) 建安装目录（需 sudo 密码）
sudo mkdir -p /opt/Xilinx && sudo chown $USER:$USER /opt/Xilinx

# 2) 给安装包加执行权限
chmod +x ~/Downloads/Xilinx_Unified_2022.2_1014_2254_Lin64.bin

# 3) 启动 GUI（Win11 的 WSLg 自带显示；Win10 需自行配 X Server）
~/Downloads/Xilinx_Unified_2022.2_1014_2254_Lin64.bin
```

GUI 里：登录 AMD 账号 → 选择 **Vitis** → 安装路径填 `/opt/Xilinx` → 勾选同意协议 → 等待完成（**15–30 分钟起，视网速**）。

> **批处理替代方案**（无需 GUI，适合 Win10）：
> ```bash
> cat > ~/vivado_install_config.txt <<'EOF'
> Edition=Vitis 2022.2
> Modules=Vitis
> InstallPath=/opt/Xilinx
> AcceptLicenseAgreements=true
> EOF
> ~/Downloads/Xilinx_Unified_2022.2_1014_2254_Lin64.bin -b Install \
>   -a XilinxEULA,3rdPartyEULA -c ~/vivado_install_config.txt
> ```

### 2.4 写死环境变量与 license

```bash
cat >> ~/.bashrc <<'EOF'
export XILINX_DIR=/opt/Xilinx
export XILINX_LIC_FILE=/home/faweist/Xilinx.lic
export OPENWIFI_HW_IMG_DIR=/home/faweist/git/openwifi-hw-img
EOF
source ~/.bashrc
```

### 2.5 验证安装成功（这一步过了才能继续）

```bash
source /opt/Xilinx/Vitis/2022.2/settings64.sh
which vivado xsct bootgen arm-linux-gnueabihf-gcc
vivado -version
```

**预期**：四个 `which` 都输出路径，`vivado -version` 打印 `Vivado v2022.2`。
**若报错**：说明安装未完成或装了 Windows 版（必须 Linux 版）。

### 2.6 清理 Windows 侧安装包

安装成功后，Windows 上的 `.bin` 可删（约几百 MB～123 GB，视你下载哪个版本）。

---

## 3. 🟡 阶段二：助手可代跑（不需要 Xilinx）

以下步骤命令确定、无需账号，**你确认后我可以直接执行**。

### 3.1 安装 Ubuntu 系统依赖（约 10 分钟）

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  build-essential gcc g++ make \
  flex bison libssl-dev \
  device-tree-compiler u-boot-tools \
  libtinfo6
sudo apt install -y libtinfo5 || echo "22.04 无 libtinfo5，一般不影响"
sudo apt install -y git git-lfs ssh rsync net-tools openssh-server
```

> ⚠️ 本机 `sudo` **需要密码**（已实测 `a password is required`）。所以我代跑时要么你临时开免密，要么这些命令你自己粘进终端更省事。见 §6.3。

### 3.2 拉三个仓库 + 子模块（约 20 分钟，约 2–3 GB）

```bash
mkdir -p ~/git && cd ~/git
git clone https://github.com/open-sdr/openwifi.git
git clone https://github.com/open-sdr/openwifi-hw.git
git clone https://github.com/open-sdr/openwifi-hw-img.git

# 综合依赖：openofdm_rx 子 IP（缺了 system.bd 综合不过）
cd openwifi-hw && ./get_ip_openofdm_rx.sh

# 内核源码树 6.12（约 2–3 GB）
cd ../openwifi && git submodule update --init adi-linux
```

> 若 `adi-linux` 拉得慢：`git submodule update --init --depth 1 adi-linux`，再按 §4 完整 checkout。

### 3.3 把 `C:/OpenWiFi` 的 side_ch 改动搬进 WSL

⚠️ **关键差异**：`C:/OpenWiFi` 里代码是**拷贝快照，非 git clone**，所以不能用原文档 §4.2 的 `git diff --stat` 校验。
另外原文档给的路径 `/Users/juicy/Projects/OpenWiFi/` 是 **Mac 路径**，本机是 `C:\OpenWiFi`。

**推荐做法：不搬运，直接用现有快照覆盖 clone 下来的仓库对应文件**（改动已核查在位）：

```bash
# 在 WSL 内执行
cd ~/git

# 驱动侧
cp /mnt/c/OpenWiFi/openwifi/driver/side_ch/side_ch.c   openwifi/driver/side_ch/
cp /mnt/c/OpenWiFi/openwifi/driver/side_ch/side_ch.h   openwifi/driver/side_ch/
cp /mnt/c/OpenWiFi/openwifi/user_space/side_ch_ctl_src/csi_iq_display.py \
   openwifi/user_space/side_ch_ctl_src/

# RTL 侧
cp /mnt/c/OpenWiFi/openwifi-hw/ip/side_ch/src/side_ch.v         openwifi-hw/ip/side_ch/src/
cp /mnt/c/OpenWiFi/openwifi-hw/ip/side_ch/src/side_ch_control.v openwifi-hw/ip/side_ch/src/

# 校验确实覆盖成功
grep -c combined openwifi/driver/side_ch/side_ch.c          # 应输出 12
grep -c csi_iq_combined openwifi-hw/ip/side_ch/src/side_ch_control.v  # 应输出 11
```

**替代方案**：先把 `C:/OpenWiFi` 的两个目录 `git init` 变成真正仓库，再走标准 diff/commit 流程。适合你想长期维护、追踪改动来源时。

> ⚠️ 原文档还提到 `openwifi-hw/ip/side_ch/unit_test/side_ch_control_tb/`。我实测 `C:/OpenWiFi/openwifi-hw/ip/side_ch/` 下**没有 `unit_test` 目录**——即 Step 2 的仿真测试台**尚不存在**。若你要跑 `side_ch_control_tb` 回归，需要先按《路线A-实现计划与代码骨架.md》编写它，这部分是额外工作量，别漏。

### 3.4 准备内核源码树（一次，30–60 分钟）

```bash
cd ~/git/openwifi/user_space
source /opt/Xilinx/Vitis/2022.2/settings64.sh
./prepare_kernel.sh $XILINX_DIR 32
```

**产出**：`adi-linux/arch/arm/boot/uImage`、`adi-linux` 内各 `.ko`。
> ⚠️ 依赖 §2 的 Vitis 已装好（脚本会调用交叉工具链），所以这步**必须在 Xilinx 安装完成之后**。

### 3.5 编驱动 → 7 个 `.ko`（分钟级）

```bash
cd ~/git/openwifi/driver
./make_all.sh $XILINX_DIR 32
ls -la sdr.ko side_ch/side_ch.ko tx_intf/tx_intf.ko rx_intf/rx_intf.ko \
       openofdm_tx/openofdm_tx.ko openofdm_rx/openofdm_rx.ko xpu/xpu.ko
```

**预期**：7 个 `.ko` 全部出现，`side_ch.ko` 含联合采集逻辑。
> 若没做 §3.4 就编：`driver/pre_def.h` / `driver/git_rev.h` 会生成，但 `.ko` 会报错。

### 3.6 综合 FPGA → `system_top.bit.bin`（1–3 小时，需 license）

```bash
source /opt/Xilinx/Vitis/2022.2/settings64.sh
cd ~/git/openwifi-hw/boards/e310v2
../create_ip_repo.sh $XILINX_DIR          # 生成 ip_repo，收进改过的 side_ch

# 主耗时步骤，建议 nohup 挂着
nohup vivado -mode batch -source ../openwifi.tcl > synth.log 2>&1 &
tail -f synth.log
# 看到 "write_bitstream completed successfully" 即成功
# 产物 openwifi_e310v2/openwifi_e310v2.runs/impl_1/system_top.bit

# 归档 xsa 并出 bit.bin
cd ~/git/openwifi-hw/boards && ./sdk_update.sh e310v2 $OPENWIFI_HW_IMG_DIR
cd ~/git/openwifi/user_space
./boot_bin_gen.sh $XILINX_DIR e310v2 $OPENWIFI_HW_IMG_DIR/boards/e310v2/sdk/system_top.xsa
ls -la system_top.bit.bin BOOT.BIN
```

**失败快查**：`synth.log` 里搜 `ERROR`；license 报错 → 查 §2.1；`openofdm_rx` 缺失 → 回 §3.2。

---

## 4. 🔴 阶段三：上板部署（Windows 侧，建议你手工）

目标板 `192.168.10.122`，接收 PC `192.168.10.1`。

```powershell
# 8.1 传 bitstream + 全部 .ko（wgd.sh 要求同目录）
scp \\wsl$\Ubuntu-22.04\home\faweist\git\openwifi\user_space\system_top.bit.bin root@192.168.10.122:openwifi/
scp \\wsl$\Ubuntu-22.04\home\faweist\git\openwifi\driver\*.ko root@192.168.10.122:openwifi/
scp \\wsl$\Ubuntu-22.04\home\faweist\git\openwifi\driver\{tx_intf,rx_intf,openofdm_tx,openofdm_rx,xpu,side_ch}\*.ko root@192.168.10.122:openwifi/
# openwifi/ 里原有 ad9361_drv.ko 等，别删
```

```bash
# 8.2 板上热重载（不重启）
ssh root@192.168.10.122
cd ~/openwifi && ./wgd.sh

# 8.3 校验 bitstream ↔ .ko 配套
./sdrctl dev sdr0 get reg xpu 63     # 必须 == 新 openwifi-hw commit 前 7 位

# 8.4 单独加载联合模式 side_ch
insmod ./side_ch.ko iq_len_init=440 num_eq_init=0 pre_trigger_init=300 combined_init=1
#   pre_trigger_init=300 是 <C1> 定标常数，首次用 300，之后按部署文档 §5.1 修正

# 8.5 开始 UDP 推送
./side_ch_ctl g10 -s 192.168.10.1
```

**一次打包替代法**：

```bash
cd ~/git/openwifi/user_space
./drv_and_fpga_package_gen.sh $OPENWIFI_HW_IMG_DIR $XILINX_DIR e310v2
# 得 drv_and_fpga.tar.gz → scp 到板 → ./wgd.sh drv_and_fpga.tar.gz
```

---

## 5. 验证（PC = 192.168.10.1）

```bash
python3 csi_iq_display.py 0 440 --ip 192.168.10.1
```

逐条对《开发文档 §7》板级清单：

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | 回环 CSI 平坦 | 解静音后发 `inject_80211`，CSI 应平坦 |
| 2 | 前导 IQ 互相关峰 | 与已知 L-STF/L-LTF 的峰落在**首采样**处（C1 正确） |
| 3 | 地址过滤 | 只出目标包；不匹配包**零输出** |
| 4 | 双 TSF 差距 | ≤ 20 µs（脚本不报 WARNING） |
| 5 | 压力测试 | `g1` + iperf3 跑 5 分钟，`wh20` 读 reg20 不长期贴 4096 |
| 6 | 三方对齐 | `tcpdump` TSF 与记录双 TSF 抽查一致 |

---

## 6. 本机特有的注意事项（原文档没提的）

### 6.1 内存偏紧，综合可能吃紧

实测 WSL 只拿到 **7.7 GiB** 内存。Vivado 综合 e310v2（Zynq-7020）通常在 8–16 GB 更稳，7.7 GiB 属于临界。

**建议**：Windows 用户目录建 `C:\Users\fawei\.wslconfig`：

```ini
[wsl2]
memory=20GB
processors=8
swap=16GB
```

然后 `wsl --shutdown` 重启 WSL 生效（前提是你 Windows 物理内存 ≥24 GB）。

### 6.2 代码必须放 ext4，别放 `/mnt/c`

原文档 §1 铁律。你现在的 `C:/OpenWiFi` 在 NTFS 上——**不要试图直接在 `/mnt/c/OpenWiFi` 里编译**，Vivado 和 git 会卡死、文件属性错乱。所以 §3.2 是"clone 到 `~/git` + 覆盖改动"，而不是"直接在 C 盘编"。

### 6.3 sudo 密码问题

本机 `sudo` 需密码，我代跑时会卡在密码输入。三种选择：

- **A（推荐）**：你自己在终端跑带 `sudo` 的那几条（§3.1），其余我代跑
- **B**：临时开免密（注意安全）：
  ```bash
  echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER
  ```
- **C**：每次都你在旁边输密码

### 6.4 `openwifi-hw/ip/side_ch/unit_test` 不存在

实测该目录缺失，即仿真回归（原文档 Step 2 / §2 仿真）**尚未落地**。若综合前想先跑仿真验证 RTL，需按骨架文档补写 testbench。**否则只能跳过仿真直接综合，风险是"静默错位"类 bug 到板上才暴露。**

### 6.5 `C:/OpenWiFi` 不是 git 仓库

`git status` 报 `not a git repository`。这意味着：
- 无法用 `git diff` 追踪改动
- 无法确定当前快照对应哪个 upstream commit，**`sdrctl get reg xpu 63` 的配套校验值无从查起**

**建议**：搞清楚这份快照的来源 commit。若不确定，接入板上后 `xpu 63` 对不上是大概率事件，需回查。

---

## 7. 时间与产出汇总

| 阶段 | 执行者 | 时间 | 产物 |
|---|---|---|---|
| 申请 license | 🔴 你 | 看审批 | `Xilinx.lic` |
| 下载安装包 | 🔴 你 | 看网速（100 GB+） | `.bin` |
| 装 Vitis 2022.2 | 🔴 你 | 15–30 min | `/opt/Xilinx` |
| 装系统依赖 | 🟡 助手/你 | ~10 min | apt 包 |
| 拉仓库 + 子模块 | 🟡 助手 | ~20 min | `~/git/*` |
| 搬 side_ch 改动 | 🟡 助手 | ~2 min | 覆盖后的源码 |
| `prepare_kernel.sh 32` | 🟡 助手 | 30–60 min | adi-linux 可编树 |
| `make_all.sh 32` | 🟡 助手 | 几分钟 | **7 个 `.ko`** |
| `create_ip_repo` + 综合 | 🟡 助手 | **1–3 h** | `system_top.bit` |
| `sdk_update` + `boot_bin_gen` | 🟡 助手 | ~10 min | **`system_top.bit.bin`** |
| scp + wgd.sh + insmod + 验证 | 🔴 你 | ~20 min | 板上联合采集开跑 |

**两条别搞错**：
1. **先试无 license 综合**——`openwifi-hw/README.md` 标注 e310v2 为「NO need」，所以**先别花时间申请 license**，直接综合；只有真报 Viterbi license 错才走 §2.1.1。别再套用"综合到 Viterbi 就停"的旧结论。
2. **全程不烧 SD、不动内核/uImage/dtb**——换 bitstream 靠 `wgd.sh` 热重载（前提是地址表/拓扑没变，本任务确实没变）。

---

## 8. 下一步：你该做的三件事

1. **先别申请 license**（§2.1.0）——你的板型 e310v2 官方标注不需要。直接装 Vitis 后综合，真报错再说。这能省掉一个环节。
2. **下载 Vitis 2022.2 Linux 安装包**（§2.2）——100 GB+，这是目前最长的等待，**建议立刻启动**。
3. **告诉助手：§3 哪些代跑**——尤其是"是否要我先把 `~/git` 三个仓库拉下来、side_ch 改动覆盖好、把内存配置调大"。这部分现在就能做，不用等 Xilinx。
