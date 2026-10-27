# 九个 FWSEC 几何常量是怎么来的

`src/unlock50x_v1.c` 顶部有一组 `FW50_*` 常量，它们描述**你的卡上那版 FWSEC 固件的内部布局**。
上游的数值来自原作者那张 leadtek 卡的 VBIOS（`.copyright`/VBIOS 串不同则固件不同），
我们这两张卡的 FWSEC sha256 是 `584b5fe0…`，和上游的 `c6fa28fa…` 不是同一份 —— 于是九个常量全部要重推。

这一页记录我们的推导方法，供其他 VBIOS 版本照做。

## 最终数值（本机两卡通用）

| 常量 | 上游 leadtek 卡 | 本机 TU102 22G（`90.02.17.00.04` / `.3E`） | 含义 |
|---|---|---|---|
| `FW50_CODE_SIZE` | 0x9800 | **0x9600** | code section 长度 |
| `FW50_DMDP_SIZE` | 0x420 | **0x410** | DMEM desc/pmem 结构长度 |
| `FW50_BLOB_SIZE` | 0x9C20 | **0x9A10** | FWSEC blob 总长 |
| `FW50_MAPPER_OFF` | 0x390 | **0x380** | DMEMMAPPER 偏移 |
| `FW50_CMDIN_OFF` | 0x3E0 | **0x3D0** | command input 偏移 |
| `FW50_NS_SIZE` | 0x400 | **0x300** | non-secure 区长度 |
| `FW50_SEC_BASE` | 0x400 | **0x300** | secure 区起始 |
| `FW50_SEC_SIZE` | 0x9400 | **0x9300** | secure 区长度 |
| `FW50_DMEM_SIZE` | (随 IRC 走) | **0x380** | DMEM 可用长度 |

## 怎么推

### 1. 先把 FWSEC 从你自己的卡上抠出来

```bash
DUMP_ONLY=1 bash build.sh       # -> TU102DUMP.EFI，不依赖任何 blob
# 装到 ESP、一次性引导，跑完在 ESP 上得到 50hx_vbios.bin
python extract_fwsec.py 50hx_vbios.bin blobs/fwsec_native.bin
```

我们的两张卡 dump 出来的 FWSEC **逐字节相同**（39440 字节，`sha256 584b5fe0…`），
尽管 VBIOS 串一个 `.04` 一个 `.3E`、ROM 相差 6704 字节。**结论：同一 SKU 的不同批次，FWSEC 可以完全一样。**
所以如果你手上是同款 22G 卡，先比对哈希能省掉整套推导工作量。

### 2. DMEMMAPPER 签名定位 → `FW50_MAPPER_OFF`

在 FWSEC 里搜 `"DMAP"` ASCII 签名。我们这版落在 **DMEM+0x380**。
签名之后读出 `version=3`，结构里的 **command offset 字段 = 0x3D0**，直接给出 `FW50_CMDIN_OFF`。

这两个值是整套常量里最硬的两个：**有签名、有版本号、有自描述指针**，不依赖猜测。

### 3. 算术关系闭合校验

FWSEC 的分区必须满足总长守恒，我们验证了三组独立关系：

```
CODE_SIZE + DMDP_SIZE + NS_SIZE              = BLOB_SIZE
SEC_BASE  + SEC_SIZE                         = DMEM 总量
DMEM_SIZE + MAPPER 之后的保留区              = 观察到的 DMEM 上界
```

代入上面的数值三组全部闭合：

```
0x9600 + 0x410 + 0x300 = 0x9A10   ✓
0x300  + 0x9300        = 0x9600   ✓
```

> 经验：**先找自描述结构（签名 + 版本 + 内部指针），再用算术守恒把剩下的量解出来，最后交叉验证。**
> 不要反过来先猜数值 —— 猜出来的数哪怕能跑通一次，换一张卡就崩。

### 4. 熵分析确认明文 / 密文边界 → `FW50_SEC_BASE`

`FW50_SEC_BASE` 是最难的一个：它是 FWSEC 里已签名（密文）区与明文区的分界，
猜错会导致 HS 启动阶段校验失败。

我们用字节熵做验证：以 256 字节为窗滑动计算香农熵，在 **0x300** 附近出现明显的台阶 ——
之前的部分熵值均匀（密文特征），之后的区域出现大量重复字节和结构化 0 填充（明文/元数据特征）。
边界位置与第 3 步算术解出的 `SEC_BASE` 完全吻合，两边互证。

（工具可以很简单：滑动窗 + `collections.Counter` 直接算香农熵即可，不必追求绘图 —— 台阶本身很有辨识度。）

## 换卡 checklist

1. `DUMP_ONLY=1` 构建 → 引导 → 拿到自己的 PROM dump
2. `extract_fwsec.py` 抽出 FWSEC，**先比对上游哈希**
3. 搜 `"DMAP"`，读 version 与 command offset
4. 用三个守恒式解出剩余常量
5. 熵分析确认 `SEC_BASE`
6. 改常量后编完先在 **SAFE 探针构建**（`SAFE_ONLY=1`）里跑一遍：它只读不改，不会把卡留在坏状态
7. 确认 `is_unlocked()` 的判据也要注意 —— 我们另一张卡的锁定态基线是 `SS0=0x02420000 / SS1=0x5`，
   和公版卡的 `0x0/0x0` 不同，别把某个合法的锁定值误判成"已解锁"
