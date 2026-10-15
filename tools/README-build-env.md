# openwifi / E316 构建环境搭建工具

`tools/build_env.sh` —— 把原先散落在 27 个历史脚本里的**环境搭建流程**合并成的一份工具。
覆盖从"裸 WSL"到"产出 boot.bin + license 就绪"的完整链路。

> 原 27 个脚本（`install_vitis*.sh`、`build_kernel.sh`、`run_vivado_synth.sh` …）已于 2026-10-10
> 清理删除 —— 所有有效逻辑与踩坑说明已完整并入本工具。

---

## 一、为什么合并

| 原问题 | 现状 |
|---|---|
| 27 个脚本各自为战，顺序靠记忆 | 一个入口，`stage` 参数线性驱动 |
| 同名脚本多版本（`install_vitis.sh` / `_offline` / `_offline2`） | 只保留最终能跑通的那条路径 |
| 踩坑经验埋在注释里，换人看不懂 | 保留为 `★ 踩坑记忆` 注释块 |
| 验证逻辑分散（`verify_*.sh`） | 统一 `all-check` 一键体检 |

---

## 二、用法

全部在 **WSL Ubuntu-22.04** 里执行：

```bash
cd /mnt/c/OpenWiFi/tools
chmod +x build_env.sh
```

### 阶段一览（按顺序）

```bash
./build_env.sh all-check        # 先体检，看缺什么（只读，不写盘）

./build_env.sh deps             # 0. Ubuntu 构建依赖
./build_env.sh submodules       # 1. 拉 openofdm_rx / adi-linux 子模块

./build_env.sh vitis-cfg        # 2a. 生成裁剪后的 Vitis 安装配置
./build_env.sh vitis            # 2b. 离线安装 Vitis 2022.2（30-90 分钟）
./build_env.sh vitis-verify     # 2c. 验证安装完整性

./build_env.sh kernel-fetch     # 3a. adi-linux 拉齐到 2026_R1
./build_env.sh kernel-check     # 3b. 预检 4 个内核补丁
./build_env.sh kernel           # 3c. 构建内核树（默认 -j4）

./build_env.sh driver           # 4.  构建 7 个驱动 .ko
./build_env.sh driver-verify    # 4b. 验证架构 / 联合采集参数 / vermagic

./build_env.sh iprepo           # 5a. 生成 FPGA IP repo
./build_env.sh synth            # 5b. 综合+实现+bitstream（产出 .xsa）
./build_env.sh bit              # 5c. .xsa -> .bit -> .bit.bin
./build_env.sh boot             # 5d. 生成 boot.bin

./build_env.sh license-hostid   # 6a. 收集 license 申请用的 Host ID
./build_env.sh license          # 6b. 安装 Viterbi license（需 sudo）
```

### 可覆盖的环境变量

```bash
HOME_GIT=~/git            # openwifi / openwifi-hw 仓库根
XILINX_DIR=/opt/Xilinx    # 工具链安装路径
JOBS=4                    # 编译并行度（WSL 8G 内存建议 ≤4）
BOARD=e310v2              # 板卡名
```

示例：内存紧张时

```bash
JOBS=2 ./build_env.sh kernel
JOBS=2 ./build_env.sh synth
```

---

## 三、★ 关键踩坑记忆（已内建在脚本中）

| # | 坑 | 正确处理 |
|---|---|---|
| 1 | Vitis 安装**不能以 root 运行** —— 否则 `~/.Xilinx` 归 root，导入 `.xsa` 静默卡在 "Loading Platform" | 普通用户运行，脚本内部用 sudo 建目录 |
| 2 | 手写安装配置的 `Edition` 键名安装器不认 | 必须用 `Edition=Vitis Unified Software Platform`；最稳是 `xsetup -b ConfigGen` 生成模板再裁剪 |
| 3 | 安装产品**不能只勾 Vivado / Vitis HLS** | 必须勾 `Vitis`（它才含 Vivado + 交叉工具链） |
| 4 | `prepare_kernel.sh` 硬编码 `make -j12` | WSL 8G 内存必 OOM，脚本改为临时 `-j$JOBS`（改副本，不动原文件） |
| 5 | `prepare_kernel.sh` 会主动 `git checkout 2026_R1`，而浅克隆只有 2019_R1 | 先 `kernel-fetch` 显式拉齐 2026_R1 并解除 shallow |
| 6 | 内核补丁 `git apply` 不带 `--check`，冲突后内核树变脏 | 先 `kernel-check` 逐个 `--check` 预检 |
| 7 | `openwifi.tcl` 写死 `impl_1 -jobs 8` | 本仓库补丁已把上游的 `-jobs 8` 改为 `-jobs 3`；脚本仍会按 `JOBS` 变量临时覆盖（`sed` 同时兼容 8/3），`JOBS=2 ./build_env.sh synth` 可进一步降并行度 |
| 8 | `.xsa` 里 `.bit` 是 zip 成员，`unzip` 可能不可用 | 用 Python `zipfile` 解出 |
| 9 | 跨文件系统拷贝（NTFS→ext4）会丢可执行权限 | 安装前 `chmod +x xsetup` 及 `bin/`、`scripts/` 下的 `.sh` |
| 10 | WSL 的 `eth0` MAC **每次冷启动都变**，不能用来申请 license | 用宿主机 Windows 物理网卡 MAC（如 WLAN），稳定不变 |
| 11 | 子模块的 `.git` 是**文件**不是目录 | 判断存在性要用 `-e`，用 `-d` 会误报 |

---

## 四、产物位置速查

| 产物 | 路径 |
|---|---|
| 内核 uImage | `~/git/openwifi/adi-linux/arch/arm/boot/uImage` |
| 驱动模块 | `~/git/openwifi/driver/*/*.ko` |
| IP repo | `~/git/openwifi-hw/boards/e310v2/ip_repo/` |
| bitstream 工程 | `~/git/openwifi-hw/boards/e310v2/openwifi_e310v2/system_top.xsa` |
| boot.bin | `~/git/openwifi/kernel_boot/boards/e310v2/output_boot_bin/` |
| license | `/opt/Xilinx/licenses/Xilinx.lic` |
| 各阶段日志 | `~/kernel_build.log`、`~/driver_build.log`、`~/vivado_synth.log`、`~/boot_bin_gen.log` |

---

## 五、与"烧录/采集"工具的分工

本工具**只负责构建环境**（WSL 侧）。板端的部署与采集是另一套：

| 文件 | 职责 |
|---|---|
| `tools/e316_deploy.sh` | 板端部署（ADC 绑定 → 重载模块 → monitor → 加载 side_ch） |
| `tools/e316_collect.sh` | 板端采集（`kill -INT` 收尾，UDP 发往 PC） |
| `tools/one_click_collect.sh` | ★ 一键采集（Git Bash：等板 → 部署 → 监听 → 采集 → 收尾） |
| `tools/win_listen_iq.py` | ★ Windows 侧 UDP 接收 + 自对齐解析（NAT 拓扑，`--layout=iq_first`） |
| `tools/wsl_listen_4000.py` | 旧：mirrored 时代的 WSL 侧接收（现 NAT 拓扑下收不到，勿用） |
| `tools/auto_ssh.py` | 免密喂密码的 ssh/scp 封装（仅用于首次往板端装公钥，之后板端命令直接走 Windows 内置 ssh/scp） |

详见《OpenWiFi-CSI-IQ-操作手册.md》。
