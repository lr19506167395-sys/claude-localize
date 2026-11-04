"""在临时副本上验证「还原 → 再安装」。

绝不接触 /Applications 下的真实 Claude.app、它的配置或备份。
只替换应用路径与外部副作用（写 locale / 启动 / 进程检测）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

from claude_localize import backup, discover, installer, integrity, paths, restorer, signer


def main() -> int:
    base = Path(sys.argv[1])
    app = base / "Claude.app"
    if app.exists():
        raise SystemExit(f"临时目录里已有 Claude.app，请换一个空目录：{app}")

    source = next(Path("/Applications").glob("Claude.backup-before-zh-*.app"))
    backup._copy_tree(source, app)

    writes: list[str] = []
    with mock.patch.object(paths, "APP_PATH", app), \
            mock.patch.object(discover, "active_config_path", return_value=None), \
            mock.patch.object(discover.ClaudeInstall, "write_locale",
                              lambda self, locale: writes.append(locale)), \
            mock.patch.object(discover.ClaudeInstall, "launch", lambda self: None), \
            mock.patch.object(discover.ClaudeInstall, "is_running", lambda self: False):
        inst = discover.detect()
        print("检测:", inst.app, inst.version, inst.locale, flush=True)

        print("== 安装 ==", flush=True)
        installer.install(launch=False, progress=lambda p, m: None)
        chosen = backup.best_backup(app)
        assert chosen and chosen.exists(), chosen
        assert discover.is_patched(inst), "安装后应当检测到补丁"
        print("备份：", chosen.name, flush=True)

        print("== 还原 ==", flush=True)
        status = restorer.restore(verbose=True)
        assert status == "restored", status
        assert chosen.exists(), "还原消耗了唯一备份"
        assert not discover.is_patched(inst), "还原后不应仍有中文内容"
        problems = integrity.verify_app(app)
        assert not problems, problems
        assert signer.verify(app)[0], "还原后签名无效"

        print("== 再安装 ==", flush=True)
        installer.install(launch=False, progress=lambda p, m: None)
        assert discover.is_patched(inst)
        problems = integrity.verify_app(app)
        assert not problems, problems
        assert signer.verify(app)[0]

    print("locale 写入:", writes, flush=True)
    print("RESTORE AND REINSTALL PASSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
