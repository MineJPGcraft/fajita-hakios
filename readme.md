

## 下载来源

| 本地文件名 | 固定来源 | 用途 |
| --- | --- | --- |
| `iOSToolchain-x86_64.tar.xz` | [L1ghtmann test-210562a](https://github.com/L1ghtmann/llvm-project/releases/download/test-210562a/iOSToolchain-x86_64.tar.xz) | Theos Linux 交叉链接器及签名工具，Clang 11.1.0 |
| `iPhoneOS16.5.sdk.tar.xz` | [Theos master-146e41f](https://github.com/theos/sdks/releases/download/master-146e41f/iPhoneOS16.5.sdk.tar.xz) | iOS SDK |
| `MacOSX13.3.sdk.tar.xz` | [macosx-sdks 13.3](https://github.com/joseluisq/macosx-sdks/releases/download/13.3/MacOSX13.3.sdk.tar.xz) | XNU DriverKit 生成头文件 |
| `linux-7.0.14.tar.xz` | [kernel.org](https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.0.14.tar.xz) | ath10k 源码 |
| `freebsd-15.1-src.txz` | [FreeBSD 15.1 src.txz](https://download.freebsd.org/releases/amd64/15.1-RELEASE/src.txz) | LinuxKPI 源码；下载后按左列命名 |
| `mesa-25.0.7.tar.xz` | [Mesa archive](https://archive.mesa3d.org/mesa-25.0.7.tar.xz) | 图形库源码 |
| `iPhone10,3,iPhone10,6_16.5_20F66_Restore.ipsw` | [Apple CDN](https://updates.cdn-apple.com/2023SpringFCS/fullrestores/032-85130/6AB0D10A-8AF9-4F6C-8CF5-A1B315CABE88/iPhone10,3,iPhone10,6_16.5_20F66_Restore.ipsw) | iOS 16.5 / 20F66 系统材料 |

## uefi

https://github.com/edk2-porting/edk2-sdm845

## hakios

https://github.com/oosexgang/hakios-xnu-kernel

## 参考仓库

| 目录 | 来源 | 用途 |
| --- | --- | --- |
| `ref/hakios-xnu-kernel` | [oosexgang/hakios-xnu-kernel](https://github.com/oosexgang/hakios-xnu-kernel) | HakiOS 通用 XNU、驱动、设备移植工具和 iOS 16.5 用户空间流程 |
| `ref/hakios-uefi` | [oosexgang/hakios-uefi](https://github.com/oosexgang/hakios-uefi) | HakiOS UEFI 修改、XNUP loader 集成和设备平台组织方式 |
| `ref/dipper-xnu` | [youzjuzi/dipper-xnu](https://github.com/youzjuzi/dipper-xnu) | SDM845 / A630 设备的 XNU、用户空间和构建补丁参考 |
| `ref/edk2-sdm845` | [edk2-porting/edk2-sdm845](https://github.com/edk2-porting/edk2-sdm845) | SDM845 UEFI 基础及 OnePlus 6T `fajita` 平台文件 |

当前克隆提交：

```text
hakios-xnu-kernel  a3384e031eb1e605cf37adaefc19991f04f6274b
hakios-uefi       687fa6c6a5a70f43184de92980e0a76eea5f2c4b
dipper-xnu        4e82c709ea42ebb509fa101b52972fdc890b873e
edk2-sdm845       e1952621f419f8db60ed28271264e1b5184c571d
```

`fajita` 的 UEFI 平台文件已在 `ref/edk2-sdm845/Platform/Oneplus/sdm845/` 中提供；HakiOS 内核和设备层仍需按 `docs/porting/` 的流程从 fajita 自身设备树开始移植，不能直接把 `dipper` 的设备参数当作通用 SDM845 参数。

## 当前适配进度

**2026-11-11 GPU 进展：前 10 步实机全部通过，GMU 固件已成功装载。** Adreno 630 的 bring-up 从 dipper（小米 8，同 SDM845 同 GPU）移植而来，寄存器基址逐项比对本机 EDL 设备树（3D 核 `0x05000000`、GMU `0x0506a000`、SMMU `0x05040000`、GPUCC/GFXCC `0x05090000`），固件从本机官方 vendor 镜像提取（`a630_gmu.bin` 32768 字节、`a630_sqe.fw`、`a630_zap.*`，带 SHA256）。

实机 `fastboot boot`（未写分区）结果：步骤 1–6 与 1–9 均全绿（`fajitagpu: 9 steps: kkkkkkkkk`）。这证明 GCC 的 GPU 时钟分支、RPMh 供电名、GPUCC 电源开关、CX 域、GMU 时钟，以及 **GMU 常开计数器确实在跳动**——即 GMU 时钟真实运行；SMMU 与上下文银行读回与 bootloader 留下的一致，ITCM/DTCM 的两字写入可接受且可恢复。

步骤 10（写 32 KB `a630_gmu.bin`）经历 4 次失败后**已实机通过**（`kkkkkkkkkk`），固件完整写入 GMU 且零字读回错误。归档镜像 `3d737c508e631571b6f3325c3579782031a076e1bb9857b9aae39b68d6f86c77`（12,880,384 字节）。四次失败的根因各不相同，且大多只能靠实测日志定位：

1. 曾假设写入越过 DTCM —— 查 Mesa 官方 XML 后确认偏移与线性写入都正确，**假设错误**。
2. 曾假设执行顺序错 —— 调整后仍失败，因为调用了 `step_rsc_pdc()`，它只读 PDC 不给 GPU 上电。
3. 新增 `gmu_rpmh_start()` 后仍失败 —— RSC 缺少可应答的 PDC 序列。
4. 补上 `gmu_rpmh_init()` 握手后仍失败 —— **`PDC_GPU_ENABLE_PDC` 被用读-改-写 `| 1` 破坏了 bit31**；Linux 要求整值写 `0x80000001`，且配置完成后必须调 `a6xx_rpmh_stop()`。

最终生效的加载序列：`gmu_rpmh_init`（关 PDC → 写 47 项握手表 → 整值写 `ENABLE_PDC=0x80000001`）→ `gmu_rpmh_stop`（REQ=1 等 bit16）→ `gmu_rpmh_start`（REQ=1<<1 等 ACK → 等 BUSY 空闲）→ 按 4 KB 分块写 32 KB 固件。

另有一项关键诊断改进：loader 原每次只采样日志尾 240 字节，而 GPU 各步合计约 25 KB，前 9 步的日志把窗口刷满，导致第 10 步的失败原因完全不可见。新增 `fajita_log_window=N` 把采样窗口提升到 4096 字节后，才看到 `the GPU's RSC was not powered` 这一决定性线索。**没有这项改动，后面三轮都是在盲猜。**

详见 [GPU 状态](devices/fajita/gpu/README.md)。

**2026-11-10 触摸已实机工作。** 候选 `.work/candidates/fajita-touch-working-device-tested/boot-fajita-touch-panel.img`，12,840,960 字节，SHA-256 `e66921026ee908608c0df436949233ff0660938586303055bc59660e8ec065c0`，用户确认手机**首次响应触摸**。仅以 `fastboot boot` 临时启动，未写入任何分区；主机套件通过，压缩 FV loader 与 XNUP 一致。

**决定性修复是删掉一个检查，而不是增加检查。** `ft_interrupt_mask()` 曾把 F01 `control1` 的读回值与刚写入的值做相等比较，不相等即失败。而固定提交的厂商驱动读了同一寄存器后**直接丢弃返回值**（`synaptics_driver_s3320.c:984-986`），只以总线结果判定成功。这块屏接受写入但读回不等于写入值（实测地址 `0x0e`），于是该比较以 `D=0x10D` 中止了整个识别流程。现在读回只作为 `ft_mask_readback` 遥测发布，不再裁决成败。

定位过程中的四轮实机数据各自推翻了一个具体假设，其中三个缺陷在诊断代码本身而非触摸逻辑：`D=0x0357d000` 暴露诊断发布被 `fajita_bus_restore()` 的 GPIO 写入覆盖；`D=0x10B` 暴露外层 `ft_fail(11)` 覆盖内层软复位细分码（已改为首次失败优先）；`D=0x10D` 才是真正的厂商偏差。由于面板 `D` 是这块设备上唯一的反馈通道，诊断必须先可信，触摸逻辑才能被判断——这个顺序是本轮能成功的原因。识别步骤码现为 `0x100|N`（1..18），完整对照见 [触控状态](devices/fajita/touchscreen.md)。

2026-11-10 源码修正（**未实机启动**）：对照固定提交的厂商驱动后修正了两处确定的偏差，候选归档于 `.work/candidates/fajita-touch-irq-mask-report-drop-hostchecked/boot-fajita-touch-panel.img`，12,840,960 字节，SHA-256 `493571d157f77dbc6470f850c6bfa2d20d5a7af1f555f85f84d00753eb5fa694`。主机套件全部通过，压缩 FV loader 与 XNUP 一致，FDT/flashboot/root APFS/内核均核验通过，内核为全新构建且只剩已记录的 `check_all_exports` 失败。本轮没有执行 `fastboot`，也没有写入任何分区。

- **中断掩码生命周期**：原先只有 `ft_configure_scan()` 写 F01 control1 且不校验写入是否被保留；`ft_vendor_soft_reset()` 还把掩码清成 `0x00` 后从不恢复，一旦重新使能失败，驱动就会轮询一条控制器被明确告知不要拉低的 attention 线。现在所有写入统一由 `ft_interrupt_mask(bool)` 负责，与厂商 `synaptics_enable_interrupt()` 对齐：选择页零、仅在使能时先读 F01 data1 清除锁存状态、写 control1（`0x7f`/`0x00`）并读回确认。掩码在 100 ms 重启期间刻意保持关闭，随后由 `ft_configure_scan()` 重新使能。这是对厂商源码的一致性修正，现有实机证据并未证明掩码就是无触摸的原因；掩码写丢失现在会让配置阶段失败并显示 `F=0000000D`，而不是一直 `A=0`。
- **越界触点逐点丢弃**：解码器原先在任一触点 `x > max_x` 或 `y > max_y` 时拒绝整帧，而厂商驱动根本不做范围检查。整帧拒绝会让一个异常 slot 掩盖同帧其它真实触点，连续八次即终止轮询线程。现在越界触点单独丢弃（保持该 slot 非活动、新增 `dropped` 计数），首帧会向内存在内存日志写入 `fajita-touch: dropped N out-of-range object(s)`。释放语义不变：被丢弃的触点等同缺席，已发布的按压照常上报 UP。截断帧、非法 attention、非法参数仍整体原子拒绝。
- **主机测试基线修复**：`test-fajita-touch-rpmh.py` 因 `fajita_power_tlmm_layout` 赋值后未使用而编译失败；该值实际用于 `fajita_touch_tlmm=` 确认，现已改为断言它。`scripts/test-fajita-touch-host.sh` 全部通过。

刻意未改：供电/复位时序、SDA/SCL 角色、RPMh 事务、TLMM 布局选择、64 字节 live ABI 和面板字段布局。`dropped` 未进入面板，因为 `A` 的 32 位已被 attention 与活动槽位掩码占满，而 ABI 严格为 64 字节。

构建环境说明：项目被移动到当前路径后，`ref/dipper-xnu` 与 `.work/theos` 中 17,759 个符号链接仍指向旧的小写 `/home/fireguo/...`，已批量修正为实际路径；参考树中固定提交的 BaseTools 在 GCC 16 下把旧代码警告当错误，构建脚本改为用 `BUILD_OPTFLAGS=-Wno-error` 覆盖（追加在全部 `BUILD_CFLAGS` 之后，不改变 `-D` 宏或代码生成），不修改参考源码。

2026-11-02 最新边界：事件链路诊断候选 `.work/candidates/fajita-touch-event-path-hostchecked/boot-fajita-touch-panel.img`（SHA-256 `05bb83c83943978177b454861e9ed160375a352d92f183a38135463ca3433cc0`）已通过主机检查，并经临时 `fastboot boot` 进入桌面；用户确认触摸仍无响应。新面板值 `P=1 F=9 D=0357d004 N=67 A=0 Q=0 E=00000143000b8300 R=0001000000000000` 表示固件准备成功、103 次报告轮询解码成功、F12 attention 寄存器 `0x0b` 读取成功且 SDA/SCL 为高，但控制器未报告活动触点，内核也没有事件可发布。故当前故障边界在控制器活动报告之前，尚未到 `/dev/redwood-touch` 或 backboardd HID。安静面板会隐藏 UEFI 控制台里的 `tl: p`，而 Q=0 时也不会产生 `tl: i`，缺少这些行不构成额外证据。厂商 S3706 报告解析对照未发现格式差异。完整 XNU all/export 的已知失败仍保留；未刷写任何分区。

为确认这块非原装屏的具体控制器，新增 `fajita_touch_probe=1` 一次性身份探测候选：`.work/candidates/fajita-touch-probe-hostchecked/boot-fajita-touch-panel.img`，SHA-256 以该目录的 `SHA256SUMS` 为准。探测固定在已知 QUP12/I²C `0x20`，读取 F01/F12 描述符、F01 10 字节 Product ID 和状态，然后恢复 GPIO 并退出；内核探测分支不发控制器扫描/复位/固件写入，也不探测其他地址或协议。RMI 分页本身需要一次 `0xff=0` 页选择写，因此寄存器读取是无配置写；但完整启动仍会先执行现有 loader 的 RPMh/LDO 与 GPIO88/99 供电/复位时序，不能称为无硬件副作用。主机回归、loader/XNU ABI、内嵌 FV/XNUP 一致性均通过。2026-11-02 临时 `fastboot boot` 已读到 Product ID `S3706B`，F01=`2B2200060101`、F12=`4A0013080112`、状态 `0`；地址与现有 Synaptics S3706 驱动相符，但不能据此确认非原装屏模组料号或调校。另有 `.work/candidates/fajita-touch-s3706-reset-hostchecked/boot-fajita-touch-panel.img` 候选，按 OnePlus 顺序增加 F01 中断屏蔽、软件复位和100 ms等待；主机测试通过，2026-11-02 临时启动 Sending/Booting 均 OKAY，运行与触控结果待回报。两份候选都只用 `fastboot boot`，未写入分区。详见[触控状态](devices/fajita/touchscreen.md)。

- 已用 HakiOS 的 `tools/hakios-device.py new` 从公开兼容 DTB 生成 `devices/fajita` 骨架，并通过 `tools/hakios-device.py check fajita` 和 `tools/hakios-config.py defconfig`。
- 当前 9 个 SDM845 地址值来自兼容 DTB；modem/IPA 所需的另外 7 个值缺失，不能据此启动 cellular 功能。
- 所有手机专属驱动在 fajita 配置中关闭。dipper 的 A630、Goodix/触控、WCN3990 和存储参数未直接复用。
- EDL `boot_a/dtbo_a` 已提取出 3 个 SDM845 base DTB 和 16 个 Qualcomm DTBO；它们作为设备 EDL 树使用，不因 Android 版本不同而作废。官方 system/vendor 属于 Android 10 build `2101210516`，用于厂商固件与配置参考。提取和输入状态见 [EDL_INPUTS.md](EDL_INPUTS.md)。
- `scripts/prepare-fajita.sh` 只同步设备层、运行设备检查并生成 `fajita_defconfig`；通用首轮不改 loader 或 XNU。
- `patches/fajita-gic-timer.patch` 为 SDM845 参考补丁；timer 值 19 来自 dipper，仍不是 fajita 实测值。
- `scripts/apply-fajita-to-dipper.sh` 在已准备的 dipper SDM845 工作树上应用 fajita loader/XNU/UEFI 改动，增量检查 XNU、构建 loader 与 EDK2，并把 one-shot 镜像写到 `.work/build/fajita/`。loader PE 时间戳固定；EDK2 FD 尚有未定位的构建差异，最终 hash 以每轮 `SHA256SUMS` 为准。该脚本只在主机运行，不操作手机。
- 当前 UEFI 恢复 dipper 的 BDS 启动顺序；`XnuLoaderDxe` 只注册 BootOrder，不在 DXE 调度期间直接执行 loader。fajita 专用内存表在 PEI 阶段预留 `0xF0000000..0xF4000000`，避免固件分配覆盖 BootShim 放入的 XNUP。
- `devices/fajita/bootargs.txt` 使用 `bootcpu=7 cpus=1 ufs=2`：XNU 只用一个核心，但 UEFI 在原核继续提供 UFS mailbox 服务。loader 新增同核 UFS 交接拒绝检查；单纯的 `bootcpu=0 cpus=1` 会在 UEFI 本来运行于 core 0 时丢失存储服务。
- `scripts/verify-fajita-source.py` 检查启动时机、4/6/8/10 GiB 内存表的预留和端点、UFS 核心检查与 loader USB 绕过逻辑。`scripts/verify-fajita-boot.py` 检查 boot/FDT/XNUP 边界、输入摘要及两份 APFS，并从镜像内实际解压 FV，确认内嵌 loader 与 XNUP 中一致。打包直接使用本轮 EDK2 boot 和用户空间 APFS，不再依赖历史候选模板。

`.work/build/fajita/boot-fajita-singlecore-no-usb.img`、`boot-fajita-ufs-no-usb.img` 和诊断版 `boot-fajita-ufs-observe.img` 均已实机失败。诊断版两次通过 `fastboot boot`；准确最后一行是 `handoff: UEFI context saved, preparing caches`，前面为 `the kernel will run on core 700 (this is core 0)`、`jumping to f74594a0`，约两秒后进入 9008。没有看到 cache-complete/PSCI/secondary-entry 标记，故当前确认的故障区间是交接前的缓存准备；`jumping to` 本身不证明 XNU 已执行。实测镜像和日志已归档至 `.work/candidates/fajita-ufs-observe-tested/`。

`boot-fajita-ufs-cache-scope.img` 的缓存范围修改后，用户于 2026-11-01 报告**已进入 iOS 桌面**，但持续刷日志闪屏、触摸无响应。这是本项目首次收到桌面成功反馈，不再把这份镜像记作未实测。该镜像 SHA-256 为 `3a2e0ab8a48c01c3a670a4179abba72e1a3e06b9fc49eddca9319617798a562a`，完整基线、原 loader/bootargs 和实测记录保存在 `.work/candidates/fajita-first-desktop/`。这次成功不代表 USB、触控或其它外设已验证。

安静版 `.work/build/fajita/boot-fajita-desktop-quiet.img` 移除 `-v`，并在 secondary-entry 确认后由 loader 让出 framebuffer，进度仅留在内存；返回或 UEFI 异常时恢复可见报告。用户随后确认“安静版没问题”；完整基线与实测记录保存在 `.work/candidates/fajita-desktop-quiet-tested/`。这一步未改内核、GPT/APFS 或 USB 硬件，也未刷 boot。

桌面运行期间，经授权的 `lsusb` 未发现手机/xnuloader 接口；当前 one-shot 镜像跳过 loader USB 服务，因此这次会话无法用它远程操作。触控现有物料已定位到 Synaptics RMI/S3706B；已新增本机 GPIO I²C、F01/F12 正常扫描、十点报告与 `/dev/redwood-touch` 接口，未启用 dipper 的 ST FTS。

首个触控版 `boot-fajita-touch.img` **实机失败并进入 9008**，最后可见 `handoff: secondary core reached the entry stub`。该行后 loader 会让出屏幕，不能将它当作具体崩溃位置；失败材料保存在 `.work/candidates/fajita-touch-v1-failed/`。静态审计发现未经本机确认的 cmd-db 地址和无跨核仲裁的内核 RPMh/TCS 操作，现已整条删除；这两项是真实缺陷，但尚未证明是本次复位的准确原因。

固件供电版 `boot-fajita-touch-vreg.img`（SHA-256 `31414219ae130efb10b259b1f9b182883b19aa4c106cf8f708294349e0dce232`）也已实测失败：`fastboot boot` 发送和 Booting 均 OKAY，用户确认最后可见 `handoff: UEFI core 0` 后进入 9008。下一条新增路径正是固件 Vreg 调用，但无法确定其中哪一步失效；二进制审计确认 ABI 匹配，也确认协议实际读写 SPMI/PMIC。“返回错误则跳过”不能覆盖固件内部异常或外部复位。材料保存在 `.work/candidates/fajita-touch-vreg-hostchecked/`，其运行状态已改为失败。

继承供电版 `boot-fajita-touch-inherit.img` 撤掉全部 loader 触控供电调用，loader 与安静成功版逐字节相同；内核仅访问 SDA/SCL/IRQ 三页，不改 GPIO88/99、PMIC/reset。镜像 SHA-256 `fa818bb35ae036a37b1ef5357e799d2087555b927dd98c8bdd2ed045b8def4e2`，用户确认在 secondary-entry 标记后仍进入 9008。失败记录已保留；这个标记不能证明触控线程已执行。

诊断版 `.work/build/fajita/boot-fajita-touch-observe.img` 已实测失败：出现 logo 和周期日志，用户确认不到 30 秒进入 9008、未看到 `fajita-touch-diag:`。它配置为触控线程先等 30 秒、再逐阶段发布访问标记，因此这次尚不能归因于触控 GPIO/I2C 访问。loader 以独立 `fajita_touch_diag=1` 临时显示日志尾部，限前 120 秒；五指持续按住退出改为 fajita 显式 opt-in。随后同内核、同 loader、只改 `fajita_touch=0` 的对照版获用户确认：桌面稳定至少一分钟，触控按设计关闭。这将回归范围缩到触控启用分支，不是触控已修复。

`boot-fajita-touch-deferred.img` 已修改实际代码：早期初始化只注册延时任务，30 秒后才创建输入线程；早期标记改用常量内存日志，触控日志移除重复 `printf`。主机检查通过，但临时启动后用户确认仍进入 9008，最后可见 activationd/lsd 的 `error = 3:No such process` 文本；这一行不是触控错误或 panic 回溯。镜像及本轮源码保存在 `.work/candidates/fajita-touch-deferred-hostchecked/`。

随后确认诊断 reader 存在可复现缺陷：环绕后仍读固定物理尾部，会反复显示旧日志。已改为按环指针读取，并新增独立 `FT=阶段 data=细节` 状态，在通用日志后显示。内核和 loader 的共享布局已配对核验，原 ABI 前 96 字节保持不动。这些修改修复诊断链，**尚未证明修好触控或复位**。具体实现和证据见 [触控状态](devices/fajita/touchscreen.md)；本轮不重新提取 EDL、不刷写触控固件或分区。

修正后的 `boot-fajita-touch-live.img` 已构建并完成主机检查，SHA-256 `fa150948589ecc40c84db50a2268767dba8b2c7612c9bb273e13163a24d8c17b`；完整归档位于 `.work/candidates/fajita-touch-live-hostchecked/`，**尚未实机启动**。本轮没有访问手机。

重新对照原始 EDL 设备树与厂商驱动后，已确认之前的触控只是**从 DT 提取部分参数的继承供电探测**，并未完整移植 GPIO88、GPIO99 与 L28 的上电/复位流程，也还没有实测 Product ID 或有效触摸报告。dipper 的 `mi8_fts.c` 提供 XNU 接入与 GPIO I²C 技术参考，不能代替 fajita 的硬件参数。

这轮已修正 IRQ125 的 active pinctrl（原先误用 2mA，实际 DT 为上拉/16mA，GPIO_CTL=`0x1c3`），并把板级参数集中到 `devices/fajita/kernel/fajita_touch_board.h`。直接读取还纠正了 AVDD 换算：`0x2de600` 是 **3,008,000µV**；旧实验实际请求 3000mV，其历史来源已标记，默认仍不调用这条失败的固件路径。新增 `scripts/audit-fajita-touch-dt.py` 对 3 份 base DTB、7 份 fajita DTBO 和编译常量做一致性检查，已接入构建前校验。GPIO49/50 的配对由树确认，但 SDA/SCL 个别角色仍未由成功传输证明。生产总线代码的主机 mock、启动与报告测试通过；这些不是实机触控成功证据。本轮没有操作手机。

供电流程现已实际接入 loader：CPU_ON 前读取本机 cmd-db 字典并解析 `ldoa28`，在固件核 TPL31/IRQ/FIQ 屏蔽期间执行有界 RPMh 事务，成功后执行厂商 GPIO88/99 时序；最终追加 `fajita_touch_prepared=1/0`，内核仅在确认成功后延时启用触控。对不确定事务不再调用固件控制台或继续交接，而是保持现场并显示底部红带。没有恢复旧的 Vreg/SPMI 猜测或内核跨核 RPMh 写入。

新候选 `.work/candidates/fajita-touch-rpmh-hostchecked/boot-fajita-touch-rpmh.img` 已构建并核验 FV/XNUP 一致，SHA-256 `a0c4a78d4db47244412207c0f13b432d13c44f54e631df34f990ee972bd15d03`。运行 `bash scripts/test-fajita-touch-host.sh` 可重放主机检查，包括实际后端的成功/忙碌/异常路径、供电时序、最终确认边界、DT正负例及ASan/UBSan。完整XNU all仍有原有export检查失败，不能称全量构建干净通过；该候选随后已通过 `fastboot boot` 临时启动，Sending 与 Booting 均返回 OKAY；用户确认进入桌面但触摸仍无响应，未看到FT/power日志；不能据此确认供电或I²C成功。启动前获准检查 fastboot，未刷写任何分区。

2026-11-02：针对“桌面正常但没有看到触控日志”，已实际加入 `fajita_touch_panel=1` 小面板，持续显示供电结果 `P`、内核阶段 `F/D`、有效报告轮询数 `N`、活动触点/attention `A`、已发布事件 `Q` 和 I²C 尝试/错误 `E`，并保留 RPMh 诊断 `R`。它每两秒只更新左下390×132像素，距底部80像素，同时抑制旧的全屏诊断输出，不调用USB协议。候选启用 dipper 已有的 `touch_lat=1`：`tl: p` 表示内核发布，`tl: i` 表示 backboardd 读取并提交 HID。另修复真实驱动运行期CRC等待及复位后重配顺序，未凭空更换SDA/SCL或再改供电时序。

新镜像 `.work/candidates/fajita-touch-panel-hostchecked/boot-fajita-touch-panel.img` 已构建，大小12,826,624字节，SHA-256 `67ec39dffff4ba68df06eeeb9002e2d854dff03435a27318a81e1201657c33f8`。主机检查包括实际面板/reader的越界与缓存范围、CRC/报告恢复、传输错误编码、staging重复及41个负例；完整XNU all仍有已知export检查失败。候选随后在用户确认厂商fastboot并批准设备访问后，已执行 `fastboot boot`，Sending/Booting 均返回 OKAY；用户确认进入桌面但触摸无响应，面板为 `P=80070003 F=00000010 D=00000006 N=00000000 E=0000000000000000`。这证明RPMh返回SKIP、供电流程停在AVDD步骤，内核因此在prepared gate跳过触控，尚未执行I²C；E=0不代表传输成功。未刷分区。字段解读与验证限制见[触控状态](devices/fajita/touchscreen.md)。

根据这次状态继续对照厂商 `qcom-rpmh-mailbox.c`，确认 ACTIVE 完成后不会清除 CONTROL 中的 AMC 位，而旧 helper 要求 CONTROL 全零。现已修改实际事务代码，仅接受 `0`、`ENABLE`、`ENABLE|TRIGGER` 三种明确空闲编码，并在任何命令写入前按厂商顺序清 TRIGGER→ENABLE；恢复时不重放历史 TRIGGER。STATUS、CMD_ENABLE、WAIT、pending IRQ、跨槽同资源冲突及FATAL保护仍保留。主机用例覆盖三种状态、两槽、每个前触发写入故障和回滚；这尚不能证明实机的SKIP就是该CONTROL条件造成。

下一候选另加第四行 `R=16hex`，把RPMh拒绝原因、槽号、寄存器偏移和读值留在屏幕，避免再只有聚合SKIP。构建脚本还在EDK2编译后、打包前再次核对项目与staged源码，拒绝审查期间输入改变造成的旧源码镜像。此前实测的三行面板镜像保持归档，不覆盖。

空闲AMC兼容候选已完成最终构建与主机核验，归档 `.work/candidates/fajita-touch-idle-amc-hostchecked/boot-fajita-touch-idle-amc.img`，12,832,768字节，SHA-256 `a68a8f1ebdfc868d5c5574f8fd7939473eb68420946bcf8d0ccb695163bcc972`，**尚未实机启动**。内核与刚才实测三行面板版逐字节相同；四个生产文件的增量补丁可从该实测基线精确重放。保留USB DXE和现有分区，只能以 `fastboot boot` 测试。

当前已获 OnePlus 6T 安静显示 iOS 桌面的用户实测反馈；`devices/fajita` 仍是部分适配。三行面板确认触控停在供电prepared gate，尚无有效 I²C 访问、Product ID或触摸事件，此前9008原因和USB控制仍未解决。

随后收到“卡在 fajita-power: RPMh ldoa28 ...”的反馈，具体镜像和底部红带尚待核对，不能只凭最后文本认定是哪条MMIO或FATAL。另修复一个确定的诊断缺口：FATAL发生在CPU_ON之前，原四行面板刷新循环根本不会运行；现在保持路径直接以内存绘图显示一次P/R，再WFE。该独立候选归档 `.work/candidates/fajita-touch-held-panel-hostchecked/boot-fajita-touch-held-panel.img`，SHA-256 `ee2e6fc81a5af760c9ce746e2807b72442fe4caf5fee03864e09ad2077d8b912`，主机回归通过，**未实机启动**。本轮没有再次访问设备。

## fajita GPT/APFS 实验记录

以下是本次在实机 LUN 0（4 KiB 扇区）上实际执行的 GPT 改造，不是只在文件中规划的布局。原 GPT 的末端是 LBA `30437370`，`userdata` 原本从 LBA `1601344` 延伸到末端。为了放置 HakiOS/APFS，先从 `userdata` 尾部划出 `5243387` 个扇区（约 20 GiB），再把这段空间作为新分区使用：

| 分区 | 起始 LBA | 结束 LBA | 扇区数 | 类型 |
| --- | ---: | ---: | ---: | --- |
| `userdata`（缩容后） | `1601344` | `25193983` | `23592640` | 原 userdata GUID `1b81e7e6-f50d-419b-a739-2aeef8da3335` |
| `darwin`（新增） | `25193984` | `30437370` | `5243387` | Apple APFS GUID `7c3457ef-0000-11aa-aa11-00306543ecac` |

`darwin` 的分区 GUID 为 `47b0585a-4452-465c-9328-9abc3596e4f6`，容量为 `5243387 × 4096 = 21476913152` 字节。GPT 其它分区的起止 LBA 保持原值。生成的 Firehose 写入描述在 `.work/fajita-apfs/rawprogram0.xml`（同内容也保存在 `gpt-install.xml`）：

```xml
<program SECTOR_SIZE_IN_BYTES="4096" filename="gpt-main-new.bin"
         label="PrimaryGPT" num_partition_sectors="6"
         physical_partition_number="0" readbackverify="true"
         sparse="false" start_sector="0" />
<program SECTOR_SIZE_IN_BYTES="4096" filename="gpt-backup-new.bin"
         label="BackupGPT" num_partition_sectors="5"
         physical_partition_number="0" readbackverify="true"
         sparse="false" start_sector="30437371" />
```

实际执行过程是：

1. 使用 bkerler EDL 的 OnePlus `fajita` 认证流程，项目 ID 为 `18801`。Firehose 在任何 GPT/分区写操作前先发送 `setprojmodel`，设备返回认证成功；未认证时同一 GPT 写入会被拒绝。
2. 按 Firehose 写入描述将主 GPT 写到 LBA `0..5`，其中 header 在 LBA 1、GPT 条目在 LBA `2..5`；备 GPT 条目写到 LBA `30437371..30437374`，header 写到 LBA `30437375`。
3. 从设备分别回读主表和备表，并重新计算 GPT header CRC 和条目 CRC。主表文件与回读文件 SHA-256 都是 `dbfd90b8d299b34f64c02d3bc2495522c04ced5b8b9a2073f6f131368b98ffe2`；备表文件与回读文件 SHA-256 都是 `e32d7e977c59e72c7f0e13317dd97f8a4a390489ad8d0b890e68886b08443fcc`。主表 header CRC 为 `a784873a`，备表 header CRC 为 `12b634fd`，两边条目 CRC 均为 `ead1bfb1`，计算值与 GPT 内记录一致。
4. 将 APFS 镜像写入 `darwin` 的起始 LBA `25193984`。写入文件 `.work/fajita-apfs/darwin-system.img` 大小为 `8365539328` 字节，SHA-256 为 `9efb156f417e32b6f92d709af33c0e8e584693c3fbd29eaf139b57eee3735560`。设备端回读首 1 MiB 和末 1 MiB，均与镜像对应区域逐字节匹配；没有做完整 8.36 GB 镜像回读。

因此可以确认 GPT 已按上表写入，`darwin` 的首尾抽样也已写入。设备启动失败与 GPT 写入结论分开记录；没有完整实机 loader/kernel 日志时，不把 9008 归因于 GPT、USB 或某个 XNU 子系统。对 dipper 完整启动链、失败候选及当前 BDS/UFS/内存预留修复见 [fajita 迁移审计](FAJITA_MIGRATION_AUDIT.md)。本轮未改 GPT、未刷 boot、未操作设备；Qualcomm USB DXE 保留，仅跳过 loader USB 服务。
