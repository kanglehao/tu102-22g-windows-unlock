# 为什么不能照搬 40HX 的 PCIe FLR 收尾

## 背景

CMP40HX 解锁固件（TU106）的协议第 7 步是这样收尾的：

> kill the still-spinning SEC2 ROP engine, then issue **PCIe FLR — the ONLY reset that
> clears a latched WPR2** (masks/selectors survive FLR). **No MMIO access after the FLR.**

它的设计哲学是"EFI 阶段不重训链路、不打扰显卡启动流程"，并且明确靠这一步拿到一个
"干净但不丢状态"的 GPU 再交给驱动。40HX 项目自己的版本记录还写着：

> `FULL_NOGEN2`（v3.03）移除了 PCIe Gen2 链路域，因为该域会干扰 GSP 正常启动（**v3.02 的 Code 43 错误**）

所以在 TU106 上，这套是**被验证过的、成立的**。

## 我们把它搬过来之后发生了什么

本机双卡各成功解锁，日志齐全：

```
*** UNLOCKED (SS0=0x88888888 SS1=0x8) ***
FLR: issued (pcie cap @0x78, 03:00.0)
FLR: issued (pcie cap @0x78, 02:00.0)
```

进 Windows：

- ✅ 两张卡 `Status = OK`、`ConfigManagerErrorCode = 0` —— **代码 43 确实消失了**（FLR 版之前两卡全 43）
- ❌ `mma_peak` 比值：**0.504 / 0.510** —— 和没解锁完全一样

也就是说：**FLR 把 WPR2 清掉了，同时也把 SS0/SS1 推回了出厂锁定值。**

> 40HX 那条 "masks/selectors survive FLR" 的结论，在 **TU102 上不成立**。

## 所以本仓库怎么处理

可以让步地保留了实现，但：

1. 默认**不编译**：`u40x_flr_current()` 和它的调用点都包在 `#ifdef U40X_ENABLE_FLR` 里
2. 需要时用 `ENABLE_FLR=1 bash build.sh` 构建，产物仅作为 **故障恢复工具**
3. 源码注释里写明了这条结论，免得后来的人再踩一次

同时代码里还留有一处主线专用的实现细节值得一看：上游那个 `do_flr()`（约 2917 行）走的是
旧的紧凑地址编码 `bus<<20 | dev<<15 | fn<<12`，**在通过 CF8 找到卡的机器上会读错设备**
（本机正是这种情况）。主线应该用 `u40x_pci_rbdf()` / `u40x_pci_wbdf()`（带 `u40x_enc_found` 语义）。
即便 FLR 本身不该用，这个"两套编码不能混用"的坑也一样值得知道。

## 它现在的唯一正当用途：替代物理断电

以前解锁实验搞砸了，恢复手段是：**拔电源线 + 按住电源键 5 秒 + 等 30~60 秒放电容余电**，
然后冷启。这一步经常要重复两三次才成功（前两次失败基本都是余电没放干净）。

有了 FLR 恢复版，流程变成：设一次性引导 → 开机 → FLR 版跑完自动进 Windows → 卡已经是干净出厂态、驱动可用。
**不用摸机箱，也不用等电容。** 这是我们这轮折腾里最有价值的副产品。

## 一句话总结

| | TU106 / 40HX | TU102 22G |
|---|---|---|
| FLR 收尾 | 有效：清 WPR2，解锁值存活 | **有害：连 SS0/SS1 一起复位** |
| 驱动 43 的根因 | 链路域干扰 GSP | **`EnableGpuFirmware` 没设**（见 [windows-gsp-and-code43.md](windows-gsp-and-code43.md)） |

同代架构、同一套寄存器、**结论不通用**。省下那十分钟搬代码的工作量，代价是多花两整轮重启去排除这个副作用。
