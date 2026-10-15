# openofdm sync_long 检测器修复（弱信号误触发/漏检根治）

> 状态：RTL 修改已完成，iverilog 仿真矩阵验证中，**尚未综合/上板**。
> 目标：解决「环境弱信号空口帧的 side_ch IQ 窗口无前导码」问题。

## 一、故障根因（详见排查记录）

`openofdm/verilog/sync_long.v` 的 LTF 检测器三处缺陷：

1. **metric 未归一化**：16 采样互相关模值随信号功率缩放，弱真峰与噪声大值无法用固定阈值区分；
2. **只验峰间距不验幅度**：两个峰相隔 62~66 采样即判定检测，噪声里随机极大值偶发满足；
3. **失败后卡死 S_IDLE**（空状态无退出），只能等 dot11 复位，期间全部漏检。

后果：检测器在弱信号/干扰下误触发或漏触发 → side_ch 的 IQ 窗口锁到与包无关的时刻（或陈旧窗口被环形 dpram 覆盖）→ record 的 440 采样 IQ 不含前导码（板端元数据 TSF/CSI 仍正常）。

## 二、修复内容（`sync_long_fix.patch`，3 处改动）

| 层 | 改动 | 作用 |
|---|---|---|
| A1 归一化判据 | 新增 16 采样滑动窗能量 `energy_sum`（与互相关同窗口）；峰值搜索只接受 `metric² ≥ REF²·E/4`（归一化相关 ≥0.25，真实 LTF≈1、白噪声均值 1/16） | 剔除噪声/干扰假峰 |
| A2 双峰一致性 | 接受条件追加两峰幅度互相 ≤1.5× | 剔除随机幅度假峰对 |
| A3 失败重装 | gap/幅度检查失败 → 回 `S_SKIPPING` 重新搜索（原卡死 `S_IDLE`） | 恢复漏检、自愈 |

**不变量**：检测成功时的 `long_preamble_detected` 断言时刻与流水延迟完全不变 → **C1 不变**（上板后仍建议注入法复核一次）。

## 三、验证仿真矩阵（iverilog，openofdm 自带 Makefile/dot11_tb）

工具（本目录）：

- `gen_stims.py` —— 基于 upstream 真实采集帧生成 5 组激励（Windows python + numpy 运行）：
  weak1(~6dB) / weak2(~−2dB) / noise(纯噪声) / cfo80k(80kHz 频偏) / cw_jam(CW 干扰+弱帧，模拟 ch5 场景)
- `run_sim_matrix.sh` —— 对「原版(sync_long.v.orig)」与「修复版」各跑 6 组（含 default），
  汇总每例的长前导检测次数 + L-SIG 解码结果到 `summary.txt`（WSL 内后台运行）
- `launch_sim.sh` —— WSL 内 nohup+setsid 脱钩启动矩阵
- `run_one.sh` + `sim_loop.ps1` —— **崩溃容错版**（推荐）：Windows 侧 PowerShell 循环逐例调用
  `wsl ... run_one.sh`，结果写 NTFS 的 `sim_summary.txt`；WSL 虚拟机崩溃/重启自动重试。

⚠️ 环境要点（2026-10-15 实测）：SSH 测试机可用内存 <5GB，WSL2 无内存上限（默认吃 8GB）
时 vvp 一加压虚拟机就崩溃重启（uptime 归零）。已在 `C:\Users\fawei\.wslconfig` 加
`memory=4GB / swap=4GB` 解决；跑 Vivado 综合时若 OOM 再临时调大。

判据：default/weak1/cfo80k 修复版应检出且 L-SIG 解出；noise **必须零检出**；weak2 观察边界。
已测基线：default 刺激（前 3000 采样含 2 帧：len=138 截头帧 + len=111）
原版与修复版均检出 2 帧、解出 2 个 L-SIG——default 不构成区分；
区分能力在 weak1/weak2/noise/cw_jam 组（仿真矩阵跑完见 sim_summary.txt）。

## 四、上板流程（仿真通过后）

1. WSL 里 Vivado 重综合 bitstream（沿用现有 `build_env.sh synth/bit/boot` 流程）；
2. 烧录 → `e316_deploy.sh`（pre_trigger 当前定标 280）；
3. 注入法复核 C1（`stf_start_rel` 应为 0；若非 0，`C1 = pre_trigger − stf_start_rel`）；
4. 环境空口实测：对比修复前后「窗口含前导码的 record 占比」。

## 五、回滚

`cd openofdm && git checkout -- verilog/sync_long.v`（或从 `sync_long.v.orig` 拷回）。
