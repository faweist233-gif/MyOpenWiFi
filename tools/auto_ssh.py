#!/usr/bin/env python3
"""
auto_ssh.py — 用 python3 pty 自动喂 SSH 密码（替代 sshpass/expect）

用法:
  python3 auto_ssh.py <password> ssh  <user@host> "<command>"
  python3 auto_ssh.py <password> scp  <local> <user@host>:<remote>
  python3 auto_ssh.py <password> put  <local> <user@host>:<remote>      # 同上
  python3 auto_ssh.py <password> get  <user@host>:<remote> <local>

原理: 用 pty 开一个伪终端跑 ssh/scp，检测到 password: 提示时写入密码。
"""
import os
import pty
import select
import sys
import time

# 无活动超时（秒）：采集类命令（side_ch_ctl g100 后台 + sleep N）在 sleep 期间
# 无任何 stdout 输出，默认 30s 会被误判为"卡在交互 shell"而中断。可用环境变量
# AUTO_SSH_IDLE_TIMEOUT 覆盖（一键脚本里设为 采集时长 + 余量）。
IDLE_TIMEOUT = int(os.environ.get("AUTO_SSH_IDLE_TIMEOUT", "30"))

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
]


def run_with_password(argv, password, timeout=600, verbose=False):
    """跑 argv，遇 password 提示自动喂密码。返回 (exit_code, output)"""
    pid, fd = pty.fork()
    if pid == 0:
        # 子进程
        os.execvp(argv[0], argv)
        os._exit(127)

    output = b""
    pw_sent = 0
    start = time.time()
    last_activity = start
    max_password_prompts = 5

    while True:
        if time.time() - start > timeout:
            output += b"\n[TIMEOUT]\n"
            try:
                os.kill(pid, 9)
            except Exception:
                pass
            break

        r, _, _ = select.select([fd], [], [], 1.0)
        if r:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            output += data
            last_activity = time.time()

            if verbose:
                sys.stderr.buffer.write(data)
                sys.stderr.buffer.flush()

            low = data.lower()
            if b"password:" in low or b"password for" in low:
                if pw_sent < max_password_prompts:
                    time.sleep(0.15)
                    os.write(fd, password.encode() + b"\n")
                    pw_sent += 1
        else:
            # 检查子进程是否已退出
            try:
                wpid, status = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    # 再读一次残留
                    try:
                        while True:
                            d = os.read(fd, 4096)
                            if not d:
                                break
                            output += d
                    except OSError:
                        pass
                    code = os.waitstatus_to_exitcode(status)
                    os.close(fd)
                    return code, output
            except ChildProcessError:
                break

            # 长时间无活动且已喂过密码，可能卡在交互 shell
            if pw_sent > 0 and time.time() - last_activity > IDLE_TIMEOUT:
                break

    try:
        os.close(fd)
    except Exception:
        pass
    try:
        wpid, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
    except Exception:
        code = -1
    return code, output


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    password = sys.argv[1]
    mode = sys.argv[2]
    args = sys.argv[3:]

    if mode == "ssh":
        if len(args) < 1:
            print("need user@host [command]")
            return 2
        target = args[0]
        cmd = args[1] if len(args) > 1 else None
        argv = ["ssh"] + SSH_OPTS + [target]
        if cmd:
            argv.append(cmd)
        code, out = run_with_password(argv, password)
        sys.stdout.buffer.write(out)
        return code

    elif mode == "scp":
        if len(args) < 2:
            print("need <src> <dst>")
            return 2
        argv = ["scp"] + SSH_OPTS + args
        code, out = run_with_password(argv, password)
        sys.stdout.buffer.write(out)
        return code

    else:
        print(f"unknown mode: {mode}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
