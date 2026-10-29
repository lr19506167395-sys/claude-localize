#!/bin/bash
# 打包并部署到 /Applications（不保留产物副本）
set -e
cd "$(dirname "$0")"

# 某些环境（从 app 里继承的 shell）会带上指向别的 app 的 PYTHONHOME/PYTHONPATH，
# 那样 py2app 会去错地方找标准库，直接 ModuleNotFoundError。
unset PYTHONHOME PYTHONPATH

echo "清理旧产物…"
rm -rf dist/Claude汉化.app build/bdist.*

echo "打包…"
build/venv-pack/bin/python setup.py py2app > /tmp/claude-localize-build.log 2>&1
build/venv-pack/bin/python setup.py postbuild >> /tmp/claude-localize-build.log 2>&1

echo "部署到 /Applications…"
APP_SRC="$PWD/dist/Claude汉化.app"
APP_DST="/Applications/Claude汉化.app"

# 校验源目录真的存在，避免 ditto 拿到空路径（源缺失时不要动 /Applications）
if [ ! -d "$APP_SRC" ]; then
  echo "错误：打包产物不存在，已中止部署：$APP_SRC"
  exit 1
fi

# 路径带空格也要安全：用 printf %q 转义后再交给 osascript
ESC_SRC=$(printf '%q' "$APP_SRC")
ESC_DST=$(printf '%q' "$APP_DST")
osascript -e "do shell script \"rm -rf $ESC_DST; ditto $ESC_SRC $ESC_DST\" with administrator privileges"

echo "清理产物（避免 Finder 出现重复 app）…"
rm -rf dist/Claude汉化.app

# 图标缓存只在图标文件真的换了才需要刷新，而且这一步会再弹一次管理员密码。
# 默认跳过；确实换了图标时用 REFRESH_ICONS=1 ./build.sh
if [ "${REFRESH_ICONS:-0}" = "1" ]; then
  echo "刷新图标缓存…"
  osascript -e 'do shell script "rm -rf /Library/Caches/com.apple.iconservices.store; killall Dock" with administrator privileges' || echo "  （跳过，可稍后手动刷新）"
else
  echo "刷新图标缓存…（跳过，图标未变；需要时用 REFRESH_ICONS=1 ./build.sh）"
fi

echo
echo "完成。已安装到 /Applications/Claude汉化.app"
