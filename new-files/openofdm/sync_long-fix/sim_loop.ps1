# sim_loop.ps1 — Windows 侧驱动逐个跑仿真例,容忍 WSL 崩溃重启
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File sim_loop.ps1
$ErrorActionPreference = 'Continue'
$cases = @('default','weak1','weak2','noise','cfo80k','cw_jam')
$log = 'C:\OpenWiFi\openofdm\sim_loop.log'
$sum = 'C:\OpenWiFi\openofdm\sim_summary.txt'

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') loop start" | Out-File -Append -Encoding utf8 $log

foreach ($v in @('base','fix')) {
  foreach ($c in $cases) {
    $ok = $false
    for ($try = 1; $try -le 4 -and -not $ok; $try++) {
      & wsl -d Ubuntu-22.04 -u root -- bash /mnt/c/OpenWiFi/openofdm/run_one.sh $c $v 2>&1 | Out-Null
      $rc = $LASTEXITCODE
      if ($rc -eq 0) {
        $ok = $true
      } else {
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $v/$c try$try rc=$rc" | Out-File -Append -Encoding utf8 $log
        Start-Sleep -Seconds 8
      }
    }
    if (-not $ok) {
      "$(Get-Date -Format 'HH:mm:ss') $v/$c GIVE_UP" | Out-File -Append -Encoding utf8 $sum
    }
  }
}
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') loop done" | Out-File -Append -Encoding utf8 $log
