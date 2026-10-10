# OpenWiFi CSI+IQ 联合采集 — 操作手册（E316 精简版）

> 版本：2026-10-10 ｜ 状态：✅ 已端到端验证通过（ch11 / ch6 多次复现）
> 平台：AntSDR E316（Zynq-7000 / AD9361 / ARMv7 Cortex-A9 / Debian 11 / GLIBC 2.31）

---

## 一、快速开始（两段式）

### ① PC 侧：启动 UDP 监听（WSL 内，常驻一个窗口）
```bash
cd /mnt/c/OpenWiFi/tools
python3 wsl_listen_4000.py
```
> ★ 必须在 **WSL 内**跑。Windows 侧 Python 收不到包（见要点 4）。

### ② 板端：部署（约 15 秒）
```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc 'cd /mnt/c/OpenWiFi/tools && \
  python3 auto_ssh.py openwifi scp e316_deploy.sh root@192.168.10.122:/tmp/ && \
  python3 auto_ssh.py openwifi ssh root@192.168.10.122 "sh /tmp/e316_deploy.sh 11"'
```
> 参数 = 信道号（默认 11）。作用：修复 ADC 绑定 → 彻底重载模块 → monitor 模式 → 加载 side_ch。

### ③ 板端：采集（紧跟监听窗口）
```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc 'cd /mnt/c/OpenWiFi/tools && \
  python3 auto_ssh.py openwifi scp e316_collect.sh root@192.168.10.122:/tmp/ && \
  python3 auto_ssh.py openwifi ssh root@192.168.10.122 "sh /tmp/e316_collect.sh 25 192.168.10.2"'
```
> 参数 = 采集秒数（≥10）、PC IP。

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
PC 侧：
```
[wsl] #1 from 192.168.10.122:44835 len=27944
[wsl] ... 200 pkts 2199592 bytes
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
insmod side_ch.ko iq_len_init=440 num_eq_init=0 combined_init=1 pre_trigger_init=256
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

### 4. UDP 接收端必须跑在 WSL 内
WSL2 `mirrored` 模式下，板子发往 `192.168.10.2` 的包进入 **WSL 网络命名空间**（WSL `eth4` 与 Windows「以太网 2」共享该 IP），Windows 宿主 socket 收不到。

### 5. UDP 目标 IP 必须显式指定
板端 `side_ch_ctl` 默认目标 `192.168.10.1`（VMnet1）。要发到 PC 必须加 `-s 192.168.10.2`。
> 历史坑：板端旧版二进制硬编码 `.1`，`-s` 被静默忽略 → 必须用新版。

### 6. 打印周期 = 64 次循环
`g100`（100ms/循环）→ **第一行要等 6.4 秒**。采集时长至少 `interval_ms × 64 / 1000 + 余量`。

### 7. 重启后 `sdr0` 可能不存在
**真因**：开机 `Tuning TX FAILED` → `cf_axi_adc` probe 返回 -5 → 设备与驱动解绑，内核**不会自动重试**。
**修复**：`echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind`

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

| 寄存器 | 期望值 | 含义 |
|---|---|---|
| rh0 | 0 | ctrl |
| rh1 | **1** | enable |
| rh2 | **998** | RECORD_LEN(440,0) = (1+440)+(2+56) = 499，×2 |
| rh3 | **3** | trigger 模式 |
| rh11 | **256** | pre_trigger |
| rh12 | **440** | iq_len |
| rh20 | 动态 | M_AXIS_DATA_COUNT（>0 即有数据；采集后应 <1000） |

> `rsN` 读**软件影子**（未 write 时返回 0 属正常）；`rhN` 读**硬件实际值**，诊断以 `rhN` 为准。

**数据格式**：`RECORD_LEN(iq_len, num_eq) = (1+iq_len) + (HEADER_LEN + CSI_LEN + num_eq*EQUALIZER_LEN)`
→ `iq_len=440, num_eq=0` 时 = 499；UDP 包长 = 3992 × 整数倍（3992 = 499×8）

---

## 五、诊断速查

| 现象 | 检查 / 处理 |
|---|---|
| `Cannot find device "sdr0"` | ADC 绑定丢失 → 要点 7 |
| `count` 恒为 0 | ① `rh20`：=0 表空口无流量（换信道）；>0 表通道卡死 → 救援流程 |
| `rh20` 停在几千不动 | 通道卡死 → 救援流程（要点 1/2） |
| 命令跑完文件为空 | 确认用的是新版 `side_ch_ctl`（md5 `ba024c66...`） |
| dmesg 刷 WARNING | 同上，救援流程 |
| PC 收不到 UDP | ① 确认加了 `-s 192.168.10.2`；② 确认监听跑在 WSL 内；③ 确认监听窗口覆盖采集时段 |
| `GLIBC_2.34 not found` | 用了动态编译版 → 必须 `-static` 重编 |

---

## 六、重新编译 side_ch_ctl（改源码后）

```bash
# WSL 内执行
cd /mnt/c/OpenWiFi/tools && bash build_sctl2.sh
# 产物: /mnt/c/OpenWiFi/build-output/side_ch_ctl_static
# 部署:
cd /mnt/c/OpenWiFi/tools && python3 auto_ssh.py openwifi scp \
  /mnt/c/OpenWiFi/build-output/side_ch_ctl_static \
  root@192.168.10.122:/root/openwifi/side_ch_ctl
```
> ★ 必须加 `-static`：Vitis 2022.2 工具链动态库要 GLIBC 2.34，板子只有 2.31。

---

## 七、保留的工具与产物

### `C:\OpenWiFi\tools\`（10 个文件）
| 文件 | 用途 |
|---|---|
| `wsl_listen_4000.py` | ★ PC 侧 UDP 接收（WSL 内跑） |
| `build_env.sh` | ★ 构建环境一站式工具（子模块/Vitis/内核/驱动/FPGA/license） |
| `README-build-env.md` | 构建环境工具说明与踩坑清单 |
| `e316_deploy.sh` | 板端部署（模块重载 + monitor + side_ch） |
| `e316_collect.sh` | 板端采集（SIGINT 收尾 + 推送 UDP） |
| `build_sctl2.sh` | 交叉编译 side_ch_ctl（含 `-static`） |
| `auto_ssh.py` | SSH 自动喂密码（WSL 内跑） |
| `push_run.sh` | 推脚本到板并执行 |
| `wait_board.sh` | 轮询等待板子上线 |
| `udp_dump.py` | UDP 落盘工具 |
| `Xilinx.lic` | Viterbi license 文件 |

> 探索期的 293 个历史脚本已于 2026-10-10 清理：
> 其中 27 个安装/构建类（Vitis 安装、内核构建、FPGA 综合、license 等）**已全部合并进 `build_env.sh`**，
> 其余为一次性排查脚本，经隔离验证（隐藏后全链路重跑通过）确认无依赖后删除。

### `C:\OpenWiFi\build-output\`
| 文件 | 说明 |
|---|---|
| `side_ch_ctl_static` | 新版 side_ch_ctl（md5 `ba024c66...`，含 `-s` + 行缓冲） |
| `ko/side_ch.ko` | v4 驱动（md5 `cebbe822...`） |
| `ko/*.ko` | 其余 6 个 openwifi 模块 |
| `BOOT.BIN` / `system_top.bit` | 固件 |

板端 `/root/openwifi/`：`side_ch.ko`（v4）、`side_ch_ctl`（新版）、`load_fpga_img.sh`、`wgd.sh`、`monitor_ch.sh`、`agc_settings.sh`

---

## 八、环境约束备忘

- 板端命令经 WSL 执行：`wsl.exe -d Ubuntu-22.04 -- bash -lc '...'`
- Windows 侧 Python **无 `pty` 模块**，`auto_ssh.py` 只能在 WSL 内跑
- 推文件用 **scp**（base64 嵌套引号会丢内容）
- `ad9361` / `mac80211` / `cf_axi_adc` 在此板为**内核内建**（`insmod ad9361_drv.ko` 失败无害）
- `wgd.sh` 在本环境跑不完整（`set -e` + 内建模块）→ **改手动逐个 insmod**
- `sdrctl dev sdr0 get reg side_ch` 不支持（此板 sdrctl 部分功能不可用）
- 网络拓扑：板子 `.122` ｜ WSL `eth4` = `.2` ｜ Windows「以太网 2」= `.2` ｜ VMnet1 = `.1`

---

## 九、验证记录

| 日期 | 信道 | 板端 count | PC 接收 | WARNING |
|---|---|---|---|---|
| 2026-10-10 | ch11 | 56 / 117 / 174 | 200 包 / 1,892,208 B | 0 |
| 2026-10-10 | ch11 | 58 / 120 / 179（93%） | 200 包 / 2,199,592 B | 0 |
| 2026-10-10 | ch6 | 36 / 64 / 99 | 100 包 / 742,512 B | 0 |
| 2026-10-10 | ch11（早期） | 60 / 122 / 181 | 150 包 / 1,093,808 B | 0 |

> 2026-10-10 清理归档目录时做过**隔离验证**：把归档改名隐藏后在"其不存在"条件下
> 重跑全链路（部署 delta=57 → 采集 count=174 → `rh20` 3934→1323 → PC 收 200 包），
> 一次通过，确认删除零影响。
