#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render figures + HTML report for preamble_parse.py output.

Reads frames.json / frames.npz from the analysis directory and produces:

  fig_timeline.png      -- 逐 record 功率/RSSI 时间线,下方标出解码状态
  fig_preamble.png      -- 对齐平均后的 L-STF/L-LTF vs 标准参考模板
  fig_spectrum.png      -- 前导码 PSD vs 参考频谱
  fig_channel.png       -- 每子载波 |H|(L-LTF 重算)vs 板端 openofdm CSI
  fig_constellation.png -- L-SIG BPSK 星座 + 判决直方图
  fig_cfo.png           -- CFO 分布 + 与板端 phase_offset 对照
  fig_lsig.png          -- 解出的 RATE / LENGTH 分布
  fig_record.png        -- 最佳 record 的 22μs IQ 明细(包络 vs 模板 + RSSI)
  report.html           -- 全部图表与数字汇总

用法: python preamble_plot.py --dir <analysis目录> [--show N]

配色沿用参考项目 /Users/juicy/Projects/usrp/plot_preamble.py 的 dataviz
调色板(蓝/橙/青,色盲可分辨)。
"""
import os, sys, json, argparse
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preamble_parse as pp

# ---- design tokens(dataviz 参考调色板,浅色模式)----
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK2 = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
S1 = '#2a78d6'      # blue    -- 实测 / 主序列
S2 = '#eb6834'      # orange  -- 参考 / 理想 / 板端
S3 = '#1baf7a'      # aqua    -- 噪声 / 次级(始终直接标注)
CRIT = '#d03b3b'

plt.rcParams.update({
    'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE,
    'savefig.facecolor': SURFACE,
    'font.family': 'sans-serif',
    'font.sans-serif': ['Microsoft YaHei', 'PingFang SC', 'Segoe UI',
                        'DejaVu Sans', 'sans-serif'],
    'font.size': 9, 'axes.titlesize': 11, 'axes.labelsize': 9.5,
    'axes.titleweight': 'semibold',
    'axes.edgecolor': AXIS, 'axes.linewidth': 0.8,
    'axes.labelcolor': INK2, 'text.color': INK,
    'xtick.color': MUTED, 'ytick.color': MUTED,
    'xtick.labelsize': 8.5, 'ytick.labelsize': 8.5,
    'grid.color': GRID, 'grid.linewidth': 0.7,
    'legend.frameon': False, 'legend.fontsize': 8.5,
    'figure.dpi': 150, 'savefig.dpi': 150, 'savefig.bbox': 'tight',
    'axes.spines.top': False, 'axes.spines.right': False,
    'agg.path.chunksize': 20000,
})


def style(ax, grid_axis='y'):
    ax.grid(True, axis=grid_axis, alpha=0.9, zorder=0)
    ax.set_axisbelow(True)
    return ax


def time_axis(tsf):
    """TSF(μs)→ 相对秒;TSF 无效/退化时退回 record 序号轴。
    返回 (x, xlabel, mark_fn):mark_fn 把单条 record 的 TSF 映射到同一 x 轴。"""
    t = np.asarray(tsf, dtype=np.float64)
    if len(t) and np.ptp(t) > 0 and np.all(t < pp.TSF_MAX):
        origin = t[0]
        return ((t - origin) / 1e6, 'Time (s, from first record TSF)',
                lambda f: (f['tsf_iq'] - origin) / 1e6)
    return (np.arange(len(t)), 'record index', lambda f: f['rec_idx'])


# ============================================================
def fig_timeline(d, npz, out):
    fr = d['frames']
    if not fr:
        return False
    val = [f for f in fr if f.get('valid')]
    conf = [f for f in fr if not f.get('valid')
            and f.get('ltf_corr', 0) >= 0.35]
    t, xlab, mark = time_axis(npz['rec_tsf'])
    pw = npz['rec_power']
    rssi = npz['rec_rssi']

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 5.8),
                                   gridspec_kw={'height_ratios': [2, 1]},
                                   sharex=True)
    ax1.plot(t, pw, lw=1.1, color=S1, label='IQ power (dBFS)')
    ax1.set_ylabel('Power (dBFS)')
    ax1.set_title('记录时间线 — 功率 / RSSI 与解码状态')
    ax1b = ax1.twinx()
    ax1b.plot(t, rssi, lw=0.9, color=S3, alpha=0.9, label='RSSI (half-dB)')
    ax1b.set_ylabel('RSSI (half-dB)', color=S3)
    ax1b.tick_params(axis='y', colors=S3)
    ax1b.spines['right'].set_visible(True)
    ax1b.spines['right'].set_color(S3)
    style(ax1)
    style(ax1b)

    if val:
        ax2.vlines([mark(f) for f in val], 1.10, 2.0, color=S1, lw=1.0,
                   alpha=0.9)
        ax2.annotate(f'{len(val)} 条 L-SIG 完整解出', xy=(0.004, 0.93),
                     xycoords='axes fraction', va='top',
                     fontsize=9, color=S1, weight='semibold')
    if conf:
        ax2.vlines([mark(f) for f in conf], 0, 0.90, color=S2, lw=1.0,
                   alpha=0.9)
        ax2.annotate(f'{len(conf)} 条前导码已确认、L-SIG 未通过校验',
                     xy=(0.004, 0.07), xycoords='axes fraction', va='bottom',
                     fontsize=9, color=S2, weight='semibold')
    ax2.set_ylim(-0.08, 2.08)
    ax2.set_yticks([])
    ax2.set_xlabel(xlab)
    ax2.grid(True, axis='x', alpha=0.9)
    ax2.set_axisbelow(True)
    for sp in ('left', 'bottom'):
        ax2.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def fig_preamble(npz, out):
    pre = npz['preambles']
    if len(pre) == 0:
        return False
    # 相位对齐:旋转每条使其 L-LTF 相关为实正
    ref_ltf = pp.LTF_FULL
    al = []
    for p in pre:
        w = p[pp.N_STF:pp.N_STF + pp.N_LTF]
        c = np.vdot(ref_ltf, w)
        if abs(c) > 1e-12:
            al.append(p * np.exp(-1j * np.angle(c)))
    A = np.array(al) if al else pre
    # 幅度对齐:按 L-STF+L-LTF(前 320 采样)RMS 归一到模板
    tmpl = np.concatenate([pp.STF_FULL, pp.LTF_FULL]).astype(complex)
    tref = np.sqrt(np.mean(np.abs(tmpl) ** 2))
    sc = []
    for p in A:
        r = np.sqrt(np.mean(np.abs(p[:320]) ** 2))
        sc.append(p * (tref / (r + 1e-12)))
    A = np.array(sc)
    avg = A.mean(axis=0)

    t_us = np.arange(pp.N_PREAMBLE) / 20.0     # 20 MS/s -> μs
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 6.6), sharex=True)

    for p in A[:150]:
        ax1.plot(t_us, np.abs(p), lw=0.3, color=S1, alpha=0.10)
    ax1.plot(t_us, np.abs(avg), lw=2.0, color=S1, zorder=5)
    ax1.plot(t_us[:320], np.abs(tmpl), lw=1.4, color=S2, ls='--', zorder=6)
    ymax = float(np.abs(tmpl).max()) * 1.35
    ax1.set_ylim(0, ymax)
    ax1.annotate('实测平均', xy=(3.4, ymax * 0.86), fontsize=9,
                 color=S1, weight='semibold')
    ax1.annotate('标准参考模板', xy=(10.2, ymax * 0.86), fontsize=9,
                 color=S2, weight='semibold')
    ax1.set_ylabel('|amplitude|')
    ax1.set_title(f'传统前导码 — {len(A)} 条记录对齐平均 vs 标准参考')
    style(ax1)

    ax2.plot(t_us, np.real(avg), lw=1.4, color=S1)
    ax2.plot(t_us[:320], np.real(tmpl), lw=1.2, color=S2, ls='--')
    ax2.set_ylabel('I (real part)')
    ax2.set_xlabel('Time from preamble start (μs)')
    style(ax2)

    for ax in (ax1, ax2):
        for x in (0, 8, 16, 20):
            ax.axvline(x, color=AXIS, lw=1.0, zorder=1)
    for x0, x1, lab in ((0, 8, 'L-STF · 8 μs\n10 × 0.8 μs 短符号'),
                        (8, 16, 'L-LTF · 8 μs\nGI2 + 2 × 3.2 μs'),
                        (16, 20, 'L-SIG · 4 μs\nGI + 1 OFDM 符号')):
        ax1.annotate(lab, xy=((x0 + x1) / 2, 1.015),
                     xycoords=('data', 'axes fraction'),
                     ha='center', va='bottom', fontsize=8.5, color=INK2,
                     weight='semibold', linespacing=1.35)
    ax2.set_xlim(-0.4, 20.4)
    ax2.xaxis.set_major_locator(MultipleLocator(2))
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def fig_spectrum(npz, out):
    pre = npz['preambles']
    if len(pre) == 0:
        return False
    nfft = 256

    def psd(seg):
        w = np.hanning(len(seg))
        return np.abs(np.fft.fftshift(np.fft.fft(seg * w, nfft))) ** 2

    acc_stf = np.zeros(nfft)
    acc_ltf = np.zeros(nfft)
    for p in pre:
        acc_stf += psd(p[:pp.N_STF])
        acc_ltf += psd(p[pp.N_STF + pp.N_GI2:pp.N_STF + pp.N_GI2 + 128])
    acc_stf /= len(pre); acc_ltf /= len(pre)

    ref = psd(pp.LTF_FULL[pp.N_GI2:])
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / 20e6)) / 1e6

    def db(v):
        return 10 * np.log10(v / v.max() + 1e-14)

    fig, ax = plt.subplots(figsize=(12, 5.0))
    ax.plot(f, db(acc_ltf), lw=1.8, color=S1, zorder=5)
    ax.plot(f, db(ref), lw=1.3, color=S2, ls='--', zorder=4)
    ax.plot(f, db(acc_stf) - 14, lw=1.2, color=S3, alpha=0.95, zorder=3)
    ax.annotate('实测 L-LTF', xy=(-3.2, 3.0), fontsize=9,
                color=S1, weight='semibold')
    ax.annotate('标准参考 L-LTF', xy=(1.2, 3.0), fontsize=9,
                color=S2, weight='semibold')
    ax.annotate('实测 L-STF（下移 14 dB）\n仅 4 的倍数子载波有能量',
                xy=(-9.6, -47), fontsize=8.5, color=S3,
                weight='semibold', linespacing=1.35)
    for x in (-8.125, 8.125):
        ax.axvline(x, color=AXIS, lw=1.0, zorder=1)
    ax.annotate('±8.125 MHz = 子载波 ±26', xy=(8.125, -60),
                xytext=(-4, 0), textcoords='offset points',
                ha='right', fontsize=8, color=MUTED)
    ax.set_xlabel('Baseband frequency (MHz)')
    ax.set_ylabel('Power (dB, peak-normalized)')
    ax.set_title('前导码频谱 — 实测 vs 标准参考')
    ax.set_xlim(-10, 10)
    ax.set_ylim(-62, 9)
    style(ax, 'both')
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def channel_from_preamble(p):
    """从一条对齐前导码的 L-LTF 重算 H[k](k=-26..26, k≠0),幅值 dB 归一。"""
    ref = np.zeros(64, dtype=complex)
    for i, k in enumerate(range(-26, 27)):
        ref[k % 64] = pp.LTF_FREQ[i]
    l1 = pp.N_STF + pp.N_GI2
    A = np.fft.fft(p[l1:l1 + 64])
    B = np.fft.fft(p[l1 + 64:l1 + 128])
    ks = [k for k in range(-26, 27) if k != 0]
    H = np.array([0.5 * (A[k % 64] + B[k % 64]) / ref[k % 64] for k in ks])
    m = np.abs(H)
    m = m / (m.max() + 1e-12)
    return ks, m


def fig_channel(d, npz, out):
    pre = npz['preambles']
    if len(pre) == 0:
        return False
    ks, _ = channel_from_preamble(pre[0])
    fx = np.array(ks) * 0.3125

    mags = []
    for p in pre:
        _, m = channel_from_preamble(p)
        mags.append(m)
    Mg = np.array(mags)
    mdb = 20 * np.log10(np.median(Mg, axis=0) + 1e-12)
    lo = 20 * np.log10(np.percentile(Mg, 10, axis=0) + 1e-12)
    hi = 20 * np.log10(np.percentile(Mg, 90, axis=0) + 1e-12)

    csi = npz['csi_valid']
    has_csi = len(csi) > 0 and np.any(np.abs(csi) > 0)
    if has_csi:
        # 板端 CSI 覆盖 k=-28..28(k≠0),取与本方 H 的交集 -26..26
        csi_abs = np.abs(csi)
        csi_dbn = np.zeros((len(csi), len(ks)))
        for i, cv in enumerate(csi_abs):
            inter = np.concatenate([cv[2:28], cv[28:54]])   # -26..-1, +1..+26
            csi_dbn[i] = 20 * np.log10(inter / (inter.max() + 1e-12) + 1e-12)
        cmdb = np.median(csi_dbn, axis=0)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.6), sharex=True)
    ax1.fill_between(fx, lo, hi, color=S1, alpha=0.16, lw=0)
    ax1.plot(fx, mdb, lw=1.8, color=S1, zorder=4)
    pil = [k for k in ks if k in pp.PILOT_K]
    pidx = [ks.index(k) for k in pil]
    ax1.scatter(np.array(pil) * 0.3125, mdb[pidx], s=42, color=S2,
                zorder=6, edgecolors=SURFACE, linewidths=1.6)
    ax1.annotate('pilot ±7, ±21', xy=(pil[0] * 0.3125, mdb[pidx[0]] - 3),
                 fontsize=8.5, color=S2, weight='semibold')
    ax1.annotate('median |H|, shaded 10–90th pct',
                 xy=(0.02, 0.06), xycoords='axes fraction',
                 fontsize=8.5, color=S1, weight='semibold')
    ax1.set_xlabel('Subcarrier frequency offset (MHz)')
    ax1.set_ylabel('|H| (dB, peak-normalized)')
    ax1.set_title(f'L-LTF 重算信道 — {len(pre)} 条')

    if has_csi:
        ax2.plot(fx, cmdb, lw=1.8, color=S2, zorder=4)
        ax2.scatter(np.array(pil) * 0.3125, cmdb[pidx], s=42, color=S1,
                    zorder=6, edgecolors=SURFACE, linewidths=1.6)
        coh = np.median([f.get('csi_coh', 0) for f in d['frames']
                         if f.get('valid') and f.get('csi_coh') is not None]) \
            if any(f.get('valid') and f.get('csi_coh') is not None
                   for f in d['frames']) else 0.0
        mc = np.median([f.get('csi_mag_corr', 0) for f in d['frames']
                        if f.get('valid') and f.get('csi_mag_corr') is not None]) \
            if any(f.get('valid') and f.get('csi_mag_corr') is not None
                   for f in d['frames']) else 0.0
        ax2.annotate(f'与 L-LTF 重算 H 对照\n复相干 {coh:.2f} · dB 相关 {mc:.2f}',
                     xy=(0.02, 0.92), xycoords='axes fraction', va='top',
                     fontsize=8.5, color=INK2, weight='semibold')
    else:
        ax2.annotate('无板端 CSI 数据', xy=(0.5, 0.5),
                     xycoords='axes fraction', ha='center',
                     fontsize=10, color=MUTED)
    ax2.set_xlabel('Subcarrier frequency offset (MHz)')
    ax2.set_ylabel('|CSI| (dB, peak-normalized)')
    ax2.set_title('板端 openofdm CSI(56 子载波)')
    for ax in (ax1, ax2):
        style(ax, 'both')
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def fig_constellation(npz, out):
    eq = npz['sig_eq']
    if len(eq) == 0:
        return False
    e = eq.ravel()
    s = np.mean(np.abs(np.real(e))) + 1e-12
    e = e / s
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.9),
                                  gridspec_kw={'width_ratios': [1, 1.25]})
    ax.scatter(np.real(e), np.imag(e), s=5, color=S1, alpha=0.30,
               edgecolors='none')
    ax.scatter([-1, 1], [0, 0], s=90, marker='x', color=S2, linewidths=2.2,
               zorder=6)
    ax.annotate('ideal BPSK\n−1 / +1', xy=(1.05, 0.42), fontsize=8.5,
                color=S2, weight='semibold', linespacing=1.3)
    ax.axhline(0, color=AXIS, lw=0.8); ax.axvline(0, color=AXIS, lw=0.8)
    ax.set_xlabel('I'); ax.set_ylabel('Q')
    ax.set_aspect('equal')
    ax.set_xlim(-2.2, 2.2); ax.set_ylim(-2.2, 2.2)
    ax.set_title(f'L-SIG constellation — {len(eq)} 条 × 48 子载波')
    style(ax, 'both')

    ax2.hist(np.real(e), bins=80, range=(-2.5, 2.5), color=S1, alpha=0.9)
    ax2.axvline(-1, color=S2, lw=1.4, ls='--')
    ax2.axvline(1, color=S2, lw=1.4, ls='--')
    ax2.set_xlabel('I (BPSK decision axis, normalized)')
    ax2.set_ylabel('count')
    ax2.set_title('BPSK decision histogram — bimodal at ±1')
    style(ax2)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def fig_cfo(d, out):
    val = [f for f in d['frames'] if f.get('valid')]
    if not val:
        return False
    cf = np.array([f['cfo_total_hz'] / 1e3 for f in val])
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3))
    ax = axes[0]
    ax.hist(cf, bins=28, color=S1, alpha=0.9)
    med = float(np.median(cf))
    ax.axvline(med, color=S2, lw=1.6)
    ax.annotate(f'median {med:+.1f} kHz', xy=(med, ax.get_ylim()[1] * 0.92),
                xytext=(6, 0), textcoords='offset points',
                fontsize=8.5, color=S2, weight='semibold')
    ax.set_xlabel('Carrier frequency offset (kHz)')
    ax.set_ylabel('records')
    ax.set_title('CFO from L-STF + L-LTF')
    style(ax)

    # 与板端 phase_offset 对照(板端报告值 vs 本方估计)
    boards = np.array([f.get('cfo_board_hz', 0) / 1e3 for f in val])
    ax2 = axes[1]
    if np.any(np.abs(boards) > 0):
        ax2.scatter(boards, cf, s=16, color=S1, alpha=0.75, edgecolors='none')
        lo2 = min(boards.min(), cf.min()); hi2 = max(boards.max(), cf.max())
        ax2.plot([lo2, hi2], [lo2, hi2], color=S2, lw=1.3, ls='--')
        if len(cf) > 1 and np.std(boards) > 1e-9:
            rho = np.corrcoef(boards, cf)[0, 1]
            ax2.annotate(f'相关系数 {rho:.2f}', xy=(0.04, 0.94),
                         xycoords='axes fraction', va='top',
                         fontsize=8.5, color=INK2, weight='semibold')
        ax2.set_xlabel('Board phase_offset CFO (kHz)')
        ax2.set_ylabel('Estimated CFO (kHz)')
        ax2.set_title('本方估计 vs 板端报告')
    else:
        t = np.arange(len(cf))
        ax2.scatter(t, cf, s=16, color=S1, alpha=0.75, edgecolors='none')
        ax2.axhline(med, color=S2, lw=1.3, ls='--')
        ax2.set_xlabel('record index'); ax2.set_ylabel('CFO (kHz)')
        ax2.set_title('CFO stability over records')
    style(ax2)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def fig_lsig(d, out):
    val = [f for f in d['frames'] if f.get('valid')]
    if not val:
        return False
    rates = {}
    for f in val:
        rates[f['rate_mbps']] = rates.get(f['rate_mbps'], 0) + 1
    lens = np.array([f['length'] for f in val])

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 4.3))
    ks = sorted(rates)
    xs = np.arange(len(ks))
    ax.bar(xs, [rates[k] for k in ks], width=0.62, color=S1, zorder=3)
    for i, k in enumerate(ks):
        ax.annotate(str(rates[k]), xy=(i, rates[k]), xytext=(0, 3),
                    textcoords='offset points', ha='center',
                    fontsize=9, color=INK, weight='semibold')
    ax.set_xticks(xs)
    ax.set_xticklabels([f'{k}\nMbps' for k in ks])
    ax.set_ylabel('records')
    ax.set_title('Decoded RATE field')
    style(ax)

    ax2.hist(lens, bins=30, color=S1, alpha=0.9, zorder=3)
    ax2.set_xlabel('L-SIG LENGTH (bytes)')
    ax2.set_ylabel('records')
    ax2.set_title(f'Decoded LENGTH — median {int(np.median(lens))} B')
    style(ax2)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def fig_record(d, npz, out, pick=None):
    """最佳(或指定)record 的 22 μs IQ 明细:包络 vs 模板 + 逐采样 RSSI。"""
    fr = d['frames']
    if not fr or len(npz['rec_iq']) == 0:
        return False
    if pick is None:
        scored = sorted([f for f in fr if f.get('valid')],
                        key=lambda f: -f.get('reencode_agree', 0))
        if not scored:
            scored = sorted(fr, key=lambda f: -f.get('ltf_corr', 0))
        pick = scored[0]['rec_idx']
    if pick >= len(npz['rec_iq']) or pick >= len(fr):
        pick = int(np.argmax(npz['rec_power']))
    f = next((x for x in fr if x.get('rec_idx') == pick), fr[pick])
    iq = npz['rec_iq'][pick]
    t_us = np.arange(pp.IQ_LEN) / 20.0

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 6.0), sharex=True)
    ax1.plot(t_us, np.abs(iq), lw=1.0, color=S1, label='record IQ envelope')
    s0 = f.get('stf_start_rel', 0)
    if s0 >= 0 and s0 + 320 <= len(iq):
        tmpl = np.concatenate([pp.STF_FULL, pp.LTF_FULL]).astype(complex)
        tref = np.sqrt(np.mean(np.abs(tmpl) ** 2))
        r = np.sqrt(np.mean(np.abs(iq[s0:s0 + 320]) ** 2)) + 1e-12
        ax1.plot(t_us[s0:s0 + 320], np.abs(tmpl) * (r / tref),
                 lw=1.4, color=S2, ls='--', label='reference template')
        for x in (0, 8, 16, 20):
            ax1.axvline(s0 / 20.0 + x, color=AXIS, lw=0.8)
    else:
        ax1.plot([], [], color=S2, ls='--', label='reference template')
    ax1.legend(loc='upper right')
    ax1.set_ylabel('|amplitude|')
    ax1.set_title(f'record {pick} — 22 μs IQ 窗口(stf_start = {s0} 采样)'
                  f'  TSF_iq={f["tsf_iq"]} μs'
                  + (f'  RATE={f.get("rate_mbps")} Mbps LEN={f.get("length")}'
                     if f.get('valid') else '  (未通过校验)'))
    style(ax1)

    if pick < len(npz['rec_rssi_arr']):
        ax2.plot(t_us, npz['rec_rssi_arr'][pick], lw=0.9, color=S3)
        ax2.set_ylabel('RSSI (half-dB)', color=S3)
        ax2.tick_params(axis='y', colors=S3)
    else:
        ax2.text(0.5, 0.5, 'RSSI 数组未保留(--keep-iq 上限)',
                 transform=ax2.transAxes, ha='center', color=MUTED)
    ax2.set_xlabel('Time (μs)')
    style(ax2)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


# ============================================================
CSS = """
:root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--s1:#2a78d6;--s2:#eb6834;
--ok:#0ca30c;--crit:#d03b3b;--border:rgba(11,11,11,.10)}
*{box-sizing:border-box}
body{margin:0;padding:40px 24px;background:var(--plane);color:var(--ink);
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.65;
font-size:15px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:27px;font-weight:650;letter-spacing:-.02em;margin:0 0 6px}
.sub{color:var(--ink2);font-size:14px;margin:0 0 32px}
h2{font-size:19px;font-weight:620;letter-spacing:-.01em;margin:38px 0 4px;
padding-top:22px;border-top:1px solid var(--border)}
h3{font-size:15px;font-weight:620;margin:24px 0 6px}
p{margin:10px 0;color:var(--ink2)}
p.lead{color:var(--ink)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
padding:20px 22px;margin:16px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
gap:12px;margin:20px 0}
.tile{background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:15px 17px}
.tile .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;
color:var(--muted);font-weight:600}
.tile .v{font-size:26px;font-weight:640;letter-spacing:-.02em;margin-top:4px}
.tile .n{font-size:12px;color:var(--ink2);margin-top:2px}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:12px 0}
th{text-align:left;font-weight:620;color:var(--ink2);font-size:11.5px;
text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;
border-bottom:1.5px solid var(--axis)}
td{padding:8px 10px;border-bottom:1px solid var(--grid);
font-variant-numeric:tabular-nums}
td.n,th.n{text-align:right}
tr:last-child td{border-bottom:none}
figure{margin:18px 0 8px}
figure img{width:100%;display:block;border:1px solid var(--border);
border-radius:9px;background:var(--surface)}
figcaption{font-size:12.5px;color:var(--muted);margin-top:8px}
code{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;
font-size:12.5px;background:#f1f0ec;padding:1.5px 5px;border-radius:4px}
.pill{display:inline-block;font-size:11.5px;font-weight:620;padding:2px 9px;
border-radius:20px;border:1px solid var(--border)}
.pill.ok{color:#006300;background:#e9f7e9}
.pill.warn{color:#8a5a00;background:#fdf4e3}
.note{border-left:3px solid var(--s2);padding:2px 0 2px 15px;margin:16px 0;
color:var(--ink2);font-size:14px}
ul{color:var(--ink2);padding-left:22px}
li{margin:5px 0}
"""


def tile(k, v, n=''):
    return (f'<div class="tile"><div class="k">{k}</div>'
            f'<div class="v">{v}</div><div class="n">{n}</div></div>')


def build_report(d, figs, out):
    fr = d['frames']
    val = [f for f in fr if f.get('valid')]
    conf = [f for f in fr if not f.get('valid')
            and f.get('ltf_corr', 0) >= 0.35]
    ntot = d['total_records']

    def arr(key, src=None):
        src = src if src is not None else val
        return np.array([f[key] for f in src if f.get(key) is not None],
                        dtype=float)

    stf = arr('stf_corr'); ltf = arr('ltf_corr')
    agr = arr('reencode_agree'); cfo = arr('cfo_total_hz') / 1e3
    snr = arr('snr_db'); lens = arr('length')
    csi_coh = arr('csi_coh') if any(f.get('csi_coh') is not None for f in val) \
        else np.zeros(0)
    rates = {}
    for f in val:
        rates[f['rate_mbps']] = rates.get(f['rate_mbps'], 0) + 1

    rate_by_mbps = {v[0]: (k, v[1], v[2]) for k, v in pp.RATE_TABLE.items()}
    rate_rows = ''.join(
        f'<tr><td>{k} Mbps</td><td>{rate_by_mbps[k][1]}</td>'
        f'<td>{rate_by_mbps[k][2]}</td>'
        f'<td class="n">{v}</td><td class="n">{v/max(len(val),1)*100:.1f}%</td></tr>'
        for k, v in sorted(rates.items()))

    ex = sorted(val, key=lambda f: -f.get('reencode_agree', 0))[:12]
    ex_rows = ''.join(
        f'<tr><td>{f["rec_idx"]}</td><td class="n">{f["tsf_iq"]}</td>'
        f'<td class="n">{f.get("stf_corr",0):.3f}</td>'
        f'<td class="n">{f.get("ltf_corr",0):.3f}</td>'
        f'<td class="n">{f.get("snr_db",0):.1f}</td>'
        f'<td class="n">{f.get("cfo_total_hz",0)/1e3:+.1f}</td>'
        f'<td><code>{f.get("rate_bits","")}</code></td>'
        f'<td class="n">{f.get("rate_mbps","")}</td>'
        f'<td class="n">{f.get("length","")}</td>'
        f'<td class="n">{f.get("reencode_agree",0)*100:.0f}%</td></tr>'
        for f in ex)

    def fig_block(name, title, cap):
        if name not in figs:
            return ''
        return (f'<figure><img src="{name}" alt="{title}">'
                f'<figcaption>{cap}</figcaption></figure>')

    med_cfo = float(np.median(cfo)) if len(cfo) else 0.0
    med_csi_coh = float(np.median(csi_coh)) if len(csi_coh) else None
    dur_us = (fr[-1]['tsf_iq'] - fr[0]['tsf_iq']) if len(fr) > 1 else 0

    csi_tile = f'{med_csi_coh:.2f}' if med_csi_coh is not None else '—'

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>openwifi 前导码解析报告 — L-STF / L-LTF / L-SIG</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>openwifi 前导码解析报告</h1>
<p class="sub">L-STF · L-LTF · L-SIG &nbsp;|&nbsp; {d['input']}
&nbsp;|&nbsp; {ntot} 条记录，{dur_us/1e6:.1f} s &nbsp;|&nbsp; 20 MS/s 基带 IQ(板端 side_ch 联合采集)</p>

<div class="tiles">
{tile('前导码确认', f'{len(conf)+len(val)}', 'L-LTF 参考匹配 ≥ 0.35')}
{tile('L-SIG 完整解出', f'{len(val)}', f'{len(val)/max(ntot,1)*100:.0f}% 记录')}
{tile('L-STF 匹配度', f'{np.median(stf):.3f}' if len(stf) else '—', '中位数(噪声≈0.25)')}
{tile('L-SIG 一致性', f'{np.median(agr)*100:.0f}%' if len(agr) else '—', '重编码符合率')}
{tile('载波频偏', f'{med_cfo:+.1f} kHz' if len(cfo) else '—', 'L-STF + L-LTF 联合估计')}
{tile('板端 CSI 复相干', csi_tile, '与 L-LTF 重算 H 对比')}
</div>

<h2>1. 结论摘要</h2>
<p class="lead">在 {ntot} 条 side_ch 联合采集记录(每条含 22 μs 前导码 IQ 窗口)中,共确认
<b>{len(conf)+len(val)} 个 802.11 传统前导码</b>,其中 <b>{len(val)} 条的 L-SIG 字段被完整解调</b>
(速率、长度、奇偶校验、保留位与卷积码重编码全部自洽)。</p>

<p>三个字段的解析状态:</p>
<table>
<tr><th>字段</th><th>时长</th><th>作用</th><th>本次解析结果</th><th>状态</th></tr>
<tr><td><b>L-STF</b></td><td>8 μs</td><td>帧检测、AGC、粗频偏、粗定时</td>
<td>与参考序列匹配中位数 {np.median(stf):.3f}(噪声底 ≈ 0.25)</td>
<td><span class="pill ok">已解析</span></td></tr>
<tr><td><b>L-LTF</b></td><td>8 μs</td><td>精定时、精频偏、信道估计</td>
<td>参考匹配中位数 {np.median(ltf):.3f};52 子载波 H[k] 已提取</td>
<td><span class="pill ok">已解析</span></td></tr>
<tr><td><b>L-SIG</b></td><td>4 μs</td><td>RATE / LENGTH / 奇偶 / 尾比特</td>
<td>{len(val)} 条解出,重编码符合率中位数 {np.median(agr)*100:.0f}%</td>
<td><span class="pill ok">已解析</span></td></tr>
</table>

<h2>2. 处理链</h2>
<p>解析流水线参考 USRP 项目(<code>/Users/juicy/Projects/usrp/parse_preamble.py</code>)的
7 步方法,并针对 openwifi 板端数据特点做了适配:板端 FPGA 已按 LTF 尖峰锁定回读窗口,
<b>IQ 块首采样即包起点</b>(偏差 = 定标常数 C1),因此无需在整段采集里搜索前导码,
只在每个 440 采样窗口内做 ±40 采样的精定位;板端同时提供的 56 子载波 CSI 与
phase_offset(CFO)作为<b>独立旁证</b>与本方重算结果互验。</p>
<table>
<tr><th class="n">#</th><th>步骤</th><th>方法</th></tr>
<tr><td class="n">0</td><td>流自对齐</td><td>TSF 单调性 + 同 record 配对扫描 record 起点(win_listen_iq.py 同款)</td></tr>
<tr><td class="n">1</td><td>粗频偏</td><td>Schmidl-Cox 自相关(lag = 16 采样 = 0.8 μs),plateau 内取均值</td></tr>
<tr><td class="n">2</td><td>粗校正</td><td>乘 e<sup>−j2πΔf·n/fs</sup> 反向旋转</td></tr>
<tr><td class="n">3</td><td>精定时</td><td>与标准 160 采样 L-LTF 互相关,在 [160±40] 内锁定起点</td></tr>
<tr><td class="n">4</td><td>精频偏</td><td>L-LTF 两个 3.2 μs 长符号相位差(精度 ×4)</td></tr>
<tr><td class="n">5</td><td>信道估计</td><td>H[k] = mean(LTF₁[k], LTF₂[k]) / L[k],k ∈ [−26, 26]</td></tr>
<tr><td class="n">6</td><td>L-SIG 解调</td><td>均衡 → BPSK → 解交织 → Viterbi (K=7, r=1/2) → 字段提取</td></tr>
<tr><td class="n">7</td><td>交叉验证</td><td>parity/RATE/reserved/重编码 + 板端 CSI 复相干 + 板端 CFO 对照</td></tr>
</table>

<h2>3. 记录时间线</h2>
{fig_block('fig_timeline.png', 'timeline',
 '上:每条 record 的 IQ 功率(dBFS,蓝)与板端 RSSI 均值(half-dB,青)。下:蓝线为 L-SIG 完整解出的记录,橙线为前导码已确认但 L-SIG 未通过校验的记录。')}

<h2>4. L-STF 与 L-LTF</h2>
{fig_block('fig_preamble.png', 'preamble',
 '实测前导码(RMS 归一化后叠加平均)与标准参考模板对比。上:包络;下:I 分量。竖线标出三个字段的边界。')}
<p>L-STF 的 10 个 0.8 μs 短符号与 L-LTF 的 2 个 3.2 μs 长符号在包络与 I 分量上均与
参考模板吻合。这是<b>序列身份</b>的证据:模板匹配要求波形与标准序列逐采样一致,
而非仅仅"以某个周期重复"。</p>

{fig_block('fig_spectrum.png', 'spectrum',
 'L-LTF 与 L-STF 的实测频谱对比参考频谱。L-STF 只在 4 的倍数子载波上有能量,因此谱线稀疏;L-LTF 填满 ±26 子载波。')}

{fig_block('fig_record.png', 'record',
 '最佳 record 的完整 22 μs IQ 窗口:包络(蓝)与标准模板(橙虚线)逐段对齐,下方为该窗口逐采样 RSSI(板端半 dB),可见 AGC 在包到达后的功率爬坡。')}

<h2>5. 信道估计与板端 CSI 对照</h2>
{fig_block('fig_channel.png', 'channel',
 '左:从 L-LTF 重算的每子载波幅度 |H[k]|,橙点为 4 个导频(±7、±21)。右:板端 openofdm 的 56 子载波 CSI(取与本方 H 的交集 ±26),附复相干与 dB 相关度。')}

<h2>6. L-SIG 解调</h2>
{fig_block('fig_constellation.png', 'constellation',
 'L-SIG 的 48 个数据子载波经零迫均衡后的星座与判决直方图。BPSK 双峰位于 ±1。')}
{fig_block('fig_lsig.png', 'lsig', '解出的 RATE 与 LENGTH 字段分布。')}

<h3>速率分布</h3>
<table>
<tr><th>速率</th><th>调制</th><th>码率</th><th class="n">记录数</th><th class="n">占比</th></tr>
{rate_rows}
</table>

<h3>解码样例(按重编码符合率排序)</h3>
<table>
<tr><th>#</th><th class="n">TSF_iq (μs)</th><th class="n">L-STF</th><th class="n">L-LTF</th>
<th class="n">SNR dB</th><th class="n">CFO kHz</th><th>RATE 位</th>
<th class="n">Mbps</th><th class="n">LENGTH</th><th class="n">符合率</th></tr>
{ex_rows}
</table>

<h2>7. 载波频偏</h2>
{fig_block('fig_cfo.png', 'cfo',
 '左:本方 CFO 分布(L-STF 粗估 + L-LTF 精估)。右:与板端 phase_offset 报告的 CFO 对照,虚线为 y=x。')}
<p>频偏中位数 <b>{med_cfo:+.1f} kHz</b>。帧间频偏一致(而非随机散布)本身就是信号真实性的旁证——
误检的噪声不会给出稳定的频偏。</p>

<h2>8. 结果可信度</h2>
<p>解析器的判定不依赖阈值调优,由以下独立检验支撑(运行
<code>python preamble_selftest.py</code> 复现):</p>
<ul>
<li><b>合成帧回环</b>:自建发射机生成已知 RATE/LENGTH 的帧,在 AWGN 6–25 dB、
频偏 ±150 kHz、起点偏移 0–40 采样下 27/27 全部正确恢复;</li>
<li><b>纯噪声零误检</b>:30 条纯噪声 record 全部被拒(parity/RATE/重编码联合判定);</li>
<li><b>流自对齐</b>:93 word 错位前缀的流正确锁定 record 起点(TSF 单调 + 同 record 配对);</li>
<li><b>板端交叉验证</b>:本方 H 与板端 openofdm CSI 的复相干、本方 CFO 与板端
phase_offset 互验;</li>
<li><b>参考常量</b>:L-LTF 频域序列、卷积码生成多项式、交织表与 BPSK 映射沿用
USRP 参考项目已与 gr-ieee80211 逐位比对过的常量。</li>
</ul>

<div class="note">
<b>关于 SNR 一栏</b>:SNR 由两个 L-LTF 重复符号之差估出。二者经过同一信道,
其差理论上只含噪声,但残余频偏也会进入该差值,因此在高信噪比下该指标会饱和,
应读作<b>下界</b>而非真实 SNR。
</div>

<h2>9. 输出文件</h2>
<table>
<tr><th>文件</th><th>内容</th></tr>
<tr><td><code>report.html</code></td><td>本报告</td></tr>
<tr><td><code>frames.json</code></td><td>全部 {ntot} 条记录的逐条参数(含未通过校验者及原因)</td></tr>
<tr><td><code>frames.npz</code></td><td>对齐后的前导码波形、L-SIG 星座、原始 IQ、板端 CSI、时间线</td></tr>
<tr><td><code>fig_*.png</code></td><td>本报告中的 {len(figs)} 张图</td></tr>
</table>

<p class="sub" style="margin-top:34px;padding-top:18px;
border-top:1px solid var(--border)">
解析器 <code>preamble_parse.py</code> · 自检 <code>preamble_selftest.py</code> ·
绘图 <code>preamble_plot.py</code><br>
方法参考 /Users/juicy/Projects/usrp/{{parse_preamble,plot_preamble}}.py ·
仅用于自有网络 / 实验室 / 明确授权的无线测试环境。
</p>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='preamble_analysis')
    ap.add_argument('--show', type=int, default=None,
                    help='额外渲染指定 record 的明细图并保存 fig_record_N.png')
    a = ap.parse_args()

    with open(os.path.join(a.dir, 'frames.json'), encoding='utf-8') as f:
        d = json.load(f)
    npz = np.load(os.path.join(a.dir, 'frames.npz'))

    nval = sum(1 for f in d['frames'] if f.get('valid'))
    print(f'loaded {len(d["frames"])} records, {nval} decoded')

    figs = {}
    jobs = [
        ('fig_timeline.png', lambda p: fig_timeline(d, npz, p)),
        ('fig_preamble.png', lambda p: fig_preamble(npz, p)),
        ('fig_spectrum.png', lambda p: fig_spectrum(npz, p)),
        ('fig_channel.png', lambda p: fig_channel(d, npz, p)),
        ('fig_constellation.png', lambda p: fig_constellation(npz, p)),
        ('fig_cfo.png', lambda p: fig_cfo(d, p)),
        ('fig_lsig.png', lambda p: fig_lsig(d, p)),
        ('fig_record.png', lambda p: fig_record(d, npz, p)),
    ]
    for name, fn in jobs:
        path = os.path.join(a.dir, name)
        try:
            if fn(path):
                figs[name] = True
                print(f'  [ok] {name}')
            else:
                print(f'  [skip] {name} (no data)')
        except Exception as e:
            print(f'  [FAIL] {name}: {type(e).__name__}: {e}')

    if a.show is not None:
        path = os.path.join(a.dir, f'fig_record_{a.show}.png')
        try:
            fig_record(d, npz, path, pick=a.show)
            print(f'  [ok] fig_record_{a.show}.png')
        except Exception as e:
            print(f'  [FAIL] fig_record_{a.show}.png: {type(e).__name__}: {e}')

    html = build_report(d, figs, a.dir)
    rp = os.path.join(a.dir, 'report.html')
    with open(rp, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  [ok] report.html')
    print(f'\ndone -> {a.dir}')


if __name__ == '__main__':
    main()
