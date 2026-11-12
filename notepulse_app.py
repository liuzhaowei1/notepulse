# -*- coding: utf-8 -*-
"""NotePulse 贴边记事本：随手记、贴边收起、Ctrl+Alt+N 随叫随到。

窗口行为：无边框、📌 置顶、拖标题栏移动并记住位置、— 收进托盘、✕ 退出、
拖到左/右/上边缘吸附并收成标签。
设计取舍：
- 标题栏只有 复制 / 粘贴 / 剪切 / 清空（作用于全部文字；粘贴插到光标处），清空不弹确认，Ctrl+Z 撤回
- 边缘拖拽调大小；Ctrl+滚轮调字号（鼠标在文本区上即可，5–28）
- 左下角 源码 / MD 切换；MD 模式只读，复制得到去掉标记的纯文字
- 贴边展开后：鼠标离开且焦点不在本窗口才收起
- 全局热键 左Ctrl+左Alt+N（右侧组合不触发）
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import notepulse_md as md

IS_WINDOWS = sys.platform == "win32"

T = {
    "bg": "#1E1E1E", "title_bg": "#2A2A2A", "fg": "#FFFFFF", "fg_dim": "#9A9A9A", "text": "#F0F0F0",
    "sep": "#383838", "accent": "#F0B429", "accent_hi": "#F5C95A", "muted": "#5A5A5A", "hover": "#363636",
    "done": "#8A8A8A", "quote": "#B8B8B8", "qbar": "#5A5A5A", "code_bg": "#2E2E2E", "block_bg": "#262626",
    "toggle_bd": "#444444", "sel": "#4A4A4A", "toast": "#3A3A3A", "link": "#7FB2FF", "on_accent": "#1E1E1E",
}
UI = "Microsoft YaHei UI"
MONO = "Consolas"
ICON = "Segoe MDL2 Assets"
SYM = "Segoe UI Symbol"
UI_SCALE = 0.866            # 与家族一致
TITLEBAR_H = 28
BOTTOM_H = 30
RZ = 5                      # 边缘拖拽调大小的感应宽度（设计稿 px）
DEFAULT_W, DEFAULT_H = 250, 280
MIN_H = 110
FS_MIN, FS_MAX = 5, 28
SAVE_DELAY_MS = 400
TOAST_MS = 1600
POLL_MS = 100
# 贴边收起（停 0.15s 展开、离开 0.6s 收回；另加「焦点在本窗口不收」）
DOCK_EDGES = ("left", "right", "top")
DOCK_SNAP = 20
DOCK_EXPAND_S = 0.15
DOCK_COLLAPSE_S = 0.6
DOCK_POP_S = 2.0
DOCK_ANIM_MS = 180
DOCK_FRAME_MS = 20
DOCK_POLL_MS = 50
# 标签 ↔ 面板形变过渡：标签亮边 → 底板由标签矩形长成面板矩形 → 面板内容按底板裁切并淡入；收起反向
DOCK_MORPH = True
MORPH_EXPAND_MS = 380
MORPH_COLLAPSE_MS = 320
MORPH_GAP_MS = 0          # 帧间不再等：DwmFlush 已对齐刷新，after(1) 还会多等 1–2 ms
TAB_GLOW = "#F4F5F2"
TAB_H = 22
TAB_TEXT = "NOTE"
HOTKEY_TEXT = "左Ctrl+左Alt+N"
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_LCONTROL, VK_LMENU = 0xA2, 0xA4


# 本程序自己的 user32 / kernel32 句柄：tray.py 会给共享的 ctypes.windll.user32 设 argtypes（如 GetMessageW 要它自己的 MSG），
# 共用会让热键线程的 GetMessageW 抛 ArgumentError、线程退出、热键随之注销（2026-11-09 实测）
if IS_WINDOWS:
    import ctypes.wintypes as wt
    U32 = ctypes.WinDLL("user32")
    K32 = ctypes.WinDLL("kernel32")
    for _name, _res, _args in (
            ("GetForegroundWindow", wt.HWND, []), ("SetForegroundWindow", wt.BOOL, [wt.HWND]),
            ("BringWindowToTop", wt.BOOL, [wt.HWND]), ("GetParent", wt.HWND, [wt.HWND]),
            ("IsWindow", wt.BOOL, [wt.HWND]), ("GetWindowThreadProcessId", wt.DWORD, [wt.HWND, ctypes.c_void_p]),
            ("AttachThreadInput", wt.BOOL, [wt.DWORD, wt.DWORD, wt.BOOL]),
            ("RegisterHotKey", wt.BOOL, [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]),
            ("UnregisterHotKey", wt.BOOL, [wt.HWND, ctypes.c_int]),
            ("GetMessageW", ctypes.c_int, [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]),
            ("PostThreadMessageW", wt.BOOL, [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]),
            ("GetAsyncKeyState", ctypes.c_short, [ctypes.c_int]),
            ("SystemParametersInfoW", wt.BOOL, [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT])):
        _f = getattr(U32, _name)
        _f.restype, _f.argtypes = _res, _args
    U32.SetWindowRgn.restype, U32.SetWindowRgn.argtypes = ctypes.c_int, [wt.HWND, ctypes.c_void_p, wt.BOOL]
    G32 = ctypes.WinDLL("gdi32")
    G32.CreateRectRgn.restype = ctypes.c_void_p
    G32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    # 动画帧率：Windows 默认计时器精度 15.6 ms，after(20) 实际 31 ms（≈32 fps）；
    # 形变期间 timeBeginPeriod(1) 临时提精度，每帧 DwmFlush 等下一次屏幕合成，帧与刷新率对齐
    U32.RedrawWindow.restype, U32.RedrawWindow.argtypes = wt.BOOL, [wt.HWND, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    RDW_FULL = 0x1 | 0x4 | 0x80 | 0x100 | 0x400     # INVALIDATE | ERASE | ALLCHILDREN | UPDATENOW | FRAME
    WINMM = ctypes.WinDLL("winmm")
    DWM = ctypes.WinDLL("dwmapi")
    K32.GetCurrentThreadId.restype = wt.DWORD
    K32.CreateMutexW.restype = wt.HANDLE
    K32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    K32.GetLastError.restype = wt.DWORD


def P(px):
    """设计稿像素 → Tk 点单位（1px@96dpi = 0.75pt）。"""
    return f"{px * 0.75:g}p"


CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "NotePulse")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
NOTE_PATH = os.path.join(CONFIG_DIR, "note.md")
DEFAULT_CFG = {
    "pos_x": None, "pos_y": None, "width": None, "height": None, "always_on_top": True,
    "dock_edge": None, "font_size": 12, "mode": "src",
}


# ---------- 纯逻辑（test_logic.py 覆盖） ----------

def clamp_fs(v):
    try:
        return max(FS_MIN, min(FS_MAX, int(v)))
    except (TypeError, ValueError):
        return DEFAULT_CFG["font_size"]


def resize_geom(g, d, dx, dy, min_w, min_h):
    """g=(x,y,w,h)，d=拖的边（n/s/e/w 组合）；拖过最小尺寸时对边不动。"""
    x, y, w, h = g
    if "e" in d:
        w = max(min_w, w + dx)
    if "s" in d:
        h = max(min_h, h + dy)
    if "w" in d:
        nw = max(min_w, w - dx)
        x, w = x + (w - nw), nw
    if "n" in d:
        nh = max(min_h, h - dy)
        y, h = y + (h - nh), nh
    return x, y, w, h


def work_area(screen_w, screen_h):
    """主屏工作区 (left, top, right, bottom)；进程已声明 DPI 感知，与 tk 坐标同为物理像素。"""
    if IS_WINDOWS:
        try:
            import ctypes.wintypes as wintypes
            r = wintypes.RECT()
            if U32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                return r.left, r.top, r.right, r.bottom
        except Exception:  # noqa: BLE001
            pass
    return 0, 0, screen_w, screen_h


def dock_edge_for(x, y, width, area, snap):
    left, top, right, _bottom = area
    if x - left <= snap:
        return "left"
    if right - (x + width) <= snap:
        return "right"
    if y - top <= snap:
        return "top"
    return None


def dock_geometry(edge, x, y, width, height, area, collapsed):
    left, top, right, bottom = area
    if edge in ("left", "right"):
        y = max(top, min(bottom - height, y))
        if edge == "left":
            x = left - width if collapsed else left
        else:
            x = right if collapsed else right - width
    elif edge == "top":
        x = max(left, min(right - width, x))
        y = top - height if collapsed else top
    return x, y


def tab_geometry(edge, x, y, tab_w, tab_h, area):
    left, top, right, bottom = area
    if edge == "top":
        return max(left, min(right - tab_w, x)), top
    ty = max(top, min(bottom - tab_h, y))
    return (left if edge == "left" else right - tab_w), ty


def seg(x, a, b):
    """x 在 [a, b] 上的线性进度，夹到 0–1。"""
    return max(0.0, min(1.0, (x - a) / (b - a)))


def ease_io(x):
    """三次缓入缓出；关于 (0.5, 0.5) 对称，反向打断时进度可直接沿用。"""
    x = max(0.0, min(1.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


# ---------- 配置与笔记 ----------
def load_config():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    if cfg.get("dock_edge") not in DOCK_EDGES:
        cfg["dock_edge"] = None
    if cfg.get("mode") not in ("src", "md"):
        cfg["mode"] = "src"
    cfg["font_size"] = clamp_fs(cfg.get("font_size"))
    return cfg


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def save_config(cfg):
    try:
        _atomic_write(CONFIG_PATH, json.dumps(cfg, ensure_ascii=False, indent=2))
    except OSError:
        pass


def load_note():
    try:
        with open(NOTE_PATH, encoding="utf-8", newline="") as f:
            return f.read()
    except OSError:
        return ""


def app_icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "app.ico")


def tray_icon_pixels(size=16):
    """托盘图标回退（app.ico 读不到时用）：琥珀色三行文字。"""
    px = []
    for y, (x0, x1) in ((4, (2, 11)), (5, (2, 11)), (8, (2, 14)), (9, (2, 14)), (12, (2, 9)), (13, (2, 9))):
        px += [(x, y, T["accent"]) for x in range(x0, x1)]
    return px, size, size


def single_instance():
    if not IS_WINDOWS:
        return True
    k32 = K32
    single_instance.h = k32.CreateMutexW(None, False, "Local\\NotePulseSingleton")
    return k32.GetLastError() != 183     # ERROR_ALREADY_EXISTS


class Hotkey(threading.Thread):
    """独立线程注册全局热键并跑消息循环；按下时往队列放 "hotkey"，注册失败放 "hotkey_fail"。"""

    def __init__(self, q, mods, vk):
        super().__init__(daemon=True)
        self.q, self.mods, self.vk, self.tid = q, mods, vk, None

    def run(self):
        import ctypes.wintypes as wt
        u32 = U32
        self.tid = K32.GetCurrentThreadId()
        if not u32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            self.q.put("hotkey_fail")
            return
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            # 只认左 Ctrl + 左 Alt + N（RegisterHotKey 分不清左右，收到后再查左键是否按着）
            if msg.message == WM_HOTKEY and U32.GetAsyncKeyState(VK_LCONTROL) & 0x8000 \
                    and U32.GetAsyncKeyState(VK_LMENU) & 0x8000:
                self.q.put("hotkey")
        u32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.tid:
            U32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


class App:
    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.title("NotePulse")
        self.root.configure(bg=T["sep"])
        self.root.overrideredirect(True)
        if IS_WINDOWS:      # 明确设成工具窗口：形变里改 -alpha 会让 Tk 重建窗口样式、丢掉这一位，面板随即出现在任务栏
            self.root.attributes("-toolwindow", True)
        self.root.attributes("-topmost", self.cfg["always_on_top"])
        self.root.tk.call("tk", "scaling", float(self.root.tk.call("tk", "scaling")) * UI_SCALE)
        self.k = self.root.winfo_fpixels("1p") * 0.75      # 设计稿 1px → 物理像素
        self.mode = self.cfg["mode"]
        self.fs = self.cfg["font_size"]
        self._drag = (0, 0)
        self._dragging = False
        self._rs = None                 # 调大小进行中：(方向, 起点 x_root, y_root, 起始几何)
        self.dock_edge = None
        self.dock_collapsed = False
        self._dock_xy = (0, 0)
        self._dock_anim = None
        self._morphing = False
        self._morph_busy = False
        self._painted = {}              # 分层画面缓存记账：{"panel"/"ghost": 画好时的尺寸}
        self._morph_u = 1.0             # 形变进度：0＝标签，1＝面板
        self._tab_glow = False
        self._hover_since = self._leave_since = None
        self._pop_until = 0.0
        self._prev_fg = None
        self._save_job = self._toast_job = None
        self._rmap = []                 # MD 视图第 n 行（0 起）→ 源码行号
        self._hr = []

        self._make_fonts()
        self._build()
        self._build_tab()
        self._build_morph()
        self.src.insert("1.0", load_note())
        self.src.edit_reset()
        self.src.edit_modified(False)
        self.src.mark_set("insert", "end-1c")
        self._set_mode(self.mode, save=False)
        self._place()
        self.root.update_idletasks()
        self.hwnd = (U32.GetParent(self.root.winfo_id()) or 0) if IS_WINDOWS else 0
        if self.cfg.get("dock_edge"):
            self._restore_dock(self.cfg["dock_edge"])
            self.root.after(400, self._prime_paint)

        self._q = queue.Queue()
        self.tray = None
        self.hotkey = None
        if IS_WINDOWS:
            try:
                import tray as _tray
                self.tray = _tray.TrayIcon(
                    tip="NotePulse 记事本（%s）" % HOTKEY_TEXT, icon=tray_icon_pixels(), icon_file=app_icon_path(),
                    on_show=lambda: self._q.put("show"), on_exit=lambda: self._q.put("exit"))
                self.tray.start()
                self.tray.add()
            except Exception:  # noqa: BLE001
                self.tray = None
            self.hotkey = Hotkey(self._q, MOD_CONTROL | MOD_ALT, ord("N"))
            self.hotkey.start()

        self.root.after(POLL_MS, self._poll)
        self.root.after(DOCK_POLL_MS, self._dock_poll)

    def S(self, px):
        return max(1, round(px * self.k))

    # ----- 字体（Ctrl+滚轮改 self.fs 后整体重设） -----
    def _make_fonts(self):
        self.f = {
            "body": tkfont.Font(family=UI), "b": tkfont.Font(family=UI, weight="bold"),
            "i": tkfont.Font(family=UI, slant="italic"), "code": tkfont.Font(family=MONO),
            "h1": tkfont.Font(family=UI, weight="bold"), "h2": tkfont.Font(family=UI, weight="bold"),
            "h3": tkfont.Font(family=UI, weight="bold"), "box": tkfont.Font(family=SYM),
            "blank": tkfont.Font(family=UI),
            "tab": tkfont.Font(family=MONO, size=-self.S(11), weight="bold"),
        }
        self._size_fonts()

    def _size_fonts(self):
        fs = self.fs
        for k, m in (("body", 1), ("b", 1), ("i", 1), ("code", 0.92), ("h1", 1.5), ("h2", 1.28),
                     ("h3", 1.12), ("box", 1), ("blank", 0.6)):
            self.f[k].configure(size=max(1, round(fs * m)))

    # ----- 构建 -----
    def _build(self):
        r = self.root
        self.body = body = tk.Frame(r, bg=T["bg"])
        body.pack(fill="both", expand=True, padx=1, pady=1)

        bar = tk.Frame(body, bg=T["title_bg"], height=P(TITLEBAR_H))
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self.bar = bar
        for glyph, label, cmd in (("", "复制", self.copy_all), ("", "粘贴", self.paste),
                                  ("", "剪切", self.cut_all), ("", "清空", self.clear_all)):
            self._action_btn(bar, glyph, label, cmd)
        self.btn_close = self._tbtn(bar, "", self.close)
        self.btn_min = self._tbtn(bar, "", self.minimize)
        self.btn_pin = self._tbtn(bar, "", self.toggle_pin)
        self._refresh_pin()
        for w in (bar,):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        bottom = tk.Frame(body, bg=T["bg"], height=P(BOTTOM_H))
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        tg = tk.Frame(bottom, bg=T["title_bg"], highlightthickness=1, highlightbackground=T["toggle_bd"])
        tg.pack(side="left", padx=(P(8), 0), pady=(0, P(6)))
        self.tg_src = tk.Label(tg, text="源码", font=(UI, 9, "bold"), padx=P(9), pady=P(1), cursor="hand2")
        self.tg_md = tk.Label(tg, text="MD", font=(UI, 9, "bold"), padx=P(9), pady=P(1), cursor="hand2")
        self.tg_src.pack(side="left", padx=(P(1), 0), pady=P(1))
        self.tg_md.pack(side="left", padx=P(1), pady=P(1))
        self.tg_src.bind("<Button-1>", lambda e: self._set_mode("src", focus=True))
        self.tg_md.bind("<Button-1>", lambda e: self._set_mode("md"))
        grip = tk.Canvas(bottom, width=self.S(10), height=self.S(10), bg=T["bg"], highlightthickness=0, bd=0)
        grip.pack(side="right", anchor="se", padx=P(2), pady=P(2))
        g = self.S(10)
        grip.create_line(g - 1, g * 0.35, g * 0.35, g - 1, fill=T["muted"], width=max(1, self.S(1)))
        grip.create_line(g - 1, g * 0.7, g * 0.7, g - 1, fill=T["muted"], width=max(1, self.S(1)))

        self.area = area = tk.Frame(body, bg=T["bg"])
        area.pack(fill="both", expand=True)
        common = dict(bg=T["bg"], fg=T["text"], insertbackground=T["accent"], selectbackground=T["sel"],
                      selectforeground=T["fg"], inactiveselectbackground=T["sel"], relief="flat", bd=0,
                      highlightthickness=0, padx=self.S(10), pady=self.S(6), wrap="char", font=self.f["body"],
                      spacing1=self.S(1), spacing3=self.S(1))
        self.src = tk.Text(area, undo=True, maxundo=-1, autoseparators=True, **common)
        self.mdv = tk.Text(area, cursor="arrow", **common)
        self.mdv.configure(state="disabled", insertwidth=0)
        self._setup_md_tags()
        for w in (self.src, self.mdv):
            w.bind("<Control-MouseWheel>", self._on_zoom_wheel)
            w.bind("<Control-a>", self._select_all)
            w.bind("<Control-A>", self._select_all)
            w.bind("<Escape>", self._on_escape)
        self.src.bind("<<Modified>>", self._on_modified)
        self.mdv.bind("<Double-Button-1>", self._md_dblclick)
        self.mdv.bind("<<Undo>>", lambda e: self._md_undo("edit_undo"))     # MD 模式下 Ctrl+Z / Ctrl+Y 作用于源码
        self.mdv.bind("<<Redo>>", lambda e: self._md_undo("edit_redo"))
        self.mdv.bind("<Configure>", lambda e: self._size_hr())
        self.mdv.tag_bind("box", "<Button-1>", self._md_box_click)
        self.mdv.tag_bind("box", "<Enter>", lambda e: self.mdv.config(cursor="hand2"))
        self.mdv.tag_bind("box", "<Leave>", lambda e: self.mdv.config(cursor="arrow"))

        self.toast = tk.Label(area, bg=T["toast"], fg=T["fg"], font=(UI, 7), padx=P(8), pady=P(3))

        # 边缘调大小：8 个感应条叠在最上层
        rz, c = self.S(RZ), self.S(RZ * 2)
        tb = self.S(TITLEBAR_H)
        zones = {
            "n": (dict(x=c, y=0, relwidth=1, width=-2 * c, height=rz), T["title_bg"], "sb_v_double_arrow"),
            "s": (dict(x=c, rely=1, y=-rz, relwidth=1, width=-2 * c, height=rz), T["bg"], "sb_v_double_arrow"),
            "w": (dict(x=0, y=tb, width=rz, relheight=1, height=-tb), T["bg"], "sb_h_double_arrow"),
            "e": (dict(relx=1, x=-rz, y=tb, width=rz, relheight=1, height=-tb), T["bg"], "sb_h_double_arrow"),
            "nw": (dict(x=0, y=0, width=c, height=rz), T["title_bg"], "size_nw_se"),
            "ne": (dict(relx=1, x=-c, y=0, width=c, height=rz), T["title_bg"], "size_ne_sw"),
            "sw": (dict(x=0, rely=1, y=-rz, width=c, height=rz), T["bg"], "size_ne_sw"),
            "se": (dict(relx=1, x=-c, rely=1, y=-rz, width=c, height=rz), T["bg"], "size_nw_se"),
        }
        for d, (geo, col, cur) in zones.items():
            z = tk.Frame(body, bg=col, cursor=cur)
            z.place(**geo)
            z.bind("<Button-1>", lambda e, d=d: self._rs_start(e, d))
            z.bind("<B1-Motion>", self._rs_move)
            z.bind("<ButtonRelease-1>", self._rs_end)
        grip.configure(cursor="size_nw_se")
        grip.bind("<Button-1>", lambda e: self._rs_start(e, "se"))
        grip.bind("<B1-Motion>", self._rs_move)
        grip.bind("<ButtonRelease-1>", self._rs_end)

    def _action_btn(self, parent, glyph, label, cmd):
        """只有图标（带字太挤），悬停 0.5 秒出文字提示。"""
        ic = tk.Label(parent, text=glyph, font=(ICON, 10), bg=T["title_bg"], fg=T["accent"],
                      padx=P(8), cursor="hand2")
        ic.pack(side="left", padx=(P(2), 0), pady=P(2), fill="y")
        ic.bind("<Enter>", lambda e: (ic.config(bg=T["hover"]), self._tip_later(ic, label)))
        ic.bind("<Leave>", lambda e: (ic.config(bg=T["title_bg"]), self._tip_hide()))
        ic.bind("<ButtonRelease-1>", lambda e: (self._tip_hide(), cmd()))

    def _tip_later(self, w, text):
        self._tip_hide()
        self._tip_job = self.root.after(500, lambda: self._tip_show(w, text))

    def _tip_show(self, w, text):
        self._tip_job = None
        tip = self._tip = tk.Toplevel(self.root, bg=T["toggle_bd"])
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, bg=T["toast"], fg=T["fg"], font=(UI, 7), padx=P(6), pady=P(2)).pack(padx=1, pady=1)
        tip.geometry("+%d+%d" % (w.winfo_rootx(), w.winfo_rooty() + w.winfo_height() + self.S(2)))

    def _tip_hide(self):
        if getattr(self, "_tip_job", None):
            self.root.after_cancel(self._tip_job)
            self._tip_job = None
        if getattr(self, "_tip", None) is not None:
            self._tip.destroy()
            self._tip = None

    def _tbtn(self, parent, glyph, cmd):
        lbl = tk.Label(parent, text=glyph, bg=T["title_bg"], fg=T["fg_dim"], font=(ICON, 8), padx=P(7), cursor="hand2")
        lbl.pack(side="right", fill="y")
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.config(fg=T["fg"]))
        lbl.bind("<Leave>", lambda e: self._restore_btn(lbl))
        return lbl

    def _restore_btn(self, lbl):
        on = lbl is self.btn_pin and self.cfg["always_on_top"]
        lbl.config(fg=T["accent"] if on else T["fg_dim"])

    def _refresh_pin(self):
        self.btn_pin.config(fg=T["accent"] if self.cfg["always_on_top"] else T["muted"])

    def _min_w(self):
        self.bar.update_idletasks()
        return self.bar.winfo_reqwidth() + 2

    def _place(self):
        self.root.update_idletasks()
        w = max(self._min_w(), int(self.cfg.get("width") or self.S(DEFAULT_W)))
        h = max(self.S(MIN_H), int(self.cfg.get("height") or self.S(DEFAULT_H)))
        x, y = self.cfg.get("pos_x"), self.cfg.get("pos_y")
        if x is None or y is None:
            x, y = self.root.winfo_screenwidth() - w - 40, 120
        self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ----- 文本：保存、提示、字号 -----
    def text(self):
        return self.src.get("1.0", "end-1c")

    def _on_modified(self, e=None):
        if not self.src.edit_modified():
            return
        self.src.edit_modified(False)
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(SAVE_DELAY_MS, self.save_note)

    def save_note(self):
        self._save_job = None
        try:
            _atomic_write(NOTE_PATH, self.text())
        except OSError as e:
            self.flash("保存失败：%s" % e.strerror)

    def flash(self, msg):
        self.toast.config(text=msg)
        self.toast.place(relx=0.5, rely=1.0, anchor="s", y=-self.S(8))
        self.toast.lift()
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(TOAST_MS, self.toast.place_forget)

    def _on_zoom_wheel(self, e):
        self.zoom(1 if e.delta > 0 else -1)
        return "break"

    def zoom(self, d):
        fs = clamp_fs(self.fs + d)
        if fs != self.fs:
            self.fs = fs
            self._size_fonts()
            if self.mode == "md":
                self._render_md(keep_view=True)
            self.cfg["font_size"] = fs
            save_config(self.cfg)
        self.flash("字号 %d" % fs)

    def _select_all(self, e):
        e.widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    # ----- 四个按钮 -----
    def _clip_set(self, s):
        self.root.clipboard_clear()
        self.root.clipboard_append(s)
        self.root.update()

    def copy_all(self):
        t = self.text()
        if not t:
            return self.flash("没有内容可复制")
        s = md.plain(t) if self.mode == "md" else t
        self._clip_set(s)
        self.flash("已复制全部 %d 字（%s）" % (len(s), "纯文字" if self.mode == "md" else "源码"))

    def cut_all(self):
        t = self.text()
        if not t:
            return self.flash("没有内容可剪切")
        self._clip_set(md.plain(t) if self.mode == "md" else t)
        self._replace_all("")
        self._set_mode("src", focus=True)
        self.flash("已剪切全部，Ctrl+Z 撤回")

    def clear_all(self):
        if not self.text():
            return
        self._replace_all("")
        self._set_mode("src", focus=True)
        self.flash("已清空，Ctrl+Z 撤回")

    def paste(self):
        try:
            s = self.root.clipboard_get()
        except tk.TclError:
            s = ""
        if not s:
            return self.flash("剪贴板是空的")
        self._set_mode("src", focus=True)
        w = self.src

        def do():
            if w.tag_ranges("sel"):
                w.delete("sel.first", "sel.last")
            w.insert("insert", s)
        self._edit(do)
        w.see("insert")
        self.flash("已粘贴到光标处")

    def _edit(self, fn):
        """把一组删 + 插合成一步撤回（autoseparators 会在删 / 插切换处自动断开）。"""
        s = self.src
        s.configure(autoseparators=False)
        s.edit_separator()
        try:
            fn()
        finally:
            s.edit_separator()
            s.configure(autoseparators=True)

    def _replace_all(self, s):
        def do():
            self.src.delete("1.0", "end")
            if s:
                self.src.insert("1.0", s)
        self._edit(do)

    # ----- 源码 / MD 切换 -----
    def _set_mode(self, mode, focus=False, save=True):
        self.mode = mode
        on, off = {"bg": T["accent"], "fg": T["on_accent"]}, {"bg": T["title_bg"], "fg": T["quote"]}
        self.tg_src.config(**(on if mode == "src" else off))
        self.tg_md.config(**(on if mode == "md" else off))
        if mode == "md":
            self._render_md()
            self.src.pack_forget()
            self.mdv.pack(fill="both", expand=True)
        else:
            self.mdv.pack_forget()
            self.src.pack(fill="both", expand=True)
            if focus:
                self.src.focus_set()
                self.src.see("insert")
        self.toast.lift()
        if save and self.cfg["mode"] != mode:
            self.cfg["mode"] = mode
            save_config(self.cfg)

    def _setup_md_tags(self):
        v = self.mdv
        v.tag_configure("h1", font=self.f["h1"], foreground=T["fg"])
        v.tag_configure("h2", font=self.f["h2"], foreground=T["fg"])
        v.tag_configure("h3", font=self.f["h3"], foreground=T["fg"])
        v.tag_configure("blank", font=self.f["blank"])
        v.tag_configure("quote", foreground=T["quote"])
        v.tag_configure("qbar", foreground=T["qbar"])
        v.tag_configure("mark", foreground=T["accent"])
        v.tag_configure("box", font=self.f["box"], foreground=T["accent"])
        v.tag_configure("done", foreground=T["done"], overstrike=True)
        v.tag_configure("pre", font=self.f["code"], background=T["block_bg"], lmargincolor=T["block_bg"],
                        rmargincolor=T["block_bg"])
        v.tag_configure("b", font=self.f["b"], foreground=T["fg"])
        v.tag_configure("i", font=self.f["i"])
        v.tag_configure("code", font=self.f["code"], background=T["code_bg"], foreground=T["accent_hi"])
        v.tag_configure("codec", background=T["code_bg"], foreground=T["accent_hi"])
        v.tag_configure("s", overstrike=True)
        v.tag_configure("link", foreground=T["link"], underline=True)
        v.tag_raise("done")         # 已完成待办里的粗体 / 代码也要变灰加删除线

    def _render_md(self, keep_view=False):
        v, S, F = self.mdv, self.S, self.f
        top = v.yview()[0]
        v.configure(state="normal")
        v.delete("1.0", "end")
        for w in self._hr:
            w.destroy()
        self._hr, self._rmap = [], []
        fs_px = F["body"].metrics("linespace")
        v.tag_configure("h1", spacing1=round(fs_px * 0.5), spacing3=round(fs_px * 0.2))
        v.tag_configure("h2", spacing1=round(fs_px * 0.45), spacing3=round(fs_px * 0.15))
        v.tag_configure("h3", spacing1=round(fs_px * 0.35), spacing3=round(fs_px * 0.1))
        v.tag_configure("pre", lmargin1=S(6), lmargin2=S(6), rmargin=S(6), spacing1=0, spacing3=0)
        qw = F["body"].measure("▎ ")
        v.tag_configure("quote", lmargin1=0, lmargin2=qw)
        step = F["body"].measure("国国")
        blocks = md.parse(self.text())
        for n, b in enumerate(blocks):
            k = b["kind"]
            line_tags = ()
            if k in ("ul", "ol", "task"):
                lvl = b["level"]
                mark = {"ul": "•", "ol": b.get("num", "") + ".", "task": "☐"}[k]
                mw = (F["box"] if k == "task" else F["body"]).measure(mark + " ")
                tag = "L%d_%d" % (lvl, mw)
                v.tag_configure(tag, lmargin1=step * lvl, lmargin2=step * lvl + mw)
                line_tags = (tag,)
            if k == "code":
                lines = b["code"].split("\n")
                for j, ln in enumerate(lines):
                    v.insert("end", ln + "\n", ("pre",))
                    self._rmap.append(b["line"] + 1 + j)
                continue
            self._rmap.append(b["line"])
            if k == "hr":
                fr = tk.Frame(v, bg=T["sep"], height=max(1, S(1)), width=10)
                v.insert("end", " ", ("blank",))
                v.window_create("end-1c", window=fr, pady=S(4))
                v.insert("end", "\n", ("blank",))
                self._hr.append(fr)
                continue
            if k == "blank":
                v.insert("end", "\n", ("blank",))
                continue
            if k == "task":
                v.insert("end", ("☑" if b["checked"] else "☐") + " ", ("box",) + line_tags)
            elif k == "ul":
                v.insert("end", "• ", ("mark",) + line_tags)
            elif k == "ol":
                v.insert("end", b["num"] + ". ", ("mark",) + line_tags)
            elif k == "quote":
                v.insert("end", "▎ ", ("qbar", "quote"))
            base = line_tags + ((k,) if k in ("h1", "h2", "h3", "quote") else ())
            if k == "task" and b["checked"]:
                base += ("done",)
            for t, st in b["segs"]:
                tags = base
                if k in ("h1", "h2", "h3"):          # 标题里不换字体，只加颜色类样式
                    tags += tuple(("codec" if s == "code" else s) for s in st if s in ("code", "s", "link"))
                else:
                    tags += tuple(st)
                v.insert("end", t, tags)
            v.insert("end", "\n", base)
        if v.get("end-2c") == "\n":
            v.delete("end-2c")
        v.configure(state="disabled")
        self._size_hr()
        if keep_view:
            v.yview_moveto(top)

    def _size_hr(self):
        if self._hr:
            w = max(10, self.mdv.winfo_width() - 2 * self.S(10) - self.S(4))
            for fr in self._hr:
                fr.configure(width=w)

    def _md_line(self, e):
        n = int(self.mdv.index("@%d,%d" % (e.x, e.y)).split(".")[0]) - 1
        return self._rmap[n] if 0 <= n < len(self._rmap) else None

    def _md_box_click(self, e):
        ln = self._md_line(e)
        if ln is None:
            return "break"
        new = md.toggle_task(self.text(), ln)
        new_line = new.split("\n")[ln]
        s = self.src

        def do():
            s.delete("%d.0" % (ln + 1), "%d.end" % (ln + 1))
            s.insert("%d.0" % (ln + 1), new_line)
        self._edit(do)
        self._render_md(keep_view=True)
        return "break"

    def _md_undo(self, op):
        try:
            getattr(self.src, op)()
        except tk.TclError:          # 没有可撤回 / 重做的
            return "break"
        self._render_md(keep_view=True)
        return "break"

    def _md_dblclick(self, e):
        ln = self._md_line(e)
        if "box" in self.mdv.tag_names("@%d,%d" % (e.x, e.y)):
            return "break"
        self._set_mode("src", focus=True)
        if ln is not None:
            self.src.mark_set("insert", "%d.end" % (ln + 1))
            self.src.see("insert")
        return "break"

    # ----- 队列（托盘、热键） -----
    def _poll(self):
        try:
            while True:
                act = self._q.get_nowait()
                if act == "show":
                    self.restore()
                elif act == "exit":
                    self.close()
                    return
                elif act == "hotkey":
                    self.on_hotkey()
                elif act == "hotkey_fail":
                    self.flash("热键 %s 被别的程序占用" % HOTKEY_TEXT)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _active(self):
        if not IS_WINDOWS:
            return self.root.focus_get() is not None
        return (U32.GetForegroundWindow() or 0) == self.hwnd

    def _force_foreground(self):
        if not IS_WINDOWS:
            self.root.focus_force()
            return
        u32, k32 = U32, K32
        fg = u32.GetForegroundWindow()
        if fg != self.hwnd:
            me = k32.GetCurrentThreadId()
            other = u32.GetWindowThreadProcessId(fg, None) if fg else 0
            if other and other != me:
                u32.AttachThreadInput(me, other, True)
            u32.SetForegroundWindow(self.hwnd)
            u32.BringWindowToTop(self.hwnd)
            if other and other != me:
                u32.AttachThreadInput(me, other, False)
        self.root.focus_force()

    def _own_hwnds(self):
        if getattr(self, "_own", None) is None:
            self._own = {self.hwnd, U32.GetParent(self.tab_win.winfo_id()) or 0}
        return self._own

    def _track_prev_fg(self):
        """记住最近一个不属于本程序的前台窗口，收起时把焦点还给它（否则打字会进到收起的窗口里）。"""
        if IS_WINDOWS:
            fg = U32.GetForegroundWindow()
            if fg and fg not in self._own_hwnds():
                self._prev_fg = fg

    def _give_focus_back(self):
        if not IS_WINDOWS:
            return
        u32, k32 = U32, K32
        h = self._prev_fg
        if not h or not u32.IsWindow(h):
            return
        me = k32.GetCurrentThreadId()
        other = u32.GetWindowThreadProcessId(h, None)
        if other and other != me:
            u32.AttachThreadInput(me, other, True)
        u32.SetForegroundWindow(h)
        if other and other != me:
            u32.AttachThreadInput(me, other, False)

    def _on_escape(self, e=None):
        """Esc：把焦点还给进记事本前的窗口（只还焦点，不主动收起；贴边时照常按「鼠标离开 + 无焦点」收起）。"""
        self._give_focus_back()
        return "break"

    def on_hotkey(self):
        """Ctrl+Alt+N：不在前台 → 弹出并把光标放到文末（切到源码）；已在前台 → 收回并把焦点还给原窗口。"""
        visible = self.root.state() != "withdrawn" and not (self.dock_edge and self.dock_collapsed)
        if visible and self._active():
            if self.dock_edge:
                self._pop_until = 0.0
                self._set_collapsed(True)
            elif self.tray is not None:
                self.minimize()
            else:
                return
            self._give_focus_back()
            return
        self.restore()
        self._set_mode("src")
        self._force_foreground()
        self.src.focus_set()
        self.src.mark_set("insert", "end-1c")
        self.src.see("insert")

    # ----- 窗口动作 -----
    def _drag_start(self, e):
        self._dragging = True
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        if self.dock_edge:
            self._undock()
        self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, e):
        self._dragging = False
        x, y = e.x_root - self._drag[0], e.y_root - self._drag[1]
        edge = dock_edge_for(x, y, self.root.winfo_width(), self._work_area(), self.S(DOCK_SNAP))
        if edge:
            self._dock(edge, x, y)
            x, y = self._dock_xy
        self.cfg["pos_x"], self.cfg["pos_y"] = x, y
        self.cfg["dock_edge"] = self.dock_edge
        save_config(self.cfg)

    def _rs_start(self, e, d):
        r = self.root
        self._dragging = True
        self._cancel_slide()
        self._rs = (d, e.x_root, e.y_root, (r.winfo_x(), r.winfo_y(), r.winfo_width(), r.winfo_height()))

    def _rs_move(self, e):
        if not self._rs:
            return
        d, x0, y0, g = self._rs
        x, y, w, h = resize_geom(g, d, e.x_root - x0, e.y_root - y0, self._min_w(), self.S(MIN_H))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _rs_end(self, e):
        if not self._rs:
            return
        self._rs_move(e)
        self._rs = None
        self._dragging = False
        r = self.root
        r.update_idletasks()
        w, h = r.winfo_width(), r.winfo_height()
        x, y = r.winfo_x(), r.winfo_y()
        if self.dock_edge:            # 贴边时改了大小：重新贴齐
            self._dock_xy = dock_geometry(self.dock_edge, x, y, w, h, self._work_area(), collapsed=False)
            r.geometry("+%d+%d" % self._dock_xy)
            x, y = self._dock_xy
        self.cfg.update(width=w, height=h, pos_x=x, pos_y=y)
        save_config(self.cfg)
        if self.mode == "md":
            self._size_hr()

    def toggle_pin(self):
        self.cfg["always_on_top"] = not self.cfg["always_on_top"]
        self.root.attributes("-topmost", self._want_topmost())
        self._refresh_pin()
        save_config(self.cfg)

    def minimize(self):
        if self.tray is None:
            return
        self._cancel_slide()
        self._hide_tab()
        self._painted.pop("panel", None)        # 隐藏会丢分层画面缓存，恢复后下次形变前补画
        self.root.withdraw()

    def restore(self):
        self.root.deiconify()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.lift()
        if self.dock_edge:
            self._pop_until = time.time() + DOCK_POP_S
            if self.dock_collapsed:
                self._set_collapsed(False)
        if not self._want_topmost():
            self.root.after(100, lambda: self.root.attributes("-topmost", False))

    # ----- 贴边收起 -----
    def _work_area(self):
        return work_area(self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _want_topmost(self):
        return bool(self.dock_edge) or self.cfg["always_on_top"]

    def _dock(self, edge, x, y):
        x, y = dock_geometry(edge, x, y, self.root.winfo_width(), self.root.winfo_height(),
                             self._work_area(), collapsed=False)
        self.dock_edge, self.dock_collapsed = edge, False
        self._dock_xy = (x, y)
        self._hover_since = self._leave_since = None
        self.root.attributes("-topmost", True)
        self._slide_to(x, y)

    def _restore_dock(self, edge):
        r = self.root
        r.update_idletasks()
        w, h = r.winfo_width(), r.winfo_height()
        area = self._work_area()
        self.dock_edge = edge
        self._dock_xy = dock_geometry(edge, r.winfo_x(), r.winfo_y(), w, h, area, collapsed=False)
        self.dock_collapsed = True
        r.attributes("-topmost", True)
        r.geometry("+%d+%d" % dock_geometry(edge, *self._dock_xy, w, h, area, collapsed=True))
        self._show_tab()

    def _undock(self):
        self._cancel_slide()
        self.dock_edge, self.dock_collapsed = None, False
        self._hide_tab()
        self.root.attributes("-topmost", self._want_topmost())

    def _set_collapsed(self, collapsed):
        if self._morph_busy:            # 形变准备中（在处理补画），稍后再来，避免两段动画并行
            self.root.after(30, self._set_collapsed, collapsed)
            return
        if not self.dock_edge or collapsed == self.dock_collapsed:
            return
        self.dock_collapsed = collapsed
        self._hover_since = self._leave_since = None
        x, y = dock_geometry(self.dock_edge, *self._dock_xy, self.root.winfo_width(),
                             self.root.winfo_height(), self._work_area(), collapsed)
        if DOCK_MORPH and IS_WINDOWS and getattr(self, "hwnd", 0):
            self._morph_to(collapsed)
            return
        if not collapsed:
            self._hide_tab()
        self._slide_to(x, y, on_done=self._show_tab if collapsed else None)

    def _cancel_slide(self):
        if self._dock_anim is not None:
            self.root.after_cancel(self._dock_anim)
            self._dock_anim = None
        if self._morphing:              # 中途被打断（拖动、调大小、进托盘）：一律落到展开态，要收由轮询再收
            self.dock_collapsed = False
            self._morph_end(False)

    # ----- 标签 ↔ 面板形变 -----
    def _build_morph(self):
        # 底板与面板同尺寸、同位置，只换裁切区：每帧改窗口尺寸会让 Tk 重新布局重画（实测每帧 ~4 ms，144 Hz 下掉一半帧）
        w = tk.Toplevel(self.root, bg=T["bg"])
        w.overrideredirect(True)
        w.attributes("-toolwindow", True)     # 同面板：不进任务栏
        w.attributes("-topmost", True)
        w.withdraw()
        self.morph_win = w
        self._ghost_hwnd = 0

    def _morph_to(self, collapsed):
        """面板始终停在展开位置、只改透明度和裁切区；标签与底板各是独立小窗。反向打断时从当前进度接着走。"""
        r = self.root
        if self._morphing:
            if self._dock_anim is not None:
                r.after_cancel(self._dock_anim)
                self._dock_anim = None
        else:
            self._morph_u = 1.0 if collapsed else 0.0
            self.tab_w, self.tab_h = self._tab_size()
            self.tab_cv.config(width=self.tab_w, height=self.tab_h)
            self._place_tab()
            self._update_tab()
            self.tab_win.attributes("-alpha", 1.0 if not collapsed else 0.0)
            self.tab_win.deiconify()
            self._morph_prepare(collapsed)
        if not self._morphing:
            WINMM.timeBeginPeriod(1)
        self._morphing = True
        r.update_idletasks()
        w, h = r.winfo_width(), r.winfo_height()
        P = (*self._dock_xy, w, h)
        tx, ty = tab_geometry(self.dock_edge, *self._dock_xy, self.tab_w, self.tab_h, self._work_area())
        Tr = (tx, ty, self.tab_w, self.tab_h)
        g = self.morph_win
        self._morph_frame(ease_io(self._morph_u), Tr, P)
        g.attributes("-alpha", 0.999)                    # Tk 在 1.0 时会去掉分层属性
        for win in (self.morph_win, self.tab_win, r):          # 底板在最下，标签其上，面板最上
            win.attributes("-topmost", True)
            win.lift()
        target = 0.0 if collapsed else 1.0
        dur = (MORPH_COLLAPSE_MS if collapsed else MORPH_EXPAND_MS) / 1000
        t0, u0 = time.time(), self._morph_u

        def step():
            du = (time.time() - t0) / dur
            u = max(0.0, u0 - du) if collapsed else min(1.0, u0 + du)
            self._morph_u = u
            self._morph_frame(ease_io(u), Tr, P)
            if u != target:
                r.update_idletasks()        # 定时器一直就绪时 Tk 不进空闲，几何与重绘要手动刷出，否则中间帧不上屏
                DWM.DwmFlush()
                self._dock_anim = r.after(MORPH_GAP_MS, step)
            else:
                self._dock_anim = None
                self._morph_end(collapsed)
        step()

    def _morph_prepare(self, collapsed):
        """形变开始前：面板（展开时）全透明放到最终位置；面板 / 底板画面缓存失效的才整块重画。
        动画中换裁切区不重画（bRedraw=False，靠分层窗口的画面缓存），缓存必须有效：
        启动即收起时面板从没画过，不补画会展开成空心框。
        每次都整块重画要 25–230 ms，悬停后明显迟钝，所以只在尺寸变化 / 首次 / 托盘恢复后补画。"""
        r = self.root
        self._morph_busy = True
        try:
            r.update_idletasks()
            if not collapsed:
                r.attributes("-alpha", 0.0)
                self._set_rgn(None)
                r.geometry("+%d+%d" % self._dock_xy)
            self._ensure_painted()
        finally:
            self._morph_busy = False

    def _ensure_painted(self):
        """面板、底板的分层画面缓存按「尺寸」记账；不符的整块重画一次（底板保持映射、只用空裁切区藏起来，缓存不丢）。"""
        r, g = self.root, self.morph_win
        if not self._ghost_hwnd:
            g.attributes("-alpha", 0.0)
            g.deiconify()
            g.update_idletasks()
            self._ghost_hwnd = U32.GetParent(g.winfo_id()) or 0
        size = (r.winfo_width(), r.winfo_height())
        gx, gy = self._dock_xy if self.dock_edge else (r.winfo_x(), r.winfo_y())
        if (g.winfo_width(), g.winfo_height()) != size or (g.winfo_x(), g.winfo_y()) != (gx, gy):
            g.geometry("%dx%d+%d+%d" % (*size, gx, gy))
        stale = [h for h, key in ((self.hwnd, "panel"), (self._ghost_hwnd, "ghost")) if self._painted.get(key) != size]
        if not stale:
            return
        for h in stale:                              # 补画时要整窗可见区域（裁切区外的部分不会被画）
            self._set_rgn(None, hwnd=h)
        r.update_idletasks()
        for h in stale:
            U32.RedrawWindow(h, None, None, RDW_FULL)
        r.update()          # 处理补画；期间到来的展开/收起请求由 _set_collapsed 推迟
        if self._ghost_hwnd in stale:
            self._set_rgn(0, 0, 0, 0, hwnd=self._ghost_hwnd)
        for h, key in ((self.hwnd, "panel"), (self._ghost_hwnd, "ghost")):
            if h in stale:
                self._painted[key] = size

    def _prime_paint(self):
        """启动即收起：后台把面板在全透明下挪进屏幕画一次再挪回，第一次悬停不用等补画。"""
        if not (DOCK_MORPH and IS_WINDOWS and self.dock_edge and self.dock_collapsed) \
                or self._morphing or self._morph_busy:
            return
        r = self.root
        self._morph_busy = True
        try:
            hidden = (r.winfo_x(), r.winfo_y())
            r.attributes("-alpha", 0.0)
            r.geometry("+%d+%d" % self._dock_xy)
            self._ensure_painted()
            if self.dock_collapsed and not self._morphing:
                r.geometry("+%d+%d" % hidden)
                r.update_idletasks()
            r.attributes("-alpha", 1.0)
        finally:
            self._morph_busy = False

    def _morph_frame(self, k, Tr, P):
        R = [round(a + (b - a) * k) for a, b in zip(Tr, P)]
        box = (R[0] - P[0], R[1] - P[1], R[0] + R[2] - P[0], R[1] + R[3] - P[1])
        self._set_rgn(*box, hwnd=self._ghost_hwnd)
        ta = 1 - seg(k, 0, 0.3)
        if ta > 0:
            self.tab_win.attributes("-alpha", ta)
            if self.tab_win.state() == "withdrawn":
                self.tab_win.deiconify()
        elif self.tab_win.state() != "withdrawn":
            self.tab_win.withdraw()
        self.root.attributes("-alpha", min(0.999, seg(k, 0.35, 0.85)))   # 动画中保持分层，见底板注释
        self._set_rgn(*box)

    def _morph_end(self, collapsed):
        r = self.root
        if self._morphing:
            WINMM.timeEndPeriod(1)
        self._morphing = False
        if collapsed:
            x, y = dock_geometry(self.dock_edge or "left", *self._dock_xy, r.winfo_width(),
                                 r.winfo_height(), self._work_area(), True)
            r.geometry("+%d+%d" % (x, y))
            r.update_idletasks()
        self._set_rgn(None, redraw=not collapsed)      # 展开结束整窗重画一次兜底（画面缓存万一失效）
        r.attributes("-alpha", 1.0)
        if self._ghost_hwnd:
            self._set_rgn(0, 0, 0, 0, hwnd=self._ghost_hwnd)   # 底板不隐藏：隐藏会丢画面缓存
        self.tab_win.attributes("-alpha", 1.0)
        self._tab_glow = False
        if collapsed and self.dock_edge:
            self._show_tab()
        else:
            self._hide_tab()

    def _set_rgn(self, *box, hwnd=None, redraw=False):
        """窗口裁切区（窗口内坐标，默认面板窗口）；None 取消裁切。区域句柄交给系统，不用自己释放。"""
        rgn = None if box == (None,) else G32.CreateRectRgn(*box)
        U32.SetWindowRgn(hwnd or self.hwnd, rgn, False)     # 面板、底板动画中都是分层窗口，系统有整张画面，不必重画
        if redraw:
            U32.RedrawWindow(hwnd or self.hwnd, None, None, RDW_FULL)

    def _slide_to(self, tx, ty, on_done=None):
        self._cancel_slide()
        self.root.update_idletasks()
        sx, sy = self.root.winfo_x(), self.root.winfo_y()
        n = max(1, DOCK_ANIM_MS // DOCK_FRAME_MS)

        def step(i):
            k = 1 - (1 - i / n) ** 3
            self.root.geometry("+%d+%d" % (round(sx + (tx - sx) * k), round(sy + (ty - sy) * k)))
            if i < n:
                self._dock_anim = self.root.after(DOCK_FRAME_MS, step, i + 1)
            else:
                self._dock_anim = None
                if on_done:
                    on_done()
        step(1)

    @staticmethod
    def _pointer_in(win, px, py):
        if win is None or not win.winfo_viewable():
            return False
        x, y = win.winfo_rootx(), win.winfo_rooty()
        return x <= px < x + win.winfo_width() and y <= py < y + win.winfo_height()

    def _dock_poll(self):
        try:
            self._track_prev_fg()
            if self.dock_edge and self._dock_anim is None and not self._dragging and not self._morph_busy \
                    and self.root.state() != "withdrawn":
                now = time.time()
                px, py = self.root.winfo_pointerxy()
                if self.dock_collapsed:
                    glow = self._pointer_in(self.tab_win, px, py)
                    if glow != self._tab_glow:
                        self._tab_glow = glow
                        self._update_tab()
                    if glow:
                        if self._hover_since is None:
                            self._hover_since = now
                        elif now - self._hover_since >= DOCK_EXPAND_S:
                            self._set_collapsed(False)
                    else:
                        self._hover_since = None
                else:
                    # 鼠标在窗上，或焦点在本窗口（正在打字）→ 不收
                    if self._pointer_in(self.root, px, py) or self._active():
                        self._leave_since = None
                    elif self._leave_since is None:
                        self._leave_since = now
                    if self._leave_since is not None and now >= self._pop_until \
                            and now - self._leave_since >= DOCK_COLLAPSE_S:
                        self._set_collapsed(True)
        except tk.TclError:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(DOCK_POLL_MS, self._dock_poll)

    # ----- 标签窗口：横排「NOTE」+ 有内容时的黄点 -----
    def _build_tab(self):
        w = tk.Toplevel(self.root, bg=T["title_bg"])
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        if DOCK_MORPH and IS_WINDOWS:           # 建窗时就设成分层窗口：首次在形变里设透明度实测卡 ~140 ms
            w.attributes("-alpha", 0.999)
            w.attributes("-alpha", 1.0)
        w.withdraw()
        self.tab_w, self.tab_h = self.S(40), self.S(TAB_H)
        self.tab_cv = tk.Canvas(w, width=self.tab_w, height=self.tab_h, bg=T["title_bg"],
                                highlightthickness=0, bd=0, cursor="hand2")
        self.tab_cv.pack()
        self.tab_cv.bind("<Button-1>", lambda e: self._tab_click())
        self.tab_win = w

    def _tab_click(self):
        self._pop_until = time.time() + DOCK_POP_S
        self._set_collapsed(False)
        self._force_foreground()
        (self.src if self.mode == "src" else self.mdv).focus_set()

    def _tab_size(self):
        """三个边都横排「NOTE」，有内容时右侧加黄点。"""
        S, F = self.S, self.f["tab"]
        return S(9) + F.measure(TAB_TEXT) + S(5) + S(5) + S(9), S(TAB_H)

    def _update_tab(self):
        S, cv, F = self.S, self.tab_cv, self.f["tab"]
        cv.delete("all")
        r, cy = S(2.5), self.tab_h / 2
        cv.create_text(S(9), cy, text=TAB_TEXT, font=F, fill=T["fg"], anchor="w")
        if self._tab_glow:              # 鼠标停在标签上：亮一圈边，提示马上要展开
            g = max(1, round(S(1.5)))
            cv.create_rectangle(g / 2, g / 2, self.tab_w - g / 2, self.tab_h - g / 2,
                                outline=TAB_GLOW, width=g)
        if self.text():
            cx = S(9) + F.measure(TAB_TEXT) + S(5) + r
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T["accent"], width=0)

    def _place_tab(self):
        x, y = tab_geometry(self.dock_edge, *self._dock_xy, self.tab_w, self.tab_h, self._work_area())
        self.tab_win.geometry(f"{self.tab_w}x{self.tab_h}+{x}+{y}")

    def _show_tab(self):
        self.tab_w, self.tab_h = self._tab_size()
        self.tab_cv.config(width=self.tab_w, height=self.tab_h)
        self._place_tab()
        self._update_tab()
        self.tab_win.deiconify()
        self.tab_win.attributes("-topmost", True)
        self.tab_win.lift()

    def _hide_tab(self):
        self.tab_win.withdraw()

    def close(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self.save_note()
        self.cfg["pos_x"], self.cfg["pos_y"] = self._dock_xy if self.dock_edge \
            else (self.root.winfo_x(), self.root.winfo_y())
        self.cfg["dock_edge"] = self.dock_edge
        self.cfg["width"], self.cfg["height"] = self.root.winfo_width(), self.root.winfo_height()
        save_config(self.cfg)
        if self.hotkey:
            self.hotkey.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if "--geom" not in sys.argv and not single_instance():
        sys.exit(0)
    if IS_WINDOWS:
        try:  # 高 DPI 下不模糊
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    app = App()
    if "--geom" in sys.argv:   # 把窗口矩形写到 cwd 的 _geom.txt（截图用）
        app.root.attributes("-topmost", True)

        def _dump_geom():
            r = app.root
            with open("_geom.txt", "w") as f:
                f.write(f"{r.winfo_rootx()} {r.winfo_rooty()} {r.winfo_width()} {r.winfo_height()}")
            r.after(1000, _dump_geom)
        app.root.after(800, _dump_geom)
    app.run()
