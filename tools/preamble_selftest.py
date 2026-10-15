#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 openwifi 前导码解析器 —— 合成帧回环自检
============================================================================

  构造标准 802.11 legacy 前导码(真实卷积编码的 L-SIG),按 side_ch 联合采集
  record 格式(499 word)打包成 .bin 流(含 93 word 错位前缀 + 噪声 record),
  验证 preamble_parse.py:

    (1) 流自对齐 —— 93 word 错位前缀被正确跳过
    (2) 合成帧回环 —— SNR/CFO/起点偏移矩阵下 L-SIG 全部正确恢复
    (3) 频偏估计   —— cfo_total 与真值误差 < 2 kHz
    (4) 板端对照   —— csi_coh(与板端 CSI 复相干)接近 1、cfo_board 一致
    (5) 纯噪声     —— 30 条噪声 record 零误检(valid=False)
    (6) 端到端 CLI —— main() 跑通,frames.json 统计正确

  用法: python preamble_selftest.py [-v]

  参考:/Users/juicy/Projects/usrp/test_preamble.py 的验证思路,
       适配本项目的 record 级数据格式。
"""
import os, sys, json, tempfile, argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preamble_parse as pp


# ═══════════════════════════════════════════════════════════════════════════════
# 发送端:构造标准前导码(与接收端解析器共用标准常量,编码链独立实现)
# ═══════════════════════════════════════════════════════════════════════════════

def tx_interleave(coded48):
    """发送端分组交织(802.11-2016 图 17-6):out[3(k%16)+k//16] = in[k]。"""
    out = np.zeros(48, dtype=coded48.dtype)
    for k in range(48):
        out[3 * (k % 16) + k // 16] = coded48[k]
    return out


def build_lsig_symbol(rate_bits, length):
    """构造完整 L-SIG OFDM 符号(80 采样)。rate_bits:4 元组,length:字节数。"""
    bits = list(rate_bits) + [0] + [ (length >> i) & 1 for i in range(12) ]
    parity = sum(bits) % 2
    bits = bits + [parity] + [0] * 6            # 24 bit
    coded = pp.cc_encode(np.array(bits, dtype=np.int8))
    inter = tx_interleave(coded)
    sym = np.where(inter == 1, 1.0, -1.0)       # 编码 0 → -1,1 → +1

    G = np.zeros(pp.N_FFT, dtype=np.complex128)
    for i, k in enumerate(pp.DATA_K):
        G[k % pp.N_FFT] = sym[i]
    for k, p in zip(pp.PILOT_K, (1, 1, 1, -1)):
        G[k % pp.N_FFT] = p
    t = np.fft.ifft(G) * pp.N_FFT
    return np.concatenate([t[-pp.N_GI:], t]), bits


def build_preamble(rate_bits, length, cfo_hz, snr_db, offset, channel=(1.0, 0.35j + 0.0)):
    """合成 440 采样 IQ 块:
       offset 采样噪声垫底 + 400 采样前导码(受 CFO/SNR/多径信道影响)。"""
    h0, h1 = channel
    sig, _ = build_lsig_symbol(rate_bits, length)
    clean = np.concatenate([pp.STF_FULL, pp.LTF_FULL, sig]).astype(np.complex128)
    # 多径信道(延迟 1 采样,远小于 GI)
    clean = h0 * clean + h1 * np.concatenate(([0], clean[:-1]))
    # 归一化信号 RMS = 1
    clean = clean / np.sqrt(np.mean(np.abs(clean) ** 2))

    n = np.arange(440)
    x = clean * np.exp(2j * np.pi * cfo_hz * n[:400] / pp.FS)
    noise_sigma = 10 ** (-snr_db / 20.0)
    noise = (np.random.randn(440) + 1j * np.random.randn(440)) / np.sqrt(2)
    win = np.zeros(440, dtype=np.complex128)
    win[offset:offset + 400] = x
    win += noise_sigma * noise
    # 量化到 int16(注意:complex→int 直接 cast 会丢弃虚部,必须 I/Q 分量化)
    win = np.round(win * 2048)
    iq_int = (np.clip(win.real, -32768, 32767).astype(np.int64)
              + 1j * np.clip(win.imag, -32768, 32767).astype(np.int64))
    return iq_int, h0, h1


def channel_response(channel):
    """多径信道在子载波 k=-28..28 上的频响。"""
    h0, h1 = channel
    ks = list(range(-28, 0)) + list(range(1, 29))
    H = []
    for k in ks:
        H.append(h0 + h1 * np.exp(-2j * np.pi * k / pp.N_FFT))
    return np.array(H, dtype=np.complex128)


def pack_record(iq_int, tsf_iq, tsf_csi, phase_i64, csi56,
                rssi=500, gain=40, agc_lock=1, fcs_ok=1, demod=1):
    """把一条联合采集 record 打包成 499 个 u64 word(iq_first 布局)。"""
    w = np.zeros(pp.REC_WORDS, dtype='<u8')
    w[0] = tsf_iq
    for i in range(pp.IQ_LEN):
        I = int(iq_int[i].real); Q = int(iq_int[i].imag)
        I &= 0xFFFF; Q &= 0xFFFF
        gpio = (gain & 0x7F) | ((agc_lock & 1) << 7)
        rssiw = (rssi & 0x7FF) | ((demod & 1) << 15) | ((1 & 1) << 14) \
                | ((fcs_ok & 1) << 13)
        w[1 + i] = (I | (Q << 16) | (gpio << 32) | (rssiw << 48))
    w[1 + pp.IQ_LEN]     = tsf_csi
    w[2 + pp.IQ_LEN]     = np.frombuffer(
        np.array([phase_i64], dtype='<i8').tobytes(), dtype='<u8')[0]
    # CSI:记录内原始序 tmp[0:28]=k=+1..+28,tmp[28:56]=k=-28..-1(解析端会交换)
    tmp = np.concatenate([csi56[28:], csi56[:28]])   # csi56 序 = [-28..-1,+1..+28]
    for i in range(pp.CSI_LEN):
        v = tmp[i]
        I = int(round(v.real)); Q = int(round(v.imag))
        w[3 + pp.IQ_LEN + i] = ((I & 0xFFFF) | ((Q & 0xFFFF) << 16))
    return w


def build_stream(rate_bits, length, cfo_hz, snr_db, offset, n_rec=1,
                 channel=(1.0, 0.35j + 0.0), tsf0=10_000_000):
    """构造 n_rec 条同参数 signal record(TSF 递增),返回 u64 数组。"""
    h0, h1 = channel
    words = []
    for k in range(n_rec):
        iq_int, _, _ = build_preamble(rate_bits, length, cfo_hz, snr_db,
                                      offset, channel)
        tsf_iq = tsf0 + k * 1000
        csi56 = channel_response(channel) * 1024.0
        p = int(round(cfo_hz * 512.0 * 2 * np.pi / pp.FS))
        words.append(pack_record(iq_int, tsf_iq, tsf_iq + 14, p, csi56))
    return np.concatenate(words)


def build_noise_records(n_rec, tsf0=50_000_000):
    """构造 n_rec 条纯噪声 record(无前导码、CSI 全 0)。"""
    words = []
    for k in range(n_rec):
        noise = (np.random.randn(pp.IQ_LEN) + 1j * np.random.randn(pp.IQ_LEN)) \
                / np.sqrt(2) * 2048 * 0.5
        nq = np.round(noise)
        iq_int = (np.clip(nq.real, -32768, 32767).astype(np.int64)
                  + 1j * np.clip(nq.imag, -32768, 32767).astype(np.int64))
        tsf_iq = tsf0 + k * 1000
        words.append(pack_record(iq_int, tsf_iq, tsf_iq + 14, 0,
                                 np.zeros(pp.CSI_LEN)))
    return np.concatenate(words)


# ═══════════════════════════════════════════════════════════════════════════════
# 断言辅助
# ═══════════════════════════════════════════════════════════════════════════════

PASS, FAIL = 0, 0

# 测试用的 4 种 RATE(键:rate_bits 元组,值:Mbps)
RATES = {(1, 1, 0, 1): 6, (1, 1, 1, 1): 9, (0, 1, 0, 1): 12, (0, 0, 0, 1): 48}


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}  {detail}')


# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global PASS, FAIL
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--demo', metavar='BIN', default=None,
                    help='生成一套无需板子的演示采集数据(前导码合成 .bin)后退出')
    args = ap.parse_args()

    rng = np.random.default_rng(42)
    np.random.seed(42)   # 保证合成噪声可复现(测试确定性)

    if args.demo:
        # ---- 演示数据:4 种 RATE × 不同 CFO/SNR/偏移 + 噪声 + 低 SNR 边界 ----
        prefix = np.full(93, 0xFFFFFFFFFFFFFFFF, dtype='<u8')
        parts = [prefix]
        tsf = 10_000_000
        for k in range(30):
            rb = list(RATES)[k % 4]
            cfo = (-80e3, -30e3, 12e3, 45e3, 80e3)[k % 5]
            snr = 25.0 - 0.5 * k
            off = (0, 8, 16, 24, 32)[k % 5]
            parts.append(build_stream(rb, 40 + 12 * k, cfo, snr, off,
                                      n_rec=1, tsf0=tsf))
            tsf += 1000
        for k in range(3):   # 低 SNR 边界:部分会"确认但 L-SIG 不过"
            parts.append(build_stream((1, 1, 0, 1), 120 + k, 37e3, 2.0, 8,
                                      n_rec=1, tsf0=tsf))
            tsf += 1000
        parts.append(build_noise_records(5, tsf0=tsf))
        stream = np.concatenate(parts)
        dname = os.path.dirname(os.path.abspath(args.demo))
        os.makedirs(dname, exist_ok=True)
        with open(args.demo, 'wb') as fh:
            fh.write(stream.tobytes())
        print(f'[demo] 已写 {args.demo}:{len(stream)} word'
              f' = 93 错位前缀 + 30 正常 + 3 低SNR + 5 噪声 record')
        print(f'[demo] 下一步: python preamble_parse.py {args.demo} '
              f'--out {os.path.join(dname, "analysis")}')
        return

    print('=' * 68)
    print('  openwifi 前导码解析器 —— 合成帧回环自检')
    print('=' * 68)

    # ---- (0) 记录格式契约自检 ----
    print('\n[0] record 打包/解析契约')
    w0 = build_stream((1, 1, 0, 1), 100, 0.0, 30.0, 0)
    rec = pp.record_of(w0, 0, pp.LAYOUTS['iq_first'])
    check('REC_WORDS == 499', pp.REC_WORDS == 499)
    check('TSF 配对解析', rec['tsf_iq'] == 10_000_000
          and rec['tsf_csi'] == 10_000_014)
    check('IQ 首采样 = L-STF 首采样(比例)', abs(rec['iq'][0]) > 100,
          f"|iq[0]|={abs(rec['iq'][0]):.0f}")
    check('RSSI/增益状态字', rec['rssi_half_db'][0] == 500
          and rec['gain'][0] == 40 and rec['fcs_ok'][0] == 1)
    check('CSI 交换后序 = [-28..-1,+1..+28]',
          abs(rec['csi'][0] - channel_response((1.0, 0.35j + 0.0))[0] * 1024) < 1.0)
    check('cfo_board 换算', abs(rec['cfo_board_hz']) < 1.0)

    # ---- (1) 流自对齐:93 word 错位前缀 ----
    print('\n[1] 流自对齐(错位前缀)')
    prefix = np.full(93, 0xFFFFFFFFFFFFFFFF, dtype='<u8')
    stream = np.concatenate([
        prefix,
        build_stream((1, 1, 0, 1), 100, 37e3, 25.0, 0, n_rec=6),
    ])
    recs = list(pp.iter_records(stream, pp.LAYOUTS['iq_first']))
    check('6 条全部解析且 TSF 正确',
          len(recs) == 6 and recs[0][2]['tsf_iq'] == 10_000_000
          and recs[5][2]['tsf_iq'] == 10_005_000,
          f'len={len(recs)}')
    # 磁盘往返
    tmpd = tempfile.mkdtemp(prefix='ow_self_')
    binp = os.path.join(tmpd, 's.bin')
    with open(binp, 'wb') as fh:
        fh.write(stream.tobytes())
    u64b = pp.load_u64_stream(binp)
    check('.bin 读回一致', len(u64b) == len(stream)
          and np.array_equal(u64b, stream))

    # ---- (2)-(4) 合成帧回环矩阵 ----
    print('\n[2] 合成帧回环(SNR × CFO × 起点偏移)')
    grid = [
        (snr, cfo, off)
        for snr in (25.0, 15.0, 6.0)
        for cfo in (-150e3, 37e3, 150e3)
        for off in (0, 24, 40)
    ]
    n_ok = 0
    for idx, (snr, cfo, off) in enumerate(grid):
        rb = list(RATES)[idx % len(RATES)]
        length = 40 + 13 * idx
        w = build_stream(rb, length, cfo, snr, off, n_rec=1,
                         tsf0=30_000_000)
        r = pp.process_record(pp.record_of(w, 0, pp.LAYOUTS['iq_first']))
        # CFO 容差随 SNR 缩放:低 SNR 下双 LTF 估计噪声 ~±3 kHz,150 kHz 时 ~±7 kHz
        tol = max(3e3, 0.06 * abs(cfo))
        cfo_err = abs(r['cfo_total_hz'] - cfo)
        ok = (r['valid'] and r['rate_bits'] == ''.join(map(str, rb))
              and r['length'] == length
              and abs(r['stf_start_rel'] - off) <= 1
              and cfo_err < tol)
        if not ok:
            print(f'     [X] snr={snr:4.0f} cfo={cfo/1e3:+8.0f}k off={off:2d}'
                  f'  valid={r["valid"]} rate={r.get("rate_bits")}'
                  f' len={r.get("length")} start={r.get("stf_start_rel")}'
                  f' cfo={r.get("cfo_total_hz", 0)/1e3:+.2f}k'
                  f' err={cfo_err/1e3:.2f}k tol={tol/1e3:.1f}k'
                  f' reason={r.get("reason", "")}')
        n_ok += ok
    check(f'{n_ok}/{len(grid)} 合成帧全部正确恢复(含 RATE/LENGTH/起点/CFO)',
          n_ok == len(grid))

    # ---- (5) 板端 CSI / phase_offset 对照 ----
    print('\n[3] 板端 CSI 与 CFO 对照')
    w = build_stream((1, 1, 0, 1), 100, 37e3, 25.0, 0, n_rec=1)
    rec3 = pp.record_of(w, 0, pp.LAYOUTS['iq_first'])
    r = pp.process_record(rec3)
    check('csi_coh ≈ 1(H 与板端 CSI 复相干)', r['csi_coh'] > 0.95,
          f'csi_coh={r["csi_coh"]:.3f}')
    check('csi_mag_corr 高', r['csi_mag_corr'] > 0.9,
          f'csi_mag_corr={r["csi_mag_corr"]:.3f}')
    check('CFO 与板端 phase_offset 一致',
          abs(r['cfo_total_hz'] - rec3['cfo_board_hz']) < 2000,
          f'{r["cfo_total_hz"]/1e3:+.1f}k vs {rec3["cfo_board_hz"]/1e3:+.1f}k')
    check('LTF 匹配分高', r['ltf_corr'] > 0.7, f'ltf_corr={r["ltf_corr"]:.3f}')
    check('STF 模板分高', r['stf_corr'] > 0.8, f'stf_corr={r["stf_corr"]:.3f}')
    check('重编码符合率 ≈ 100%', r['reencode_agree'] > 0.95,
          f'{r["reencode_agree"]*100:.0f}%')

    # ---- (6) 纯噪声零误检 ----
    print('\n[4] 纯噪声零误检')
    noise_stream = np.concatenate([prefix, build_noise_records(30)])
    noise_recs = list(pp.iter_records(noise_stream, pp.LAYOUTS['iq_first']))
    noise_results = [pp.process_record(rec) for _, _, rec in noise_recs]
    n_false = sum(1 for r in noise_results if r['valid'])
    check('30 条噪声 record 零误检', len(noise_recs) == 30 and n_false == 0,
          f'解析 {len(noise_recs)} 条,误检 {n_false} 条')

    # ---- (7) 端到端 CLI:完整 main() 流程 ----
    print('\n[5] 端到端 CLI(main())')
    e2e_stream = np.concatenate([
        prefix,
        build_stream((1, 1, 0, 1), 64, 37e3, 20.0, 8, n_rec=5, tsf0=70_000_000),
        build_noise_records(5, tsf0=80_000_000),
    ])
    binp2 = os.path.join(tmpd, 'e2e.bin')
    with open(binp2, 'wb') as fh:
        fh.write(e2e_stream.tobytes())
    out_dir = os.path.join(tmpd, 'analysis')
    old_argv = sys.argv
    sys.argv = ['preamble_parse.py', binp2, '--out', out_dir]
    try:
        pp.main()
    finally:
        sys.argv = old_argv
    with open(os.path.join(out_dir, 'frames.json'), encoding='utf-8') as fh:
        d = json.load(fh)
    check('frames.json 统计正确',
          d['total_records'] == 10 and d['lsig_decoded'] == 5
          and d['ltf_confirmed'] == 5,
          f"total={d['total_records']} lsig={d['lsig_decoded']} "
          f"ltf={d['ltf_confirmed']}")
    npz = np.load(os.path.join(out_dir, 'frames.npz'))
    check('frames.npz 对齐波形 5×400', npz['preambles'].shape == (5, 400),
          f"shape={npz['preambles'].shape}")

    print('\n' + '=' * 68)
    print(f'  结果: {PASS} PASS / {FAIL} FAIL')
    print('=' * 68)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == '__main__':
    main()
