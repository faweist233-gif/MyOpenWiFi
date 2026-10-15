#!/usr/bin/env python3
"""Windows 侧 UDP 监听器 —— 接收板端 side_ch_ctl 推送的 CSI+IQ 联合数据

用法: python win_listen_iq.py [输出根目录] [监听秒数] [--clean]
  --clean: 显式要求时才清理根目录下旧的 iq_* 产物目录（默认绝不删除）

产物（同一个以 iq 时间戳命名的目录 <根目录>/iq_<stamp>/）：
  iq_<stamp>.bin      原始 UDP 数据流裸拼接（无包头，按到达顺序首尾相接）
  iq_<stamp>.log      文本日志：包级明细 + record 级解析 + 汇总（汇总区块原地覆盖，只留最新）
  iq_<stamp>_csi.csv  完整 CSI 宽表（每行一个 record：tsf + 56 子载波 I/Q）

★ record 布局（RTL 修复后，[IQ块][CSI块]，499 word = 3992 字节）：
    w[0]        TSF_iq   (u64, 微秒)
    w[1..440]   440 x IQ，每 word 低32位 = {I16, Q16}，高32位 = 状态24b+GPIO8b
    w[441]      TSF_csi  (u64, 微秒)
    w[442]      phase_offset_taken (i64)  —— 载波频偏 CFO 估计（两个 LTF 符号相位差）
    w[443..498] 56 x CSI，每 word 低32位 = {I16, Q16}，高32位恒 0

★★ 同包配对（RTL 修复后）：同一条 record 内 IQ 块与 CSI 块属**同一包**，
    TSF_csi − TSF_iq ≈ 13~15 µs（LTF尖峰→header解完的固定间隔）。
    （历史：跨包错位 bug 时代曾为 [CSI块][IQ块] 且两块相差 ~13ms 跨包，已修复。）

★★★ 自对齐：PC 从流中途接入时，bin 起点 ≠ record 起点（实测曾错位 93 word）。
    解析器扫描 s∈[0,499) 找使 TSF 单调且数值合理（<2^48）的起点，
    首尾残段丢弃；TSF 跳变（回退或 >1s）处自动分段。

安全约定：绝不删除已有文件；--clean 才清理。
"""
import socket
import sys
import os
import time
import struct
import signal
from datetime import datetime

# ---- record 契约（与 side_ch.h RECORD_LEN 对齐）----
IQ_LEN = 440
NUM_EQ = 0
CSI_LEN = 56
HEADER_LEN = 2                      # TSF + phase_offset
CSI_BLK_LEN = HEADER_LEN + CSI_LEN + NUM_EQ * (CSI_LEN - 4)   # 58
REC_WORDS = (1 + IQ_LEN) + CSI_BLK_LEN                          # 499
REC_BYTES = REC_WORDS * 8                                       # 3992
TSF_MAX = 1 << 48                 # TSF 合理上限（IQ/状态字形态的 u64 远大于此）
TSF_GAP_LIMIT = 1_000_000         # 相邻 record TSF 差超过 1s 视为分段
PAIR_WINDOW = (10, 60)            # 同包配对 TSF 差的正常窗口 (µs)：LTF尖峰→header解完
# 同 record 内 TSF_csi − TSF_iq 的物理上限（µs）。两者是同一包内"LTF尖峰→header解完"
# 两个时刻，实测差固定 13~15µs。错位时两个 TSF 位置读到的是不相关 IQ 采样，差值随机，
# 用它做强校验：单靠 <2^48 在 TSF 值小时会把"高32位恰为0的 IQ 采样"误判成合法 TSF。
TSF_PAIR_GAP_MAX = 50

# ---- 块顺序（两种布局，由 --layout 选择）----
#   csi_first：历史（跨包错位 bug 时代的 bitstream，[CSI块][IQ块]）
#   iq_first ：当前（RTL 修复后，[IQ块][CSI块] 同包配对）——默认
LAYOUTS = {
    "csi_first": dict(OFF_TSF_CSI=0,   OFF_PHASE=1,   OFF_CSI=2,
                      OFF_TSF_IQ=CSI_BLK_LEN,              OFF_IQ=CSI_BLK_LEN + 1),
    "iq_first":  dict(OFF_TSF_IQ=0,    OFF_IQ=1,
                      OFF_TSF_CSI=1 + IQ_LEN,              OFF_PHASE=2 + IQ_LEN,
                      OFF_CSI=3 + IQ_LEN),
}

# ---- 参数解析 ----
args = [a for a in sys.argv[1:] if not a.startswith("--")]
CLEAN = "--clean" in sys.argv[1:]

_LAYOUT = "iq_first"
for a in sys.argv[1:]:
    if a.startswith("--layout="):
        _LAYOUT = a.split("=", 1)[1].strip()
if _LAYOUT not in LAYOUTS:
    raise SystemExit(f"未知 --layout={_LAYOUT}，可选 {list(LAYOUTS)}")
L = LAYOUTS[_LAYOUT]
OFF_TSF_CSI = L["OFF_TSF_CSI"]
OFF_PHASE = L["OFF_PHASE"]
OFF_CSI = L["OFF_CSI"]
OFF_TSF_IQ = L["OFF_TSF_IQ"]
OFF_IQ = L["OFF_IQ"]

OUT_ROOT = args[0] if len(args) > 0 else r"C:\iq"
DURATION = int(args[1]) if len(args) > 1 else 30
PORT = 4000


# ================= 解析核心（可独立回放测试） =================

def find_alignment(u64_list):
    """扫描 s∈[0,REC_WORDS)，返回使 TSF_csi 单调且数值合理的 record 起点。
    u64_list: 整段流的 64bit 数组。返回 (offset, 使用的record数) 或 None。"""
    n = len(u64_list)
    need = 4  # 至少 4 个 record 才可信
    if n < (need + 1) * REC_WORDS:
        return None
    for s in range(REC_WORDS):
        kmax = (n - s) // REC_WORDS
        if kmax < need:
            break
        kmax = min(kmax, 64)
        seq = [u64_list[s + k * REC_WORDS] for k in range(kmax)]
        if not all(v < TSF_MAX for v in seq):
            continue
        if all(seq[i] <= seq[i + 1] for i in range(kmax - 1)):
            # 验证另一块 TSF 也合理（排除巧合）：seq 已取 w[0]（iq_first 下是
            # TSF_iq、csi_first 下是 TSF_csi），这里再验另一块。iq_first 下若不验
            # TSF_csi，垃圾数据里会"侥幸"对齐到 w[0] 单调、但 w[441] 是垃圾的位置，
            # 导致 parse_record 失败 + 重同步死循环。
            other_off = OFF_TSF_CSI if OFF_TSF_CSI != 0 else OFF_TSF_IQ
            other_seq = [u64_list[s + k * REC_WORDS + other_off]
                         for k in range(kmax) if s + k * REC_WORDS + other_off < n]
            if other_seq and all(v < TSF_MAX for v in other_seq):
                # 同 record 差校验：两块 TSF 必须同包（差 ~13~15µs），排除"高32位
                # 恰为0的 IQ 采样被误判成合法 TSF"（否则 TSF 值小时 <2^48 区分度不够）。
                paired = True
                for k in range(len(other_seq)):
                    d = (other_seq[k] - seq[k]) if (other_off == OFF_TSF_CSI) else (seq[k] - other_seq[k])
                    if not (0 <= d <= TSF_PAIR_GAP_MAX):
                        paired = False
                        break
                if paired:
                    return (s, kmax)
    return None


def parse_record(u64_list, base):
    """解析 base 处的一条 record，返回 dict 或 None（若 TSF 不合理或非同包）。"""
    if base + REC_WORDS > len(u64_list):
        return None
    tsf_csi = u64_list[base + OFF_TSF_CSI]
    tsf_iq = u64_list[base + OFF_TSF_IQ]
    if tsf_csi >= TSF_MAX or tsf_iq >= TSF_MAX:
        return None
    # ★ 同包强校验：TSF_csi 与 TSF_iq 是同一包的两个时刻，差固定 ~13~15µs。
    # 错位时两个位置读到的是不相关 IQ 采样，差值随机 → 据此排除"高32位为0的假 TSF"
    # （否则 TSF 值小时，<2^48 校验区分度不够，会把错位的 IQ 采样误判成合法）。
    if not (0 <= tsf_csi - tsf_iq <= TSF_PAIR_GAP_MAX):
        return None
    phase = struct.unpack("<q", struct.pack("<Q", u64_list[base + OFF_PHASE]))[0]
    return {"tsf_csi": tsf_csi, "tsf_iq": tsf_iq, "phase": phase, "base": base}


def csi_iq_of_record(u64_list, base):
    """取一条 record 的 56 CSI 复数 (I,Q) 与 440 IQ 复数。
    每 word 低 32 位 = {I16, Q16}（小端：第1个int16=I，第2个=Q，与官方
    side_info_display.py 的取法一致）。"""
    csi, iq = [], []
    for i in range(CSI_LEN):
        w = u64_list[base + OFF_CSI + i]
        csi.append((w & 0xFFFF, (w >> 16) & 0xFFFF))          # 有符号性在 CSV 层转
    for i in range(IQ_LEN):
        w = u64_list[base + OFF_IQ + i]
        iq.append((w & 0xFFFF, (w >> 16) & 0xFFFF))
    return csi, iq


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


class SideChParser:
    """CSI+IQ 联合数据流的增量解析器（可独立实例化，便于离线回放测试）。

    职责：
      - feed(bytes) 累积字节流
      - 自对齐：扫描 record 起点（TSF 单调性）
      - 逐 record 解析，维护段号 / 跳变点
      - 回调 on_header(align) / on_record(idx, rec, csi) / on_event(str)
    不碰文件 I/O，产物落盘由调用方决定。
    """

    def __init__(self, on_header=None, on_record=None, on_event=None):
        self.stream = bytearray()
        self.align = None              # 首锁对齐 (offset, n_used)，不再被重同步改写
        self.next_base = 0             # 下一条待解析 record 的流内 word 起点
        self.prev_tsf_iq = None
        self.rec_count = 0
        self.seg_count = 1
        self.last_tsf_csi = None
        self.skip_pair = False
        self.resync_tried_words = 0
        self.resync_backoff = 5 * REC_WORDS   # 剩余数据 < 此值时不尝试重同步（等更多数据）
        self.pair_diffs = []
        self.csi_zero_recs = 0
        self.tsf_gaps = []
        self.on_header = on_header
        self.on_record = on_record
        self.on_event = on_event

    def _event(self, msg):
        if self.on_event:
            self.on_event(msg)

    def feed(self, data):
        self.stream += data
        self._parse()

    def _parse(self):
        u64n = len(self.stream) // 8
        if u64n < 5 * REC_WORDS:
            return
        u64 = struct.unpack_from(f"<{u64n}Q", self.stream, 0)

        if self.align is None:
            r = find_alignment(u64)
            if r:
                self.align = r
                self.next_base = r[0]
                if self.on_header:
                    self.on_header(r)
            else:
                return

        while self.next_base + REC_WORDS <= u64n:
            rec = parse_record(u64, self.next_base)
            if rec is None:
                # TSF 数值不合理（>2^48）→ 流内错位，逐条跳过坏区重新找对齐
                new_data = u64n - self.next_base
                if new_data < self.resync_backoff:
                    break
                self._event(f"# [重同步] rec {self.rec_count} 处 TSF 数值异常，"
                            f"跳过错位区找对齐（数据 {new_data} word）…\n")
                if self.next_base + 5 * REC_WORDS <= u64n:
                    sub = find_alignment(u64[self.next_base:])
                    if sub is not None:
                        # 找到对齐（sub[0] 可能为 0 = 当前位置即好区，无需前移）
                        if sub[0] > 0:
                            self.next_base += sub[0]
                        self.seg_count += 1
                        self.resync_backoff = 5 * REC_WORDS   # 复位
                        self._event(f"# [重同步] 新起点 = word {self.next_base}"
                                    f"（进入段 {self.seg_count}）\n")
                        continue
                    # find_alignment 找不到（错位区 ≥ 4 条 record，前 4 个候选位置
                    # 必落在坏区）：跳过一条 record 再找，可跨越任意长度错位区
                    self.next_base += REC_WORDS
                    continue
                break

            # TSF 跳变分段（合法现象：板子停发/重启）
            if self.last_tsf_csi is not None:
                d = rec["tsf_csi"] - self.last_tsf_csi
                if d < 0 or d > TSF_GAP_LIMIT:
                    self.seg_count += 1
                    self.tsf_gaps.append((self.rec_count, rec["tsf_csi"]))
                    self._event(f"# [分段] rec {self.rec_count} 处 TSF 跳变 {d} µs → "
                                f"段 {self.seg_count}\n")
            # 同 record 内 TSF 差（同包，~13~15µs）
            self.pair_diffs.append(rec["tsf_csi"] - rec["tsf_iq"])

            self.last_tsf_csi = rec["tsf_csi"]
            csi, _ = csi_iq_of_record(u64, self.next_base)
            if all(i == 0 and q == 0 for (i, q) in csi):
                self.csi_zero_recs += 1
            if self.on_record:
                self.on_record(self.rec_count, self.seg_count, rec, csi)
            self.rec_count += 1
            self.next_base += REC_WORDS

    def pair_ok_count(self):
        return sum(1 for d in self.pair_diffs if PAIR_WINDOW[0] <= d <= PAIR_WINDOW[1])


def main():
    # ================= 监听主流程 =================

    os.makedirs(OUT_ROOT, exist_ok=True)
    existing = [d for d in os.listdir(OUT_ROOT) if d.startswith("iq_")]
    if CLEAN:
        import shutil
        if existing:
            print(f"[win] ⚠ --clean：将删除 {len(existing)} 个旧产物目录", flush=True)
            for d in existing:
                shutil.rmtree(os.path.join(OUT_ROOT, d))
            print("[win] 已清理", flush=True)

    start_wall = datetime.now()
    stamp = start_wall.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, f"iq_{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, f"iq_{stamp}.bin")
    log_path = os.path.join(out_dir, f"iq_{stamp}.log")
    csv_path = os.path.join(out_dir, f"iq_{stamp}_csi.csv")

    print(f"[win] ★ 就绪：监听 0.0.0.0:{PORT}  时长 {DURATION}s", flush=True)
    print(f"[win] ★ 启动板端 e316_collect.sh", flush=True)
    print(f"[win] 产物目录: {out_dir}", flush=True)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.settimeout(1.0)

    stop_flag = {"v": False}


    def _on_signal(signum, frame):
        stop_flag["v"] = True
        print(f"\n[win] 收到信号 {signum}，提前收尾…", flush=True)


    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # ---- 统计状态 ----
    pkt_count = 0
    byte_count = 0
    pkt_lens = {}
    first_pkt_time = None
    last_pkt_time = None

    csv_file = None
    csv_header_written = False
    LOG_HEAD_END = 0            # 明细区末尾偏移（汇总区块从这里原地覆盖）

    # 解析器实例（闭包回调写入日志/CSV）
    parser = None


    def csv_open():
        nonlocal csv_file, csv_header_written
        if csv_file is None:
            csv_file = open(csv_path, "w", encoding="utf-8", buffering=1)
        if not csv_header_written:
            cols = ["rec_idx", "seg", "tsf_csi_us", "tsf_iq_us", "phase_offset"]
            for i in range(CSI_LEN):
                cols += [f"csi_{i:02d}_i", f"csi_{i:02d}_q"]
            cols += ["# note: csi 子载波序为硬件输出序(0..55)，映射到频域序参照 side_info_display.py 前后半段交换规则",
                     "# note: RTL 修复后，本 record 的 CSI 与 IQ 属同一包（tsf_csi - tsf_iq ≈ 13~15µs）"]
            csv_file.write(",".join(cols) + "\n")
            csv_header_written = True

    def write_csv_row(rec_idx, seg, rec, csi):
        csv_open()
        row = [str(rec_idx), str(seg), str(rec["tsf_csi"]), str(rec["tsf_iq"]), str(rec["phase"])]
        for (i, q) in csi:
            row += [str(s16(i)), str(s16(q))]
        csv_file.write(",".join(row) + "\n")

    def ensure_detail_zone(log):
        """明细写入前：若文件尾部有旧汇总区块，截断回明细区末尾（方案 A 原地覆盖）。"""
        nonlocal LOG_HEAD_END
        if log.tell() != LOG_HEAD_END:
            log.seek(LOG_HEAD_END)
            log.truncate()

    def w_detail(log, line):
        """写一行明细（自动维护明细区边界）。"""
        nonlocal LOG_HEAD_END
        ensure_detail_zone(log)
        log.write(line)
        log.flush()
        LOG_HEAD_END = log.tell()

    def write_summary(log):
        """汇总区块原地覆盖：truncate 回明细末尾再写。"""
        log.flush()
        log.seek(LOG_HEAD_END)
        log.truncate()
        dur = (last_pkt_time - first_pkt_time) if first_pkt_time else 0.0
        log.write("{" + "=" * 48 + "}\n")
        log.write("===== 汇总 =====\n")
        log.write(f"最后更新      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"UDP 总包数    : {pkt_count}\n")
        log.write(f"UDP 总字节    : {byte_count}\n")
        log.write(f"首包->末包    : {dur:.3f} 秒\n")
        log.write(f"包长分布      : {dict(sorted(pkt_lens.items(), key=lambda x: -x[1]))}\n")
        if parser is None or parser.rec_count == 0:
            log.write("record 数     : 0（未解析出任何完整 record）\n")
            log.write("对齐偏移      : 未锁定（数据不足或格式异常）\n")
        else:
            log.write(f"record 数     : {parser.rec_count}（解析出的完整条数）\n")
            if parser.align:
                log.write(f"对齐偏移      : word {parser.align[0]}（首锁值；流起点相对 record 起点的错位，"
                          f"首尾残段已丢弃）\n")
            log.write(f"TSF 跳变分段  : {len(parser.tsf_gaps)} 处 {parser.tsf_gaps[:5]}\n")
            log.write(f"CSI 全 0 记录 : {parser.csi_zero_recs}/{parser.rec_count}\n")
            if parser.pair_diffs:
                import statistics
                pd = [d for d in parser.pair_diffs if PAIR_WINDOW[0] <= d <= PAIR_WINDOW[1]]
                log.write(f"同包配对校验  : {len(pd)}/{len(parser.pair_diffs)} 条 TSF 差落在 "
                          f"{PAIR_WINDOW[0]}~{PAIR_WINDOW[1]}µs（中位数 "
                          f"{statistics.median(pd) if pd else 'N/A'}）\n")
            log.write("配对语义      : ★ 同包——record r 的 CSI 与 IQ 属同一包（差 ~13~15µs）\n")
        log.write(f"\n# 产物: {raw_path}\n#       {csv_path}\n")
        log.write("{" + "=" * 48 + "}\n")

    # ---- 解析器回调（把事件/record 落盘） ----
    def on_header(log, r):
        w_detail(log, f"# [对齐] 锁定 record 起点 = word {r[0]}（依据 {r[1]} 条 TSF 单调）\n")

    def on_record(log, idx, seg, rec, csi):
        iq_start = (rec["base"] + OFF_IQ) * 8
        iq_end = iq_start + IQ_LEN * 8
        w_detail(log, f"#rec{idx:<5d} 段{seg}  TSF_csi={rec['tsf_csi']:>12d}  "
                      f"TSF_iq={rec['tsf_iq']:>12d}  iq_bin={iq_start}..{iq_end}\n")
        write_csv_row(idx, seg, rec, csi)

    with open(raw_path, "wb") as raw, open(log_path, "w", encoding="utf-8", buffering=1) as log:
        log.write("# ===== Windows UDP 监听日志（CSI+IQ 联合采集） =====\n")
        log.write(f"# 开始时间 : {start_wall.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"# 监听地址 : 0.0.0.0:{PORT} (UDP)\n")
        log.write(f"# 监听时长 : {DURATION} 秒\n")
        log.write(f"# record   : {REC_WORDS} word = {REC_BYTES} B，布局/配对语义见脚本头部注释\n")
        log.write(f"# 产物目录 : {out_dir}\n")
        log.write("# ------------------------------------------------\n")
        log.flush()
        LOG_HEAD_END = log.tell()

        # 解析器实例：回调把事件/record 写进日志与 CSV
        parser = SideChParser(
            on_header=lambda r: on_header(log, r),
            on_record=lambda idx, seg, rec, csi: on_record(log, idx, seg, rec, csi),
            on_event=lambda msg: w_detail(log, msg),
        )

        start = time.time()
        while time.time() - start < DURATION and not stop_flag["v"]:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            now = time.time()
            if first_pkt_time is None:
                first_pkt_time = now
                first_pkt_wall = datetime.now()
            last_pkt_time = now
            pkt_count += 1
            byte_count += len(data)
            pkt_lens[len(data)] = pkt_lens.get(len(data), 0) + 1

            raw.write(data)
            raw.flush()

            parser.feed(data)

            if pkt_count % 10 == 0:
                write_summary(log)
                print(f"[win] ... {pkt_count} 包 / {byte_count} 字节 / "
                      f"{parser.rec_count} record", flush=True)

        write_summary(log)

    if csv_file:
        csv_file.close()

    print(f"\n[win] ===== 最终汇总 =====", flush=True)
    print(f"[win] UDP 包: {pkt_count}  字节: {byte_count}", flush=True)
    print(f"[win] record: {parser.rec_count if parser else 0}  "
          f"对齐: {parser.align if parser else None}", flush=True)
    print(f"[win] 包长分布: {pkt_lens}", flush=True)
    print(f"[win] 产物目录: {out_dir}", flush=True)


if __name__ == "__main__":
    main()
