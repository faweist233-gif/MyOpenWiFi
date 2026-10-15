# MyOpenWiFi

openwifi **CSI + 前导码 IQ 联合采集**（AntSDR E316 / Zynq-7020 + AD9361）的改动集与配套工具。

> 本仓库**不含上游全量代码**（上游含 3.4GB 的 `adi-linux` 内核树）。
> 只保存「改动 patch + 新增源码 + 工具 + 文档」，用 `setup.sh` 一键拉齐可编译的工作环境。

---

## 一、快速部署

### Linux（原生 Ubuntu 22.04）或 WSL2

```bash
# 1. 克隆本仓库
git clone git@github.com:faweist233-gif/MyOpenWiFi.git ~/MyOpenWiFi
cd ~/MyOpenWiFi

# 2. 一键拉齐（克隆上游 + 拉子模块 3.4G + 套改动 + 拷工具）
./setup.sh

# 3. 体检
~/tools/build_env.sh all-check
```

`setup.sh` 完成后你会得到：

```
~/git/openwifi/       ← 上游代码 + 已套用联合采集改动
~/git/openwifi-hw/    ← 上游代码 + 已套用联合采集改动
~/tools/              ← 全部工具脚本
```

### Windows 上使用

Windows **不能直接编译** openwifi（缺 Linux 内核工具链），但可以：

```powershell
# 1. 装 WSL2
wsl --install -d Ubuntu-22.04

# 2. 在 WSL 里按上面「Linux」步骤操作
wsl -d Ubuntu-22.04
git clone git@github.com:faweist233-gif/MyOpenWiFi.git ~/MyOpenWiFi
cd ~/MyOpenWiFi && ./setup.sh
```

在 Windows 资源管理器里访问代码：`\\wsl$\Ubuntu-22.04\home\<用户名>\git\openwifi`
（或直接在 VS Code 里 `code .` 用 Remote-WSL 打开）

---

## 二、setup.sh 选项

| 选项 | 说明 |
|---|---|
| （无） | 完整流程：克隆 + 子模块 + patch + 工具 |
| `--work-dir /opt/ow` | 指定工作目录（默认 `~/git`） |
| `--skip-submodules` | 跳过 3.4GB 的 adi-linux 等**全部三个子模块**（openofdm_rx / adi-hdl 也会被跳过）——只用于先浏览代码；之后可用 `tools/build_env.sh submodules` 补 openofdm_rx + adi-linux，FPGA 综合前还需手动 `git submodule update --init --depth 1 adi-hdl` |
| `--patch-only` | 已有上游代码，只套 patch |

> ⚠️ **代码必须放 Linux 文件系统**（`~/git`），**不要**放 `/mnt/c`。
> NTFS 挂载会让 Vivado / git 卡死、文件属性错乱。

---

## 三、仓库内容

### `patches/` —— 改动补丁

| 文件 | 内容 |
|---|---|
| `openwifi-side_ch.patch` | 驱动 `side_ch.c/h` 联合采集逻辑 + `side_ch_ctl.c` 行缓冲 + `build_boot_bin.sh` 无显示修复 |
| `openwifi-hw-side_ch.patch` | RTL `side_ch_control.v`/`side_ch.v` 联合模式 + 构建脚本批量模式修复 |

改动清单（vs 上游）：

| 文件 | 类型 |
|---|---|
| `driver/side_ch/side_ch.c` | 源码 —— 模块参数 `pre_trigger_init`/`combined_init`、`RECORD_LEN` 分支、E316 FIFO 钳制、DMA cookie==0 放行（WARNING 修复）、`IS_ERR_OR_NULL`/`terminate_sync` 安全释放 |
| `driver/side_ch/side_ch.h` | 源码 —— `CSI_BLK_LEN`/`RECORD_LEN`/`SIDE_CH_M_AXIS_FIFO_DEPTH` 宏 |
| `user_space/side_ch_ctl_src/side_ch_ctl.c` | 源码 —— `main()` 加 `setvbuf` 行缓冲（静态二进制重定向"无输出"修复） |
| `ip/side_ch/src/side_ch_control.v` | 源码 —— 联合模式仲裁、IQ FSM、`record_len` 防回绕 |
| `ip/side_ch/src/side_ch.v` | 源码 —— `csi_iq_combined` 端口映射 |
| `kernel_boot/build_boot_bin.sh` | 构建 —— `xsct` 加 `-nodisp`（无显示环境必需） |
| `boards/create_ip_repo.sh` | 构建 —— `vivado -mode batch -nolog -nojournal` |
| `ip/create_vivado_proj.sh` | 构建 —— 同上 |
| `boards/openwifi.tcl` | 构建 —— `impl_1 -jobs 8→3`（防 WSL 内存 OOM） |
| `prepare_adi_lib.sh` | 构建 —— adi-hdl 分支 `hdl_2022_r2`、`make -j1`（防 OOM） |

### `new-files/` —— 新增源码（patch 不含新增文件）

| 文件 | 说明 |
|---|---|
| `openwifi/user_space/side_ch_ctl_src/csi_iq_display.py` | CSI+IQ 联合解析器（按 `record_len` 切包、双 TSF 配对校验） |
| `openwifi-hw/ip/side_ch/unit_test/` | Verilator 仿真测试台（tb.v / tcl / 测试向量 / 生成脚本） |

### `tools/` —— 工具脚本（setup.sh 会拷到 `~/tools/`）

| 文件 | 用途 |
|---|---|
| `build_env.sh` | ★ 构建环境一站式（子模块/Vitis/内核/驱动/FPGA/license） |
| `one_click_collect.sh` | ★ 一键采集（等板→部署→Windows 监听→采集→收尾，Git Bash 跑） |
| `win_listen_iq.py` | ★ PC 侧 UDP 接收 + 自对齐解析（**Windows 侧跑**，NAT 拓扑；`--layout=iq_first`） |
| `e316_deploy.sh` | 板端部署（ADC 修复 + 重载模块 + monitor + side_ch） |
| `e316_collect.sh` | 板端采集（SIGINT 收尾 + 推 UDP） |
| `wsl_listen_4000.py` | 旧：mirrored 时代的 WSL 侧 UDP 接收（现 NAT 拓扑下收不到，勿用） |
| `build_sctl2.sh` | 交叉编译 `side_ch_ctl`（`-static`） |
| `auto_ssh.py` | SSH 自动喂密码 |
| `push_run.sh` / `wait_board.sh` / `udp_dump.py` | 辅助 |
| `README-build-env.md` | 构建环境说明与 11 条踩坑记忆 |

### `docs/` —— 文档

- `OpenWiFi-CSI-IQ-操作手册.md` —— ★ **日常操作先看这个**
- `OpenWiFi-CSI-IQ-上板全链路打通报告.md` —— 6 个阻塞点根因分析
- `构建与原理/` —— 开发文档、部署文档、WSL 构建流程、路线 A 实现计划
- `学习资料/` —— 接收链路入门、二次开发指南、烧录指南、license 申请

---

## 四、完整构建流程（setup.sh 之后）

> ⚠️ **Viterbi license 必须自行申请（本仓库不随附）**
> license 绑定**网卡 MAC**，无法跨机器分发。在新电脑上综合 FPGA 前，须：
> 1. 重新申请 Xilinx 评估 license，绑定**新电脑**的网卡 MAC（见 `docs/学习资料/Viterbi-License-申请与安装清单.md`）；
> 2. WSL 关掉 `mirrored` 后 eth0 MAC 会变成 NAT 默认值，与 license 绑定的物理网卡 MAC 不一致 → 综合前需临时把 eth0 MAC 改成绑定值：
>    `sudo ip link set eth0 address <绑定MAC>`（WSL 重启后失效，需重设）。
> 否则 `open_bd_design` 会报 `no valid licenses found` 卡死。

```bash
# 环境体检
~/tools/build_env.sh all-check

# 装依赖 + Vitis（30-90 分钟，需 AMD 账号）
~/tools/build_env.sh deps
~/tools/build_env.sh vitis-cfg
~/tools/build_env.sh vitis
~/tools/build_env.sh vitis-verify

# 内核树 + 驱动
~/tools/build_env.sh kernel-fetch
~/tools/build_env.sh kernel-check
~/tools/build_env.sh kernel
~/tools/build_env.sh driver

# FPGA（1-3 小时）
~/tools/build_env.sh iprepo
~/tools/build_env.sh synth
~/tools/build_env.sh bit
~/tools/build_env.sh boot
```

### 板端部署与采集

```bash
# 推荐：一键（Git Bash，Windows 侧）——等板 → 部署 → 监听 → 采集 → 收尾
bash ~/tools/one_click_collect.sh 11 25 C:/iq

# 或两段式：板子 root@192.168.10.122，PC 侧 IP = 192.168.10.2（Windows「以太网 2」）
python ~/tools/win_listen_iq.py C:/iq 60 --layout=iq_first   # ① 先启 Windows 监听
~/tools/e316_deploy.sh 11                                    # ② 板端部署
~/tools/e316_collect.sh 25 192.168.10.2                      # ③ 板端采集
```

> UDP 监听必须在 **Windows 侧**跑（现为 NAT 拓扑，`.2` 归 Windows「以太网 2」；
> mirrored 时代的 `wsl_listen_4000.py` 已收不到，详见操作手册要点 4）。

---

## 五、版本对齐

`patches/` 是基于上游两个 commit 生成的，`setup.sh` 会**自动 checkout 到基线 commit**
（`openwifi@2f112f0`、`openwifi-hw@d047d79`，见 `setup.sh` 顶部 `OW_BASE`/`HW_BASE`）再套 patch，
因此正常情况下无需手动处理版本。

若上游更新导致基线 commit 失效（或你想基于更新的上游重新开发）：

```bash
cd ~/git/openwifi
git log --oneline -5                # 看当前版本
git apply --check ~/MyOpenWiFi/patches/openwifi-side_ch.patch   # 试打
# 若失败 → 手动 `git apply -3`（三方合并）或按 patch 内容手动改
```

patch 涉及的都是**独立改动块**，冲突概率低；即使有冲突，按 patch 里的上下文手工套用即可。

---

## 六、平台差异对照

| 操作 | Windows | Linux / WSL2 |
|---|---|---|
| 编译代码 | ❌ 不可（缺 Linux 工具链） | ✅ |
| 编辑代码 | ✅（VS Code Remote-WSL） | ✅ |
| 跑 Vivado/Vitis | ❌ | ✅ |
| 板端 SSH 部署 | ✅（需 WSL 或 PuTTY） | ✅ |
| UDP 接收监听 | ⚠️ 见下 | ✅ |

> **Windows 侧 UDP 收不到包（历史）**：仅在 WSL2 `mirrored` 网络模式下成立——板子发往 `192.168.10.2` 的包进入 WSL 命名空间，Windows 宿主 socket 收不到。**现拓扑已改为 NAT（关 mirrored）**，`.2` 归 Windows「以太网 2」独占，监听器必须在 **Windows 侧**跑（`win_listen_iq.py`），WSL 内反而收不到。

---

## 七、许可证与来源

- 上游代码来自 [open-sdr/openwifi](https://github.com/open-sdr/openwifi) 与 [open-sdr/openwifi-hw](https://github.com/open-sdr/openwifi-hw)（AGPL / 各自许可证）
- 本仓库只包含改动与工具；使用上游代码请遵守其原始许可证
- Viterbi IP 需要 Xilinx 评估 license（见 `docs/学习资料/Viterbi-License-申请与安装清单.md`）
- **license 不随本仓库分发**：license 绑定网卡 MAC，跨机器无效，新电脑须重新申请（见「四、完整构建流程」顶部提醒）
