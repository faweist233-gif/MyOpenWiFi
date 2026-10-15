#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_long 修复验证激励生成器（在 Windows 侧用带 numpy 的 python 运行）

基于 upstream 真实采集帧(conducted/dot11a_24mbps_qos_data_*.txt,32bit hex,
{I16,Q16}),生成 5 组测试激励:
  weak1.txt   0.25 缩幅 + 噪声 σ=0.12  (~6dB, 弱但应可检出)
  weak2.txt   0.12 缩幅 + 噪声 σ=0.15  (~-2dB, 边界)
  noise.txt   纯噪声 σ=0.15            (必须零检出)
  cfo80k.txt  原幅 + 80kHz 频偏 + σ=0.05 (频偏下应可检出)
  cw_jam.txt  0.25 缩幅 + CW 干扰(3.2MHz, 幅 0.5) + σ=0.08 (模拟 ch5 干扰场景)
"""
import os
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, 'conducted',
                   'dot11a_24mbps_qos_data_e4_90_7e_15_2a_16_e8_de_27_90_6e_42.txt')

words = []
with open(SRC) as f:
    for line in f:
        line = line.strip()
        if line:
            words.append(int(line, 16))
W = np.array(words, dtype=np.uint32)
I = ((W >> 16) & 0xFFFF).astype(np.int32)
Q = (W & 0xFFFF).astype(np.int32)
I = np.where(I >= 0x8000, I - 0x10000, I)
Q = np.where(Q >= 0x8000, Q - 0x10000, Q)
x = (I.astype(np.float64) + 1j * Q.astype(np.float64)) / 32768.0
n = np.arange(len(x))
rng = np.random.default_rng(20261015)


def save(name, y):
    yi = np.clip(np.round(y.real * 32768), -32768, 32767).astype(np.int64)
    yq = np.clip(np.round(y.imag * 32768), -32768, 32767).astype(np.int64)
    out = ((yi & 0xFFFF) << 16) | (yq & 0xFFFF)
    with open(os.path.join(BASE, 'conducted', name), 'w') as f:
        f.write('\n'.join(f'{v:08x}' for v in out) + '\n')
    print(f'{name}: {len(out)} samples')


def noise(s):
    return (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) / np.sqrt(2) * s


save('weak1.txt',  x * 0.25 + noise(0.12))
save('weak2.txt',  x * 0.12 + noise(0.15))
save('noise.txt',  noise(0.15))
save('cfo80k.txt', x * np.exp(2j * np.pi * 80e3 * n / 20e6) + noise(0.05))
save('cw_jam.txt', x * 0.25 + 0.5 * np.exp(2j * np.pi * 0.16 * n) + noise(0.08))
print('done')
