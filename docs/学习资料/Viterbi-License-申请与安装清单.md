# Viterbi Decoder 评估 License 申请与安装清单

> 生成时间：2026-10-09
> 状态：**✅ license 已申请、已安装、已验证通过**
> 目标：为 AntSDR E316 (e310v2 / xc7z020clg400-1) 的 openwifi FPGA 综合解除 viterbi IP 阻塞

---

## 零、当前状态速览

| 项目 | 状态 |
|---|---|
| license 文件 | `C:\Users\fawei\Downloads\Xilinx.lic`（3099 字节） |
| HOSTID | `7cb27db968c4` ✔ 匹配物理网卡 `7C-B2-7D-B9-68-C4` |
| 有效期 | 2026-10-08 → **2027-02-06**（120 天评估期） |
| 安装位置 | `/opt/Xilinx/licenses/Xilinx.lic` |
| 环境变量 | `/etc/profile.d/xilinx-license.sh` |
| IP 解锁验证 | ✅ `IS_LOCKED = 0`，`upgrade_ip` Rev.10 → Rev.13 SUCCESS |

---

## 一、为什么需要这个 license

openwifi 的 `openofdm_rx` IP 里硬编码实例化了 Xilinx 的 **Viterbi Decoder** IP
（`ip/openofdm_rx/verilog/ofdm_decoder.v:189,202`），这是**付费 IP**，
但 Xilinx/AMD 提供**免费评估 license**。

`openwifi-hw/README.md` 里板卡表的 `Vivado license` 列（e310v2 标 `NO need`）
指的是**Vivado 工具本身**不需要付费授权（因为 Zynq-7020 在免费版覆盖范围内），
**不包括 viterbi IP**。README 第 49 行明确要求所有板子都要装 viterbi 评估 license。

---

## 二、★★★ 核心结论：三个条件必须同时满足

这是整个排查过程最重要的发现。**单独任何一个都不够。**

| # | 条件 | 缺了会报什么错 | 怎么做 |
|---|---|---|---|
| **①** | **Viterbi 评估 license** | `IP requires one or more mandatory licenses but no valid licenses were found` | 装 `/opt/Xilinx/licenses/Xilinx.lic` |
| **②** | **IP 器件与项目匹配** | `Current project part 'xc7z020clg400-1' and the part 'xc7z045ffg900-2' ... do not match` | 改 xci 的 DEVICE/PACKAGE/SPEEDGRADE/BASE_BOARD_PART |
| **③** | **执行 `upgrade_ip`** | IP 保持 `IS_LOCKED = 1` | `upgrade_ip [get_ips] -log <file>` |

**三者齐备后的成功标志：**
```
INFO: [IP_Flow 19-3422] Upgraded viterbi_v7_0 (Viterbi Decoder 9.1) from revision 10 to revision 13
SUCCESS in the upgrade of viterbi_v7_0 (xilinx.com:ip:viterbi:9.1) from (Rev. 10) to (Rev. 13)
IS_LOCKED = 0
WARNING: [IP_Flow 19-650] IP license key 'viterbi@2014.10' is enabled with a Hardware_Evaluation license.
```
> 最后那条 WARNING 是**好消息**（表示正在合法使用评估 license），不是错误。

### 条件 ② 的正确做法（有坑）

⚠️ **`read_ip` 会原地改写 `.xci` 的格式**，务必在 `read_ip` **之前**用 `sed` 改原始文件：

| 阶段 | 大小 | 格式 | 字段写法 |
|---|---|---|---|
| 原始文件 | 27422 B | **SPIRIT XML** | `<spirit:...referenceId="PROJECT_PARAM.DEVICE">xc7z045</spirit:...>` |
| `read_ip` 后 | 18549 B | **JSON** | `"DEVICE": [ { "value": "xc7z020" } ],` |

```bash
XCI=~/git/openwifi-hw/ip/openofdm_rx/verilog/Xilinx/zynq/viterbi/viterbi_v7_0.xci
cp -f "$XCI" "$XCI.orig"    # 务必备份

sed -i \
  -e 's|\(PROJECT_PARAM.DEVICE">\)xc7z045|\1xc7z020|' \
  -e 's|\(PROJECT_PARAM.PACKAGE">\)ffg900|\1clg400|' \
  -e 's|\(PROJECT_PARAM.SPEEDGRADE">\)-2|\1-1|' \
  -e 's|\(PROJECT_PARAM.BASE_BOARD_PART">\)xilinx.com:zc706:part0:1.2|\1|' \
  "$XCI"
```

改动会被保留 —— `openofdm_rx.tcl:138-141` 会从
`verilog/Xilinx/zynq/viterbi/`（**改过的源文件**）复制到 `ip_repo/`。

---

## 三、申请前必须确认的环境状态

| 项目 | 要求值 | 当前状态 |
|---|---|---|
| 网络模式 | `networkingMode=mirrored` | ✅ 已配置并验证 |
| 用于申请的 MAC | **`7C-B2-7D-B9-68-C4`** | ✅ 物理网卡 WLAN，永久稳定 |
| MAC 稳定性 | 冷启动后不变 | ✅ 两次冷启动实测一致 |
| Clash TUN 模式 | 必须关闭 | ✅ `tun.enable=false` |
| 外网连通 | 能访问 xilinx.com | ✅ 已验证 |

**为什么必须用 `7C-B2-7D-B9-68-C4`：**

license 绑定的是 Vivado 所在机器的网卡 MAC。Vivado 跑在 WSL 里，
在 **mirrored 网络模式**下，WSL 的 `eth3` 直接复用 Windows 物理网卡，
MAC = `7C-B2-7D-B9-68-C4`，且**每次重启都保持不变**。

> ⚠️ 如果之后把 `.wslconfig` 的 `networkingMode` 改回 `nat`，
> eth0 的 MAC 会变成每次随机，**license 立刻失效**。

验证命令（WSL 内执行）：
```bash
/lib64/ld-linux-x86-64.so.2 \
  /opt/Xilinx/Vivado/2022.2/bin/unwrapped/lnx64.o/lmutil lmhostid
```
期望输出（第一个 host ID 即为要填的 MAC）：
```
The FlexNet host ID of this machine is ""7cb27db968c4 00155d9d9a47 005056c00008 b6bf532ec421 005056c00001""
```

---

## 三、申请步骤（7 步）

### Step 1 — 打开产品页
```
https://www.xilinx.com/products/intellectual-property/viterbi_decoder.html
```
需要先登录 AMD/Xilinx 账号（免费注册）。

### Step 2 — 点左侧 `Evaluate`
页面左侧导航栏有 **Evaluate** 入口。

### Step 3 — 勾选并创建
在弹出的产品列表里找到 **Viterbi Decoder**，勾选该行
（该行方框默认可能已勾选，若没有请手动勾），然后点 **Create License**。

### Step 4 — 填写主机信息 ★关键步骤
| 字段 | 填写值 |
|---|---|
| System / OS | **Linux** |
| Ethernet MAC | **7C-B2-7D-B9-68-C4** |

> ⚠️ MAC 必须与前面 `lmutil lmhostid` 输出的第一项一致（去掉冒号写或不写均可，
> 表单一般接受 `XX-XX-XX-XX-XX-XX` 格式）。
>
> ⚠️ **不要**填物理网卡的 Windows 侧值之外的任何值。
> 若表单要求 hostname，填 `LoF`。

### Step 5 — 提交
`Next` → 勾选 `Agree` → 一路 `Next` 到完成。

### Step 6 — 下载 license 文件
生成是**即时**的。点页面左下角的**下载箭头图标**，下载 `.lic` 文件。
同时会发一份到你注册邮箱。

### Step 7 — 安装到 Vivado

**推荐做法**（放到独立目录，避免 Vivado 升级时被重置）：
```bash
sudo mkdir -p /opt/Xilinx/licenses
sudo cp ~/Downloads/Xilinx.lic /opt/Xilinx/licenses/

# 全局生效（写入 profile.d，所有 shell 都能读到）
echo 'export XILINXD_LICENSE_FILE="/opt/Xilinx/licenses/Xilinx.lic"' | \
  sudo tee /etc/profile.d/xilinx-license.sh
sudo chmod +x /etc/profile.d/xilinx-license.sh
```

**不要**放到 `$XILINX_VIVADO/data/licenses/` —— 该目录在 Vivado 每次升级时会被覆盖重置。

---

## 四、验证 license 是否生效

```bash
source /opt/Xilinx/Vivado/2022.2/settings64.sh
export XILINXD_LICENSE_FILE=/opt/Xilinx/licenses/Xilinx.lic
echo "XILINXD_LICENSE_FILE = $XILINXD_LICENSE_FILE"
```

**最小化验证脚本**（约 1 分钟，避免白等 1-3 小时的综合）：
```tcl
create_project -force vit_test ./vit_test -part xc7z020clg400-1
read_ip <path>/viterbi_v7_0.xci
set ip [get_ips viterbi_v7_0]
puts "IS_LOCKED = [get_property IS_LOCKED $ip]"     ;# 必须为 0
upgrade_ip [get_ips] -log ./upgrade.log
puts "IS_LOCKED = [get_property IS_LOCKED $ip]"     ;# 升级后应为 0
generate_target all [get_ips]
```

现成脚本：`tools/test_upgrade_ip.tcl` + `tools/run_test_upgrade.sh`

**本机验证结果（已通过）**：
```
IPDEF     = xilinx.com:ip:viterbi:9.1
IS_LOCKED = 1  →  upgrade_ip  →  IS_LOCKED = 0    ✔
Upgrade Log: SUCCESS from (Rev. 10) to (Rev. 13)
```

---

## 五、license 到位后的后续步骤

```bash
# 1. 重新生成 IP repo（约 20 分钟）
#    ★必须在 boards/e310v2/ 下运行！（ip_repo_gen.tcl 用 pwd 末段作为 BOARD_NAME）
cd ~/git/openwifi-hw/boards/e310v2
source /opt/Xilinx/Vivado/2022.2/settings64.sh
export XILINXD_LICENSE_FILE=/opt/Xilinx/licenses/Xilinx.lic
vivado -mode batch -nolog -nojournal -source ../ip_repo_gen.tcl

# 2. 完整 Vivado 综合 + 实现（1-3 小时）
bash /home/faweist/run_vivado_synth.sh

# 3. 导出 XSA 后更新 SDK
cd ~/git/openwifi-hw/boards
./../sdk_update.sh e310v2 <OPENWIFI_HW_IMG_DIR>

# 4. 生成 bit.bin
cd ~/git/openwifi/user_space
./boot_bin_gen.sh /opt/Xilinx e310v2 <xsa_path>
```

> ⚠️ **内存提示**：本机 WSL 只有 8GB。`openwifi.tcl:306` 的
> `launch_runs impl_1 -to_step write_bitstream -jobs 8` 有 OOM 风险，
> **已改为 `-jobs 3`**（备份 `openwifi.tcl.orig`）。

---

## 六、已知限制

| 限制 | 说明 | 应对 |
|---|---|---|
| 板上运行 ~2 小时后停摆 | 评估 license 的时间限制，Viterbi 解码器停止工作 | 重载 FPGA bitstream 或断电重启（`openwifi/README.md:116`） |
| 有效期 120 天 | 2026-10-08 → 2027-02-06 | 到期前重新走一次申请流程（可无限次续期） |
| license 绑定 MAC | MAC 变了 license 失效 | **不要**改回 `networkingMode=nat` |

---

## 七、当前环境快照

```
WSL 发行版     : Ubuntu-22.04
WSL 版本       : 2.7.14.0 / 内核 6.18.33.2-2
hostname       : LoF
Vivado         : v2022.2 (64-bit) @ /opt/Xilinx/Vivado/2022.2
license 文件   : /opt/Xilinx/licenses/Xilinx.lic  ✔ 已安装
环境变量       : XILINXD_LICENSE_FILE=/opt/Xilinx/licenses/Xilinx.lic
网络模式       : mirrored（.wslconfig）
物理网卡 MAC   : 7C-B2-7D-B9-68-C4 (Intel Wireless-AC 9560)
```

待办勾选：
- [x] 申请 license（填入 MAC `7C-B2-7D-B9-68-C4`）
- [x] 下载 `.lic` 文件
- [x] 安装到 `/opt/Xilinx/licenses/` 并设环境变量
- [x] 验证 license 生效（`IS_LOCKED = 0`）
- [ ] 改 xci 器件为 xc7z020（**已完成**，待最终确认）
- [ ] 重跑 ip_repo_gen（**进行中**）
- [ ] 跑完整综合
