# OpenWiFi CSI+IQ 联合采集 — 操作手册（E316 精简版）

> 版本：2026-10-10 ｜ 状态：✅ 已端到端验证通过（含 RTL 跨包错位修复后 169 record 同包对齐）  
> 平台：AntSDR E316（Zynq-7000 / AD9361 / ARMv7 Cortex-A9 / Debian 11 / GLIBC 2.31）

---

## 一、快速开始

### ★ 一键采集（推荐：部署 + 监听 + 采集 全自动）

PC 侧 Git Bash 里一条命令搞定（等板上线 → 部署 → 监听 → 采集 → 收尾）：

```bash
cd /c/OpenWiFi/tools
bash one_click_collect.sh 5 25 C:/iq
#   参数: [信道] [采集秒数] [输出目录]（信道默认 ch5，信号更强）
#   板子已部署过可加 --skip-deploy 跳过部署步骤
```

数据落盘到 `C:/iq/iq_<时间戳>/`（`.bin` 原始流 + `_csi.csv` CSI 宽表）。

### 手动两段式（调试用）

#### ① PC 侧：启动 UDP 监听（Windows 侧）

```bash
C:/Users/fawei/.workbuddy/binaries/python/versions/3.13.12/python.exe \
  /c/OpenWiFi/tools/win_listen_iq.py C:/iq 60 --layout=iq_first
```

> ★ 在 **Windows** 侧跑（关 mirrored 后 `192.168.10.2` 归 Windows「以太网 2」，见要点 4）。
> `--layout=iq_first`：RTL 修复后 record 布局为 `[IQ块][CSI块]`，同包配对。

#### ② 板端：部署（约 15 秒）

```bash
scp /c/OpenWiFi/tools/e316_deploy.sh root@192.168.10.122:/tmp/
ssh root@192.168.10.122 "sh /tmp/e316_deploy.sh 5"
```

> 参数 = 信道号（默认 5，此频段信号更强）。作用：修复 ADC 绑定 → 彻底重载模块 → monitor 模式 → 加载 side_ch。
> 板端命令直接在 Windows 执行（内置 OpenSSH 客户端；免密需一次性装公钥，见 §八）。

#### ③ 板端：采集（紧跟监听窗口）

```bash
scp /c/OpenWiFi/tools/e316_collect.sh root@192.168.10.122:/tmp/
ssh root@192.168.10.122 "sh /tmp/e316_collect.sh 25 192.168.10.2"
```

> 参数 = 采集秒数（≥10）、PC IP。脚本已内置「采集前排空 FIFO」（第 3 参数排空秒数，默认 2s）。

### 预期结果

板端：

```
loop 64  side info count 58
loop 128 side info count 120
loop 192 side info count 179
Caught signal 2
--- rh20 (采集后, 应 < 1000 表示正常排空): val 708
--- get_side_info WARNING 累计: 0
```

PC 侧（win_listen_iq.py 输出）：

```
[win] ★ 就绪：监听 0.0.0.0:4000  时长 60s
[win] 产物目录: C:\iq\iq_20261010_101234
[win] ... 30 包 / 223552 字节 / 56 record
[win] ... 70 包 / 626744 字节 / 157 record
[win] ===== 最终汇总 =====
[win] record: 169  对齐: (0, 64)
```

---

## 二、板端手动执行（等价于上面 ②③，便于调试）

```bash
cd /root/openwifi

# ---- 部署 ----
# 1) 若 sdr0 不存在，修复 ADC 绑定（重启后常见）
echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind

# 2) 彻底重载模块组（确保 DMA 通道复位）
rmmod side_ch; rmmod sdr xpu openofdm_rx openofdm_tx rx_intf tx_intf; rmmod xilinx_dma
sleep 2
insmod xilinx_dma.ko
for M in tx_intf rx_intf openofdm_tx openofdm_rx xpu sdr; do insmod $M.ko; done
sleep 2

# 3) monitor 模式（★ CSI 必须用 monitor，不能用 fosdem.sh 的 ad-hoc）
ip link set sdr0 down
iw dev sdr0 set type monitor
ip link set sdr0 up
iw dev sdr0 set channel 11
sleep 3

# 4) 加载 side_ch（联合采集：iq_len=440, num_eq=0, combined=1）
insmod side_ch.ko iq_len_init=440 num_eq_init=0 combined_init=1 pre_trigger_init=280
sleep 2
./side_ch_ctl rh20          # 看硬件 FIFO 是否有数据

# ---- 采集 ----
./side_ch_ctl g100 -s 192.168.10.2 > /tmp/side.log 2>&1 &
P=$!
sleep 25
kill -INT $P                # ★ 必须 SIGINT，不能用 timeout/kill -TERM
wait $P
cat /tmp/side.log
```

---

## 三、★ 七个必知要点（全部踩坑总结）

### 1. 停止方式：必须 `kill -INT`，不能 `timeout` / `kill -TERM`

程序**只注册了 SIGINT handler**（打印 `Caught signal 2` 后优雅退出，`do_exit=true`）。  
`SIGTERM` 会强杀进程 → **side_ch DMA 通道永久卡死**，之后 `count` 恒为 0、`rh20` 停在旧值、dmesg 刷 `get_side_info WARNING`。

### 2. 绝不能把 `side_ch_ctl` 接 `| head`

`head` 提前关闭管道 → SIGPIPE 杀死进程 → 同样卡死通道（原因同上）。

### 3. 输出重定向：已内置行缓冲，直接 `> file` 即可

源码 `main()` 开头已加 `setvbuf(stdout, NULL, _IOLBF, 0)`。

> 历史坑：静态链接二进制无法用 `stdbuf`（依赖 `LD_PRELOAD`），此前重定向会全缓冲导致"无输出"。

### 4. UDP 接收端跑在 Windows 侧（关 mirrored 后）

关 mirrored（NAT）后，`192.168.10.2` 归 Windows「以太网 2」独占，WSL 退到 NAT 网段（`172.23.x.x`）。板子发往 `192.168.10.2` 的包进 **Windows 侧**，须用 `win_listen_iq.py`（Windows 原生监听）。

> 历史：早期 mirrored 模式下 `.2` 归 WSL 命名空间，那时用 `wsl_listen_4000.py` 在 WSL 内收；现拓扑已变，改 Windows 侧监听。

### 5. UDP 目标 IP 必须显式指定

板端 `side_ch_ctl` 默认目标 `192.168.10.1`（VMnet1）。要发到 PC 必须加 `-s 192.168.10.2`。

> 历史坑：板端旧版二进制硬编码 `.1`，`-s` 被静默忽略 → 必须用新版。

### 6. 打印周期 = 64 次循环

`g100`（100ms/循环）→ **第一行要等 6.4 秒**。采集时长至少 `interval_ms × 64 / 1000 + 余量`。

### 7. 重启后 `sdr0` 可能不存在

**真因**：开机 `Tuning TX FAILED` → `cf_axi_adc` probe 返回 -5 → 设备与驱动解绑，内核**不会自动重试**。  
**修复**：
```bash
echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind
```
> 若 bind 后设备仍未出现（dmesg 报 `Failed to get converter device: -19`，ad9361 侧注册已陈旧），整链重绑：
> ```bash
> echo 79024000.cf-ad9361-dds-core-lpc > /sys/bus/platform/drivers/cf_axi_dds/unbind
> echo spi0.0 > /sys/bus/spi/drivers/ad9361/unbind; sleep 1
> echo spi0.0 > /sys/bus/spi/drivers/ad9361/bind; sleep 2
> echo 79024000.cf-ad9361-dds-core-lpc > /sys/bus/platform/drivers/cf_axi_dds/bind
> echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind
> ```
> `e316_deploy.sh` 的 STEP1 已内置这两级修复；修复后需重跑部署（sdr.ko 不会自动重新 probe）。

### 通道卡死的救援流程

```bash
rmmod side_ch
rmmod sdr xpu openofdm_rx openofdm_tx rx_intf tx_intf
rmmod xilinx_dma
sleep 2
insmod xilinx_dma.ko
for M in tx_intf rx_intf openofdm_tx openofdm_rx xpu sdr; do insmod $M.ko; done
# 再重设 monitor + 重载 side_ch
```

---

## 四、寄存器对照表

| 寄存器  | 期望值     | 含义                                          |
| ---- | ------- | ------------------------------------------- |
| rh0  | 0       | ctrl                                        |
| rh1  | **1**   | enable                                      |
| rh2  | **998** | RECORD_LEN(440,0) = (1+440)+(2+56) = 499，×2 |
| rh3  | **3**   | trigger 模式                                  |
| rh11 | **280** | pre_trigger（C1 定标，2026-10-15 回环注入实测） |
| rh12 | **440** | iq_len                                      |
| rh20 | 动态      | M_AXIS_DATA_COUNT（>0 即有数据；采集后应 <1000）       |

> `rsN` 读**软件影子**（未 write 时返回 0 属正常）；`rhN` 读**硬件实际值**，诊断以 `rhN` 为准。
>
> 注：rh2（RECORD_LEN）实测值取决于初始化时机——刚 `insmod side_ch` 未触发采集时读到 0，`side_ch_ctl` 首次运行后为 998；诊断关键看 **rh20**（FIFO 计数），rh2 仅作参考。

**数据格式**：`RECORD_LEN(iq_len, num_eq) = (1+iq_len) + (HEADER_LEN + CSI_LEN + num_eq*EQUALIZER_LEN)`  
→ `iq_len=440, num_eq=0` 时 = 499；UDP 包长 = 3992 × 整数倍（3992 = 499×8）

**record 布局**（RTL 修复后，`[IQ块][CSI块]`，499 word = 3992 字节）：

| 偏移(word) | 字段 |
|---|---|
| 0 | TSF_iq（u64，微秒）|
| 1..440 | 440 × IQ 采样（每 word = {I16,Q16,状态24b,GPIO8b}）|
| 441 | TSF_csi（u64，微秒）|
| 442 | phase_offset（i64）|
| 443..498 | 56 × CSI（每 word = {I16,Q16,0,0}）|

> ★ 同一条 record 内 IQ 块与 CSI 块属**同一包**：`TSF_csi − TSF_iq ≈ 13~15 µs`（LTF 尖峰 → header 解完的固定间隔）。这是跨包错位 bug 修复后的正确形态（修复前是 `[CSI块][IQ块]` 且同 record 两块差 ~13ms 跨包）。

---

## 五、诊断速查

| 现象                          | 检查 / 处理                                                |
| --------------------------- | ------------------------------------------------------ |
| `Cannot find device "sdr0"` | ADC 绑定丢失 → 要点 7                                        |
| `count` 恒为 0                | ① `rh20`：=0 表空口无流量（换信道）；>0 表通道卡死 → 救援流程                |
| `rh20` 停在几千不动               | 通道卡死 → 救援流程（要点 1/2）                                    |
| 命令跑完文件为空                    | 确认用的是新版 `side_ch_ctl`（md5 `ba024c66...`）               |
| dmesg 刷 WARNING             | 同上，救援流程                                                |
| PC 收不到 UDP                  | ① 确认加了 `-s 192.168.10.2`；② 确认监听跑在 **Windows** 侧（`win_listen_iq.py`）；③ 确认监听窗口覆盖采集时段 |
| `GLIBC_2.34 not found`      | 用了动态编译版 → 必须 `-static` 重编                              |

---

## 六、重新编译 side_ch_ctl（改源码后）

```bash
# WSL 内执行
cd /mnt/c/OpenWiFi/tools && bash build_sctl2.sh
# 产物: /mnt/c/OpenWiFi/build-output/side_ch_ctl_static
# 部署（Windows 侧）:
scp /mnt/c/OpenWiFi/build-output/side_ch_ctl_static \
  root@192.168.10.122:/root/openwifi/side_ch_ctl
```

> ★ 必须加 `-static`：Vitis 2022.2 工具链动态库要 GLIBC 2.34，板子只有 2.31。

---

## 七、保留的工具与产物

### `C:\OpenWiFi\tools\`（13 个文件）

| 文件                    | 用途                                        |
| --------------------- | ----------------------------------------- |
| `one_click_collect.sh` | ★ 一键采集（等板→部署→监听→采集→收尾）                  |
| `win_listen_iq.py`     | ★ PC 侧 UDP 接收 + 自对齐解析（Windows 跑，`--layout=iq_first`）|
| `build_env.sh`        | ★ 构建环境一站式工具（子模块/Vitis/内核/驱动/FPGA/license） |
| `README-build-env.md` | 构建环境工具说明与踩坑清单                             |
| `e316_deploy.sh`      | 板端部署（模块重载 + monitor + side_ch）            |
| `e316_collect.sh`     | 板端采集（排空 FIFO + SIGINT 收尾 + 推送 UDP）        |
| `build_sctl2.sh`      | 交叉编译 side_ch_ctl（含 `-static`）             |
| `auto_ssh.py`         | SSH 自动喂密码（WSL 内跑，idle 超时可用 `AUTO_SSH_IDLE_TIMEOUT` 调）|
| `push_run.sh`         | 推脚本到板并执行                                  |
| `wait_board.sh`       | 轮询等待板子上线                                  |
| `udp_dump.py`         | UDP 落盘工具                                  |
| `wsl_listen_4000.py`  | 旧：WSL 侧 UDP 接收（mirrored 模式时代用，现 NAT 拓扑下收不到）|
| `Xilinx.lic`          | Viterbi license 文件                        |

> 探索期的 293 个历史脚本已于 2026-10-10 清理：  
> 其中 27 个安装/构建类（Vitis 安装、内核构建、FPGA 综合、license 等）**已全部合并进 `build_env.sh`**，  
> 其余为一次性排查脚本，经隔离验证（隐藏后全链路重跑通过）确认无依赖后删除。

### `C:\OpenWiFi\build-output\`

| 文件                            | 说明                                             |
| ----------------------------- | ---------------------------------------------- |
| `side_ch_ctl_static`          | 新版 side_ch_ctl（md5 `ba024c66...`，含 `-s` + 行缓冲） |
| `ko/side_ch.ko`               | v4 驱动（md5 `cebbe822...`）                       |
| `ko/*.ko`                     | 其余 6 个 openwifi 模块                             |
| `BOOT.BIN` / `system_top.bit` | 固件                                             |

板端 `/root/openwifi/`：`side_ch.ko`（v4）、`side_ch_ctl`（新版）、`load_fpga_img.sh`、`wgd.sh`、`monitor_ch.sh`、`agc_settings.sh`

---

## 八、环境约束备忘

- 板端命令直接在 **Windows**（Git Bash / PowerShell）执行：系统内置 OpenSSH 客户端（ssh/scp），无需 WSL
- 免密：一次性把本机公钥装入板端（Git Bash 无密钥先 `ssh-keygen -t ed25519`；首次装公钥借道 WSL 的 `auto_ssh.py` 喂密码，之后不再需要）：
  ```bash
  wsl.exe -d Ubuntu-22.04 -- bash -lc "cd /mnt/c/OpenWiFi/tools && python3 auto_ssh.py openwifi ssh root@192.168.10.122 \"mkdir -p /root/.ssh; echo \$(cat ~/.ssh/id_ed25519.pub) >> /root/.ssh/authorized_keys; chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys\""
  ```
- Windows 侧 Python **无 `pty` 模块**，`auto_ssh.py` 只能在 WSL 内跑（仅用于上面的首次装公钥）
- 推文件用 **scp**（base64 嵌套引号会丢内容）
- `ad9361` / `mac80211` / `cf_axi_adc` 在此板为**内核内建**（`insmod ad9361_drv.ko` 失败无害）
- `wgd.sh` 在本环境跑不完整（`set -e` + 内建模块）→ **改手动逐个 insmod**
- `sdrctl dev sdr0 get reg side_ch` 不支持（此板 sdrctl 部分功能不可用）
- 网络拓扑（NAT，关 mirrored）：板子 `.122` ｜ Windows「以太网 2」= `.2`（UDP 目标）｜ WSL `eth0` = `172.23.x.x`（NAT，不在 192.168.10 网段）｜ VMnet1 = `.1`

---

## 九、验证记录

| 日期         | 信道       | 板端 count            | PC 接收               | WARNING |
| ---------- | -------- | ------------------- | ------------------- | ------- |
| 2026-10-10 | ch11     | 56 / 117 / 174      | 200 包 / 1,892,208 B | 0       |
| 2026-10-10 | ch11     | 58 / 120 / 179（93%） | 200 包 / 2,199,592 B | 0       |
| 2026-10-10 | ch6      | 36 / 64 / 99        | 100 包 / 742,512 B   | 0       |
| 2026-10-10 | ch11（早期） | 60 / 122 / 181      | 150 包 / 1,093,808 B | 0       |
| 2026-10-10 | ch11（RTL 修复后） | —            | 169 record / 694KB，全部同包（TSF 差 13~15µs）| 0  |

> ★ 2026-10-10 RTL 跨包错位修复后上板验证：169 条 record 全部 `[IQ][CSI]` 同包对齐（同 record 内 `TSF_csi − TSF_iq = 13~15µs`），跨包错位 bug 彻底修复、数据可做正确联合分析。

> 2026-10-10 清理归档目录时做过**隔离验证**：把归档改名隐藏后在"其不存在"条件下  
> 重跑全链路（部署 delta=57 → 采集 count=174 → `rh20` 3934→1323 → PC 收 200 包），  
> 一次通过，确认删除零影响。
