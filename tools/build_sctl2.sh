set -e
BASE=/opt/Xilinx/Vitis/2022.2/gnu/aarch32/lin/gcc-arm-linux-gnueabi
SYSROOT=$BASE/cortexa9t2hf-neon-xilinx-linux-gnueabi
SRC=/mnt/c/OpenWiFi/openwifi/user_space/side_ch_ctl_src

echo "=== sysroot glibc 版本 ==="
strings $SYSROOT/lib/libc.so.6 2>/dev/null | grep -oE "GNU C Library.*version [0-9.]+" | head -1

echo "=== 尝试1: 静态链接（不依赖板端 glibc）==="
"${BASE}/bin/arm-linux-gnueabihf-gcc" -O2 -Wall -static -o /mnt/c/OpenWiFi/build-output/side_ch_ctl_static "$SRC/side_ch_ctl.c" 2>&1 | head -20
if [ -f /mnt/c/OpenWiFi/build-output/side_ch_ctl_static ]; then
  echo "静态构建 OK"
  file /mnt/c/OpenWiFi/build-output/side_ch_ctl_static
  ls -la /mnt/c/OpenWiFi/build-output/side_ch_ctl_static
  strings /mnt/c/OpenWiFi/build-output/side_ch_ctl_static | grep -iE "Missing server" | head -2
fi
