# fajita 启动链与迁移流程审计

审计范围：在手机不可启动的前提下，对项目候选镜像、`ref/dipper-xnu` 的启动实现、`ref/edk2-sdm845` 的 fajita 平台和 HakiOS 的设备移植文档做主机侧静态核对。没有执行设备操作。本审计区分源码/镜像可直接证明的事实与必须在设备上观察的未知项。

## 结论

此前反复回到 9008 的候选并非同一条完整启动链：旧 `boot-fajita-no-usb.img` 在 UEFI FD 后没有 `XNUP`，根本没有可执行的 XNU；`boot-fajita-direct-apfs-v7.img` 虽含 kernel，却只有 4 MiB `ramdisk`，没有 dipper 的 `flashboot` 启动卷，不能视为 dipper 文档中的完整 UFS 根卷启动载荷。因而此前的启动结果不能证明“USB 导致 9008”，也不能用来断言当前完整候选会失败在哪个 XNU 初始化阶段。

在 dipper 的 loader 实现中，不带 `usb` 的 payload 仍会进入 `run_kernel()`；`oneshot` 只让 loader 跳过 USB fastboot 服务后执行内嵌 kernel。当前 `.work/candidates/fajita-singlecore-no-usb-v1/boot-fajita-singlecore-no-usb.img` 的 XNUP 结构已包含 `loader.efi`、`flashboot`、`kernel`、`ramdisk`、cmdline 和 `oneshot`，并使用单核、`ufs=2 root_apfs=ufs_darwin`。从主机静态证据看，它才与 dipper 的完整 UFS 启动路径相符；尚无该镜像的实机结果，不能宣称它已能启动。

## 参考仓库各自覆盖什么

`ref/dipper-xnu` 是主要参考。其 `docs/development/FIRST_INSTALL.md`、`docs/build/BUILD.md`、`docs/build/USERLAND.md`、`docs/install/STARTUP.md` 和 `tools/build/build-payload.py` 串起固定源码准备、XNU/loader/UEFI 构建、iOS 用户空间与固件准备、分区/镜像校验、临时启动和验收。该流程在小米 Mi 8（`dipper`）上有实机启动与桌面证据，但 loader、DT/身份、UFS 参数及驱动状态属于 dipper，不能直接当作 fajita 已验证流程。

`ref/dipper-xnu/work/edk2/Platform/Oneplus/sdm845/` 提供 fajita/enchilada 的 EDK2 平台源文件。当前 dipper 工作树对 fajita 加入了 `XnuLoaderDxe`，并将 `loader.efi` 编入 FV；`boot-fajita.img` 是 BootShim + UEFI FD 的 Android boot 镜像，不带 XNUP kernel payload。完整执行仍需要在该镜像后附上正确的 XNUP。

`ref/hakios-xnu-kernel/docs/porting/` 给出通用设备层/SoC 层/驱动逐步移植流程；其 `devices/fajita/` 是 provisional 骨架，明确所有手机驱动仍关闭。它不是可直接启动的 fajita 内核发行物。

`ref/hakios-uefi` 描述 HakiOS 在 Mu-Silicium 上的另一种 UEFI 集成流程，包括自有 BootShim 与 Boot Manager。当前 fajita 候选实际基于 `dipper-xnu/work/edk2` 的 EDK2 `XnuLoaderDxe` 路线，不是把 `hakios-uefi` 应用后的 Mu-Silicium 镜像；不要把两种 loader 启动入口混成一条链。

## dipper 启动链映射

1. 主机把 BootShim、SDM845 UEFI FD 和 XNUP 一起打入 Android boot v1 的 kernel 区域，gzip 后附 DTB。`mkxnuimg.py` 与 EDK2 的 `Silicon/platform.sh.inc` 定义了这个布局。
2. BootShim 将 FD 复制到固件地址，并把 FD 后面的 XNUP 复制到 `PAYLOAD_BASE`（当前为 `0xF0000000`）。
3. UEFI 的 `XnuLoaderDxe` 从 firmware volume 加载并启动内嵌 `loader.efi`。loader 从 `PAYLOAD_BASE` 解析 XNUP。
4. `loader.c::run_kernel()` 要求 payload 含 Mach-O `kernel`，放置 kernel、ramdisk 和 boot args，随后按 cmdline 初始化 UFS 服务并跳入 XNU。
5. UFS 根卷的 dipper 流程传入 `flashboot.apfs` 和第二个 payload ramdisk：前者映射为 `/dev/md0` 启动卷，后者为 `/dev/md1`；完整 iOS 系统从设备 `darwin` UFS 分区读取。主机只把两份 4 MiB APFS 启动卷放入 payload，不把完整系统塞进 XNUP。

## 候选镜像静态核验

核验方法：解析 Android boot v1 头、解压 kernel 区域、检查 `XNUP` 边界及每个 24 字节条目头，再核对条目长度和 payload 终止边界。下列结果来自当前工作区文件。

| 镜像 | XNUP 条目 | 静态判断 |
| --- | --- | --- |
| `.work/artifacts/fajita/boot-fajita-no-usb.img` | 无 XNUP | 确定错误：loader 没有 kernel，不能启动 XNU。 |
| `.work/artifacts/fajita/boot-fajita-direct-apfs-v7.img` | `kernel`, `ramdisk`, `cmdline` | 有 kernel，但没有 `flashboot`；不完整匹配 dipper 的 UFS 根卷配方。 |
| `.work/candidates/fajita-singlecore-no-usb-v1/boot-fajita-singlecore-no-usb.img` | `loader.efi`, `flashboot`, `kernel`, `ramdisk`, `cmdline`, `oneshot` | payload 长度、XNUP 条目和末尾边界一致；没有 `usb`；结构符合单核 one-shot + UFS 根卷路径。SHA-256：`ec613de648e2cbff8478dedfd3abbd0c3ca03e7c3b783ecb4a0d8807cde2f3cb`。 |
| `.work/candidates/fajita-checkpoint-999-v2/boot-fajita-checkpoint-999.img` | `loader.efi`, `kernel`, `cmdline`, `checkpoint` | loader return-path 自检镜像，不含 flashboot/root APFS，也不继续启动 iOS；保留作诊断产物，不作为本轮推荐启动镜像。 |

当前完整 one-shot payload 中实际的 cmdline 字符串为 `-v ufs=2 root_apfs=ufs_darwin vtimer_intid=27`；loader 在没有 `cpus=` 时自动追加 `cpus=1`，没有 `bootcpu=` 时使用当前执行核心。`27` 是 XNU 的默认虚拟 timer PPI，`19` 来自 dipper 的 SDM845 实测；fajita 尚无独立 timer 观测，因此该参数仍是假设。该候选是单核配置，不依赖 PSCI 启动其余核心。

还有一个构建一致性细节：当前 one-shot 的 XNUP `loader.efi` 条目早于最后一次 loader 重建，SHA-256 为 `db6053ff4da766c6b29eac0817ac9b84d094142992758a86fa129cdcf656f264`；dipper EDK2 FV 中 `XnuLoaderPkg/Binary/loader.efi` 为 `0b291aaaa4f96a4574565c7bf1c2c155eb928998661e3f95b0c829ccf1698eef`。当前 `XnuLoaderDxe.c` 从 FV 路径直接 `LoadImage`，所以这条 EDK2 路线上有效 loader 是 FV 副本，XNUP 中同名条目不会替换它。该不一致不解释已观察的 9008，但应在下一次生成发布候选时统一，避免更换到读取 XNUP loader 的 UEFI 入口后加载旧版本。

## 已知与未知

可以确定：旧无 USB 镜像缺少 XNUP；v7 缺少 dipper 配方中的 `flashboot`；缺少 `usb` 条目本身不会让 `run_kernel()` 被跳过；USB gadget 报错与 XNU 启动交接是不同阶段。

尚不能从主机判定：设备最后运行过的每个镜像与屏幕现象是否一一对应；XNUP 是否在手机物理内存被 BootShim 正确复制；GOP/MIDR 最终选中的平台、fajita timer PPI、UEFI 传入的 UFS Block I/O 是否被 XNU 正确服务、XNU 是否已开始执行。历史轮次没有保存完整 loader/kernel log，因此不能把 9008 精确定位到其中某一项。

设备上的 UEFI logo 只证明 fastboot 启动的 Android boot image 至少进入了 BootShim/UEFI 显示阶段，不证明 XNUP 被读取、loader 被运行或 XNU 已执行。反过来，`dev_state_default_address` USB 状态错误也不能证明或否定 XNU 是否启动。

## 处理结论

不再把多个 timer、checkpoint、USB 生命周期镜像排成多轮设备试验。当前主机审计完成后，唯一结构完整的现成候选是 `fajita-singlecore-no-usb-v1`；若后续要做一次设备确认，只应选这个候选并保留屏幕/loader 结果，不能再把旧 v7 或无 XNUP 的镜像当成对照。若它仍重启/进 9008，下一步应先取得一次完整的 loader 屏幕/日志证据，再修改启动链；不应继续盲改 timer、USB 或 GPT。

分区/APFS 记录及 EDL 输入状态仍见 [readme.md](readme.md) 和 [EDL_INPUTS.md](EDL_INPUTS.md)。
