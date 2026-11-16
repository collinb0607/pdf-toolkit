#!/usr/bin/env python3
"""
PDF Toolkit - a cross-platform (Windows / macOS / Linux) desktop app for the
PDF jobs websites charge for: merge, reorder, rotate, delete and extract pages,
split, compress, convert images to PDF and pages to images, add page numbers
or a watermark, and add or remove a password. Everything stays on your computer.

Pages are shown as thumbnails in one workspace: open PDFs and images, arrange
the pages, then save. Compression, page numbers, watermark and password are
options applied when saving.

Requirements:
    Python 3.8+ (with Tkinter, included in the python.org installers)
    pikepdf     -> reads, edits and writes PDFs (qpdf)
    pypdfium2   -> draws the page thumbnails and exports pages as images (PDFium)
    Pillow, pillow-heif -> images (pillow-heif opens iPhone HEIC photos)
    tkinterdnd2 -> drag and drop from other apps (optional: the app works without it)
    The app offers to install them on first run. Manual install, using the Python that runs the app:
        python -m pip install pikepdf pypdfium2 pillow pillow-heif tkinterdnd2

Run:
    python pdf_toolkit.py
"""

import io
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import zlib
from tkinter import filedialog, messagebox, simpledialog, ttk

# Filled in by load_libraries(), so the window can still open (and offer to
# install them) when they're missing.
pikepdf = pdfium = Image = ImageOps = ImageTk = None

PACKAGES = ["pikepdf", "pypdfium2", "pillow", "pillow-heif", "tkinterdnd2"]
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".gif"}
FILE_TYPES = [
    ("PDFs and images", " ".join("*" + e for e in sorted(PDF_EXTS | IMAGE_EXTS))),
    ("PDF files", "*.pdf"),
    ("All files", "*.*"),
]

# Compression: images inside the PDF are re-encoded as JPEG, shrunk to fit in
# max_side pixels. Only kept when the result is smaller.
COMPRESSION = {
    "None — keep everything as it is": None,
    "Light — best quality": dict(max_side=10000, quality=85),
    "Balanced — recommended": dict(max_side=2000, quality=70),
    "Strong — smallest file": dict(max_side=1200, quality=50),
}
NUMBER_FORMATS = ["1", "Page 1", "Page 1 of 9", "1 / 9"]
NUMBER_PLACES = ["Bottom centre", "Bottom right", "Top right"]
IMAGE_PAGE_SIZES = ["Same size as the image", "A4", "Letter"]
PAPER = {"A4": (595.28, 841.89), "Letter": (612.0, 792.0)}
IMAGE_DPI = 150  # page size for images without (sensible) DPI information

# Thumbnail grid. Pages are drawn once at RENDER_PX and scaled to the zoom size.
RENDER_PX = 300
PAD = 16

# Look and feel: a dark app bar, a light tools panel, pages on a grey desk.
_FAMILY = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
UI = dict(
    family=_FAMILY, font=(_FAMILY, 10), small=(_FAMILY, 9), tiny=(_FAMILY, 8), bold=(_FAMILY, 10, "bold"),
    heading=(_FAMILY, 13, "bold"), title=(_FAMILY, 12, "bold"), icon=(_FAMILY, 14),
    appbar="#2b2b2b", toolbar="#ffffff", panel="#ffffff", canvas="#e4e6ea", statusbar="#f6f7f9",
    border="#d9dce1", hover="#eceef2", text="#1f2328", muted="#68707d", disabled="#b4b9c2",
    accent="#1473e6", accent_dark="#0d5fc4", sel_bg="#dbe9fd", error="#b42318",
)

# Helvetica character widths (1/1000 em) for ASCII 32-126, for centring stamps.
HELVETICA = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + [
    278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
    278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
    260, 334, 584]

# PDFium isn't thread-safe: every call into it goes through this lock.
PDFIUM_LOCK = threading.Lock()


def load_libraries():
    """Import the PDF and image libraries. Returns the pip names of missing
    packages (tkinterdnd2 is optional and checked separately)."""
    global pikepdf, pdfium, Image, ImageOps, ImageTk
    import importlib
    importlib.invalidate_caches()  # see packages installed since startup
    missing = []
    try:
        import pikepdf
    except ImportError:
        missing.append("pikepdf")
    try:
        import pypdfium2 as pdfium
    except ImportError:
        missing.append("pypdfium2")
    try:
        from PIL import Image, ImageOps, ImageTk
        Image.MAX_IMAGE_PIXELS = 250_000_000
    except ImportError:
        missing.append("pillow")
    try:
        import pillow_heif
        if Image:
            pillow_heif.register_heif_opener()
    except ImportError:
        missing.append("pillow-heif")
    try:
        import tkinterdnd2  # noqa: F401
    except ImportError:
        missing.append("tkinterdnd2")
    return missing


# Hide the console window that would otherwise flash up on Windows.
_POPEN_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def install_requirements(packages):
    """pip-install the packages into *this* Python interpreter.

    Using sys.executable guarantees they land in the same Python that's running
    the app — the usual cause of 'I installed it but it's not found' is having
    several Pythons, where `pip` and double-clicking the script use different ones.
    """
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):  # pythonw has no console; use python.exe for pip
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    r = subprocess.run([exe, "-m", "pip", "install", "--upgrade"] + packages,
                       capture_output=True, text=True, errors="replace", **_POPEN_FLAGS)
    add_user_site()
    return r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-3:]


def add_user_site():
    """Make packages pip just put in the per-user folder importable right away.

    When Python's own folder isn't writable (e.g. installed under Program
    Files), pip installs to the user folder instead (%APPDATA%\\Python\\...
    on Windows, ~/Library/Python/... on macOS). Python only adds that folder
    to sys.path at startup if it already existed, so on a first install it
    isn't searched until the app is restarted.
    """
    import site
    path = site.getusersitepackages() if site.ENABLE_USER_SITE else None  # off in venvs
    if path and os.path.isdir(path) and path not in sys.path:
        site.addsitedir(path)


# --------------------------------------------------------------------------- #
#  PDF building blocks (no Tk in here)
# --------------------------------------------------------------------------- #
class PasswordNeeded(Exception):
    pass


class Cancelled(Exception):
    pass


def open_pdf_bytes(data, password=""):
    """Check a PDF opens (with the password, if any). Returns its page count.
    Raises PasswordNeeded for a missing or wrong password."""
    try:
        with pikepdf.open(io.BytesIO(data), password=password) as pdf:
            return len(pdf.pages)
    except pikepdf.PasswordError:
        raise PasswordNeeded()


def image_to_pdf(path, page_size):
    """Turn an image file into PDF bytes, one page per frame (multi-page TIFFs).

    A JPEG goes in as it is (no quality loss) when nothing needs changing;
    anything else is stored losslessly, with transparency as a soft mask.
    """
    pdf = pikepdf.new()
    with Image.open(path) as im:
        frames = getattr(im, "n_frames", 1) if (im.format or "").upper() == "TIFF" else 1
        for f in range(frames):
            im.seek(f)
            im.load()
            frame = ImageOps.exif_transpose(im) if f == 0 else im.copy()
            dpi = im.info.get("dpi", (0, 0))[0] or 0
            raw = None
            if (im.format == "JPEG" and frame.mode in ("RGB", "L") and frame.size == im.size
                    and im.getexif().get(0x0112, 1) == 1):
                with open(path, "rb") as fh:
                    raw = fh.read()  # the original JPEG data, untouched
            pdf.pages.append(_image_page(pdf, frame, raw, dpi, page_size))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _image_page(pdf, im, jpeg, dpi, page_size):
    alpha = None
    if jpeg is not None:
        data, filt, mode = jpeg, pikepdf.Name.DCTDecode, im.mode
    else:
        if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            div = 256 if im.mode.startswith("I;16") else 1
            im = im.convert("I").point(lambda v: v / div).convert("L")
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        if has_alpha:
            rgba = im.convert("RGBA")
            a = rgba.getchannel("A")
            if a.getextrema()[0] < 255:
                alpha = a
            im = rgba.convert("RGB")
        mode = "L" if im.mode in ("L", "1") else "RGB"
        im = im.convert(mode)
        data, filt = zlib.compress(im.tobytes(), 6), pikepdf.Name.FlateDecode
    cs = pikepdf.Name.DeviceGray if mode == "L" else pikepdf.Name.DeviceRGB
    xobj = pdf.make_stream(data, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=im.width,
                           Height=im.height, ColorSpace=cs, BitsPerComponent=8, Filter=filt)
    if alpha is not None:
        xobj.SMask = pdf.make_stream(zlib.compress(alpha.tobytes(), 6), Type=pikepdf.Name.XObject,
                                     Subtype=pikepdf.Name.Image, Width=im.width, Height=im.height,
                                     ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8,
                                     Filter=pikepdf.Name.FlateDecode)
    # Page size: the image at its own resolution, or fitted onto paper.
    if dpi < 30 or dpi > 1200:
        dpi = IMAGE_DPI
    iw, ih = im.width * 72 / dpi, im.height * 72 / dpi
    if page_size in PAPER:
        pw, ph = PAPER[page_size]
        if im.width > im.height:
            pw, ph = ph, pw  # landscape paper for a landscape image
        margin = 36
        scale = min((pw - 2 * margin) / iw, (ph - 2 * margin) / ih, 1.0)
        dw, dh = iw * scale, ih * scale
        x, y = (pw - dw) / 2, (ph - dh) / 2
    else:
        pw, ph, dw, dh, x, y = iw, ih, iw, ih, 0, 0
    content = f"q {dw:.3f} 0 0 {dh:.3f} {x:.3f} {y:.3f} cm /Im0 Do Q".encode()
    return pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, round(pw, 3), round(ph, 3)],
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj)),
        Contents=pdf.make_stream(content)))


def inherited(page_obj, key, default=None):
    """A page attribute that may be set on the page or inherited from its parents."""
    node = page_obj
    for _ in range(64):
        if key in node:
            return node[key]
        if "/Parent" not in node:
            break
        node = node.Parent
    return default


def compress_images(pdf, max_side, quality, check_cancel):
    """Re-encode the images inside the PDF as JPEG (shrunk to max_side pixels),
    keeping each new version only if it's smaller. Images that JPEG can't hold
    faithfully (masks, 1-bit scans, CMYK, palettes, special colour spaces) are left alone."""
    seen, saved, changed = set(), 0, 0
    for page in pdf.pages:
        check_cancel()
        images = page.get_images() if hasattr(page, "get_images") else page.images
        for raw in list(images.values()):
            if raw.objgen in seen or raw.objgen == (0, 0):
                continue
            seen.add(raw.objgen)
            try:
                if raw.get("/ImageMask", False) or "/Decode" in raw or raw.get("/BitsPerComponent", 8) != 8:
                    continue
                pim = pikepdf.PdfImage(raw)
                if pim.mode not in ("RGB", "L") or pim.indexed:
                    continue
                im = pim.as_pil_image()
                if im.mode not in ("RGB", "L"):
                    continue
                before = len(raw.read_raw_bytes())
            except Exception:
                continue  # an unusual image: leave it as it is
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) >= before:
                continue
            cs = raw.get("/ColorSpace")
            raw.write(data, filter=pikepdf.Name.DCTDecode)
            raw.Width, raw.Height, raw.BitsPerComponent = im.width, im.height, 8
            if not (isinstance(cs, pikepdf.Array) and len(cs) and cs[0] == pikepdf.Name.ICCBased):
                raw.ColorSpace = pikepdf.Name.DeviceGray if im.mode == "L" else pikepdf.Name.DeviceRGB
            for k in ("/DecodeParms", "/SMaskInData"):
                if k in raw:
                    del raw[k]
            saved += before - len(data)
            changed += 1
    return changed, saved


def _pdf_text(text):
    """Text as a PDF string in the WinAnsi encoding of the built-in Helvetica font."""
    b = text.encode("cp1252", "replace")
    return b"(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def text_width(text, size):
    return sum(HELVETICA[ord(c) - 32] if 32 <= ord(c) < 127 else 556 for c in text) * size / 1000


def stamp_pages(pdf, numbers=None, place="Bottom centre", watermark=None):
    """Add page numbers and/or a diagonal watermark, upright as each page is shown
    (whatever its rotation), in the standard Helvetica font (no font is embedded)."""
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    gs = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, ca=0.22, CA=0.22))
    total = len(pdf.pages)
    for i, page in enumerate(pdf.pages):
        box = [float(v) for v in inherited(page.obj, "/CropBox", None) or inherited(page.obj, "/MediaBox")]
        x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
        w, h = x1 - x0, y1 - y0
        rot = int(inherited(page.obj, "/Rotate", 0)) % 360
        # The page as it's shown: width W, height H, and the matrix from those
        # "shown" coordinates to the page's own.
        W, H = (h, w) if rot in (90, 270) else (w, h)
        cm = {0: (1, 0, 0, 1, x0, y0), 90: (0, 1, -1, 0, x1, y0),
              180: (-1, 0, 0, -1, x1, y1), 270: (0, -1, 1, 0, x0, y1)}[rot]
        ops = [f"q {' '.join(f'{v:.4f}' if isinstance(v, float) else str(v) for v in cm)} cm"]
        if watermark:
            size = min(W, H) / max(6, len(watermark) * 0.62)
            tw = text_width(watermark, size)
            import math
            c, s = math.cos(math.radians(35)), math.sin(math.radians(35))
            # centre the text on the page, turned 35°
            tx = W / 2 - (tw / 2) * c + (size * 0.35) * s
            ty = H / 2 - (tw / 2) * s - (size * 0.35) * c
            ops.append(f"q /TKGS gs 0.6 0 0 rg BT /TKHelv {size:.2f} Tf {c:.4f} {s:.4f} {-s:.4f} {c:.4f} "
                       f"{tx:.2f} {ty:.2f} Tm ")
            ops.append(_pdf_text(watermark).decode("latin-1") + " Tj ET Q")
        if numbers:
            text = (numbers.replace("9", str(total)).replace("1", str(i + 1), 1)
                    if numbers != "1" else str(i + 1))
            size = 10
            tw = text_width(text, size)
            margin = 24
            tx = {"Bottom centre": W / 2 - tw / 2, "Bottom right": W - margin - tw, "Top right": W - margin - tw}[place]
            ty = H - margin - size if place == "Top right" else margin
            ops.append(f"BT 0 g /TKHelv {size} Tf {tx:.2f} {ty:.2f} Td ")
            ops.append(_pdf_text(text).decode("latin-1") + " Tj ET")
        ops.append("Q")
        # Our own resources dictionary (a copy), so pages sharing one aren't changed.
        res = page.obj.get("/Resources")
        res = pikepdf.Dictionary(res) if isinstance(res, pikepdf.Dictionary) else pikepdf.Dictionary()
        fonts = res.get("/Font")
        res.Font = pikepdf.Dictionary(fonts) if isinstance(fonts, pikepdf.Dictionary) else pikepdf.Dictionary()
        res.Font.TKHelv = font
        states = res.get("/ExtGState")
        res.ExtGState = pikepdf.Dictionary(states) if isinstance(states, pikepdf.Dictionary) else pikepdf.Dictionary()
        res.ExtGState.TKGS = gs
        page.obj.Resources = res
        # Wrap the existing content in q/Q, so whatever state it leaves doesn't affect the stamp.
        page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
        page.contents_add(pdf.make_stream(("\nQ\n" + "\n".join(ops)).encode("latin-1")))


def explain_error(e):
    name = type(e).__name__
    if isinstance(e, PermissionError):
        return "Permission denied — the file may be open in another program."
    if isinstance(e, FileNotFoundError):
        return "File not found."
    if isinstance(e, MemoryError):
        return "Not enough memory."
    if name == "UnidentifiedImageError":
        return "Not an image, or the file is damaged."
    if name in ("PdfError", "PdfiumError"):
        return "The PDF looks damaged and couldn't be read."
    return str(e) or name


def fmt_size(n):
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def enable_drag_and_drop(root, widgets, on_drop):
    """Accept files dragged onto the given widgets, using tkinterdnd2 if it's
    installed. on_drop(paths, x_root, y_root). Returns False when it isn't."""
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        TkinterDnD.require(root)
    except Exception:
        return False

    def drop(event):
        # The paths come as a Tcl list: names with spaces are in {braces}.
        on_drop(list(root.tk.splitlist(event.data)), event.x_root, event.y_root)
        return event.action  # "copy": the files stay where they are

    for w in widgets:
        w.drop_target_register(DND_FILES)
        w.dnd_bind("<<Drop>>", drop)
    return True


# --------------------------------------------------------------------------- #
#  The app
# --------------------------------------------------------------------------- #
class Doc:
    """One opened file. Its PDF bytes are kept in memory, so the original file
    can be moved or even overwritten by Save without breaking the workspace."""
    def __init__(self, path, data, password, pages):
        self.path, self.data, self.password, self.pages = path, data, password, pages
        self.name = os.path.basename(path)
        self.render = None  # pypdfium2 document, opened on the render thread


class FlatButton(tk.Label):
    """A flat, label-like button with a hover highlight, like a modern editor's toolbar."""
    def __init__(self, parent, text, command, bg, fg=None, hover=None, font=None, padx=10, pady=5,
                 primary=False, tip=None, **kw):
        self.colors = dict(bg=bg, fg=fg or UI["text"], hover=hover or UI["hover"])
        if primary:
            self.colors = dict(bg=UI["accent"], fg="#ffffff", hover=UI["accent_dark"])
        super().__init__(parent, text=text, bg=self.colors["bg"], fg=self.colors["fg"], font=font or UI["font"],
                         padx=padx, pady=pady, cursor="hand2", **kw)
        self.command, self.enabled = command, True
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg=self.colors["hover"]))
        self.bind("<Leave>", lambda e: self.config(bg=self.colors["bg"]))
        self.bind("<ButtonRelease-1>", self._click)
        if tip:
            Tooltip(self, tip)

    def _click(self, event):
        if self.enabled and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.config(fg=self.colors["fg"] if on else UI["disabled"], cursor="hand2" if on else "")
        if not on:
            self.config(bg=self.colors["bg"])


class Tooltip:
    """A small hint that appears when the mouse rests on a widget."""
    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, event):
        self.job = self.widget.after(600, self._show)

    def _show(self):
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#2c2c2c", fg="#ffffff", font=UI["small"], padx=8, pady=4).pack()

    def _hide(self, event=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ToolkitApp:
    def __init__(self, root):
        self.root = root
        root.title("PDF Toolkit")
        root.minsize(980, 620)
        root.configure(bg=UI["canvas"])
        self.missing = load_libraries()
        self.docs = {}  # id -> Doc
        self.next_doc = 1
        self.pages = []  # the workspace: dicts (uid, doc, index, rot)
        self.next_uid = 1
        self.selected = set()  # uids
        self.anchor = None  # uid for shift-click ranges
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.busy = False
        self.cancelled = False
        self.thumbs = {}  # (doc, index) -> PIL image (unrotated, RENDER_PX at most)
        self.photos = {}  # (doc, index, rot, size) -> PhotoImage on screen
        self.items = {}  # uid -> canvas item ids
        self.drag = None
        self.zoom = tk.IntVar(value=150)  # thumbnail size in pixels
        self.ui_queue = queue.Queue()
        self.render_queue = []  # (doc, index) waiting to be drawn, most wanted first
        self.render_cv = threading.Condition()
        self._pump_ui()
        threading.Thread(target=self._render_worker, daemon=True).start()
        root.report_callback_exception = self._report_error
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        home = os.path.expanduser("~")
        docs_dir = os.path.join(home, "Documents")
        self.v = {
            "compress": tk.StringVar(value=list(COMPRESSION)[0]),
            "numbers": tk.BooleanVar(value=False),
            "number_fmt": tk.StringVar(value=NUMBER_FORMATS[2]),
            "number_place": tk.StringVar(value=NUMBER_PLACES[0]),
            "watermark": tk.BooleanVar(value=False),
            "watermark_text": tk.StringVar(value="CONFIDENTIAL"),
            "protect": tk.BooleanVar(value=False),
            "password": tk.StringVar(value=""),
            "image_size": tk.StringVar(value=IMAGE_PAGE_SIZES[0]),
            "last_dir": tk.StringVar(value=docs_dir if os.path.isdir(docs_dir) else home),
        }
        self._style()
        self._build_ui()
        for name in ("numbers", "watermark", "protect"):
            self.v[name].trace_add("write", lambda *_: self._on_options())
        self._on_options()
        self.dnd = enable_drag_and_drop(root, [self.canvas, self.empty], self._on_drop)
        self._redraw()
        if self._required_missing():
            self.root.after(200, self.offer_install)

    # ---- dependency installer ------------------------------------------ #
    def _required_missing(self):
        return [m for m in self.missing if m != "tkinterdnd2"]

    def offer_install(self):
        if getattr(sys, "frozen", False):
            messagebox.showerror("Missing components", "Missing: " + ", ".join(self.missing) +
                                 "\n\nThis copy of PDF Toolkit is incomplete. Please download it again.")
            return
        if not messagebox.askyesno(
                "Install components?",
                f"PDF Toolkit needs: {', '.join(self.missing)}\n\nInstall now? (about 25 MB download)\n\n"
                f"This runs pip for the Python running this app:\n{sys.executable}"):
            self._status("Components missing — nothing can be opened until they're installed.")
            return
        self._set_busy(True, cancellable=False)
        self._status("Installing… this can take a minute.")
        packages = list(self.missing)

        def work():
            try:
                ok, tail = install_requirements(packages)
            except Exception as e:
                ok, tail = False, [str(e)]
            self.ui(lambda: self._install_done(ok, tail))
        threading.Thread(target=work, daemon=True).start()

    def _install_done(self, ok, tail):
        self._set_busy(False)
        self.missing = load_libraries()
        if ok and not self._required_missing():
            if not self.dnd:
                self.dnd = enable_drag_and_drop(self.root, [self.canvas, self.empty], self._on_drop)
            self._redraw()
            self._status("Installed! Ready.")
        else:
            self._status("Install failed — see the error message.")
            messagebox.showerror("Install failed", "Couldn't install automatically:\n\n" + "\n".join(tail) +
                                 "\n\nTry running this in a terminal:\n"
                                 f'"{sys.executable}" -m pip install ' + " ".join(PACKAGES))

    # ---- layout --------------------------------------------------------- #
    def _style(self):
        s = ttk.Style()
        for name in ("Side.TCheckbutton", "Side.TLabel", "Side.TRadiobutton"):
            s.configure(name, background=UI["panel"], foreground=UI["text"], font=UI["font"])
        s.configure("Hint.TLabel", background=UI["panel"], foreground=UI["muted"], font=UI["small"])
        s.configure("Status.TLabel", background=UI["statusbar"], foreground=UI["muted"], font=UI["small"])
        s.configure("Zoom.Horizontal.TScale", background=UI["toolbar"])
        s.configure("Accent.Horizontal.TProgressbar", background=UI["accent"])

    def _build_ui(self):
        root = self.root
        # App bar: name, open file, the main action.
        bar = tk.Frame(root, bg=UI["appbar"], height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        mark = tk.Canvas(bar, width=26, height=30, bg=UI["appbar"], highlightthickness=0)
        mark.create_polygon(3, 2, 17, 2, 24, 9, 24, 28, 3, 28, fill="#ffffff", outline="")
        mark.create_polygon(17, 2, 17, 9, 24, 9, fill="#c9d6ea", outline="")
        for i, yy in enumerate((14, 18, 22)):
            mark.create_line(7, yy, 20 if i < 2 else 15, yy, fill=UI["accent"], width=2)
        mark.pack(side="left", padx=(14, 8))
        tk.Label(bar, text="PDF Toolkit", bg=UI["appbar"], fg="#ffffff", font=UI["title"]).pack(side="left")
        self.doc_lbl = tk.Label(bar, text="", bg=UI["appbar"], fg="#b8b8b8", font=UI["font"])
        self.doc_lbl.pack(side="left", padx=(14, 0))
        self.save_btn = FlatButton(bar, "  Save as PDF  ", self.save_all, UI["appbar"], primary=True,
                                   font=UI["bold"], pady=6, tip="Save every page in the workspace as one PDF (Ctrl+S)")
        self.save_btn.pack(side="right", padx=14, pady=8)
        self.open_btn = FlatButton(bar, "＋  Add files", self.add_files, UI["appbar"], fg="#ffffff",
                                   hover="#4a4a4a", pady=6, tip="Add PDFs or images (or drag them in)")
        self.open_btn.pack(side="right", pady=8)

        body = tk.Frame(root, bg=UI["canvas"])
        body.pack(fill="both", expand=True)

        # Left: the tools panel.
        side = tk.Frame(body, bg=UI["panel"], width=292, highlightthickness=1, highlightbackground=UI["border"])
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Tools", bg=UI["panel"], fg=UI["text"], font=UI["heading"], anchor="w").pack(
            fill="x", padx=16, pady=(14, 4))
        tk.Label(side, text="These apply when you save, split or extract.", bg=UI["panel"], fg=UI["muted"],
                 font=UI["small"], anchor="w").pack(fill="x", padx=16)

        def card(title, icon):
            f = tk.Frame(side, bg=UI["panel"])
            f.pack(fill="x", padx=16, pady=(14, 0))
            tk.Label(f, text=f"{icon}  {title}", bg=UI["panel"], fg=UI["text"], font=UI["bold"], anchor="w").pack(
                fill="x", pady=(0, 4))
            inner = tk.Frame(f, bg=UI["panel"])
            inner.pack(fill="x", padx=(22, 0))
            return inner

        f = card("Compress", "🗜")
        ttk.Combobox(f, values=list(COMPRESSION), textvariable=self.v["compress"], state="readonly",
                     width=29).pack(anchor="w")
        f = card("Page numbers", "#")
        ttk.Checkbutton(f, text="Add page numbers", variable=self.v["numbers"], style="Side.TCheckbutton").pack(anchor="w")
        row = tk.Frame(f, bg=UI["panel"])
        row.pack(anchor="w", pady=(2, 0))
        self.num_cb = ttk.Combobox(row, values=NUMBER_FORMATS, textvariable=self.v["number_fmt"],
                                   state="readonly", width=11)
        self.num_cb.pack(side="left")
        self.place_cb = ttk.Combobox(row, values=NUMBER_PLACES, textvariable=self.v["number_place"],
                                     state="readonly", width=13)
        self.place_cb.pack(side="left", padx=(6, 0))
        f = card("Watermark", "💧")
        ttk.Checkbutton(f, text="Add a watermark", variable=self.v["watermark"], style="Side.TCheckbutton").pack(anchor="w")
        self.wm_e = ttk.Entry(f, textvariable=self.v["watermark_text"], width=31)
        self.wm_e.pack(anchor="w", pady=(2, 0))
        f = card("Protect", "🔒")
        ttk.Checkbutton(f, text="Require a password to open", variable=self.v["protect"],
                        style="Side.TCheckbutton").pack(anchor="w")
        self.pw_e = ttk.Entry(f, textvariable=self.v["password"], width=31, show="•")
        self.pw_e.pack(anchor="w", pady=(2, 0))
        ttk.Label(f, text="Off = saved without a password (this also\nremoves one from a PDF you opened).",
                  style="Hint.TLabel").pack(anchor="w", pady=(2, 0))
        f = card("Images to PDF", "📥")
        ttk.Label(f, text="Added images become pages:", style="Side.TLabel").pack(anchor="w")
        ttk.Combobox(f, values=IMAGE_PAGE_SIZES, textvariable=self.v["image_size"], state="readonly",
                     width=29).pack(anchor="w", pady=(2, 0))

        tk.Frame(side, bg=UI["border"], height=1).pack(fill="x", padx=16, pady=(16, 8))
        self.action_btns = []
        for text, cmd, tip in (("✂  Split into several PDFs…", self.split, "Every page, every N pages, or at chosen pages"),
                               ("📄  Save selected pages…", self.extract, "Save only the selected pages as a new PDF"),
                               ("🖼  Export pages as images…", self.export_images, "PNG or JPG, at the resolution you choose")):
            btn = FlatButton(side, text, cmd, UI["panel"], hover=UI["hover"], anchor="w", tip=tip)
            btn.pack(fill="x", padx=10)
            self.action_btns.append(btn)
        self.extract_btn = self.action_btns[1]

        # Right: toolbar + page grid + status bar.
        main = tk.Frame(body, bg=UI["canvas"])
        main.pack(side="left", fill="both", expand=True)
        tools = tk.Frame(main, bg=UI["toolbar"], height=44, highlightthickness=1, highlightbackground=UI["border"])
        tools.pack(fill="x")
        tools.pack_propagate(False)
        tk.Label(tools, text="Organize pages", bg=UI["toolbar"], fg=UI["text"], font=UI["bold"]).pack(
            side="left", padx=(14, 12))
        self.tool_btns = []
        for text, cmd, tip in (("⟲", lambda: self.rotate(-90), "Rotate left"),
                               ("⟳", lambda: self.rotate(90), "Rotate right"),
                               ("⧉", self.duplicate, "Duplicate (Ctrl+D)"),
                               ("🗑", self.delete, "Delete (Del)"),
                               ("|", None, None),
                               ("↶", self.undo, "Undo (Ctrl+Z)"),
                               ("↷", self.redo, "Redo (Ctrl+Y)")):
            if cmd is None:
                tk.Frame(tools, bg=UI["border"], width=1).pack(side="left", fill="y", padx=6, pady=9)
                continue
            btn = FlatButton(tools, text, cmd, UI["toolbar"], font=UI["icon"], padx=9, pady=2, tip=tip)
            btn.pack(side="left", padx=1)
            self.tool_btns.append(btn)
        self.undo_btn, self.redo_btn = self.tool_btns[-2], self.tool_btns[-1]
        self.clear_btn = FlatButton(tools, "Clear all", self.clear_all, UI["toolbar"], tip="Empty the workspace")
        self.clear_btn.pack(side="right", padx=(0, 10))
        tk.Label(tools, text="🔍", bg=UI["toolbar"], fg=UI["muted"], font=UI["font"]).pack(side="right", padx=(10, 0))
        ttk.Scale(tools, from_=90, to=260, variable=self.zoom, orient="horizontal", length=110,
                  style="Zoom.Horizontal.TScale", command=lambda v: self._on_zoom()).pack(side="right")
        tk.Label(tools, text="Page size", bg=UI["toolbar"], fg=UI["muted"], font=UI["small"]).pack(side="right", padx=6)

        status = tk.Frame(main, bg=UI["statusbar"], height=30, highlightthickness=1, highlightbackground=UI["border"])
        status.pack(side="bottom", fill="x")
        status.pack_propagate(False)
        self.status = ttk.Label(status, text="Ready.", style="Status.TLabel", anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=12)
        self.cancel_btn = FlatButton(status, "Cancel", self.cancel, UI["statusbar"], pady=1)
        self.cancel_btn.pack(side="right", padx=(4, 8))
        self.cancel_btn.set_enabled(False)
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=1000, length=160,
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.pack(side="right", pady=8)

        grid = tk.Frame(main, bg=UI["canvas"])
        grid.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(grid, bg=UI["canvas"], highlightthickness=0, takefocus=1)
        sb = ttk.Scrollbar(grid, orient="vertical", command=self._yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.empty = tk.Frame(self.canvas, bg=UI["canvas"])
        drop = tk.Canvas(self.empty, width=380, height=200, bg=UI["canvas"], highlightthickness=0)
        drop.create_rectangle(4, 4, 376, 196, outline="#a9b1bd", dash=(6, 4), width=2)
        drop.create_text(190, 70, text="⇪", font=(UI["family"], 30), fill=UI["accent"])
        self.empty_text = drop.create_text(190, 125, text="", font=UI["heading"], fill=UI["text"])
        drop.create_text(190, 156, text="PDFs and images (JPG, PNG, HEIC, WebP, TIFF)", font=UI["small"],
                         fill=UI["muted"])
        drop.pack()
        self.empty_canvas = drop
        tk.Label(self.empty, text="Then drag pages to reorder them, select pages to rotate, duplicate or delete,\n"
                                  "and save the whole document, part of it, or each page as an image.",
                 bg=UI["canvas"], fg=UI["muted"], font=UI["font"], justify="center").pack(pady=(14, 8))
        FlatButton(self.empty, "  Choose files  ", self.add_files, UI["canvas"], primary=True,
                   font=UI["bold"], pady=6).pack()

        c = self.canvas
        c.bind("<Configure>", lambda e: self._redraw())
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_motion)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<Button-2>" if sys.platform == "darwin" else "<Button-3>", self._on_menu)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            c.bind(seq, self._on_wheel)
        mod = "Command" if sys.platform == "darwin" else "Control"
        for seq, fn in (("<Delete>", self.delete), (f"<{mod}-a>", self.select_all), (f"<{mod}-z>", self.undo),
                        (f"<{mod}-y>", self.redo), (f"<{mod}-Z>", self.redo), (f"<{mod}-d>", self.duplicate),
                        (f"<{mod}-s>", self.save_all), ("<Escape>", self.select_none)):
            # Only when the page grid has focus, so typing in a box (a password) isn't taken over.
            c.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        root.bind(f"<{mod}-s>", lambda e: (self.save_all(), "break")[1])
        self.menu = tk.Menu(root, tearoff=0)
        for label, cmd in (("Rotate left", lambda: self.rotate(-90)), ("Rotate right", lambda: self.rotate(90)),
                           ("Duplicate", self.duplicate), ("Delete", self.delete), (None, None),
                           ("View larger", self.view_selected), ("Save selected pages as PDF…", self.extract),
                           ("Export selected pages as images…", lambda: self.export_images(selected_only=True)),
                           (None, None), ("Select all", self.select_all)):
            if label:
                self.menu.add_command(label=label, command=cmd)
            else:
                self.menu.add_separator()

    def _on_options(self):
        self.num_cb.config(state="readonly" if self.v["numbers"].get() else "disabled")
        self.place_cb.config(state="readonly" if self.v["numbers"].get() else "disabled")
        self.wm_e.config(state="normal" if self.v["watermark"].get() else "disabled")
        self.pw_e.config(state="normal" if self.v["protect"].get() else "disabled")

    def _status(self, text, error=False):
        self.status.config(text=text, foreground=UI["error"] if error else UI["muted"])

    def _on_zoom(self):
        size = int(self.zoom.get())
        if getattr(self, "_shown_zoom", None) != size:
            self._shown_zoom = size
            self.photos.clear()
            self._redraw()

    # ---- thread plumbing ----------------------------------------------- #
    def ui(self, fn):
        """Run fn on the main (UI) thread. Safe to call from any thread.

        Tkinter isn't thread-safe: touching widgets, or even calling
        root.after(), from a worker thread works on some systems and silently
        does nothing on others. So workers only put callbacks in a queue, and
        the UI thread drains it every 50 ms in _pump_ui().
        """
        self.ui_queue.put(fn)

    def _pump_ui(self):
        # Reschedule FIRST: if a queued callback raises, the pump must keep
        # running, or every later message (status, progress, errors) is lost.
        self.root.after(50, self._pump_ui)
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                self._report_error(*sys.exc_info())

    # ---- thumbnails ----------------------------------------------------- #
    def _render_worker(self):
        """Background thread: draws page thumbnails, the visible ones first."""
        while True:
            with self.render_cv:
                while not self.render_queue:
                    self.render_cv.wait()
                key = self.render_queue.pop(0)
            if key in self.thumbs or key[0] not in self.docs:
                continue
            doc = self.docs[key[0]]
            try:
                with PDFIUM_LOCK:
                    if doc.render is None:
                        doc.render = pdfium.PdfDocument(doc.data, password=doc.password or None)
                    page = doc.render[key[1]]
                    w, h = page.get_size()  # already turned by the page's own rotation
                    scale = RENDER_PX / max(w, h, 1)
                    im = page.render(scale=scale, may_draw_forms=True).to_pil()
                    page.close()
                im = im.convert("RGB")
            except Exception as e:
                log_error(f"Thumbnail {doc.name} p{key[1] + 1}: {type(e).__name__}: {e}")
                im = Image.new("RGB", (int(RENDER_PX * 0.77), RENDER_PX), (250, 230, 230))
            self.thumbs[key] = im
            self.ui(lambda k=key: self._thumb_ready(k))

    def _want(self, keys):
        """Put thumbnails at the front of the render queue."""
        keys = [k for k in keys if k not in self.thumbs]
        if not keys:
            return
        with self.render_cv:
            wanted = set(keys)
            self.render_queue = keys + [k for k in self.render_queue if k not in wanted]
            self.render_cv.notify()

    def _photo(self, page):
        key = (page["doc"], page["index"])
        im = self.thumbs.get(key)
        if im is None:
            return None
        size = int(self.zoom.get())
        pkey = key + (page["rot"], size)
        if pkey not in self.photos:
            shown = im.rotate(-page["rot"], expand=True) if page["rot"] else im  # PIL turns anticlockwise
            shown = shown.copy()
            shown.thumbnail((size, size), Image.Resampling.LANCZOS)
            self.photos[pkey] = ImageTk.PhotoImage(shown)
        return self.photos[pkey]

    def _thumb_ready(self, key):
        for page in self.pages:
            if (page["doc"], page["index"]) == key and page["uid"] in self.items:
                self._draw_thumb(page)

    # ---- drawing the grid ---------------------------------------------- #
    def _metrics(self):
        t = int(self.zoom.get())
        return t, t + 44, t + 62  # thumb box, cell width, cell height

    def _cols(self):
        _, cw, _ = self._metrics()
        return max(1, (self.canvas.winfo_width() - PAD) // cw)

    def _cell(self, k):
        _, cw, ch = self._metrics()
        cols = self._cols()
        x0 = max(PAD, (self.canvas.winfo_width() - cols * cw) // 2)  # centre the grid
        return x0 + (k % cols) * cw, PAD + (k // cols) * ch

    def _redraw(self):
        """Draw every page's frame and number (cheap); thumbnails are added for the
        visible rows in _fill_visible, so even a 1,000-page workspace stays fast."""
        c = self.canvas
        c.delete("all")
        self.items.clear()
        n = len(self.pages)
        if not n:
            self.empty_canvas.itemconfig(self.empty_text, text="Drop files here to start" if getattr(self, "dnd", False)
                                         else "Add files to start")
            self.empty.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.empty.place_forget()
        t, cw, ch = self._metrics()
        rows = (n + self._cols() - 1) // self._cols()
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(c.winfo_height(), PAD * 2 + rows * ch)))
        for k, page in enumerate(self.pages):
            x, y = self._cell(k)
            sel = page["uid"] in self.selected
            bg = c.create_rectangle(x + 4, y, x + cw - 4, y + ch - 6, width=2, fill=UI["sel_bg"] if sel else "",
                                    outline=UI["accent"] if sel else "")
            num = c.create_text(x + cw / 2, y + t + 26, text=str(k + 1), font=UI["bold"], fill=UI["text"])
            doc = self.docs[page["doc"]]
            name = doc.name if len(doc.name) <= 24 else doc.name[:21] + "…"
            sub = c.create_text(x + cw / 2, y + t + 44, fill=UI["muted"], font=UI["tiny"],
                                text=f"{name} · p{page['index'] + 1}" if doc.pages > 1 else name)
            self.items[page["uid"]] = dict(bg=bg, num=num, sub=sub, img=None, extra=[], k=k)
        self._fill_visible()
        self._update_title()

    def _visible_range(self):
        c = self.canvas
        _, _, ch = self._metrics()
        top, bottom = c.canvasy(0), c.canvasy(c.winfo_height())
        cols = self._cols()
        first = max(0, int((top - PAD) // ch) * cols)
        last = min(len(self.pages), (int((bottom - PAD) // ch) + 2) * cols)
        return first, last

    def _fill_visible(self):
        first, last = self._visible_range()
        want = []
        for page in self.pages[first:last]:
            it = self.items.get(page["uid"])
            if it and it["img"] is None:
                if not self._draw_thumb(page):
                    want.append((page["doc"], page["index"]))
        # The visible ones first, then the next screenful.
        nxt = [(p["doc"], p["index"]) for p in self.pages[last:last + (last - first)]]
        self._want(want + nxt)

    def _draw_thumb(self, page):
        it = self.items.get(page["uid"])
        if it is None:
            return False
        t, cw, _ = self._metrics()
        photo = self._photo(page)
        x, y = self._cell(it["k"])
        cx, cy = x + cw / 2, y + 10 + t / 2
        c = self.canvas
        for item in it["extra"]:
            c.delete(item)
        it["extra"] = []
        if it["img"] is not None:
            c.delete(it["img"])
        if photo is None:
            pw, ph = t * 0.72, t
            it["img"] = c.create_rectangle(cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2, fill="#ffffff",
                                           outline=UI["border"])
            return False
        pw, ph = photo.width(), photo.height()
        # A soft drop shadow under the page, like paper on a desk.
        for off, col in ((4, "#c9ccd1"), (2, "#b7bbc2")):
            it["extra"].append(c.create_rectangle(cx - pw / 2 + off, cy - ph / 2 + off, cx + pw / 2 + off,
                                                  cy + ph / 2 + off, fill=col, outline=""))
        it["img"] = c.create_image(cx, cy, image=photo)
        it["extra"].append(c.create_rectangle(cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2,
                                              outline="#9aa1ab"))
        return True

    def _yview(self, *args):
        self.canvas.yview(*args)
        self._fill_visible()

    def _on_wheel(self, event):
        if sys.platform == "darwin":
            step = -event.delta
        elif event.num == 4:
            step = -2
        elif event.num == 5:
            step = 2
        else:
            step = -2 if event.delta > 0 else 2
        self.canvas.yview_scroll(int(step), "units")
        self._fill_visible()

    def _update_title(self):
        n = len(self.pages)
        names = sorted({self.docs[p["doc"]].name for p in self.pages})
        label = ("" if not names else names[0] if len(names) == 1 else f"{len(names)} files")
        self.doc_lbl.config(text=(f"{label}  —  {n} page{'s' * (n != 1)}" + ("  •  not saved" if self.dirty else ""))
                            if n else "")
        self.root.title(f"PDF Toolkit — {label}" if n else "PDF Toolkit")
        idle = not self.busy
        for btn in self.tool_btns + [self.open_btn, self.clear_btn]:
            btn.set_enabled(idle)
        self.undo_btn.set_enabled(bool(self.undo_stack) and idle)
        self.redo_btn.set_enabled(bool(self.redo_stack) and idle)
        for btn in self.action_btns + [self.save_btn]:
            btn.set_enabled(bool(n) and idle)
        self.extract_btn.set_enabled(bool(self.selected) and idle)

    # ---- selection and dragging ---------------------------------------- #
    def _index_at(self, x, y):
        """Index of the page under canvas point (x, y), or None."""
        _, cw, ch = self._metrics()
        cols = self._cols()
        x0, _ = self._cell(0)
        col, row = int((x - x0) // cw), int((y - PAD) // ch)
        if x < x0 or y < PAD or col >= cols:
            return None
        k = row * cols + col
        return k if 0 <= k < len(self.pages) else None

    def _slot_at(self, x, y):
        """Insertion slot (0..n) nearest canvas point (x, y)."""
        _, cw, ch = self._metrics()
        cols = self._cols()
        x0, _ = self._cell(0)
        row = max(0, int((y - PAD) // ch))
        col = min(cols, max(0, int(round((x - x0) / cw))))
        return max(0, min(len(self.pages), row * cols + col))

    def _on_press(self, event):
        self.canvas.focus_set()
        if self.busy:
            return
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        k = self._index_at(x, y)
        ctrl = event.state & (0x0004 | 0x0008) if sys.platform == "darwin" else event.state & 0x0004
        shift = event.state & 0x0001
        if k is None:
            if not (ctrl or shift):
                self.select_none()
            self.drag = None
            return
        uid = self.pages[k]["uid"]
        if shift and self.anchor is not None:
            ks = [i for i, p in enumerate(self.pages) if p["uid"] == self.anchor]
            a = ks[0] if ks else k
            self.selected = {p["uid"] for p in self.pages[min(a, k):max(a, k) + 1]}
        elif ctrl:
            self.selected ^= {uid}
            self.anchor = uid
        elif uid not in self.selected:
            self.selected = {uid}
            self.anchor = uid
        self.drag = dict(x=x, y=y, k=k, uid=uid, moving=False, line=None, plain=not (ctrl or shift))
        self._refresh_selection()

    def _on_motion(self, event):
        if not self.drag or self.busy:
            return
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        d = self.drag
        if not d["moving"] and abs(x - d["x"]) + abs(y - d["y"]) < 8:
            return
        d["moving"] = True
        # Scroll when dragging near the top or bottom edge.
        if event.y < 30:
            self.canvas.yview_scroll(-1, "units")
            self._fill_visible()
        elif event.y > self.canvas.winfo_height() - 30:
            self.canvas.yview_scroll(1, "units")
            self._fill_visible()
        slot = self._slot_at(x, y)
        d["slot"] = slot
        if d["line"]:
            self.canvas.delete(d["line"])
        _, cw, ch = self._metrics()
        cols = self._cols()
        if slot and slot % cols == 0:  # after the last page of a row: show it at that row's end
            lx, ly = self._cell(slot - 1)
            lx += cw
        else:
            lx, ly = self._cell(slot)
        d["line"] = self.canvas.create_line(lx, ly + 4, lx, ly + ch - 10, fill=UI["accent"], width=4)
        self.canvas.config(cursor="fleur")

    def _on_release(self, event):
        d, self.drag = self.drag, None
        self.canvas.config(cursor="")
        if not d:
            return
        if d["line"]:
            self.canvas.delete(d["line"])
        if d["moving"] and "slot" in d:
            self._move_selected(d["slot"])
        elif d["plain"]:  # a plain click on an already-selected page selects just it
            self.selected = {d["uid"]}
            self.anchor = d["uid"]
            self._refresh_selection()

    def _move_selected(self, slot):
        moving = [p for p in self.pages if p["uid"] in self.selected]
        if not moving:
            return
        before = sum(1 for p in self.pages[:slot] if p["uid"] in self.selected)
        rest = [p for p in self.pages if p["uid"] not in self.selected]
        new = rest[:slot - before] + moving + rest[slot - before:]
        if [p["uid"] for p in new] == [p["uid"] for p in self.pages]:
            return
        self._change(new, f"Moved {len(moving)} page{'s' * (len(moving) != 1)}.")

    def _refresh_selection(self):
        for page in self.pages:
            it = self.items.get(page["uid"])
            if it:
                sel = page["uid"] in self.selected
                self.canvas.itemconfig(it["bg"], fill=UI["sel_bg"] if sel else "",
                                       outline=UI["accent"] if sel else "")
        n = len(self.selected)
        if n:
            self._status(f"{n} page{'s' * (n != 1)} selected — drag to move, right-click for more.")
        self._update_title()

    def _on_double(self, event):
        k = self._index_at(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if k is not None:
            self.selected = {self.pages[k]["uid"]}
            self._refresh_selection()
            self.view_selected()

    def _on_menu(self, event):
        if self.busy:
            return
        k = self._index_at(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if k is None:
            return
        if self.pages[k]["uid"] not in self.selected:
            self.selected = {self.pages[k]["uid"]}
            self._refresh_selection()
        self.menu.tk_popup(event.x_root, event.y_root)

    def select_all(self):
        self.selected = {p["uid"] for p in self.pages}
        self._refresh_selection()

    def select_none(self):
        self.selected = set()
        self._refresh_selection()
        self._status(f"{len(self.pages)} pages." if self.pages else "Ready.")

    # ---- editing (with undo) -------------------------------------------- #
    def _change(self, new_pages, message):
        self.undo_stack.append((list(self.pages), set(self.selected)))
        del self.undo_stack[:-200]
        self.redo_stack.clear()
        self.pages = new_pages
        self.selected &= {p["uid"] for p in self.pages}
        self.dirty = True
        self._redraw()
        self._status(message)

    def _targets(self):
        sel = [p for p in self.pages if p["uid"] in self.selected]
        if not sel:
            self._status("Select one or more pages first (click; Ctrl+click or Shift+click for several).")
        return sel

    def rotate(self, angle):
        if self.busy or not self._targets():
            return
        new = [dict(p, rot=(p["rot"] + angle) % 360) if p["uid"] in self.selected else p for p in self.pages]
        n = len(self.selected)
        self._change(new, f"Rotated {n} page{'s' * (n != 1)} {'left' if angle < 0 else 'right'}.")

    def duplicate(self):
        if self.busy or not self._targets():
            return
        new, copies = [], set()
        for p in self.pages:
            new.append(p)
            if p["uid"] in self.selected:
                c = dict(p, uid=self.next_uid)
                self.next_uid += 1
                new.append(c)
                copies.add(c["uid"])
        n = len(copies)
        self._change(new, f"Duplicated {n} page{'s' * (n != 1)}.")
        self.selected = copies
        self._refresh_selection()

    def delete(self):
        if self.busy or not self._targets():
            return
        n = len(self.selected)
        self._change([p for p in self.pages if p["uid"] not in self.selected],
                     f"Deleted {n} page{'s' * (n != 1)}. Undo brings them back.")
        self.selected = set()
        self._update_title()

    def undo(self):
        if self.busy or not self.undo_stack:
            return
        self.redo_stack.append((list(self.pages), set(self.selected)))
        self.pages, self.selected = self.undo_stack.pop()
        self.dirty = True
        self._redraw()
        self._status("Undone.")

    def redo(self):
        if self.busy or not self.redo_stack:
            return
        self.undo_stack.append((list(self.pages), set(self.selected)))
        self.pages, self.selected = self.redo_stack.pop()
        self.dirty = True
        self._redraw()
        self._status("Redone.")

    def clear_all(self):
        if self.busy or not self.pages:
            return
        if self.dirty and not messagebox.askyesno("Clear all?", "Remove all pages from the workspace? "
                                                  "Changes that haven't been saved are lost."):
            return
        self.pages, self.selected, self.undo_stack, self.redo_stack = [], set(), [], []
        with PDFIUM_LOCK:
            for doc in self.docs.values():
                if doc.render is not None:
                    doc.render.close()
        self.docs.clear()
        self.thumbs.clear()
        self.photos.clear()
        self.dirty = False
        self._redraw()
        self._status("Ready.")

    # ---- adding files --------------------------------------------------- #
    def add_files(self):
        paths = filedialog.askopenfilenames(title="Add PDFs or images", filetypes=FILE_TYPES,
                                            initialdir=self.v["last_dir"].get())
        if paths:
            self.v["last_dir"].set(os.path.dirname(paths[0]))
            self._add(list(paths), self._insert_slot())

    def _insert_slot(self):
        """New pages go after the last selected page, or at the end."""
        ks = [k for k, p in enumerate(self.pages) if p["uid"] in self.selected]
        return ks[-1] + 1 if ks else len(self.pages)

    def _on_drop(self, paths, x_root, y_root):
        if self.busy:
            self._status("Wait for this to finish before adding files.")
            return
        c = self.canvas
        x, y = c.canvasx(x_root - c.winfo_rootx()), c.canvasy(y_root - c.winfo_rooty())
        slot = self._slot_at(x, y) if self.pages else 0
        files = []
        for p in paths:
            if os.path.isdir(p):  # a folder adds the PDFs and images directly inside it
                files += sorted(os.path.join(p, n) for n in os.listdir(p)
                                if os.path.splitext(n)[1].lower() in PDF_EXTS | IMAGE_EXTS)
            else:
                files.append(p)
        self._add(files, slot)

    def _add(self, paths, slot):
        if self._required_missing():
            self.offer_install()
            return
        if not paths:
            return
        size = self.v["image_size"].get()
        self._set_busy(True)
        self._status(f"Opening {len(paths)} file{'s' * (len(paths) != 1)}…")

        def work():
            opened, problems = [], []
            for k, path in enumerate(paths):
                if self.cancelled:
                    break
                ext = os.path.splitext(path)[1].lower()
                try:
                    if ext in PDF_EXTS:
                        with open(path, "rb") as f:
                            data = f.read()
                        try:
                            pages = open_pdf_bytes(data)
                            password = ""
                        except PasswordNeeded:
                            password, pages = self._ask_password(path, data)
                            if password is None:
                                problems.append(f"{os.path.basename(path)}: skipped (no password)")
                                continue
                    elif ext in IMAGE_EXTS:
                        data, password = image_to_pdf(path, size), ""
                        pages = open_pdf_bytes(data)
                    else:
                        problems.append(f"{os.path.basename(path)}: not a PDF or an image")
                        continue
                    opened.append(Doc(path, data, password, pages))
                except Exception as e:
                    log_error(f"Open {path}: {type(e).__name__}: {e}")
                    problems.append(f"{os.path.basename(path)}: {explain_error(e)}")
                self.ui(lambda k=k: self.progress.config(value=(k + 1) / len(paths) * 1000))
            self.ui(lambda: self._added(opened, problems, slot))
        threading.Thread(target=work, daemon=True).start()

    def _ask_password(self, path, data):
        """Ask (on the UI thread) for a PDF's password, up to 3 tries. Returns (password, pages) or (None, 0)."""
        for attempt in range(3):
            answer = {}
            done = threading.Event()

            def ask(attempt=attempt):
                msg = (f"{os.path.basename(path)} is password-protected.\n\nPassword:" if attempt == 0 else
                       "That password didn't work. Try again:")
                answer["pw"] = simpledialog.askstring("Password needed", msg, show="•", parent=self.root)
                done.set()
            self.ui(ask)
            done.wait()
            if answer.get("pw") is None:
                return None, 0
            try:
                return answer["pw"], open_pdf_bytes(data, answer["pw"])
            except PasswordNeeded:
                continue
        return None, 0

    def _added(self, docs, problems, slot):
        self._set_busy(False)
        new = []
        for doc in docs:
            did = self.next_doc
            self.next_doc += 1
            self.docs[did] = doc
            for i in range(doc.pages):
                new.append(dict(uid=self.next_uid, doc=did, index=i, rot=0))
                self.next_uid += 1
        if new:
            slot = min(slot, len(self.pages))
            n = len(new)
            self._change(self.pages[:slot] + new + self.pages[slot:],
                         f"Added {n} page{'s' * (n != 1)} from {len(docs)} file{'s' * (len(docs) != 1)}.")
            self.selected = {p["uid"] for p in new}
            self._refresh_selection()
            if len(self.pages) == len(new):
                self.dirty = False  # just opened: nothing to save yet
                self._update_title()
        if problems:
            self._status("Some files couldn't be added — see the message.", error=True)
            messagebox.showwarning("Some files weren't added", "\n".join(problems[:15]))
        elif not new:
            self._status("Nothing was added.")

    # ---- saving ----------------------------------------------------------- #
    def _options(self):
        """The 'When saving' options, checked. Returns a dict, or None after saying why not."""
        o = dict(compress=COMPRESSION[self.v["compress"].get()],
                 numbers=self.v["number_fmt"].get() if self.v["numbers"].get() else None,
                 place=self.v["number_place"].get(),
                 watermark=self.v["watermark_text"].get().strip() if self.v["watermark"].get() else None,
                 password=self.v["password"].get() if self.v["protect"].get() else None)
        if self.v["watermark"].get() and not o["watermark"]:
            messagebox.showwarning("Check settings", "Type the watermark text, or untick Watermark.")
            return None
        if o["password"] is not None and not o["password"]:
            messagebox.showwarning("Check settings", "Type a password, or untick Password.")
            return None
        return o

    def _suggest_name(self, pages, suffix):
        first = self.docs[pages[0]["doc"]]
        stem = os.path.splitext(first.name)[0]
        docs = {p["doc"] for p in pages}
        return f"{stem}{' (merged)' if len(docs) > 1 else suffix}.pdf"

    def _ask_save(self, pages, suffix):
        first = self.docs[pages[0]["doc"]]
        path = filedialog.asksaveasfilename(
            title="Save PDF", defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")],
            initialdir=os.path.dirname(first.path) or self.v["last_dir"].get(),
            initialfile=self._suggest_name(pages, suffix))
        if path:
            self.v["last_dir"].set(os.path.dirname(path))
        return path

    def save_all(self):
        if self.busy or not self.pages:
            return
        o = self._options()
        if o is None:
            return
        path = self._ask_save(self.pages, " (edited)")
        if path:
            self._run_save([(list(self.pages), path)], o, mark_clean=True)

    def extract(self):
        if self.busy:
            return
        pages = [p for p in self.pages if p["uid"] in self.selected]
        if not pages:
            self._status("Select the pages to save first.")
            return
        o = self._options()
        if o is None:
            return
        path = self._ask_save(pages, " (pages)")
        if path:
            self._run_save([(pages, path)], o)

    def split(self):
        if self.busy or not self.pages:
            return
        o = self._options()
        if o is None:
            return
        SplitDialog(self, o)

    def _build_pdf(self, pages, o, check_cancel, opened):
        """Assemble the pages into a new PDF and apply the options. Returns a pikepdf.Pdf."""
        out = pikepdf.new()
        forms = []
        for p in pages:
            check_cancel()
            src = opened.get(p["doc"])
            if src is None:
                doc = self.docs[p["doc"]]
                src = opened[p["doc"]] = pikepdf.open(io.BytesIO(doc.data), password=doc.password)
            spage = src.pages[p["index"]]
            # The page's own rotation (it may be inherited from the page tree), plus ours.
            base = int(inherited(spage.obj, "/Rotate", 0))
            out.pages.append(spage)
            out.pages[-1].obj.Rotate = (base + p["rot"]) % 360
            if "/AcroForm" in src.Root and src not in forms:
                forms.append(src)
        # Keep form fields working: their document-level list goes along too.
        for src in forms:
            try:
                af = src.Root.AcroForm
                if "/AcroForm" not in out.Root:
                    out.Root.AcroForm = out.copy_foreign(af)
                elif "/Fields" in af:
                    for field in af.Fields:
                        out.Root.AcroForm.Fields.append(out.copy_foreign(field))
            except Exception as e:
                log_error(f"Form fields: {type(e).__name__}: {e}")
        first = opened.get(pages[0]["doc"])
        if first is not None and "/Title" in first.docinfo and len({p["doc"] for p in pages}) == 1:
            out.docinfo["/Title"] = first.docinfo["/Title"]
        if o["compress"]:
            compress_images(out, o["compress"]["max_side"], o["compress"]["quality"], check_cancel)
        if o["numbers"] or o["watermark"]:
            stamp_pages(out, o["numbers"], o["place"], o["watermark"])
        return out

    def _run_save(self, jobs, o, mark_clean=False):
        """Save each (pages, path) job in the background."""
        names = [os.path.normcase(os.path.abspath(path)) for _, path in jobs]
        self._set_busy(True)
        self._status("Saving…")

        def work():
            opened, written, total_in = {}, [], 0
            try:
                for k, (pages, path) in enumerate(jobs):
                    self.ui(lambda k=k: (self.progress.config(value=k / len(jobs) * 1000),
                                         self._status(f"Saving {os.path.basename(jobs[k][1])}…")))
                    out = self._build_pdf(pages, o, self._check_cancel, opened)
                    self._check_cancel()
                    kw = dict(compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
                    if o["compress"]:
                        kw["recompress_flate"] = True
                    if o["password"]:
                        kw["encryption"] = pikepdf.Encryption(user=o["password"], owner=o["password"], R=6)
                    part = path + ".part"
                    try:
                        out.save(part, **kw)
                        out.close()
                        os.replace(part, path)  # Cancel or a crash never leaves half a file
                    finally:
                        if os.path.exists(part):
                            os.remove(part)
                    written.append(path)
                self.ui(lambda: self._saved(written, jobs, mark_clean))
            except Cancelled:
                self.ui(lambda: self._save_failed("Cancelled." + (f" {len(written)} file(s) were saved." if written else "")))
            except Exception as e:
                log_error(f"Save: {type(e).__name__}: {e}")
                self.ui(lambda e=e: self._save_failed(f"Couldn't save: {explain_error(e)}", error=True))
            finally:
                for src in opened.values():
                    src.close()
        threading.Thread(target=work, daemon=True).start()

    def _saved(self, written, jobs, mark_clean):
        self._set_busy(False)
        if mark_clean:
            self.dirty = False
            self._update_title()
        size = sum(os.path.getsize(p) for p in written)
        pages = sum(len(pg) for pg, _ in jobs)
        what = os.path.basename(written[0]) if len(written) == 1 else f"{len(written)} files"
        self._status(f"Saved {what}: {pages} page{'s' * (pages != 1)}, {fmt_size(size)}.")
        reveal_file(written[0])

    def _save_failed(self, text, error=False):
        self._set_busy(False)
        self._status(text, error=error)
        if error:
            messagebox.showerror("Couldn't save", text)

    # ---- images --------------------------------------------------------- #
    def export_images(self, selected_only=False):
        if self.busy or not self.pages:
            return
        pages = [p for p in self.pages if p["uid"] in self.selected] if selected_only or self.selected else self.pages
        if not pages:
            return
        ExportDialog(self, pages)

    def _run_export(self, pages, folder, fmt, dpi):
        self._set_busy(True)
        first = self.docs[pages[0]["doc"]]
        stem = os.path.splitext(first.name)[0]

        def work():
            written = []
            try:
                for k, p in enumerate(pages):
                    self._check_cancel()
                    self.ui(lambda k=k: (self.progress.config(value=k / len(pages) * 1000),
                                         self._status(f"Exporting page {k + 1} of {len(pages)}…")))
                    doc = self.docs[p["doc"]]
                    with PDFIUM_LOCK:
                        if doc.render is None:
                            doc.render = pdfium.PdfDocument(doc.data, password=doc.password or None)
                        page = doc.render[p["index"]]
                        im = page.render(scale=dpi / 72, may_draw_forms=True,
                                         rotation=p["rot"] % 360).to_pil()
                        page.close()
                    name = f"{stem} page {k + 1:0{len(str(len(pages)))}d}.{fmt}"
                    path = os.path.join(folder, name)
                    part = path + ".part"
                    try:
                        if fmt == "jpg":
                            im.convert("RGB").save(part, "JPEG", quality=92, dpi=(dpi, dpi))
                        else:
                            im.save(part, "PNG", dpi=(dpi, dpi))
                        os.replace(part, path)
                    finally:
                        if os.path.exists(part):
                            os.remove(part)
                    written.append(path)
                self.ui(lambda: (self._set_busy(False), self._status(
                    f"Exported {len(written)} image{'s' * (len(written) != 1)} at {dpi} DPI."), reveal_file(written[0])))
            except Cancelled:
                self.ui(lambda: self._save_failed(f"Cancelled. {len(written)} image(s) were saved."))
            except Exception as e:
                log_error(f"Export: {type(e).__name__}: {e}")
                self.ui(lambda e=e: self._save_failed(f"Couldn't export: {explain_error(e)}", error=True))
        threading.Thread(target=work, daemon=True).start()

    def view_selected(self):
        pages = [p for p in self.pages if p["uid"] in self.selected]
        if pages:
            PageViewer(self, pages[0])

    # ---- busy state ------------------------------------------------------ #
    def _set_busy(self, busy, cancellable=True):
        self.busy = busy
        self.cancelled = False
        self.cancel_btn.set_enabled(busy and cancellable)
        self.progress.config(value=0)
        self._update_title()

    def cancel(self):
        self.cancelled = True

    def _check_cancel(self):
        if self.cancelled:
            raise Cancelled()

    def _report_error(self, exc, val, tb):
        import traceback
        self.busy = False
        detail = "".join(traceback.format_exception(exc, val, tb))
        log_error(detail)
        self._status(f"Unexpected error: {val}", error=True)
        messagebox.showerror("Unexpected error", detail[-1500:])

    def on_close(self):
        if self.dirty and self.pages and not messagebox.askyesno(
                "Quit without saving?", "The workspace has changes that haven't been saved. Quit anyway?"):
            return
        self.cancelled = True
        self.root.destroy()


class SplitDialog:
    """Split the workspace into several PDFs."""
    def __init__(self, app, options):
        self.app, self.options = app, options
        w = self.win = tk.Toplevel(app.root)
        w.title("Split into several PDFs")
        w.transient(app.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        self.mode = tk.StringVar(value="each")
        self.every = tk.StringVar(value="2")
        self.at = tk.StringVar(value="")
        n = len(app.pages)
        ttk.Label(f, text=f"Split the {n} pages in the workspace into separate PDF files:").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Radiobutton(f, text="One file per page", value="each", variable=self.mode).grid(
            row=1, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(f, text="A new file every", value="every", variable=self.mode).grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.every, width=5).grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="pages").grid(row=2, column=2, sticky="w")
        ttk.Radiobutton(f, text="Start new files at pages:", value="at", variable=self.mode).grid(
            row=3, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.at, width=18).grid(row=3, column=1, columnspan=2, sticky="w")
        ttk.Label(f, text="e.g. 4, 9 makes pages 1–3, 4–8 and 9 to the end", foreground="gray").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=(22, 0))
        multi = len({p["doc"] for p in app.pages}) > 1
        rb = ttk.Radiobutton(f, text="One file per original file", value="source", variable=self.mode)
        rb.grid(row=5, column=0, columnspan=3, sticky="w")
        if not multi:
            rb.config(state="disabled")
        btns = ttk.Frame(f)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="Choose folder and split…", command=self.go).pack(side="right", padx=(0, 6))
        w.grab_set()
        center_over(w, app.root)

    def groups(self):
        pages, mode, n = self.app.pages, self.mode.get(), len(self.app.pages)
        if mode == "each":
            return [[p] for p in pages]
        if mode == "every":
            k = int(self.every.get())
            if k < 1:
                raise ValueError
            return [pages[i:i + k] for i in range(0, n, k)]
        if mode == "at":
            starts = sorted({int(s) for s in self.at.get().replace(";", ",").split(",") if s.strip()})
            if not starts or starts[0] < 1 or starts[-1] > n:
                raise ValueError
            cuts = [0] + [s - 1 for s in starts if s > 1] + [n]
            return [pages[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]
        groups = []
        for p in pages:
            if groups and groups[-1][-1]["doc"] == p["doc"]:
                groups[-1].append(p)
            else:
                groups.append([p])
        return groups

    def go(self):
        try:
            groups = self.groups()
        except ValueError:
            messagebox.showwarning("Check settings", "Check the page numbers: whole numbers, within "
                                   f"1–{len(self.app.pages)}.", parent=self.win)
            return
        folder = filedialog.askdirectory(title="Save the split files in", parent=self.win,
                                         initialdir=self.app.v["last_dir"].get())
        if not folder:
            return
        app = self.app
        stem = os.path.splitext(app.docs[app.pages[0]["doc"]].name)[0]
        width = len(str(len(groups)))
        jobs, k = [], 1
        for g in groups:
            if self.mode.get() == "source":
                name = os.path.splitext(app.docs[g[0]["doc"]].name)[0] + " (split).pdf"
            else:
                first, last = k, k + len(g) - 1
                name = f"{stem} part {len(jobs) + 1:0{width}d} (p{first}" + (f"-{last}" if last > first else "") + ").pdf"
            k += len(g)
            jobs.append((g, os.path.join(folder, name)))
        # Same-named outputs (e.g. two sources called scan.pdf) get (2), (3)…
        seen = set()
        for i, (g, path) in enumerate(jobs):
            base, n = os.path.splitext(path)[0], 2
            while os.path.normcase(path) in seen:
                path = f"{base} ({n}).pdf"
                n += 1
            seen.add(os.path.normcase(path))
            jobs[i] = (g, path)
        existing = [p for _, p in jobs if os.path.exists(p)]
        if existing and not messagebox.askyesno(
                "Files already exist", f"{len(existing)} of the {len(jobs)} files already exist in that "
                f"folder (e.g. {os.path.basename(existing[0])}).\n\nReplace them?", parent=self.win):
            return
        self.win.destroy()
        app.v["last_dir"].set(folder)
        app._run_save(jobs, self.options)


class ExportDialog:
    """Export pages as PNG or JPG images."""
    def __init__(self, app, pages):
        self.app, self.pages = app, pages
        w = self.win = tk.Toplevel(app.root)
        w.title("Export as images")
        w.transient(app.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        n = len(pages)
        what = "the selected" if len(pages) < len(app.pages) else "all"
        ttk.Label(f, text=f"Export {what} {n} page{'s' * (n != 1)} as images.").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.fmt = tk.StringVar(value="PNG (sharp text)")
        self.dpi = tk.StringVar(value="150")
        ttk.Label(f, text="Format").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(f, values=["PNG (sharp text)", "JPG (smaller, for photos)"], textvariable=self.fmt,
                     state="readonly", width=24).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Resolution").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(f, values=["72", "150", "200", "300", "600"], textvariable=self.dpi, width=8).grid(
            row=2, column=1, sticky="w", pady=2)
        ttk.Label(f, text="DPI: 150 for screens, 300 for printing.", foreground="gray").grid(
            row=3, column=0, columnspan=2, sticky="w")
        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="Choose folder and export…", command=self.go).pack(side="right", padx=(0, 6))
        w.grab_set()
        center_over(w, app.root)

    def go(self):
        try:
            dpi = int(self.dpi.get())
            if not 30 <= dpi <= 1200:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Check settings", "The resolution must be a number from 30 to 1200.",
                                   parent=self.win)
            return
        folder = filedialog.askdirectory(title="Save the images in", parent=self.win,
                                         initialdir=self.app.v["last_dir"].get())
        if not folder:
            return
        self.win.destroy()
        self.app.v["last_dir"].set(folder)
        self.app._run_export(self.pages, folder, "jpg" if self.fmt.get().startswith("JPG") else "png", dpi)


class PageViewer:
    """One page, large, to read it before deciding where it goes."""
    def __init__(self, app, page):
        doc = app.docs[page["doc"]]
        w = tk.Toplevel(app.root)
        k = next((i for i, p in enumerate(app.pages) if p["uid"] == page["uid"]), 0)
        w.title(f"Page {k + 1} — {doc.name}, page {page['index'] + 1}")
        w.transient(app.root)
        side = max(400, min(app.root.winfo_screenheight() - 160, 1000))
        with PDFIUM_LOCK:
            if doc.render is None:
                doc.render = pdfium.PdfDocument(doc.data, password=doc.password or None)
            pg = doc.render[page["index"]]
            pw, ph = pg.get_size()
            if page["rot"] in (90, 270):
                pw, ph = ph, pw
            scale = side / max(pw, ph)
            im = pg.render(scale=scale, may_draw_forms=True, rotation=page["rot"]).to_pil()
            pg.close()
        self.photo = ImageTk.PhotoImage(im)
        tk.Label(w, image=self.photo, bg="#6b7280").pack(padx=0, pady=0)
        w.bind("<Escape>", lambda e: w.destroy())
        center_over(w, app.root)
        w.focus_set()


def center_over(win, root):
    """Open a window centred over the main one (it could otherwise land on
    another monitor, or off screen)."""
    win.withdraw()
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = root.winfo_rootx() + (root.winfo_width() - w) // 2
    y = max(root.winfo_rooty(), root.winfo_rooty() + (root.winfo_height() - h) // 2)
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.deiconify()
    win.lift()


LOG_PATH = os.path.join(tempfile.gettempdir(), "pdf_toolkit_errors.log")


def log_error(text):
    """Append an error to a log file. Errors can otherwise vanish when the
    script runs without a console (double-clicked / pythonw)."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{text}\n")
    except OSError:
        pass


def reveal_file(path):
    """Open the file's folder with the file selected."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass


def self_test():
    """Used by the release build: exit code 0 if the packaged app can turn an
    image into a PDF, merge, compress, stamp, password-protect and render, and
    load drag and drop."""
    try:
        if load_libraries():
            return 1
        tmp = tempfile.mkdtemp()
        img = os.path.join(tmp, "t.png")
        Image.new("RGB", (800, 600), (40, 90, 200)).save(img)
        a = pikepdf.open(io.BytesIO(image_to_pdf(img, "A4")))
        b = pikepdf.open(io.BytesIO(image_to_pdf(img, "Same size as the image")))
        out = pikepdf.new()
        out.pages.extend([a.pages[0], b.pages[0]])
        compress_images(out, 400, 60, lambda: None)
        stamp_pages(out, "Page 1 of 9", "Bottom centre", "TEST")
        buf = io.BytesIO()
        out.save(buf, encryption=pikepdf.Encryption(user="pw", owner="pw", R=6))
        doc = pdfium.PdfDocument(buf.getvalue(), password="pw")
        text = doc[0].get_textpage().get_text_range()
        if len(doc) != 2 or "Page 1 of 2" not in text:
            return 2
        root = tk.Tk()
        root.withdraw()
        from tkinterdnd2 import TkinterDnD
        TkinterDnD.require(root)
        root.destroy()
        return 0
    except Exception as e:
        log_error(f"Self-test failed: {type(e).__name__}: {e}")
        return 3


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    root = tk.Tk()
    try:  # nicer native look where available
        ttk.Style().theme_use("vista" if sys.platform == "win32" else
                              "aqua" if sys.platform == "darwin" else "clam")
    except tk.TclError:
        pass
    app = ToolkitApp(root)
    # Files given on the command line (e.g. "Open with") go straight in.
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if files:
        root.after(300, lambda: app._add(files, 0))
    root.mainloop()


if __name__ == "__main__":
    main()
