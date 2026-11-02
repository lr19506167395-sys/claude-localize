# 更新日志

## 1.0.1

修复 Claude 自动更新后备份选择出错的问题，并支持 4.4212.0。

### 修复

- **还原/重装被旧版本备份卡死**：`best_backup()` 原先只判「是不是官方原版」不看版本，
  Claude 从 4.29.0 升到 4.4212.0 之后，仍会选中 4.29 那份带原版标记的备份，
  于是「恢复原样」被降级保护拦下报错、重装补丁也提示「请手动重装 Claude」，
  而旁边那份同版本的干净备份根本没被用上。现在只在**同版本**里选备份，
  同版本内才优先原版。
- **旧版本备份永不清理**：原版备份原先无条件豁免，导致旧版本那份几百 MB
  的备份长期留在 `/Applications`。现在版本对不上一律清掉，
  只保留当前版本的原版备份。
- **`status` 标签贴错对象**：显示的是最新备份的路径，却用另一份备份的性质打标签，
  把真正的故障掩盖了。现在两处都取自同一份。
- **`is_pristine()` 漏判**：除标记文件外，也认「仍带官方 TeamIdentifier 签名」，
  标记文件写失败时不会再误判。
- **`build.sh` 部署必定失败**：`printf %q` 产出的 `$'...'` 转义 AppleScript 不认，
  带中文的路径触发 -2741 语法错误。改用 `osascript` argv 传参。
- **API Key 同机可读**：`gui-config.json` 默认 0644，现在写入即设为 0600。

### 变更

- 已验证版本增加到 **4.29.0 / 4.4212.0**
- 备份选择加了进程内缓存，`codesign` 不再对同一路径反复执行

## 1.0.0

首个公开版本，面向 **Claude Desktop for macOS 4.29.0**。

### 功能

- **主界面汉化**：`ion-dist.asar` → `i18n/zh-CN.json`，39,482 条中 91.3% 已翻译
- **桌面壳层汉化（可选）**：`app.asar` → `resources/i18n/zh-CN.json`，并注册外壳语言白名单
  （官方随包的 `zh-Hans.json` 不在白名单里，4.29 上永远不会被加载）
- **原生字符串**：`zh-Hans.lproj` / `zh_CN.lproj` / `zh-CN.lproj` 的 `Localizable.strings`
- **斜杠命令双语菜单**：`/help 帮助与支持` 等 49 条，只改显示层
- **动态词表**：`i18n/dynamic/zh-CN.json`
- **四源合并**：手动锁定 → 官方 zh-Hans → 内置词表（含 javaht 上游）→ 自有翻译 → 英文兜底
- **备份分级**：官方原版备份带标记，「恢复原样」优先用它，零残留还原
- **残留清理**：javaht 散装语言文件、中断的解包目录、半截备份

### 安全措施

- 安装全程在临时副本上完成，验证通过才原子替换
- 三重完整性校验全同步：asar 条目 integrity / `Info.plist` header 哈希 / Framework 字典摘要槽
- ad-hoc 重签名保留 entitlements，写入 `identifier` 级 designated requirement 保证自动更新可用
- 还原后自动校验完整性，不通过会明确报错而不是静默失败

### 已知限制

- 仅支持 macOS
- 仅简体中文（`zh-CN` 槽位）
- Claude 自动更新会覆盖补丁，需要重新安装一次
- javaht 时代留在 `ion-dist.asar` 里的硬编码字符串替换无法逐字还原，重装官方 Claude 可彻底清除

### 致谢

本项目的思路、词表与部分实现来自
[javaht/claude-desktop-zh-cn](https://github.com/javaht/claude-desktop-zh-cn)（MIT）：

- 词表 / 硬编码替换表 / `Localizable.strings` 直接复用
- 斜杠命令双语菜单**移植**自其 `patch_slash_command_menu`
- 三重完整性同步、ad-hoc 重签、整包备份的实现**思路参考**自其脚本

详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
