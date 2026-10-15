#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 openwifi 前导码解析器 —— side_ch 联合采集记录 → L-STF / L-LTF / L-SIG
============================================================================

  输入:side_ch 联合采集记录流(win_listen_iq.py 落盘的 iq_*.bin,
       或 csi_iq_display.py 落盘的 csi_iq.txt)。每条记录含:
        [IQ块]  TSF_iq(64b) + 440 × {I16, Q16, gpio_status16, rssi16}
        [CSI块] TSF_csi(64b) + phase_offset(64b) + 56 × CSI{I16, Q16}

  输出:frames.json(逐记录标量参数) + frames.npz(对齐波形/星座) + 控制台汇总,
       之后用 preamble_plot.py 生成图表与 HTML 报告。

  解析流水线(参考 /Users/juicy/Projects/usrp/parse_preamble.py,共 7 步):
    步骤 1: 粗频偏 —— Schmidl-Cox 延迟自相关(lag = 16 采样 = 0.8 μs)
    步骤 2: 粗校正 —— 乘 e^{-j2πΔf·n/fs}
    步骤 3: 精定时 —— 与标准 160 采样 L-LTF 互相关,在 [160±40] 内锁定起点
             (FPGA 已按定标常数 C1 把 IQ 块首采样对齐到 L-STF 第 0 点,
             此处 ±40 采样搜索是对 C1 漂移/重配的容差)
    步骤 4: 精频偏 —— L-LTF 两个 3.2 μs 长符号间相位差(精度 ×4)
    步骤 5: 信道估计 —— H[k] = (LTF1[k]+LTF2[k]) / (2·L[k]), k∈[-26,26]
    步骤 6: L-SIG 解调 —— 零迫均衡 → BPSK → 解交织 → Viterbi(K=7, r=1/2)
    步骤 7: 校验 —— parity / RATE 合法 / reserved=0 / 重编码符合率 ≥ 90%

  本项目特有(参考项目没有的部分):
    · 每 record = 一个已触发的包,无需在全文件里搜索前导码;
    · 与板端 openofdm_rx 的 CSI(56 子载波)做互相验证(csi_coh / csi_mag_corr);
    · 与板端 phase_offset(记录 word 442)报告的 CFO 互相验证;
    · 逐采样 RSSI/AGC/GPIO 状态字解析(展示 AGC 功率爬坡)。

  校验体系(与参考项目一致,防噪声假阳性):
    (1) parity_ok —— L-SIG 第 17 位是前 17 位的偶校验
    (2) rate_ok   —— RATE 是 802.11-2016 表 17-4 的 8 个合法值之一
    (3) reserved=0 —— 保留位发送方固定填 0
    (4) re-encode —— 解码 24 bit 重编码后与接收硬判决符合率 ≥ 90%

  参考标准:
    IEEE Std 802.11-2016 Clause 17 (OFDM PHY): 表 17-6/17-7 (STF/LTF),
    表 17-4 (RATE)、图 17-6 (交织器)、图 17-8 (卷积编码器)。
    Schmidl & Cox (1997) "Robust frequency and timing synchronization for OFDM".

  用法:
    python preamble_parse.py <输入> [--out DIR] [--layout iq_first|csi_first]
                             [--max-records N] [--keep-iq N]

  示例:
    # 解析采集目录(自动找 iq_*.bin)
    python preamble_parse.py C:/iq/iq_20261010_123456

    # 直接解析 bin 流
    python preamble_parse.py C:/iq/iq_20261010_123456/iq_20261010_123456.bin

    # 解析 csi_iq_display.py 落盘的文本 dump
    python preamble_parse.py csi_iq.txt --out ./analysis

    # 只看前 50 条记录
    python preamble_parse.py C:/iq/iq_x --max-records 50
"""

import os, sys, json, time, argparse

# ---- Windows 控制台 UTF-8 修正 ----
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║                        第 0 部分:常量定义                                 ║
# ╚════════════════════════════════════════════════════════════════════════════╝

# ---- 采样率与 OFDM 参数(openwifi AD9361 20 MHz 基带,IQ 块天然 20 MS/s)----
FS = 20e6
N_FFT        = 64    # FFT 长度
N_STF_SHORT  = 16    # 一个短训练符号 = 0.8 μs
N_STF        = 160   # L-STF = 10 × 16 = 8 μs
N_GI2        = 32    # L-LTF 双倍 GI = 1.6 μs
N_LTF        = 160   # L-LTF = 32 + 64 + 64 = 8 μs
N_GI         = 16    # 普通符号 GI
N_SYM        = 80    # 完整 OFDM 符号 = 4 μs
N_PREAMBLE   = N_STF + N_LTF + N_SYM   # 400 采样 = 20 μs
T_SHORT = 0.8e-6     # L-STF 自相关臂长(粗频偏)
T_LONG  = 3.2e-6     # L-LTF 自相关臂长(精频偏)

# ---- record 布局(与 side_ch.h RECORD_LEN / win_listen_iq.py 对齐)----
IQ_LEN       = 440
NUM_EQ       = 0
CSI_LEN      = 56
HEADER_LEN   = 2                 # TSF_csi + phase_offset
CSI_BLK_LEN  = HEADER_LEN + CSI_LEN + NUM_EQ * (CSI_LEN - 4)
REC_WORDS    = (1 + IQ_LEN) + CSI_BLK_LEN      # 499
REC_BYTES    = REC_WORDS * 8                   # 3992
TSF_MAX      = 1 << 48           # TSF 合理上限
TSF_PAIR_GAP_MAX = 50            # 同 record 内 TSF_csi - TSF_iq 上限(μs)
PAIR_WINDOW  = (10, 60)          # 正常配对窗口(μs)
TSF_GAP_LIMIT = 1_000_000        # 相邻 record TSF 差超过 1s 视为分段

# ---- 块顺序(两种布局)----
LAYOUTS = {
    "csi_first": dict(OFF_TSF_CSI=0,   OFF_PHASE=1,   OFF_CSI=2,
                      OFF_TSF_IQ=CSI_BLK_LEN,         OFF_IQ=CSI_BLK_LEN + 1),
    "iq_first":  dict(OFF_TSF_IQ=0,    OFF_IQ=1,
                      OFF_TSF_CSI=1 + IQ_LEN,         OFF_PHASE=2 + IQ_LEN,
                      OFF_CSI=3 + IQ_LEN),
}

# ---- 前导码起点搜索范围(LTF 起点名义 160,±40 容差)----
LTF_SEARCH_LO = 120
LTF_SEARCH_HI = 200            # 含:ltf0 ≤ 200 时 L-SIG 止于 440 内

# ---- L-SIG 校验阈值(与参考项目一致)----
REENCODE_MIN = 0.90


# ╔════════════════════════════════════════════════════════════════════════════╗
# ║           第 1 部分:标准参考序列(802.11-2016 表 17-6 / 17-7)             ║
# ╚════════════════════════════════════════════════════════════════════════════╝

def _stf_freq():
    """L-STF 频域序列 S_{-26..26}(表 17-6)。只在 k=±4,±8,±12,±16,±20,±24 非零,
    使 IFFT 后的时域波形具有 16 采样(0.8 μs)周期。"""
    S = np.zeros(53, dtype=np.complex128)
    signs = {-24: +1, -20: -1, -16: +1, -12: -1, -8: -1, -4: +1,
             +4: -1, +8: -1, +12: +1, +16: +1, +20: +1, +24: +1}
    for k, s in signs.items():
        S[k + 26] = s * (1 + 1j)
    return S * np.sqrt(13.0 / 6.0)


def _ltf_freq():
    """L-LTF 频域序列 L_{-26..26}(表 17-7),取值 ±1,DC 为 0。"""
    return np.array([
        1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1,
        -1, 1, -1, 1, 1, 1, 1,
        0,
        1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1, 1, -1, -1,
        1, -1, 1, -1, 1, 1, 1, 1
    ], dtype=np.complex128)


def _freq_to_time(F53):
    """-26..26 频域 → 64 点 IFFT 栅格(索引 1..26=正频,38..63=负频)→ 时域。"""
    G = np.zeros(N_FFT, dtype=np.complex128)
    for i, k in enumerate(range(-26, 27)):
        G[k % N_FFT] = F53[i]
    return np.fft.ifft(G) * N_FFT


STF_FREQ  = _stf_freq()
LTF_FREQ  = _ltf_freq()
STF_TIME  = _freq_to_time(STF_FREQ)      # 64 采样 = 4 个短符号
LTF_TIME  = _freq_to_time(LTF_FREQ)      # 64 采样 = 1 个长符号

LTF_FULL   = np.concatenate([LTF_TIME[-N_GI2:], LTF_TIME, LTF_TIME])
LTF_FULL_N = LTF_FULL / np.linalg.norm(LTF_FULL)
STF_FULL   = np.tile(STF_TIME[:N_STF_SHORT], 10)
STF_FULL_N = STF_FULL / np.linalg.norm(STF_FULL)

PILOT_K = (-21, -7, 7, 21)
DATA_K  = [k for k in range(-26, 27) if k != 0 and k not in PILOT_K]
assert len(DATA_K) == 48


# ╔════════════════════════════════════════════════════════════════════════════╗
# ║           第 2 部分:卷积码与交织器(802.11-2016 §17.3.5.6)              ║
# ╚════════════════════════════════════════════════════════════════════════════╝

G0, G1 = 0o133, 0o171
K_CC = 7
N_STATES = 1 << (K_CC - 1)


def _parity(v):
    v ^= v >> 4
    v ^= v >> 2
    v ^= v >> 1
    return v & 1


def _build_trellis():
    nxt = np.zeros((N_STATES, 2), dtype=np.int32)
    out = np.zeros((N_STATES, 2, 2), dtype=np.int8)
    for s in range(N_STATES):
        for b in (0, 1):
            reg = (b << (K_CC - 1)) | s
            out[s, b, 0] = _parity(reg & G0)
            out[s, b, 1] = _parity(reg & G1)
            nxt[s, b] = reg >> 1
    return nxt, out


TRELLIS_NEXT, TRELLIS_OUT = _build_trellis()


def cc_encode(bits):
    """码率 1/2 卷积编码,24 bit → 48 bit(重编码校验的编码端)。"""
    coded = np.zeros(2 * len(bits), dtype=np.int8)
    state = 0
    for t, b in enumerate(bits):
        reg = (int(b) << (K_CC - 1)) | state
        coded[2 * t]     = _parity(reg & G0)
        coded[2 * t + 1] = _parity(reg & G1)
        state = reg >> 1
    return coded


def viterbi_decode(soft, n_out, terminated=True):
    """软判决 Viterbi 译码(码率 1/2, K=7)。编码 0 → 发射 -1,1 → 发射 +1。"""
    NEG = -1e18
    metric = np.full(N_STATES, NEG)
    metric[0] = 0.0
    path = np.zeros((n_out, N_STATES), dtype=np.int8)
    prev = np.zeros((n_out, N_STATES), dtype=np.int32)

    for t in range(n_out):
        r0, r1 = soft[2 * t], soft[2 * t + 1]
        newm = np.full(N_STATES, NEG)
        for s in range(N_STATES):
            m = metric[s]
            if m <= NEG / 2:
                continue
            for b in (0, 1):
                o0, o1 = TRELLIS_OUT[s, b]
                cand = m + (-r0 if o0 == 0 else r0) + (-r1 if o1 == 0 else r1)
                ns = TRELLIS_NEXT[s, b]
                if cand > newm[ns]:
                    newm[ns] = cand
                    path[t, ns] = b
                    prev[t, ns] = s
        metric = newm

    end = 0 if terminated else int(np.argmax(metric))
    bits = np.zeros(n_out, dtype=np.int8)
    s = end
    for t in range(n_out - 1, -1, -1):
        bits[t] = path[t, s]
        s = prev[t, s]
    return bits, float(metric[end])


def deinterleave_bpsk48(sym_bits):
    """分组交织器逆变换(N_CBPS=48, N_BPSC=1):out[k] = in[3(k%16)+k//16]。"""
    out = np.empty(48, dtype=sym_bits.dtype)
    for k in range(48):
        out[k] = sym_bits[3 * (k % 16) + k // 16]
    return out


# ---- RATE 字段速查表(802.11-2016 表 17-4)----
RATE_TABLE = {
    (1, 1, 0, 1): (6,  'BPSK',    '1/2'),
    (1, 1, 1, 1): (9,  'BPSK',    '3/4'),
    (0, 1, 0, 1): (12, 'QPSK',    '1/2'),
    (0, 1, 1, 1): (18, 'QPSK',    '3/4'),
    (1, 0, 0, 1): (24, '16-QAM',  '1/2'),
    (1, 0, 1, 1): (36, '16-QAM',  '3/4'),
    (0, 0, 0, 1): (48, '64-QAM',  '2/3'),
    (0, 0, 1, 1): (54, '64-QAM',  '3/4'),
}


# ╔════════════════════════════════════════════════════════════════════════════╗
# ║         第 3 部分:record 流读取 —— 自对齐(移植 win_listen_iq.py)           ║
# ╚════════════════════════════════════════════════════════════════════════════╝

def load_u64_stream(path):
    """把 .bin(UDP 裸流)或 .txt(csi_iq.txt,u16 文本)统一成 uint64 数组。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.txt':
        a = np.loadtxt(path, dtype=np.uint16)
        if a.size % 4:
            a = a[:a.size - (a.size % 4)]
        return np.frombuffer(a.tobytes(), dtype='<u8')
    with open(path, 'rb') as fh:
        data = fh.read()
    if len(data) % 8:
        data = data[:len(data) - (len(data) % 8)]
    return np.frombuffer(data, dtype='<u8')


def s16(v):
    """uint16 → int16。"""
    v = int(v)
    return v - 0x10000 if v >= 0x8000 else v


def find_alignment(u64, layout):
    """扫描 s∈[0,REC_WORDS),返回使 TSF 单调且同 record 配对的 record 起点。
    移植自 win_listen_iq.py,并把"要求 64 条连续单调"改为"连续 need=4 条即锁定、
    遇损坏即停"——win_listen 是增量解析(锁定时缓冲里还没有流中部的损坏),
    本工具一次性加载整段流,若要求 64 条连续单调会被流中部损坏区打死,
    导致整段 0 解析;锁定后由 iter_records 的前进解析 + 重同步跨过损坏区。"""
    L = layout
    n = len(u64)
    need = 4
    if n < (need + 1) * REC_WORDS:
        return None
    other_off = L["OFF_TSF_CSI"] if L["OFF_TSF_IQ"] == 0 else L["OFF_TSF_IQ"]

    def ok_rec(s, k):
        """第 k 条 record 的 TSF 合理性 + 同包配对。返回配对差(μs)或 None。"""
        b = s + k * REC_WORDS
        if b + REC_WORDS > n:
            return None
        vi = int(u64[b + L["OFF_TSF_IQ"]])
        vc = int(u64[b + L["OFF_TSF_CSI"]])
        if vi >= TSF_MAX or vc >= TSF_MAX:
            return None
        d = vc - vi
        return d if 0 <= d <= TSF_PAIR_GAP_MAX else None

    for s in range(REC_WORDS):
        kmax = (n - s) // REC_WORDS
        if kmax < need:
            break
        if ok_rec(s, 0) is None:
            continue
        n_good = 1
        prev_i = int(u64[s + L["OFF_TSF_IQ"]])
        for k in range(1, kmax):
            if ok_rec(s, k) is None:
                break
            vi = int(u64[s + k * REC_WORDS + L["OFF_TSF_IQ"]])
            if vi < prev_i:
                break
            prev_i = vi
            n_good += 1
            if n_good >= need:
                return (s, n_good)
    return None


def record_of(u64, base, layout):
    """解析 base 处一条 record,返回 dict(含 IQ/CSI/状态数组)或 None。
    同 record 内 TSF_csi − TSF_iq ∈ [0,50] 是同包强校验(实测差 13~15 μs)。"""
    L = layout
    if base + REC_WORDS > len(u64):
        return None
    tsf_csi = int(u64[base + L["OFF_TSF_CSI"]])
    tsf_iq  = int(u64[base + L["OFF_TSF_IQ"]])
    if tsf_csi >= TSF_MAX or tsf_iq >= TSF_MAX:
        return None
    if not (0 <= tsf_csi - tsf_iq <= TSF_PAIR_GAP_MAX):
        return None

    phase = np.frombuffer(np.array([u64[base + L["OFF_PHASE"]]],
                                   dtype='<u8').tobytes(), dtype='<i8')[0]
    phase = int(phase)

    # ---- IQ 块:每 word 低 32 位 = {I16, Q16, gpio_status16, rssi16} ----
    b = np.empty(4 * IQ_LEN, dtype=np.uint16)
    for i in range(IQ_LEN):
        w = int(u64[base + L["OFF_IQ"] + i])
        b[4 * i]     = w & 0xFFFF
        b[4 * i + 1] = (w >> 16) & 0xFFFF
        b[4 * i + 2] = (w >> 32) & 0xFFFF
        b[4 * i + 3] = (w >> 48) & 0xFFFF
    iq   = np.array([s16(v) for v in b[0::4]], dtype=np.float64) \
         + 1j * np.array([s16(v) for v in b[1::4]], dtype=np.float64)
    gpio = b[2::4].astype(np.int64)
    rssiw = b[3::4].astype(np.int64)
    gain      = gpio & 0x7F
    agc_lock  = (gpio >> 7) & 1
    ch_idle   = (b[1::4].astype(np.int64) >> 15) & 1
    rssi_half_db = rssiw & 0x7FF
    demod     = (rssiw >> 15) & 1
    tx_rf     = (rssiw >> 14) & 1
    fcs_ok    = (rssiw >> 13) & 1

    # ---- CSI 块:56 × {I16, Q16},按 side_info_display.py 规则交换前后半段 ----
    csi = np.zeros(CSI_LEN, dtype=np.complex128)
    for i in range(CSI_LEN):
        w = int(u64[base + L["OFF_CSI"] + i])
        csi[i] = s16(w & 0xFFFF) + 1j * s16((w >> 16) & 0xFFFF)
    tmp = csi.copy()
    csi[0:CSI_LEN // 2] = tmp[CSI_LEN // 2:CSI_LEN]
    csi[CSI_LEN // 2:]  = tmp[0:CSI_LEN // 2]
    # 顺序即 [-28..-1, +1..+28](物理频率升序,跳过 DC)

    # ---- 板端报告 CFO(官方换算:freq = 20e6·int16(低16位)/512/(2π))----
    cfo_board_hz = FS * s16(phase & 0xFFFF) / 512.0 / (2 * np.pi)

    return {"tsf_csi": tsf_csi, "tsf_iq": tsf_iq, "phase": phase,
            "phase16": s16(phase & 0xFFFF), "cfo_board_hz": cfo_board_hz,
            "iq": iq, "csi": csi,
            "gain": gain, "agc_lock": agc_lock, "ch_idle": ch_idle,
            "rssi_half_db": rssi_half_db,
            "demod": demod, "tx_rf": tx_rf, "fcs_ok": fcs_ok, "base": base}


def iter_records(u64, layout, max_records=None):
    """整段流的 record 生成器:首锁对齐 → 逐条产出 → TSF 异常时重同步。"""
    L = layout
    r = find_alignment(u64, L)
    if r is None:
        return
    base = r[0]
    rec_idx, seg, last_tsf = 0, 1, None
    n = len(u64)
    while base + REC_WORDS <= n:
        if max_records is not None and rec_idx >= max_records:
            return
        rec = record_of(u64, base, L)
        if rec is None:
            if base + 5 * REC_WORDS > n:
                return
            sub = find_alignment(u64[base:], L)
            if sub is not None and sub[0] > 0:
                base += sub[0]
                seg += 1
                continue
            base += REC_WORDS
            continue
        if last_tsf is not None:
            d = rec["tsf_csi"] - last_tsf
            if d < 0 or d > TSF_GAP_LIMIT:
                seg += 1
        last_tsf = rec["tsf_csi"]
        yield rec_idx, seg, rec
        rec_idx += 1
        base += REC_WORDS


# ╔════════════════════════════════════════════════════════════════════════════╗
# ║            第 4 部分:单 record 精细处理(移植参考项目 process_frame)        ║
# ╚════════════════════════════════════════════════════════════════════════════╝

def schmidl_cox(x, lag, window):
    """归一化延迟自相关(Schmidl-Cox 检测统计量)。
    返回 (M, Pfull):M = |P|/√(E1·E2) ∈ [0,1],Pfull 的相角给出 CFO。"""
    N = len(x)
    M = np.zeros(N, dtype=np.float64)
    Pfull = np.zeros(N, dtype=np.complex128)
    if N <= lag + window:
        return M, Pfull
    xc = x.astype(np.complex128)
    prod = xc[lag:] * np.conj(xc[:-lag])
    e1 = np.abs(xc[lag:]) ** 2
    e2 = np.abs(xc[:-lag]) ** 2

    def rsum(v):
        c = np.concatenate(([0.0 + 0j] if np.iscomplexobj(v) else [0.0],
                            np.cumsum(v)))
        return c[window:] - c[:-window]

    P  = rsum(prod)
    R1 = rsum(e1)
    R2 = rsum(e2)
    Mv = np.abs(P) / np.sqrt(np.maximum(R1 * R2, 1e-24))
    off = lag + window - 1
    nn = min(len(Mv), N - off)
    M[off:off + nn] = Mv[:nn]
    Pfull[off:off + nn] = P[:nn]
    return M, Pfull


def apply_cfo(x, f_hz, fs=FS):
    """乘 e^{-j2πΔf·n/fs} 反向旋转,校正频偏。"""
    n = np.arange(len(x))
    return x * np.exp(-2j * np.pi * f_hz * n / fs)


def template_match_score(x, pos, nseg=8):
    """在 pos 处对 320 采样 L-STF+L-LTF 模板做分段归一化匹配(0~1)。
    移植自参考项目 preamble_matched_filter_at(单点版)。"""
    tmpl = np.concatenate([STF_FULL, LTF_FULL]).astype(np.complex128)
    n = len(tmpl)
    seg = n // nseg
    segs = [tmpl[i * seg:(i + 1) * seg] for i in range(nseg)]
    segs = [t / np.linalg.norm(t) for t in segs]
    if pos < 0 or pos + n > len(x):
        return 0.0
    acc = 0.0
    for i, t in enumerate(segs):
        w = x[pos + i * seg: pos + (i + 1) * seg]
        d = np.linalg.norm(w)
        if d > 1e-12:
            acc += abs(np.vdot(t, w)) / d
    return acc / nseg


def process_record(rec, keep_iq=False):
    """对一条 record 的 440 采样 IQ 块做完整 L-STF/L-LTF/L-SIG 解析。
    返回 dict,键 'valid' 表示是否通过全部验收条件。
    keep_iq=True 时结果里附带原始 IQ/CSI(供 npz 与 --show 使用)。"""
    res = {'valid': False, 'reason': ''}
    if keep_iq:
        res['iq']  = rec['iq'].astype(np.complex64)
        res['rssi'] = rec['rssi_half_db'].astype(np.uint16)
        res['gain'] = rec['gain'].astype(np.uint8)
    res['csi'] = rec['csi'].astype(np.complex64)   # 56 采样,体积小,始终保留
    x = rec['iq'].astype(np.complex128)
    if len(x) < N_PREAMBLE:
        res['reason'] = 'IQ 块长度不足 400 采样'
        return res

    # ---- 去直流(AD9361 DC 偏移只影响 k=0,顺带让能量计算更干净)----
    x = x - x.mean()

    # =====================================================================
    # 步骤 1: 粗频偏 —— L-STF 0.8 μs 重复的 Schmidl-Cox 自相关
    # STF 名义起点 = 采样 0(C1 对齐),plateau 在 [64, 180] 内
    # =====================================================================
    M, P = schmidl_cox(x, N_STF_SHORT, 48)
    seg = M[64:180]
    if seg.size and seg.max() < 0.30:
        res['reason'] = '无 L-STF 周期结构(M 峰值过低)'
        res['stf_M'] = float(seg.max())
        return res
    hi = int(np.argmax(seg)) + 64
    plateau = (M[64:180] > 0.8 * float(M[hi])).nonzero()[0] + 64
    if plateau.size == 0:
        plateau = np.array([hi])
    cfo_coarse = float(np.angle(np.mean(P[plateau])) / (2 * np.pi * T_SHORT))
    res['stf_M'] = float(M[hi])
    res['cfo_coarse_hz'] = cfo_coarse

    # =====================================================================
    # 步骤 2-3: 粗校正 + 与标准 160 采样 L-LTF 互相关精定时
    # =====================================================================
    xc = apply_cfo(x, cfo_coarse)
    corr = np.correlate(xc.astype(np.complex128), LTF_FULL_N, mode='valid')
    e = np.sqrt(np.convolve(np.abs(xc.astype(np.complex128)) ** 2,
                            np.ones(N_LTF), mode='valid'))
    cn = np.abs(corr) / np.maximum(e, 1e-12)
    lo = max(LTF_SEARCH_LO, 0)
    hi = min(LTF_SEARCH_HI, len(cn) - 1)
    if hi - lo < 10:
        res['reason'] = 'LTF 搜索空间不足'
        return res
    jbest = lo + int(np.argmax(cn[lo:hi + 1]))
    res['ltf_corr'] = float(cn[jbest])
    ltf0 = jbest                      # LTF 起点(GI2 起点)
    ltf1 = ltf0 + N_GI2               # 第一个长符号
    ltf2 = ltf1 + N_FFT               # 第二个长符号
    stf_start = ltf0 - N_STF
    res['ltf0_rel'] = int(ltf0)
    res['stf_start_rel'] = int(stf_start)

    if ltf2 + N_FFT + N_SYM > len(xc):
        res['reason'] = '前导码超出 IQ 窗口边界'
        return res

    # =====================================================================
    # 步骤 4: 精频偏 —— L-LTF 两个 3.2 μs 长符号间相位差(精度 ×4)
    # =====================================================================
    a = xc[ltf1:ltf1 + N_FFT]
    b = xc[ltf2:ltf2 + N_FFT]
    pl = np.vdot(a, b)
    cfo_fine = float(np.angle(pl) / (2 * np.pi * T_LONG))
    res['cfo_fine_hz'] = cfo_fine
    res['cfo_total_hz'] = cfo_coarse + cfo_fine
    res['ltf_repeat_corr'] = float(np.abs(pl) /
                                   max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12))
    n = np.arange(len(xc)) - ltf1
    xf = xc * np.exp(-2j * np.pi * cfo_fine * n / FS)

    # =====================================================================
    # 步骤 5: 信道估计 —— H[k] = (LTF1+LTF2)/(2·L[k]);SNR 由两符号差估
    # =====================================================================
    A = np.fft.fft(xf[ltf1:ltf1 + N_FFT])
    B = np.fft.fft(xf[ltf2:ltf2 + N_FFT])
    Lavg = 0.5 * (A + B)
    H = np.zeros(N_FFT, dtype=np.complex128)
    for i, k in enumerate(range(-26, 27)):
        if LTF_FREQ[i] != 0:
            H[k % N_FFT] = Lavg[k % N_FFT] / LTF_FREQ[i]
    in_band = [k % N_FFT for k in range(-26, 27) if k != 0]
    sig_p = float(np.mean(np.abs(Lavg[in_band]) ** 2))
    noi_p = float(np.mean(np.abs(0.5 * (A[in_band] - B[in_band])) ** 2))
    res['snr_db'] = 10 * np.log10(sig_p / max(noi_p, 1e-12))
    hm = np.abs(H[in_band])
    res['H_mag_mean'] = float(np.mean(hm))
    res['H_ripple_db'] = float(20 * np.log10(hm.max() / max(hm.min(), 1e-12)))

    # =====================================================================
    # 步骤 6: L-SIG 解调 —— BPSK 均衡 + 解交织 + Viterbi 译码
    # =====================================================================
    sig_start = ltf2 + N_FFT + N_GI
    Y = np.fft.fft(xf[sig_start:sig_start + N_FFT])

    pil = np.array([Y[k % N_FFT] / H[k % N_FFT] for k in PILOT_K])
    pil_ref = np.array([1, 1, 1, -1], dtype=np.complex128)
    pcorr = np.vdot(pil_ref, pil)
    phi = np.angle(pcorr)
    res['pilot_corr'] = float(np.abs(pcorr) / max(np.linalg.norm(pil), 1e-12) / 2.0)

    eq = np.array([Y[k % N_FFT] / H[k % N_FFT] for k in DATA_K])
    eq = eq * np.exp(-1j * phi)
    res['sig_eq'] = eq

    soft48 = np.real(eq)
    scale = np.mean(np.abs(soft48)) + 1e-12
    soft48 = soft48 / scale
    res['sig_evm_db'] = float(
        10 * np.log10(np.mean((np.abs(eq) / (np.mean(np.abs(eq)) + 1e-12) - 1) ** 2
                              + (np.imag(eq) / (np.mean(np.abs(eq)) + 1e-12)) ** 2)
                      + 1e-12))

    coded = deinterleave_bpsk48(soft48)
    bits, metric = viterbi_decode(coded, 24, terminated=True)
    res['viterbi_metric'] = metric / 48.0

    # ---- 提取 SIGNAL 字段(802.11-2016 图 17-1)----
    rate_bits = tuple(int(b) for b in bits[0:4])
    reserved  = int(bits[4])
    length    = int(sum(int(bits[5 + i]) << i for i in range(12)))
    parity    = int(bits[17])
    tail      = [int(b) for b in bits[18:24]]

    parity_ok = (sum(int(b) for b in bits[0:17]) % 2) == parity
    tail_ok   = all(t == 0 for t in tail)
    rate_ok   = rate_bits in RATE_TABLE

    # ---- 重编码校验:解码 24 bit 重编码后与接收硬判决逐位比对 ----
    reenc = cc_encode(bits)
    recv_hard = (coded > 0).astype(np.int8)
    agree = float(np.mean(reenc == recv_hard))
    res['reencode_agree'] = agree

    res.update({
        'rate_bits' : ''.join(str(b) for b in rate_bits),
        'reserved'  : reserved,
        'length'    : length,
        'parity_bit': parity,
        'parity_ok' : parity_ok,
        'tail_ok'   : tail_ok,
        'rate_ok'   : rate_ok,
        'sig_bits'  : ''.join(str(int(b)) for b in bits),
    })
    if rate_ok:
        mbps, mod, cr = RATE_TABLE[rate_bits]
        res['rate_mbps']   = mbps
        res['modulation']  = mod
        res['code_rate']   = cr
        ndbps = int(mbps * 4)
        nsym  = int(np.ceil((16 + 8 * length + 6) / ndbps))
        res['n_data_symbols'] = nsym
        res['ppdu_us'] = 20 + 4 * nsym
    else:
        res['rate_mbps'] = None
        res['modulation'] = None
        res['code_rate'] = None
        res['n_data_symbols'] = None
        res['ppdu_us'] = None

    # ---- 最终判定 ----
    res['valid'] = bool(parity_ok and rate_ok and reserved == 0
                        and agree >= REENCODE_MIN)
    if not res['valid']:
        parts = []
        if not rate_ok:           parts.append('RATE 值非法')
        if not parity_ok:         parts.append('奇偶校验失败')
        if reserved != 0:         parts.append(f'保留位={reserved}(非零)')
        if agree < REENCODE_MIN:  parts.append(
            f'重编码符合率 {agree*100:.0f}%<{REENCODE_MIN*100:.0f}%')
        res['reason'] = '; '.join(parts)

    # ---- 模板匹配分 + STF 序列身份 ----
    res['mf_score'] = template_match_score(xf, stf_start)
    if 0 <= stf_start and stf_start + N_STF <= len(xf):
        w = xf[stf_start:stf_start + N_STF]
        d = np.linalg.norm(w) * np.linalg.norm(STF_FULL)
        res['stf_corr'] = float(abs(np.vdot(STF_FULL, w)) / d) if d > 0 else 0.0
    else:
        res['stf_corr'] = 0.0

    # ---- 保存对齐后的前导码波形 ----
    if 0 <= stf_start and stf_start + N_PREAMBLE <= len(xf):
        res['preamble'] = xf[stf_start:stf_start + N_PREAMBLE].copy()

    # =====================================================================
    # 本项目特有:与板端 CSI / phase_offset 互相验证
    # =====================================================================
    csi = rec.get('csi')
    if csi is not None and len(csi) == CSI_LEN:
        csi_by_k = {}
        for i, k in enumerate(range(-28, 0)):
            csi_by_k[k] = csi[i]
        for i, k in enumerate(range(1, 29)):
            csi_by_k[k] = csi[CSI_LEN // 2 + i]
        ks = [k for k in range(-26, 27) if k != 0]
        Hv = np.array([H[k % N_FFT] for k in ks])
        Cv = np.array([csi_by_k[k] for k in ks])
        den = np.linalg.norm(Hv) * np.linalg.norm(Cv)
        res['csi_coh'] = float(abs(np.vdot(Hv, Cv)) / den) if den > 1e-12 else 0.0
        if den > 1e-12 and float(np.std(np.abs(Cv))) > 0:
            hdb = 20 * np.log10(np.abs(Hv) + 1e-12)
            cdb = 20 * np.log10(np.abs(Cv) + 1e-12)
            hdb = hdb - hdb.mean(); cdb = cdb - cdb.mean()
            res['csi_mag_corr'] = float(np.corrcoef(hdb, cdb)[0, 1])
            res['csi_scale_db'] = float(np.median(cdb - hdb))
        else:
            res['csi_mag_corr'] = None
            res['csi_scale_db'] = None
    else:
        res['csi_coh'] = None
        res['csi_mag_corr'] = None
        res['csi_scale_db'] = None

    return res


# ╔════════════════════════════════════════════════════════════════════════════╗
# ║                      第 5 部分:主函数                                      ║
# ╚════════════════════════════════════════════════════════════════════════════╝

def _json_default(o):
    if isinstance(o, np.floating): return float(o)
    if isinstance(o, np.integer):  return int(o)
    if isinstance(o, np.bool_):    return bool(o)
    return str(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='输入:iq_*.bin / csi_iq.txt / 采集目录(自动找 .bin)')
    ap.add_argument('--layout', default='iq_first',
                    choices=sorted(LAYOUTS), help='record 块顺序(默认 iq_first)')
    ap.add_argument('--out', default=None,
                    help='输出目录(默认:<输入>旁新建 preamble_analysis/)')
    ap.add_argument('--max-records', type=int, default=0,
                    help='最多解析 N 条 record(0=全部)')
    ap.add_argument('--keep-iq', type=int, default=500,
                    help='frames.npz 里保留原始 440 采样 IQ 的前 N 条(供 --show)')
    args = ap.parse_args()

    # ---- 解析输入路径(目录 → 自动找 .bin)----
    in_path = args.input
    if os.path.isdir(in_path):
        bins = sorted(f for f in os.listdir(in_path) if f.endswith('.bin'))
        if not bins:
            raise SystemExit(f'目录 {in_path} 内没有 .bin 文件')
        in_path = os.path.join(in_path, bins[0])
        print(f'[in] 自动选择 {in_path}')
    if not os.path.exists(in_path):
        raise SystemExit(f'输入不存在: {in_path}')

    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.abspath(in_path)), 'preamble_analysis')
    os.makedirs(out_dir, exist_ok=True)
    layout = LAYOUTS[args.layout]

    print('=' * 68)
    print('  openwifi 前导码解析器 —— L-STF / L-LTF / L-SIG')
    print('=' * 68)
    print(f'  输入          : {in_path}')
    print(f'  record 布局   : {args.layout}   {REC_WORDS} word = {REC_BYTES} B/条')
    print(f'  IQ 块         : {IQ_LEN} 采样 @ 20 MS/s(前导码 400 采样 + 40 余量)')
    print(f'  重编码阈值    : {REENCODE_MIN*100:.0f}%')
    print(f'  输出目录      : {out_dir}')

    t0 = time.time()
    u64 = load_u64_stream(in_path)
    print(f'\n[1/2] 流长度 {len(u64)} word,开始解析...')

    frames = []
    n_aligned = 0
    for rec_idx, seg, rec in iter_records(u64, layout,
                                          args.max_records or None):
        r = process_record(rec, keep_iq=(rec_idx < args.keep_iq))
        r.update({
            'rec_idx'   : rec_idx,
            'seg'       : seg,
            'tsf_iq'    : rec['tsf_iq'],
            'tsf_csi'   : rec['tsf_csi'],
            'tsf_gap_us': rec['tsf_csi'] - rec['tsf_iq'],
            'phase'     : rec['phase'],
            'phase16'   : rec['phase16'],
            'cfo_board_hz': rec['cfo_board_hz'],
            'rssi_mean_half_db': float(np.mean(rec['rssi_half_db'])),
            'rssi_std_half_db' : float(np.std(rec['rssi_half_db'])),
            'gain_final': int(rec['gain'][-1]),
            'agc_lock_final': int(rec['agc_lock'][-1]),
            'demod': int(rec['demod'][-1]),
            'tx_rf': int(rec['tx_rf'][-1]),
            'fcs_ok': int(rec['fcs_ok'][-1]),
            'ch_idle': int(rec['ch_idle'][-1]),
            'iq_power_dbfs': float(20 * np.log10(
                np.sqrt(np.mean(np.abs(rec['iq']) ** 2)) / 32768.0 + 1e-12)),
        })
        frames.append(r)
        n_aligned += 1
        if rec_idx % 50 == 0 and rec_idx > 0:
            print(f'    ... {rec_idx} 条')

    n_conf = sum(1 for f in frames if f.get('ltf_corr', 0) >= 0.35)
    n_val  = sum(1 for f in frames if f['valid'])
    print(f'\n    解析完成,{time.time()-t0:.1f}s,共 {n_aligned} 条 record')
    print(f'      L-LTF 参考序列确认前导码 : {n_conf}')
    print(f'      L-SIG 完整解出           : {n_val}')

    # ---- 写入 frames.npz ----
    pre_v = np.array([f['preamble'] for f in frames
                      if f['valid'] and 'preamble' in f], dtype=np.complex64)
    pre_c = np.array([f['preamble'] for f in frames
                      if not f['valid'] and f.get('ltf_corr', 0) >= 0.35
                      and 'preamble' in f], dtype=np.complex64)
    sq_v = np.array([f['sig_eq'] for f in frames if f['valid']],
                    dtype=np.complex64)
    csi_v = np.array([f.get('csi', np.zeros(CSI_LEN)) for f in frames
                      if f['valid']], dtype=np.complex64)
    k_iq = min(args.keep_iq, len(frames))
    rec_iq = np.zeros((k_iq, IQ_LEN), dtype=np.complex64)
    rec_rssi_arr = np.zeros((k_iq, IQ_LEN), dtype=np.uint16)
    rec_gain_arr = np.zeros((k_iq, IQ_LEN), dtype=np.uint8)
    rec_tsf   = np.zeros(len(frames), dtype=np.float64)
    rec_power = np.zeros(len(frames), dtype=np.float64)
    rec_rssi  = np.zeros(len(frames), dtype=np.float64)
    for i, f in enumerate(frames):
        rec_tsf[i]   = f['tsf_iq']
        rec_power[i] = f['iq_power_dbfs']
        rec_rssi[i]  = f['rssi_mean_half_db']
        if i < k_iq:
            rec_iq[i]      = frames[i].get('iq', np.zeros(IQ_LEN))
            rec_rssi_arr[i] = frames[i].get('rssi', np.zeros(IQ_LEN, dtype=np.uint16))
            rec_gain_arr[i] = frames[i].get('gain', np.zeros(IQ_LEN, dtype=np.uint8))
    np.savez_compressed(
        os.path.join(out_dir, 'frames.npz'),
        preambles=pre_v, preambles_conf=pre_c, sig_eq=sq_v, csi_valid=csi_v,
        rec_iq=rec_iq, rec_rssi_arr=rec_rssi_arr, rec_gain_arr=rec_gain_arr,
        rec_tsf=rec_tsf, rec_power=rec_power, rec_rssi=rec_rssi,
    )

    # ---- 写入 frames.json(剔除大数组)----
    meta = []
    for f in frames:
        d = {k: v for k, v in f.items()
             if k not in ('preamble', 'sig_eq', 'iq', 'csi')
             and not isinstance(v, np.ndarray)}
        meta.append(d)
    summary = {
        'input'        : os.path.basename(in_path),
        'layout'       : args.layout,
        'iq_len'       : IQ_LEN,
        'fs'           : FS,
        'total_records': n_aligned,
        'ltf_confirmed': n_conf,
        'lsig_decoded' : n_val,
        'reencode_min' : REENCODE_MIN,
        'frames'       : meta,
    }
    with open(os.path.join(out_dir, 'frames.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False, default=_json_default)
    print(f'    frames.json + frames.npz → {out_dir}')

    print('\n[2/2] 运行  python preamble_plot.py --dir ' + out_dir +
          '  生成图表和 HTML 报告。')


if __name__ == '__main__':
    main()
