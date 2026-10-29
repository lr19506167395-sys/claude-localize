"""安装主流程：临时目录打补丁 → 验证 → 原子替换。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import (
    asar, asar_edit, backup, cleanup, discover, dompatch, frontend_bundle,
    integrity, jsonutil, native_strings, paths, shell, signer, slashmenu,
)
from . import frontend_locale as locale
from . import merger, validator


@dataclass
class InstallReport:
    version: str
    lang: str
    frontend_translated: int
    frontend_total: int
    desktop_translated: int
    desktop_total: int
    whitelist_files: int
    hardcoded_replacements: int
    dom_strings: int
    signed_files: int
    backup_path: Path | None = None
    shell_enabled: bool = False
    shell_registered: bool = False
    native_strings: int = 0
    cleaned: int = 0

    def render(self) -> str:
        cov = lambda a, b: f"{a}/{b} ({a / b * 100:.1f}%)" if b else "0/0"
        lines = [
            f"Claude 版本    : {self.version}",
            f"目标语言      : {self.lang}",
            f"主界面翻译    : {cov(self.frontend_translated, self.frontend_total)}",
            f"桌面壳层翻译  : {cov(self.desktop_translated, self.desktop_total)}"
            if self.shell_enabled else "桌面壳层汉化  : 已关闭（保持英文）",
            f"外壳白名单    : {'已注册 ' + self.lang if self.shell_registered else '未改动'}",
            f"原生字符串    : {self.native_strings} 个 lproj" if self.native_strings else "原生字符串    : 未安装",
            f"白名单注册    : {self.whitelist_files} 个 bundle",
            f"硬编码替换    : {self.hardcoded_replacements} 处",
            f"DOM 翻译条目  : {self.dom_strings} 条",
            f"重签名文件    : {self.signed_files} 个",
        ]
        if self.cleaned:
            lines.append(f"清理残留      : {self.cleaned} 项")
        if self.backup_path:
            lines.append(f"备份位置      : {self.backup_path}")
        return "\n".join(lines)


def _pack_root() -> Path:
    return paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE


def _load_pack(name: str) -> dict[str, str]:
    p = _pack_root() / name
    return jsonutil.load_dict(p) if p.exists() else {}


def _official_desktop(app: Path, work: Path | None = None) -> dict[str, str]:
    """官方 zh-Hans 桌面层（若存在）。

    3.x 在 Resources/zh-Hans.json；4.29 在 app.asar 内部
    （app_work/resources/i18n/zh-Hans.json），必须去解包目录找。
    """
    candidates = []
    if work is not None:
        candidates.append(work / "resources" / "i18n" / "zh-Hans.json")
    candidates.append(paths.resources_of(app) / "zh-Hans.json")
    for p in candidates:
        if p.exists():
            return jsonutil.load_dict(p)
    return {}


def _custom_file() -> Path:
    return paths.custom_path()


def patch_tree(
    app: Path, lang: str, *, with_asar: bool = True, verbose: bool = True,
    progress=None, sign: bool = True,
) -> InstallReport:
    """在给定的 app 副本上打补丁。

    ``with_asar=False`` 时完全不碰 ``app.asar``：桌面壳层保持英文，
    也不注入 DOM 兜底，只做前端（ion-dist.asar + 原生字符串）汉化——
    这就是 javaht 的路线，风险最低。
    """
    log = print if verbose else (lambda *a, **k: None)

    # 关闭增强时通常完全不碰 app.asar；但如果里面还留着上次的外壳汉化，
    # 必须先撤掉再重打包，否则关了开关界面还是中文。
    needs_asar = with_asar or shell.has_artifacts(
        app / frontend_bundle.APP_ASAR, lang
    )

    # Claude 4.29+ 把前端和桌面壳层都打包进了 asar：
    # 先解包成目录，打完补丁再重新打包并同步 Info.plist 哈希。
    if needs_asar:
        with frontend_bundle.FrontendTarget(app):
            with frontend_bundle.AppAsarTarget(app) as app_work:
                report = _patch_tree_inner(
                    app, lang, with_asar=with_asar, log=log, app_work=app_work,
                    progress=progress,
                )
    else:
        with frontend_bundle.FrontendTarget(app):
            report = _patch_tree_inner(
                app, lang, with_asar=False, log=log, app_work=None,
                progress=progress,
            )

    # 原生 .lproj 在 asar 外面，单独写（不参与 asar 校验）
    written = native_strings.install(app, log=log)
    report.native_strings = len(written)

    # plist 里的 ElectronAsarIntegrity 变了，二进制里预埋的字典摘要槽也必须同步，
    # 否则 Electron 启动自检失败直接 SIGTRAP（Claude 打不开）。
    slots = integrity.sync_digest_slot(app)
    if slots:
        log(f"已同步 Electron Framework 完整性摘要槽（{slots} 个）")

    # 重打包 + Info.plist 哈希同步都完成后才能签名，
    # 否则签名封的是旧资源，重打包后必然报 sealed resource invalid。
    if sign:
        if progress:
            progress(80, "重签名")
        signer.clear_quarantine(app)
        stats = signer.resign(app)
        ok, err = signer.verify(app)
        if not ok:
            raise SystemExit(f"签名校验失败：{err}")
        log(f"重签名完成：{stats['signed']} 个文件")
        report.signed_files = stats["signed"]

    return report


def _patch_tree_inner(
    app: Path, lang: str, *, with_asar: bool, log, app_work: Path | None = None,
    progress=None,
) -> InstallReport:
    def tick(pct: int, msg: str | None = None) -> None:
        if progress:
            progress(pct, msg or "")
    inst = discover.detect(app)

    en_frontend = jsonutil.load_dict(inst.frontend_en())

    # 桌面壳层：3.x 在 Resources/en-US.json，4.29 在 app.asar/resources/i18n/
    desktop_dir = frontend_bundle.desktop_i18n_dir(app)
    merged_desktop: dict[str, str] = {}
    st_d = merger.MergeStats()

    if desktop_dir is not None:
        en_desktop_path = desktop_dir / "en-US.json"
        if not en_desktop_path.exists():
            raise SystemExit(f"缺少桌面壳层英文语言文件：{en_desktop_path}")
        en_desktop = jsonutil.load_dict(en_desktop_path)
        merged_desktop, st_d, _ = merger.merge(
            en_desktop,
            _load_pack("desktop.json"),
            official=_official_desktop(app, app_work),
            custom=None,
        )

    # ---- 合并词表 ----
    merged_frontend, st_f, _ = merger.merge(
        en_frontend,
        _load_pack("frontend.json"),
        official=None,
        custom=merger.load_custom(_custom_file()),
    )
    tick(45, "已合并词表")
    log(f"主界面：{st_f.report()}")
    if merged_desktop:
        log(f"桌面层：{st_d.report()}")

    # ---- 写入语言文件 ----
    jsonutil.save_json(inst.frontend(lang), merged_frontend)

    shell_registered = False
    if merged_desktop and (with_asar or app_work is None):
        if with_asar and app_work is not None:
            # 4.29：写进 asar 内部，并注册外壳语言白名单
            stats = shell.install(app_work, lang, merged_desktop, log=log)
            shell_registered = bool(stats["registered"])
        else:
            # 3.x：散装文件
            jsonutil.save_json(desktop_dir / f"{lang}.json", merged_desktop)
    elif app_work is not None and not with_asar:
        # 关闭增强：把上次装进 asar 的外壳汉化撤掉
        shell.uninstall(app_work, lang, log=log)
        _unpatch_dom(app_work, log)

    # dynamic 目录：新版本强制加载，缺失会回退英文。
    # 有随包中文词表就用中文，没有就用官方英文兜底（保证加载不失败）。
    dyn_en = inst.frontend_dynamic("en-US")
    if dyn_en.exists():
        localized = _pack_root() / "dynamic.json"
        if localized.exists():
            merged_dyn, _, _ = merger.merge(
                jsonutil.load_dict(dyn_en), jsonutil.load_dict(localized)
            )
        else:
            merged_dyn = jsonutil.load_dict(dyn_en)
        jsonutil.save_json(inst.frontend_dynamic(lang), merged_dyn)

    # ---- 前端 bundle ----
    whitelist_files = 0
    hardcoded_count = 0
    hardcoded_rules = []
    hp = _pack_root() / "hardcoded.json"
    if hp.exists():
        raw = jsonutil.load_json(hp)
        if isinstance(raw, list):
            hardcoded_rules = raw

    replacer = locale.HardcodedReplacer(hardcoded_rules) if hardcoded_rules else None
    tick(50, "写入语言文件")
    js_files = list(inst.assets().glob("*.js"))
    total_js = len(js_files)
    log(f"扫描 {total_js} 个前端 bundle…")

    for idx, js in enumerate(js_files, 1):
        if idx % 500 == 0:
            log(f"  {idx}/{total_js}")
            tick(50 + int(25 * idx / max(1, total_js)), f"扫描前端 {idx}/{total_js}")
        try:
            text = js.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        # 语言白名单只在含目标数组的文件里出现，先粗筛
        if '"en-US","de-DE"' not in text and f'"{lang}"' not in text:
            if replacer is None:
                continue

        original = text
        if '"en-US","de-DE"' in text:
            text, n = locale.patch_whitelist(text, lang)
            if n:
                whitelist_files += 1
        if "Intl.DisplayNames" in text and locale.DISPLAY_NAME_MARKER not in text:
            text, _ = locale.patch_display_names(text)
        if replacer is not None:
            text, c = replacer.apply(text)
            hardcoded_count += c

        if text != original:
            js.write_text(text, encoding="utf-8")

    # 斜杠命令双语菜单（/help 帮助），只改显示层
    slashmenu.patch_dir(inst.assets(), lang, log=log)

    tick(76, "注入 DOM 翻译")
    # ---- DOM 补丁（app.asar 主进程）----
    dom_strings = 0
    if with_asar:
        dom_strings = _patch_asar(inst, lang, log, app_work=app_work)

    return InstallReport(
        version=inst.version,
        lang=lang,
        frontend_translated=st_f.total - st_f.counts.get("english", 0),
        frontend_total=st_f.total,
        desktop_translated=st_d.total - st_d.counts.get("english", 0),
        desktop_total=st_d.total,
        whitelist_files=whitelist_files,
        hardcoded_replacements=hardcoded_count,
        dom_strings=dom_strings,
        signed_files=0,
        shell_enabled=with_asar,
        shell_registered=shell_registered,
    )


def _unpatch_dom(app_work: Path, log=print) -> int:
    """从主进程 bundle 里移除我们的 DOM 注入块（关闭增强时用）。"""
    from . import dompatch

    removed = 0
    for js in (app_work / ".vite" / "build").glob("*.js"):
        try:
            text = js.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if dompatch.MAIN_MARKER not in text:
            continue
        new, changed = dompatch.strip_previous(text)
        if changed:
            js.write_text(new, encoding="utf-8")
            removed += 1
    if removed:
        log(f"已移除 DOM 注入（{removed} 个 bundle）")
    return removed


def _patch_asar(
    inst: discover.ClaudeInstall, lang: str, log, app_work: Path | None = None
) -> int:
    """把 DOM 翻译脚本注入主进程。

    app_work 非空时（4.29+）在解包目录里改文件，由 AppAsarTarget 统一重打包；
    否则退回 3.x 的 asar 原地插入。
    """
    from . import dompatch

    # DOM 映射只取纯文本短句：含变量、换行或过长的条目交给 i18n 层处理
    en_frontend = jsonutil.load_dict(inst.frontend_en())
    mapping = {}
    for k, v in _load_pack("frontend.json").items():
        if k not in en_frontend:
            continue
        src = en_frontend[k]
        if "{" in src or "\n" in src or len(src) > 120:
            continue
        if validator.is_untranslated(v, src):
            continue
        mapping[src] = v

    rules = []
    dom_rules_path = _pack_root() / "dom-rules.json"
    if dom_rules_path.exists():
        raw = jsonutil.load_json(dom_rules_path)
        if isinstance(raw, list):
            rules = raw

    page_script = dompatch.build_page_script(lang, mapping, rules)

    # ── 4.29+：在解包目录里找主进程 bundle ──
    if app_work is not None:
        anchor = b'webContents.on("dom-ready"'
        candidates = []
        for js in (app_work / ".vite" / "build").glob("*.js"):
            try:
                body = js.read_bytes()
            except OSError:
                continue
            if dompatch.MAIN_MARKER.encode() in body or anchor in body:
                candidates.append(js)
        if not candidates:
            # DOM 兜底只是锦上添花：主界面的 i18n 才是主力。
            # 上游改了主进程结构时不要连累整个安装失败。
            log("跳过 DOM 注入：找不到锚点（Claude 主进程结构可能已变化）")
            return 0

        target = candidates[0]
        original = target.read_text(encoding="utf-8")
        updated, _ = dompatch.inject(original, page_script)
        if updated == original:
            log("DOM 补丁已是目标状态，跳过")
        else:
            target.write_text(updated, encoding="utf-8")
            log(f"DOM 补丁已注入：{len(mapping)} 条映射，{len(rules)} 条规则")
        return len(mapping)

    # ── 3.x：asar 原地插入 ──
    asar_file = inst.asar()
    if not asar_file.exists():
        log("app.asar 不存在，跳过 DOM 补丁")
        return 0

    data, hs, header = asar.load(asar_file)
    targets = asar_edit.find_by_marker(data, hs, header, dompatch.MAIN_MARKER.encode())
    if not targets:
        targets = [
            p for p in asar_edit.list_js_files(header)
            if b'webContents.on("dom-ready"'
            in asar.read_file(data, hs, asar.find_entry(header, p))
        ]
    if not targets:
        log("跳过 DOM 注入：找不到锚点（Claude 主进程结构可能已变化）")
        return 0

    target = targets[0]
    entry = asar.find_entry(header, target)
    original = asar.read_file(data, hs, entry).decode("utf-8")
    updated, _ = dompatch.inject(original, page_script)
    new_bytes = updated.encode("utf-8")

    if len(new_bytes) >= len(original):
        asar_edit.replace_file_content(inst.app, target, new_bytes)
        log(f"DOM 补丁已注入：{len(mapping)} 条映射，{len(rules)} 条规则")
    else:
        log("跳过 DOM 注入：新内容更短")

    return len(mapping)


def install(
    lang: str = paths.TARGET_LOCALE,
    *,
    with_asar: bool = True,
    dry_run: bool = False,
    verbose: bool = True,
    launch: bool = True,
    progress=None,
) -> InstallReport:
    """完整安装流程。progress(percent, message) 用于界面显示进度。"""
    def tick(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    inst = discover.detect()

    if inst.is_running():
        if dry_run:
            print("提示：Claude 正在运行，实际安装会先退出")
        else:
            tick(3, "退出 Claude")
            print("退出 Claude（等待进程结束）…")
            if not inst.quit(wait=True):
                raise SystemExit("Claude 未能退出，请手动退出后重试")

    # 已打补丁则先恢复原始版本；没有备份就直接在现有 app 上重打（幂等）
    if not dry_run and discover.is_patched(inst):
        # 但如果当前已是官方签名（说明 Claude 刚自动更新过），
        # 恢复备份会把新版本降级成旧版——直接在新版上重新打补丁即可
        if backup.is_officially_signed(inst.app):
            print("当前为官方新版本（可能刚自动更新），跳过恢复，直接打补丁")
        else:
            b = backup.best_backup(inst.app)   # 优先拿官方原版备份
            if b is None:
                # 备份可能已经被「恢复原样」用掉了；我们的补丁是覆盖式的，
                # 直接在现有 app 上重打不会叠加。
                print("检测到已有补丁，但没有可用备份：直接在现有 app 上重新打补丁")
            else:
                print("检测到已有补丁，先恢复原始版本…")
                cur = backup.bundle_version(inst.app)
                bak = backup.bundle_version(b)
                if backup.version_key(bak) < backup.version_key(cur):
                    raise SystemExit(
                        f"备份版本 {bak} 低于当前 {cur}，恢复会降级。\n"
                        "请手动重装 Claude 后重试。"
                    )
                backup.restore_backup(inst.app, b)

    if dry_run:
        print(f"[dry-run] 将在临时目录为 {inst.app} 打补丁，不修改真实文件")
        with tempfile.TemporaryDirectory(prefix="claude-zh-dry.") as td:
            tmp = Path(td) / "Claude.app"
            _copy_light(inst.app, tmp)
            report = patch_tree(tmp, lang, with_asar=with_asar, verbose=verbose, sign=False)
        print(report.render())
        return report

    # 临时目录打补丁：全程不碰真实 app，失败则原样保留
    with tempfile.TemporaryDirectory(prefix="claude-zh.") as td:
        tmp = Path(td) / "Claude.app"
        tick(8, "复制 Claude.app")
        print("复制到临时目录…")
        _ditto(inst.app, tmp)

        # 历史残留（javaht 的散装语言文件、上次中断的解包目录）在这一步清掉，
        # 保证装出来的是一个干净包；真实 app 在替换前完全不动。
        residue = cleanup.clean(tmp, log=print)
        removed = [r for r in residue if r.removed]

        tick(35, "打补丁")
        print("打补丁…")
        report = patch_tree(
            tmp, lang, with_asar=with_asar, verbose=verbose, progress=progress
        )
        report.cleaned = len(removed)

        # 打完先验证，验证不过不替换（javaht 的 verify 步骤）
        tick(88, "验证")
        print("验证临时副本…")
        _verify_tree(tmp, lang, with_asar=with_asar)

        # 先写 locale，再替换 app（javaht 的顺序）。
        # 反过来的话，Claude 启动时可能读到旧 locale 并写回 en-US。
        print("写入语言设置…")
        inst.write_locale(lang)

        tick(93, "备份并替换")
        print("备份并替换…")
        # 只有「当前这份还是官方原版」时，备份才值得标记
        pristine = backup.is_officially_signed(inst.app) and not discover.is_patched(inst)
        report.backup_path = backup.create_backup(inst.app, pristine=pristine)
        if not backup.is_complete_backup(report.backup_path):
            # 备份不完整就别替换，否则用户没有可回退的副本
            raise SystemExit(f"备份不完整，已中止安装：{report.backup_path}")
        try:
            backup.put_in_place(inst.app, tmp)
        except Exception as exc:
            # 替换失败：把备份放回原位，保证 Claude 仍在
            b = report.backup_path
            if b and b.exists() and not inst.app.exists():
                b.rename(inst.app)
                print(f"替换失败，已回滚到原版本：{exc}")
            raise SystemExit(f"替换 app 失败：{exc}") from exc

    tick(97, "清理旧备份")
    backup.prune_old_backups(inst.app, keep=1)

    # 需要自动打开时，等 Claude 完全启动后再确认一次 locale。
    if launch and not dry_run:
        import time

        tick(99, "打开 Claude")
        inst.launch()
        time.sleep(5)
        if inst.read_locale_from_config() != lang:
            print("Claude 启动时重置了 locale，补写中…")
            inst.write_locale(lang)

    return report


def strip_patch(app: Path, lang: str, log=print, *, pin_locale: str | None = None) -> bool:
    """把我们打进去的东西撤干净：外壳语言文件/白名单/DOM 注入 + 主界面语言包。

    用于「恢复原样」——当备份本身也是汉化状态时，零改动还原换回来的还是中文。
    ``pin_locale`` 给定时，会额外留一行脚本把前端 localStorage 里的
    ``spa:locale`` 改回该语言（否则 web 端会一直沿用缓存里的 zh-CN）。
    返回是否真的改动了包。
    """
    changed = False

    asar_path = app / frontend_bundle.APP_ASAR
    if shell.has_artifacts(asar_path, lang) or pin_locale is not None:
        with frontend_bundle.AppAsarTarget(app) as work:
            if work is not None:
                if shell.has_artifacts(asar_path, lang):
                    shell.uninstall(work, lang, log=log)
                _unpatch_dom(work, log)
                if pin_locale:
                    target = _find_main_bundle(work)
                    if target is not None:
                        original = target.read_text(encoding="utf-8")
                        updated, _ = dompatch.inject(
                            original, dompatch.build_locale_pin(pin_locale)
                        )
                        if updated != original:
                            target.write_text(updated, encoding="utf-8")
                            log(f"已把前端语言锁回 {pin_locale}（覆盖 localStorage 缓存）")
                changed = True

    # 主界面语言包：留着它，界面上会一直是中文（即使配置是 en-US）
    res = paths.resources_of(app)
    frontend_asar = res / "ion-dist.asar"
    frontend_dir = res / "ion-dist"
    has_frontend_pack = (
        asar.has_entry(frontend_asar, f"i18n/{lang}.json")
        if frontend_asar.is_file()
        else (frontend_dir / "i18n" / f"{lang}.json").exists()
    )
    if has_frontend_pack or slashmenu.has_marker_in_asar(frontend_asar):
        with frontend_bundle.FrontendTarget(app) as fe_dir:
            zh = fe_dir / "i18n" / f"{lang}.json"
            if zh.exists():
                zh.unlink()
                log(f"已移除主界面语言包 i18n/{lang}.json")
                changed = True
            if slashmenu.revert_dir(fe_dir / "assets" / "v1", log=log):
                changed = True

    if changed:
        integrity.sync_digest_slot(app)
    return changed


def _find_main_bundle(app_work: Path) -> Path | None:
    """在解包目录里找主进程 bundle（含 dom-ready 锚点的那个）。"""
    anchor = b'webContents.on("dom-ready"'
    for js in (app_work / ".vite" / "build").glob("*.js"):
        try:
            body = js.read_bytes()
        except OSError:
            continue
        if dompatch.MAIN_MARKER.encode() in body or anchor in body:
            return js
    return None


def _ditto(src: Path, dst: Path) -> None:
    """用 ditto 复制 app：保留扩展属性、ACL、资源分支（cp/copytree 会丢）。"""
    if dst.exists():
        shutil.rmtree(dst)
    result = subprocess.run(
        ["ditto", str(src), str(dst)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(f"复制 app 失败：{result.stderr.strip()}")


def _copy_light(src: Path, dst: Path) -> None:
    """dry-run 用：只复制会被修改的部分，跳过 Frameworks 等大二进制。"""
    dst.mkdir(parents=True, exist_ok=True)
    contents = dst / "Contents"
    contents.mkdir(exist_ok=True)

    info = src / "Contents" / "Info.plist"
    if info.exists():
        shutil.copy2(info, contents / "Info.plist")

    res_src = src / "Contents" / "Resources"
    res_dst = contents / "Resources"
    res_dst.mkdir(exist_ok=True)

    # 3.x：散装语言文件与 ion-dist 目录
    for name in ("en-US.json", "zh-CN.json", "zh-Hans.json"):
        p = res_src / name
        if p.exists():
            shutil.copy2(p, res_dst / name)

    # 4.29+：两个 asar（含前端与桌面壳层）
    for name in ("app.asar", "ion-dist.asar"):
        p = res_src / name
        if p.exists():
            shutil.copy2(p, res_dst / name)
        unpacked = Path(str(p) + ".unpacked")
        if unpacked.is_dir():
            shutil.copytree(unpacked, res_dst / f"{name}.unpacked", symlinks=True,
                            dirs_exist_ok=True)

    ion_dir = res_src / "ion-dist"
    if ion_dir.is_dir():
        shutil.copytree(ion_dir, res_dst / "ion-dist", symlinks=True, dirs_exist_ok=True)


def _verify_tree(app: Path, lang: str, *, with_asar: bool = True) -> None:
    """对打完补丁的临时副本做启动前自检，任一项不过就不替换。"""
    import hashlib
    import plistlib

    problems: list[str] = []
    res = app / "Contents" / "Resources"
    plist = app / "Contents" / "Info.plist"
    with plist.open("rb") as f:
        info = plistlib.load(f)
    integrity_map = info.get("ElectronAsarIntegrity", {}) or {}
    asar_layout = (res / "ion-dist.asar").is_file()

    # 1. 所有 asar 都可解析、条目 integrity 正确、plist 哈希同步
    parsed: dict[str, tuple[bytes, int, dict]] = {}
    for rel in ("app.asar", "ion-dist.asar"):
        asar_path = res / rel
        if not asar_path.exists():
            if rel == "app.asar":
                problems.append("缺少 app.asar")
            continue
        try:
            data, hs, header = asar.load(asar_path)
        except Exception as exc:
            problems.append(f"{rel} 无法解析：{exc}")
            continue
        parsed[rel] = (data, hs, header)

        for path, entry in asar.walk(header):
            if "unpacked" in entry or "integrity" not in entry:
                continue
            body = asar.read_file(data, hs, entry)
            if entry["integrity"]["hash"] != hashlib.sha256(body).hexdigest():
                problems.append(f"{rel} integrity 不符：{path}")

        plist_rel = f"Resources/{rel}"
        recorded = (integrity_map.get(plist_rel) or {}).get("hash")
        if recorded is not None:
            _hs, hstr, _h = asar.read_header(data)
            actual = hashlib.sha256(hstr.encode("utf-8")).hexdigest()
            if recorded != actual:
                problems.append(f"Info.plist 中 {rel} 的哈希未同步（会导致启动崩溃）")

    # 2. 桌面壳层：语言文件 + 白名单注册
    if not asar_layout:
        # 3.x：散装语言文件
        if not (res / f"{lang}.json").exists():
            problems.append(f"缺少桌面语言文件：{lang}.json")
    elif with_asar:
        app_parsed = parsed.get("app.asar")
        if app_parsed is None:
            problems.append("无法读取 app.asar，桌面壳层汉化未生效")
        else:
            data, hs, header = app_parsed
            entry = asar.find_entry(header, f"resources/i18n/{lang}.json")
            if entry is None:
                problems.append(f"app.asar 中缺少 resources/i18n/{lang}.json")
            else:
                body = asar.read_file(data, hs, entry).decode("utf-8", "replace")
                if not any("一" <= ch <= "鿿" for ch in body):
                    problems.append(f"resources/i18n/{lang}.json 里没有中文")

            registered = False
            for rel, entry2 in asar.walk(header):
                if not rel.endswith(".js"):
                    continue
                try:
                    text = asar.read_file(data, hs, entry2).decode("utf-8", "ignore")
                except Exception:
                    continue
                if "getPreferredSystemLanguages" in text:
                    registered = shell.is_whitelisted(text, lang)
                    break
            if not registered:
                problems.append(
                    f"外壳语言白名单未注册 {lang}（配置被重置后会退回英文）"
                )

    # 3. 主界面语言文件
    frontend_asar = res / "ion-dist.asar"
    frontend_dir = res / "ion-dist"
    if frontend_asar.exists():
        data, hs, header = asar.load(frontend_asar)
        entry = asar.find_entry(header, f"i18n/{lang}.json")
        if entry is None:
            problems.append(f"ion-dist.asar 中缺少 i18n/{lang}.json")
        else:
            body = asar.read_file(data, hs, entry).decode("utf-8", "replace")
            if '"' not in body:
                problems.append(f"i18n/{lang}.json 内容异常")

        dyn = asar.find_entry(header, f"i18n/dynamic/{lang}.json")
        if asar.find_entry(header, "i18n/dynamic/en-US.json") is not None and dyn is None:
            problems.append(f"ion-dist.asar 中缺少 i18n/dynamic/{lang}.json")
    elif frontend_dir.exists():
        if not (frontend_dir / "i18n" / f"{lang}.json").exists():
            problems.append(f"缺少主界面语言文件：i18n/{lang}.json")
    else:
        problems.append("找不到前端资源（ion-dist.asar 或 ion-dist 目录）")

    # 4. Electron Framework 里的字典摘要槽
    ok_slot, msg = integrity.verify_digest_slot(app)
    if not ok_slot:
        problems.append(msg)

    # 5. 原生 .lproj 字符串
    if native_strings.pack_file().exists() and not native_strings.installed(app):
        problems.append("原生 .lproj 中文字符串未安装")

    # 6. 签名与权限
    ok, err = signer.verify(app)
    if not ok:
        problems.append(f"签名无效：{err}")
    if not signer.has_library_validation_disabled(app):
        problems.append("重签名丢失了 entitlements（disable-library-validation 缺失）")
    if not signer.bundle_identifier(app):
        problems.append("重签名丢失了 bundle identifier")

    if problems:
        for item in problems[:12]:
            print(f"  验证失败：{item}")
        raise SystemExit("临时副本验证未通过，已中止安装（原 app 未改动）")

    print("  验证通过")
