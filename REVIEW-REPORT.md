# claude-localize 全流程代码查验报告

- 查验日期：2026-11-02
- 查验方式：完整源码审查、临时目录故障注入、118 个单元测试、真实 Claude 临时副本隔离验证
- 安全边界：未修改项目代码；未对 `/Applications/Claude.app` 执行安装、还原、清理或写入

## 结论摘要

**成功安装路径表现良好，但还原与清理路径仍有数据安全风险。**

共发现：

- **严重：2 项**
- **高：3 项**
- **中：2 项**
- **低：1 项**

最核心的问题是：还原会先消耗唯一备份，再直接修改原位包；同时，部分摘要槽检查仍会 fail-open。清理功能还会自动删除不完整备份，可能让用户失去最后一份回滚资源。

---

## 严重

### [严重] 还原先消耗唯一备份，后续失败时无副本可回滚

文件：`claude_localize/backup.py:186`、`claude_localize/restorer.py:53`、`claude_localize/restorer.py:79`

触发条件：只有一份可用备份；还原将备份重命名为 `Claude.app` 后，撤补丁、重签名或最终完整性校验失败。

后果：`restore_backup()` 通过 `backup.rename(app)` 消耗备份。后续任一步失败，备份路径都不再存在；如果失败发生在 asar 重打包或摘要槽同步后，原位 `Claude.app` 还可能无法启动。

实际验证：在临时夹具中注入 `strip_patch()` 异常后，还原输出完整性错误；原位 app 存在，但唯一备份目录已经消失。

建议修法：复制备份到临时副本，在副本上完成撤补丁、摘要槽同步、重签名和全部校验，最后交换就位；整个过程中保留原备份。

### [严重] 清理功能直接删除不完整备份，包括唯一回滚副本

文件：`claude_localize/cleanup.py:65`、`claude_localize/cleanup.py:133`

触发条件：备份缺少 `Frameworks`、`app.asar` 或 `Info.plist` 中任意一项，例如手工复制中断、磁盘故障或外部工具破坏；用户执行“清理安装残留”。

后果：`scan()` 将其标为 `broken-backup`，`clean()` 不询问、不检查是否存在其他完整备份，直接删除整个目录。不完整备份虽然不适合直接换入，但仍可能包含用户唯一保存的原始资源。

实际验证：临时目录中只有一份不完整备份时，执行 `cleanup.clean()` 后该目录被完整删除。

建议修法：默认只列出、不删除不完整备份；确需清理时要求显式确认，并且只在存在其他完整同版本备份时允许自动删除。

---

## 高

### [高] 还原校验找不到 Framework 或摘要槽时仍报告通过

文件：`claude_localize/integrity.py:196`、`claude_localize/restorer.py:79`

触发条件：无备份时就地撤补丁；asar 和 plist 哈希正确，但 Framework 文件不存在、sentinel 改变或没有 `used=1` 槽。

后果：

- `verify_digest_slot()` 对缺 Framework、缺 sentinel、零已启用槽均返回通过。
- `verify_app()` 因而可能返回空问题列表。
- 还原流程随后输出“完整性 + 签名校验：通过”。

这正是四层完整性约束中的高危 fail-open：真实 Electron 布局变化时，可能放过启动即 SIGTRAP 的包。

实际验证：使用结构和 plist 哈希均有效、但没有 Framework 摘要槽的临时 app，还原仍成功返回并报告完整性通过。

建议修法：对声明了完整性校验的 asar 强制要求实际校验至少一个摘要槽；`strip_patch()` 在修改前执行 `probe_layout()`。

### [高] 菜单栏安装可在 busy 标志设置前重复排队

文件：`claude_localize/menubar.py:154`、`claude_localize/menubar.py:173`、`claude_localize/menubar.py:316`

触发条件：连续点击安装模式。`_apply_mode()` 通过 `_ui()` 延迟调用 `_run_install()`，因此两次点击都可能先通过 `self.busy == False` 检查。

后果：两个后台安装可同时复制、备份和替换同一个 `Claude.app`，产生时间戳冲突、重复恢复、备份错序或替换竞争。

实际验证：模拟真实延迟调度后，两次点击均进入待执行队列； `_background()` 的互斥在任务真正开始时才生效，无法阻止此前的重复排队。

建议修法：在 `_apply_mode()` 内同步、原子地完成 busy 检查和设置，再调度后台任务。

### [高] 无备份的就地还原没有预检，异常被吞后继续改包

文件：`claude_localize/restorer.py:63`、`claude_localize/installer.py:534`

触发条件：没有可用备份，而当前包的 plist、摘要槽或 asar 布局不符合预期。

后果：`strip_patch()` 不调用 `probe_layout()`；其 `SystemExit` 被还原流程捕获为“警告”后继续删除原生字符串、重签名和写配置。用户可能只得到事后“建议重装官方 Claude”，而不是在改动前安全中止。

建议修法：就地还原前完成布局预检；撤补丁失败时立即停止，不继续修改包。

---

## 中

### [中] `install --dry-run` 在正常 asar 布局下因缺 Framework 失败

文件：`claude_localize/installer.py:461`、`claude_localize/installer.py:611`

触发条件：正常安装的 Claude 4.x 上执行 `install --dry-run`。

后果：`_copy_light()` 不复制 `Electron Framework`，随后 `probe_layout()` 因找不到 Framework 中止。即使增强模式通过预检，no-shell 模式后面的 `_verify_tree()` 又会因没有摘要槽失败。dry-run 因而无法可靠承担安装前检查作用。

建议修法：预检和摘要槽校验读取真实源包，或让 dry-run 复制必要 Framework，并明确区分仅适用于待替换副本的检查。

### [中] 核心危险路径没有事务级测试

文件：`tests/test_backup.py:233`、`tests/test_integrity.py:98`

触发条件：现有 118 个测试全部通过，但上述还原和清理问题均未被发现。

具体盲区：

- 备份测试只验证成功还原，没有覆盖备份换入后的阶段失败。
- 完整性测试没有覆盖“asar 合法但无已启用摘要槽”。
- 没有 `restorer.restore()` 的故障注入测试。
- 没有验证清理后仍保留回滚资源的测试。
- `tools/isolated_verify.py` 只覆盖安装成功路径。
- 菜单栏测试把 `_ui()` 改成同步执行，掩盖了真实延迟调度下的并发窗口。

建议修法：增加唯一备份下各阶段失败、无槽位还原、唯一不完整备份清理和连续点击安装的回归测试。

---

## 低

### [低] 硬编码替换器直接插入译文，缺少 JS 字符串转义

文件：`claude_localize/frontend_locale.py:88`

触发条件：上游译文包含未转义的 `"`、`\` 或换行。

后果：可能生成语法错误或语义改变的 bundle。当前内置词表未出现对应的换行转义差异，因此这是上游数据变化后的风险，不代表当前包已经损坏。

建议修法：按源字符串的引号和转义形态编码译文，而不是用 f-string 直接插入。

---

## 已验证正常的部分

- 118 个单元测试全部通过。
- `tools/isolated_verify.py` 在真实 Claude 的临时副本上通过。
- 临时副本的两个 asar 条目哈希、分块哈希、plist 哈希、Framework 摘要槽、签名、entitlements 和主进程 JS 语法均正常。
- 当前真实包只读检查：两个 asar 的条目及分块哈希均正确，摘要槽匹配。
- asar 重打包的 offset、共享内容去重、4 字节对齐及 unpacked 处理未发现新增问题。
- 白名单注册/摘除、斜杠菜单正常撤回均通过；当前真实斜杠补丁也能撤回并清除 marker。
- 上游 ZIP 按成员名读取，未发现路径穿越或命令注入。
- 下载大小和解压大小没有上限，作为补充观察，不单列为缺陷。

## 总体评价

这个项目**最可能坏在还原，而不是安装**。

安装采用“临时副本 → 校验 → 备份 → 替换”的事务思路，实际验证也较完整；还原则先移动唯一备份，再直接修改原位包，同时保留若干 fail-open 校验。只要碰上 Electron 布局变化、磁盘错误或签名失败，就可能出现“Claude 已坏、备份也没了”的最差状态。清理功能进一步放大了这个风险。

建议首先统一安装与还原的事务模型：**所有修改都在临时副本完成，验证通过后才交换；原备份在完整成功前永不移动、永不删除。**
