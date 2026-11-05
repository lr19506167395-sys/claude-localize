"""子进程输出的统一解码。

**不要用 ``subprocess(..., text=True)``。**

``text=True`` 走 ````locale.getpreferredencoding()`` 解码。命令行里 TTY
环境通常是 UTF-8，但 py2app 打包后从 Finder 启动的 app，这个值退化成
ASCII；此时任何含非 ASCII 字符的输出（最常见的来源就是中文路径
``/Applications/Claude汉化.app/...``）都会抛::

    UnicodeDecodeError: 'ascii' codec can't decode byte 0xe6 ...
                                 in position 45763: ordinal not in range(128)

实测就是这一条把菜单栏「安装补丁」整个打断的（`ps` 输出里含汉化 app
自己的中文可执行文件路径）。因为它在 `is_running()` 里，失败得很早，
用户只看到一句没头没尾的错误。

统一改成：拿 bytes，自己按 UTF-8 解，坏字节 replace 兜底。进程名/错误
信息解错一个字符，远好过整个安装崩掉。
"""
from __future__ import annotations

import subprocess


def decode(raw: bytes | None) -> str:
    """把子进程输出按 UTF-8 解码，坏字节替换而不是抛异常。"""
    if not raw:
        return ""
    return raw.decode("utf-8", "replace")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` 的安全版本：始终返回 bytes，不做隐式解码。

    调用方用 :func:`decode` 取文本。``text`` / ``encoding`` / ``errors``
    会被忽略，避免有人又传回来。
    """
    for key in ("text", "universal_newlines", "encoding", "errors", "input"):
        kwargs.pop(key, None)
    kwargs.setdefault("capture_output", True)
    return subprocess.run(cmd, **kwargs)
