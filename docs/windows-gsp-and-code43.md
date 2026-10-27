# 代码 43 的完整因果链（本过程中最贵的教训）

## 现象

双卡 pre-OS 解锁全部成功，日志清清楚楚：

```
card #0 (03:00.0)  SS0=0x02420000 SS1=0x5  ->  SS0=0x88888888 SS1=0x8   verified UNLOCKED
card #1 (02:00.0)  SS0=0x0        SS1=0x0  ->  SS0=0x88888888 SS1=0x8   verified UNLOCKED
```

进 Windows 后两张卡都是：

```
ConfigManagerErrorCode = 43   (CM_PROB_FAILED_POST_START)
```

桌面是靠 USB 虚拟显示器撑起来的；当时试着用 `Disable-PnpDevice` / `Enable-PnpDevice` 让驱动重新初始化，
结果**两张卡只能起来一张，来回切换**，最后把机器搞挂，23:26 自动重启。

## 真凶

一行注册表：

```
HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\<每张卡各自的子键>
EnableGpuFirmware = 1   (DWORD)
```

作用是让 Windows 驱动走 **GSP-RM** 路径，从而"接受" pre-OS 阶段就写好的 GPU 状态。
本项目上游的 README 把这一条写在安装步骤第 0 位，故障排除表里又点了一次：

> 解锁后进 Windows 但基准没变 → 链载途中发生了重 POST（看日志最后一行）+ **确认 `EnableGpuFirmware=1`**

我们第一轮完全跳过了它。

## 为什么很难发现

因为**查了、但查错了地方**。`EnableGpuFirmware` 不在下面这些位置：

- ❌ `HKLM\SYSTEM\CurrentControlSet\Services\nvlddmkm`
- ❌ Class 键的顶层 `{4d36e968-…}`

它在 Class 键的**每个数字子键**里（`0000`、`0001`、…），**每张卡一个，必须各自设置**。
当时只在上面两个位置查了，看到"值不存在"就判断成"走默认行为、不是问题" —— 这个判断是错的。

> **教训**：GPU 类注册表的开关几乎都在**每张卡的实例子键**里。查不到的时候，
> 先问"是不是该枚举子键"，而不是急着得出"这项不是问题"的结论。

## 修正后的因果链

| 尝试 | `EnableGpuFirmware` | PCIe FLR | 结果 | 原因 |
|---|---|---|---|---|
| 第一次 | ❌ 没设 | ❌ 无 | **两卡代码 43** | 寄存器确实解锁了，但驱动走非 GSP 路径，不接受 pre-OS 写好的状态 |
| 第二次 | ❌ 没设 | ✅ 有 | 驱动正常，**但算力没涨**（比值仍是 0.50） | FLR 把 SS0/SS1 一起推回出厂锁定值，见 [flr-pitfall.md](flr-pitfall.md) |
| 第三次 | ✅ 1 | ❌ 无 | **两卡正常 + 算力满速（比值 0.96 / 0.98）** | 正确组合 |

## 成功判据（按可信度排序）

1. `mma_peak` 比值从 ~0.50 涨到 ~0.96 —— **最终判据**，直接看硬件
2. `nvidia-smi --query-gpu=gsp.mode.current` 从 `N/A` 变成 `Enabled`，
   并出现 `GSP Firmware Version`（本机 615.78.05）—— GSP 真的起来了
3. 设备管理器 `Status = OK`、`ConfigManagerErrorCode = 0`

注意第 2 条只是"驱动侧开对了"，不等于"解锁生效" —— 第二次尝试时 GSP 也是 Enabled，算力却没涨。
**一定要用第 1 条收尾。**

## 副作用：`EnableGpuFirmware=1` 本身有风险

打开 GSP 后，一旦 NVIDIA 驱动在这张卡上有兼容性问题，表现通常是黑屏而不是 43。
所以：

- 保留一条不依赖这张卡的显示输出（核显 / USB 虚拟显示器）
- 知道怎么进安全模式（微软基本显示驱动不加载 `nvlddmkm`、不跑 GSP）
- 知道 RustDesk 之类的远程通道还能用

回退很简单：把值改成 `0` 或删掉。

## 关于"必须在解锁态下禁用/启用设备"

社区里流传 "43 后禁用再启用就能恢复" 的说法（针对 40HX 那类 TU106 卡）。
**在 TU102 解锁态下不要这么做。** 我们实测：

- 第一次发现有几率把其中一张卡拉回来，但另一张立刻掉下去
- 反复操作会把机器搞挂

真要恢复，请用专门构建的 **FLR 恢复版**（见 [flr-pitfall.md](flr-pitfall.md)），
它的作用就是一次干净的 PCI function 复位，不会让系统崩溃。
