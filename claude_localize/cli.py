"""命令行入口。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-localize",
        description="Claude Desktop 中文界面补丁（macOS）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_install = sub.add_parser("install", help="安装/重装中文补丁")
    p_install.add_argument("--lang", default="zh-CN")
    p_install.add_argument("--dry-run", action="store_true")
    p_install.add_argument(
        "--no-shell", "--no-asar", dest="no_shell", action="store_true",
        help="不修改 app.asar（桌面壳层保持英文，风险最低，与 javaht 一致）",
    )

    p_restore = sub.add_parser("restore", help="还原为英文原版")
    p_restore.add_argument("--dry-run", action="store_true")
    p_restore.add_argument(
        "--clean", action="store_true",
        help="顺带清掉备份里的历史残留（会重新签名）",
    )

    p_clean = sub.add_parser("clean", help="清理历史安装残留")
    p_clean.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="查看当前状态")
    sub.add_parser("verify", help="安装后自检")
    sub.add_parser("gui", help="启动菜单栏 App")

    p_check = sub.add_parser("check-update", help="检查上游词表是否有新版")

    p_pull = sub.add_parser("pull-upstream", help="拉取上游词表")
    p_pull.add_argument("--method", default="auto", choices=["auto", "jsdelivr", "zip", "local"])
    p_pull.add_argument("--ref", default="main")
    p_pull.add_argument("--local", type=Path, help="本地 javaht 目录")

    p_lock = sub.add_parser("lock", help="锁定某条译文的翻译")
    p_lock.add_argument("key")
    p_lock.add_argument("value")

    p_inspect = sub.add_parser("inspect", help="查看某条译文的来源")
    p_inspect.add_argument("key")

    p_val = sub.add_parser("validate", help="校验词表占位符与 ICU 结构")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return 0

    if args.cmd == "install":
        from . import installer

        r = installer.install(
            args.lang, with_asar=not args.no_shell, dry_run=args.dry_run
        )
        print()
        print(r.render())
        if not args.dry_run:
            print("\n完成。重新打开 Claude 生效。")
        return 0

    if args.cmd == "restore":
        from . import restorer

        restorer.restore(dry_run=args.dry_run, clean=args.clean)
        return 0

    if args.cmd == "clean":
        from . import cleanup, discover

        inst = discover.detect()
        items = cleanup.clean_and_resign(inst.app, dry_run=args.dry_run)
        if not items:
            print("没有可清理的残留。")
        return 0

    if args.cmd == "status":
        from . import checks

        checks.status()
        return 0

    if args.cmd == "verify":
        from . import checks

        return 0 if checks.verify() else 1

    if args.cmd == "gui":
        from . import menubar

        menubar.main()
        return 0

    if args.cmd == "check-update":
        from . import upstream

        remote = upstream.check_remote()
        local = upstream.local_version()
        print(f"上游版本: {remote.get('release')}")
        print(f"本地版本: {local or '未记录'}")
        if local != remote.get("release"):
            print("有新版本可用，运行 claude-localize pull-upstream 更新")
        else:
            print("已是最新")
        return 0

    if args.cmd == "pull-upstream":
        from . import upstream

        counts = upstream.pull(
            method=args.method, ref=args.ref, local_path=args.local
        )
        print("词表已更新：")
        for name, n in counts.items():
            print(f"  {name:16} {n:6} 条")
        return 0

    if args.cmd == "lock":
        from . import merger, paths

        path = paths.custom_path()
        entries = merger.load_custom(path)
        entries[args.key] = merger.CustomEntry(value=args.value, locked=True)
        merger.save_custom(path, entries)
        print(f"已锁定 {args.key}")
        return 0

    if args.cmd == "inspect":
        from . import checks, jsonutil, merger, paths

        pack_dir = paths.PROJECT_ROOT / "packs" / "zh-CN"
        from . import discover

        inst = discover.detect()
        en = checks.current_frontend_en(inst)
        if args.key not in en:
            print("当前 Claude 版本中不存在该 key")
            return 1
        _, _, origin = merger.merge(
            en,
            jsonutil.load_dict(pack_dir / "frontend.json"),
            custom=merger.load_custom(
                paths.custom_path()
            ),
        )
        print(f"key     : {args.key}")
        print(f"英文    : {en[args.key]}")
        print(f"来源    : {merger.SOURCE_LABELS.get(origin.get(args.key), '?')}")
        return 0

    if args.cmd == "validate":
        from . import checks, jsonutil, paths, validator

        inst = discover.detect()
        en = checks.current_frontend_en(inst)
        pack = jsonutil.load_dict(paths.PROJECT_ROOT / "packs/zh-CN/frontend.json")
        errs = validator.validate_dict(en, pack, "frontend")
        print(f"校验问题：{len(errs)} / {len(pack)}")
        for e in errs[:20]:
            print(f"  {e}")
        return 0 if not errs else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
