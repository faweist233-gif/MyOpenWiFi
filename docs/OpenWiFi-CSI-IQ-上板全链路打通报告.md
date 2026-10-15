# OpenWiFi CSI+IQ 联合采集 — E316 上板全链路打通报告

**日期**：2026-10-10
**平台**：AntSDR E316（Zynq-7000 / AD9361 / ARMv7 Cortex-A9 / Debian 11）
**状态**：✅ **端到端全链路打通并多次复现验证**

> 日常操作请看 `OpenWiFi-CSI-IQ-操作手册.md`（精简版，含可复制命令）。
> 本文档记录问题定位过程与根因分析。

---

## 一、最终结果

空口 802.11 帧 → `sdr0` monitor 模式 → `side_ch` DMA 采集 → `side_ch_ctl` 解包 → UDP:4000 → PC（Windows 侧）**完整到达**。

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
**真因**：开机时 `cf_axi_adc` probe 因 ADC 数字调谐失败返回 `-5`，设备 `79020000.cf-ad9361-lpc` 与驱动解绑，且内核**不会自动重试**。`load_fpga_img.sh`（上游 @2f112f0 已核对）会按 `cf_axi_adc`(79020000) → `cf_axi_dds`(79024000) → `ad9361`(spi0.0) 的顺序解绑并用 `bind_until_ready` 重绑三者，但 ADC 调谐失败期间重绑仍会 probe 失败，脚本等待/重试后板子依旧没有 `sdr0`。

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
> 后续复现（2026-10-15）：重启后直接 bind 可能报 `Failed to get converter device: -19`（ad9361 侧 converter 注册已因开机 probe 失败而陈旧）。此时需整链重绑：unbind `cf_axi_dds` + `ad9361`(spi0.0) → bind `spi0.0`（重跑 ad9361 probe，TX tune 可恢复）→ bind `cf_axi_dds` → bind `cf_axi_adc`；随后重载 sdr 模块组才有 `sdr0`。`e316_deploy.sh` STEP1 已内置该两级修复（见操作手册要点 7）。

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
**真因（当时的 mirrored 拓扑）**：WSL2 `mirrored` 模式下，板子发往 `192.168.10.2` 的包进入 **WSL 网络命名空间**（WSL `eth4` 与 Windows「以太网 2」共享该 IP `.2`），**Windows 宿主 socket 收不到**。当时的修复是监听器跑在 WSL 内（`wsl_listen_4000.py`）。

```
[wsl] #1 from 192.168.10.122:55465 len=27944
[wsl] ... 200 pkts 1896200 bytes
```

> ★ **现拓扑已变**：mirrored 已关闭（NAT），`192.168.10.2` 归 Windows「以太网 2」独占、WSL 退到 `172.23.x.x`。**监听器现在必须在 Windows 侧跑**（`win_listen_iq.py --layout=iq_first`），`wsl_listen_4000.py` 收不到（见操作手册要点 4）。

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

> 板端：`tools/e316_deploy.sh <CH>` + `tools/e316_collect.sh <DUR> <PC_IP>`（手动等价步骤见 `OpenWiFi-CSI-IQ-操作手册.md` 第二节）；PC 侧监听：Windows 侧 `tools/win_listen_iq.py C:/iq <秒> --layout=iq_first`。

---

## 四、寄存器 / 诊断 / 产物 / 环境备忘

> 见 `OpenWiFi-CSI-IQ-操作手册.md` §四（寄存器）、§五（诊断）、§七（工具与产物）、§八（环境备忘）。
