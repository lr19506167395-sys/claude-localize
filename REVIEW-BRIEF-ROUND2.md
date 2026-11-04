# claude-localize 代码审查任务书（第二轮）

- 项目路径：`/Users/li/Desktop/dev/AI-app/claude-localize`
- 当前版本：1.0.2（`d073846`），工作区另有未提交的进程检测修复
- 代码规模：约 7,700 行 Python（`claude_localize/` + `tests/` + `tools/` + `setup.py`）
- 上一轮审查报告：`REVIEW-REPORT.md`（本目录）
- 安全边界：**不得修改 `/Applications` 下任何文件，不得执行 install/restore/clean 的真实模式**

---

## 1. 项目做什么

给 Claude Desktop（macOS，Electron 应用）打简体中文补丁。会：

1. 往 `ion-dist.asar` 写入 `i18n/zh-CN.json`
2. （可选）改写 `app.asar`，写桌面壳层语言文件并注册语言白名单
3. 写 `zh-Hans.lproj` / `zh_CN.lproj` / `zh-CN.lproj` 的原生字符串
4. 重打包 asar → 同步四层完整性校验 → ad-hoc 重签名
5. 安装前整包备份，失败可回滚

同时提供一个**菜单栏 GUI**（py2app 打包），让用户点菜单完成上述操作。

---

## 2. 上一轮已修复的问题（不要重复报告）

除非修复不完整或引入了新问题：

1. 还原消耗唯一备份 → 改为「复制到临时目录 → 验证 → 交换」，原备份保留
2. 清理自动删除不完整备份 → 改为只报告保留
3. 摘要槽 fail-open → 缺 Framework/sentinel/used 槽一律判失败
4. 菜单连续点击重复排队 → 首次点击同步占位
5. 就地还原无预检 → 撤补丁前 `probe_layout()`，无备份直接拒绝
6. `install --dry-run` 缺 Framework → 轻量副本补齐
7. JS 译文未转义 → 按原引号形态编码
8. `resign` 遗留 entitlements 临时目录 → 改用 `TemporaryDirectory`
9. 备份版本读不出来被误删 / PlistBuddy 错误文本被当版本号
10. `is_pristine()` 只判 TeamIdentifier 不可靠 → 增加封条有效性 + 内容判据
11. `verify_digest_slot` / `verify_app` 多处 fail-open
12. `build.sh` 中文路径导致 AppleScript -2741

## 3. 工作区未提交的改动（本次审查重点之一）

- `claude_localize/discover.py`：把 `pgrep -x Claude` 改为 `ps -ax -o pid=,comm=` 解析。
  原因：macOS 上 `comm` 返回完整路径，`pgrep -x` 永远匹配不到主进程，
  导致 `is_running()` 恒为 False，安装时**静默跳过退出 Claude**。
- `tests/test_discover.py`：对应回归测试。

请重点核验这个改动：路径级别判断是否可靠、是否会误杀别的 Claude 副本、`quit()` 的强杀路径是否安全。

---

## 4. 本次需要审查的新问题背景

用户在真实使用中踩到两个问题，需要你从代码层面给出根因与修法：

### 4.1 菜单栏 App 启动极慢

现象：双击 `Claude汉化.app` 后 Dock 图标出现，但**菜单栏图标很久才出现**。

已知线索：

- 图标在 `rumps.App.__init__()` 里创建，此前要先跑完 `__boot__.py` 的模块加载
- `setup.py` 的 `postbuild` 用 `insert(0, path)` 把 `site-packages` 插到 `sys.path` **最前面**
- 打包产物中存在**重复模块**：
  - `lib/python3.14/rumps` 和 `lib/python3.14/site-packages/rumps`（两份）
  - `lib/python314.zip` 里有 `AppKit`/`objc`/`Foundation` 等，`site-packages/` 里**又有一份**
- `packages` 里同时声明了 `rumps`，postbuild 又手工复制一遍

请评估：

1. 这份 `sys.path` 布局会导致什么样的导入开销？
2. 重复模块除了慢，是否会导致**版本不一致 / 类型混淆**（例如两份 PyObjC 同时被加载）？
3. 正确的 py2app 打包方式应该是什么？postbuild 里那 20 行手工复制是否该删？
4. `NSStatusBar` 创建之前还有哪些可能阻塞的操作（`_load_settings` 磁盘 IO、图标 PNG 加载、`PROJECT_ROOT` 计算等）？

### 4.2 Claude 自动更新后补丁只装了一半

现象：Claude 从 4.4212.0 自动更新到 4.16336.0 后变英文；用户重装补丁，结果：

```
Claude-3p/config.json : en-US   ← 实际在用的配置
Claude/config.json    : zh-CN   ← 不用的那个
app.asar 中文         : False   ← 外壳没汉化
ion-dist zh-CN        : True    ← 主界面语言包在
```

请从代码层面分析：

1. `installer.install()` 里「已打补丁则先恢复原版」的分支，在**版本跨越**时行为是否正确？
2. `write_locale()` 遍历多份 Claude 配置目录，写入时机（退出 Claude 前后）是否会导致被覆盖？`quit(wait=True)` 是否真的等到配置文件刷盘？
3. 为什么 `install --no-shell` 或切换模式后，`app.asar` 的汉化会丢失而 `ion-dist.asar` 保留？`strip_patch` / `shell.uninstall` 的幂等性如何？
4. 自动更新后 `ShipIt` 替换 app 时，我们的备份与现包版本不同——`best_backup()` 在此时的选择是否符合预期？

---

## 5. 审查范围与优先顺序

**第一优先（数据安全 / 会让 Claude 打不开）**
- `claude_localize/backup.py` — 备份选择、prune、swap_in、restore_backup
- `claude_localize/integrity.py` — 四层校验
- `claude_localize/installer.py` — install / patch_tree / strip_patch / _verify_tree
- `claude_localize/restorer.py` — 还原流程
- `claude_localize/asar.py`、`asar_edit.py`、`frontend_bundle.py`

**第二优先（本次新问题）**
- `setup.py`、`menubar_app.py`、`claude_localize/menubar.py`
- `claude_localize/discover.py`（含未提交改动）
- `claude_localize/paths.py`

**第三优先（功能正确性）**
- `shell.py`、`slashmenu.py`、`native_strings.py`、`frontend_locale.py`
- `merger.py`、`validator.py`、`cleanup.py`
- `translator.py`、`upstream.py`、`cli.py`、`checks.py`

**测试**
- `tests/` 全部（当前 138 项）
- `tools/isolated_verify.py`、`tools/verify_restore_cycle.py`

---

## 6. 具体要求

1. **不要重复报告第 2 节已修复的问题**；如认为修复不到位，请说明缺什么。
2. **对 4.1 / 4.2 给出明确根因和修法**，这是本次的核心交付。
3. 关注**跨模块一致性**：`installer` ↔ `restorer` ↔ `backup` ↔ `integrity` 之间对「什么算已打补丁」「什么算可用的备份」的判据是否统一。
4. 关注**版本跨界**：Claude 自动更新前后，所有依赖 `bundle_version()` 的逻辑是否仍然成立。
5. 关注**并发与重入**：菜单栏点击、安装中途退出、Ctrl-C 中断。
6. 关注**测试有效性**：现有测试是否用了过强的 mock 而绕过了真实风险。

## 7. 报告格式

```
### [严重度] 一句话标题
文件：path:line
触发条件：（可复现的具体输入/状态）
后果：
建议修法：
```

严重度：**严重**（毁数据 / Claude 打不开）、**高**（常见场景功能失效）、**中**（边界出错 / 测试盲区）、**低**（可维护性 / 性能 / 日志）。

最后给一段总体评价：**这个项目当前最可能以什么方式坏掉**。

## 8. 可自行验证的安全命令

```bash
cd /Users/li/Desktop/dev/AI-app/claude-localize

# 单元测试（不接触真实 Claude）
cd tests && env -u PYTHONHOME -u PYTHONPATH ../.venv/bin/python -m unittest discover -s . -p "test_*.py"

# 在临时副本上跑完整安装流程并逐层校验（需要真实 Claude 已安装）
env -u PYTHONHOME -u PYTHONPATH .venv/bin/python tools/isolated_verify.py

# 只读查看真实包结构
python -c "
from pathlib import Path
from claude_localize import asar
d, hs, h = asar.load(Path('/Applications/Claude.app/Contents/Resources/ion-dist.asar'))
print(hs, list(h.keys())[:5])
"
```

**绝对不要**执行 `install` / `restore` / `clean` 的真实模式，也不要修改 `/Applications` 下任何内容。
