

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
