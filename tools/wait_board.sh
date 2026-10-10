#!/bin/bash
# Wait for board SSH port to come up
for i in $(seq 1 24); do
  if timeout 5 bash -c "echo > /dev/tcp/192.168.10.122/22" 2>/dev/null; then
    echo "UP after $((i*10-10))s"
    exit 0
  fi
  sleep 10
done
echo "STILL DOWN after 230s"
exit 1
