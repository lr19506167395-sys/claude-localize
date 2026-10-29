# claude-localize

Claude Desktop 中文界面补丁（macOS，简体中文）。

不修改任何在线服务，只替换本地语言资源，并用 ad-hoc 重签名保证 Claude 能正常启动和自动更新。

> **支持版本：Claude Desktop for macOS 4.29.0。** Claude 升级后布局可能变化，装之前先用
> `claude-localize status` 确认版本。

## ⚠️ 使用前必读

本工具会**修改 `/Applications/Claude.app` 内部资源**，需要输入管理员密码。请了解以下风险再使用：

| 风险 | 说明 |
|---|---|
| **修改官方应用** | 会重打包 `ion-dist.asar`（可选还有 `app.asar`），并 ad-hoc 重签名 |
| **签名变成本地签名** | 官方 Anthropic 签名被替换，系统不再认为它来自认证开发者 |
| **Claude 升级后失效** | 自动更新会覆盖补丁，界面变回英文，需要重新安装一次 |
| **无法撤销的部分** | javaht 时代的硬编码字符串替换是内容级改动，无法逐字还原 |
| **出问题可恢复** | 安装前自动整包备份；实在不行重装官方 Claude 即可彻底还原 |

**首次打开 Claude汉化.app 被系统拦截时**：在 Finder 里右键 → 「打开」 → 再点「打开」。
这是 macOS Gatekeeper 对未签名应用的正常拦截，不是应用本身有问题。

## 安装

### 方式 A：从源码安装（推荐）

```bash
git clone https://github.com/lr19506167395-sys/claude-localize.git
cd claude-localize
./install.sh
```

需要 Python 3.10+ 和 [pipx](https://pipx.pypa.io/)（`brew install pipx`）。

### 方式 B：下载菜单栏 App

到 [Releases](https://github.com/lr19506167395-sys/claude-localize/releases) 下载 `Claude汉化.app.zip`，
解压后拖进 `/Applications`，**首次打开需右键 →「打开」**（未签名未公证，Gatekeeper 会拦截）。

## 特性

- **四源合并**：手动锁定 → 官方 zh-Hans → 内置词表（含 javaht 上游）→ 自有翻译 → 英文兜底
- **抗版本升级**：按 key 查表，新版新增的文案显示英文，而不是让整个补丁失效
- **桌面壳层汉化（一键开关）**：菜单里点一下就把 `app.asar` 层装上或撤掉，
  不需要再单独点安装。开启前弹风险说明（可勾选不再提示），
  勾选状态由真实文件检测得出，不会出现"显示开着其实是英文"
- **原生字符串**：`zh-Hans.lproj` / `zh_CN.lproj` 的 `Localizable.strings`，
  覆盖权限提示等 Swift/ObjC 界面
- **动态词表**：`i18n/dynamic/zh-CN.json`（模型描述等）
- **斜杠命令双语菜单**：`/help 帮助与支持`，只改显示层，命令 ID / 搜索 / 点击行为不变
  （移植自 javaht，49 条 gloss 词表随包分发，恢复原样时可精确撤回）
- **完整回滚**：安装前整包备份，一条命令还原。还原只做整体替换、**不改包内任何文件**
  （删包内被签名的资源会让签名封条失效，Claude 启动即 SIGTRAP）；
  想清历史残留用 `restore --clean` 或菜单里的「清理安装残留」，两者都会重新签名
- **残留清理**：javaht/旧版留下的散装 `zh-CN.json`、中断的解包目录、半截备份
- **隔离验证**：`tools/isolated_verify.py` 在临时副本上跑完整流程再逐层校验

## 菜单栏 App

```bash
./build.sh   # 打包并部署到 /Applications/Claude汉化.app
```

菜单结构（首次使用）：

```
状态：4.29.0 · 未汉化
1. 安装中文补丁 ▸
     1. 仅主界面中文（不改 app.asar）
     2. 主界面 + 桌面壳层中文（改 app.asar）
2. 更新中文翻译 ▸
     1. 从 javaht 获取中文更新
     2. 使用翻译 agent 获取中文更新
       * 翻译 agent 配置…
3. 恢复原样 / 卸载补丁
* 清理安装残留
上次操作：—
退出
```

安装后一级项变成当前模式，二级菜单只显示「切到另一种」：

```
1. 中文补丁：主界面 + 外壳 ▸
     切换为「仅主界面中文（不改 app.asar）」
```

**词表更新过**（拉了上游 / 补翻过 / 改过译文）时，二级菜单会多出第二项：

```
1. 中文补丁：主界面 + 外壳 ▸
     1. 切换为「仅主界面中文（不改 app.asar）」
     2. 安装更新补丁
```

编号规则：二级菜单**两项及以上才加 `1. 2.`**，单项不编号。
点哪个模式就立刻切成那个模式，不需要再点安装。

每个操作结束后，菜单底部「上次操作」会留下 `✓ / ✗` 记录，
运行中该项标题会显示进度条或转圈动画。

## 使用

```bash
# 安装 / 重装（默认启用桌面壳层增强）
claude-localize install

# 不碰 app.asar：桌面壳层保持英文，风险最低（与 javaht 相同策略）
claude-localize install --no-shell

# 预览会做什么（不修改任何文件）
claude-localize install --dry-run

claude-localize status    # 状态：覆盖率、外壳白名单、残留
claude-localize verify    # 安装后自检
claude-localize clean     # 清理历史残留（删到包内文件会自动重签名）
claude-localize restore   # 还原成英文（整体换回备份，零改动）
claude-localize restore --clean   # 还原并顺带清掉备份里的历史残留（会重签名）
```

## 更新词表

```bash
claude-localize check-update          # 只查上游版本
claude-localize pull-upstream         # jsDelivr → GitHub zip → 本地目录
claude-localize pull-upstream --local ~/Downloads/claude-desktop-zh-cn-1.4.19
```

拉完词表后重新 `install` 生效。

## 自定义翻译

```bash
claude-localize lock <key> "你的译文"   # 锁定后优先级最高，上游更新也不覆盖
claude-localize inspect <key>           # 查看某条译文来自哪个源
claude-localize validate                # 校验占位符 / ICU / HTML 结构
```

### 用翻译 agent 补翻（可选）

菜单「2. 更新中文翻译 → * 翻译 agent 配置…」里填**你自己的** API：

- 支持任意 OpenAI 兼容（`/chat/completions`）或 Anthropic（`/v1/messages`）端点
- **本项目不提供、不代理、不预置任何 API 服务**——默认值为空，不填就用不了这项功能
- 没配全时会明确提示缺哪几项，不会静默失败

## Claude 更新后

Claude Desktop 自动更新会把补丁覆盖掉，界面变回英文。重新跑一次 `install` 即可。
如果新版本出现英文界面，先 `pull-upstream` 更新词表。

## 工作原理

Claude Desktop 是 Electron 应用，界面文本分布在四个地方（4.29+ 布局）：

| 来源 | 位置 | 处理方式 |
|---|---|---|
| 主界面 i18n | `ion-dist.asar` → `i18n/zh-CN.json` | 写入合并词表（约 3.9 万条） |
| 主界面动态词表 | `ion-dist.asar` → `i18n/dynamic/zh-CN.json` | 随包中文，缺失则英文兜底 |
| 前端硬编码 | `ion-dist.asar` → `assets/v1/*.js` | 语言白名单 + 静态字符串替换 |
| 桌面壳层 | `app.asar` → `resources/i18n/zh-CN.json` | **可选**：写语言文件 + 注册白名单 |
| 原生字符串 | `Contents/Resources/*.lproj` | `Localizable.strings` |

改 `app.asar` / `ion-dist.asar` 必须同步三重完整性校验，否则 Claude 启动即崩：

1. 每个 asar 条目的 `integrity`（整文件 + 4MB 分块 sha256）
2. `Info.plist` → `ElectronAsarIntegrity` → `<asar 相对路径>` → header 字符串的 sha256
3. Electron Framework 二进制里的字典摘要槽（sentinel `AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A`）

顺序固定为：重打包 asar → 同步两个 plist 哈希 → 同步二进制摘要槽 → 最后签名。

安装全程在临时目录完成，验证通过后才原子替换正式文件，失败不影响现有安装。

## 测试

```bash
cd tests && python -m unittest discover -s . -p "test_*.py"
env -u PYTHONHOME -u PYTHONPATH .venv/bin/python tools/isolated_verify.py
```

## 注意事项

- 修改资源后签名失效，本项目会做 ad-hoc 重签名（保留 entitlements），这也是自动更新能正常工作的前提
- 需要写入 `/Applications`，通常需要输入管理员密码
- 若机器上装过 javaht 或旧版本补丁，`install` 会自动清理其文件残留；但它在 `ion-dist.asar`
  内部改过的 JS（如双语斜杠菜单）属于内容替换，无法逐字还原，重装官方 Claude 可彻底清除
- 在线页面兜底（DOM 注入）依赖英文原文匹配，官方改文案后可能出现少量漏翻

## 许可证

MIT。词表与 `Localizable.strings` 来源于
[javaht/claude-desktop-zh-cn](https://github.com/javaht/claude-desktop-zh-cn)，
详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。