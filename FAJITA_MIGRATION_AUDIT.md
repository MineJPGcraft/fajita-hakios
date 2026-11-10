# fajita 启动链与迁移审计

最新候选 `.work/candidates/fajita-touch-chipinfo-guid-hostchecked/boot-fajita-touch-chipinfo-guid.img`（SHA-256 `76154ec9baf09869542a5f68b6a3dc9e66af0400cc1dfe64289ec838bb3bcc8f`，12,834,816字节）已通过临时 `fastboot boot` 进入 iOS 桌面。用户面板值为 `P=1 F=9 D=0357d004 N=594 E=00000704000b8300 R=0001000000000000`：ChipInfo/板级准备通过，内核持续轮询，最后一次成功读寄存器 `0x0b`。N 会累计空帧，用户尚未明确实际触摸是否有效。接下来审计只针对报告和 HID 用户空间路径，不再更改供电、GPIO 或启动链。上一版 P80080000 证明芯片元数据 gate 拒绝；根因是 EFIChipInfo.h 宏 GUID 61224FBE... 与 BSP DEC/安装二进制 B0760469... 不同。安装接口 revision 0x10002，GetVersion/ID 偏移 8/24，ABI 无需改。失败查询 P 低位分别报告 1 缺 locate、2 Locate 错误、3 空接口、4 revision、5 缺方法、6 ID 调用失败、7 version 调用失败、8 未知芯片版本。未知元数据仍跳过，不探测备选 GPIO 页。

## 当前结论（2026-11-02）

上一候选 `.work/candidates/fajita-touch-command-poll-guarded-hostchecked/boot-fajita-touch-command-poll-guarded.img`（SHA-256 `67ac41d2579b8e0a2bbe8db99744b5e17fb504504214f0664911d36d900e8b9a`）已实际执行 fastboot boot，Sending/Booting均OKAY，但用户确认进入9008，并转录最后一行 `fajita-power: GPT100a 1`。对应日志与原话记录在该目录 `runtime-attempt-01.json`，不能再称未上机，也不能把转录直接当作GPIO88访问已成功。

本轮修复：EDL base0/base2为SDM845 v2.1/v2，base1为v1，旧审计遗漏compatible并统一套v1。GPIO88的v1/v2地址分别为0x03d58000/0x03558000，GPIO125为0x0397d000/0x0357d000，其余三个触控GPIO地址相同。loader在触控MMIO前通过EFIChipInfo验证ID321及版本0x10000/0x20000/0x20001；未知返回P80080000且不访问触控寄存器。成功后最后追加prepared和tlmm两参数，XNU先验证供电再验证布局，缺失/未知布局SKIPPED detail7且不创建触控线程。输出GPIO改为保留无关位并只验证输出锁存bit1，独立输入bit0不再强制同步。本轮新版已构建为 `.work/candidates/fajita-touch-tlmm-layout-hostchecked/boot-fajita-touch-tlmm-layout.img`，SHA-256 `5df2be03fa7330426704fd7733201b09655f41a38d6fdbb0d5039956a4bfaa7e`，12,828,672字节。主机套件、实际FV/XNUP匹配、编译后live ABI及16文件补丁重放通过。随后经授权确认设备在fastboot，并执行临时fastboot boot，Sending/Booting均OKAY，**用户确认进入桌面但触摸无响应；P80080000/F10/D6、N/E/R为0，芯片查询gate拒绝，触控MMIO未执行**；不因这些修复宣称9008或触摸已解决。

最新补充反馈为卡在 `fajita-power: RPMh ldoa28 ...`；具体启动镜像及红带有无尚未确认。该行在TPL31事务前打印，不能据此判定准确故障指令。已单独修复FATAL前CPU_ON时无法显示R码的诊断缺口，并构建 `.work/candidates/fajita-touch-held-panel-hostchecked/boot-fajita-touch-held-panel.img`（SHA-256 `ee2e6fc81a5af760c9ce746e2807b72442fe4caf5fee03864e09ad2077d8b912`），主机验证通过，未上机。报告记录与构建镜像分开，不猜测两者对应。

最新实测是 RPMh 供电候选进入桌面但触控无响应，用户未看到 FT/power 提示，尚不能确认供电确认位或 I²C 成功。本轮已实际加入小型持久诊断面板和传输错误遥测，并修复运行期 F01 CRC 状态6的有界等待及 unconfigured/reset 重配顺序。新候选 `.work/candidates/fajita-touch-panel-hostchecked/boot-fajita-touch-panel.img` 为12,826,624字节，SHA-256 `67ec39dffff4ba68df06eeeb9002e2d854dff03435a27318a81e1201657c33f8`，主机检查后已获准执行 `fastboot boot`，Sending/Booting 均返回 OKAY，用户确认进入桌面、触控无响应；面板 `P=80070003 F=10 D=6 N=0 E=0` 证明RPMh返回SKIP，内核在prepared gate跳过触控，没有I²C尝试。触发后异常会FATAL，当前SKIP只可能来自触发前拒绝或成功回滚；尚不能区分具体分支。

面板每两秒只画左下390×84区域，距底部80像素，显示 `P/F/D/N/E`：供电结果、内核阶段、细节、有效报告轮询数和最后I²C尝试；字段含义见 `devices/fajita/touchscreen.md`。启用时不重新打开旧全屏日志控制台，不改变 Qualcomm USB DXE、one-shot USB服务绕过、CPU7/UFS交接或供电时序。主机回归覆盖真实renderer/reader、缓存范围、旧ABI/边界、CRC恢复和脚本负例；不能代替触控实测。没有刷写boot或其它分区，GPT/APFS未改；随后仅执行了获准的临时启动。

### RPMh SKIP 后的窄修复

厂商 `qcom-rpmh-mailbox.c` 的 ACTIVE 完成路径只清 CMD_ENABLE/WAIT及IRQ，不清CONTROL；下次触发前才清TRIGGER→ENABLE。因此旧 `CONTROL==0` 门槛排除了已完成槽的合法状态。当前helper仅新增接受 `ENABLE`、`ENABLE|TRIGGER`，仍要求STATUS空闲、CMD_ENABLE/WAIT为零、无pending IRQ、两槽同资源检查通过，且在首个命令字写入前归一旧控制位。回滚和完成恢复只写 `saved.control & ~TRIGGER`，未知位、TRIGGER-only、不确定回滚和触发后错误仍拒绝或FATAL。

新四行面板以 `R` 分辨初始资源拒绝、无可用槽、重检变化、触发前回滚和FATAL；不改变供电确认门槛。真实helper、loader及renderer/reader主机回归和四个生产文件的干净补丁重放通过，内核与实测面板版逐字节相同。该修改是否命中手机的实际SKIP条件仍未实测，不能写成触控已修复。

最终候选 `.work/candidates/fajita-touch-idle-amc-hostchecked/boot-fajita-touch-idle-amc.img` 为12,832,768字节，SHA-256 `a68a8f1ebdfc868d5c5574f8fd7939473eb68420946bcf8d0ccb695163bcc972`。最终适配脚本返回0，打包前二次source校验与FV/XNUP内嵌loader核对通过；完整XNU all仍有既有export失败。该候选尚未实机启动，本次新增构建未操作手机。

### 此前触控供电阶段（2026-11-01）

当时实测边界：安静桌面版已获用户确认正常；触控首版与继承供电版均在最后可见 secondary-entry 标记后进入 9008；固件 Vreg 版则在 `handoff: UEFI core 0` 后进入 9008。只改 `fajita_touch=0` 的单变量对照版桌面稳定至少一分钟；启用触控的延后线程版仍进入 9008。当时已确认日志尾采样存在绕环缺陷，随后以正确的环尾和独立状态修复；activationd/lsd 的最后可见文本不能当作复位原因，详见“触控诊断调整”。

本轮重新从原始 DT 对照触控：`audit-fajita-touch-dt.py` 核验了3份 base DTB、7份 fajita overlay、固定厂商 pinctrl 源码与编译头文件。修正 IRQ125 active 配置偏差：DT 要求 GPIO/input/pull-up/16mA，现用 `0x1c3`。AVDD 的 `0x2de600` 原来换算有误，实际是3,008,000µV；旧 Vreg 实验请求的3000mV作为失败历史保留且明确标错，默认仍不启用。参数集中到 `fajita_touch_board.h`，构建前核对原始输入与摘要。GPIO49/50的个别SDA/SCL角色没有被DT命名或有效传输独立证明，审计中明确保留这一限制。

实际驱动与构建校验已修改：生产总线 mock 覆盖引脚配置、三页映射、0x40/0x41线地址、重复 START、ACK/NACK、有界恢复及原配置恢复；启动、报告与独立状态reader检查也通过。真实XNU构建已重新编译 `fajita_synaptics.o`，未见编译错误；完整all仍因已有export检查失败，不能称完整构建成功。最新供电候选已打包，本轮没有USB/刷写/启动操作。

此前继承供电探测缺少厂商完整上电/复位流程。现在新增 loader 侧实现：CPU_ON 前从本机 DT 的 `0x0c3f000c` 字典取 cmd-db 地址/长度，在受限 carveout 内解析 `ldoa28`，使用本版厂商枚举对应的 ACTIVE TCS0..1、3008mV/enable=1/HPM=7，同步完成后执行 AVDD→10ms→GPIO88→10ms→GPIO99高10ms/低10ms/高→active pinctrl→100ms。固件核在 TPL31 与 IRQ/FIQ 屏蔽期间独占该本地调用窗口，内核不再写 RPMh。此实现并不证明全部固件上下文的槽位所有权或实测电压，仍未读到真实 Product ID/报告。

新后端审查修复了一个未上机的新缺陷：不确定事务若在 TPL31 返回通用清理路径，日志会调用 GraphicsConsole 并尝试 RaiseTPL(16)。现 FATAL 当场进入无固件协议的内存日志/原 framebuffer 红带 hold，不恢复 TPL、不交接内核，也不覆盖可能仍执行的命令。成功路径恢复槽位字段、IRQ enable、原 DAIF/TPL；busy/无效输入跳过触控。loader 把 `fajita_touch_prepared=1/0` 放在 payload 参数之后，内核 power 模式只接受 ready=1，inherit 参数不能绕过。

`scripts/test-fajita-touch-host.sh` 已覆盖生产 parser/sequence/transaction/backend、启动、GPIO传输、报告、共享日志和最终 acknowledgement；包括严格编译、ASan/UBSan、故障清理、两个槽位、TPL/DAIF、无 console 的 FATAL、参数边界及8组设备树常量负例。新镜像 `boot-fajita-touch-rpmh.img` 为12,826,624字节，SHA-256 `a0c4a78d4db47244412207c0f13b432d13c44f54e631df34f990ee972bd15d03`，归档 `.work/candidates/fajita-touch-rpmh-hostchecked/`，FV/XNUP loader 一致。随后已获准执行 `fastboot boot`，Sending/Booting 均返回 OKAY；用户确认进入桌面但触摸无响应，未看到FT/power提示；触控与此前9008原因未获证明。命令记录见候选目录 `runtime-attempt-01.json`，未刷写分区。

首次桌面成功：用户报告 `boot-fajita-ufs-cache-scope.img` **进入 iOS 桌面**，伴随持续日志/闪屏、触摸不响应。该版本 SHA-256 为 `3a2e0ab8a48c01c3a670a4179abba72e1a3e06b9fc49eddca9319617798a562a`；镜像、原 loader 源码/二进制、bootargs、prefix、XNUP、日志及用户反馈保存在 `.work/candidates/fajita-first-desktop/`。这证明本次实际越过了缓存交接并到达用户空间，未证明所有外设工作。以下失败记录保留为历史证据。

上一版 `.work/build/fajita/boot-fajita-singlecore-no-usb.img`（SHA-256 `546689dbf549ede970c4ae55eccb80c5b137718aec3bb85f3b4913be96974fb2`）实机仍进入 9008，已停止推荐。没有完整 loader/kernel 输出，不能确认它执行到了 XNU，更不能把编译成功、UEFI logo 或主机设备名称当作 XNU 启动证据。

本轮修改代码并构建 `.work/build/fajita/boot-fajita-ufs-no-usb.img`，修复下述三处主机侧可确认的问题。随后经授权执行了 `fastboot boot`，传输与 Booting 均返回 OKAY；用户反馈屏幕出现大量日志，看到 `jumping to` 或内核文字，稍后进入 9008。这一版也确认实机失败，不能继续称为“尚未实机测试”。该反馈将范围缩小到 loader 交接附近或之后，但没有确认 core 7 已执行 XNU。未刷写 boot、未更改 GPT，也未禁用 Qualcomm USB DXE。

这份已实测失败的镜像为 12,804,096 字节，SHA-256 `03be952478163c4952eda415f2391427e5880119757bbe09f181e57b8a085670`；当时构建日志为 `.work/build/fajita/apply-ufs-bds-final.log`。

失败镜像及其 prefix、XNUP、摘要、构建日志和实测记录已保留到 `.work/candidates/fajita-ufs-bds-tested/`。后续诊断版单独命名为 `boot-fajita-ufs-observe.img`，不覆盖这份实测镜像。

## 本次实测后的故障响应

代码审查找到一条确定的复位路径：loader 的 `exception_handler()` 原来会输出 UEFI 异常寄存器，然后无条件 `ResetSystem(EfiResetCold)`，即使 fajita 配置禁止落盘日志。这会销毁唯一可见报告，但没有证据表明本次一定走了该分支。

`patches/fajita-loader-fault-observation.patch` 将无持久日志平台的同步异常/SError 改为显示 ESR/ELR/FAR 等寄存器并保持，不再由此分支主动冷重启；增加 context-save、cache-ready、PSCI 请求、secondary-entry 标记及每两秒内核日志字节数/末尾摘要。它不是对原始异常的恢复，也不能拦截 TrustZone/固件外部的复位。

另一个待验证的并发风险是 `run_on_other_cpu()` 原先会在启动 XNU 后让固件核心继续接收中断 24 秒；源码自己注明 XNU 会配置共享 GIC。无落盘日志模式现在在 PSCI 启核前即屏蔽固件本核 DAIF/Group1，返回时恢复，保留原有同步 UFS mailbox 服务。没有关 USB 驱动、时钟或控制器，也没有启动 loader USB 服务。这个改动有源码依据，但仍不能认定它就是 9008 的根因。

独立核对当前 PSCI 汇编没有发现参数/寄存器覆盖：`CPU_ON` 的 x0/x1/x2/x3 分别为函数号、MPIDR、secondary entry、启动上下文；入口取出 boot args 到 x0 后跳转。APSS watchdog 在 SDM845 PrePi 中被关闭，Haven watchdog 的 loader 启用分支仅适用于 redwood；没有证据将本次重启归因于 loader 自行开启的 watchdog。GIC PPI、实际异常位置和 XNU 执行阶段仍需可读输出。

诊断版整条构建脚本返回 0，镜像为 12,810,240 字节，SHA-256 `753c5da5d672cc3baae402d255b0b0d9168f10b1fc50d77ab95b2941e1b95e2c`，日志为 `.work/build/fajita/apply-observe.log`。编译后的 COFF 反汇编确认 DAIF/Group1 屏蔽位于 PSCI SMC 之前，且返回分支恢复；无日志异常分支包含保持循环。压缩 FV 内嵌 loader 与 XNUP 一致，loader 摘要为 `580e9b38efd604710ea7afbdf1f5768a3ada6fda266671dffa10ccc9ab9ea93c`。

随后这份诊断镜像已两次通过 `fastboot boot` 传输并得到 Booting OKAY；用户补充的准确屏幕顺序为：

```text
the kernel will run on core 700 (this is core 0)
jumping to f74594a0
handoff: UEFI context saved, preparing caches
```

最后一行后约两秒进入 9008。没有看到 `cache preparation complete`、PSCI 请求或 secondary-entry 标记。`700` 是十六进制 MPIDR `0x700`，对应当前 `core_shift=8` 配置的 core 7；`jumping to` 打印在真正交接之前。这次证据把故障区间缩小到交接前的缓存准备块，尚不能确认具体是哪条缓存指令/哪段内存，也没有证明 XNU 已执行。该区间还未执行提前屏蔽固件中断和 PSCI 的改动，不能把本次故障归因于它们。镜像、loader、prefix、XNUP、构建日志、反汇编和准确实测记录保存在 `.work/candidates/fajita-ufs-observe-tested/`。

### 按交接方式收窄缓存维护

旧代码在同核和异核启动前都遍历固件内存表，对全部已分配的 loader/boot-services/runtime/ACPI 区域逐行清缓存，并再次清理当前栈。这原本用于固件本核关闭 MMU/cache 后返回的路径；当前 `bootcpu=7` 路径中，固件留在 core 0，未经过这个 MMU-off round trip。

`patches/fajita-loader-secondary-cache.patch` 将上述全固件/栈清理限制在 `kernel_mpidr == this_mpidr` 分支，保留同核 selftest 行为。异核分支仍对 XNU 自有 `[phys_base, top)` 执行原来的 clean+invalidate 和 DSB；`run_on_other_cpu()` 原有的启动参数、stage/done、PSCI 模式、trampoline 和 `loader_ctx` 的显式 clean+DSB 全部保留。`block_start()` 和 `charger_poll()` 原有共享结构发布也保留。新增分支和 XNU 地址范围标记，用于区分全固件扫表和 XNU 窗口维护。

缓存候选单独命名为 `.work/build/fajita/boot-fajita-ufs-cache-scope.img`，不覆盖实测失败版。构建时尚未确认能越过缓存准备；随后用户已报告进入桌面（见当前结论），故本次运行的交接阻塞已越过。没有更改 GPT、APFS、USB 硬件开关或内核启动参数；不能由一次成功推定后续启动可靠性。

该候选整条主机构建脚本返回 0，大小 12,806,144 字节，SHA-256 `3a2e0ab8a48c01c3a670a4179abba72e1a3e06b9fc49eddca9319617798a562a`；loader 为 74,240 字节，SHA-256 `d4f7ff2fdcb99c17f7ea34b7f770f1564d25132c0cd5ef4bd24db0baff840061`。日志为 `.work/build/fajita/apply-cache-scope.log`，检查记录为 `cache-scope-verification.json`。最终压缩 FV 中的 loader 与 XNUP 一致；XNU 内核摘要不变，仍有已记录的后置导出检查限制。

缓存候选 COFF 的 `run_kernel` 中，`0x50ec b.ne 0x51e0` 选择异核分支，`0x51ec b 0x522c` 跳过固件扫表和栈清理；XNU 窗口的 `DC CIVAC` 在 `0x5260`，`DSB SY` 在 `0x5278`，之后才打印 cache-ready 并在 `0x52ac` 调用 `run_on_other_cpu`。第二次执行源码准备阶段不改变 loader 源码，确认追加补丁不会令旧观察补丁的反向检查失效。该段地址对应归档的缓存候选，不是后续新 loader 的固定地址。

## 首次桌面后的显示与输入

首次成功镜像仍带 `-v`，使 XNU 的 boot-video `v_display=0`，可在屏幕输出内核文本。另一方面，故障观察补丁在 `no_log_write` 平台每两秒打印一次内核进度；one-shot 跳过 USB 命令循环，因此原本在该循环设置的 `screen_quiet=1` 从未发生。两条路径会与 iOS 争用同一 framebuffer，是当时代码中明确存在的刷屏来源；后续安静版已获用户确认正常。

`patches/fajita-loader-framebuffer-owner.patch` 在 secondary-entry 标记之后设置 `screen_quiet=1`，让 loader 后续输出只进入内存日志；从核返回/启动超时恢复原标志，UEFI 异常处理器先重新打开屏幕输出再打印寄存器。安静版同时移除 `-v`，其余启动参数、内核、APFS 和缓存交接保持成功基线。用户已确认 `.work/build/fajita/boot-fajita-desktop-quiet.img` 安静版正常，归档见 `.work/candidates/fajita-desktop-quiet-tested/`。

安静版主机构建和最终镜像检查已通过，大小 12,806,144 字节，SHA-256 `4c14a41d0663f4c1c6ee8530e88f5fedc8fa37418f93a7f2c0f5dc88f448f73e`；loader SHA-256 `486e8644029396c38a57cb005c30127d0e9fa41c4659fa2000f88acef42f42d2`。三项 loader 补丁反向/正向重放与当前源码一致，第二次准备保持源码不变；负向检查确认遗漏 framebuffer 让出或异常可见性会失败。最终压缩 FV 与 XNUP 的 loader 逐字节一致。记录为 `.work/build/fajita/desktop-quiet-verification.json`，构建日志为 `apply-desktop-quiet.log`。XNU 未改变，仍保留前述后置导出检查限制。

桌面会话中主机经授权执行 `lsusb`，没有手机/xnuloader 枚举，故没有可用的 host 命令通道，也没有执行截图、停止或输入操作。当前成功镜像有 `oneshot`、无 `usb`，按设计跳过 loader USB 协议服务；保留 Qualcomm USB DXE 硬件状态不等于存在 xnuloader USB 端点。此次没有重启或刷写手机。

现有 EDL overlay 与官方型号 18801 固件提供了总线/GPIO/供电及 S3706B 身份线索，现已新增本机 Synaptics RMI 驱动。prepared XNU 的 dipper 驱动是 ST FTS/SE14/0x49，本机是 Synaptics/SE12/0x20；不能直接启用 dipper 参数。无需新的 EDL 提取即可继续主机侧驱动工作，见 `devices/fajita/touchscreen.md`。

### 首个触控候选回归（2026-11-01）

用户确认 `boot-fajita-touch.img` 再次进入 9008，最后可见 `handoff: secondary core reached the entry stub`。镜像 SHA-256 为 `c2ebe8a8ad44ee5761809d61eab3391633e8daae3a2c65d29c686e00bb40a593`；源文件、内核、载荷和实测记录已保存至 `.work/candidates/fajita-touch-v1-failed/`。该标记之后 loader 即进入屏幕静默，故它证明 secondary stub 到达，不能定位后续复位。

审计确认新增触控供电代码有两处缺陷：未经本机确认便读取 dipper 的 cmd-db 表地址 `0x85fe0000`；复用 DRV2 TCS0 时未与驻留 UEFI/UFS 核心仲裁。DRV2 地址有本机 DT 依据，但不代表内核拥有槽位；检查 idle 不能代替锁。现在删除了内核全部 cmd-db/RPMh/TCS 映射和读写，不用延时掩盖竞态。这些静态缺陷尚未被证明是准确复位原因。

修订版 loader 在 CPU_ON 前通过 `EFIPmicVreg.h` 定义的固件协议为 PMIC0 LDO28 设置 3000 mV，启用后以有界等待确认 SwEnable/VregOk；生成 `fajita_touch_vreg=1/0` 放在 payload 参数之后，因为本 XNU 数字参数采用最后一个匹配。只有成功确认才创建触控线程，失败时不访问 GPIO。协议缺失/调用失败不会主动请求系统复位。内核只保留五个已核对的 TLMM 页。

固件 Vreg 镜像为 12,816,384 字节，SHA-256 `31414219ae130efb10b259b1f9b182883b19aa4c106cf8f708294349e0dce232`。主机检查通过后已执行 `fastboot boot`，用户确认最后可见 `handoff: UEFI core 0`，随后进入 9008；不能再称为未实测。二进制核对未发现 ABI/枚举错位，但该协议确实执行实际 SPMI/PMIC 访问，错误返回保护覆盖不了方法内部复位。

继承供电版 `boot-fajita-touch-inherit.img`（SHA-256 `fa818bb35ae036a37b1ef5357e799d2087555b927dd98c8bdd2ed045b8def4e2`）已撤掉 Vreg，loader 与安静成功版逐字节相同；内核仅访问 SDA/SCL/IRQ 三页、不改 GPIO88/99/reset。用户确认仍在 secondary-entry 标记后进入 9008，实测记录见 `.work/candidates/fajita-touch-inherit-hostchecked/`。该结果不证明问题一定发生于首次 MMIO，因为此前没有可见线程阶段标记。

### 触控诊断调整

源码审计未发现当前线程自终止、`ml_io_map` 原型或 16 KiB 页偏移处理错误。`ml_io_map` 使用的 `KMA_NOFAIL` 仍可能在资源失败时 panic，普通返回值检查不能覆盖；本次只有 48 KiB 映射，暂无耗尽证据。另确认输入框架有五点持续 1500 ms 后主动 `loader_return()` 的路径，现对 `fajita_touch=1` 默认关闭，只有 `fajita_touch_exit=1` 才启用，避免异常触点触发退出；尚无证据表明此前真的触发过它。

诊断版 `boot-fajita-touch-observe.img` 不改电源、reset 或 USB 硬件。触控线程先等待 30 秒，再在映射、各 GPIO 快照、pinmux、页选择、PDT 和扫描配置前发布整行标记并留出可观察时间；loader 仅在 `fajita_touch_diag=1` 时暂时显示原有内核日志尾部，限前 120 秒，普通安静模式保持不变。实测出现 logo 与周期日志，用户确认不到 30 秒进入 9008，未见 `fajita-touch-diag:`。该时段早于配置的首次触控硬件访问，未证明触控线程已启动；不能据此继续猜 GPIO 或供电。

随后从这份完整归档制作单变量对照版 `boot-fajita-touch-control.img`：所有载荷和固件完全相同，只把 `fajita_touch=1` 改成 `fajita_touch=0`。完整镜像校验通过，SHA-256 为 `9ee73d1ba031695cd5583444612e1c2eb13609bc0ccde57ab745f988d0bfc8ab`。临时启动后用户确认桌面稳定至少一分钟，触控按设计关闭。此版仍包含同一静态 SYSCTL 和 loader 诊断绘屏；回归依赖触控启用分支，不能据此归因于静态注册，也尚不能判定是线程还是 GPIO。

针对这一边界，实际修改 `fajita_synaptics.c`：BSD/devfs 初始化仅分配并提交延时 `thread_call`，30 秒到期后由回调创建独立输入线程；回调不访问硬件、不执行诊断等待。早期标记和五指退出 gate 改用常量 `loader_log_line()`，触控格式化日志不再重复调用内核 `printf`。独立审计确认回调签名、延时单位、引用释放及一次性生命周期未见阻断项；线程可能先于回调结束标记运行，不应误读这两行的交错顺序。这是早期启动隔离措施，尚未证明修复复位。

`boot-fajita-touch-deferred.img` 的 SHA-256 为 `283905596830941f41bcddbc23a466ed85d0224b6185251f9c9bdc92660bd710`，完整材料位于 `.work/candidates/fajita-touch-deferred-hostchecked/`。相对失败诊断版仅更换 kernel 载荷；loader、固件 prefix、FDT、APFS 与启用触控的 bootargs 保持相同。实际新启动函数的主机 mock 验证了禁用/继承供电 gate、到期前不创建线程、参数边界、重复调用与失败跳过；严格 C11 + ASan/UBSan 通过，LSan 因主机 tracing 环境关闭。报告解码与打包核验通过。完整 XNU all/export 检查仍失败。该候选临时启动后用户确认再次进入 9008；最后可见 `name=com.apple.mobileactivationd, flags = 0x8, requestor = lsd[29], error = 3:No such process`，本版复位耗时未提供。不能沿用上一版“不到 30 秒”的时间，也不能把该进程文本当作 panic 回溯；没有刷写分区。

### 日志尾与独立状态修正

`note_kernel_progress()` 原来只读 `log_len` 的物理末 240 字节；writer 首次绕环后长度冻结，新日志改从 `log_head + ring_at` 写入。主机样例已复现“新触控 marker 跨过环尾，而屏幕仍取旧物理尾”的缺陷。这能解释为什么最后可见文本可能陈旧，**尚不能解释或证明本次 9008 的原因**。

已用纯几何 helper 按最新游标生成最多两段、总计不超过 240 字节的时序尾部；变化中的元数据最多重取三次，失败显示 unavailable，不回退旧尾。另给触控分配独占 64 字节缓存行的状态对象，单个原子 64 位值包含阶段与细节，在访问前 release-store、clean 本缓存行并 DSB。原 live ABI 前 96 字节不变，尾加 magic3/size/pointer，总长 120；loader 在 cache 操作前验证布局、内核地址范围及 64 字节对齐，并在普通尾日志后显示 `FT=...`。粗阶段仍保留 4 秒观察窗；紧邻外部复位的单次状态可能来不及显示，不保证捕获准确故障指令。

主机测试覆盖绕环、新 marker 跨界、异常元数据、旧 ABI、错误 magic/size、未对齐及越界指针；使用生产 reader 和实际编译内核的 Mach-O 数据检查，而非仅检查字符串。两个导出补丁均可从保存的基线逐字节重放；staging 重复两次内容不变。触控供电、reset、GPIO 地址和 USB 策略未改。此轮先修诊断链，不将它宣称为触控修复。

最终诊断镜像 `.work/candidates/fajita-touch-live-hostchecked/boot-fajita-touch-live.img` 为 12,820,480 字节，SHA-256 `fa150948589ecc40c84db50a2268767dba8b2c7612c9bb273e13163a24d8c17b`。适配脚本最终返回 0，实际解压 FV 的 loader 与 XNUP 一致；loader 摘要 `5f06f82626f8e11fe2a82c450a1eac57a393c6b5c48ef67cff4c15054932a2e7`，内核摘要 `8d9ae59d0092db5a72c3c8c0eddfcb6fd8620eb509cbcd0e9620fb50e1462a65`。完整 XNU all/export 检查仍有已知失败，报告的 126 个符号/方法名与上一版一致。该镜像尚未实机启动，本轮没有访问设备；后续应依据独立 `FT=` 状态定位，而不是把通用日志尾当作故障点。

## 已修复的启动链问题

### 1. 不在 DXE 调度期间执行 loader

旧工作树的 `XnuLoaderDxeEntry()` 在注册 BootOrder 前直接调用 `LoadImage/StartImage`。它只有 variable 架构协议依赖，没有 GOP、Block I/O 或控制器连接完成依赖。该调用会占住 DXE 调度，不保证控制台与 UFS 已就绪。

已恢复 `ref/dipper-xnu/bootloader/uefi/XnuLoaderPkg/Driver/XnuLoaderDxe/` 的 BDS 路径，并把源码纳入本项目的 `devices/fajita/uefi/files/XnuLoaderPkg/Driver/`。DXE 只把 loader 放到 BootOrder 首位，不自行执行。参考平台的 `PlatformBootManagerAfterConsole()` 会连接其余设备（`EfiBootManagerConnectAll()`），然后由 BDS 执行启动选项。

### 2. UFS 服务与 XNU 不能占用同一个核心

`loader.c::run_on_other_cpu()` 才会持续执行 `block_poll()`，为 XNU 的 `redwood_ufs.c` mailbox 请求调用固件 Block I/O。同核 `jump_to_kernel()` 不会继续运行这条服务循环；内核 UFS 请求会等不到回复。

旧 `bootcpu=0 cpus=1 ufs=2` 在 UEFI 位于 core 0 时存在这个确定的服务缺口。当前 `devices/fajita/bootargs.txt` 为：

```text
bootcpu=7 cpus=1 ufs=2 root_apfs=ufs_darwin vtimer_intid=19
```

这里 `cpus=1` 只限制 XNU 用一个核心，UEFI 仍留在原核提供 UFS 服务。`bootcpu=7` 沿用 dipper 的核间交接选择；没有启用 dipper 的其它六个 XNU 核心，也没有启用其 GPU、触控或 Wi-Fi 参数。

首次桌面成功版在这组参数前带 `-v`；安静候选移除了它，以关闭直接写屏的 XNU 文本控制台。需要重新观察内核启动文本时可单独制作带 `-v` 的诊断候选，不应覆盖已归档的成功版本。

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

- `verify-fajita-source.py` 在编译前检查已同步的 BDS 源码、fajita DSC 库选择、四种内存表、同核 UFS 检查、loader USB 绕过、异常保持、中断隔离顺序及同/异核缓存范围。它还检查六项 secondary bootstrap 显式清理仍在；主机负向检查确认缺少缓存分支或启动上下文发布会被拒绝。观察补丁与缓存补丁已在项目内暂存目录反向/正向重放，结果与当前源码逐字节一致。
- `verify-fajita-boot.py` 校验 boot/FDT/XNUP 长度、对齐、panic-log 边界、条目集合、命令行以及 loader/kernel/两份 APFS 的逐字节对应关系。还从最终镜像的 LZMA 压缩 FV 中解析真正的 APPLICATION FFS，确认固件内嵌 loader 与 XNUP 的 loader 一致，而非只比较旁边的中间文件。
- loader PE 时间戳固定。EDK2 FD 在重复构建中仍有差异，具体来源未定位，不能称为整个镜像可重现；每轮以 `.work/build/fajita/SHA256SUMS` 为准。
- XNU 使用现有工作树增量构建，内核为 15,442,352 字节，SHA-256 `02bcdfb941ba6fe25835aa47f82f28e775e37794e6113adf7b364eb19b04980a`。构建仍有已记录的 symbolset / `check_all_exports` 后置检查失败；不能据此宣称完成了干净的全量 XNU 构建。本轮没有改变 XNU 内核代码。
- Python APFS 读取器确认启动卷 `/sbin/launchd`、第二卷 `/sbin/init` 与 SpringBoard plist 存在、所有者为 root；解压读出的 stage0/init 与 `userland/obj/{stage0,init}` 逐字节相同。本地系统镜像的 `SystemVersion.plist` 可读取。这是主机镜像检查，不是设备上已启动用户空间的证据。C APFS 检查工具因缺少 OpenSSL 开发头文件未编译成功，不算其验证通过。

最终镜像、prefix、XNUP、构建日志与校验日志都在 `.work/build/fajita/`。中间文件保持在项目内。

## 历史候选与剩余未知

旧 `.work/artifacts/fajita/boot-fajita-no-usb.img` 没有 XNUP，不能启动内核。`boot-fajita-direct-apfs-v7.img` 有 kernel，却缺少完整 UFS 根卷配方的 `flashboot`。后来完整的同核候选仍在实机进入 9008。这些是不同镜像，不能合并成“禁 USB 必然造成 9008”的结论。

dipper 是主要参考，覆盖 `docs/development/FIRST_INSTALL.md`、`docs/build/BUILD.md`、`docs/build/USERLAND.md`、`docs/install/STARTUP.md` 和实际 loader。`ref/hakios-uefi` 的 Mu-Silicium 集成属于另一条 UEFI 路线，不能直接混用；HakiOS 通用设备层仍是 provisional 状态。

用户已报告缓存候选进入桌面，随后确认安静版正常。触控首版回归进入 9008；其修订版、可用的 USB 控制通道、存储读写完整性及其它外设仍需验证。PPI 19 最初来自 dipper，本次成功沿用该值，但没有在 fajita 单独执行 timer-PPI 测量，不能据桌面反馈代替该测量。一次成功也不能保证后续不进入 9008。

GPT/APFS 已写入的具体过程和首尾回读范围见 `readme.md`。没有新证据前不重改 GPT；不刷 boot 分区，实机启动仍采用经授权的 `fastboot boot`。
