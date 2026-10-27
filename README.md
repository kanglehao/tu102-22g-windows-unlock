# 在 Windows 上解锁 TU102 22G 被砍半的 Tensor Core（完整流程 + 实测数据）

> Restoring the disabled half of the FP16→FP32 Tensor Core accumulate rate on TU102
> (RTX 2080 Ti 22G / CMP 50HX class boards) **on Windows**, with every step measured.

---

## 如果你只记住一件事

> **在 Windows 上，缺了 `EnableGpuFirmware=1` 这一步，解锁后必然报 Code 43 或根本不生效。**
> 寄存器写得再漂亮也没用。这一步要写在**每张显卡各自的**注册表子键里（见下文）。

我们走了整整两轮弯路才找到它：第一次两卡都成功写入 `SS0=0x88888888 / SS1=0x8`，进 Windows 后全报 **代码 43**；
后来误以为是 GSP 复位问题，加了 PCIe FLR —— 结果驱动正常了、**解锁却被 FLR 一起抹掉**。真凶始终只是那个没写的注册表值。

---

## 这是什么 / 能得到什么

TU102 有一部分量产卡（典型的 22G 改卡）在固件里把 Tensor Core 的 **FP16→FP32 累加** 通路砍掉一半。
固件在 OS 启动前通过 `SS0` / `SS1` 两个"速度选择"寄存器完成这个限制。本项目在 **UEFI pre-OS 阶段**把这两个寄存器
改回满速值，然后**不重启显卡**直接把控制权交给 Windows（chainload 到 `bootmgfw.efi`）。

结果用寄存器级微基准 (`bench/mma_peak.cu`) 实测：

| 卡 | 解锁前（ FP16→FP32 ） | 解锁后 | 变化 | FP16→FP16 对照组（不变） |
|---|---|---|---|---|
| #0（MSI 37111462） | 65.12 TFLOPS（比值 0.504） | **126.72 TFLOPS（比值 0.964）** | **+94.6%** | 129.2 → 131.5 |
| #1（公版 12A410DE） | 67.26 TFLOPS（比值 0.510） | **129.41 TFLOPS（比值 0.980）** | **+92.4%** | 131.8 → 132.1 |

比值 = `FP16→FP32 累加 / FP16→FP16 累加`。原厂约 0.50（正好砍半），满速趋近 1.0。

**重要：解锁是易失的。** 不写 VBIOS、不动熔丝。断电或冷启即回到原厂砍半态，
所以**每次开机都要跑一次解锁程序**。本仓库提供把它排到开机首位的脚本，开机自动完成（多等 30~60 秒）。

---

## 但是：算力翻倍 ≠ 端到端翻倍

这是最容易误判的地方，我们把真实数据摊开讲清楚。本机跑 strata 引擎（Qwen3.8-Flash-Next，IQ3_XXS MoE，双卡 layer split）：

| 负载 | 原厂锁定态 | 解锁后 | 变化 |
|---|---|---|---|
| prefill（52,083 token 冷读，零 KV 复用） | 1730~1806 tok/s（历史稳定簇） | **1952 tok/s**（1953/1973/1930） | **约 +8%~13%** |
| decode | 55~90 tok/s | 57~77 tok/s | **无变化** |

为什么只涨一成？引擎日志自己写明：

```
strata serve: layer split: 85% of the experts resident, the prompt path's streamed ring 384 slots
```

prompt 路径上仍有 15% 的专家要经 PCIe 流式读取，prefill 的瓶颈在**显存/PCIe 带宽与专家调度**，不在 GEMM 算力；
decode 更是纯带宽受限（专家缓存命中率 95.9%~99.7%）。

> **收益 ≈ 算力提升 × 算力在总耗时里的占比。**
> 只有 GEMM 密集的负载（大 batch 训练、纯稠密矩阵、吞吐导向的批处理）才吃得到接近翻倍的收益。
> 想知道你的负载能不能吃到，先跑 `bench/mma_peak` 确认硬件层确实生效，再按 `bench/run_bench.py` 的量法测端到端。

---

## 适用与不适用

**适用**：PCI ID `10DE:1E07` 的 TU102 22G 卡（本仓库在两张不同品牌、不同 VBIOS 版本
`90.02.17.00.04` 与 `90.02.17.00.3E` 上验证通过，两卡 FWSEC 逐字节相同）。

**不适用 / 请绕行**：
- TU106（如 CMP 40HX）→ 去看 `CMP40HX-Unlock-OnlyEFI`。**但别照搬它的 FLR 收尾**，见 [`docs/flr-pitfall.md`](docs/flr-pitfall.md)。
- GA10x /  newer → 架构不同，本项目不适用。

---

## 前置条件与风险（请先读完）

| 项目 | 要求 |
|---|---|
| Secure Boot | **必须关闭**（EFI 应用未经签名） |
| 权限 | Windows 管理员（要写 UEFI 固件变量） |
| 备用显示 | 强烈建议保留一条 USB/虚拟显示或核显输出，万一黑屏还能看到桌面 |
| NVRAM | 脚本每次写启动项前都会备份到 `nvram_backup/<时间戳>/` |
| 编译环境 | 一台 Linux（本机 Windows 不能编出 EFI **PE32+**）。服务器、VM、Docker 均可 |
| 重启次数 | 正常 2~3 次；排障时可能更多 |

**风险提示**：本项目会改写 UEFI 启动变量、在 pre-OS 阶段操作 GPU 内部状态。虽然不写 VBIOS、不动熔丝，
但操作不当可能出现需要**断电冷启**才能恢复的情况。请确认你能接受再来。

**千万别做的一件事**：**在解锁态下用设备管理器禁用再启用显卡。** 实测会直接把机器搞挂
（2026-10-27 一次死机，就是这么来的）。要恢复请走下面的 FLR 恢复版，或者在 tools 菜单里选恢复项。

---

## 完整流程

### 步骤 0 · 确认硬件匹配

```powershell
Get-PnpDevice -Class Display | Select FriendlyName, InstanceId, Status
```

`InstanceId` 里应包含 `VEN_10DE&DEV_1E07`。型号不同请参考 [`docs/geometry-constants.md`](docs/geometry-constants.md)
自行推算 FWSEC 几何常量。

### 步骤 1 · 写 GSP 开关（这步不能漏）

管理员 PowerShell：

```powershell
$cls = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
Get-ChildItem $cls | Where-Object { $_.PSChildName -match '^\d{4}$' } | ForEach-Object {
    $d = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    if ($d.DriverDesc -match 'NVIDIA') {
        New-ItemProperty -Path $_.PSPath -Name 'EnableGpuFirmware' -PropertyType DWord -Value 1 -Force | Out-Null
        "set $($_.PSChildName)  $($d.DriverDesc)"
    }
}
```

回读应为 `EnableGpuFirmware = 1`；要求是**每张卡各自的子键**（本机是 `0000` 和 `0001`），
不能只写一处，也不在 `Services\nvlddmkm` 下。

### 步骤 2 · 编译

在 Linux 机器上（本机无 gnu-efi，Windows 侧编不了这种 PE32+ 目标）：

```bash
sudo apt install -y gnu-efi build-essential

# blob 准备：见 src/blobs/README.md
# 用自己的 FWSEC 时必定与脚本里的哈希不符 —— 必须放行：
TU102_ALLOW_FWSEC=1 bash build.sh        # -> TU102UNLK.EFI  （日常使用的解锁版）
ENABLE_FLR=1 TU102_ALLOW_FWSEC=1 bash build.sh   # -> 恢复版（不日常用，见下）
```

`build.sh` 里硬编码的 FWSEC 哈希 `c6fa28fa…` 是原作者那张卡的，你用自己的 dump 时**必然不匹配**，
用 `TU102_ALLOW_FWSEC=1` 放行即可（脚本会打印清楚该怎么办）。注意控制台那句 banner 始终显示 `c6fa28fa`，
那是上游的字符串，不代表实际编进去的内容。

新版本 binutils（2.46+）移除了 `-O efi-app-x86_64`，本仓库的 `build.sh` 已改用
`-O pei-x86-64 --subsystem=efi-app --image-base=0`，不用自己改。

### 步骤 3 · 装到 ESP（不需要 U 盘）

Windows 管理员命令行，在本仓库目录下：

```bat
python win\switch_bootnext.py            :: 列出全部 TU102 启动项
python win\set_bootorder.py              :: 预演：把解锁项排到开机首位
python win\set_bootorder.py --apply      :: 写入（自动备份 NVRAM）
```

所有 NVRAM 读写都通过 Python `ctypes` 直接调 `GetFirmwareEnvironmentVariableExW`，
**不需要 EasyUEFI 之类的第三方工具**，也不需要挂载 ESP。

### 步骤 4 · 第一次验证（一次性引导）

```bat
python win\switch_bootnext.py               :: 先列一遍，看本机的编号是多少
python win\switch_bootnext.py unlock        :: 或用用途关键字，免去记编号
shutdown /r /t 0
```

> Boot#### 的编号每台机器都不一样（`0000` 之后的编号取决于这台机器上建过多少启动项），
> 上面的 `unlock` 关键字会按用途自动选中对的那个；想手动给编号就用 `switch_bootnext.py 0x0085` 这种形式。
> 不确定识别得对不对，先用不带参数的命令列一遍。

开机画面会跑解锁程序，两卡各输出一行：

```
*** UNLOCKED (SS0=0x88888888 SS1=0x8) ***
```

然后自动进入 Windows。日志在 ESP 上的 `50hx_unlk.txt`：

```bat
python win\esp_read_logs.py              :: 挂载 ESP 取回
python win\esp_logs_to_utf8.py           :: 转 UTF-8 便于查看
```

### 步骤 5 · 验证真的生效了

```bat
:: 1) GSP 必须启用（不再是 N/A）
nvidia-smi --query-gpu=index,gsp.mode.current,gsp.firmware_version --format=csv

:: 2) 设备管理器不能有代码 43
Get-PnpDevice -Class Display | Select Status, ProblemDescription

:: 3) 算力：比值应接近 1.0（原厂约 0.50）
mma_peak.exe 0
mma_peak.exe 1
```

编译 `mma_peak.cu`：

```bat
:: 见 bench/mma_peak.cu 头部注释；Turing 只能用 m16n8k8 形状
nvcc -arch=sm_75 -O2 mma_peak.cu -o mma_peak.exe
```

> **坑**：Turing 只有 `mma.sync.aligned.m16n8k8`。写成 Ampere 的 `m16n8k16` 会编译失败，
> 拿 sm_80 的例子直接改 `arch` 是常见的错误。

三项全部通过后，再把解锁项排到开机首位（`win\set_bootorder.py --apply`），
以后每次开机自动解锁、自动进 Windows。

---

## 恢复与退路

| 症状 | 处置 |
|---|---|
| 开机卡在解锁程序没进系统 | 开机按 **F11**（MSI 主板）调出启动菜单，选 Windows Boot Manager 直接进系统 |
| 想临时回到原厂 | `win\switch_bootnext.py win` 一次性进 Windows，或 `set_bootorder.py --apply` 反过来把 Windows 放回首位 |
| 驱动报 **代码 43** / 黄叹号 | 大概率是 `EnableGpuFirmware` 没写给**每一张卡**，回到步骤 1 检查；仍不行则用 FLR 恢复版把卡复位回干净出厂态 |
| 完全不想折腾了 | 把启动项删掉即可，卡本身没有任何永久改动 |

**FLR 恢复版**（`ENABLE_FLR=1` 构建）存在的唯一意义，就是给上面第 3 行兜底：它对 GPU 发一次 PCIe Function Level Reset，
把 SS0/SS1 连同各种残留状态一起推回出厂值。**代价是解锁也被清掉**，所以它不适合日常使用，
但它替代了"拔电源线 + 按住开机键放余电"的物理断电操作。

还有一个可选的桌面菜单（把状态/测算力/开关自动解锁/Code43 恢复都做进去了）：

```
win\tu102_boot.py            :: 直接跑（非管理员会自动请求提权）
```

配套的桌面入口只要一个 3 行的 `.cmd` 转发即可；注意 **`.cmd` 内容必须保持纯 ASCII** ——
cmd.exe 按本地代码页逐字节解析批处理，UTF-8 中文会让它把命令拆错。中文一律放到 Python 里输出。

---

## 目录结构

```
.
├── src/
│   ├── unlock50x_v1.c     改动后的解锁程序源码（双卡 + TU102 几何常量 + 独立日志）
│   ├── build.sh           构建脚本（binutils 2.46 适配 + FLR 开关）
│   ├── extract_fwsec.py   从你自己的 PROM dump 里提取 FWSEC
│   ├── PORT-NOTES.md      上游移植说明
│   ├── LICENSE-40HX-UNLOCK
│   └── blobs/README.md    blob 怎么来（不随仓库分发 NVIDIA 固件）
├── win/
│   ├── espvar.py                UEFI 固件变量读写核心（ctypes）
│   ├── switch_bootnext.py       一次性引导 / 列启动项
│   ├── set_bootorder.py         把解锁项排到开机首位 / 恢复 Windows 优先
│   ├── esp_read_logs.py         挂载 ESP 取回日志
│   ├── esp_logs_to_utf8.py      日志转 UTF-8
│   └── tu102_boot.py            桌面菜单（状态 / 测速 / 恢复）
├── bench/
│   ├── mma_peak.cu        寄存器级 HMMA 峰值微基准（几乎不占显存）
│   └── run_bench.py       固定 prompt 的 prefill 对照测速
└── docs/
    ├── geometry-constants.md     9 个 FWSEC 几何常量怎么从你自己的卡推出来
    ├── windows-gsp-and-code43.md 代码 43 的完整因果链
    ├── flr-pitfall.md            为什么 TU102 不能用 40HX 的 FLR 收尾
    └── measurement.md            怎么量才不会自欺欺人
```

---

## 可复现性保证

本仓库的默认构建与作者机器上实测通过的那份二进制**逐字节一致**：

| 构建 | 产物大小 | sha256 | 用途 |
|---|---|---|---|
| 默认（无 FLR） | 1,596,488 B | `d5703a52653a7c38f45cc2abf49e297c1bdc130928813bd47619fbe9435a84e3` | 日常解锁 |
| `ENABLE_FLR=1` | 1,596,570 B | `23ad4fedcfb30d60b83b6e4df05b00294f9d3b4ff0ac6ecc8c90484592be83c6` | 故障恢复 |

（两份都在双卡 `03:00.0` + `02:00.0` 上完整跑过并拿到验证通过日志。）

---

## 致谢

解锁协议与 V67 canary exploit 来自：

- `PZH1gdmu/CMP40HX-Unlock`（`unlock40x_v70.c`，MIT）
- Jon Pry, *A Canary in the Crypto Mine*, DOI 10.5281/zenodo.20916112
- `chenxluo/tu102-22g-uefi-unlock`（TU102 移植；本项目在其基础上补了 Windows 侧缺失环节）

Windows 侧实操的 GSP 线索来自 `nie2100/40hx-unlock-recovery`。

## 许可证

MIT，继承上游 MIT 声明。见 [`LICENSE`](LICENSE) 与 `src/LICENSE-40HX-UNLOCK`。

## 免责声明

本项目涉及 UEFI 启动流程与 GPU 内部状态，**可能导致无法启动或设备暂时不可用**。
作者不对任何硬件损坏或数据损失负责。请自行备份、理解每一步再执行。
不建议在生产机器或唯一可用的电脑上首次尝试。
