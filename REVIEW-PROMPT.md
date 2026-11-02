# 代码审查任务：claude-localize

你是一名资深代码审查者。请对一个 macOS 工具做**全流程代码审查**。本文件是给你的完整任务书，自包含，不需要额外背景。

---

## 1. 项目是什么

`claude-localize` 给 **Claude Desktop for macOS**（Electron 应用）打简体中文补丁。它会：

1. 把中文词表写进 `ion-dist.asar` 里的 `i18n/zh-CN.json`
2. （可选）改写 `app.asar`，写入桌面壳层语言文件并注册语言白名单
3. 写入原生 `zh-Hans.lproj` / `zh_CN.lproj` / `zh-CN.lproj` 的 `Localizable.strings`
4. 重打包 asar、同步完整性校验值、**ad-hoc 重签名**

安装前会整包备份成 `/Applications/Claude.backup-before-zh-<时间戳>.app`。

代码仓库根目录在 `claude_localize/`，测试在 `tests/`，工具在 `tools/`。

---

## 2. 必须先理解的领域约束（否则会把正常设计当成 bug）

### 2.1 四层完整性校验，任何一层不同步 = Claude 启动即 SIGTRAP

改 asar 后必须同步这四层，**顺序固定**（重打包 asar → 同步 plist 哈希 → 同步二进制摘要槽 → 最后签名）：

| 层 | 位置 | 内容 |
|---|---|---|
| 1 | asar header 每个条目 | `integrity = {algorithm, hash, blockSize=4194304, blocks[]}`，整文件 sha256 + 每 4MB 分块 sha256 |
| 2 | `Contents/Info.plist` | `ElectronAsarIntegrity → "Resources/app.asar" / "Resources/ion-dist.asar" → hash` = **header 字符串**（不是整个文件）的 sha256 |
| 3 | `Electron Framework` 二进制 | 预埋一个槽位：`sentinel + used(1B) + version(1B) + sha256(32B)`，sentinel = `AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A`；运行时用它校验整个 `ElectronAsarIntegrity` 字典未被篡改 |
| 4 | 代码签名封条 | 删除任何被签名的 bundle 资源会报 "a sealed resource is missing or invalid" |

**关键：这四层失败时都没有可读错误**，Claude 静默崩溃。所以任何“校验没跑成但返回通过”的路径都是高危缺陷。

### 2.2 asar 格式

```
[0:4]   = 4
[4:8]   = header_size (= payload_size + 4)
[8:12]  = payload_size
[12:16] = json_len
之后    = JSON header + 零填充到 4 字节对齐，再之后是文件内容
```

### 2.3 备份不能跨版本用

Claude 自动更新后，旧版本的整包备份**不能**拿来还原（等于降级，且布局可能不兼容）。备份选择必须按 `CFBundleShortVersionString` 过滤。

### 2.4 其他背景

- 官方 Claude 4.29 随包有 `zh-Hans.json`，但主进程有硬编码的 11 语言白名单**不含中文**，所以官方中文是死数据。本项目在 `shell.py` 里注册 `zh-CN` 白名单。
- 前端把 locale 缓存在 `localStorage["spa:locale"]`，会覆盖配置文件；`dompatch.py` 负责注入 locale 固定逻辑。
- 打包用 py2app，`PROJECT_ROOT` 指向只读的 app bundle，用户数据必须放 `~/.claude-localize/`。
- 项目明确**不预置任何 API 服务商**（`DEFAULT_MODEL` / `DEFAULT_BASE_URL` 都是空串）。这是产品决策，不是遗漏，**不要**建议填默认值。
- `rumps.MenuItem` 继承 `OrderedDict` 因而**不可哈希**，不能当 dict key。菜单回调里抛的异常会被 AppKit 吞掉。

---

## 3. 已修复的问题（不要重复报告）

以下问题已经修过，如果你还看到，说明修复不完整，可以报；如果修复到位，不要当新问题：

1. `best_backup()` 曾不看版本，导致 Claude 升级后旧版原版备份压住同版本新备份，还原/重装全部报错 → 已改为先按版本筛
2. `prune_old_backups` 曾无条件保留原版备份，导致旧版本备份永久占用几百 MB → 已改为版本对不上一律删
3. 版本读不出来时（plist 在但无 `CFBundleShortVersionString`）曾把所有备份判成版本不符全部删光 → 已抽出 `_current_key()` 返回 `None`
4. `verify_digest_slot` / `sync_digest_slot` 曾在找不到 sentinel 或 `used=0` 时静默放行 → 已新增 `probe_layout()` 前置拒绝
5. `_verify_tree` 曾在“改了 asar 但没校验到槽”时算通过 → 已改为不通过
6. `verify_app` 曾只比整文件 hash 不比分块 → 已补 `_first_bad_block()`
7. `checks.status()` 曾用一份备份的路径配另一份备份的性质标签 → 已统一
8. `build.sh` 曾因 `printf %q` 产出的 `$'...'` 让 AppleScript 报 -2741 → 已改用 argv 传参
9. `gui-config.json` 曾 0644，含明文 API Key → 已改 0600

---

## 4. 已知且接受的问题（不要报，或只标注不要求修）

- `asar_edit.replace_file_content` 的 offset 修正不完整，但该函数在 3.x/4.x 上**不可达**（只要 `app.asar` 存在就走 `AppAsarTarget`）
- `signer.resign` 会在 `/tmp/claude-zh-ent.*` 留临时文件
- 未做 Apple 签名公证、未做 Homebrew cask、README 不放截图（隐私考虑）
- `installer.py` 的 `write_locale` 早于备份/替换，替换失败会出现“配置已改 zh-CN 但界面仍英文”（不崩溃）
- `dompatch` 的在线页面兜底依赖英文原文匹配，官方改文案后会漏翻（已文档化）

---

## 5. 审查范围与重点

请完整读这些文件（按重要性排序）：

**第一优先（会毁数据或让 Claude 打不开）**
- `claude_localize/backup.py` — 备份创建/选择/清理/还原
- `claude_localize/integrity.py` — 四层校验中的第 1、2、3 层
- `claude_localize/installer.py` — 主流程编排、`patch_tree`、`strip_patch`、`_verify_tree`
- `claude_localize/restorer.py` — 还原流程
- `claude_localize/asar.py` / `claude_localize/asar_edit.py` — asar 读写与重打包
- `claude_localize/frontend_bundle.py` — 从 asar 解包、打完再打包

**第二优先（功能正确性）**
- `claude_localize/shell.py` — 桌面壳层语言文件 + 白名单正则
- `claude_localize/slashmenu.py` — 斜杠命令双语菜单的打入与撤回（撤回必须字节级一致）
- `claude_localize/native_strings.py` — `.lproj` 写入/删除
- `claude_localize/merger.py` / `validator.py` — 四源合并、占位符/ICU 校验
- `claude_localize/discover.py` — 补丁状态检测（只看内容不看签名）
- `claude_localize/cleanup.py` — 残留扫描与清理

**第三优先（交互与外围）**
- `claude_localize/menubar.py` — 菜单栏状态机
- `claude_localize/translator.py` — 翻译 agent（会调外部 API）
- `claude_localize/upstream.py` — 拉取上游词表（会联网）
- `claude_localize/cli.py` / `paths.py` / `checks.py`

**测试**
- `tests/` 下全部，以及 `tools/isolated_verify.py`

### 具体要查什么

1. **数据安全**：任何输入组合下，会不会删掉用户唯一的回滚副本？`create_backup` → `put_in_place` 之间 app 路径消失的窗口期，哪一步失败会导致 Claude 和备份同时不存在？
2. **完整性链**：改 asar 的所有代码路径（含“新增条目”“删除条目”“替换等长内容”）是否都成对重算了 `hash` 和 `blocks[]`？header 的 `size` / `offset` 是否重算？零填充是否 4 字节对齐？
3. **撤回的幂等性**：`strip_patch` / `slashmenu.revert_dir` / `shell.unpatch_locale_whitelist` 反复执行是否安全？对**别人打过补丁的包**执行是否会产生非法状态？
4. **异常路径**：安装中途 Ctrl-C / 磁盘满 / 权限不足 / Claude 正在运行，会留下什么状态？下次运行能否自愈？
5. **并发**：菜单栏可能双击，是否所有长任务都有互斥？
6. **测试真实性**：现有测试是否用了假对象而绕过了真正的风险？哪些核心路径**完全没有**测试覆盖？
7. **正则与字符串处理**：`shell.py` 的白名单正则、`slashmenu.py` 的定位逻辑，在压缩过的 JS（单行、极长）上是否会误匹配或性能爆炸？
8. **外部输入**：`upstream.py` 下载并解压上游词表、`translator.py` 调用用户配置的任意端点——是否有路径穿越、命令注入、解压炸弹、把恶意内容写进 Claude 包的风险？

---

## 6. 报告格式

对每个问题给出：

```
### [严重度] 一句话标题
文件：claude_localize/xxx.py:行号
触发条件：（具体输入/状态，要能复现）
后果：（会不会崩溃 / 丢数据 / 只在特定版本出现）
建议修法：（一句话即可）
```

严重度定义：

- **严重**：会毁掉用户的回滚副本、让 Claude 无法启动、或静默损坏包
- **高**：特定常见场景下功能失效（如升级后还原失败）
- **中**：边界条件出错、错误处理缺失、测试盲区
- **低**：可维护性、性能、日志

**不要报告**：纯风格问题（命名、类型标注、注释多少）、上述第 3 节已修复的问题、第 4 节已接受的问题、“建议加更多测试”这类空泛建议（除非能指出具体未覆盖的高危路径）。

请按严重度从高到低排序。如果某个类别没发现问题，明确说“未发现”。最后给一段总体评价：这个项目**最可能以什么方式坏掉**。

---

## 7. 如何自行验证

你可以在**临时副本**上验证，不要碰 `/Applications/Claude.app`：

```bash
# 仓库自带虚拟环境，直接用它（不要用系统 python）
cd /Users/li/Desktop/dev/AI-app/claude-localize

# 造一个假包跑单测（不接触真实 Claude）
cd tests && env -u PYTHONHOME -u PYTHONPATH ../.venv/bin/python -m unittest discover -s . -p "test_*.py"

# 在临时副本上走完整补丁流程并逐层校验（需要真实 Claude 已安装）
env -u PYTHONHOME -u PYTHONPATH .venv/bin/python tools/isolated_verify.py
```

如果你要读真实包的内部结构（只读，安全）：

```bash
# asar header（asar.load 要 pathlib.Path，不是 str）
python -c "
from pathlib import Path
from claude_localize import asar
d, hs, h = asar.load(Path('/Applications/Claude.app/Contents/Resources/ion-dist.asar'))
print('header_size', hs, '顶层键', list(h.keys())[:5])
print('条目数', len(list(asar.walk(h))))
"
```

**绝对不要**执行 `install` / `restore` / `clean` 的真实模式，也不要直接修改 `/Applications` 下的任何东西。
