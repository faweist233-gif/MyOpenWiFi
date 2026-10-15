#!/bin/bash
# 在 WSL 内启动仿真矩阵(与 ssh 会话脱钩),写 PID 到 launcher_pid.txt
# 注意: 匹配模式用 [.] 防自匹配(否则 pkill/pgrep 会命中自身外壳)
cd "$HOME/openofdm-sim/verilog" || exit 1
pkill -f 'run_sim_matrix[.]sh' 2>/dev/null
sleep 1
nohup setsid bash /mnt/c/OpenWiFi/openofdm/run_sim_matrix.sh \
    > "$HOME/openofdm-sim/run_all.log" 2>&1 < /dev/null &
sleep 2
if pgrep -f 'run_sim_matrix[.]sh' > /dev/null; then
    pgrep -f 'run_sim_matrix[.]sh' > "$HOME/openofdm-sim/launcher_pid.txt"
    echo "RUNNING pid=$(cat "$HOME/openofdm-sim/launcher_pid.txt")"
else
    echo "NOT_RUNNING"
    tail -5 "$HOME/openofdm-sim/run_all.log"
fi
