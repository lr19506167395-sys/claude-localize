# 更新日志

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

词表与 `Localizable.strings` 来自 [javaht/claude-desktop-zh-cn](https://github.com/javaht/claude-desktop-zh-cn)（MIT），
详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
