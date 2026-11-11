# -*- coding: utf-8 -*-
"""NotePulse 的 Markdown 子集：解析成块、转纯文字、切换待办勾选。纯逻辑，不碰界面（test_logic.py 覆盖）。

支持：# / ## / ### 标题、- / * 无序列表（缩进分级）、1. 有序列表、- [ ] / - [x] 待办、> 引用、
``` 代码块、--- 分隔线；行内 **粗体** / __粗体__、*斜体*、`代码`、~~删除线~~、[文字](链接)（只显示文字）。
不支持：表格、图片、HTML、嵌套行内样式（**粗`码`** 里的 ` 原样显示）。
"""
import re

_INLINE = re.compile(
    r"(\*\*[^*\n]+?\*\*|__[^_\n]+?__|`[^`\n]+`|~~[^~\n]+?~~|\[[^\]\n]+\]\([^)\n]*\)|\*[^*\s][^*\n]*?\*)")
_H = re.compile(r"^(#{1,3})\s+(.*)$")
_TASK = re.compile(r"^(\s*)[-*+]\s+\[( |x|X)\]\s+(.*)$")
_UL = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_HR = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s*```")
_BOX = re.compile(r"\[( |x|X)\]")


def inline(s):
    """行内样式 → [(文字, 样式集合)]；样式取值 b / i / code / s / link。"""
    out = []
    for p in _INLINE.split(s):
        if not p:
            continue
        if (p.startswith("**") and p.endswith("**") or p.startswith("__") and p.endswith("__")) and len(p) > 4:
            out.append((p[2:-2], frozenset({"b"})))
        elif p.startswith("~~") and p.endswith("~~") and len(p) > 4:
            out.append((p[2:-2], frozenset({"s"})))
        elif p.startswith("`") and p.endswith("`") and len(p) > 2:
            out.append((p[1:-1], frozenset({"code"})))
        elif p.startswith("[") and "](" in p and p.endswith(")"):
            out.append((p[1:p.index("](")], frozenset({"link"})))
        elif p.startswith("*") and p.endswith("*") and len(p) > 2:
            out.append((p[1:-1], frozenset({"i"})))
        else:
            out.append((p, frozenset()))
    return out


def _level(ws):
    return min(3, len(ws.replace("\t", "    ")) // 2)


def parse(text):
    """源码 → 块列表。每块 dict：kind、line（源码起始行号，0 起）、segs 或 code，及各类型字段。"""
    blocks = []
    fence = None
    for i, ln in enumerate(text.split("\n")):
        if _FENCE.match(ln):
            if fence is None:
                fence = {"kind": "code", "line": i, "code": []}
            else:
                fence["code"] = "\n".join(fence["code"])
                blocks.append(fence)
                fence = None
            continue
        if fence is not None:
            fence["code"].append(ln)
            continue
        m = _H.match(ln)
        if m:
            blocks.append({"kind": "h%d" % len(m.group(1)), "line": i, "segs": inline(m.group(2))})
            continue
        m = _TASK.match(ln)
        if m:
            blocks.append({"kind": "task", "line": i, "level": _level(m.group(1)),
                           "checked": m.group(2) != " ", "segs": inline(m.group(3))})
            continue
        if _HR.match(ln):
            blocks.append({"kind": "hr", "line": i})
            continue
        m = _UL.match(ln)
        if m:
            blocks.append({"kind": "ul", "line": i, "level": _level(m.group(1)), "segs": inline(m.group(2))})
            continue
        m = _OL.match(ln)
        if m:
            blocks.append({"kind": "ol", "line": i, "level": _level(m.group(1)), "num": m.group(2),
                           "segs": inline(m.group(3))})
            continue
        m = _QUOTE.match(ln)
        if m:
            blocks.append({"kind": "quote", "line": i, "segs": inline(m.group(1))})
            continue
        if not ln.strip():
            blocks.append({"kind": "blank", "line": i})
            continue
        blocks.append({"kind": "p", "line": i, "segs": inline(ln)})
    if fence is not None:                       # 没闭合的代码块：剩下全算代码
        fence["code"] = "\n".join(fence["code"])
        blocks.append(fence)
    return blocks


def seg_text(segs):
    return "".join(t for t, _ in segs)


def plain(text):
    """MD 模式下「复制」用：去掉标记符号，保留屏幕上看得到的列表符号（• 1. ☐ ☑）。"""
    out = []
    for b in parse(text):
        k = b["kind"]
        ind = "  " * b.get("level", 0)
        if k == "code":
            out.append(b["code"])
        elif k in ("hr", "blank"):
            out.append("")
        elif k == "task":
            out.append(ind + ("☑ " if b["checked"] else "☐ ") + seg_text(b["segs"]))
        elif k == "ul":
            out.append(ind + "• " + seg_text(b["segs"]))
        elif k == "ol":
            out.append(ind + b["num"] + ". " + seg_text(b["segs"]))
        else:
            out.append(seg_text(b["segs"]))
    return "\n".join(out)


def toggle_task(text, line):
    """把第 line 行（0 起）待办的 [ ] ↔ [x] 互换；不是待办行原样返回。"""
    lines = text.split("\n")
    if not (0 <= line < len(lines)) or not _TASK.match(lines[line]):
        return text
    on = _TASK.match(lines[line]).group(2) != " "
    lines[line] = _BOX.sub("[ ]" if on else "[x]", lines[line], count=1)
    return "\n".join(lines)
