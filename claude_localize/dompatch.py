"""在线页面 DOM 汉化：往主进程注入翻译脚本。

原理：Claude 登录后主窗口加载的是 claude.ai 的远程页面，
CSS/前端 bundle 都管不到，只能在 dom-ready 时注入 JS 改 DOM。

⚠ 关键点：注入的代码必须在**渲染进程**执行。
主进程里没有 window/document/localStorage，直接粘贴代码不会生效，
必须用 webContents.executeJavaScript() 送过去。
"""
from __future__ import annotations

import json
import re

MARKER = "__claudeZhInjected"
MAIN_MARKER = "/*__claudeZhMain*/"

# 主进程 dom-ready 锚点，按顺序尝试
ANCHOR_PATTERNS = [
    # 形如 y.webContents.on("dom-ready",(()=>{ ... }))
    re.compile(
        r'(?P<wc>[A-Za-z_$][\w$]*\.webContents)'
        r'\.on\("dom-ready",\(\(\)=>\{'
    ),
]


def build_page_script(lang: str, mapping: dict[str, str], rules: list) -> str:
    """生成在渲染进程里执行的脚本（会作为字符串传给 executeJavaScript）。"""
    pairs = [[k, v] for k, v in mapping.items()]
    payload = json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))
    rules_json = json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
    lang_json = json.dumps(lang)
    return (
        f'(()=>{{try{{'
        f'localStorage.setItem("spa:locale",{lang_json});'
        f'document.documentElement&&document.documentElement.setAttribute("lang",{lang_json});'
        f'if(window.{MARKER})return;window.{MARKER}=1;'
        f'const P={payload};const M=Object.create(null);'
        f'for(let i=0;i<P.length;i++)M[P[i][0]]=P[i][1];'
        f'const G={rules_json}.map(([p,r])=>[new RegExp(p),r]);'
        f'const N=s=>(s||"").replace(/\\s+/g," ").trim();'
        f'const R=s=>{{const n=N(s);const d=M[n];if(d)return d;'
        f'for(const[r,t]of G){{const m=n.match(r);'
        f'if(m)return t.replace(/\\$(\\d)/g,(_,i)=>m[i]||"")}}return null}};'
        f'const SKIP=new Set(["SCRIPT","STYLE","NOSCRIPT"]);'
        f'const CODE="pre,code,kbd,samp,var,.cm-editor,.monaco-editor,.hljs";'
        f'const USER=\'[data-testid="user-message"],.standard-markdown,'
        f'[contenteditable],[data-testid="chat-input"]\';'
        f'function T(){{try{{const b=document.body;if(!b)return;'
        f'const w=document.createTreeWalker(b,NodeFilter.SHOW_TEXT,{{acceptNode(n){{'
        f'const p=n.parentElement;'
        f'if(!p||SKIP.has(p.tagName)||p.closest(CODE)||p.closest(USER))'
        f'return NodeFilter.FILTER_REJECT;'
        f'return R(n.nodeValue)?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT'
        f'}}}});'
        f'let n;while(n=w.nextNode()){{const v=R(n.nodeValue);if(v)n.nodeValue=v}}'
        f'document.querySelectorAll("[aria-label],[title],[placeholder]").forEach(e=>{{'
        f'["aria-label","title","placeholder"].forEach(a=>{{'
        f'try{{if(e.closest(CODE)||e.closest(USER))return;'
        f'const v=e.getAttribute(a);const t=v&&R(v);if(t)e.setAttribute(a,t)}}'
        f'catch{{}}}})}});'
        f'}}catch{{}}}}'
        f'T();setTimeout(T,800);'
        f'if(!window.__czObs){{window.__czObs=1;'
        f'new MutationObserver(()=>{{clearTimeout(window.__czT);'
        f'window.__czT=setTimeout(T,150)}})'
        f'.observe(document.documentElement,'
        f'{{subtree:true,childList:true,characterData:true}})}}'
        f'}}catch(e){{}}}})();'
    )


def build_locale_pin(lang: str) -> str:
    """只锁语言的一行脚本（撤补丁后把 web 端从 localStorage 缓存切回来）。

    前端会优先读 ``localStorage["spa:locale"]``（``qv()``），我们以前注入的脚本
    把它设成了 zh-CN；撤掉注入后这个值还在，界面仍然是中文。用同一套 marker
    注入一行覆盖它，下次安装时会被 ``strip_previous`` 清掉再换成完整脚本。
    """
    lang_literal = json.dumps(lang)
    return (
        f'(()=>{{try{{'
        f'localStorage.setItem("spa:locale",{lang_literal});'
        f'document.documentElement&&document.documentElement.setAttribute("lang",{lang_literal})'
        f'}}catch(e){{}}}})();'
    )


def find_anchor(text: str) -> re.Match[str] | None:
    for pattern in ANCHOR_PATTERNS:
        m = pattern.search(text)
        if m:
            return m
    return None


def strip_previous(text: str) -> tuple[str, bool]:
    """移除之前注入的补丁（幂等重装）。"""
    pattern = re.compile(re.escape(MAIN_MARKER) + r".*?" + re.escape(MAIN_MARKER), re.S)
    new, n = pattern.subn("", text)
    return new, n > 0


def inject(text: str, page_script: str) -> tuple[str, bool]:
    """把「在渲染进程执行 page_script」的调用插到 dom-ready 回调开头。"""
    cleaned, _ = strip_previous(text)
    anchor = find_anchor(cleaned)
    if anchor is None:
        raise SystemExit("找不到 dom-ready 锚点，Claude 主进程结构可能已变化")

    wc = anchor.group("wc")
    js_literal = json.dumps(page_script)
    snippet = (
        f'{MAIN_MARKER}'
        f'{wc}.executeJavaScript({js_literal}).catch(()=>{{}});'
        f'{MAIN_MARKER}'
    )
    pos = anchor.end()
    return cleaned[:pos] + snippet + cleaned[pos:], True
