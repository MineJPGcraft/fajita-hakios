# fajita 启动链与迁移审计

## 当前结论（2026-11-01）

上一版 `.work/build/fajita/boot-fajita-singlecore-no-usb.img`（SHA-256 `546689dbf549ede970c4ae55eccb80c5b137718aec3bb85f3b4913be96974fb2`）实机仍进入 9008，已停止推荐。没有完整 loader/kernel 输出，不能确认它执行到了 XNU，更不能把编译成功、UEFI logo 或主机设备名称当作 XNU 启动证据。

本轮修改代码并构建 `.work/build/fajita/boot-fajita-ufs-no-usb.img`，修复下述三处主机侧可确认的问题。新版尚未实机测试；这些问题不能直接等同于上一版进入 9008 的唯一原因。本轮未枚举设备、未刷写、未更改 GPT，也未禁用 Qualcomm USB DXE。

最终整条构建脚本返回 0；当前镜像为 12,804,096 字节，SHA-256 `03be952478163c4952eda415f2391427e5880119757bbe09f181e57b8a085670`。日志为 `.work/build/fajita/apply-ufs-bds-final.log`；重建后仍以该轮 `SHA256SUMS` 为准。

## 已修复的启动链问题

### 1. 不在 DXE 调度期间执行 loader

旧工作树的 `XnuLoaderDxeEntry()` 在注册 BootOrder 前直接调用 `LoadImage/StartImage`。它只有 variable 架构协议依赖，没有 GOP、Block I/O 或控制器连接完成依赖。该调用会占住 DXE 调度，不保证控制台与 UFS 已就绪。

已恢复 `ref/dipper-xnu/bootloader/uefi/XnuLoaderPkg/Driver/XnuLoaderDxe/` 的 BDS 路径，并把源码纳入本项目的 `devices/fajita/uefi/files/XnuLoaderPkg/Driver/`。DXE 只把 loader 放到 BootOrder 首位，不自行执行。参考平台的 `PlatformBootManagerAfterConsole()` 会连接其余设备（`EfiBootManagerConnectAll()`），然后由 BDS 执行启动选项。

### 2. UFS 服务与 XNU 不能占用同一个核心

`loader.c::run_on_other_cpu()` 才会持续执行 `block_poll()`，为 XNU 的 `redwood_ufs.c` mailbox 请求调用固件 Block I/O。同核 `jump_to_kernel()` 不会继续运行这条服务循环；内核 UFS 请求会等不到回复。

旧 `bootcpu=0 cpus=1 ufs=2` 在 UEFI 位于 core 0 时存在这个确定的服务缺口。当前 `devices/fajita/bootargs.txt` 为：

```text
-v bootcpu=7 cpus=1 ufs=2 root_apfs=ufs_darwin vtimer_intid=19
```

这里 `cpus=1` 只限制 XNU 用一个核心，UEFI 仍留在原核提供 UFS 服务。`bootcpu=7` 沿用 dipper 的核间交接选择；没有启用 dipper 的其它六个 XNU 核心，也没有启用其 GPU、触控或 Wi-Fi 参数。

`patches/fajita-loader-ufs-handoff.patch` 额外加入同核拒绝检查。如果 UEFI 恰好已经位于所选 core 7，loader 显示原因并返回保持路径，不会尝试没有存储服务的交接。新日志包含 UEFI/XNU MPIDR 与 UFS 模式。PSCI 是否能在 fajita 启动该核心仍需运行证据。

### 3. 在 PEI 内存表中预留 XNUP

BootShim 在 UEFI 启动前把 XNUP 复制到 `0xF0000000`。旧 SDM845 内存表把该地址包含在 conventional RAM 中。loader 自己避开该范围，不能防止固件在 loader 执行前分配它。

fajita DSC 现在选择本项目自己的 `PlatformMemoryMapLib`。4/6/8/10 GiB 四种表都在原 RAM 区间内拆出 `0xF0000000..0xF4000000`（64 MiB），标为 `EfiReservedMemoryType`。其余部分保持 conventional RAM，原起止地址不变，没有额外叠加重叠的内存 HOB。预留也覆盖 loader 使用的末端 1 MiB panic log；打包器相应禁止 payload 越过这个边界。

已确认构建选择了这个库，编译后的 SEC 包含四项 `XNU Payload`，源码检查验证了四种容量表没有新增孔洞或重叠。

## 当前启动链

1. Android boot v1 的 kernel 为 gzip(BootShim + 7 MiB UEFI FD + XNUP + 16 字节对齐填充)，后接 fajita 兼容 DTB。BootShim 为 192 字节，FD 基址 `0xCE000000`，XNUP 目的地址 `0xF0000000`。
2. EDK2 在 PEI 阶段保留 XNUP 内存；DXE 完成驱动调度，BDS 连接控制器/控制台并执行内嵌 loader。
3. loader 解析 XNUP，选择 fajita 平台并准备 XNU 内存窗口、设备树和 boot args。Qualcomm USB 驱动保留，但 payload 不含 `usb` 且有 `oneshot`；loader 不调用 `usb_start()`，`USB == NULL` 的 poll 立即返回。
4. loader 在原 UEFI 核心提供固件 UFS 服务，XNU 通过 PSCI 在 core 7 执行。XNU 仅启用一个核心。
5. `flashboot` 成为 `/dev/md0`，其中 stage0 `/sbin/launchd` 挂载 payload ramdisk（`/dev/md1`）到 `/redwood` 并执行 `/redwood/sbin/init`，再按 `root_apfs=ufs_darwin` 使用已写入设备的 APFS 系统。

Android boot 头中的 ramdisk 是 EDK2 `build.sh` 生成的一字节换行占位，不是上述两份 APFS。旧历史模板使用五字节 `dummy`；新版直接使用本轮 EDK2 输出，校验器已对应区分，避免继续依赖旧模板。

## 构建与验证

```sh
bash scripts/apply-fajita-to-dipper.sh
```

该脚本需要已经准备好的 `ref/dipper-xnu/work/{hackios,edk2}`、XNU 源码/依赖和 `hackios/userland/{flashboot,root}.apfs`。它不再依赖 `.work/candidates/` 中的历史镜像。仍不能宣称只靠本仓库的干净 checkout 就能构建：固定上游准备、工具链和 Apple 用户空间材料仍是前置输入。

- `verify-fajita-source.py` 在编译前检查已同步的 BDS 源码、fajita DSC 库选择、四种内存表、同核 UFS 检查及 loader USB 绕过条件。
- `verify-fajita-boot.py` 校验 boot/FDT/XNUP 长度、对齐、panic-log 边界、条目集合、命令行以及 loader/kernel/两份 APFS 的逐字节对应关系。还从最终镜像的 LZMA 压缩 FV 中解析真正的 APPLICATION FFS，确认固件内嵌 loader 与 XNUP 的 loader 一致，而非只比较旁边的中间文件。
- loader PE 时间戳固定。EDK2 FD 在重复构建中仍有差异，具体来源未定位，不能称为整个镜像可重现；每轮以 `.work/build/fajita/SHA256SUMS` 为准。
- XNU 使用现有工作树增量构建，内核为 15,442,352 字节，SHA-256 `02bcdfb941ba6fe25835aa47f82f28e775e37794e6113adf7b364eb19b04980a`。构建仍有已记录的 symbolset / `check_all_exports` 后置检查失败；不能据此宣称完成了干净的全量 XNU 构建。本轮没有改变 XNU 内核代码。
- Python APFS 读取器确认启动卷 `/sbin/launchd`、第二卷 `/sbin/init` 与 SpringBoard plist 存在、所有者为 root；解压读出的 stage0/init 与 `userland/obj/{stage0,init}` 逐字节相同。本地系统镜像的 `SystemVersion.plist` 可读取。这是主机镜像检查，不是设备上已启动用户空间的证据。C APFS 检查工具因缺少 OpenSSL 开发头文件未编译成功，不算其验证通过。

最终镜像、prefix、XNUP、构建日志与校验日志都在 `.work/build/fajita/`。中间文件保持在项目内。

## 历史候选与剩余未知

旧 `.work/artifacts/fajita/boot-fajita-no-usb.img` 没有 XNUP，不能启动内核。`boot-fajita-direct-apfs-v7.img` 有 kernel，却缺少完整 UFS 根卷配方的 `flashboot`。后来完整的同核候选仍在实机进入 9008。这些是不同镜像，不能合并成“禁 USB 必然造成 9008”的结论。

dipper 是主要参考，覆盖 `docs/development/FIRST_INSTALL.md`、`docs/build/BUILD.md`、`docs/build/USERLAND.md`、`docs/install/STARTUP.md` 和实际 loader。`ref/hakios-uefi` 的 Mu-Silicium 集成属于另一条 UEFI 路线，不能直接混用；HakiOS 通用设备层仍是 provisional 状态。

仍需运行证据确认：最后进入的引导阶段、fajita 的 PSCI 核间启动、GIC virtual-timer PPI、GOP/MIDR 平台识别结果、固件 UFS mailbox 的读取，以及 XNU 首条输出。PPI 19 来自 dipper 实测，不是 fajita 实测。新版修正上述静态问题后仍可能暴露其它平台问题，尚不能保证不进入 9008。

GPT/APFS 已写入的具体过程和首尾回读范围见 `readme.md`。没有新证据前不重改 GPT；不刷 boot 分区，实机启动仍采用经授权的 `fastboot boot`。
