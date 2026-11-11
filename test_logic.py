# -*- coding: utf-8 -*-
"""纯逻辑单测（不开窗）：python test_logic.py → ALL PASS"""
import notepulse_md as md
import notepulse_app as app

SAMPLE = ("# 今日待办\n- [x] 回电王工：周三下午确认**接口字段**\n- [ ] 快递单号 `SF1402 6688 2031`\n\n"
          "## 会议要点\n1. 需规里的数据提供责任写给**乙方**\n2. 验收清单分三段\n> 周五前发给甲方确认")


def test_inline():
    assert md.inline("a**b**c") == [("a", frozenset()), ("b", frozenset({"b"})), ("c", frozenset())]
    assert md.inline("`x*y`") == [("x*y", frozenset({"code"}))]
    assert md.inline("*斜*") == [("斜", frozenset({"i"}))]
    assert md.inline("~~删~~") == [("删", frozenset({"s"}))]
    assert md.inline("[百度](https://x.y)") == [("百度", frozenset({"link"}))]
    assert md.inline("3 * 4 * 5") == [("3 * 4 * 5", frozenset())]      # 两边带空格的 * 不算斜体
    assert md.inline("") == []


def test_parse():
    kinds = [b["kind"] for b in md.parse(SAMPLE)]
    assert kinds == ["h1", "task", "task", "blank", "h2", "ol", "ol", "quote"], kinds
    b = md.parse(SAMPLE)
    assert b[1]["checked"] and not b[2]["checked"]
    assert b[5]["num"] == "1" and b[5]["line"] == 5
    assert md.parse("- a\n  - b\n    - c")[2]["level"] == 2
    assert [x["kind"] for x in md.parse("---\n***")] == ["hr", "hr"]
    c = md.parse("前\n```py\nx = 1\n\ny\n```\n后")
    assert [x["kind"] for x in c] == ["p", "code", "p"] and c[1]["code"] == "x = 1\n\ny"
    assert md.parse("```\n未闭合")[-1] == {"kind": "code", "line": 0, "code": "未闭合"}


def test_plain():
    assert md.plain(SAMPLE) == ("今日待办\n☑ 回电王工：周三下午确认接口字段\n☐ 快递单号 SF1402 6688 2031\n\n"
                                "会议要点\n1. 需规里的数据提供责任写给乙方\n2. 验收清单分三段\n周五前发给甲方确认")
    assert md.plain("- a\n  - b") == "• a\n  • b"
    assert md.plain("```\n**原样**\n```") == "**原样**"
    assert md.plain("") == ""


def test_toggle():
    t = md.toggle_task(SAMPLE, 2)
    assert t.split("\n")[2] == "- [x] 快递单号 `SF1402 6688 2031`"
    assert md.toggle_task(t, 2) == SAMPLE
    assert md.toggle_task(SAMPLE, 0) == SAMPLE           # 非待办行不动
    assert md.toggle_task(SAMPLE, 99) == SAMPLE


def test_config():
    assert app.clamp_fs(3) == app.FS_MIN == 5 and app.clamp_fs(99) == app.FS_MAX == 28 and app.clamp_fs(12) == 12
    assert app.clamp_fs("x") == app.DEFAULT_CFG["font_size"]


def test_dock():
    area = (0, 0, 1920, 1040)
    assert app.dock_edge_for(5, 300, 200, area, 20) == "left"
    assert app.dock_edge_for(1710, 300, 200, area, 20) == "right"
    assert app.dock_edge_for(800, 10, 200, area, 20) == "top"
    assert app.dock_edge_for(800, 300, 200, area, 20) is None
    assert app.dock_geometry("right", 1700, 300, 200, 150, area, True) == (1920, 300)
    assert app.dock_geometry("top", 800, 5, 200, 150, area, True) == (800, -150)


def test_resize():
    # (方向, dx, dy) → 新 (x, y, w, h)；最小 100×80
    g = (100, 100, 300, 200)
    assert app.resize_geom(g, "se", 50, 20, 100, 80) == (100, 100, 350, 220)
    assert app.resize_geom(g, "nw", 50, 20, 100, 80) == (150, 120, 250, 180)
    assert app.resize_geom(g, "w", 500, 0, 100, 80) == (300, 100, 100, 200)    # 左边拖过头：右缘不动
    assert app.resize_geom(g, "n", 0, -30, 100, 80) == (100, 70, 300, 230)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("ALL PASS")
