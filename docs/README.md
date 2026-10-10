# 文档索引 —— OpenWiFi CSI+IQ 联合采集（AntSDR E316）

## 状态
✅ **端到端全链路打通并多次复现验证**（2026-10-10）

空口 802.11 帧 → `sdr0` monitor → `side_ch` DMA 采集 → UDP:4000 → PC（WSL）完整到达。

## 马上开始

👉 **[OpenWiFi-CSI-IQ-操作手册.md](OpenWiFi-CSI-IQ-操作手册.md)** —— 两段式命令，复制即可跑

```bash
# 工作环境准备（首次）：在仓库根执行
./setup.sh

# ① PC 侧起监听（WSL/Linux 内，常驻）
python3 ~/tools/wsl_listen_4000.py
# ② 板端部署
~/tools/e316_deploy.sh 11
# ③ 板端采集
~/tools/e316_collect.sh 25 192.168.10.2
```

---

## 构建与原理

| 文档 | 内容 |
|---|---|
| [构建与原理/OpenWiFi-CSI-IQ-WSL构建部署流程.md](构建与原理/OpenWiFi-CSI-IQ-WSL构建部署流程.md) | WSL2 从零构建到上板的逐条流程 |
| [构建与原理/OpenWiFi-CSI-IQ同时采集-部署文档.md](构建与原理/OpenWiFi-CSI-IQ同时采集-部署文档.md) | 路线 A 构建、上板部署与验证手册 |
| [构建与原理/OpenWiFi-CSI-IQ同时采集-开发文档.md](构建与原理/OpenWiFi-CSI-IQ同时采集-开发文档.md) | RTL 时序与驱动实现原理 |
| [构建与原理/OpenWiFi-CSI-IQ同时采集-路线A-实现计划与代码骨架.md](构建与原理/OpenWiFi-CSI-IQ同时采集-路线A-实现计划与代码骨架.md) | 代码改动对照 |
| [构建与原理/OpenWiFi-CSI-IQ同时采集-方案汇报.md](构建与原理/OpenWiFi-CSI-IQ同时采集-方案汇报.md) | 方案汇报稿 |

## 学习资料

| 文档 | 内容 |
|---|---|
| [学习资料/OpenWiFi-接收链路入门资料.md](学习资料/OpenWiFi-接收链路入门资料.md) | mac80211 框架与帧转发机制 |
| [学习资料/OpenWiFi-二次开发指南.md](学习资料/OpenWiFi-二次开发指南.md) | 二次开发上手 |
| [学习资料/OpenWiFi-二次开发指南-核对报告.md](学习资料/OpenWiFi-二次开发指南-核对报告.md) | 指南与源码核对 |
| [学习资料/ANTSDR-E316-openwifi-烧录指南.md](学习资料/ANTSDR-E316-openwifi-烧录指南.md) | SD 卡镜像、dd 写盘、bitstream 加载 |
| [学习资料/ANtsDR-E316-联合采集-人工执行清单.md](学习资料/ANtsDR-E316-联合采集-人工执行清单.md) | 人工逐步执行清单 |
| [学习资料/Viterbi-License-申请与安装清单.md](学习资料/Viterbi-License-申请与安装清单.md) | Xilinx Viterbi 评估 license |

## 问题定位

| 文档 | 内容 |
|---|---|
| [OpenWiFi-CSI-IQ-上板全链路打通报告.md](OpenWiFi-CSI-IQ-上板全链路打通报告.md) | 6 个阻塞点根因分析（含两个隐藏根因） |

---

## 核心参数速查

| 项 | 值 |
|---|---|
| 板子 IP | `192.168.10.122` |
| PC IP（WSL `eth4`） | `192.168.10.2` |
| UDP 端口 | `4000` |
| side_ch 加载参数 | `iq_len_init=440 num_eq_init=0 combined_init=1 pre_trigger_init=256` |
| 记录长度 | `RECORD_LEN(440,0) = 499` |
| UDP 包长 | 3992 的整数倍（3992 = 499×8） |

## 最容易踩的三个坑

1. **停止 `side_ch_ctl` 必须用 `kill -INT`**，`timeout`（SIGTERM）或 `| head` 会永久卡死 DMA 通道
2. **UDP 监听必须跑在 WSL/Linux 内**，Windows 侧收不到（mirrored 网络模式）
3. **`sdr0` 不创建**时，执行 `echo 79020000.cf-ad9361-lpc > /sys/bus/platform/drivers/cf_axi_adc/bind`

详见操作手册「★ 七个必知要点」。构建环境另有 11 条踩坑，见 `tools/README-build-env.md`。
