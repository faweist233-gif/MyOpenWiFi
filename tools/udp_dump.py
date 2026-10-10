#!/usr/bin/env python3
"""Pure-text UDP dump for side_ch combined CSI+IQ (no matplotlib).
Bind localhost:4000, receive N packets, write raw hex to a file.
Usage: udp_dump.py <iq_len> <num_pkts> <outfile>
"""
import os
import socket
import sys
import numpy as np

UDP_IP = "0.0.0.0"
UDP_PORT = 4000
MAX_NUM_DMA_SYMBOL = 8192

iq_len = int(sys.argv[1]) if len(sys.argv) > 1 else 440
num_pkts = int(sys.argv[2]) if len(sys.argv) > 2 else 3
outfile = sys.argv[3] if len(sys.argv) > 3 else "iq_dump.txt"
num_eq = int(sys.argv[4]) if len(sys.argv) > 4 else 0

# combined mode: RECORD_LEN(iq_len, num_eq) = (1+iq_len) + (HEADER_LEN+CSI_LEN+num_eq*EQUALIZER_LEN)
CSI_LEN = 56
HEADER_LEN = 2
EQUALIZER_LEN = 52
record_len = (1 + iq_len) + (HEADER_LEN + CSI_LEN + num_eq*EQUALIZER_LEN)
bytes_per_pkt = record_len * 8

if os.path.exists(outfile):
    os.remove(outfile)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(15)

print(f"listening {UDP_IP}:{UDP_PORT}  record_len={record_len} bpp={bytes_per_pkt}")

fd = open(outfile, "a")
n = 0
while n < num_pkts:
    try:
        data, addr = sock.recvfrom(MAX_NUM_DMA_SYMBOL * 8)
    except socket.timeout:
        print(f"timeout, got {n} pkts")
        break
    n += 1
    symbols = len(data) // 8
    fd.write(f"# pkt {n} from {addr} bytes={len(data)} symbols={symbols}\n")
    u16 = np.frombuffer(data, dtype="uint16")
    fd.write(" ".join(str(x) for x in u16[:64]) + "\n")
    resid = len(data) % bytes_per_pkt
    print(f"pkt {n}: len={len(data)} symbols={symbols} resid_vs_record={resid}")
    fd.flush()

print(f"DONE {n} pkts -> {outfile}")
fd.close()
sock.close()
