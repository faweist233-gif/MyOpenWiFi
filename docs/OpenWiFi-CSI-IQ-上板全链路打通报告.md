# OpenWiFi CSI+IQ 联合采集 — E316 上板全链路打通报告

**日期**：2026-10-10
**平台**：AntSDR E316（Zynq-7000 / AD9361 / ARMv7 Cortex-A9 / Debian 11）
**状态**：✅ **端到端全链路打通并多次复现验证**

> 日常操作请看 `OpenWiFi-CSI-IQ-操作手册.md`（精简版，含可复制命令）。
> 本文档记录问题定位过程与根因分析。

---

## 一、最终结果

空口 802.11 帧 → `sdr0` monitor 模式 → `side_ch` DMA 采集 → `side_ch_ctl` 解包 → UDP:4000 → PC（WSL）**完整到达**。

| 信道 | 板端 count（每 64 循环） | 成功率 | PC 接收 | WARNING |
|---|---|---|---|---|
| ch11 | 58 / 120 / 179 | 93% | 200 包 / 2,199,592 B | **0** |
| ch6 | 36 / 64 / 99 | — | 100 包 / 742,512 B | **0** |
| ch11（早期） | 60 / 122 / 181 | 94% | 150 包 / 1,093,808 B | **0** |

- 包长全部为 **3992 的整数倍**（3992 = 499 × 8，499 = RECORD_LEN(440,0)）
- DMA 正常排空：`rh20` 从 3934 降到 708 / 0

---

## 二、攻克的 6 个阻塞点

### 阻塞点 1：重启后 `sdr0` 未创建
**真因**：开机时 `cf_axi_adc` probe 因 ADC 数字调谐失败返回 `-5`，设备 `79020000.cf-ad9361-lpc` 与驱动解绑。`load_fpga_img.sh` 只 unbind/bind `spi0.0`（AD9361），**不会触发 `cf_axi_adc` 重新 probe**，脚本卡在 `while` 死等。

```
[2.741685] ad9361_dig_tune_delay: Tuning TX FAILED!
[2.747557] cf_axi_adc 79020000.cf-ad9361-lpc: probe failed with error -5
[53.328896] sdr,sdr find_dev ad9361-phy failed. dev.driver_data == NULL
```

**修复**：
```bash
echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind
# dmesg: cf_axi_adc ... probed ADC AD9361 as MASTER → sdr0 随即创建
```

### 阻塞点 2：`-s` 参数被忽略，UDP 全发到错误 IP
**真因**：板端 `side_ch_ctl` 是**旧版二进制**（md5 `57e0e777...`），`strings` 查无 `Missing server`，**硬编码 `192.168.10.1`**。

strace 铁证：`sendto(4, ..., 4000, sin_addr=inet_addr("192.168.10.1"))`（85 次全是 `.1`）

**修复**：交叉编译新版并部署
```bash
TC=/opt/Xilinx/Vitis/2022.2/gnu/aarch32/lin/gcc-arm-linux-gnueabi/bin/arm-linux-gnueabihf-
${TC}gcc -O2 -Wall -static -o side_ch_ctl_static side_ch_ctl.c   # ★ 必须 -static
```
> 动态版要求 GLIBC 2.34，板子只有 2.31 → `-static` 绕开。

### 阻塞点 3：PC 收不到 UDP
**真因**：WSL2 `mirrored` 模式下，板子发往 `192.168.10.2` 的包进入 **WSL 网络命名空间**（WSL `eth4` 与 Windows「以太网 2」共享该 IP `.2`），**Windows 宿主 socket 收不到**。

**修复**：监听器必须跑在 **WSL 内**（`wsl_listen_4000.py`）。

```
[wsl] #1 from 192.168.10.122:55465 len=27944
[wsl] ... 200 pkts 1896200 bytes
```

### 阻塞点 4：`| head` / `timeout`(SIGTERM) 会卡死 DMA 通道 ★ 最关键
**真因**：`side_ch_ctl` **只注册了 `SIGINT` handler**（`do_exit=true` 优雅退出）。
- `| head` → SIGPIPE
- `timeout N` → SIGTERM

两者都**强杀进程**，导致 `side_ch` 的 S2MM/MM2S DMA 通道**永久卡死**。症状：
- `count` 恒为 0
- `rh20` 停在旧值不动（如 3644）
- dmesg 刷 `get_side_info WARNING status!=DMA_COMPLETE`（可达 500+ 条）

**修复**：
```bash
./side_ch_ctl g100 -s 192.168.10.2 > /tmp/side.log 2>&1 &
P=$!; sleep 25; kill -INT $P; wait $P     # ★ SIGINT，不是 timeout
```
**救援**：彻底重载模块组（`rmmod side_ch` + sdr 组 + `xilinx_dma`，再按序 insmod）

### 阻塞点 5：输出"消失"（静态二进制 + stdbuf 无效）★★
**真因**：`side_ch_ctl` 是**静态链接**，`stdbuf -oL` 依赖 `LD_PRELOAD` 对静态程序**无效**。重定向到文件时 stdout 全缓冲（4KB），`count` 行太短填不满 → 程序退出前不 flush → **看起来"无输出"**，极易误判为故障。

**修复**：在源码 `main()` 开头加
```c
setvbuf(stdout, NULL, _IOLBF, 0);
```
重新编译部署后，普通 `> file` 即可立即拿到输出。

### 阻塞点 6：DMA 描述符提交后 cookie 判定（v4 驱动修复）
`chan_to_ps_cookie == 0` 时 `dma_async_is_complete(0, last_complete>0, ...)` 恒返回 `DMA_IN_PROGRESS`，导致 `get_side_info` 一直返回 -1。
**修复**：cookie 为 0 时直接放行（v4 驱动 `cebbe822...`）。效果：WARNING 从 60 条/6 秒 → **0**。

---

## 三、最终部署流程

### PC 侧（WSL）
```bash
cd /mnt/c/OpenWiFi/tools && python3 wsl_listen_4000.py
```

### 板端
```bash
cd /root/openwifi
# 0) ADC 绑定（若 sdr0 缺失）
echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind
# 1) 彻底重载模块组
rmmod side_ch; rmmod sdr xpu openofdm_rx openofdm_tx rx_intf tx_intf; rmmod xilinx_dma
sleep 2
insmod xilinx_dma.ko
for M in tx_intf rx_intf openofdm_tx openofdm_rx xpu sdr; do insmod $M.ko; done
# 2) monitor 模式
ip link set sdr0 down; iw dev sdr0 set type monitor; ip link set sdr0 up; iw dev sdr0 set channel 11
# 3) side_ch
insmod side_ch.ko iq_len_init=440 num_eq_init=0 combined_init=1 pre_trigger_init=256
# 4) 采集 + 推 UDP（SIGINT 收尾）
./side_ch_ctl g100 -s 192.168.10.2 > /tmp/side.log 2>&1 &
P=$!; sleep 25; kill -INT $P; wait $P; cat /tmp/side.log
```
> 也可直接用 `tools/e316_deploy.sh <CH>` + `tools/e316_collect.sh <DUR> <PC_IP>` 两段式脚本。

---

## 四、寄存器对照表

| 寄存器 | 期望值 | 含义 |
|---|---|---|
| rh0 | 0 | ctrl |
| rh1 | **1** | enable |
| rh2 | **998** | RECORD_LEN(440,0) = 499 × 2 |
| rh3 | **3** | trigger 模式 |
| rh11 | **256** | pre_trigger |
| rh12 | **440** | iq_len |
| rh20 | 动态 | M_AXIS_DATA_COUNT（>0 有数据；采集后应 <1000） |

> `rsN` 读软件影子；`rhN` 读硬件实际值，诊断以 `rhN` 为准。

**数据格式**：`RECORD_LEN(iq_len, num_eq) = (1+iq_len) + (HEADER_LEN + CSI_LEN + num_eq*EQUALIZER_LEN)`
→ `iq_len=440, num_eq=0` 时 = 499；UDP 包长 = 3992 的整数倍

---

## 五、诊断速查

| 现象 | 检查 / 处理 |
|---|---|
| `Cannot find device "sdr0"` | ADC 绑定丢失 → 阻塞点 1 |
| `count` 恒为 0 | `rh20`：=0 表空口无流量（换信道）；>0 表通道卡死 → 救援流程 |
| `rh20` 停在几千不动 | 通道卡死 → 阻塞点 4 救援流程 |
| 命令跑完文件为空 | 确认用新版 `side_ch_ctl`（md5 `ba024c66...`，含行缓冲） |
| dmesg 刷 WARNING | 同上，救援流程 |
| PC 收不到 UDP | ① 加 `-s 192.168.10.2`；② 监听在 WSL 内；③ 监听窗口覆盖采集时段 |
| `GLIBC_2.34 not found` | 用了动态版 → 必须 `-static` |

---

## 六、产物清单

### `C:\OpenWiFi\tools\`（10 个文件）
| 文件 | 说明 |
|---|---|
| `wsl_listen_4000.py` | ★ PC 侧 UDP 接收（WSL 内跑） |
| `e316_deploy.sh` | 板端部署（模块重载 + monitor + side_ch） |
| `e316_collect.sh` | 板端采集（SIGINT 收尾 + 推 UDP） |
| `build_sctl2.sh` | 交叉编译 side_ch_ctl（`-static`） |
| `auto_ssh.py` | SSH 自动喂密码（WSL 内跑） |
| `build_env.sh` | ★ 构建环境一站式工具（27 个历史安装/构建脚本合并） |
| `README-build-env.md` | 构建环境工具说明与踩坑清单 |
| `push_run.sh` / `wait_board.sh` / `udp_dump.py` | 辅助工具 |
| `Xilinx.lic` | Viterbi license 文件 |

探索期的 293 个历史脚本已于 2026-10-10 清理：27 个安装/构建类已合并进 `build_env.sh`，
其余经隔离验证（隐藏后全链路重跑通过）确认无依赖后删除。

### `C:\OpenWiFi\build-output\`
| 文件 | md5 / 说明 |
|---|---|
| `side_ch_ctl_static` | `ba024c66...`（含 `-s` 解析 + 行缓冲） |
| `ko/side_ch.ko` | `cebbe822...`（v4 驱动） |
| `ko/*.ko` | 其余 6 个 openwifi 模块 |
| `BOOT.BIN` / `system_top.bit` | 固件 |

### 板端 `/root/openwifi/`
`side_ch.ko`（v4）、`side_ch_ctl`（新版）、`load_fpga_img.sh`、`wgd.sh`、`monitor_ch.sh`、`agc_settings.sh`

---

## 七、环境约束备忘

- 板端命令经 WSL 执行：`wsl.exe -d Ubuntu-22.04 -- bash -lc '...'`
- Windows 侧 Python **无 `pty` 模块**，`auto_ssh.py` 只能在 WSL 内跑
- 推文件用 **scp**（base64 嵌套引号会丢内容）
- `ad9361` / `mac80211` / `cf_axi_adc` 在此板为**内核内建**
- `wgd.sh` 在本环境跑不完整 → **改手动逐个 insmod**
- 网络拓扑：板子 `.122` ｜ WSL `eth4` = `.2` ｜ Windows「以太网 2」= `.2` ｜ VMnet1 = `.1`
