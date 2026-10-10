#!/bin/bash
# push_run.sh <local_script>  -- base64 push a local script to board and run it
set -e
LOCAL="$1"
cd /mnt/c/OpenWiFi/tools
B64=$(base64 -w0 "$LOCAL")
python3 auto_ssh.py openwifi ssh root@192.168.10.122 "echo $B64 | base64 -d > /tmp/run.sh && sh /tmp/run.sh"
