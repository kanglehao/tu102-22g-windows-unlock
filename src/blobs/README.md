# blob 从哪来（本仓库不分发 NVIDIA 固件）

`build.sh` 需要 `src/blobs/` 下有这些文件。本仓库**不随仓库附带** NVIDIA 固件，请按下面的方式自行获取。

| 文件 | 来源 |
|---|---|
| `v67_payload.bin` | 上游 `CMP40HX-Unlock` 仓库的 `tools/get-blobs.sh` |
| `booter_ucode_prod.bin` | 同上 |
| `booter_ucode_dbg.bin` | 同上 |
| `gsp_rm_boot_dbg.bin` | 同上 |
| `bl_gsp_tu102.bin` | 同上（TU102 专用） |
| `sec2_ucode_vbios_49.bin` / `_89.bin` | 同上 |
| `fwsec_50hx_prod.bin` / `_dbg.bin` | 同上 |
| **`fwsec_native.bin`** | **你自己卡上的 FWSEC**，用 `extract_fwsec.py` 提取（见下） |

## 为什么最后那个必须你自己来

FWSEC 是随 VBIOS 发布的固件 blob，**不同 VBIOS 版本内容不同**。
上游硬编码的哈希 `c6fa28fa…` 是原作者那张卡的一份；本机两张卡的 sha256 是 `584b5fe0…`，不是同一份。

用别人的 FWSEC 去要求 HS（high-secure）启动，轻则不启动，重则把不明状态喂给一块你还要继续用的 GPU。
所以请提取你自己的。

## 提取方法

```bash
# 1) 构建不依赖任何 blob 的 bootstrap 版本
DUMP_ONLY=1 bash build.sh        # -> TU102DUMP.EFI

# 2) 装到 ESP、一次性引导，跑完后在 ESP 上取回 50hx_vbios.bin
python ../win/esp_read_logs.py

# 3) 从 PROM dump 中提取 FWSEC
python extract_fwsec.py 50hx_vbios.bin blobs/fwsec_native.bin

# 4) 核对常量：见 ../docs/geometry-constants.md
sha256sum blobs/fwsec_native.bin

# 5) 用自己的 FWSEC 时哈希必定不符，放行构建
TU102_ALLOW_FWSEC=1 bash build.sh
```

## 建议

1. **先比对哈希**：把你的 FWSEC sha256 贴到 issue 里比一比。我们发现两张 VBIOS 串不同（`.04` / `.3E`）、
   ROM 相差 6704 字节的卡，FWSEC 逐字节相同 —— 同 SKU 很可能可以直接复用常量。
2. **先在 SAFE 探针里验证**：`SAFE_ONLY=1 bash build.sh` 出来的 EFI 只读不改，
   既不杀 GFW 也不 DMA 加载固件，能验证基础设施又不冒风险。
3. 别把 blob 提交进你 fork 的公开仓库 —— 它们是第三方固件。
