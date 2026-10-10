import socket, time, sys
PORT=4000
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(("0.0.0.0",PORT))
# 超时 = 采集时长 + 余量，默认 120s；可用第 1 个参数覆盖（如 python3 wsl_listen_4000.py 60）
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 120
s.settimeout(TIMEOUT)
n=0; total=0; t0=time.time()
print("[wsl] listening 0.0.0.0:%d"%PORT, flush=True)
while True:
    try:
        d,a=s.recvfrom(65536)
    except socket.timeout:
        print("[wsl] TIMEOUT %.1fs: %d pkts / %d bytes"%(time.time()-t0,n,total), flush=True); break
    n+=1; total+=len(d)
    if n<=10:
        print("[wsl] #%d from %s:%d len=%d head=%s"%(n,a[0],a[1],len(d),d[:16].hex()), flush=True)
    elif n%50==0:
        print("[wsl] ... %d pkts %d bytes"%(n,total), flush=True)
print("[wsl] DONE %d pkts / %d bytes / %.1fs"%(n,total,time.time()-t0), flush=True)
