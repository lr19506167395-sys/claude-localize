# 第三方资源声明

## 中文词表与原生字符串

本项目 `packs/zh-CN/` 下的资源来源于下列开源项目，经过筛选、合并与补充：

- 项目名称：claude-desktop-zh-cn
- 作者：javaht
- 项目地址：https://github.com/javaht/claude-desktop-zh-cn
- 许可证：MIT License
- 版权：Copyright (c) 2025 javaht

具体文件：

| 本仓库文件 | 上游文件 | 用途 |
|---|---|---|
| `packs/zh-CN/frontend.json` | `resources/frontend-zh-CN.json` | 主界面词表 |
| `packs/zh-CN/desktop.json` | `resources/desktop-zh-CN.json` | 桌面壳层词表 |
| `packs/zh-CN/hardcoded.json` | `resources/frontend-hardcoded-zh-CN.json` | 硬编码替换表 |
| `packs/zh-CN/dynamic.json` | `resources/dynamic-zh-CN.json` | 动态词表 |
| `packs/zh-CN/statsig.json` | `resources/statsig-zh-CN.json` | 功能开关文案 |
| `packs/zh-CN/Localizable.strings` | `resources/Localizable.strings` | macOS 原生字符串（UTF-16LE） |

原始许可证全文见 `licenses/javaht-MIT.txt`。

根据 MIT 许可证条款，本项目的词表可自由使用、修改与再分发，
但须保留上述版权声明与许可声明。

## 修改说明

本项目的词表在原始版本基础上做了以下处理：

1. 按当前 Claude Desktop 版本的 key 集合筛选
2. 过滤掉与英文原文相同、未实际翻译的条目
3. 校验并丢弃占位符、ICU 结构、HTML 标签不匹配的条目
4. 与官方 zh-Hans 译文、项目自有译文按优先级合并
5. `Localizable.strings` 原样随包分发，写入 `zh-Hans.lproj` / `zh_CN.lproj` / `zh-CN.lproj`
