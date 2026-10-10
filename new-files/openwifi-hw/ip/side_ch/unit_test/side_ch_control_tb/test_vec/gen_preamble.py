#!/usr/bin/env python3
"""
Generate an 802.11n/ag 20 MHz baseband preamble stream for side_ch_control_tb.

Layout: N_REGIONS regions of SPACING samples each. In each region the same
L-STF + L-LTF + L-SIG baseline (404 samples) is placed at the region start,
scaled per-region so that sample(gstart) != sample(gstart+3) for every region
(the tb alignment self-check needs the first IQ sample to be distinguishable).

Output data_in.txt: two columns 'I Q' per line, 16-bit signed, 20 MHz.
"""
import numpy as np

NUM_SUBCARRIER = 64          # FFT size at 20 MHz
N_REGIONS = 10
SPACING = 2100
PRE_TRIG = 300               # reliable LTF correlation peak position from packet start (C1)

# ---- L-STF frequency coefficients (IEEE 802.11-2016), 53 taps k=-26..26 ----
stf_pilot = np.array([0, 0, 1+1j, 0, 0, 0, -1-1j, 0, 0, 0, 1+1j, 0, 0, 0, -1-1j, 0,
                      0, 0, -1-1j, 0, 0, 0, 1+1j, 0, 0, 0, 0, 0, 0, 0, -1-1j, 0, 0,
                      0, -1-1j, 0, 0, 0, 1+1j, 0, 0, 0, 1+1j, 0, 0, 0, 1+1j, 0, 0,
                      0, 1+1j, 0, 0])
stf_freq = np.zeros(NUM_SUBCARRIER, dtype=complex)
stf_freq[6:6+len(stf_pilot)] = stf_pilot * np.sqrt(13.0/6.0)
stf_seq = np.fft.ifft(np.fft.ifftshift(stf_freq)) * 64
stf = np.tile(stf_seq[:16], 10)                        # 10 x 0.8us

# ---- L-LTF (IEEE 802.11-2016) ----
ltf_k_neg = np.array([1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1], dtype=complex)  # k=-26..-1
ltf_k_pos = np.array([1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1], dtype=complex)   # k= 1..26
ltf_freq = np.zeros(NUM_SUBCARRIER, dtype=complex)
ltf_freq[6:32] = ltf_k_neg
ltf_freq[38:64] = ltf_k_pos
ltf_freq = np.fft.ifftshift(ltf_freq)
ltf_t1 = np.fft.ifft(ltf_freq) * 64
ltf = np.concatenate([ltf_t1[-32:], ltf_t1, ltf_t1])   # TGI + T1 + T2

# ---- L-SIG placeholder (80 samples) ----
lsig = np.concatenate([ltf_t1, ltf_t1[:16]])

preamble = np.concatenate([stf, ltf, lsig])
preamble = preamble / (np.max(np.abs(preamble)) + 1e-9) * 6000.0
assert len(preamble) == 400

stream = np.zeros(N_REGIONS*SPACING, dtype=complex)
for r in range(N_REGIONS):
    scale = 1.0 - 0.05*r   # distinct per region, stays within int16
    stream[r*SPACING : r*SPACING+400] = preamble * scale

i_sig = np.int16(np.round(stream.real))
q_sig = np.int16(np.round(stream.imag))

with open("data_in.txt", "w") as f:
    for ii, qq in zip(i_sig, q_sig):
        f.write(f"{ii} {qq}\n")

# self-check: every region's first sample must be distinguishable from +3 (alignment test)
for r in range(N_REGIONS):
    g = r*SPACING
    assert i_sig[g] != i_sig[g+3], f"region {r}: start sample == start+3 -> alignment self-check invalid"
print(f"wrote {len(stream)} samples to data_in.txt "
      f"(N_REGIONS={N_REGIONS} SPACING={SPACING}); region0 I0={i_sig[0]}, I3={i_sig[3]}")