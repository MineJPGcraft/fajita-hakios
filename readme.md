

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

## 本地参考仓库

上游仓库已分别克隆到 `ref/`，仅用于本地对照，不作为本项目源码提交：

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

- 已用 HakiOS 的 `tools/hakios-device.py new` 从公开兼容 DTB 生成 `devices/fajita` 骨架，并通过 `tools/hakios-device.py check fajita` 和 `tools/hakios-config.py defconfig`。
- 当前 9 个 SDM845 地址值来自兼容 DTB；modem/IPA 所需的另外 7 个值缺失，不能据此启动 cellular 功能。
- 所有手机专属驱动在 fajita 配置中关闭。dipper 的 A630、Goodix/触控、WCN3990 和存储参数未直接复用。
- EDL `boot_a/dtbo_a` 已提取出 3 个 SDM845 base DTB 和 16 个 Qualcomm DTBO；它们作为设备 EDL 树使用，不因 Android 版本不同而作废。官方 system/vendor 属于 Android 10 build `2101210516`，用于厂商固件与配置参考。提取和输入状态见 [EDL_INPUTS.md](EDL_INPUTS.md)。
- `scripts/prepare-fajita.sh` 只同步设备层、运行设备检查并生成 `fajita_defconfig`；通用首轮不改 loader 或 XNU。
- `patches/fajita-loader.patch` 与 `patches/fajita-gic-timer.patch` 是未验证的实验补丁，timer 值 19 仅来自 dipper，不能作为 fajita 已确认参数；暂不纳入通用准备流程。
- `scripts/apply-fajita-to-dipper.sh` 可在 dipper 的固定 SDM845 源码准备完成后，替换 loader 平台名、关闭未核验的 UFS 写入，并重建 ARM64 loader。

当前 `devices/fajita` 是移植草案，不代表已在 OnePlus 6T 实机启动或验证。

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

因此可以确认 GPT 已按上表写入，`darwin` 的首尾抽样也已写入。设备启动失败与 GPT 写入结论分开记录；没有完整实机 loader/kernel 日志时，不把 9008 归因于 GPT、USB 或某个 XNU 子系统。对 dipper 完整启动链与 fajita 候选的主机侧核对见 [fajita 迁移审计](FAJITA_MIGRATION_AUDIT.md)。已按审计修正 XNU timer 参数接线并重打唯一完整单核 one-shot 候选，细节和摘要见审计文档。
