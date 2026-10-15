# 前导码解析与可视化工具（preamble_parse / preamble_plot / preamble_selftest）

把板端 side_ch 联合采集的 **440 采样前导码 IQ 块**解析成 L-STF / L-LTF / L-SIG
三个字段的完整参数，并生成图表 + HTML 报告。

方法参考 USRP 采集项目 `/Users/juicy/Projects/usrp/` 的
`parse_preamble.py`（7 步流水线）与 `plot_preamble.py`（图表/报告），
针对 openwifi 板端数据的特点做了三处适配：

| 参考项目（USRP N310） | 本项目（openwifi E316） |
|---|---|
| 全文件流式扫描找前导码 | 板端 FPGA 已按 LTF 尖峰锁定回读窗口，**每条 record 就是一个包**，只需在 440 采样窗口内做 ±40 采样精定位 |
| 25 MS/s sc16 需重采样到 20 MS/s | IQ 块天然 20 MS/s，无需重采样 |
| 只有 IQ | 每 record 自带 **56 子载波 CSI** 与 **phase_offset(CFO)**，作为独立旁证与本方重算结果互验 |

---

## 一、文件

| 文件 | 用途 |
|---|---|
| `preamble_parse.py` | ★ 解析器：读 `iq_*.bin` / `csi_iq.txt` → 逐 record 前导码解析 → `frames.json` + `frames.npz` |
| `preamble_plot.py` | ★ 可视化：读解析产物 → 8 张图 + `report.html` |
| `preamble_selftest.py` | 合成帧回环自检 + `--demo` 生成无需板子的演示数据 |

依赖：Python 3.9+、numpy、scipy（仅 parse 的 resample 用不到，保留无妨）、matplotlib。

---

## 二、用法（采集完数据之后）

> ★ **一键脚本已内置这两步**：`one_click_collect.sh` 在采集收尾后自动对最新
> `iq_*` 目录跑解析 + 绘图，并用浏览器打开 `report.html`；Windows Python 缺
> numpy/scipy/matplotlib 时会提示安装命令（`--skip-viz` 可跳过）。以下为手动方式。

```bash
# ① 解析采集目录（自动找里面的 iq_*.bin）
python preamble_parse.py C:/iq/iq_20261010_123456

# ② 生成图表与 HTML 报告
python preamble_plot.py --dir C:/iq/iq_20261010_123456/preamble_analysis

# 也可以直接解析 bin / txt
python preamble_parse.py C:/iq/iq_x/iq_x.bin --out ./analysis
python preamble_parse.py csi_iq.txt --max-records 100
```

打开 `preamble_analysis/report.html` 即可看到全部结果。

### 常用选项（preamble_parse.py）

| 选项 | 说明 |
|---|---|
| `--layout iq_first` | record 块顺序，默认 `iq_first`（RTL 修复后）；历史 bitstream 用 `csi_first` |
| `--max-records N` | 只解析前 N 条 |
| `--keep-iq N` | npz 里保留原始 IQ 的前 N 条（供 `--show`），默认 500 |
| `--out DIR` | 输出目录，默认在输入旁新建 `preamble_analysis/` |

### 常用选项（preamble_plot.py）

```bash
python preamble_plot.py --dir <analysis> --show 12   # 额外渲染第 12 条 record 的明细图
```

---

## 三、输出

| 文件 | 内容 |
|---|---|
| `fig_timeline.png` | 逐 record 功率/RSSI 时间线，下方标 L-SIG 解出（蓝）/确认未过（橙） |
| `fig_preamble.png` | 对齐平均后的 L-STF/L-LTF 包络 vs 标准参考模板 |
| `fig_spectrum.png` | 前导码 PSD vs 参考频谱（L-STF 只占 4 的倍数子载波） |
| `fig_channel.png` | L-LTF 重算的每子载波 \|H\|（左）vs 板端 openofdm CSI（右），附复相干/dB 相关 |
| `fig_constellation.png` | L-SIG BPSK 星座 + 判决直方图 |
| `fig_cfo.png` | CFO 分布 + 与板端 phase_offset 对照 |
| `fig_lsig.png` | 解出的 RATE / LENGTH 分布 |
| `fig_record.png` | 最佳 record 的 22 μs IQ 明细（包络 vs 模板 + 逐采样 RSSI） |
| `report.html` | 全部图表 + 数字汇总 + 可信度说明 |
| `frames.json` | 逐 record 标量参数（含未通过校验者及原因） |
| `frames.npz` | 对齐前导码波形、L-SIG 星座、原始 IQ、板端 CSI、时间线 |

---

## 四、判定体系（防噪声假阳性）

一条 record 被认定为真实 legacy 前导码需**同时满足**：

1. `parity_ok` —— L-SIG 第 17 位是前 17 位的偶校验；
2. `rate_ok` —— RATE 是 802.11-2016 表 17-4 的 8 个合法值之一；
3. `reserved = 0` —— 保留位发送方固定填 0；
4. `re-encode ≥ 90%` —— 解码出的 24 bit 重新卷积编码后，与接收端 48 bit
   硬判决的符合率。纯噪声 Viterbi 译码会终止于 0 状态（尾比特"天然全零"），
   单靠 parity/rate 假接受率约 25%；重编码符合率把噪声（均值 ~76%，最坏 ~88%）
   与真实帧（~100%）分离。

另有 L-LTF 参考序列匹配（≥0.35）作为"前导码确认"门槛。

---

## 五、自检（改完代码必跑）

```bash
python preamble_selftest.py
# 期望: 18 PASS / 0 FAIL

# 生成演示数据(无需板子,前导码合成):
python preamble_selftest.py --demo demo.bin
python preamble_parse.py demo.bin --out analysis
python preamble_plot.py --dir analysis
```

自检覆盖：27 组合成帧（SNR 6–25 dB × CFO ±150 kHz × 起点偏移 0–40 采样）
全部正确恢复；30 条纯噪声零误检；93 word 错位前缀的流自对齐；
板端 CSI 复相干/CFO 对照；端到端 CLI 全流程。

---

## 六、数据格式速查（与板端/win_listen_iq.py 一致）

```
record = 499 word = 3992 B（iq_len=440, num_eq=0）
w[0]      TSF_iq  (u64, μs)                       ← IQ 块
w[1..440] 440 × IQ 采样,每 word 低32位={I16,Q16}
          高32位 = {gpio_status16, rssi16}
          (gpio: bit7 AGC lock, bit6:0 增益; rssi: 低11位 half-dB,
           b15 demod, b14 tx_rf, b13 fcs_ok; Q 字 b15 = ch_idle)
w[441]    TSF_csi (u64, μs)                       ← CSI 块
w[442]    phase_offset (i64, 低32位有效)          ← 板端 CFO
w[443..498] 56 × CSI {I16,Q16},交换后序 = [-28..-1, +1..+28]
```

同 record 内 `TSF_csi − TSF_iq ≈ 13~15 μs` 是同包强校验。
IQ 块首采样 = 包起点（L-STF 第 0 点），偏差 = 定标常数 C1（±40 采样内）。
