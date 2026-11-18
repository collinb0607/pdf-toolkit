#!/usr/bin/env python3
"""
PDF Toolkit - a cross-platform (Windows / macOS / Linux) desktop app for the
PDF jobs websites charge for: merge, reorder, rotate, delete, crop and extract
pages, split, compress, convert images to PDF and pages to images, edit the
pages' own text and images, view and search, comment and mark up, fill in
forms and sign (including certificate signatures), create fillable forms,
compare two PDFs, add page numbers, headers and footers, Bates numbers or a
watermark, and add or remove a password. Everything stays on your computer.

Pages are shown as thumbnails in one workspace: open PDFs and images, arrange
the pages, then save. Compression, page numbers, watermark and password are
options applied when saving.

Requirements:
    Python 3.8+ (with Tkinter, included in the python.org installers)
    pikepdf     -> reads, edits and writes PDFs (qpdf)
    pypdfium2   -> draws the page thumbnails and exports pages as images (PDFium)
    Pillow, pillow-heif -> images (pillow-heif opens iPhone HEIC photos)
    pyhanko     -> certificate (digital) signatures
    fonttools   -> uses this computer's fonts when editing a PDF's text
    tkinterdnd2 -> drag and drop from other apps (optional: the app works without it)
    The app offers to install them on first run. Manual install, using the Python that runs the app:
        python -m pip install pikepdf pypdfium2 pillow pillow-heif pyhanko fonttools tkinterdnd2

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
import tkinter.font as tkfont
import zlib
from tkinter import filedialog, messagebox, simpledialog, ttk

# Filled in by load_libraries(), so the window can still open (and offer to
# install them) when they're missing.
pikepdf = pdfium = Image = ImageOps = ImageTk = None

PACKAGES = ["pikepdf", "pypdfium2", "pillow", "pillow-heif", "pyhanko", "fonttools", "tkinterdnd2"]
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
HF_ALIGN = ["Left", "Centre", "Right"]
HF_TOKENS = "{page}, {pages}, {date}, {file}, {bates}"
PAPER = {"A4": (595.28, 841.89), "Letter": (612.0, 792.0)}
IMAGE_DPI = 150  # page size for images without (sensible) DPI information

# Thumbnail grid. Pages are drawn once at RENDER_PX and scaled to the zoom size.
RENDER_PX = 300
PAD = 16

# Look and feel (the Offline Toolkit's colours): a dark purple app bar, a light tools panel,
# pages on a soft lavender desk, blue actions.
_FAMILY = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
UI = dict(
    family=_FAMILY, font=(_FAMILY, 10), small=(_FAMILY, 9), tiny=(_FAMILY, 8), bold=(_FAMILY, 10, "bold"),
    heading=(_FAMILY, 13, "bold"), title=(_FAMILY, 12, "bold"), icon=(_FAMILY, 14),
    appbar="#2b1655", toolbar="#ffffff", panel="#ffffff", canvas="#ebe7f5", statusbar="#f7f5fc",
    border="#e2ddf0", hover="#f1ecfb", text="#1c1433", muted="#6e6787", disabled="#c9c4d8",
    accent="#2f7cf6", accent_dark="#1d67dc", sel_bg="#e7f0ff", error="#c8364f",
    # On the app bar: the open tab, a hovered button, separators, the search box, its text.
    bar_tab="#4a2a8a", bar_hover="#3d2470", bar_sep="#4d3a7a", bar_field="#3a2468",
    bar_text="#ddd5f5", bar_dim="#c4b8ea", bar_label="#d6ccf5",
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
        import pyhanko.sign  # noqa: F401
    except ImportError:
        missing.append("pyhanko")
    try:
        import fontTools.subset  # noqa: F401
    except ImportError:
        missing.append("fonttools")
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
    """Check a PDF opens (with the password, if any). Returns each page's (box, rotation).
    Raises PasswordNeeded for a missing or wrong password."""
    try:
        with pikepdf.open(io.BytesIO(data), password=password) as pdf:
            return page_boxes(pdf)
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


def stamp_pages(pdf, numbers=None, place="Bottom centre", watermark=None, header=None, footer=None,
                align="Centre", bates=None, first_number=0, file_stem=""):
    """Add page numbers, a header and footer, and/or a diagonal watermark, upright as
    each page is shown (whatever its rotation), in the standard Helvetica font (no font
    is embedded). In headers and footers {page} {pages} {date} {file} {bates} are filled
    in; first_number continues Bates numbers across split files."""
    import math
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    gs = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, ca=0.22, CA=0.22))
    total = len(pdf.pages)
    today = time.strftime("%d %b %Y").lstrip("0")
    for i, page in enumerate(pdf.pages):
        (x0, y0, x1, y1), rot = page_boxes_one(page)
        # The page as it's shown: width W, height H, and the matrix from those
        # "shown" coordinates to the page's own.
        W, H = shown_size((x0, y0, x1, y1), rot)
        cm = shown_matrix((x0, y0, x1, y1), rot)
        ops = [f"q {' '.join(f'{v:.4f}' for v in cm)} cm"]
        if watermark:
            size = min(W, H) / max(6, len(watermark) * 0.62)
            tw = text_width(watermark, size)
            c, s = math.cos(math.radians(35)), math.sin(math.radians(35))
            # centre the text on the page, turned 35°
            tx = W / 2 - (tw / 2) * c + (size * 0.35) * s
            ty = H / 2 - (tw / 2) * s - (size * 0.35) * c
            ops.append(f"q /TKGS gs 0.6 0 0 rg BT /TKHelv {size:.2f} Tf {c:.4f} {s:.4f} {-s:.4f} {c:.4f} "
                       f"{tx:.2f} {ty:.2f} Tm ")
            ops.append(_pdf_text(watermark).decode("latin-1") + " Tj ET Q")
        margin, size = 24, 10
        lines = []
        if numbers:
            text = (numbers.replace("9", str(total)).replace("1", str(i + 1), 1)
                    if numbers != "1" else str(i + 1))
            tw = text_width(text, size)
            tx = {"Bottom centre": W / 2 - tw / 2, "Bottom right": W - margin - tw, "Top right": W - margin - tw}[place]
            ty = H - margin - size if place == "Top right" else margin
            lines.append((text, tx, ty))
        for text, top in ((header, True), (footer, False)):
            if not text:
                continue
            b = f"{bates['prefix']}{bates['start'] + first_number + i:0{bates['digits']}d}" if bates else ""
            text = (text.replace("{page}", str(i + 1)).replace("{pages}", str(total)).replace("{date}", today)
                    .replace("{file}", file_stem).replace("{bates}", b))
            tw = text_width(text, size)
            tx = {"Left": margin, "Centre": W / 2 - tw / 2, "Right": W - margin - tw}[align]
            # Leave room for page numbers in the same corner.
            ty = (H - margin - size) if top else (margin + (14 if numbers and place != "Top right" and align ==
                                                             {"Bottom centre": "Centre", "Bottom right": "Right"}.get(place) else 0))
            lines.append((text, tx, ty))
        for text, tx, ty in lines:
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


def page_boxes_one(page):
    b = [float(v) for v in (inherited(page.obj, "/CropBox", None) or inherited(page.obj, "/MediaBox"))]
    return ((min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])),
            int(inherited(page.obj, "/Rotate", 0)) % 360)

# ---- page geometry ------------------------------------------------------------ #
# "Shown" coordinates are the page as you see it after its rotation: points,
# origin bottom-left. A page's own coordinates are what the PDF stores.
def shown_matrix(box, rot):
    """Matrix (a, b, c, d, e, f) from shown coordinates to the page's own, for a page
    whose visible box is `box` (x0, y0, x1, y1) and which is turned `rot` degrees clockwise."""
    x0, y0, x1, y1 = box
    return {0: (1, 0, 0, 1, x0, y0), 90: (0, 1, -1, 0, x1, y0),
            180: (-1, 0, 0, -1, x1, y1), 270: (0, -1, 1, 0, x0, y1)}[rot % 360]


def shown_size(box, rot):
    w, h = box[2] - box[0], box[3] - box[1]
    return (h, w) if rot % 180 else (w, h)


def to_page(box, rot, X, Y):
    a, b, c, d, e, f = shown_matrix(box, rot)
    return a * X + c * Y + e, b * X + d * Y + f


def to_shown(box, rot, x, y):
    a, b, c, d, e, f = shown_matrix(box, rot)
    x, y = x - e, y - f
    return a * x + b * y, c * x + d * y  # a rotation's inverse is its transpose


def page_boxes(pdf):
    """Each page's visible box (CropBox, else MediaBox) and own rotation."""
    out = []
    for page in pdf.pages:
        b = [float(v) for v in (inherited(page.obj, "/CropBox", None) or inherited(page.obj, "/MediaBox"))]
        out.append(((min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])),
                    int(inherited(page.obj, "/Rotate", 0)) % 360))
    return out


def blank_pdf(width, height):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(width, height))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


# ---- form fields -------------------------------------------------------------- #
def _full_name(field):
    parts, node = [], field
    for _ in range(32):
        if "/T" in node:
            parts.append(str(node.T))
        if "/Parent" not in node:
            break
        node = node.Parent
    return ".".join(reversed(parts))


def _field_of(widget):
    """The field a widget belongs to (the widget itself when they're merged)."""
    node = widget
    for _ in range(32):
        if "/FT" in node or "/Parent" not in node:
            return node
        node = node.Parent
    return node


def read_fields(pdf):
    """Every fillable widget, page by page: dicts with name, kind, page, rect, on, options, value."""
    out = []
    for i, page in enumerate(pdf.pages):
        for annot in page.obj.get("/Annots", []):
            try:
                if annot.get("/Subtype") != pikepdf.Name.Widget:
                    continue
                field = _field_of(annot)
                ft = inherited(field, "/FT")
                ff = int(inherited(field, "/Ff", 0))
                r = [float(v) for v in annot.Rect]
                rect = (min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3]))
                states = [str(k)[1:] for k in annot.AP.N.keys() if k != "/Off"] if "/AP" in annot and \
                    isinstance(annot.AP.get("/N"), pikepdf.Dictionary) else []
                value = inherited(field, "/V")
                if ft == pikepdf.Name.Tx:
                    kind = "text"
                elif ft == pikepdf.Name.Ch:
                    kind = "choice"
                elif ft == pikepdf.Name.Btn:
                    if ff & (1 << 16):
                        continue  # a push button: nothing to fill
                    kind = "radio" if ff & (1 << 15) else "check"
                else:
                    continue  # signature fields and others
                opts = []
                for o in inherited(field, "/Opt", []) or []:
                    opts.append(str(o[1] if isinstance(o, pikepdf.Array) else o))
                if kind in ("text", "choice"):
                    value = str(value) if value is not None else ""
                else:
                    value = str(value)[1:] if isinstance(value, pikepdf.Name) and value != pikepdf.Name.Off else ""
                out.append(dict(name=_full_name(field), kind=kind, page=i, rect=rect, on=states[0] if states else "Yes",
                                options=opts, value=value, readonly=bool(ff & 1), multiline=bool(ff & (1 << 12))))
            except Exception as e:
                log_error(f"Form field: {type(e).__name__}: {e}")
    return out


def apply_fill(pdf, values):
    """Set form field values: text/choice -> str, checkbox -> bool, radio -> the chosen state name."""
    if "/AcroForm" not in pdf.Root or not values:
        return
    done = set()
    for page in pdf.pages:
        for annot in page.obj.get("/Annots", []):
            if annot.get("/Subtype") != pikepdf.Name.Widget:
                continue
            field = _field_of(annot)
            name = _full_name(field)
            if name not in values:
                continue
            v = values[name]
            ft = inherited(field, "/FT")
            if ft in (pikepdf.Name.Tx, pikepdf.Name.Ch):
                if name not in done:
                    field.V = pikepdf.String(v)
                    if "/AP" in annot and ft == pikepdf.Name.Tx:
                        del annot["/AP"]  # stale picture of the old value; a new one is generated
            elif ft == pikepdf.Name.Btn:
                states = [pikepdf.Name(k) for k in annot.AP.N.keys() if k != "/Off"] if "/AP" in annot else \
                    [pikepdf.Name.Yes]
                if isinstance(v, bool):  # checkbox
                    on = states[0] if states else pikepdf.Name.Yes
                    field.V = on if v else pikepdf.Name.Off
                    annot.AS = on if v else pikepdf.Name.Off
                else:  # radio: this widget is on if it's the chosen option
                    chosen = pikepdf.Name("/" + v) if v else pikepdf.Name.Off
                    field.V = chosen
                    annot.AS = chosen if chosen in states else pikepdf.Name.Off
            done.add(name)
    pdf.Root.AcroForm.NeedAppearances = True


# ---- bookmarks ---------------------------------------------------------------- #
def _dest_page(src, item, names):
    """The page object an outline item points to, or None."""
    dest = item.destination
    if dest is None and item.action is not None and item.action.get("/S") == pikepdf.Name.GoTo:
        dest = item.action.get("/D")
    if isinstance(dest, (pikepdf.String, pikepdf.Name)):
        dest = names.get(str(dest).lstrip("/"))
        if isinstance(dest, pikepdf.Dictionary):
            dest = dest.get("/D")
    if isinstance(dest, pikepdf.Array) and len(dest) and isinstance(dest[0], pikepdf.Dictionary):
        return dest[0]
    return None


def _named_dests(src):
    names = {}
    try:
        if "/Names" in src.Root and "/Dests" in src.Root.Names:
            for k, v in pikepdf.NameTree(src.Root.Names.Dests).items():
                names[str(k)] = v
        if "/Dests" in src.Root:
            for k, v in src.Root.Dests.items():
                names[str(k).lstrip("/")] = v
    except Exception as e:
        log_error(f"Named destinations: {type(e).__name__}: {e}")
    return names


def copy_bookmarks(out, used, opened, docs):
    """Rebuild the bookmarks for the pages that were kept.

    used: [(doc id, source page index, output page index)] in output order. One
    file keeps its bookmarks as they were; when files are combined, each gets
    a bookmark with its name holding its own (as PDF editors do)."""
    order = []
    first = {}
    where = {}
    for doc_id, si, oi in used:
        if doc_id not in first:
            order.append(doc_id)
            first[doc_id] = oi
        where.setdefault((doc_id, si), oi)
    tops = []
    for doc_id in order:
        src = opened[doc_id]
        items = []
        try:
            index = {pg.obj.objgen: i for i, pg in enumerate(src.pages)}
            names = _named_dests(src)

            def convert(nodes):
                res = []
                for node in nodes:
                    kids = convert(node.children)
                    page = _dest_page(src, node, names)
                    oi = where.get((doc_id, index.get(page.objgen))) if page is not None else None
                    if oi is None:
                        res.extend(kids)  # its page was removed: keep what's under it
                        continue
                    item = pikepdf.OutlineItem(str(node.title), oi)
                    item.children.extend(kids)
                    res.append(item)
                return res
            with src.open_outline() as ol:
                items = convert(ol.root)
        except Exception as e:
            log_error(f"Bookmarks of {docs[doc_id].name}: {type(e).__name__}: {e}")
        if len(order) > 1:
            if not docs[doc_id].path:  # an inserted blank page: no bookmark of its own
                tops.extend(items)
                continue
            top = pikepdf.OutlineItem(os.path.splitext(docs[doc_id].name)[0], first[doc_id])
            top.children.extend(items)
            tops.append(top)
        else:
            tops.extend(items)
    if tops:
        with out.open_outline() as ol:
            ol.root.extend(tops)


# ---- placed text, marks and signatures -------------------------------------- #
INK = (16, 24, 40)  # the colour of drawn and typed signatures
SIG_DIR = os.path.join(os.path.expanduser("~"), ".pdf-toolkit")


def signature_path(kind):
    return os.path.join(SIG_DIR, f"{kind}.png")


def load_signature(kind):
    try:
        with Image.open(signature_path(kind)) as im:
            return im.convert("RGBA")
    except (OSError, ValueError):
        return None


def save_signature(kind, im):
    os.makedirs(SIG_DIR, exist_ok=True)
    im.save(signature_path(kind))


def forget_signatures():
    for kind in ("signature", "initials"):
        try:
            os.remove(signature_path(kind))
        except OSError:
            pass


def handwriting_fonts():
    """Handwriting-style fonts installed on this computer: {name: path}."""
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        candidates = [("Segoe Script", "segoesc.ttf"), ("Ink Free", "Inkfree.ttf"),
                      ("Lucida Handwriting", "LHANDW.TTF"), ("Bradley Hand", "BRADHITC.TTF"),
                      ("Freestyle Script", "FREESCPT.TTF")]
    elif sys.platform == "darwin":
        base = "/System/Library/Fonts/Supplemental"
        candidates = [("Snell Roundhand", "SnellRoundhand.ttc"), ("Bradley Hand", "Bradley Hand Bold.ttf"),
                      ("Apple Chancery", "Apple Chancery.ttf"), ("Noteworthy", "Noteworthy.ttc"),
                      ("Chalkboard", "Chalkboard.ttc")]
    else:
        base = "/usr/share/fonts/truetype"
        candidates = [("URW Chancery", "urw-base35/Z003-MediumItalic.ttf"), ("DejaVu Serif Italic",
                                                                             "dejavu/DejaVuSerif-Italic.ttf")]
    return {name: os.path.join(base, f) for name, f in candidates if os.path.exists(os.path.join(base, f))}


def typed_signature(text, font_path):
    """A typed name in a handwriting font, as a transparent image cropped to the ink."""
    from PIL import ImageDraw, ImageFont
    font = ImageFont.truetype(font_path, 120) if font_path else ImageFont.load_default()
    l, t, r, b = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
    im = Image.new("RGBA", (r - l + 40, b - t + 40), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((20 - l, 20 - t), text, font=font, fill=INK + (255,))
    return im.crop(im.getbbox()) if im.getbbox() else im


def drawn_signature(strokes, size):
    """Mouse strokes (lists of (x, y) in a box of `size`) as a smooth transparent image."""
    from PIL import ImageDraw
    k = 3  # draw big and shrink, for smooth edges
    im = Image.new("RGBA", (size[0] * k, size[1] * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for s in strokes:
        pts = [(x * k, y * k) for x, y in s]
        if len(pts) == 1:
            x, y = pts[0]
            d.ellipse([x - 4 * k, y - 4 * k, x + 4 * k, y + 4 * k], fill=INK + (255,))
        else:
            d.line(pts, fill=INK + (255,), width=3 * k, joint="curve")
            for x, y in (pts[0], pts[-1]):
                d.ellipse([x - 1.5 * k, y - 1.5 * k, x + 1.5 * k, y + 1.5 * k], fill=INK + (255,))
    im = im.resize(size, Image.Resampling.LANCZOS)
    box = im.getbbox()
    return im.crop((max(0, box[0] - 4), max(0, box[1] - 4), box[2] + 4, box[3] + 4)) if box else None


MARK_GLYPHS = {"check": "4", "cross": "8"}  # ✔ and ✘ in the standard ZapfDingbats font


def mark_size(m):
    """A placed item's width and height in points (as created, before any later page turn)."""
    if m["kind"] in ("text", "date", "box"):
        return text_width(m["text"], m["size"]) + 2, m["size"] * 1.25
    if m["kind"] in MARK_GLYPHS:
        return m["size"] * 0.9, m["size"]
    return m["w"], m["w"] * m["image"].height / m["image"].width


def mark_annotation(pdf, m, box):
    """A placed item as a PDF annotation: typed text as FreeText (still editable in
    PDF editors), marks and signatures as a Stamp. Each has its own appearance, so
    it looks the same everywhere, and can be flattened into the page."""
    w, h = mark_size(m)
    a, b, c, d, _, _ = shown_matrix(box, m["rot"])
    ex, ey = (h / 2, w / 2) if m["rot"] % 180 else (w / 2, h / 2)
    rect = [m["x"] - ex, m["y"] - ey, m["x"] + ex, m["y"] + ey]
    res = pikepdf.Dictionary()
    if m["kind"] in ("text", "date"):
        res.Font = pikepdf.Dictionary(Helv=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding)))
        ops = (f"q BT 0 g /Helv {m['size']:.2f} Tf 1 {m['size'] * 0.27:.2f} Td ".encode()
               + _pdf_text(m["text"]) + b" Tj ET Q")
    elif m["kind"] in MARK_GLYPHS:
        res.Font = pikepdf.Dictionary(ZaDb=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.ZapfDingbats)))
        ops = f"q BT 0 g /ZaDb {m['size']:.2f} Tf 0 {m['size'] * 0.18:.2f} Td ({MARK_GLYPHS[m['kind']]}) Tj ET Q".encode()
    else:
        im = m["image"]
        xo = pdf.make_stream(zlib.compress(im.convert("RGB").tobytes(), 6), Type=pikepdf.Name.XObject,
                             Subtype=pikepdf.Name.Image, Width=im.width, Height=im.height,
                             ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        xo.SMask = pdf.make_stream(zlib.compress(im.getchannel("A").tobytes(), 6), Type=pikepdf.Name.XObject,
                                   Subtype=pikepdf.Name.Image, Width=im.width, Height=im.height,
                                   ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8,
                                   Filter=pikepdf.Name.FlateDecode)
        res.XObject = pikepdf.Dictionary(Sig=xo)
        ops = f"q {w:.3f} 0 0 {h:.3f} 0 0 cm /Sig Do Q".encode()
    ap = pdf.make_stream(ops, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, w, h],
                         Matrix=[a, b, c, d, 0, 0], Resources=res)
    annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Rect=rect, F=4, AP=pikepdf.Dictionary(N=ap),
                               NM=pikepdf.String(f"pdftoolkit-{m['id']}"))
    if m["kind"] in ("text", "date"):
        annot.Subtype = pikepdf.Name.FreeText
        annot.Contents = pikepdf.String(m["text"])
        annot.DA = pikepdf.String(f"/Helv {m['size']:.1f} Tf 0 g")
    else:
        annot.Subtype = pikepdf.Name.Stamp
        annot.Name = pikepdf.Name("/" + ("Signature" if m["kind"] in ("signature", "initials") else "Approved"))
    return pdf.make_indirect(annot)


def auto_trim(pil_image, threshold=245, pad=0.01):
    """Margins (left, top, right, bottom) as fractions, around whatever isn't white."""
    g = pil_image.convert("L").point(lambda v: 255 if v < threshold else 0)
    box = g.getbbox()
    if not box:
        return None
    w, h = pil_image.size
    return (max(0.0, box[0] / w - pad), max(0.0, box[1] / h - pad),
            max(0.0, 1 - box[2] / w - pad), max(0.0, 1 - box[3] / h - pad))


# ---- comments (standard PDF annotations, each with its own appearance) ----------- #
COMMENT_COLORS = {"Yellow": (1.0, 0.85, 0.1), "Green": (0.35, 0.8, 0.3), "Blue": (0.25, 0.55, 1.0),
                  "Pink": (1.0, 0.45, 0.7), "Red": (0.9, 0.15, 0.15)}
TEXT_MARKUP = ("highlight", "underline", "strike")
SHAPES = ("rect", "ellipse", "arrow", "ink")
COMMENT_KINDS = TEXT_MARKUP + SHAPES + ("note", "box")


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(c * 255) for c in rgb)


def _bounds(points, pad=0.0):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]


def _form_xobject(pdf, ops, bbox, res=None, matrix=None):
    kw = dict(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[round(v, 3) for v in bbox],
              Resources=res or pikepdf.Dictionary())
    if matrix:
        kw["Matrix"] = matrix
    return pdf.make_stream(ops.encode("latin-1") if isinstance(ops, str) else ops, **kw)


def _ellipse_ops(x0, y0, x1, y1):
    k = 0.5523
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    return (f"{cx + rx:.2f} {cy:.2f} m {cx + rx:.2f} {cy + k * ry:.2f} {cx + k * rx:.2f} {cy + ry:.2f} {cx:.2f} {cy + ry:.2f} c "
            f"{cx - k * rx:.2f} {cy + ry:.2f} {cx - rx:.2f} {cy + k * ry:.2f} {cx - rx:.2f} {cy:.2f} c "
            f"{cx - rx:.2f} {cy - k * ry:.2f} {cx - k * rx:.2f} {cy - ry:.2f} {cx:.2f} {cy - ry:.2f} c "
            f"{cx + k * rx:.2f} {cy - ry:.2f} {cx + rx:.2f} {cy - k * ry:.2f} {cx + rx:.2f} {cy:.2f} c")


def arrow_head(x1, y1, x2, y2, size):
    """The two short lines of an open arrowhead at (x2, y2)."""
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    return [(x2 - size * math.cos(a - 0.45), y2 - size * math.sin(a - 0.45)),
            (x2 - size * math.cos(a + 0.45), y2 - size * math.sin(a + 0.45))]


def comment_annotation(pdf, m, box):
    """A comment as a standard PDF annotation (Highlight, Underline, StrikeOut, Text,
    FreeText, Square, Circle, Line, Ink), so other PDF apps show and edit it."""
    N = pikepdf.Name
    col = m["color"]
    rgb = f"{col[0]:.3f} {col[1]:.3f} {col[2]:.3f}"
    lw = m.get("width", 2)
    annot = pikepdf.Dictionary(Type=N.Annot, F=4, C=list(col), NM=pikepdf.String(f"pdftoolkit-{m['id']}"),
                               T=pikepdf.String(m.get("author", "")), M=pikepdf.String(time.strftime("D:%Y%m%d%H%M%S")))
    if m.get("text"):
        annot.Contents = pikepdf.String(m["text"])
    kind = m["kind"]
    if kind in TEXT_MARKUP:
        rects = m["rects"]
        quads = []
        for l, b, r, t in rects:
            quads += [l, t, r, t, l, b, r, b]
        rect = _bounds([(l, b) for l, b, _, _ in rects] + [(r, t) for _, _, r, t in rects], 1)
        annot.Subtype = {"highlight": N.Highlight, "underline": N.Underline, "strike": N.StrikeOut}[kind]
        annot.QuadPoints = [round(v, 3) for v in quads]
        ops, res = [], pikepdf.Dictionary()
        if kind == "highlight":
            res.ExtGState = pikepdf.Dictionary(GS=pdf.make_indirect(pikepdf.Dictionary(Type=N.ExtGState, BM=N.Multiply,
                                                                                        ca=1, CA=1)))
            ops.append(f"/GS gs {rgb} rg")
            ops += [f"{l:.2f} {b:.2f} {r - l:.2f} {t - b:.2f} re f" for l, b, r, t in rects]
        else:
            ops.append(f"{rgb} RG")
            for l, b, r, t in rects:
                h = t - b
                y = b + h * 0.08 if kind == "underline" else b + h * 0.42
                ops.append(f"{max(0.6, h * 0.07):.2f} w {l:.2f} {y:.2f} m {r:.2f} {y:.2f} l S")
        ap = _form_xobject(pdf, "\n".join(ops), rect, res)
    elif kind == "note":
        x, y = m["x"], m["y"]  # top-left corner of the icon
        rect = [x, y - 20, x + 20, y]
        annot.Subtype = N.Text
        annot.Name = N.Comment
        annot.Open = False
        ops = (f"{rgb} rg 0.25 G 0.8 w {x + 1:.2f} {y - 19:.2f} 18 15 re B "
               f"{x + 5:.2f} {y - 4:.2f} m {x + 5:.2f} {y - 1:.2f} l {x + 9:.2f} {y - 4:.2f} l f "
               f"0.25 G 1 w {x + 4:.2f} {y - 9:.2f} m {x + 16:.2f} {y - 9:.2f} l S "
               f"{x + 4:.2f} {y - 13:.2f} m {x + 16:.2f} {y - 13:.2f} l S")
        ap = _form_xobject(pdf, ops, rect)
    elif kind == "box":
        # A text box: like placed text, but with a coloured border.
        w, h = mark_size(m)
        w, h = w + 8, h + 6
        a, b, c, d, _, _ = shown_matrix(box, m["rot"])
        ex, ey = (h / 2, w / 2) if m["rot"] % 180 else (w / 2, h / 2)
        rect = [m["x"] - ex, m["y"] - ey, m["x"] + ex, m["y"] + ey]
        annot.Subtype = N.FreeText
        annot.DA = pikepdf.String(f"/Helv {m['size']:.1f} Tf 0 g")
        annot.BS = pikepdf.Dictionary(W=1)
        res = pikepdf.Dictionary(Font=pikepdf.Dictionary(Helv=pdf.make_indirect(pikepdf.Dictionary(
            Type=N.Font, Subtype=N.Type1, BaseFont=N.Helvetica, Encoding=N.WinAnsiEncoding))))
        ops = (f"q 1 1 1 rg {rgb} RG 1.2 w 0.6 0.6 {w - 1.2:.2f} {h - 1.2:.2f} re B Q "
               f"q BT 0 g /Helv {m['size']:.2f} Tf 5 {3 + m['size'] * 0.27:.2f} Td ").encode() + _pdf_text(m["text"]) + b" Tj ET Q"
        ap = _form_xobject(pdf, ops, [0, 0, w, h], res, [a, b, c, d, 0, 0])
    elif kind in ("rect", "ellipse"):
        x0, y0, x1, y1 = m["rect"]
        rect = [x0 - lw, y0 - lw, x1 + lw, y1 + lw]
        annot.Subtype = N.Square if kind == "rect" else N.Circle
        annot.BS = pikepdf.Dictionary(W=lw)
        path = (f"{x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f} re" if kind == "rect" else _ellipse_ops(x0, y0, x1, y1))
        ap = _form_xobject(pdf, f"{rgb} RG {lw} w {path} S", rect)
    elif kind == "arrow":
        (x1, y1), (x2, y2) = m["points"]
        head = arrow_head(x1, y1, x2, y2, 6 + lw * 2)
        rect = _bounds([(x1, y1), (x2, y2)] + head, lw + 2)
        annot.Subtype = N.Line
        annot.L = [x1, y1, x2, y2]
        annot.LE = [N("/None"), N.OpenArrow]
        annot.BS = pikepdf.Dictionary(W=lw)
        ops = (f"{rgb} RG {lw} w 1 J 1 j {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S "
               f"{head[0][0]:.2f} {head[0][1]:.2f} m {x2:.2f} {y2:.2f} l {head[1][0]:.2f} {head[1][1]:.2f} l S")
        ap = _form_xobject(pdf, ops, rect)
    elif kind == "ink":
        strokes = m["strokes"]
        rect = _bounds([p for s in strokes for p in s], lw + 1)
        annot.Subtype = N.Ink
        annot.InkList = [[round(v, 2) for p in s for v in p] for s in strokes]
        annot.BS = pikepdf.Dictionary(W=lw)
        ops = [f"{rgb} RG {lw} w 1 J 1 j"]
        for s in strokes:
            ops.append(f"{s[0][0]:.2f} {s[0][1]:.2f} m " + " ".join(f"{x:.2f} {y:.2f} l" for x, y in s[1:]) + " S"
                       if len(s) > 1 else f"{s[0][0]:.2f} {s[0][1]:.2f} m {s[0][0] + 0.1:.2f} {s[0][1]:.2f} l S")
        ap = _form_xobject(pdf, "\n".join(ops), rect)
    else:
        raise ValueError(kind)
    annot.Rect = [round(v, 3) for v in rect]
    annot.AP = pikepdf.Dictionary(N=ap)
    if kind == "highlight":
        annot.CA = 1
    return pdf.make_indirect(annot)


# ---- new form fields ------------------------------------------------------------ #
FIELD_KINDS = {"text": "Text field", "check": "Checkbox", "choice": "Dropdown", "signature": "Signature field"}


def _acroform(pdf):
    N = pikepdf.Name
    if "/AcroForm" not in pdf.Root:
        pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=pikepdf.Array()))
    af = pdf.Root.AcroForm
    if "/Fields" not in af:
        af.Fields = pikepdf.Array()
    dr = af.get("/DR")
    if not isinstance(dr, pikepdf.Dictionary):
        af.DR = dr = pikepdf.Dictionary()
    if "/Font" not in dr:
        dr.Font = pikepdf.Dictionary()
    if "/Helv" not in dr.Font:
        dr.Font.Helv = pdf.make_indirect(pikepdf.Dictionary(Type=N.Font, Subtype=N.Type1, BaseFont=N.Helvetica,
                                                            Encoding=N.WinAnsiEncoding))
    if "/ZaDb" not in dr.Font:
        dr.Font.ZaDb = pdf.make_indirect(pikepdf.Dictionary(Type=N.Font, Subtype=N.Type1, BaseFont=N.ZapfDingbats))
    if "/DA" not in af:
        af.DA = pikepdf.String("/Helv 0 Tf 0 g")
    return af


def add_form_fields(pdf, page, fields, rot):
    """Create fillable fields (made with Prepare form) on an output page."""
    N = pikepdf.Name
    af = _acroform(pdf)
    taken = {_full_name(f) for f in _walk_fields(af.Fields)}
    annots = pikepdf.Array(list(page.obj.get("/Annots", [])))
    for f in fields:
        name, n = f["name"], 2
        while name in taken:
            name, n = f"{f['name']}_{n}", n + 1
        taken.add(name)
        w = pikepdf.Dictionary(Type=N.Annot, Subtype=N.Widget, Rect=[round(v, 3) for v in f["rect"]], F=4,
                               P=page.obj, T=pikepdf.String(name),
                               MK=pikepdf.Dictionary(BC=[0.45, 0.5, 0.6], BG=[0.92, 0.95, 1.0], R=rot))
        if f["kind"] == "text":
            w.FT = N.Tx
            w.DA = pikepdf.String("/Helv 0 Tf 0 g")
            if f.get("multiline"):
                w.Ff = 1 << 12
            if f.get("value"):
                w.V = pikepdf.String(f["value"])
        elif f["kind"] == "choice":
            w.FT = N.Ch
            w.Ff = 1 << 17  # a dropdown
            w.Opt = pikepdf.Array([pikepdf.String(o) for o in f.get("options", [])])
            w.DA = pikepdf.String("/Helv 0 Tf 0 g")
            if f.get("value"):
                w.V = pikepdf.String(f["value"])
        elif f["kind"] == "check":
            x0, y0, x1, y1 = f["rect"]
            s = min(x1 - x0, y1 - y0)
            on = _form_xobject(pdf, f"q 0 g BT /ZaDb {s * 0.8:.2f} Tf {s * 0.12:.2f} {s * 0.2:.2f} Td (4) Tj ET Q",
                               [0, 0, x1 - x0, y1 - y0],
                               pikepdf.Dictionary(Font=pikepdf.Dictionary(ZaDb=af.DR.Font.ZaDb)))
            off = _form_xobject(pdf, "", [0, 0, x1 - x0, y1 - y0])
            w.FT = N.Btn
            w.AP = pikepdf.Dictionary(N=pikepdf.Dictionary(Yes=on, Off=off))
            w.V = w.AS = N.Yes if f.get("value") else N.Off
            w.MK.CA = pikepdf.String("4")
        elif f["kind"] == "signature":
            w.FT = N.Sig
        w = pdf.make_indirect(w)
        annots.append(w)
        af.Fields.append(w)
    page.obj.Annots = annots
    af.NeedAppearances = True


def _walk_fields(fields):
    for f in fields:
        yield f
        if "/Kids" in f:
            yield from _walk_fields(f.Kids)


# ---- certificate signatures (pyHanko) ---------------------------------------------- #
CERT_PATH = os.path.join(SIG_DIR, "certificate.p12")


def create_certificate(name, email, password, path=CERT_PATH):
    """A self-signed signing certificate, saved as a password-protected .p12 file."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, name)]
    if email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email))
    subject = x509.Name(attrs)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .sign(key, hashes.SHA256()))
    data = pkcs12.serialize_key_and_certificates(name.encode("utf-8"), key, cert, None,
                                                serialization.BestAvailableEncryption(password.encode("utf-8")))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def load_signer(path, password):
    """The signing key and certificate from a .p12/.pfx file. Raises ValueError with a
    readable reason for a wrong password or a file without a key."""
    import logging
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
    logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)
    from pyhanko.sign import signers
    try:
        signer = signers.SimpleSigner.load_pkcs12(path, passphrase=password.encode("utf-8") if password else None)
    except Exception as e:
        raise ValueError(f"Couldn't open the certificate: {e}") from e
    if signer is None:
        raise ValueError("Couldn't open the certificate. Check the password.")
    return signer


def signer_name(signer):
    subj = signer.signing_cert.subject.native
    return subj.get("common_name") or subj.get("email_address") or "Unknown"


def signature_appearance(name, when, reason, image, w, h):
    """The visible box of a certificate signature: the handwritten signature (if any)
    on the left, who signed, when and why on the right."""
    from PIL import ImageDraw, ImageFont
    scale = 6  # pixels per point, so it prints sharply
    W, H = max(1, int(w * scale)), max(1, int(h * scale))
    im = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W - 1, H - 1], outline=(70, 90, 130, 255), width=max(2, scale // 2))
    text_x = 10 * scale // 2
    if image is not None:
        box_w = int(W * 0.45)
        sig = image.copy()
        sig.thumbnail((box_w - 6 * scale, H - 6 * scale), Image.Resampling.LANCZOS)
        im.alpha_composite(sig, (3 * scale + (box_w - 6 * scale - sig.width) // 2, (H - sig.height) // 2))
        text_x = box_w
    try:
        font = ImageFont.truetype("arial.ttf" if sys.platform == "win32" else "Helvetica.ttc", 10)
    except OSError:
        font = None
    lines = [f"Digitally signed by {name}", f"Date: {when}"] + ([f"Reason: {reason}"] if reason else [])
    # The largest text size that fits the room left.
    avail_w, avail_h = W - text_x - 4 * scale, H - 6 * scale
    size = max(6, min(int(avail_h / (len(lines) * 1.3)), 13 * scale))
    while size > 6:
        try:
            font = ImageFont.truetype("arial.ttf" if sys.platform == "win32" else "Helvetica.ttc", size)
        except OSError:
            font = ImageFont.load_default()
            break
        if max(d.textlength(l, font=font) for l in lines) <= avail_w:
            break
        size -= 2
    y = (H - len(lines) * size * 1.3) / 2
    for line in lines:
        d.text((text_x, y), line, font=font, fill=(25, 35, 60, 255))
        y += size * 1.3
    return im


def sign_pdf_file(src_path, dst_path, req, password=None):
    """Add a certificate signature to a finished PDF (as the very last step: any change
    after this would break it). req: path, password, page, rect, reason, location, image."""
    from pyhanko.pdf_utils.images import PdfImage
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko.stamp import TextStampStyle
    signer = load_signer(req["path"], req["password"])
    name = signer_name(signer)
    x0, y0, x1, y1 = req["rect"]
    when = time.strftime("%d %b %Y %H:%M").lstrip("0")
    ap = signature_appearance(name, when, req.get("reason"), req.get("image"), x1 - x0, y1 - y0)
    with open(src_path, "rb") as f:
        w = IncrementalPdfFileWriter(io.BytesIO(f.read()))
    if password:
        w.encrypt(password)
    field = f"Signature_{time.strftime('%Y%m%d%H%M%S')}"
    fields.append_signature_field(w, fields.SigFieldSpec(field, on_page=req["page"], box=(x0, y0, x1, y1)))
    meta = signers.PdfSignatureMetadata(field_name=field, reason=req.get("reason") or None,
                                        location=req.get("location") or None, md_algorithm="sha256", name=name)
    style = TextStampStyle(stamp_text="", background=PdfImage(ap), background_opacity=1.0, border_width=0)
    with open(dst_path, "wb") as out:
        signers.PdfSigner(meta, signer=signer, stamp_style=style).sign_pdf(w, output=out)
    return name


def check_signatures(data, password=None):
    """Existing certificate signatures in a PDF: [dict(field, name, when, intact, covers_all)].
    'intact' = nothing changed since signing. Who the signer is can't be confirmed
    offline without a list of trusted authorities, so that's not claimed."""
    import logging
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
    logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    out = []
    r = PdfFileReader(io.BytesIO(data))
    if r.encrypted and password:
        r.decrypt(password)
    for sig in r.embedded_signatures:
        try:
            st = validate_pdf_signature(sig, ValidationContext(allow_fetching=False, trust_roots=[]))
            subj = st.signing_cert.subject.native
            when = st.signer_reported_dt.strftime("%d %b %Y %H:%M").lstrip("0") if st.signer_reported_dt else "?"
            out.append(dict(field=sig.field_name, name=subj.get("common_name") or subj.get("email_address") or "?",
                            when=when, intact=bool(st.intact and st.valid),
                            covers_all=str(st.coverage).endswith("ENTIRE_FILE")))
        except Exception as e:
            out.append(dict(field=sig.field_name, name="?", when="?", intact=False, covers_all=False, error=str(e)))
    return out


# ---- comparing two PDFs ------------------------------------------------------------ #
def diff_boxes(a, b, cell=8, threshold=40):
    """Rectangles (in pixels) around the places where two same-sized renders differ."""
    from PIL import ImageChops
    mask = ImageChops.difference(a.convert("L"), b.convert("L")).point(lambda v: 255 if v > threshold else 0)
    small = mask.resize((max(1, mask.width // cell), max(1, mask.height // cell)), Image.Resampling.BOX)
    sw, sh = small.size
    px = small.load()
    seen = set()
    boxes = []
    for y in range(sh):
        for x in range(sw):
            if px[x, y] < 8 or (x, y) in seen:
                continue
            # flood-fill the connected changed cells (with a cell of slack)
            stack, x0, y0, x1, y1 = [(x, y)], x, y, x, y
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                x0, y0, x1, y1 = min(x0, cx), min(y0, cy), max(x1, cx), max(y1, cy)
                for nx in range(cx - 2, cx + 3):
                    for ny in range(cy - 2, cy + 3):
                        if 0 <= nx < sw and 0 <= ny < sh and (nx, ny) not in seen and px[nx, ny] >= 8:
                            seen.add((nx, ny))
                            stack.append((nx, ny))
            boxes.append((x0 * cell - 3, y0 * cell - 3, (x1 + 1) * cell + 3, (y1 + 1) * cell + 3))
    return boxes


def word_changes(old, new):
    """Words removed and added between two texts: [('removed'|'added', 'text')]."""
    import difflib
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            out.append(("removed", " ".join(a[i1:i2])))
        if op in ("insert", "replace"):
            out.append(("added", " ".join(b[j1:j2])))
    return out


# ---- editing a page's own text and images ---------------------------------------- #
# A PDF page is a list of drawing operators. To edit text in place, the page's
# operators are read the way a PDF renderer does (graphics state, text state,
# fonts) to learn where every glyph is and what character it is. Editing a
# paragraph then (1) replaces the operators that drew it with ones that draw
# nothing but move the pen the same distance, so everything after stays where it
# was, and (2) draws the new text, re-wrapped to the paragraph's width: in the
# PDF's own embedded font when it has every character needed, otherwise in the
# same font from this computer (only the letters used are embedded), otherwise
# in the closest standard font.
STD_WIDTHS = {
    "Helvetica": HELVETICA,
    "Helvetica-Bold": [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584],
    "Times-Roman": [250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444, 921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722, 556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500, 333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541],
    "Times-Bold": [250, 333, 555, 500, 500, 1000, 833, 278, 333, 333, 500, 570, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 333, 333, 570, 570, 570, 500, 930, 722, 667, 722, 722, 667, 611, 778, 778, 389, 500, 778, 667, 944, 722, 778, 611, 778, 722, 556, 667, 722, 722, 1000, 722, 722, 667, 333, 278, 333, 581, 500, 333, 500, 556, 444, 556, 444, 333, 500, 556, 278, 333, 556, 278, 833, 556, 500, 556, 556, 444, 389, 333, 556, 500, 722, 500, 500, 444, 394, 220, 394, 520],
    "Times-Italic": [250, 333, 420, 500, 500, 833, 778, 214, 333, 333, 500, 675, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 333, 333, 675, 675, 675, 500, 920, 611, 611, 667, 722, 611, 611, 722, 722, 333, 444, 667, 556, 833, 667, 722, 611, 722, 611, 500, 556, 722, 611, 833, 611, 556, 556, 389, 278, 389, 422, 500, 333, 500, 500, 444, 500, 444, 278, 500, 500, 278, 278, 444, 278, 722, 500, 500, 500, 500, 389, 389, 278, 500, 444, 667, 444, 444, 389, 400, 275, 400, 541],
    "Times-BoldItalic": [250, 389, 555, 500, 500, 833, 778, 278, 333, 333, 500, 570, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 333, 333, 570, 570, 570, 500, 832, 667, 667, 667, 722, 667, 667, 722, 778, 389, 500, 667, 611, 889, 722, 722, 611, 722, 667, 556, 611, 722, 667, 889, 667, 611, 611, 333, 278, 333, 570, 500, 333, 500, 500, 444, 500, 444, 333, 500, 556, 278, 278, 500, 278, 778, 556, 500, 500, 500, 389, 389, 278, 556, 444, 667, 500, 444, 389, 348, 220, 348, 570],
}
STD_WIDTHS["Helvetica-Oblique"] = STD_WIDTHS["Helvetica"]
STD_WIDTHS["Helvetica-BoldOblique"] = STD_WIDTHS["Helvetica-Bold"]
for _n in ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"):
    STD_WIDTHS[_n] = [600] * 95


def mat_mul(m, n):
    """Product of two PDF matrices (a, b, c, d, e, f), m applied first."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def mat_inv(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return (1, 0, 0, 1, 0, 0)
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _num(v):
    return float(v)


# ---- fonts ------------------------------------------------------------------------ #
def _parse_tounicode(data):
    """A ToUnicode CMap: {code bytes: text}, and the code lengths it uses."""
    import re as _re
    out, lengths = {}, set()

    def hexb(h):
        return bytes.fromhex(h)

    def hext(h):
        try:
            return bytes.fromhex(h).decode("utf-16-be", "replace")
        except ValueError:
            return ""
    text = data.decode("latin-1")
    for block in _re.findall(r"beginbfchar(.*?)endbfchar", text, _re.S):
        for src, dst in _re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[hexb(src)] = hext(dst)
            lengths.add(len(src) // 2)
    for block in _re.findall(r"beginbfrange(.*?)endbfrange", text, _re.S):
        for lo, hi, rest in _re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            n = len(lo) // 2
            lengths.add(n)
            a, b = int(lo, 16), int(hi, 16)
            if rest.startswith("["):
                dsts = _re.findall(r"<([0-9A-Fa-f]*)>", rest)
                for i, code in enumerate(range(a, min(b, a + len(dsts) - 1) + 1)):
                    out[code.to_bytes(n, "big")] = hext(dsts[i])
            else:
                base = int(rest[1:-1] or "0", 16)
                width = max(2, len(rest) - 2) // 2
                for i, code in enumerate(range(a, min(b, a + 65535) + 1)):
                    try:
                        out[code.to_bytes(n, "big")] = (base + i).to_bytes(width, "big").decode("utf-16-be", "replace")
                    except OverflowError:
                        break
    return out, lengths


class FontInfo:
    """What's needed to read and write text in one font: code length, widths,
    code -> text, and text -> code (for the characters the font has)."""
    def __init__(self, font):
        self.font = font
        self.base = str(font.get("/BaseFont", "/Unknown"))[1:]
        self.composite = font.get("/Subtype") == pikepdf.Name.Type0
        self.widths, self.default_w = {}, 1000 if self.composite else 500
        self.to_text, self.from_text = {}, {}
        desc = None
        if self.composite:
            cid = font.DescendantFonts[0]
            desc = cid.get("/FontDescriptor")
            self.default_w = float(cid.get("/DW", 1000))
            w = cid.get("/W")
            if w is not None:
                w = list(w)
                i = 0
                while i < len(w):
                    first = int(w[i])
                    if isinstance(w[i + 1], pikepdf.Array):
                        for k, v in enumerate(w[i + 1]):
                            self.widths[first + k] = float(v)
                        i += 2
                    else:
                        last, v = int(w[i + 1]), float(w[i + 2])
                        for k in range(first, last + 1):
                            self.widths[k] = v
                        i += 3
            self.identity = font.get("/Encoding") in (pikepdf.Name("/Identity-H"), pikepdf.Name("/Identity-V"))
            self.code_len = 2
        else:
            desc = font.get("/FontDescriptor")
            fc = int(font.get("/FirstChar", 0))
            for k, v in enumerate(font.get("/Widths", [])):
                self.widths[fc + k] = float(v)
            if not self.widths and self.base in STD_WIDTHS:
                for k, v in enumerate(STD_WIDTHS[self.base]):
                    self.widths[32 + k] = float(v)
            self.code_len = 1
            self.identity = False
        self.ascent = float(desc.get("/Ascent", 800)) / 1000 if desc is not None else 0.8
        self.descent = float(desc.get("/Descent", -200)) / 1000 if desc is not None else -0.2
        if self.ascent <= 0.2:
            self.ascent, self.descent = 0.8, -0.2
        flags = int(desc.get("/Flags", 0)) if desc is not None else 0
        name = self.base.lower()
        self.bold = "bold" in name or "black" in name or "heavy" in name or "semibold" in name or bool(flags & (1 << 18))
        self.italic = "italic" in name or "oblique" in name or bool(flags & 64) or \
            (desc is not None and float(desc.get("/ItalicAngle", 0)) != 0)
        self.mono = "courier" in name or "mono" in name or "consol" in name or bool(flags & 1)
        self.serif = (not self.mono) and ("times" in name or "serif" in name and "sans" not in name or "georgia" in name
                                          or "garamond" in name or "cambria" in name or "book" in name
                                          or "minion" in name or "roman" in name or bool(flags & 2))
        tu = font.get("/ToUnicode")
        if isinstance(tu, pikepdf.Stream):
            try:
                self.to_text, lens = _parse_tounicode(tu.read_bytes())
                if lens and not self.composite:
                    self.code_len = min(lens)
            except Exception:
                self.to_text = {}
        if not self.to_text and not self.composite:
            enc = font.get("/Encoding")
            for code in range(32, 256):
                try:
                    self.to_text[bytes([code])] = bytes([code]).decode("cp1252")
                except UnicodeDecodeError:
                    pass
            if isinstance(enc, pikepdf.Dictionary) and "/Differences" in enc:
                code = 0
                for item in enc.Differences:
                    if isinstance(item, int) or isinstance(item, float):
                        code = int(item)
                    else:
                        ch = glyph_char(str(item)[1:])
                        if ch:
                            self.to_text[bytes([code])] = ch
                        code += 1
        for code, text in self.to_text.items():
            if len(text) == 1 and text not in self.from_text:
                self.from_text[text] = code

    def codes(self, data):
        """Split shown bytes into codes."""
        n = self.code_len
        return [data[i:i + n] for i in range(0, len(data) - n + 1, n)]

    def width(self, code):
        """Glyph width of a code, in 1/1000 em."""
        return self.widths.get(int.from_bytes(code, "big"), self.default_w)

    def text(self, code):
        t = self.to_text.get(code)
        if t is None:
            t = chr(code[-1]) if not self.composite and 32 <= code[-1] < 127 else "�"
        return t

    def encode(self, text):
        """Codes for text in this font, or None if it lacks a character."""
        out = []
        for ch in text:
            code = self.from_text.get(ch)
            if code is None:
                if ch == " " and not self.composite and b" " in self.to_text:
                    code = b" "
                else:
                    return None
            out.append(code)
        return out

    def standard(self):
        """The standard PDF font most like this one."""
        family = "Courier" if self.mono else "Times" if self.serif else "Helvetica"
        if family == "Times":
            style = {(False, False): "-Roman", (True, False): "-Bold", (False, True): "-Italic", (True, True): "-BoldItalic"}
        else:
            style = {(False, False): "", (True, False): "-Bold", (False, True): "-Oblique", (True, True): "-BoldOblique"}
        return family + style[(self.bold, self.italic)]


_GLYPHS = {"space": " ", "hyphen": "-", "period": ".", "comma": ",", "colon": ":", "semicolon": ";",
           "quoteright": "’", "quoteleft": "‘", "quotedblleft": "“", "quotedblright": "”",
           "endash": "–", "emdash": "—", "bullet": "•", "ellipsis": "…", "exclam": "!",
           "question": "?", "parenleft": "(", "parenright": ")", "slash": "/", "ampersand": "&", "at": "@",
           "dollar": "$", "percent": "%", "quotesingle": "'", "quotedbl": '"', "fi": "fi", "fl": "fl",
           "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
           "seven": "7", "eight": "8", "nine": "9"}


def glyph_char(name):
    if len(name) == 1:
        return name
    if name.startswith("uni") and len(name) == 7:
        try:
            return chr(int(name[3:], 16))
        except ValueError:
            return None
    return _GLYPHS.get(name)


# ---- reading a page ------------------------------------------------------------------ #
def read_page_content(pdf, page):
    """Interpret a page's operators. Returns (ops, glyphs, images, fonts):
    each glyph is a dict with the op index, character, its baseline start and end
    (page coordinates), size, font resource name and fill colour; each image is a
    dict with the op index, resource name and the matrix placing its unit square."""
    ops = list(pikepdf.parse_content_stream(page))
    res = page.obj.get("/Resources", pikepdf.Dictionary())
    fonts_res = res.get("/Font", pikepdf.Dictionary())
    xobjs = res.get("/XObject", pikepdf.Dictionary())
    fonts = {}
    glyphs, images = [], []
    gs = dict(ctm=(1, 0, 0, 1, 0, 0), fill=(0, 0, 0))
    stack = []
    ts = dict(font=None, size=12, tc=0, tw=0, th=1, tl=0, rise=0)
    tm = tlm = (1, 0, 0, 1, 0, 0)
    per_op = {}  # op index -> number of its last glyph

    def font_info(name):
        if name not in fonts:
            f = fonts_res.get(name)
            fonts[name] = FontInfo(f) if isinstance(f, pikepdf.Dictionary) else None
        return fonts[name]

    def show(i, data):
        nonlocal tm
        fi = font_info(ts["font"])
        if fi is None:
            return
        size, th = ts["size"], ts["th"]
        for code in fi.codes(bytes(data)):
            seq = per_op[i] = per_op.get(i, -1) + 1
            w0 = fi.width(code) / 1000
            trm = mat_mul(mat_mul((size * th, 0, 0, size, 0, ts["rise"]), tm), gs["ctm"])
            x0, y0 = mat_apply(trm, 0, 0)
            tx = (w0 * size + ts["tc"] + (ts["tw"] if code == b" " or (fi.code_len == 1 and code == b"\x20") else 0)) * th
            nm = mat_mul((1, 0, 0, 1, tx, 0), tm)
            x1, y1 = mat_apply(mat_mul(mat_mul((size * th, 0, 0, size, 0, ts["rise"]), nm), gs["ctm"]), 0, 0)
            # height direction of the glyph, in page units: the size as drawn
            ux, uy = mat_apply(trm, 0, 1)
            h = ((ux - x0) ** 2 + (uy - y0) ** 2) ** 0.5
            glyphs.append(dict(op=i, seq=seq, ch=fi.text(code), x0=x0, y0=y0, x1=x1, y1=y1, size=h, font=ts["font"],
                               fill=gs["fill"], ctm=gs["ctm"]))
            tm = nm

    for i, op in enumerate(ops):
        o = str(op.operator)
        a = op.operands
        try:
            if o == "q":
                stack.append((dict(gs), dict(ts)))  # the text state is part of the graphics state
            elif o == "Q":
                if stack:
                    gs, ts = stack.pop()
            elif o == "cm":
                gs["ctm"] = mat_mul(tuple(_num(v) for v in a), gs["ctm"])
            elif o in ("rg", "sc", "scn") and len(a) == 3 and all(not isinstance(v, pikepdf.Name) for v in a):
                gs["fill"] = tuple(_num(v) for v in a)
            elif o in ("g", "sc", "scn") and len(a) == 1 and not isinstance(a[0], pikepdf.Name):
                gs["fill"] = (_num(a[0]),) * 3
            elif o in ("k",) and len(a) == 4:
                c, m, y, k = (_num(v) for v in a)
                gs["fill"] = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
            elif o == "BT":
                tm = tlm = (1, 0, 0, 1, 0, 0)
            elif o == "Tf":
                ts["font"], ts["size"] = str(a[0]), _num(a[1])
            elif o == "Tc":
                ts["tc"] = _num(a[0])
            elif o == "Tw":
                ts["tw"] = _num(a[0])
            elif o == "Tz":
                ts["th"] = _num(a[0]) / 100
            elif o == "TL":
                ts["tl"] = _num(a[0])
            elif o == "Ts":
                ts["rise"] = _num(a[0])
            elif o == "Td":
                tlm = mat_mul((1, 0, 0, 1, _num(a[0]), _num(a[1])), tlm)
                tm = tlm
            elif o == "TD":
                ts["tl"] = -_num(a[1])
                tlm = mat_mul((1, 0, 0, 1, _num(a[0]), _num(a[1])), tlm)
                tm = tlm
            elif o == "Tm":
                tlm = tm = tuple(_num(v) for v in a)
            elif o == "T*":
                tlm = mat_mul((1, 0, 0, 1, 0, -ts["tl"]), tlm)
                tm = tlm
            elif o == "Tj":
                show(i, a[0])
            elif o == "'":
                tlm = mat_mul((1, 0, 0, 1, 0, -ts["tl"]), tlm)
                tm = tlm
                show(i, a[0])
            elif o == '"':
                ts["tw"], ts["tc"] = _num(a[0]), _num(a[1])
                tlm = mat_mul((1, 0, 0, 1, 0, -ts["tl"]), tlm)
                tm = tlm
                show(i, a[2])
            elif o == "TJ":
                for item in a[0]:
                    if isinstance(item, (pikepdf.String, bytes, str)):
                        show(i, item)
                    else:  # a number (int, Decimal...): move the pen back by that many 1/1000 em
                        tm = mat_mul((1, 0, 0, 1, -float(item) / 1000 * ts["size"] * ts["th"], 0), tm)
            elif o == "Do":
                name = str(a[0])
                x = xobjs.get(name)
                if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == pikepdf.Name.Image:
                    images.append(dict(op=i, name=name, ctm=gs["ctm"]))
        except Exception as e:  # an odd operator: skip it, keep reading
            log_error(f"Read content op {o}: {type(e).__name__}: {e}")
    return ops, glyphs, images, fonts


def text_blocks(glyphs):
    """Group glyphs into lines, and lines into paragraphs. Each paragraph:
    dict(ops (op indices), text, lines (list of line dicts), box (x0, y0, x1, y1),
    size, font, fill, angle)."""
    import math
    lines = []
    for g in glyphs:
        if g["ch"] in ("�",) or g["size"] <= 0:
            continue
        dx, dy = g["x1"] - g["x0"], g["y1"] - g["y0"]
        ang = round(math.degrees(math.atan2(dy, dx)) / 90) * 90 % 360 if (dx or dy) else 0
        cur = lines[-1] if lines else None
        if cur and cur["angle"] == ang and abs(cur["size"] - g["size"]) < 0.6 * max(cur["size"], g["size"]):
            # Same line if it continues along the baseline from the last glyph.
            last = cur["glyphs"][-1]
            ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            along = (g["x0"] - last["x1"]) * ca + (g["y0"] - last["y1"]) * sa
            across = -(g["x0"] - last["x1"]) * sa + (g["y0"] - last["y1"]) * ca
            if abs(across) < 0.35 * g["size"] and -0.5 * g["size"] < along < 1.6 * g["size"]:
                if along > 0.22 * g["size"] and last["ch"] != " " and g["ch"] != " ":
                    cur["text"] += " "
                cur["glyphs"].append(g)
                cur["text"] += g["ch"]
                continue
        lines.append(dict(glyphs=[g], text=g["ch"], angle=ang, size=g["size"]))
    for ln in lines:
        gl = ln["glyphs"]
        ln["text"] = ln["text"].rstrip()
        xs = [v for g in gl for v in (g["x0"], g["x1"])]
        ys = [v for g in gl for v in (g["y0"], g["y1"])]
        ln["box"] = (min(xs), min(ys), max(xs), max(ys))
        ln["start"] = (gl[0]["x0"], gl[0]["y0"])
        ln["font"] = max(set(g["font"] for g in gl), key=lambda f: sum(1 for g in gl if g["font"] == f))
        ln["size"] = sorted(g["size"] for g in gl)[len(gl) // 2]
    lines = [ln for ln in lines if ln["text"].strip()]
    paras = []
    for ln in lines:
        p = paras[-1] if paras else None
        if p and p["angle"] == ln["angle"] == 0 and abs(p["size"] - ln["size"]) < 0.15 * ln["size"]:
            prev = p["lines"][-1]
            gap = prev["start"][1] - ln["start"][1]
            left_ok = abs(prev["start"][0] - ln["start"][0]) < 1.5 * ln["size"]
            if left_ok and 0.8 * ln["size"] < gap < 1.9 * ln["size"] and \
                    (len(p["lines"]) == 1 or abs(gap - p["leading"]) < 0.25 * ln["size"]):
                p["lines"].append(ln)
                p["leading"] = gap
                continue
        paras.append(dict(lines=[ln], angle=ln["angle"], size=ln["size"], leading=ln["size"] * 1.2))
    for p in paras:
        gl = [g for ln in p["lines"] for g in ln["glyphs"]]
        p["ops"] = sorted(set(g["op"] for g in gl))
        p["glyph_ids"] = sorted((g["op"], g["seq"]) for g in gl)
        p["text"] = " ".join(ln["text"].strip() for ln in p["lines"])
        fonts = [g["font"] for g in gl if g["ch"] != " "] or [gl[0]["font"]]
        p["font"] = max(set(fonts), key=fonts.count)
        p["mixed"] = len(set(fonts)) > 1
        fills = [g["fill"] for g in gl if g["ch"] != " "] or [(0, 0, 0)]
        p["fill"] = max(set(fills), key=fills.count)
        xs = [v for ln in p["lines"] for v in (ln["box"][0], ln["box"][2])]
        ys = [v for ln in p["lines"] for v in (ln["box"][1], ln["box"][3])]
        p["box"] = (min(xs), min(ys) - p["size"] * 0.25, max(xs), max(ys) + p["size"] * 0.8)
        p["start"] = p["lines"][0]["start"]
        p["width"] = max(ln["box"][2] - ln["start"][0] for ln in p["lines"]) if p["angle"] == 0 else 0
    return paras


def image_box(ctm):
    pts = [mat_apply(ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ---- changing a page ------------------------------------------------------------------- #
def _strip_glyphs(ops, fonts_res, drop):
    """The page's operators with some glyphs no longer drawn. drop maps a text-showing
    op's index to the set of its glyph numbers to remove. Each removed glyph becomes
    an equal move of the pen (a TJ number), so everything else stays exactly where it was."""
    out, fonts, stack = [], {}, []
    ts = dict(font=None, size=12.0, tc=0.0, tw=0.0)
    for i, op in enumerate(ops):
        o = str(op.operator)
        a = op.operands
        if o == "q":
            stack.append(dict(ts))
        elif o == "Q" and stack:
            ts = stack.pop()
        elif o == "Tf":
            ts["font"], ts["size"] = str(a[0]), float(a[1])
        elif o == "Tc":
            ts["tc"] = float(a[0])
        elif o == "Tw":
            ts["tw"] = float(a[0])
        elif o == '"':
            ts["tw"], ts["tc"] = float(a[0]), float(a[1])
        if i not in drop:
            out.append(op)
            continue
        name = ts["font"]
        if name not in fonts:
            f = fonts_res.get(name)
            fonts[name] = FontInfo(f) if isinstance(f, pikepdf.Dictionary) else None
        fi, size = fonts[name], ts["size"]
        if fi is None or size == 0:
            out.append(op)
            continue
        gone = drop[i]
        items = list(a[0]) if o == "TJ" else [a[-1]]
        arr, text, seq = [], b"", 0

        def push_move(n):
            nonlocal text
            if text:
                arr.append(pikepdf.String(text))
                text = b""
            if arr and not isinstance(arr[-1], pikepdf.String):
                arr[-1] = arr[-1] + n
            else:
                arr.append(n)
        for item in items:
            if isinstance(item, (pikepdf.String, bytes, str)):
                for code in fi.codes(bytes(item)):
                    if seq in gone:
                        adv = fi.width(code) / 1000 * size + ts["tc"] +                             (ts["tw"] if fi.code_len == 1 and code == b" " else 0)
                        push_move(-adv * 1000 / size)
                    else:
                        text += code
                    seq += 1
            else:
                push_move(float(item))
        if text:
            arr.append(pikepdf.String(text))
        arr = [round(v, 3) if not isinstance(v, pikepdf.String) else v for v in arr]
        if o == "'":
            out.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("T*")))
        elif o == '"':
            out += [pikepdf.ContentStreamInstruction([a[0]], pikepdf.Operator("Tw")),
                    pikepdf.ContentStreamInstruction([a[1]], pikepdf.Operator("Tc")),
                    pikepdf.ContentStreamInstruction([], pikepdf.Operator("T*"))]
        out.append(pikepdf.ContentStreamInstruction([pikepdf.Array(arr)], pikepdf.Operator("TJ")))
    return out


def _std_font_dict(pdf, name):
    return pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name("/" + name), Encoding=pikepdf.Name.WinAnsiEncoding))


def _own_resources(page):
    """The page's resources as its own copy (they may be shared with other pages)."""
    res = page.obj.get("/Resources")
    res = pikepdf.Dictionary(res) if isinstance(res, pikepdf.Dictionary) else pikepdf.Dictionary()
    for k in ("/Font", "/XObject", "/ExtGState"):
        if isinstance(res.get(k), pikepdf.Dictionary):
            res[k] = pikepdf.Dictionary(res[k])
    page.obj.Resources = res
    return res


def wrap_words(text, width_of, max_w):
    """Greedy word wrap. width_of(str) -> points."""
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            trial = (cur + " " + w) if cur else w
            if cur and width_of(trial) > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
    return lines


def write_page_ops(pdf, page, ops):
    """Replace the page's content with these operators, starting and ending in the
    default coordinate system (so anything added at the end is in page coordinates)."""
    body = pikepdf.unparse_content_stream(ops)
    page.obj.Contents = pdf.make_stream(body)


def edit_paragraph(pdf, page, para, new_text, size=None):
    """Replace a paragraph's text. Returns a note for the user (or "")."""
    ops, glyphs, images, fonts = read_page_content(pdf, page)
    remove = set(para["ops"])
    if any(i >= len(ops) or str(ops[i].operator) not in ("Tj", "TJ", "'", '"') for i in remove):
        raise ValueError("The page changed since it was read")
    res = _own_resources(page)
    drop = {}
    for op_i, seq in para["glyph_ids"]:
        drop.setdefault(op_i, set()).add(seq)
    for op_i, seqs in drop.items():
        # An op drawn only for this paragraph goes entirely (with any spaces or
        # unreadable glyphs); one shared with other text loses just these glyphs.
        rest = [g for g in glyphs if g["op"] == op_i and g["seq"] not in seqs]
        if all(g["ch"] == "�" or not g["ch"].strip() for g in rest):
            seqs.update(g["seq"] for g in rest)
    new_ops = _strip_glyphs(ops, res.get("/Font", pikepdf.Dictionary()), drop)
    note = ""
    if new_text.strip():
        fi = fonts.get(para["font"]) or FontInfo(res.Font[para["font"]])
        size = size or para["size"]
        codes = fi.encode(new_text.replace("\n", " "))
        if codes is not None and not para["mixed"]:
            font_name = para["font"]
            width_of = lambda s: sum(fi.width(c) for c in fi.encode(s) or []) / 1000 * size  # noqa: E731

            def encode(s):
                return pikepdf.String(b"".join(fi.encode(s)))
        else:
            # The same font from this computer, if it's installed (only the needed
            # letters are embedded); otherwise the closest standard PDF font.
            sysfont = embed_system_font(pdf, fi.base, new_text) if _cp1252_ok(new_text) else None
            if sysfont is not None:
                fdict, widths, label = sysfont
                n = 1
                while f"/TKSys{n}" in res.Font:
                    n += 1
                font_name = f"/TKSys{n}"
                res.Font[font_name] = fdict
                width_of = lambda s: sum(widths.get(ch, 0.5) for ch in s) * size  # noqa: E731
                note = ("" if not para["mixed"] else "This paragraph mixed several fonts; it now uses one.")
            else:
                std = fi.standard()
                font_name = "/TKEdit" + std.replace("-", "")
                res.Font[font_name] = _std_font_dict(pdf, std)
                table = STD_WIDTHS[std]
                width_of = lambda s: sum(table[ord(ch) - 32] if 32 <= ord(ch) < 127 else 556 for ch in s) / 1000 * size  # noqa: E731
                label = std.split("-")[0]
                note = (f"The original font isn't fully in this PDF or on this computer, so this paragraph now "
                        f"uses {label}." if codes is None else "This paragraph mixed several fonts; it now uses one.")

            def encode(s):
                return pikepdf.String(s.encode("cp1252", "replace"))
        x, y = para["start"]
        max_w = max(para["width"], width_of("M" * 8)) * 1.01 + 0.5  # a hair of slack for rounding
        if len(para["lines"]) == 1:
            # A single line (a heading, a label) may grow toward the page's right
            # margin; only multi-line paragraphs keep their column width.
            mb = [float(v) for v in page.mediabox]
            left = x - min(mb[0], mb[2])
            room = max(mb[0], mb[2]) - x - max(18.0, min(left, 72.0))
            max_w = max(max_w, room)
        lines = wrap_words(new_text, width_of, max_w)
        lead = para["leading"] if len(para["lines"]) > 1 else size * 1.2
        r, g, b = para["fill"]
        new_ops.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("q")))
        tail = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("BT")),
                pikepdf.ContentStreamInstruction([r, g, b], pikepdf.Operator("rg")),
                pikepdf.ContentStreamInstruction([pikepdf.Name(font_name), size], pikepdf.Operator("Tf")),
                pikepdf.ContentStreamInstruction([1, 0, 0, 1, x, y], pikepdf.Operator("Tm"))]
        for k, line in enumerate(lines):
            if k:
                tail.append(pikepdf.ContentStreamInstruction([0, -lead], pikepdf.Operator("Td")))
            tail.append(pikepdf.ContentStreamInstruction([encode(line)], pikepdf.Operator("Tj")))
        tail += [pikepdf.ContentStreamInstruction([], pikepdf.Operator("ET")),
                 pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q"))]
        if len(lines) > len(para["lines"]):
            note = (note + " " if note else "") + "The new text is longer, so it runs below the original space."
        new_ops += tail
    _write_with_reset(pdf, page, new_ops, len(new_ops) - (len(tail) + 1 if new_text.strip() else 0))
    return note


def _write_with_reset(pdf, page, ops, n_original):
    """Write ops, wrapping the original part in q/Q so additions start in page coordinates."""
    q = pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))
    Q = pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q"))
    out = [q] + ops[:n_original] + [Q] + ops[n_original:]
    write_page_ops(pdf, page, out)


def add_text(pdf, page, x, y, text, size=12, font="Helvetica", fill=(0, 0, 0)):
    """New text (real page text, not a comment) with its top-left near (x, y), page coordinates."""
    ops = list(pikepdf.parse_content_stream(page))
    res = _own_resources(page)
    if "/Font" not in res:
        res.Font = pikepdf.Dictionary()
    name = "/TKEdit" + font.replace("-", "")
    res.Font[name] = _std_font_dict(pdf, font)
    lines = text.split("\n")
    tail = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("BT")),
            pikepdf.ContentStreamInstruction(list(fill), pikepdf.Operator("rg")),
            pikepdf.ContentStreamInstruction([pikepdf.Name(name), size], pikepdf.Operator("Tf")),
            pikepdf.ContentStreamInstruction([1, 0, 0, 1, x, y - size * 0.8], pikepdf.Operator("Tm"))]
    for k, line in enumerate(lines):
        if k:
            tail.append(pikepdf.ContentStreamInstruction([0, -size * 1.2], pikepdf.Operator("Td")))
        tail.append(pikepdf.ContentStreamInstruction([pikepdf.String(line.encode("cp1252", "replace"))],
                                                     pikepdf.Operator("Tj")))
    tail.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("ET")))
    _write_with_reset(pdf, page, ops + [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))] + tail +
                      [pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q"))], len(ops))


def edit_image(pdf, page, image, new_box=None, delete=False, replace_with=None):
    """Move/resize an image to new_box (page coordinates), delete it, or swap its picture."""
    ops = list(pikepdf.parse_content_stream(page))
    i = image["op"]
    if not (i < len(ops) and str(ops[i].operator) == "Do" and str(ops[i].operands[0]) == image["name"]):
        raise ValueError("The page changed since it was read")
    res = _own_resources(page)
    if delete:
        ops[i] = pikepdf.ContentStreamInstruction([], pikepdf.Operator("n"))  # a no-op outside a path
        del ops[i]
        write_page_ops(pdf, page, ops)
        return
    name = image["name"]
    old = image_box(image["ctm"])
    box = new_box or old
    if replace_with is not None:
        xo = _image_xobject(pdf, replace_with)
        n = 1
        while f"/TKImg{n}" in res.XObject:
            n += 1
        name = f"/TKImg{n}"
        res.XObject[name] = xo
        # Fit the new picture inside the box, keeping its proportions.
        bw, bh = box[2] - box[0], box[3] - box[1]
        iw, ih = replace_with.size
        s = min(bw / iw, bh / ih)
        w, h = iw * s, ih * s
        box = (box[0] + (bw - w) / 2, box[1] + (bh - h) / 2, box[0] + (bw + w) / 2, box[1] + (bh + h) / 2)
        # Draw it upright in its box, whatever the old placement was.
        ctm_new = (box[2] - box[0], 0, 0, box[3] - box[1], box[0], box[1])
    else:
        # The same picture, scaled and moved so its box becomes `box`.
        ow, oh = old[2] - old[0], old[3] - old[1]
        sx = (box[2] - box[0]) / ow if ow else 1
        sy = (box[3] - box[1]) / oh if oh else 1
        T = (sx, 0, 0, sy, box[0] - old[0] * sx, box[1] - old[1] * sy)
        ctm_new = mat_mul(image["ctm"], T)
    # cm X sets CTM := X × CTM, so X = CTM_new × CTM⁻¹ makes the image land in its new place.
    X = mat_mul(ctm_new, mat_inv(image["ctm"]))
    ops[i:i + 1] = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q")),
                    pikepdf.ContentStreamInstruction([round(v, 6) for v in X], pikepdf.Operator("cm")),
                    pikepdf.ContentStreamInstruction([pikepdf.Name(name)], pikepdf.Operator("Do")),
                    pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q"))]
    write_page_ops(pdf, page, ops)


def add_image(pdf, page, im, box):
    ops = list(pikepdf.parse_content_stream(page))
    res = _own_resources(page)
    if "/XObject" not in res:
        res.XObject = pikepdf.Dictionary()
    n = 1
    while f"/TKImg{n}" in res.XObject:
        n += 1
    res.XObject[f"/TKImg{n}"] = _image_xobject(pdf, im)
    tail = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q")),
            pikepdf.ContentStreamInstruction([box[2] - box[0], 0, 0, box[3] - box[1], box[0], box[1]], pikepdf.Operator("cm")),
            pikepdf.ContentStreamInstruction([pikepdf.Name(f"/TKImg{n}")], pikepdf.Operator("Do")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q"))]
    _write_with_reset(pdf, page, ops + tail, len(ops))


def _image_xobject(pdf, im):
    """An image as a PDF image (JPEG for photos, lossless with transparency otherwise)."""
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        rgba = im.convert("RGBA")
        xo = pdf.make_stream(zlib.compress(rgba.convert("RGB").tobytes(), 6), Type=pikepdf.Name.XObject,
                             Subtype=pikepdf.Name.Image, Width=im.width, Height=im.height,
                             ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        xo.SMask = pdf.make_stream(zlib.compress(rgba.getchannel("A").tobytes(), 6), Type=pikepdf.Name.XObject,
                                   Subtype=pikepdf.Name.Image, Width=im.width, Height=im.height,
                                   ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        return xo
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return pdf.make_stream(buf.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                           Width=im.width, Height=im.height, ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                           Filter=pikepdf.Name.DCTDecode)


# ---- the same font from this computer ---------------------------------------------- #
_SYS_FONTS = None


def _norm(name):
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _cp1252_ok(text):
    try:
        text.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def system_fonts():
    """Installed TrueType fonts: {normalised PostScript name or family+style: (path, index)}.
    Scanned once (reading only each font's name table)."""
    global _SYS_FONTS
    if _SYS_FONTS is not None:
        return _SYS_FONTS
    from fontTools.ttLib import TTCollection, TTFont
    if sys.platform == "win32":
        dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
                os.path.expanduser("~/Library/Fonts")]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts")]
    index = {}
    for d in dirs:
        for root_dir, _, files in os.walk(d):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in (".ttf", ".ttc"):
                    continue
                path = os.path.join(root_dir, fn)
                try:
                    fonts = TTCollection(path, lazy=True).fonts if ext == ".ttc" else [TTFont(path, lazy=True)]
                except Exception:
                    continue
                for k, f in enumerate(fonts):
                    try:
                        if "glyf" not in f:
                            continue  # CFF outlines: not embeddable as a simple TrueType font
                        names = f["name"]
                        ps = names.getDebugName(6) or ""
                        fam = names.getDebugName(16) or names.getDebugName(1) or ""
                        sty = names.getDebugName(17) or names.getDebugName(2) or "Regular"
                        keys = [_norm(ps), _norm(fam + sty)]
                        if _norm(sty) == "regular":
                            keys.append(_norm(fam))
                        for key in keys:
                            if key and key not in index:
                                index[key] = (path, k)
                    except Exception:
                        continue
    _SYS_FONTS = index
    return index


def find_system_font(base):
    """The installed font matching a PDF font name like 'BAAAAA+TimesNewRomanPS-BoldMT'."""
    name = base.split("+", 1)[-1]
    fonts = system_fonts()
    for cand in (name, name.replace(",", "-"), name.replace("PSMT", "").replace("MT", "").replace("PS", "")):
        hit = fonts.get(_norm(cand))
        if hit:
            return hit
    return None


def _winansi_tounicode():
    """A ToUnicode CMap for WinAnsi codes 32-255, so copied/searched text is right."""
    pairs = []
    for code in range(32, 256):
        try:
            ch = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            continue
        pairs.append(f"<{code:02X}> <{ord(ch):04X}>")
    body = []
    for k in range(0, len(pairs), 100):
        chunk = pairs[k:k + 100]
        body.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar")
    return ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n" + "\n".join(body) +
            "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n").encode("ascii")


def embed_system_font(pdf, base, text):
    """Embed just the letters `text` needs from the installed font matching `base`.
    Returns (font dictionary, {char: width in em}, PostScript name), or None if
    there's no matching font or its licence doesn't allow embedding."""
    hit = find_system_font(base)
    if hit is None:
        return None
    try:
        import logging
        import random
        from fontTools import subset
        logging.getLogger("fontTools").setLevel(logging.ERROR)
        from fontTools.ttLib import TTFont
        font = TTFont(hit[0], fontNumber=hit[1])
        fs_type = font["OS/2"].fsType if "OS/2" in font else 0
        if fs_type & 0x0002 or fs_type & 0x0200:
            return None  # "restricted licence" or "bitmap only": embedding not allowed
        upm = font["head"].unitsPerEm
        opts = subset.Options()
        opts.layout_features = []
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        opts.recalc_bounds = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=[ord(c) for c in set(text) | {" "}])
        sub.subset(font)
        cmap = font.getBestCmap() or {}
        hmtx = font["hmtx"]
        widths, wlist = {}, []
        for code in range(32, 256):
            try:
                ch = bytes([code]).decode("cp1252")
            except UnicodeDecodeError:
                wlist.append(0)
                continue
            g = cmap.get(ord(ch))
            w = hmtx[g][0] * 1000 / upm if g else 0
            wlist.append(round(w))
            if g:
                widths[ch] = w / 1000
        buf = io.BytesIO()
        font.save(buf)
        data = buf.getvalue()
        head, hhea = font["head"], font["hhea"]
        os2 = font["OS/2"] if "OS/2" in font else None
        italic = font["post"].italicAngle if "post" in font else 0
        ps = (font["name"].getDebugName(6) or base.split("+", 1)[-1]).replace(" ", "")
        tag = "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(6))
        sc = 1000 / upm
        desc = pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name(f"/{tag}+{ps}"), Flags=32 | (64 if italic else 0),
            FontBBox=[round(head.xMin * sc), round(head.yMin * sc), round(head.xMax * sc), round(head.yMax * sc)],
            ItalicAngle=float(italic), Ascent=round(hhea.ascent * sc), Descent=round(hhea.descent * sc),
            CapHeight=round((getattr(os2, "sCapHeight", 0) or hhea.ascent * 0.7) * sc), StemV=80,
            FontFile2=pdf.make_stream(data, Length1=len(data)))
        fdict = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name(f"/{tag}+{ps}"),
            FirstChar=32, LastChar=255, Widths=wlist, Encoding=pikepdf.Name.WinAnsiEncoding,
            FontDescriptor=pdf.make_indirect(desc)))
        fdict.ToUnicode = pdf.make_stream(_winansi_tounicode())
        return fdict, widths, ps
    except Exception as e:
        log_error(f"Embed font {base}: {type(e).__name__}: {e}")
        return None


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
    def __init__(self, path, data, password, boxes, name=None):
        self.path, self.data, self.password = path, data, password
        self.boxes = boxes  # each page's (visible box, own rotation)
        self.pages = len(boxes)
        self.name = name or os.path.basename(path)
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
        tk.Label(self.tip, text=self.text, bg=UI["appbar"], fg="#ffffff", font=UI["small"], padx=8, pady=4).pack()

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
        self.marks = {}  # page uid -> items placed with Fill & Sign
        self.fill = {}  # doc id -> {form field name: value}
        self.field_cache = {}  # doc id -> its form fields
        self.docinfo = {}  # title, author… for the saved PDF (empty = keep the original's)
        self.new_fields = {}  # page uid -> form fields made with Prepare form
        self.cert_request = None  # a certificate signature to add when saving
        self.sig_cache = {}  # doc id -> its existing certificate signatures
        self.mark_seq = 0
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
            "header": tk.StringVar(value=""),
            "footer": tk.StringVar(value=""),
            "hf_align": tk.StringVar(value="Centre"),
            "bates_prefix": tk.StringVar(value=""),
            "bates_start": tk.StringVar(value="1"),
            "bates_digits": tk.StringVar(value="6"),
            "flatten": tk.BooleanVar(value=False),
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
        mark.create_polygon(17, 2, 17, 9, 24, 9, fill="#cfc6ff", outline="")
        for i, yy in enumerate((14, 18, 22)):
            mark.create_line(7, yy, 20 if i < 2 else 15, yy, fill=UI["accent"], width=2)
        mark.pack(side="left", padx=(14, 8))
        tk.Label(bar, text="PDF Toolkit", bg=UI["appbar"], fg="#ffffff", font=UI["title"]).pack(side="left", padx=(0, 14))
        self.mode_btns = []
        for label, cmd, tip in (("Organize", None, "Arrange, rotate, crop and combine pages"),
                                ("View", lambda: self.open_editor("view"), "Read, search and copy text"),
                                ("Edit PDF", lambda: self.open_editor("edit"), "Change the pages' own text and pictures"),
                                ("Comment", lambda: self.open_editor("comment"), "Highlight, notes, shapes, drawing"),
                                ("Fill & Sign", lambda: self.open_editor("fill"), "Fill in forms and sign"),
                                ("Prepare form", lambda: self.open_editor("form"), "Add fillable fields"),
                                ("Compare", lambda: CompareWindow(self), "Compare two PDFs")):
            b = FlatButton(bar, label, cmd or (lambda: None), UI["appbar"], fg=UI["bar_text"], hover=UI["bar_hover"],
                           font=UI["bold"], padx=10, pady=12, tip=tip)
            b.pack(side="left")
            if cmd is None:
                b.colors.update(bg=UI["bar_tab"], fg="#ffffff")
                b.config(bg=UI["bar_tab"], fg="#ffffff", cursor="")
            else:
                self.mode_btns.append(b)
        self.save_btn = FlatButton(bar, "  Save as PDF  ", self.save_all, UI["appbar"], primary=True,
                                   font=UI["bold"], pady=6, tip="Save every page in the workspace as one PDF (Ctrl+S)")
        self.save_btn.pack(side="right", padx=14, pady=8)
        self.open_btn = FlatButton(bar, "＋  Add files", self.add_files, UI["appbar"], fg="#ffffff",
                                   hover=UI["bar_hover"], pady=6, tip="Add PDFs or images (or drag them in)")
        self.open_btn.pack(side="right", pady=8)
        self.print_btn = FlatButton(bar, "🖨  Print", self.print_pdf, UI["appbar"], fg="#ffffff", hover=UI["bar_hover"],
                                    pady=6, tip="Opens the finished PDF in your PDF reader to print")
        self.print_btn.pack(side="right", pady=8)

        body = tk.Frame(root, bg=UI["canvas"])
        body.pack(fill="both", expand=True)

        # Left: the tools panel.
        outer = tk.Frame(body, bg=UI["panel"], width=300, highlightthickness=1, highlightbackground=UI["border"])
        outer.pack(side="left", fill="y")
        outer.pack_propagate(False)
        # The panel scrolls when the window is too short to show every tool.
        sc = tk.Canvas(outer, bg=UI["panel"], highlightthickness=0, width=284)
        ssb = ttk.Scrollbar(outer, orient="vertical", command=sc.yview)
        sc.configure(yscrollcommand=ssb.set)
        sc.pack(side="left", fill="both", expand=True)
        side = tk.Frame(sc, bg=UI["panel"])
        sc.create_window(0, 0, window=side, anchor="nw", width=284)

        def fit(_e=None):
            sc.configure(scrollregion=(0, 0, 284, side.winfo_reqheight() + 10))
            if side.winfo_reqheight() > sc.winfo_height():
                ssb.pack(side="right", fill="y")
            else:
                ssb.pack_forget()
        side.bind("<Configure>", fit)
        sc.bind("<Configure>", fit)

        def wheel(e):
            if side.winfo_reqheight() > sc.winfo_height():
                sc.yview_scroll(-1 if (getattr(e, "delta", 0) > 0 or e.num == 4) else 1, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            sc.bind_all(seq, lambda e: wheel(e) if str(e.widget).startswith(str(outer)) else None, add="+")
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
        ttk.Label(f, text="Off = saved without a password (this also removes one from a PDF you opened).",
                  style="Hint.TLabel", wraplength=205).pack(anchor="w", pady=(2, 0))
        f = card("Header & footer", "▤")
        for label, key in (("Header", "header"), ("Footer", "footer")):
            row = tk.Frame(f, bg=UI["panel"])
            row.pack(anchor="w", pady=1)
            ttk.Label(row, text=label, style="Side.TLabel", width=7).pack(side="left")
            ttk.Entry(row, textvariable=self.v[key], width=23).pack(side="left")
        row = tk.Frame(f, bg=UI["panel"])
        row.pack(anchor="w", pady=1)
        ttk.Label(row, text="Align", style="Side.TLabel", width=7).pack(side="left")
        ttk.Combobox(row, values=HF_ALIGN, textvariable=self.v["hf_align"], state="readonly", width=8).pack(side="left")
        ttk.Label(f, text="Use " + HF_TOKENS + ". For example: {file} — page {page} of {pages}",
                  style="Hint.TLabel", wraplength=205).pack(anchor="w", pady=(2, 0))
        row = tk.Frame(f, bg=UI["panel"])
        row.pack(anchor="w", pady=(4, 0))
        ttk.Label(row, text="Bates", style="Side.TLabel", width=7).pack(side="left")
        ttk.Entry(row, textvariable=self.v["bates_prefix"], width=8).pack(side="left")
        ttk.Label(row, text=" from ", style="Side.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.v["bates_start"], width=6).pack(side="left")
        ttk.Label(f, text="Prefix and first number for {bates}: ACME- from 1 gives ACME-000001, ACME-000002…",
                  style="Hint.TLabel", wraplength=205).pack(anchor="w", pady=(2, 0))
        f = card("Forms & signatures", "✍")
        ttk.Checkbutton(f, text="Flatten when saving", variable=self.v["flatten"],
                        style="Side.TCheckbutton").pack(anchor="w")
        ttk.Label(f, text="Makes filled-in fields, signatures and comments part of the page, so they can't be "
                          "changed. Off: asks each time something was filled in.", style="Hint.TLabel",
                  wraplength=205).pack(anchor="w")
        f = card("Images to PDF", "📥")
        ttk.Label(f, text="Added images become pages:", style="Side.TLabel").pack(anchor="w")
        ttk.Combobox(f, values=IMAGE_PAGE_SIZES, textvariable=self.v["image_size"], state="readonly",
                     width=29).pack(anchor="w", pady=(2, 0))

        tk.Frame(side, bg=UI["border"], height=1).pack(fill="x", padx=16, pady=(16, 8))
        self.action_btns = []
        for text, cmd, tip in (("ⓘ  Document properties…", lambda: PropertiesDialog(self), "Title, author, subject, keywords"),
                               ("✂  Split into several PDFs…", self.split, "Every page, every N pages, or at chosen pages"),
                               ("📄  Save selected pages…", self.extract, "Save only the selected pages as a new PDF"),
                               ("🖼  Export pages as images…", self.export_images, "PNG or JPG, at the resolution you choose")):
            btn = FlatButton(side, text, cmd, UI["panel"], hover=UI["hover"], anchor="w", tip=tip)
            btn.pack(fill="x", padx=10)
            self.action_btns.append(btn)
        self.extract_btn = self.action_btns[2]

        # Right: toolbar + page grid + status bar.
        main = tk.Frame(body, bg=UI["canvas"])
        main.pack(side="left", fill="both", expand=True)
        tools = tk.Frame(main, bg=UI["toolbar"], height=44, highlightthickness=1, highlightbackground=UI["border"])
        tools.pack(fill="x")
        tools.pack_propagate(False)
        self.doc_lbl = tk.Label(tools, text="", bg=UI["toolbar"], fg=UI["text"], font=UI["bold"])
        self.doc_lbl.pack(side="left", padx=(14, 10))
        self.tool_btns = []
        for text, cmd, tip in (("⟲", lambda: self.rotate(-90), "Rotate left"),
                               ("⟳", lambda: self.rotate(90), "Rotate right"),
                               ("⧉", self.duplicate, "Duplicate (Ctrl+D)"),
                               ("🗑", self.delete, "Delete (Del)"),
                               ("📄", self.insert_blank, "Insert a blank page after the selection"),
                               ("⛶", self.crop, "Crop the selected pages, or trim their white margins"),
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
        zoom_icon = tk.Label(tools, text="🔍", bg=UI["toolbar"], fg=UI["muted"], font=UI["font"])
        zoom_icon.pack(side="right", padx=(6, 0))
        Tooltip(zoom_icon, "Thumbnail size")
        zoom = ttk.Scale(tools, from_=90, to=260, variable=self.zoom, orient="horizontal", length=100,
                         style="Zoom.Horizontal.TScale", command=lambda v: self._on_zoom())
        zoom.pack(side="right", padx=(6, 0))
        Tooltip(zoom, "Thumbnail size")

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
        support_link(status, bg=UI["statusbar"], font=(_FAMILY, 9, "underline")).pack(side="right", padx=(0, 16))

        grid = tk.Frame(main, bg=UI["canvas"])
        grid.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(grid, bg=UI["canvas"], highlightthickness=0, takefocus=1)
        sb = ttk.Scrollbar(grid, orient="vertical", command=self._yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.empty = tk.Frame(self.canvas, bg=UI["canvas"])
        drop = tk.Canvas(self.empty, width=380, height=200, bg=UI["canvas"], highlightthickness=0)
        drop.create_rectangle(4, 4, 376, 196, outline="#a99fd0", dash=(6, 4), width=2)
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
        tk.Frame(side, bg=UI["panel"], height=14).pack()

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
                           ("Duplicate", self.duplicate), ("Delete", self.delete), ("Crop…", self.crop),
                           ("Insert blank page after", self.insert_blank), (None, None),
                           ("Fill & Sign this page", self.fill_sign), ("Comment on this page", lambda: self.open_editor("comment")),
                           ("Open in the editor", self.view_selected), ("Save selected pages as PDF…", self.extract),
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
                    page = self._pdfium(doc)[key[1]]
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
        crop = page.get("crop")
        pkey = key + (page["rot"], size, crop)
        if pkey not in self.photos:
            shown = im
            if crop:
                # The crop in the thumbnail's own orientation (the page's rotation, before ours).
                doc = self.docs[page["doc"]]
                box, base = doc.boxes[page["index"]]
                W, H = shown_size(box, base)
                pts = [to_shown(box, base, x, y) for x, y in ((crop[0], crop[1]), (crop[2], crop[3]))]
                X0, X1 = sorted(q[0] for q in pts)
                Y0, Y1 = sorted(q[1] for q in pts)
                sx, sy = im.width / W, im.height / H
                shown = im.crop((int(X0 * sx), int((H - Y1) * sy), int(X1 * sx), int((H - Y0) * sy)))
            shown = shown.rotate(-page["rot"], expand=True) if page["rot"] else shown.copy()  # PIL turns anticlockwise
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
        for off, col in ((4, "#d3cce6"), (2, "#bfb6da")):
            it["extra"].append(c.create_rectangle(cx - pw / 2 + off, cy - ph / 2 + off, cx + pw / 2 + off,
                                                  cy + ph / 2 + off, fill=col, outline=""))
        it["img"] = c.create_image(cx, cy, image=photo)
        it["extra"].append(c.create_rectangle(cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2,
                                              outline="#9d93bf"))
        if self._has_fill(page):  # a badge on pages that were filled in or signed
            bx, by = cx + pw / 2 - 4, cy - ph / 2 + 4
            it["extra"].append(c.create_oval(bx - 12, by - 12, bx + 12, by + 12, fill=UI["accent"], outline="#ffffff",
                                             width=2))
            it["extra"].append(c.create_text(bx, by, text="✍", fill="#ffffff", font=UI["small"]))
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
        self.doc_lbl.config(text=(f"{label} — {n} page{'s' * (n != 1)}" + (" •" if self.dirty else ""))
                            if n else "Organize pages")
        self.root.title(f"PDF Toolkit — {label}" if n else "PDF Toolkit")
        idle = not self.busy
        for btn in self.tool_btns + [self.open_btn, self.clear_btn]:
            btn.set_enabled(idle)
        self.undo_btn.set_enabled(bool(self.undo_stack) and idle)
        for b in self.mode_btns[:-1]:  # Compare works without open pages
            b.set_enabled(bool(n) and idle)
        self.redo_btn.set_enabled(bool(self.redo_stack) and idle)
        for btn in self.action_btns + [self.save_btn, self.print_btn]:
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
            self.open_editor("view")

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
        self.undo_stack.append(self._snapshot())
        del self.undo_stack[:-200]
        self.redo_stack.clear()
        self.pages = new_pages
        self.selected &= {p["uid"] for p in self.pages}
        self.dirty = True
        self._redraw()
        self._status(message)

    def _snapshot(self):
        return (list(self.pages), set(self.selected), self._fill_state())

    def _restore(self, snap):
        self.pages, self.selected, (marks, fill, fields, cert) = snap[0], snap[1], snap[2]
        self.marks = {u: [dict(m) for m in ms] for u, ms in marks.items()}
        self.fill = {d: dict(v) for d, v in fill.items()}
        self.new_fields = {u: [dict(f) for f in fs] for u, fs in fields.items()}
        self.cert_request = dict(cert) if cert else None

    def _fill_state(self):
        return ({u: [dict(m) for m in ms] for u, ms in self.marks.items()}, {d: dict(v) for d, v in self.fill.items()},
                {u: [dict(f) for f in fs] for u, fs in self.new_fields.items()},
                dict(self.cert_request) if self.cert_request else None)

    def _fill_done(self, before):
        """Fill & Sign closed: one undo step for everything done in it."""
        after = self._fill_state()
        strip = lambda st: ({u: [{k: v for k, v in m.items() if k != "_photos"} for m in ms] for u, ms in st[0].items() if ms},
                            {d: {k: v for k, v in vals.items()} for d, vals in st[1].items() if vals},
                            {u: fs for u, fs in st[2].items() if fs},
                            (st[3]["uid"], st[3]["rect"], st[3]["path"]) if st[3] else None)  # noqa: E731
        if strip(after) != strip(before):
            self.undo_stack.append((list(self.pages), set(self.selected), before))
            self.redo_stack.clear()
            self.dirty = True
            n = sum(len(v) for v in self.marks.values())
            f = sum(len(v) for v in self.fill.values())
            nf = sum(len(v) for v in self.new_fields.values())
            parts = [f"{n} comment{'s' * (n != 1)} and placed item{'s' * (n != 1)}"]
            if f:
                parts.append(f"{f} filled-in field{'s' * (f != 1)}")
            if nf:
                parts.append(f"{nf} new form field{'s' * (nf != 1)}")
            if self.cert_request:
                parts.append("a certificate signature")
            self._status("Editor: " + ", ".join(parts) + ". They're added when you save.")
        self._redraw()

    def _has_fill(self, page):
        if self.marks.get(page["uid"]) or self.new_fields.get(page["uid"]):
            return True
        if self.cert_request and self.cert_request["uid"] == page["uid"]:
            return True
        vals = self.fill.get(page["doc"])
        return bool(vals) and any(f["page"] == page["index"] and f["name"] in vals
                                  for f in self.field_cache.get(page["doc"], []))

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
                if self.marks.get(p["uid"]):  # a copy keeps what was placed on the page
                    self.marks[c["uid"]] = [dict(m, id=self._new_mark_id()) for m in self.marks[p["uid"]]]
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
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())
        self.dirty = True
        self._redraw()
        self._status("Undone.")

    def redo(self):
        if self.busy or not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())
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
        self.marks, self.fill, self.field_cache, self.docinfo = {}, {}, {}, {}
        self.new_fields, self.cert_request, self.sig_cache = {}, None, {}
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


    # ---- page geometry, crop, blank pages ------------------------------- #
    def _page_box(self, p):
        return self.docs[p["doc"]].boxes[p["index"]][0]

    def _total_rot(self, p):
        return (self.docs[p["doc"]].boxes[p["index"]][1] + p["rot"]) % 360

    def _shown_margins_to_box(self, p, left, bottom, right, top):
        """Margins (points) on the page as shown -> a crop box in the page's own coordinates."""
        box, R = self._page_box(p), self._total_rot(p)
        W, H = shown_size(box, R)
        a = to_page(box, R, left, bottom)
        b = to_page(box, R, W - right, H - top)
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

    def _render_crop(self, p):
        """The page's crop as PDFium wants it: (left, bottom, right, top) to cut off, as shown."""
        crop = p.get("crop")
        if not crop:
            return (0, 0, 0, 0)
        box, R = self._page_box(p), self._total_rot(p)
        W, H = shown_size(box, R)
        pts = [to_shown(box, R, x, y) for x, y in ((crop[0], crop[1]), (crop[2], crop[3]))]
        X0, X1 = sorted(q[0] for q in pts)
        Y0, Y1 = sorted(q[1] for q in pts)
        return (X0, Y0, W - X1, H - Y1)

    def _set_crops(self, crops, message):
        new = []
        for p in self.pages:
            if p["uid"] in crops:
                q = {k: v for k, v in p.items() if k != "crop"}
                if crops[p["uid"]]:
                    q["crop"] = crops[p["uid"]]
                new.append(q)
            else:
                new.append(p)
        self._change(new, message)

    def crop(self):
        if self.busy or not self._targets():
            return
        CropDialog(self, [p for p in self.pages if p["uid"] in self.selected])

    def insert_blank(self):
        if self.busy:
            return
        slot = self._insert_slot()
        ref = self.pages[slot - 1] if slot and self.pages else None
        if ref is not None:  # the same size as the page before it, as it's shown
            w, h = shown_size(self._page_box(ref), self._total_rot(ref))
        else:
            w, h = PAPER["Letter"]
        data = blank_pdf(round(w, 2), round(h, 2))
        did = self.next_doc
        self.next_doc += 1
        self.docs[did] = Doc("", data, "", open_pdf_bytes(data), name="Blank page")
        p = dict(uid=self.next_uid, doc=did, index=0, rot=0)
        self.next_uid += 1
        self._change(self.pages[:slot] + [p] + self.pages[slot:], "Inserted a blank page.")
        self.selected = {p["uid"]}
        self._refresh_selection()

    def _pdfium(self, doc):
        """The document opened in PDFium, with form fields drawn. Call with PDFIUM_LOCK held."""
        if doc.render is None:
            doc.render = pdfium.PdfDocument(doc.data, password=doc.password or None)
            try:
                doc.render.init_forms()
            except Exception:
                pass  # forms just won't be drawn
        return doc.render

    # ---- Fill & Sign --------------------------------------------------- #
    def _fields(self, doc_id):
        if doc_id not in self.field_cache:
            doc = self.docs[doc_id]
            try:
                with pikepdf.open(io.BytesIO(doc.data), password=doc.password) as pdf:
                    self.field_cache[doc_id] = read_fields(pdf)
            except Exception as e:
                log_error(f"Read fields of {doc.name}: {type(e).__name__}: {e}")
                self.field_cache[doc_id] = []
        return self.field_cache[doc_id]

    def _new_mark_id(self):
        self.mark_seq += 1
        return self.mark_seq

    def fill_sign(self):
        self.open_editor("fill")

    def open_editor(self, mode):
        if self.busy or not self.pages:
            return
        ks = [k for k, p in enumerate(self.pages) if p["uid"] in self.selected]
        PageEditor(self, ks[0] if ks else 0, mode)

    def edit_page_content(self, k, action, message):
        """Change workspace page k's own content: action(pdf, page) runs on a copy of
        the page's document, and the page then shows that copy (one undo step, and
        the original file is untouched). Returns what action returns."""
        p = self.pages[k]
        doc = self.docs[p["doc"]]
        with pikepdf.open(io.BytesIO(doc.data), password=doc.password) as pdf:
            result = action(pdf, pdf.pages[p["index"]])
            buf = io.BytesIO()
            pdf.save(buf)
        data = buf.getvalue()
        did = self.next_doc
        self.next_doc += 1
        self.docs[did] = Doc(doc.path, data, "", open_pdf_bytes(data), name=doc.name)
        if self.fill.get(p["doc"]):
            self.fill[did] = dict(self.fill[p["doc"]])  # filled-in form values carry over
        pages = list(self.pages)
        pages[k] = dict(p, doc=did)
        self._change(pages, message)
        return result

    def _signatures(self, doc_id):
        """The certificate signatures already in a source file (checked once)."""
        if doc_id not in self.sig_cache:
            doc = self.docs[doc_id]
            sigs = []
            if b"/ByteRange" in doc.data:
                try:
                    sigs = check_signatures(doc.data, doc.password or None)
                except Exception as e:
                    log_error(f"Check signatures of {doc.name}: {type(e).__name__}: {e}")
            self.sig_cache[doc_id] = sigs
        return self.sig_cache[doc_id]

    def _confirm_signed(self, pages):
        """Saving makes a new file, so signatures in the originals don't carry over: say so first."""
        signed = sorted({self.docs[p["doc"]].name for p in pages if self._signatures(p["doc"])})
        if not signed:
            return True
        return messagebox.askyesno(
            "Signed document", f"{', '.join(signed)} has a certificate signature. A signature only stays valid in "
            "the exact file that was signed, so the copy you're saving won't carry it (the original file is not "
            "changed).\n\nSave anyway?")

    def _source_info(self):
        first = self.docs[self.pages[0]["doc"]] if self.pages else None
        if not first or not first.data:
            return {}
        try:
            with pikepdf.open(io.BytesIO(first.data), password=first.password) as pdf:
                return {k: str(pdf.docinfo[k]) for k, _ in PropertiesDialog.FIELDS if k in pdf.docinfo}
        except Exception:
            return {}

    def _ask_flatten(self, pages, o):
        """Decide whether to flatten. Asks when something was filled in or placed and the
        Tools panel doesn't already say. Returns True, False, or None to stop."""
        if any(self.new_fields.get(p["uid"]) for p in pages):
            return False  # flattening would turn the new form fields into plain text
        if o["flatten"]:
            return True
        if not any(self.marks.get(p["uid"]) or (self.fill.get(p["doc"]) and self._has_fill(p)) for p in pages):
            return False
        answer = messagebox.askyesnocancel(
            "Flatten the filled-in parts?",
            "Make the filled-in fields, comments, text and signatures part of the page?\n\n"
            "Yes = flatten: nobody can change or move them (best for a signed document).\n"
            "No = keep them editable: they can still be changed in a PDF editor.")
        return answer

    def print_pdf(self):
        """Print: build the finished PDF and open it in the computer's PDF reader."""
        if self.busy or not self.pages:
            return
        o = self._options()
        if o is None:
            return
        o["flatten"] = self._ask_flatten(self.pages, o)
        if o["flatten"] is None:
            return
        o["password"] = None  # a reader asking for a password just to print would be odd
        path = os.path.join(tempfile.gettempdir(), f"PDF Toolkit print {time.strftime('%H%M%S')}.pdf")
        self._run_save([(list(self.pages), path)], o, open_after=True)

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
                            boxes = open_pdf_bytes(data)
                            password = ""
                        except PasswordNeeded:
                            password, boxes = self._ask_password(path, data)
                            if password is None:
                                problems.append(f"{os.path.basename(path)}: skipped (no password)")
                                continue
                    elif ext in IMAGE_EXTS:
                        data, password = image_to_pdf(path, size), ""
                        boxes = open_pdf_bytes(data)
                    else:
                        problems.append(f"{os.path.basename(path)}: not a PDF or an image")
                        continue
                    opened.append(Doc(path, data, password, boxes))
                except Exception as e:
                    log_error(f"Open {path}: {type(e).__name__}: {e}")
                    problems.append(f"{os.path.basename(path)}: {explain_error(e)}")
                self.ui(lambda k=k: self.progress.config(value=(k + 1) / len(paths) * 1000))
            self.ui(lambda: self._added(opened, problems, slot))
        threading.Thread(target=work, daemon=True).start()

    def _ask_password(self, path, data):
        """Ask (on the UI thread) for a PDF's password, up to 3 tries. Returns (password, boxes) or (None, 0)."""
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
                 password=self.v["password"].get() if self.v["protect"].get() else None,
                 header=self.v["header"].get().strip() or None, footer=self.v["footer"].get().strip() or None,
                 align=self.v["hf_align"].get(), flatten=self.v["flatten"].get(), info=dict(self.docinfo))
        o["bates"] = None
        if "{bates}" in (o["header"] or "") + (o["footer"] or ""):
            try:
                start = int(self.v["bates_start"].get())
                if start < 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Check settings", "The first Bates number must be a whole number.")
                return None
            o["bates"] = dict(prefix=self.v["bates_prefix"].get(), start=start,
                              digits=max(1, min(12, int(self.v["bates_digits"].get() or 6))))
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
        if not self._confirm_signed(self.pages):
            return
        o["flatten"] = self._ask_flatten(self.pages, o)
        if o["flatten"] is None:
            return
        path = self._ask_save(self.pages, " (signed)" if self.marks or self.cert_request else " (edited)")
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
        if not self._confirm_signed(pages):
            return
        o["flatten"] = self._ask_flatten(pages, o)
        if o["flatten"] is None:
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
        o["flatten"] = self._ask_flatten(self.pages, o)
        if o["flatten"] is None:
            return
        SplitDialog(self, o)

    def _build_pdf(self, pages, o, check_cancel, opened, first_number=0, file_stem=""):
        """Assemble the pages into a new PDF and apply everything. Returns a pikepdf.Pdf."""
        out = pikepdf.new()
        forms, used = [], []
        filled = False
        for p in pages:
            check_cancel()
            src = opened.get(p["doc"])
            if src is None:
                doc = self.docs[p["doc"]]
                src = opened[p["doc"]] = pikepdf.open(io.BytesIO(doc.data), password=doc.password)
                if self.fill.get(p["doc"]):
                    apply_fill(src, self.fill[p["doc"]])
            filled = filled or bool(self.fill.get(p["doc"]))
            spage = src.pages[p["index"]]
            # The page's own rotation (it may be inherited from the page tree), plus ours.
            base = int(inherited(spage.obj, "/Rotate", 0))
            out.pages.append(spage)
            page = out.pages[-1]
            page.obj.Rotate = (base + p["rot"]) % 360
            if p.get("crop"):
                page.obj.CropBox = pikepdf.Array([round(v, 3) for v in p["crop"]])
            marks = self.marks.get(p["uid"])
            if marks:
                # A new list, so a duplicated page's annotations aren't shared with its twin.
                annots = pikepdf.Array(list(page.obj.get("/Annots", [])))
                for m in marks:
                    make = comment_annotation if m["kind"] in COMMENT_KINDS else mark_annotation
                    annots.append(make(out, m, self._page_box(p)))
                page.obj.Annots = annots
            if self.new_fields.get(p["uid"]):
                add_form_fields(out, page, self.new_fields[p["uid"]], self._total_rot(p))
                filled = True
            used.append((p["doc"], p["index"], len(out.pages) - 1))
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
        if filled and "/AcroForm" in out.Root:
            out.Root.AcroForm.NeedAppearances = True
            try:
                out.generate_appearance_streams()  # so filled values show everywhere, and can be flattened
            except Exception as e:
                log_error(f"Field appearances: {type(e).__name__}: {e}")
        copy_bookmarks(out, used, opened, self.docs)
        # Document properties: the ones set in the app, else the original's title (one file only).
        first = opened.get(pages[0]["doc"])
        if first is not None and "/Title" in first.docinfo and len({p["doc"] for p in pages}) == 1:
            # As text: some files keep the title as an object of its own, which can't be put
            # into another PDF as it is.
            out.docinfo["/Title"] = pikepdf.String(str(first.docinfo["/Title"]))
        for k, v in (o.get("info") or {}).items():
            if v:
                out.docinfo[k] = v
            elif k in out.docinfo:
                del out.docinfo[k]
        if o.get("flatten"):
            out.flatten_annotations("all")
            if "/AcroForm" in out.Root:
                del out.Root["/AcroForm"]
        if o["compress"]:
            compress_images(out, o["compress"]["max_side"], o["compress"]["quality"], check_cancel)
        if o["numbers"] or o["watermark"] or o.get("header") or o.get("footer"):
            stamp_pages(out, o["numbers"], o["place"], o["watermark"], o.get("header"), o.get("footer"),
                        o.get("align", "Centre"), o.get("bates"), first_number, file_stem)
        return out

    def _run_save(self, jobs, o, mark_clean=False, open_after=False):
        """Save each (pages, path) job in the background."""
        self._set_busy(True)
        self._status("Saving…")

        def work():
            opened, written, done_pages = {}, [], 0
            try:
                for k, (pages, path) in enumerate(jobs):
                    self.ui(lambda k=k: (self.progress.config(value=k / len(jobs) * 1000),
                                         self._status(f"Saving {os.path.basename(jobs[k][1])}…")))
                    out = self._build_pdf(pages, o, self._check_cancel, opened, done_pages,
                                          os.path.splitext(os.path.basename(path))[0])
                    done_pages += len(pages)
                    self._check_cancel()
                    kw = dict(compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
                    if o["compress"]:
                        kw["recompress_flate"] = True
                    if o["password"]:
                        kw["encryption"] = pikepdf.Encryption(user=o["password"], owner=o["password"], R=6)
                    part = path + ".part"
                    req = self.cert_request
                    sign_at = next((i for i, p in enumerate(pages) if req and p["uid"] == req["uid"]), None)
                    try:
                        out.save(part, **kw)
                        out.close()
                        if sign_at is not None and not open_after:
                            # The certificate signature goes on last: any change after it would break it.
                            self.ui(lambda: self._status("Adding the certificate signature…"))
                            signed = part + "2"
                            try:
                                sign_pdf_file(part, signed, dict(req, page=sign_at), o["password"])
                                os.replace(signed, part)
                            finally:
                                if os.path.exists(signed):
                                    os.remove(signed)
                        os.replace(part, path)  # Cancel or a crash never leaves half a file
                    finally:
                        if os.path.exists(part):
                            os.remove(part)
                    written.append(path)
                self.ui(lambda: self._saved(written, jobs, mark_clean, open_after))
            except Cancelled:
                self.ui(lambda: self._save_failed("Cancelled." + (f" {len(written)} file(s) were saved." if written else "")))
            except Exception as e:
                log_error(f"Save: {type(e).__name__}: {e}")
                self.ui(lambda e=e: self._save_failed(f"Couldn't save: {explain_error(e)}", error=True))
            finally:
                for src in opened.values():
                    src.close()
        threading.Thread(target=work, daemon=True).start()

    def _saved(self, written, jobs, mark_clean, open_after=False):
        self._set_busy(False)
        if open_after:
            open_file(written[0])
            self._status("Opened in your PDF reader: print from there (Ctrl+P, or ⌘P on a Mac).")
            return
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
                        page = self._pdfium(doc)[p["index"]]
                        im = page.render(scale=dpi / 72, may_draw_forms=True, rotation=p["rot"] % 360,
                                         crop=self._render_crop(p)).to_pil()
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
        self.open_editor("view")

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


class SignatureDialog:
    """Create a signature or initials: draw it, or type it in a handwriting font."""
    def __init__(self, parent, kind, on_done):
        self.kind, self.on_done, self.strokes = kind, on_done, []
        w = self.win = tk.Toplevel(parent)
        w.title("Create your " + ("signature" if kind == "signature" else "initials"))
        w.transient(parent)
        w.resizable(False, False)
        w.configure(bg=UI["panel"])
        f = tk.Frame(w, bg=UI["panel"], padx=16, pady=14)
        f.pack(fill="both", expand=True)
        self.mode = tk.StringVar(value="type")
        tabs = tk.Frame(f, bg=UI["panel"])
        tabs.pack(anchor="w")
        self.tab_btns = {}
        for mode, label in (("type", "Type"), ("draw", "Draw")):
            b = FlatButton(tabs, label, lambda m=mode: self._tab(m), UI["panel"], font=UI["bold"], padx=14)
            b.pack(side="left")
            self.tab_btns[mode] = b
        self.body = tk.Frame(f, bg=UI["panel"])
        self.body.pack(fill="both", pady=(8, 0))
        # Type
        self.type_f = tk.Frame(self.body, bg=UI["panel"])
        self.fonts = handwriting_fonts()
        self.text = tk.StringVar()
        self.font = tk.StringVar(value=next(iter(self.fonts), ""))
        row = tk.Frame(self.type_f, bg=UI["panel"])
        row.pack(fill="x")
        e = ttk.Entry(row, textvariable=self.text, width=34, font=UI["heading"])
        e.pack(side="left")
        if self.fonts:
            ttk.Combobox(row, values=list(self.fonts), textvariable=self.font, state="readonly", width=18).pack(
                side="left", padx=(8, 0))
        self.preview = tk.Canvas(self.type_f, width=560, height=170, bg="#ffffff", highlightthickness=1,
                                 highlightbackground=UI["border"])
        self.preview.pack(pady=(8, 0))
        self.text.trace_add("write", lambda *_: self._update_preview())
        self.font.trace_add("write", lambda *_: self._update_preview())
        # Draw
        self.draw_f = tk.Frame(self.body, bg=UI["panel"])
        self.pad = tk.Canvas(self.draw_f, width=560, height=200, bg="#ffffff", highlightthickness=1,
                             highlightbackground=UI["border"], cursor="pencil")
        self.pad.pack()
        self.pad.create_line(30, 160, 530, 160, fill="#c9ced6", dash=(4, 3))
        self.pad.create_text(40, 175, text="Sign above the line with your mouse, touchpad or pen", anchor="w",
                             fill=UI["muted"], font=UI["small"])
        self.pad.bind("<ButtonPress-1>", self._pen_down)
        self.pad.bind("<B1-Motion>", self._pen_move)
        FlatButton(self.draw_f, "Clear", self._clear, UI["panel"]).pack(anchor="e", pady=(4, 0))
        # Buttons
        btns = tk.Frame(f, bg=UI["panel"])
        btns.pack(fill="x", pady=(12, 0))
        tk.Label(btns, text="Saved on this computer only, to use again later.", bg=UI["panel"],
                 fg=UI["muted"], font=UI["small"]).pack(side="left")
        FlatButton(btns, "  Done  ", self._done, UI["panel"], primary=True, font=UI["bold"]).pack(side="right")
        FlatButton(btns, "Cancel", w.destroy, UI["panel"]).pack(side="right", padx=6)
        self._tab("type" if self.fonts else "draw")
        e.focus_set()
        w.grab_set()
        center_over(w, parent)

    def _tab(self, mode):
        self.mode.set(mode)
        for m, b in self.tab_btns.items():
            b.colors["fg"] = UI["accent"] if m == mode else UI["muted"]
            b.config(fg=b.colors["fg"])
        self.type_f.pack_forget()
        self.draw_f.pack_forget()
        (self.type_f if mode == "type" else self.draw_f).pack(fill="both")

    def _image(self):
        if self.mode.get() == "type":
            text = self.text.get().strip()
            return typed_signature(text, self.fonts.get(self.font.get())) if text else None
        return drawn_signature(self.strokes, (560, 200)) if self.strokes else None

    def _update_preview(self):
        self.preview.delete("all")
        im = self._image() if self.text.get().strip() else None
        if im is None:
            return
        im = im.copy()
        im.thumbnail((520, 150), Image.Resampling.LANCZOS)
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        self.preview_photo = ImageTk.PhotoImage(bg)
        self.preview.create_image(280, 85, image=self.preview_photo)

    def _pen_down(self, e):
        self.strokes.append([(e.x, e.y)])
        self.last = self.pad.create_oval(e.x - 1, e.y - 1, e.x + 1, e.y + 1, fill="#101828", outline="")

    def _pen_move(self, e):
        px, py = self.strokes[-1][-1]
        if abs(e.x - px) + abs(e.y - py) < 2:
            return
        self.strokes[-1].append((e.x, e.y))
        self.pad.create_line(px, py, e.x, e.y, fill="#101828", width=3, capstyle="round", smooth=True, tags="ink")

    def _clear(self):
        self.strokes = []
        self.pad.delete("ink")
        self.pad.delete(getattr(self, "last", None))

    def _done(self):
        im = self._image()
        if im is None:
            messagebox.showwarning("Nothing to save", "Type your name or draw your signature first.", parent=self.win)
            return
        try:
            save_signature(self.kind, im)
        except OSError as e:
            log_error(f"Save signature: {e}")  # still usable now, just not remembered
        self.win.destroy()
        self.on_done(im)


class CropDialog:
    """Crop the selected pages by margins, or trim their white edges automatically."""
    def __init__(self, app, pages):
        self.app, self.pages = app, pages
        w = self.win = tk.Toplevel(app.root)
        w.title("Crop pages")
        w.transient(app.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        n = len(pages)
        ttk.Label(f, text=f"Crop {n} selected page{'s' * (n != 1)}. Cropping hides the edges; "
                          "nothing is deleted.").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.m = {k: tk.StringVar(value="0") for k in ("Top", "Bottom", "Left", "Right")}
        for i, k in enumerate(("Top", "Bottom", "Left", "Right")):
            ttk.Label(f, text=k).grid(row=1 + i // 2, column=(i % 2) * 2, sticky="e", padx=(0, 6), pady=3)
            ttk.Entry(f, textvariable=self.m[k], width=8).grid(row=1 + i // 2, column=(i % 2) * 2 + 1, sticky="w", pady=3)
        ttk.Label(f, text="Margins in millimetres, as the page is shown.", foreground="gray").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(2, 8))
        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=4, sticky="ew")
        ttk.Button(btns, text="Trim white margins automatically", command=self.auto).pack(side="left")
        ttk.Button(btns, text="Remove crop", command=self.remove).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="Crop", command=self.apply).pack(side="right", padx=6)
        w.grab_set()
        center_over(w, app.root)

    def apply(self):
        try:
            mm = {k: float(v.get().replace(",", ".")) for k, v in self.m.items()}
            if any(v < 0 for v in mm.values()):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Check settings", "Margins must be numbers (0 or more).", parent=self.win)
            return
        pt = {k: v * 72 / 25.4 for k, v in mm.items()}
        crops = {}
        for p in self.pages:
            box, R = self.app._page_box(p), self.app._total_rot(p)
            W, H = shown_size(box, R)
            if pt["Left"] + pt["Right"] >= W - 36 or pt["Top"] + pt["Bottom"] >= H - 36:
                messagebox.showwarning("Too much", "Those margins would leave (almost) nothing of a page.",
                                       parent=self.win)
                return
            crops[p["uid"]] = self.app._shown_margins_to_box(p, pt["Left"], pt["Bottom"], pt["Right"], pt["Top"])
        self.win.destroy()
        self.app._set_crops(crops, f"Cropped {len(crops)} page{'s' * (len(crops) != 1)}.")

    def auto(self):
        crops, skipped = {}, 0
        for p in self.pages:
            doc = self.app.docs[p["doc"]]
            with PDFIUM_LOCK:
                pdoc = self.app._pdfium(doc)
                page = pdoc[p["index"]]
                im = page.render(scale=0.6, rotation=p["rot"], may_draw_forms=True).to_pil()
                page.close()
            fr = auto_trim(im)
            if not fr:
                skipped += 1
                continue
            W, H = shown_size(self.app._page_box(p), self.app._total_rot(p))
            crops[p["uid"]] = self.app._shown_margins_to_box(p, fr[0] * W, fr[3] * H, fr[2] * W, fr[1] * H)
        self.win.destroy()
        msg = f"Trimmed the white margins of {len(crops)} page{'s' * (len(crops) != 1)}."
        if skipped:
            msg += f" {skipped} blank page{'s were' if skipped != 1 else ' was'} left as {'they were' if skipped != 1 else 'it was'}."
        self.app._set_crops(crops, msg)

    def remove(self):
        self.win.destroy()
        self.app._set_crops({p["uid"]: None for p in self.pages}, "Crop removed.")


class PropertiesDialog:
    """Title, author, subject and keywords of the saved PDF."""
    FIELDS = (("Title", "/Title"), ("Author", "/Author"), ("Subject", "/Subject"), ("Keywords", "/Keywords"))

    def __init__(self, app):
        self.app = app
        w = self.win = tk.Toplevel(app.root)
        w.title("Document properties")
        w.transient(app.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        current = app.docinfo or app._source_info()
        self.v = {}
        for i, (label, key) in enumerate(self.FIELDS):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="e", padx=(0, 8), pady=3)
            self.v[key] = tk.StringVar(value=current.get(key, ""))
            ttk.Entry(f, textvariable=self.v[key], width=48).grid(row=i, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Saved into the PDF; shown by PDF readers and used by search engines.",
                  foreground="gray").grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        btns = ttk.Frame(f)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self.ok).pack(side="right", padx=6)
        w.grab_set()
        center_over(w, app.root)

    def ok(self):
        self.app.docinfo = {k: v.get().strip() for k, v in self.v.items()}
        self.app.dirty = True
        self.app._update_title()
        self.app._status("Document properties will be saved with the PDF.")
        self.win.destroy()


class CertificateDialog:
    """Choose the certificate for a certificate signature: your own .p12/.pfx file,
    or a self-signed one made here."""
    def __init__(self, parent, on_done):
        self.on_done = on_done
        w = self.win = tk.Toplevel(parent)
        w.title("Sign with a certificate")
        w.transient(parent)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)
        have = os.path.exists(CERT_PATH)
        self.mode = tk.StringVar(value="saved" if have else "file")
        self.path = tk.StringVar(value="")
        self.pw = tk.StringVar()
        self.name = tk.StringVar()
        self.email = tk.StringVar()
        self.new_pw = tk.StringVar()
        self.new_pw2 = tk.StringVar()
        self.reason = tk.StringVar(value="I approve this document")
        self.location = tk.StringVar()
        self.show_sig = tk.BooleanVar(value=load_signature("signature") is not None)
        ttk.Label(f, text="A certificate signature proves the document hasn't changed since you signed it.",
                  wraplength=460).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        r = 1
        if have:
            ttk.Radiobutton(f, text="My saved certificate", value="saved", variable=self.mode).grid(
                row=r, column=0, columnspan=3, sticky="w")
            r += 1
        ttk.Radiobutton(f, text="A certificate file (.p12 or .pfx)", value="file", variable=self.mode).grid(
            row=r, column=0, columnspan=2, sticky="w")
        ttk.Button(f, text="Choose…", command=self.choose).grid(row=r, column=2, sticky="e")
        r += 1
        ttk.Label(f, textvariable=self.path, foreground="gray").grid(row=r, column=0, columnspan=3, sticky="w", padx=(22, 0))
        r += 1
        ttk.Label(f, text="Certificate password").grid(row=r, column=0, sticky="w", padx=(22, 0), pady=2)
        ttk.Entry(f, textvariable=self.pw, show="•", width=26).grid(row=r, column=1, columnspan=2, sticky="w", pady=2)
        r += 1
        ttk.Radiobutton(f, text="Create a self-signed certificate", value="new", variable=self.mode).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
        r += 1
        for label, var, show in (("Your name", self.name, ""), ("Email (optional)", self.email, ""),
                                 ("New password", self.new_pw, "•"), ("Repeat password", self.new_pw2, "•")):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(22, 0), pady=2)
            ttk.Entry(f, textvariable=var, show=show, width=26).grid(row=r, column=1, columnspan=2, sticky="w", pady=2)
            r += 1
        ttk.Label(f, text="Self-signed: anyone can check the document hasn't changed, but PDF readers will say the "
                          "signer's identity is unknown. For a trusted identity, use a certificate from your employer "
                          "or a certificate authority.", foreground="gray", wraplength=460).grid(
            row=r, column=0, columnspan=3, sticky="w", padx=(22, 0))
        r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=8)
        r += 1
        for label, var in (("Reason", self.reason), ("Location (optional)", self.location)):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(f, textvariable=var, width=36).grid(row=r, column=1, columnspan=2, sticky="w", pady=2)
            r += 1
        ttk.Checkbutton(f, text="Show my handwritten signature in the box", variable=self.show_sig).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(4, 0))
        r += 1
        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(btns, text="Use this certificate", command=self.ok).pack(side="right", padx=6)
        w.grab_set()
        center_over(w, parent)

    def choose(self):
        p = filedialog.askopenfilename(title="Choose your certificate", parent=self.win,
                                       filetypes=[("Certificates", "*.p12 *.pfx"), ("All files", "*.*")])
        if p:
            self.path.set(p)
            self.mode.set("file")

    def ok(self):
        mode = self.mode.get()
        try:
            if mode == "new":
                if not self.name.get().strip():
                    raise ValueError("Type your name.")
                if len(self.new_pw.get()) < 6 or self.new_pw.get() != self.new_pw2.get():
                    raise ValueError("The two passwords must match, and be at least 6 characters.")
                path = create_certificate(self.name.get().strip(), self.email.get().strip(), self.new_pw.get())
                password = self.new_pw.get()
            else:
                path = CERT_PATH if mode == "saved" else self.path.get()
                if not path:
                    raise ValueError("Choose your certificate file.")
                password = self.pw.get()
            signer = load_signer(path, password)  # checks the password now, not at save time
        except ValueError as e:
            messagebox.showwarning("Certificate", str(e), parent=self.win)
            return
        except OSError as e:
            messagebox.showwarning("Certificate", f"Couldn't use the certificate: {e}", parent=self.win)
            return
        self.win.destroy()
        self.on_done(dict(path=path, password=password, name=signer_name(signer), reason=self.reason.get().strip(),
                          location=self.location.get().strip(),
                          image=load_signature("signature") if self.show_sig.get() else None))


class PageEditor:
    """One page at a time, large: view and search, comment, fill in and sign,
    and prepare forms. Everything placed here is added to the PDF when saving."""
    MODES = (("view", "View"), ("edit", "Edit PDF"), ("comment", "Comment"), ("fill", "Fill & Sign"),
             ("form", "Prepare form"))
    FILL_TOOLS = (("text", "Ab", "Add text"), ("check", "✓", "Add a tick"), ("cross", "✗", "Add a cross"),
                  ("date", "📅", "Add today's date"))
    COMMENT_TOOLS = (("highlight", "🖍", "Highlight text (drag over it)"), ("underline", "U̲", "Underline text"),
                     ("strike", "S̶", "Strike through text"), ("note", "💬", "Sticky note"),
                     ("box", "▭A", "Text box"), ("rect", "□", "Rectangle"), ("ellipse", "○", "Ellipse"),
                     ("arrow", "↗", "Arrow"), ("ink", "✎", "Draw freehand"))
    FORM_TOOLS = (("text", "Text field"), ("check", "Checkbox"), ("choice", "Dropdown"), ("signature", "Signature field"))

    def __init__(self, app, start, mode="view"):
        self.app = app
        self.k = start  # index into app.pages
        self.mode, self.tool = None, None
        self.sel = None  # ("mark", id) or ("field", id)
        self.editing = None
        self.widgets = []
        self.drag = None
        self.zoom = "fit"
        self.textpages = {}
        self.selection = None  # (page uid, start char, count)
        self.matches, self.match_i = [], -1
        self.color = "Yellow"
        self.layout = {}  # (doc, index) -> (paragraphs, images) for Edit PDF
        self.esel = None  # index of the selected image in Edit PDF
        self.para_edit = None  # the text being edited in Edit PDF
        self.new_image = None  # picture waiting to be placed
        self.before = app._fill_state()
        w = self.win = tk.Toplevel(app.root)
        w.title("PDF Toolkit — editor")
        w.configure(bg=UI["canvas"])
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"{min(1240, sw - 80)}x{min(920, sh - 100)}")
        w.minsize(1000, 600)
        w.transient(app.root)
        w.protocol("WM_DELETE_WINDOW", self.done)

        bar = tk.Frame(w, bg=UI["appbar"], height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.tabs = {}
        for key, label in self.MODES:
            b = FlatButton(bar, label, lambda k=key: self.set_mode(k), UI["appbar"], fg=UI["bar_text"], hover=UI["bar_hover"],
                           font=UI["bold"], padx=12, pady=12)
            b.pack(side="left", padx=(10 if key == "view" else 0, 0))
            self.tabs[key] = b
        FlatButton(bar, "  Done  ", self.done, UI["appbar"], primary=True, font=UI["bold"], pady=6).pack(
            side="right", padx=(8, 14), pady=8)
        self.page_lbl = tk.Label(bar, text="", bg=UI["appbar"], fg=UI["bar_label"], font=UI["font"])
        nav = [("▶", lambda: self.go(1), "Next page (Page Down)"), (None, None, None),
               ("◀", lambda: self.go(-1), "Previous page (Page Up)")]
        for icon, fn, tip in nav:
            if icon is None:
                self.page_lbl.pack(side="right", padx=2)
                continue
            FlatButton(bar, icon, fn, UI["appbar"], fg="#ffffff", hover=UI["bar_hover"], tip=tip).pack(side="right")
        tk.Frame(bar, bg=UI["bar_sep"], width=1).pack(side="right", fill="y", padx=8, pady=12)
        for icon, fn, tip in (("＋", lambda: self.set_zoom(1.25), "Zoom in"), ("Fit", lambda: self.set_zoom(None), "Fit page"),
                              ("－", lambda: self.set_zoom(0.8), "Zoom out")):
            FlatButton(bar, icon, fn, UI["appbar"], fg="#ffffff", hover=UI["bar_hover"], tip=tip, padx=7).pack(side="right")
        self.zoom_lbl = tk.Label(bar, text="", bg=UI["appbar"], fg=UI["bar_label"], font=UI["small"], width=5)
        self.zoom_lbl.pack(side="right")
        tk.Frame(bar, bg=UI["bar_sep"], width=1).pack(side="right", fill="y", padx=8, pady=12)
        self.count_lbl = tk.Label(bar, text="", bg=UI["appbar"], fg=UI["bar_label"], font=UI["small"])
        self.count_lbl.pack(side="right", padx=(4, 0))
        FlatButton(bar, "▼", lambda: self.next_match(1), UI["appbar"], fg="#ffffff", hover=UI["bar_hover"],
                   tip="Next match (Enter)", padx=5).pack(side="right")
        FlatButton(bar, "▲", lambda: self.next_match(-1), UI["appbar"], fg="#ffffff", hover=UI["bar_hover"],
                   tip="Previous match (Shift+Enter)", padx=5).pack(side="right")
        self.query = tk.StringVar()
        self.search_e = tk.Entry(bar, textvariable=self.query, width=20, relief="flat", font=UI["font"],
                                 bg=UI["bar_field"], fg="#ffffff", insertbackground="#ffffff")
        self.search_e.pack(side="right", ipady=4, pady=11)
        self.search_e.bind("<Return>", lambda e: self.search())
        self.search_e.bind("<Shift-Return>", lambda e: self.next_match(-1))
        tk.Label(bar, text="🔍", bg=UI["appbar"], fg=UI["bar_label"], font=UI["font"]).pack(side="right", padx=(0, 4))

        self.tools = tk.Frame(w, bg=UI["toolbar"], height=46, highlightthickness=1, highlightbackground=UI["border"])
        self.tools.pack(fill="x")
        self.tools.pack_propagate(False)
        self.banner = tk.Label(w, text="", anchor="w", font=UI["small"], padx=14, pady=5)

        body = tk.Frame(w, bg=UI["canvas"])
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=UI["canvas"], highlightthickness=0)
        ys = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        xs = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        c = self.canvas
        c.bind("<Configure>", lambda e: self.show() if self.zoom == "fit" else None)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Button-2>" if sys.platform == "darwin" else "<Button-3>", self.on_menu)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            c.bind(seq, self.on_wheel)
        typing = (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)
        w.bind("<Delete>", lambda e: self.delete_sel() if not isinstance(e.widget, typing) else None)
        w.bind("<Escape>", lambda e: (self.set_tool(None), self.select(None), self.clear_selection()))
        w.bind("<Prior>", lambda e: self.go(-1))
        w.bind("<Next>", lambda e: self.go(1))
        mod = "Command" if sys.platform == "darwin" else "Control"
        w.bind(f"<{mod}-c>", lambda e: self.copy() if not isinstance(e.widget, typing) else None)
        w.bind(f"<{mod}-f>", lambda e: (self.search_e.focus_set(), self.search_e.select_range(0, "end")))
        w.bind(f"<{mod}-z>", lambda e: self.edit_undo() if self.mode == "edit" and not isinstance(e.widget, typing) else None)
        self.set_mode(mode)
        center_over(w, app.root)
        w.after(60, self.show)

    # ---- modes and tools -------------------------------------------------- #
    def set_mode(self, mode):
        self.commit_edit()
        self.mode = mode
        self.tool = None
        self.select(None)
        for k, b in self.tabs.items():
            on = k == mode
            b.colors["fg"] = "#ffffff" if on else UI["bar_dim"]
            b.colors["bg"] = UI["bar_tab"] if on else UI["appbar"]
            b.config(fg=b.colors["fg"], bg=b.colors["bg"])
        for child in self.tools.winfo_children():
            child.destroy()
        self.tool_btns = {}
        t = self.tools
        if mode == "view":
            tk.Label(t, text="Drag across text to select it; Ctrl+C copies. Ctrl+F searches.", bg=UI["toolbar"],
                     fg=UI["muted"], font=UI["font"]).pack(side="left", padx=14)
            FlatButton(t, "Copy", self.copy, UI["toolbar"], font=UI["bold"], tip="Copy the selected text").pack(side="left")
        elif mode == "comment":
            for key, icon, tip in self.COMMENT_TOOLS:
                b = FlatButton(t, icon, lambda k=key: self.set_tool(k), UI["toolbar"], font=UI["icon"], padx=10, tip=tip)
                b.pack(side="left", padx=(8 if key == "highlight" else 1, 1), pady=4)
                self.tool_btns[key] = b
            tk.Frame(t, bg=UI["border"], width=1).pack(side="left", fill="y", padx=8, pady=9)
            self.swatches = {}
            for name, rgb in COMMENT_COLORS.items():
                sw = tk.Label(t, text="", bg=_hex(rgb), width=2, relief="flat", cursor="hand2", bd=0,
                              highlightthickness=2, highlightbackground=UI["toolbar"])
                sw.pack(side="left", padx=2, pady=12)
                sw.bind("<Button-1>", lambda e, n=name: self.set_color(n))
                Tooltip(sw, name)
                self.swatches[name] = sw
            self.set_color(self.color)
        elif mode == "fill":
            for key, icon, tip in self.FILL_TOOLS:
                b = FlatButton(t, icon, lambda k=key: self.set_tool(k), UI["toolbar"], font=UI["icon"], padx=11, tip=tip)
                b.pack(side="left", padx=(8 if key == "text" else 1, 1), pady=4)
                self.tool_btns[key] = b
            tk.Frame(t, bg=UI["border"], width=1).pack(side="left", fill="y", padx=8, pady=9)
            self.sign_btn = FlatButton(t, "✍  Sign ▾", self.sign_menu, UI["toolbar"], font=UI["bold"],
                                       tip="Your signature or initials, or a certificate signature")
            self.sign_btn.pack(side="left")
        elif mode == "edit":
            b = FlatButton(t, "＋ Text", lambda: self.set_tool("addtext"), UI["toolbar"], font=UI["bold"], padx=9,
                           tip="Add new text to the page")
            b.pack(side="left", padx=(8, 1), pady=4)
            self.tool_btns["addtext"] = b
            b = FlatButton(t, "＋ Image", self.pick_new_image, UI["toolbar"], font=UI["bold"], padx=9,
                           tip="Add a picture to the page")
            b.pack(side="left", padx=1, pady=4)
            self.tool_btns["addimage"] = b
            FlatButton(t, "Replace image", self.replace_image, UI["toolbar"], font=UI["bold"], padx=9,
                       tip="Swap the selected picture for another").pack(side="left", padx=1, pady=4)
            tk.Frame(t, bg=UI["border"], width=1).pack(side="left", fill="y", padx=8, pady=9)
            for icon, fn, tip in (("A−", lambda: self.resize(-1), "Smaller text"), ("A+", lambda: self.resize(1), "Bigger text")):
                FlatButton(t, icon, fn, UI["toolbar"], font=UI["bold"], padx=8, tip=tip).pack(side="left")
            FlatButton(t, "↶", self.edit_undo, UI["toolbar"], font=UI["icon"], padx=9,
                       tip="Undo the last change (Ctrl+Z)").pack(side="left", padx=1)
        elif mode == "form":
            for key, label in self.FORM_TOOLS:
                b = FlatButton(t, "＋ " + label, lambda k=key: self.set_tool(k), UI["toolbar"], font=UI["bold"], padx=9)
                b.pack(side="left", padx=(8 if key == "text" else 1, 1), pady=4)
                self.tool_btns[key] = b
            tk.Frame(t, bg=UI["border"], width=1).pack(side="left", fill="y", padx=8, pady=9)
            self.prop = tk.Frame(t, bg=UI["toolbar"])
            self.prop.pack(side="left", fill="y")
        if mode in ("comment", "fill"):
            tk.Frame(t, bg=UI["border"], width=1).pack(side="left", fill="y", padx=8, pady=9)
            for icon, fn, tip in (("A−", lambda: self.resize(-1), "Smaller"), ("A+", lambda: self.resize(1), "Bigger")):
                FlatButton(t, icon, fn, UI["toolbar"], font=UI["bold"], padx=8, tip=tip).pack(side="left")
        if mode != "view":
            FlatButton(t, "🗑", self.delete_sel, UI["toolbar"], font=UI["icon"], padx=9,
                       tip="Delete the selected item (Del)").pack(side="left", padx=1)
        self.hint = tk.Label(t, text="", bg=UI["toolbar"], fg=UI["muted"], font=UI["small"])
        self.hint.pack(side="right", padx=12)
        self.set_tool(None)
        if hasattr(self, "photo"):
            self.show()

    def set_tool(self, tool):
        self.tool = tool
        for k, b in self.tool_btns.items():
            b.colors["bg"] = UI["sel_bg"] if k == tool else UI["toolbar"]
            b.config(bg=b.colors["bg"])
        text_tools = ("highlight", "underline", "strike")
        self.canvas.config(cursor="xterm" if (self.mode == "view" or tool in text_tools) else "crosshair" if tool else "")
        hints = {
            ("view", None): "",
            ("comment", None): "Pick a tool and a colour. Click a comment to select it; drag to move it.",
            ("comment", "highlight"): "Drag across the text to highlight.", ("comment", "underline"): "Drag across the text to underline.",
            ("comment", "strike"): "Drag across the text to strike it through.", ("comment", "note"): "Click where the note goes.",
            ("comment", "box"): "Click where the text box starts, then type.", ("comment", "rect"): "Drag to draw a rectangle.",
            ("comment", "ellipse"): "Drag to draw an ellipse.", ("comment", "arrow"): "Drag from the tail to the point.",
            ("comment", "ink"): "Draw with the mouse, touchpad or pen.",
            ("fill", None): "Click a form field to fill it, or choose a tool and click on the page.",
            ("fill", "text"): "Click where the text should start, then type.", ("fill", "check"): "Click to place a tick.",
            ("fill", "cross"): "Click to place a cross.", ("fill", "date"): "Click to place today's date.",
            ("fill", "signature"): "Click where your signature goes.", ("fill", "initials"): "Click where your initials go.",
            ("fill", "certificate"): "Drag a box where the certificate signature goes.",
            ("form", None): "Choose a field type, then drag a box on the page (click for a checkbox).",
            ("edit", None): "Click text to change it. Click a picture to move it; drag its corner to resize it.",
            ("edit", "addtext"): "Click where the new text goes, then type.",
            ("edit", "addimage"): "Click where the picture goes, or drag a box for its size.",
        }
        self.hint.config(text=hints.get((self.mode, tool), "Drag a box on the page." if self.mode == "form" else ""))
        if self.mode == "form":
            self.show_props()

    def set_color(self, name):
        self.color = name
        for n, sw in self.swatches.items():
            sw.config(highlightbackground=UI["text"] if n == name else UI["toolbar"])
        # With a tool picked, the colour is for the next comment; with none, it
        # recolours the selected one.
        m = self.selected_mark() if not self.tool else None
        if m and m["kind"] in COMMENT_KINDS:
            m["color"] = COMMENT_COLORS[name]
            self.show()

    def set_zoom(self, factor):
        if factor is None:
            self.zoom = "fit"
        else:
            self.zoom = max(0.25, min(6.0, (self.s if self.zoom == "fit" else self.zoom) * factor))
        self.show()

    # ---- page & coordinates ------------------------------------------------- #
    def page(self):
        return self.app.pages[self.k]

    def go(self, step, to=None):
        self.commit_edit()
        k = self.k + step if to is None else to
        if 0 <= k < len(self.app.pages) and k != self.k:
            self.k = k
            self.select(None)
            self.esel = None
            self.selection = None
            self.canvas.yview_moveto(0 if step >= 0 else 1)
            self.show()

    def show(self):
        """Draw the current page with its fields, comments and placed items."""
        self.commit_edit()
        c = self.canvas
        c.delete("all")
        for wdg in self.widgets:
            wdg.destroy()
        self.widgets = []
        p = self.page()
        doc = self.app.docs[p["doc"]]
        box, R = self.app._page_box(p), self.app._total_rot(p)
        crop = p.get("crop") or box
        pts = [to_shown(box, R, x, y) for x, y in ((crop[0], crop[1]), (crop[2], crop[3]))]
        self.cx0, self.cy0 = min(q[0] for q in pts), min(q[1] for q in pts)
        cw, ch = abs(pts[1][0] - pts[0][0]), abs(pts[1][1] - pts[0][1])
        W, H = max(c.winfo_width(), 100), max(c.winfo_height(), 100)
        fit = min((W - 60) / cw, (H - 40) / ch)
        self.s = fit if self.zoom == "fit" else self.zoom
        pw, ph = cw * self.s, ch * self.s
        self.ox, self.oy = max(30, (W - pw) / 2), max(20, (H - ph) / 2)
        c.configure(scrollregion=(0, 0, max(W, pw + 60), max(H, ph + 40)))
        self.vh = ch
        Wf, Hf = shown_size(box, R)
        with PDFIUM_LOCK:
            pg = self.app._pdfium(doc)[p["index"]]
            im = pg.render(scale=self.s, rotation=p["rot"], may_draw_forms=self.mode in ("view", "comment"),
                           crop=(self.cx0, self.cy0, Wf - self.cx0 - cw, Hf - self.cy0 - ch)).to_pil()
            pg.close()
        highlights = [m for m in self.app.marks.get(p["uid"], []) if m["kind"] == "highlight"]
        if highlights:
            # Blend highlights into the page itself (like a real marker), so the
            # colour is true and the text under it stays crisp.
            from PIL import ImageChops, ImageDraw
            layer = Image.new("RGB", im.size, "white")
            d = ImageDraw.Draw(layer)
            for m in highlights:
                for rect in m["rects"]:
                    x0, y0, x1, y1 = self.canvas_rect(rect)
                    d.rectangle([x0 - self.ox, y0 - self.oy, x1 - self.ox, y1 - self.oy],
                                fill=tuple(int(v * 255) for v in m["color"]))
            im = ImageChops.multiply(im.convert("RGB"), layer)
        self.photo = ImageTk.PhotoImage(im)
        c.create_rectangle(self.ox + 3, self.oy + 3, self.ox + im.width + 3, self.oy + im.height + 3, fill="#c4bcdb", outline="")
        c.create_image(self.ox, self.oy, image=self.photo, anchor="nw")
        self.page_lbl.config(text=f"Page {self.k + 1} of {len(self.app.pages)}")
        self.zoom_lbl.config(text=f"{self.s * 72 / 96 * 100:.0f}%")  # 100% = actual size on a 96 DPI screen
        self.show_banner(p, doc)
        if self.mode == "fill":
            self.draw_fields(p)
        for f in self.app.new_fields.get(p["uid"], []):
            self.draw_new_field(f)
        req = self.app.cert_request
        if req and req["uid"] == p["uid"]:
            x0, y0, x1, y1 = self.canvas_rect(req["rect"])
            c.create_rectangle(x0, y0, x1, y1, outline="#7a3cc8", dash=(5, 3), width=2, tags=("mark", "mcert"))
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=f"Certificate signature\n{req['name']}\n(added when saving)",
                          fill="#7a3cc8", font=UI["small"], justify="center", tags=("mark", "mcert"))
        for m in self.app.marks.get(p["uid"], []):
            self.draw_mark(m)
        self.draw_selection()
        self.draw_matches()
        if self.mode == "edit":
            self.draw_layout(p)
        if self.sel:
            self.select(self.sel)

    def show_banner(self, p, doc):
        sigs = self.app._signatures(p["doc"])
        if not sigs:
            self.banner.pack_forget()
            return
        if all(s["intact"] for s in sigs):
            names = ", ".join(f"{s['name']} ({s['when']})" for s in sigs)
            text = (f"✔  Certificate-signed by {names}. Unchanged since it was signed. "
                    "(This app can't confirm who the signer is: check the certificate in a PDF reader.) "
                    "Saving a new copy here doesn't keep the signature.")
            bg = "#e7f6ec"
        else:
            text = "✖  This document was changed after it was signed, or a signature is damaged."
            bg = "#fdecea"
        self.banner.config(text=text, bg=bg, fg=UI["text"], wraplength=1100, justify="left")
        self.banner.pack(fill="x", after=self.tools)

    def to_canvas(self, x, y):
        p = self.page()
        X, Y = to_shown(self.app._page_box(p), self.app._total_rot(p), x, y)
        return self.ox + (X - self.cx0) * self.s, self.oy + (self.vh - (Y - self.cy0)) * self.s

    def from_canvas(self, cx, cy):
        p = self.page()
        X = (cx - self.ox) / self.s + self.cx0
        Y = self.vh - (cy - self.oy) / self.s + self.cy0
        return to_page(self.app._page_box(p), self.app._total_rot(p), X, Y)

    def canvas_rect(self, rect):
        a = self.to_canvas(rect[0], rect[1])
        b = self.to_canvas(rect[2], rect[3])
        return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])

    def page_rect(self, cx0, cy0, cx1, cy1):
        a = self.from_canvas(cx0, cy0)
        b = self.from_canvas(cx1, cy1)
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

    def on_page(self, cx, cy):
        return self.ox <= cx <= self.ox + self.photo.width() and self.oy <= cy <= self.oy + self.photo.height()

    def ev(self, e):
        return self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)

    # ---- text: selection, copy, search ------------------------------------- #
    def textpage(self, p=None):
        p = p or self.page()
        key = (p["doc"], p["index"])
        if key not in self.textpages:
            with PDFIUM_LOCK:
                pg = self.app._pdfium(self.app.docs[p["doc"]])[p["index"]]
                self.textpages[key] = (pg, pg.get_textpage())
        return self.textpages[key][1]

    def char_at(self, cx, cy):
        x, y = self.from_canvas(cx, cy)
        tp = self.textpage()
        with PDFIUM_LOCK:
            for tol in (2, 6, 14):
                i = tp.get_index(x, y, tol, tol)
                if i is not None and i >= 0:
                    return i
        return None

    def text_rects(self, start, count, p=None):
        tp = self.textpage(p)
        with PDFIUM_LOCK:
            n = tp.count_rects(start, count)
            return [tp.get_rect(i) for i in range(n)]

    def clear_selection(self):
        self.selection = None
        self.canvas.delete("textsel")

    def draw_selection(self):
        self.canvas.delete("textsel")
        if not self.selection or self.selection[0] != self.page()["uid"]:
            return
        _, start, count = self.selection
        for l, b, r, t in self.text_rects(start, count):
            x0, y0, x1, y1 = self.canvas_rect((l, b, r, t))
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#2f74d0", stipple="gray25", outline="", tags="textsel")

    def selected_text(self):
        if not self.selection or self.selection[0] != self.page()["uid"]:
            return ""
        tp = self.textpage()
        with PDFIUM_LOCK:
            return tp.get_text_range(self.selection[1], self.selection[2])

    def copy(self):
        text = self.selected_text()
        if text:
            self.win.clipboard_clear()
            self.win.clipboard_append(text.replace("\r\n", "\n"))
            self.hint.config(text=f"Copied {len(text)} characters.")
        else:
            self.hint.config(text="Select some text first: drag across it.")

    def search(self):
        q = self.query.get().strip()
        self.matches, self.match_i = [], -1
        if not q:
            self.count_lbl.config(text="")
            self.show()
            return
        for k, p in enumerate(self.app.pages):
            tp = self.textpage(p)
            with PDFIUM_LOCK:
                searcher = tp.search(q, match_case=False)
                while True:
                    hit = searcher.get_next()
                    if not hit:
                        break
                    self.matches.append((k, hit[0], hit[1]))
                searcher.close()
        if not self.matches:
            self.count_lbl.config(text="No matches")
            self.show()
            return
        # The first match at or after this page.
        self.match_i = next((i for i, m in enumerate(self.matches) if m[0] >= self.k), 0) - 1
        self.next_match(1)

    def next_match(self, step):
        if not self.matches:
            return self.search() if self.query.get().strip() else None
        self.match_i = (self.match_i + step) % len(self.matches)
        k = self.matches[self.match_i][0]
        self.count_lbl.config(text=f"{self.match_i + 1} of {len(self.matches)}")
        if k != self.k:
            self.go(0, to=k)
        else:
            self.draw_matches()
        b = self.canvas.bbox("current_match")
        if b:  # scroll it into view
            sr = [float(v) for v in str(self.canvas.cget("scrollregion")).split()]
            if sr and sr[3] > self.canvas.winfo_height():
                self.canvas.yview_moveto(max(0, (b[1] - self.canvas.winfo_height() / 3) / sr[3]))

    def draw_matches(self):
        self.canvas.delete("match")
        for i, (k, start, count) in enumerate(self.matches):
            if k != self.k:
                continue
            cur = i == self.match_i
            for l, b, r, t in self.text_rects(start, count):
                x0, y0, x1, y1 = self.canvas_rect((l, b, r, t))
                self.canvas.create_rectangle(x0 - 1, y0 - 1, x1 + 1, y1 + 1, fill="#ff9d00" if cur else "#ffd84d",
                                             stipple="gray50", outline="#e07b00" if cur else "",
                                             tags=("match", "current_match") if cur else ("match",))

    # ---- form fields (Fill & Sign) ---------------------------------------- #
    def page_fields(self, p):
        """The page's fields to fill: the PDF's own, then ones made in Prepare form."""
        out = [dict(f, new=False) for f in self.app._fields(p["doc"]) if f["page"] == p["index"]]
        for f in self.app.new_fields.get(p["uid"], []):
            if f["kind"] != "signature":
                out.append(dict(name=f["name"], kind=f["kind"], rect=f["rect"], on="Yes", options=f.get("options", []),
                                value=f.get("value", "" if f["kind"] != "check" else False), readonly=False,
                                multiline=f.get("multiline", False), new=True, ref=f))
        return out

    def draw_fields(self, p):
        values = self.app.fill.setdefault(p["doc"], {})
        field_bg = "#dde9fb"
        fields = self.page_fields(p)
        for f in fields:
            if f["readonly"]:
                continue
            x0, y0, x1, y1 = self.canvas_rect(f["rect"])
            w, h = max(8, x1 - x0), max(8, y1 - y0)
            if f["new"]:
                current = f["ref"].get("value", "" if f["kind"] in ("text", "choice") else False)
            else:
                current = values.get(f["name"], f["value"] if f["kind"] in ("text", "choice") else
                                     (f["value"] == f["on"] if f["kind"] == "check" else f["value"]))
            setter = (lambda v, ref=f["ref"]: ref.__setitem__("value", v)) if f["new"] else \
                (lambda v, n=f["name"]: self.set_value(n, v))
            if f["kind"] == "text":
                size = -max(9, min(int(h * 0.62), 22)) if not f["multiline"] else -12
                if f["multiline"]:
                    wdg = tk.Text(self.canvas, bg=field_bg, relief="flat", font=("Helvetica", size), wrap="word",
                                  highlightthickness=1, highlightbackground="#9cb8e6", highlightcolor=UI["accent"])
                    wdg.insert("1.0", current or "")
                    wdg.bind("<KeyRelease>", lambda e, t=wdg, s=setter: s(t.get("1.0", "end-1c")))
                else:
                    var = tk.StringVar(value=current or "")
                    wdg = tk.Entry(self.canvas, textvariable=var, bg=field_bg, relief="flat", font=("Helvetica", size),
                                   highlightthickness=1, highlightbackground="#9cb8e6", highlightcolor=UI["accent"])
                    var.trace_add("write", lambda *_, v=var, s=setter: s(v.get()))
                    wdg.var = var
                self.canvas.create_window(x0, y0, window=wdg, anchor="nw", width=w, height=h)
            elif f["kind"] == "choice":
                var = tk.StringVar(value=current or "")
                wdg = ttk.Combobox(self.canvas, values=f["options"], textvariable=var)
                var.trace_add("write", lambda *_, v=var, s=setter: s(v.get()))
                wdg.var = var
                self.canvas.create_window(x0, y0, window=wdg, anchor="nw", width=max(w, 60), height=h)
            else:
                on = (current is True) if f["kind"] == "check" else (current == f["on"])
                wdg = tk.Label(self.canvas, text="✔" if on else "", bg=field_bg, fg="#101828", cursor="hand2",
                               font=(UI["family"], -max(8, int(min(w, h) * 0.75))), relief="flat", bd=0,
                               highlightthickness=1, highlightbackground="#9cb8e6")
                wdg.bind("<Button-1>", lambda e, fld=f: self.toggle(fld))
                self.canvas.create_window(x0, y0, window=wdg, anchor="nw", width=w, height=h)
            self.widgets.append(wdg)
        if fields and not self.tool:
            self.hint.config(text="This page has form fields: click one to fill it in.")

    def set_value(self, name, value):
        self.app.fill.setdefault(self.page()["doc"], {})[name] = value

    def toggle(self, f):
        if f["new"]:
            f["ref"]["value"] = not f["ref"].get("value", False)
        else:
            values = self.app.fill.setdefault(self.page()["doc"], {})
            if f["kind"] == "check":
                values[f["name"]] = not values.get(f["name"], f["value"] == f["on"])
            else:
                current = values.get(f["name"], f["value"])
                values[f["name"]] = "" if current == f["on"] else f["on"]
        self.show()

    # ---- Prepare form ------------------------------------------------------- #
    def draw_new_field(self, f):
        if self.mode == "fill" and f["kind"] != "signature":
            return  # drawn as a fillable widget instead
        x0, y0, x1, y1 = self.canvas_rect(f["rect"])
        tag = ("field", f"f{f['id']}")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#7a3cc8", fill="#efe6fb", stipple="gray25", width=1.5,
                                     tags=tag)
        label = {"check": "✓", "choice": f"{f['name']} ▾", "signature": f"✍ {f['name']}"}.get(f["kind"], f["name"])
        self.canvas.create_text(x0 + 4, (y0 + y1) / 2, text=label, anchor="w", fill="#5b2a99",
                                font=(UI["family"], -max(8, min(12, int((y1 - y0) * 0.6)))), tags=tag)

    def show_props(self):
        """Name and options of the selected new field, in the toolbar."""
        if self.mode != "form" or not getattr(self, "prop", None) or not self.prop.winfo_exists():
            return  # the toolbar is still being built
        for child in self.prop.winfo_children():
            child.destroy()
        f = self.selected_field()
        if f is None:
            return
        tk.Label(self.prop, text=FIELD_KINDS[f["kind"]] + ":", bg=UI["toolbar"], fg=UI["muted"], font=UI["small"]).pack(
            side="left", padx=(0, 4))
        name = tk.StringVar(value=f["name"])
        e = ttk.Entry(self.prop, textvariable=name, width=16)
        e.pack(side="left", pady=10)
        name.trace_add("write", lambda *_: (f.__setitem__("name", name.get().strip() or f["name"]), self.redraw_field(f)))
        Tooltip(e, "Field name (what the filled-in value is called)")
        if f["kind"] == "choice":
            opts = tk.StringVar(value=", ".join(f.get("options", [])))
            tk.Label(self.prop, text="Options", bg=UI["toolbar"], fg=UI["muted"], font=UI["small"]).pack(side="left", padx=(8, 4))
            oe = ttk.Entry(self.prop, textvariable=opts, width=26)
            oe.pack(side="left")
            opts.trace_add("write", lambda *_: f.__setitem__("options", [o.strip() for o in opts.get().split(",") if o.strip()]))
            Tooltip(oe, "Separate the choices with commas")
        if f["kind"] == "text":
            ml = tk.BooleanVar(value=f.get("multiline", False))
            ttk.Checkbutton(self.prop, text="Several lines", variable=ml,
                            command=lambda: f.__setitem__("multiline", ml.get())).pack(side="left", padx=8)

    def redraw_field(self, f):
        self.canvas.delete(f"f{f['id']}")
        self.draw_new_field(f)
        self.select(("field", f["id"]))

    def selected_field(self):
        if not self.sel or self.sel[0] != "field":
            return None
        return next((f for f in self.app.new_fields.get(self.page()["uid"], []) if f["id"] == self.sel[1]), None)

    def add_field(self, kind, rect):
        fields = self.app.new_fields.setdefault(self.page()["uid"], [])
        taken = {f["name"] for fs in self.app.new_fields.values() for f in fs}
        base = {"text": "Text", "check": "Check", "choice": "Dropdown", "signature": "Signature"}[kind]
        n = 1
        while f"{base}{n}" in taken:
            n += 1
        f = dict(id=self.app._new_mark_id(), kind=kind, name=f"{base}{n}", rect=rect,
                 options=["Option 1", "Option 2"] if kind == "choice" else [], multiline=False)
        fields.append(f)
        self.draw_new_field(f)
        self.select(("field", f["id"]))
        self.show_props()

    # ---- placed items and comments ------------------------------------------ #
    def draw_mark(self, m):
        c, s = self.canvas, self.s
        R = self.app._total_rot(self.page())
        tag = ("mark", f"m{m['id']}")
        kind = m["kind"]
        col = _hex(m.get("color", (0, 0, 0)))
        if kind in TEXT_MARKUP:
            for rect in m["rects"]:
                x0, y0, x1, y1 = self.canvas_rect(rect)
                if kind == "highlight":  # the colour is blended into the page in show(); this is for clicking
                    c.create_rectangle(x0, y0, x1, y1, fill="", outline="", tags=tag)
                else:
                    y = y1 - (y1 - y0) * 0.08 if kind == "underline" else (y0 + y1) / 2
                    c.create_rectangle(x0, y0, x1, y1, fill="", outline="", tags=tag)  # clickable area
                    c.create_line(x0, y, x1, y, fill=col, width=max(1, (y1 - y0) * 0.08), tags=tag)
            return
        if kind == "note":
            x, y = self.to_canvas(m["x"], m["y"])
            z = 20 * s
            c.create_rectangle(x, y, x + z, y + z * 0.8, fill=col, outline="#404040", tags=tag)
            c.create_text(x + z / 2, y + z * 0.4, text="💬", font=(UI["family"], -max(6, int(z * 0.5))), tags=tag)
            return
        if kind in ("rect", "ellipse"):
            x0, y0, x1, y1 = self.canvas_rect(m["rect"])
            (c.create_rectangle if kind == "rect" else c.create_oval)(x0, y0, x1, y1, outline=col,
                                                                        width=max(1, m.get("width", 2) * s), tags=tag)
            return
        if kind == "arrow":
            (x1, y1), (x2, y2) = [self.to_canvas(*p) for p in m["points"]]
            c.create_line(x1, y1, x2, y2, fill=col, width=max(1, m.get("width", 2) * s), arrow="last",
                          arrowshape=(10 * s, 12 * s, 4 * s), tags=tag)
            return
        if kind == "ink":
            for stroke in m["strokes"]:
                pts = [v for p in stroke for v in self.to_canvas(*p)]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=col, width=max(1, m.get("width", 2) * s), capstyle="round",
                                  joinstyle="round", smooth=True, tags=tag)
            return
        turn = (R - m["rot"]) % 360  # the page was turned after this was placed
        cx, cy = self.to_canvas(m["x"], m["y"])
        if kind in ("text", "date", "box"):
            item = c.create_text(cx, cy, text=m["text"], font=("Helvetica", -max(4, int(m["size"] * s))), fill="#000000",
                                 angle=-turn, tags=tag)
            if kind == "box":
                b = c.bbox(item)
                c.create_rectangle(b[0] - 4 * s, b[1] - 2 * s, b[2] + 4 * s, b[3] + 2 * s, outline=col,
                                   width=max(1, 1.2 * s), tags=tag)
        elif kind in MARK_GLYPHS:
            c.create_text(cx, cy, text="✔" if kind == "check" else "✘", fill="#000000",
                          font=(UI["family"], -max(4, int(m["size"] * s))), angle=-turn, tags=tag)
        else:
            w, h = mark_size(m)
            im = m["image"].resize((max(1, int(w * s)), max(1, int(h * s))), Image.Resampling.LANCZOS)
            if turn:
                im = im.rotate(-turn, expand=True)
            photo = ImageTk.PhotoImage(im)
            m.setdefault("_photos", {})[self.k] = photo  # keep a reference
            c.create_image(cx, cy, image=photo, tags=tag)

    def marks(self):
        return self.app.marks.setdefault(self.page()["uid"], [])

    def selected_mark(self):
        if not self.sel or self.sel[0] != "mark":
            return None
        return next((m for m in self.marks() if m["id"] == self.sel[1]), None)

    def select(self, sel):
        self.canvas.delete("selbox")
        self.sel = sel
        if sel is None:
            if self.mode == "form":
                self.show_props()
            return
        tag = f"m{sel[1]}" if sel[0] == "mark" else f"f{sel[1]}"
        b = self.canvas.bbox(tag)
        if not b:
            self.sel = None
            return
        self.canvas.create_rectangle(b[0] - 4, b[1] - 3, b[2] + 4, b[3] + 3, outline=UI["accent"], dash=(4, 2),
                                     width=2, tags="selbox")
        if self.mode == "form":
            self.show_props()

    def sign_menu(self):
        menu = tk.Menu(self.win, tearoff=0)
        sig, ini = load_signature("signature"), load_signature("initials")
        menu.add_command(label="Add signature" if sig else "Create signature…", command=lambda: self.use_sig("signature"))
        menu.add_command(label="Add initials" if ini else "Create initials…", command=lambda: self.use_sig("initials"))
        menu.add_separator()
        menu.add_command(label="Sign with a certificate…", command=lambda: self.set_tool("certificate"))
        if self.app.cert_request:
            menu.add_command(label="Remove the certificate signature", command=self.remove_cert)
        menu.add_separator()
        if sig:
            menu.add_command(label="Change signature…", command=lambda: self.new_sig("signature"))
        if ini:
            menu.add_command(label="Change initials…", command=lambda: self.new_sig("initials"))
        if sig or ini:
            menu.add_command(label="Forget my saved signature and initials", command=self.forget)
        b = self.sign_btn
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def use_sig(self, kind):
        im = load_signature(kind)
        if im is None:
            self.new_sig(kind)
        else:
            self.pending_image = im
            self.set_tool(kind)

    def new_sig(self, kind):
        def done(im):
            self.pending_image = im
            self.set_tool(kind)
        SignatureDialog(self.win, kind, done)

    def forget(self):
        if messagebox.askyesno("Forget signature?", "Delete your saved signature and initials from this computer? "
                               "Ones already placed in documents stay.", parent=self.win):
            forget_signatures()
            self.hint.config(text="Your saved signature and initials were deleted from this computer.")

    def remove_cert(self):
        self.app.cert_request = None
        self.show()

    def new_mark(self, kind, **kw):
        m = dict(id=self.app._new_mark_id(), kind=kind, rot=self.app._total_rot(self.page()), **kw)
        if kind in COMMENT_KINDS:
            m.setdefault("color", COMMENT_COLORS[self.color])
            m.setdefault("width", 2)
        self.marks().append(m)
        return m

    def add_mark(self, kind, cx, cy):
        x, y = self.from_canvas(cx, cy)
        if kind == "date":
            m = self.new_mark(kind, x=x, y=y, size=12, text=time.strftime("%d %b %Y").lstrip("0"))
        elif kind in ("text", "box"):
            m = self.new_mark(kind, x=x, y=y, size=12, text="")
            self.draw_mark(m)
            self.edit_text(m, new=True)
            return m
        elif kind in ("signature", "initials"):
            m = self.new_mark(kind, x=x, y=y, image=self.pending_image, w=150 if kind == "signature" else 55, size=None)
        elif kind in MARK_GLYPHS:
            m = self.new_mark(kind, x=x, y=y, size=14)
        elif kind == "note":
            m = self.new_mark(kind, x=x, y=y, text="")
            self.draw_mark(m)
            self.edit_note(m, new=True)
            return m
        self.draw_mark(m)
        self.select(("mark", m["id"]))
        return m

    def edit_note(self, m, new=False):
        top = tk.Toplevel(self.win)
        top.title("Note")
        top.transient(self.win)
        t = tk.Text(top, width=40, height=7, wrap="word", font=UI["font"], bg=_hex(m["color"]) if False else "#fffbe0")
        t.pack(padx=10, pady=(10, 4))
        t.insert("1.0", m.get("text", ""))
        t.focus_set()

        def ok():
            m["text"] = t.get("1.0", "end-1c").strip()
            top.destroy()
            if not m["text"] and new:
                self.app.marks[self.page()["uid"]] = [x for x in self.marks() if x["id"] != m["id"]]
            self.show()
            if m["text"]:
                self.select(("mark", m["id"]))
        ttk.Button(top, text="OK", command=ok).pack(pady=(0, 10))
        top.bind("<Control-Return>", lambda e: ok())
        center_over(top, self.win)

    def edit_text(self, m, new=False):
        """Type into a placed text item or text box, in place."""
        self.commit_edit()
        cx, cy = self.to_canvas(m["x"], m["y"])
        self.canvas.delete(f"m{m['id']}")
        var = tk.StringVar(value=m["text"])
        size = max(4, int(m["size"] * self.s))
        e = tk.Entry(self.canvas, textvariable=var, font=("Helvetica", -size), relief="flat", bg="#fffbe6",
                     highlightthickness=1, highlightbackground=UI["accent"])
        width = max(160, int(text_width(m["text"] or "W" * 12, m["size"]) * self.s) + 30)
        x0 = cx if new else cx - (text_width(m["text"], m["size"]) * self.s) / 2
        win = self.canvas.create_window(x0, cy, window=e, anchor="w", width=width, height=size + 10)
        self.editing = (m, var, e, win, new, x0, cy)
        e.focus_set()
        e.icursor("end")
        e.bind("<Return>", lambda ev: self.commit_edit())
        e.bind("<Escape>", lambda ev: self.commit_edit())
        e.bind("<FocusOut>", lambda ev: self.win.after(1, self.commit_edit))

    def commit_edit(self):
        self.commit_para_edit()
        if not self.editing:
            return
        m, var, e, win, new, x0, cy = self.editing
        self.editing = None
        text = var.get().strip()
        try:
            self.canvas.delete(win)
            e.destroy()
        except tk.TclError:
            pass
        if not text:
            self.app.marks[self.page()["uid"]] = [x for x in self.marks() if x["id"] != m["id"]]
            return
        m["text"] = text
        if new:  # placed at the click as its left edge: centre it from there
            w = text_width(text, m["size"]) * self.s
            m["x"], m["y"] = self.from_canvas(x0 + w / 2 + 1, cy)
        self.draw_mark(m)
        self.select(("mark", m["id"]))

    def resize(self, step):
        if self.mode == "edit":
            pe = self.para_edit
            if pe is None:
                self.hint.config(text="Click into some text first, or choose ＋ Text.")
                return
            pe["size"] = max(4, min(96, round(pe["size"] * (1.1 if step > 0 else 1 / 1.1), 1)))
            pe["font"].configure(size=-max(4, round(pe["size"] * self.s)))
            self.fit_para_box()
            pe["text"].focus_set()
            return
        m = self.selected_mark()
        if m is None:
            self.hint.config(text="Select an item on the page first (click it).")
            return
        if m["kind"] in ("signature", "initials"):
            m["w"] = max(20, min(500, m["w"] * (1.15 if step > 0 else 1 / 1.15)))
        elif m["kind"] in SHAPES:
            m["width"] = max(0.5, min(12, m.get("width", 2) + step))
        elif m.get("size"):
            m["size"] = max(6, min(72, m["size"] + 2 * step))
        self.show()

    def delete_sel(self):
        if self.mode == "edit":
            self.edit_delete()
            return
        if self.sel is None:
            return
        uid = self.page()["uid"]
        if self.sel[0] == "mark":
            self.app.marks[uid] = [m for m in self.marks() if m["id"] != self.sel[1]]
        else:
            self.app.new_fields[uid] = [f for f in self.app.new_fields.get(uid, []) if f["id"] != self.sel[1]]
        self.sel = None
        self.show()

    def translate(self, item, dx, dy):
        """Move a comment, placed item or new field by (dx, dy) points, in page coordinates."""
        if "x" in item:
            item["x"] += dx
            item["y"] += dy
        if "rects" in item:
            item["rects"] = [(l + dx, b + dy, r + dx, t + dy) for l, b, r, t in item["rects"]]
        if "rect" in item:
            l, b, r, t = item["rect"]
            item["rect"] = (l + dx, b + dy, r + dx, t + dy)
        if "points" in item:
            item["points"] = [(x + dx, y + dy) for x, y in item["points"]]
        if "strokes" in item:
            item["strokes"] = [[(x + dx, y + dy) for x, y in s] for s in item["strokes"]]

    # ---- mouse -------------------------------------------------------------- #
    def _hit(self, cx, cy):
        for item in reversed(self.canvas.find_overlapping(cx - 3, cy - 3, cx + 3, cy + 3)):
            for t in self.canvas.gettags(item):
                if t.startswith("m") and t not in ("mark", "match", "mcert") and t[1:].isdigit():
                    return ("mark", int(t[1:]))
                if t.startswith("f") and t != "field" and t[1:].isdigit():
                    return ("field", int(t[1:]))
        return None

    def on_press(self, e):
        self.commit_edit()
        self.canvas.focus_set()
        cx, cy = self.ev(e)
        self.drag = None
        if self.mode == "edit":
            self.edit_press(cx, cy)
            return
        hit = self._hit(cx, cy)
        movable = hit and ((hit[0] == "field" and self.mode == "form") or
                           (hit[0] == "mark" and self.mode in ("comment", "fill")))
        if movable and not self.tool:
            self.select(hit)
            self.drag = dict(kind="move", x=cx, y=cy, start=self.from_canvas(cx, cy), moved=False)
            return
        if self.mode == "view" or self.tool in TEXT_MARKUP:
            i = self.char_at(cx, cy)
            self.clear_selection()
            if i is not None:
                self.drag = dict(kind="text", a=i)
            return
        if not self.tool or not self.on_page(cx, cy):
            self.select(None)
            return
        tool = self.tool
        if tool in ("rect", "ellipse", "arrow", "certificate") or (self.mode == "form" and tool != "check"):
            self.drag = dict(kind="shape", tool=tool, x=cx, y=cy, item=None)
        elif tool == "ink":
            self.drag = dict(kind="ink", points=[(cx, cy)], items=[])
        elif self.mode == "form" and tool == "check":
            x, y = self.from_canvas(cx, cy)
            self.set_tool(None)
            self.add_field("check", (x - 7, y - 7, x + 7, y + 7))
        else:
            self.set_tool(None)
            self.add_mark(tool, cx, cy)

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        cx, cy = self.ev(e)
        if d["kind"].startswith("e"):
            self.edit_drag(d, cx, cy)
        elif d["kind"] == "move":
            if not d["moved"] and abs(cx - d["x"]) + abs(cy - d["y"]) < 3:
                return
            d["moved"] = True
            tag = f"m{self.sel[1]}" if self.sel[0] == "mark" else f"f{self.sel[1]}"
            self.canvas.move(tag, cx - d["x"], cy - d["y"])
            self.canvas.move("selbox", cx - d["x"], cy - d["y"])
            d["x"], d["y"] = cx, cy
        elif d["kind"] == "text":
            j = self.char_at(cx, cy)
            if j is not None:
                a, b = sorted((d["a"], j))
                self.selection = (self.page()["uid"], a, b - a + 1)
                self.draw_selection()
        elif d["kind"] == "shape":
            if d["item"]:
                self.canvas.delete(d["item"])
            col = _hex(COMMENT_COLORS[self.color]) if self.mode == "comment" else "#7a3cc8"
            if d["tool"] == "arrow":
                d["item"] = self.canvas.create_line(d["x"], d["y"], cx, cy, fill=col, width=2, arrow="last")
            elif d["tool"] == "ellipse":
                d["item"] = self.canvas.create_oval(d["x"], d["y"], cx, cy, outline=col, width=2)
            else:
                d["item"] = self.canvas.create_rectangle(d["x"], d["y"], cx, cy, outline=col, width=2,
                                                         dash=(4, 2) if self.mode != "comment" else None)
            d["end"] = (cx, cy)
        elif d["kind"] == "ink":
            px, py = d["points"][-1]
            if abs(cx - px) + abs(cy - py) >= 2:
                d["points"].append((cx, cy))
                d["items"].append(self.canvas.create_line(px, py, cx, cy, fill=_hex(COMMENT_COLORS[self.color]),
                                                          width=max(1, 2 * self.s), capstyle="round"))

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        if d["kind"].startswith("e"):
            self.edit_release(d)
        elif d["kind"] == "move" and d["moved"]:
            x, y = self.from_canvas(*self.ev(e))
            item = self.selected_mark() if self.sel[0] == "mark" else self.selected_field()
            if item is not None:
                self.translate(item, x - d["start"][0], y - d["start"][1])
            self.show()
        elif d["kind"] == "text" and self.selection and self.tool in TEXT_MARKUP:
            rects = self.text_rects(self.selection[1], self.selection[2])
            if rects:
                m = self.new_mark(self.tool, rects=merge_line_rects(rects),
                                  text=self.selected_text().replace("\r\n", " "))
                self.clear_selection()
                self.show()  # highlights are blended into the page; the tool stays on for the next passage
        elif d["kind"] == "shape" and "end" in d:
            if d["item"]:
                self.canvas.delete(d["item"])
            (x0, y0), (x1, y1) = (d["x"], d["y"]), d["end"]
            if abs(x1 - x0) + abs(y1 - y0) < 6:
                return
            tool = d["tool"]
            self.set_tool(None)
            if tool == "arrow":
                m = self.new_mark("arrow", points=[self.from_canvas(x0, y0), self.from_canvas(x1, y1)])
                self.draw_mark(m)
                self.select(("mark", m["id"]))
            elif tool == "certificate":
                self.place_certificate(self.page_rect(x0, y0, x1, y1))
            elif self.mode == "form":
                self.add_field(tool, self.page_rect(x0, y0, x1, y1))
            else:
                m = self.new_mark(tool, rect=self.page_rect(x0, y0, x1, y1))
                self.draw_mark(m)
                self.select(("mark", m["id"]))
        elif d["kind"] == "ink":
            for it in d["items"]:
                self.canvas.delete(it)
            if len(d["points"]) > 1:
                m = self.new_mark("ink", strokes=[[self.from_canvas(x, y) for x, y in d["points"]]])
                self.draw_mark(m)

    def on_double(self, e):
        hit = self._hit(*self.ev(e))
        if not hit or hit[0] != "mark":
            return
        self.sel = hit
        m = self.selected_mark()
        if m and m["kind"] in ("text", "box"):
            self.edit_text(m)
        elif m and m["kind"] == "note":
            self.edit_note(m)

    def on_menu(self, e):
        menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="Copy", command=self.copy, state="normal" if self.selected_text() else "disabled")
        if self.selected_text():
            for kind, label in (("highlight", "Highlight"), ("underline", "Underline"), ("strike", "Strike through")):
                menu.add_command(label=label, command=lambda k=kind: self.markup_selection(k))
        menu.tk_popup(e.x_root, e.y_root)

    def markup_selection(self, kind):
        rects = self.text_rects(self.selection[1], self.selection[2])
        if rects:
            self.new_mark(kind, rects=merge_line_rects(rects), text=self.selected_text().replace("\r\n", " "))
            self.clear_selection()
            self.show()

    def on_wheel(self, e):
        if sys.platform == "darwin":
            step = -e.delta
        elif e.num == 4:
            step = -1
        elif e.num == 5:
            step = 1
        else:
            step = -1 if e.delta > 0 else 1
        top, bottom = self.canvas.yview()
        if step > 0 and bottom >= 0.999 and self.k < len(self.app.pages) - 1:
            self.go(1)  # scrolling past the end of a page goes to the next
        elif step < 0 and top <= 0.001 and self.k > 0:
            self.go(-1)
        else:
            self.canvas.yview_scroll(int(step) * 2, "units")

    # ---- Edit PDF: the page's own text and pictures ------------------------------ #
    def page_layout(self, p=None):
        """The page's paragraphs and images (read once per version of the page)."""
        p = p or self.page()
        key = (p["doc"], p["index"])
        if key not in self.layout:
            doc = self.app.docs[p["doc"]]
            paras, images = [], []
            try:
                with pikepdf.open(io.BytesIO(doc.data), password=doc.password) as pdf:
                    _, glyphs, images, fonts = read_page_content(pdf, pdf.pages[p["index"]])
                    paras = [q for q in text_blocks(glyphs) if q["angle"] == 0 and q["text"].strip()]
                    for q in paras:
                        fi = fonts.get(q["font"])
                        q["style"] = (fi.serif, fi.mono, fi.bold, fi.italic, fi.base) if fi else \
                            (False, False, False, False, "")
            except Exception as e:
                log_error(f"Read page layout: {type(e).__name__}: {e}")
            self.layout[key] = (paras, images)
        return self.layout[key]

    def draw_layout(self, p):
        c = self.canvas
        if self.app._total_rot(p) != 0:
            self.hint.config(text="This page is turned. Turn it upright (in Organize) to edit its text and pictures.")
            return
        paras, images = self.page_layout(p)
        for k, im in enumerate(images):
            x0, y0, x1, y1 = self.canvas_rect(image_box(im["ctm"]))
            if k == self.esel:
                c.create_rectangle(x0, y0, x1, y1, outline=UI["accent"], width=2, tags=("lay", "esel"))
                c.create_rectangle(x1 - 5, y1 - 5, x1 + 5, y1 + 5, fill="#ffffff", outline=UI["accent"], width=2,
                                   tags=("lay", "esel"))
            else:
                c.create_rectangle(x0, y0, x1, y1, outline="#9db7e0", dash=(3, 3), tags="lay")
        for q in paras:
            x0, y0, x1, y1 = self.canvas_rect(q["box"])
            c.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1, outline="#9db7e0", dash=(3, 3), tags="lay")

    def _display_family(self, style):
        """A screen font that looks like the PDF's font (the same one if it's installed)."""
        serif, mono, bold, italic, base = style
        cache = PageEditor._families = getattr(PageEditor, "_families", {})
        if base not in cache:
            fam = None
            try:
                hit = find_system_font(base) if base else None
                if hit:
                    from fontTools.ttLib import TTCollection, TTFont
                    f = TTCollection(hit[0], lazy=True).fonts[hit[1]] if hit[0].lower().endswith(".ttc") else \
                        TTFont(hit[0], lazy=True)
                    fam = f["name"].getDebugName(1)
                    if fam not in tkfont.families(self.win):
                        fam = None
            except Exception:
                fam = None
            if not fam:
                if sys.platform == "darwin":
                    fam = "Courier" if mono else "Times" if serif else "Helvetica"
                else:
                    fam = "Courier New" if mono else "Times New Roman" if serif else "Arial"
            cache[base] = fam
        return cache[base]

    def edit_press(self, cx, cy):
        p = self.page()
        if self.app._total_rot(p) != 0 or not self.on_page(cx, cy):
            self.select_image(None)
            return
        if self.tool == "addtext":
            self.set_tool(None)
            self.start_para_edit(None, cx, cy)
            return
        if self.tool == "addimage":
            self.drag = dict(kind="enew", x=cx, y=cy, item=None, end=(cx, cy))
            return
        paras, images = self.page_layout()
        if self.esel is not None and self.esel < len(images):
            b = self.canvas_rect(image_box(images[self.esel]["ctm"]))
            if abs(cx - b[2]) <= 8 and abs(cy - b[3]) <= 8:
                self.drag = dict(kind="eresize", box=b, item=None, end=b)
                return
        x, y = self.from_canvas(cx, cy)
        for q in paras:
            b = q["box"]
            if b[0] - 2 <= x <= b[2] + 2 and b[1] <= y <= b[3]:
                self.select_image(None)
                self.start_para_edit(q, cx, cy)
                return
        for k in reversed(range(len(images))):
            b = image_box(images[k]["ctm"])
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                self.select_image(k)
                self.drag = dict(kind="emove", x=cx, y=cy, dx=0, dy=0, box=self.canvas_rect(b))
                return
        self.select_image(None)

    def select_image(self, k):
        if k != self.esel:
            self.esel = k
            self.canvas.delete("lay")
            self.draw_layout(self.page())
        if k is not None:
            self.hint.config(text="Drag to move it; drag the corner to resize. Del removes it; Replace image swaps it.")

    def edit_drag(self, d, cx, cy):
        c = self.canvas
        if d["kind"] == "emove":
            c.move("esel", cx - d["x"] - d["dx"], cy - d["y"] - d["dy"])
            d["dx"], d["dy"] = cx - d["x"], cy - d["y"]
            return
        if d["item"]:
            c.delete(d["item"])
        if d["kind"] == "eresize":
            x0, y0, x1, y1 = d["box"]
            ratio = (y1 - y0) / max(1, x1 - x0)
            w = max(12, cx - x0)
            if (cy - y0) / ratio > w:  # follow whichever way the corner went further
                w = max(12, (cy - y0) / ratio)
            d["end"] = (x0, y0, x0 + w, y0 + w * ratio)
            d["item"] = c.create_rectangle(*d["end"], outline=UI["accent"], width=2, dash=(4, 2))
        else:  # enew
            d["end"] = (cx, cy)
            d["item"] = c.create_rectangle(d["x"], d["y"], cx, cy, outline=UI["accent"], width=2, dash=(4, 2))

    def edit_release(self, d):
        if d.get("item"):
            self.canvas.delete(d["item"])
        k = self.esel
        if d["kind"] == "emove":
            if abs(d["dx"]) + abs(d["dy"]) < 3:
                self.canvas.move("esel", -d["dx"], -d["dy"])
                return
            x0, y0, x1, y1 = d["box"]
            box = self.page_rect(x0 + d["dx"], y0 + d["dy"], x1 + d["dx"], y1 + d["dy"])
            img = self.page_layout()[1][k]
            self.apply_edit(lambda pdf, page: edit_image(pdf, page, img, new_box=box), "Moved a picture.")
        elif d["kind"] == "eresize":
            if d["end"] == d["box"]:
                return
            box = self.page_rect(*d["end"])
            img = self.page_layout()[1][k]
            self.apply_edit(lambda pdf, page: edit_image(pdf, page, img, new_box=box), "Resized a picture.")
        elif d["kind"] == "enew" and self.new_image is not None:
            im, self.new_image = self.new_image, None
            self.set_tool(None)
            (x0, y0), (x1, y1) = (d["x"], d["y"]), d["end"]
            if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
                # A click: the picture's own size (96 per inch), at most half the page wide.
                bx = self.app._page_box(self.page())
                w = min(im.width * 0.75, (bx[2] - bx[0]) * 0.5)
                h = w * im.height / im.width
                px, py = self.from_canvas(x0, y0)
                box = (px, py - h, px + w, py)
            else:
                bx0, by0, bx1, by1 = self.page_rect(x0, y0, x1, y1)
                s = min((bx1 - bx0) / im.width, (by1 - by0) / im.height)
                w, h = im.width * s, im.height * s
                box = (bx0, by1 - h, bx0 + w, by1)
            n = len(self.page_layout()[1])
            if self.apply_edit(lambda pdf, page: add_image(pdf, page, im, box), "Added a picture.") is not False:
                self.select_image(n if n < len(self.page_layout()[1]) else None)

    def apply_edit(self, action, message):
        """Make one change to the page's content. Returns the action's result, or False if it failed."""
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            result = self.app.edit_page_content(self.k, action, message)
        except Exception as e:
            log_error(f"Edit page: {type(e).__name__}: {e}")
            messagebox.showerror("Edit PDF", f"That change couldn't be made.\n\n{explain_error(e)}", parent=self.win)
            return False
        finally:
            self.win.config(cursor="")
        self.textpages.pop((self.page()["doc"], self.page()["index"]), None)
        self.show()
        return result

    def start_para_edit(self, para, cx, cy):
        """Type over a paragraph (or new text) in place."""
        if para is not None:
            style, size = para["style"], para["size"]
            text = para["text"]
            lead = para["leading"] if len(para["lines"]) > 1 else size * 1.2
            bx = self.app._page_box(self.page())
            if len(para["lines"]) > 1:
                width = para["width"] * 1.01
            else:  # one line may grow toward the right margin, as it will in the PDF
                left = para["start"][0] - bx[0]
                width = max(para["width"] * 1.01, bx[2] - para["start"][0] - max(18.0, min(left, 72.0)))
            x, top = self.to_canvas(para["start"][0], para["start"][1])
            fill = _hex(para["fill"])
            if para.get("mixed"):
                self.hint.config(text="This text mixes fonts (like bold words). After editing it uses one font.")
            else:
                self.hint.config(text="Type your changes. Click outside (or Ctrl+Enter) to apply; Esc cancels.")
        else:
            style, size, text, fill = (False, False, False, False, ""), 12.0, "", "#000000"
            lead = size * 1.2
            bx = self.app._page_box(self.page())
            px, py = self.from_canvas(cx, cy)
            width = max(120.0, bx[2] - px - 36)
            x, top = cx, None
            self.hint.config(text="Type the new text. Click outside (or Ctrl+Enter) to add it; Esc cancels.")
        font = tkfont.Font(self.win, family=self._display_family(style), size=-max(4, round(size * self.s)),
                           weight="bold" if style[2] else "normal", slant="italic" if style[3] else "roman")
        if top is not None:
            top -= font.metrics("ascent")  # the first line's baseline lands on the PDF's
        else:
            top = cy - font.metrics("ascent") * 0.6
        if para is not None:  # hide the old text while typing over it
            x0, y0, x1, y1 = self.canvas_rect(para["box"])
            self.canvas.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1, fill="#ffffff", outline="", tags="pecover")
        t = tk.Text(self.canvas, font=font, wrap="word", bd=0, padx=0, pady=0, highlightthickness=1,
                    highlightbackground=UI["accent"], highlightcolor=UI["accent"], bg="#ffffff", fg=fill,
                    insertbackground=fill, undo=True)
        t.insert("1.0", text)
        win = self.canvas.create_window(x - 1, top - 1, window=t, anchor="nw", width=max(40, width * self.s + 4),
                                        height=font.metrics("linespace") + 4)
        self.para_edit = dict(para=para, text=t, win=win, size=size, start_size=size, font=font, lead=lead,
                              xy=self.from_canvas(x, top + font.metrics("ascent")) if para is None else None)
        t.bind("<Escape>", lambda e: (self.commit_para_edit(cancel=True), "break")[1])
        t.bind("<Control-Return>", lambda e: (self.commit_para_edit(), "break")[1])
        t.bind("<Command-Return>" if sys.platform == "darwin" else "<Alt-Return>",
               lambda e: (self.commit_para_edit(), "break")[1])
        t.bind("<KeyRelease>", lambda e: self.fit_para_box())
        self.fit_para_box()
        t.focus_set()
        t.update_idletasks()
        t.mark_set("insert", f"@{int(cx - x)},{int(cy - top)}" if para is not None else "end")

    def fit_para_box(self):
        pe = self.para_edit
        if pe is None:
            return
        t, font = pe["text"], pe["font"]
        line_px = pe["lead"] * pe["size"] / pe["start_size"] * self.s
        extra = max(0, round(line_px - font.metrics("linespace")))
        t.configure(spacing2=extra, spacing3=extra)
        t.update_idletasks()
        try:
            n = t.count("1.0", "end", "displaylines")
            n = n[0] if isinstance(n, tuple) else n
        except tk.TclError:
            n = None
        n = max(1, n or int(t.index("end-1c").split(".")[0]))
        self.canvas.itemconfig(pe["win"], height=round(n * (font.metrics("linespace") + extra)) + 6)

    def commit_para_edit(self, cancel=False):
        pe, self.para_edit = self.para_edit, None
        if pe is None:
            return
        new = pe["text"].get("1.0", "end-1c").strip()
        try:
            self.canvas.delete(pe["win"])
            self.canvas.delete("pecover")
            pe["text"].destroy()
        except tk.TclError:
            pass
        self.hint.config(text="")
        if cancel:
            return
        para, size = pe["para"], pe["size"]
        if para is None:
            if new:
                x, y = pe["xy"]
                self.apply_edit(lambda pdf, page: add_text(pdf, page, x, y + size * 0.8, new, size=size), "Added text.")
            return
        sized = abs(size - pe["start_size"]) > 0.05
        if " ".join(new.split()) == " ".join(para["text"].split()) and not sized:
            return
        note = self.apply_edit(lambda pdf, page: edit_paragraph(pdf, page, para, new, size=size if sized else None),
                               "Edited text." if new else "Deleted text.")
        if note:
            self.hint.config(text=note)
            self.app._status(note)

    def edit_delete(self):
        pe = self.para_edit
        if pe is not None and pe["para"] is not None:
            pe["text"].delete("1.0", "end")
            self.commit_para_edit()
        elif pe is not None:
            self.commit_para_edit(cancel=True)
        elif self.esel is not None:
            img = self.page_layout()[1][self.esel]
            self.esel = None
            self.apply_edit(lambda pdf, page: edit_image(pdf, page, img, delete=True), "Removed a picture.")
        else:
            self.hint.config(text="Click a picture to select it, or into text, first.")

    def _ask_picture(self, title):
        path = filedialog.askopenfilename(parent=self.win, title=title, filetypes=[
            ("Pictures", " ".join("*" + e for e in sorted(IMAGE_EXTS))), ("All files", "*.*")])
        if not path:
            return None
        try:
            im = Image.open(path)
            im = ImageOps.exif_transpose(im)
            im.load()
            return im
        except Exception as e:
            messagebox.showerror("Edit PDF", f"That picture couldn't be opened.\n\n{explain_error(e)}", parent=self.win)
            return None

    def pick_new_image(self):
        self.commit_para_edit()
        im = self._ask_picture("Add a picture")
        if im is not None:
            self.new_image = im
            self.set_tool("addimage")

    def replace_image(self):
        self.commit_para_edit()
        if self.esel is None:
            self.hint.config(text="Click the picture to replace first.")
            return
        img = self.page_layout()[1][self.esel]
        im = self._ask_picture("Replace the picture with")
        if im is not None:
            self.apply_edit(lambda pdf, page: edit_image(pdf, page, img, replace_with=im), "Replaced a picture.")

    def edit_undo(self):
        self.commit_para_edit(cancel=True)
        if not self.app.undo_stack:
            return
        self.app.undo()
        self.esel = None
        self.k = min(self.k, len(self.app.pages) - 1)
        self.show()

    # ---- certificate signature ------------------------------------------------ #
    def place_certificate(self, rect):
        p = self.page()
        if self.app._total_rot(p) != 0:
            messagebox.showinfo("Certificate signature", "Certificate signatures can only be placed on pages "
                                "that aren't rotated. Turn this page upright first.", parent=self.win)
            return
        if (rect[2] - rect[0]) < 60 or (rect[3] - rect[1]) < 25:
            messagebox.showinfo("Certificate signature", "Draw a bigger box (at least about 2 × 1 cm).", parent=self.win)
            return

        def done(cert):
            self.app.cert_request = dict(cert, uid=p["uid"], rect=rect)
            self.show()
            self.hint.config(text="The certificate signature is added when you save, as the very last step.")
        CertificateDialog(self.win, done)

    def done(self):
        self.commit_edit()
        for uid in list(self.app.marks):
            self.app.marks[uid] = [m for m in self.app.marks[uid] if m["kind"] not in ("text", "box", "note") or m.get("text")]
            if not self.app.marks[uid]:
                del self.app.marks[uid]
        with PDFIUM_LOCK:
            for pg, tp in self.textpages.values():
                tp.close()
                pg.close()
        self.app._fill_done(self.before)
        self.win.destroy()


def merge_line_rects(rects, tol=2.0):
    """PDFium gives one rectangle per run of text; join the ones on the same line."""
    out = []
    for l, b, r, t in sorted(rects, key=lambda q: (-q[3], q[0])):
        if out and abs(out[-1][1] - b) < tol and abs(out[-1][3] - t) < tol and l - out[-1][2] < 6:
            pl, pb, pr, pt = out[-1]
            out[-1] = (min(pl, l), min(pb, b), max(pr, r), max(pt, t))
        else:
            out.append((l, b, r, t))
    return out


class CompareWindow:
    """Compare two PDFs page by page: what changed in the text, and where on the page."""
    def __init__(self, app):
        self.app = app
        self.results = []
        self.k = 0
        w = self.win = tk.Toplevel(app.root)
        w.title("Compare two PDFs — PDF Toolkit")
        w.configure(bg=UI["canvas"])
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"{min(1300, sw - 80)}x{min(880, sh - 100)}")
        w.transient(app.root)
        bar = tk.Frame(w, bg=UI["appbar"], height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="⇄  Compare", bg=UI["appbar"], fg="#ffffff", font=UI["title"]).pack(side="left", padx=14)
        FlatButton(bar, "  Close  ", w.destroy, UI["appbar"], primary=True, font=UI["bold"], pady=6).pack(
            side="right", padx=14, pady=8)
        pick = tk.Frame(w, bg=UI["toolbar"], highlightthickness=1, highlightbackground=UI["border"])
        pick.pack(fill="x")
        self.a, self.b = tk.StringVar(), tk.StringVar()
        first = next((d.path for d in app.docs.values() if d.path.lower().endswith(".pdf")), "")
        self.a.set(first)
        for row, (label, var) in enumerate((("Original", self.a), ("Changed", self.b))):
            tk.Label(pick, text=label, bg=UI["toolbar"], font=UI["bold"], width=9, anchor="w").grid(row=row, column=0, padx=(14, 4), pady=4)
            ttk.Entry(pick, textvariable=var, width=90).grid(row=row, column=1, pady=4, sticky="ew")
            ttk.Button(pick, text="Choose…", command=lambda v=var: self.choose(v)).grid(row=row, column=2, padx=6)
        self.go_btn = FlatButton(pick, "  Compare  ", self.run, UI["toolbar"], primary=True, font=UI["bold"], pady=6)
        self.go_btn.grid(row=0, column=3, rowspan=2, padx=(8, 14))
        pick.columnconfigure(1, weight=1)
        self.summary = tk.Label(w, text="Choose the original and the changed version, then click Compare.",
                                bg=UI["canvas"], fg=UI["text"], font=UI["font"], anchor="w")
        self.summary.pack(fill="x", padx=14, pady=(8, 4))
        body = tk.Frame(w, bg=UI["canvas"])
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        left = tk.Frame(body, bg=UI["panel"], width=300, highlightthickness=1, highlightbackground=UI["border"])
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Pages", bg=UI["panel"], font=UI["bold"], anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        self.pages = tk.Listbox(left, height=10, font=UI["font"], activestyle="none", relief="flat",
                                highlightthickness=0, selectbackground=UI["sel_bg"], selectforeground=UI["text"])
        self.pages.pack(fill="x", padx=6)
        self.pages.bind("<<ListboxSelect>>", lambda e: self.show(self.pages.curselection()[0]) if self.pages.curselection() else None)
        tk.Label(left, text="Text changes on this page", bg=UI["panel"], font=UI["bold"], anchor="w").pack(fill="x", padx=10, pady=(10, 2))
        self.changes = tk.Text(left, wrap="word", font=UI["small"], relief="flat", bg=UI["panel"], height=20)
        self.changes.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.changes.tag_configure("removed", foreground="#b42318", overstrike=True)
        self.changes.tag_configure("added", foreground="#067647")
        self.view = tk.Canvas(body, bg=UI["canvas"], highlightthickness=0)
        self.view.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.view.bind("<Configure>", lambda e: self.show(self.k) if self.results else None)
        center_over(w, app.root)

    def choose(self, var):
        p = filedialog.askopenfilename(title="Choose a PDF", parent=self.win, filetypes=[("PDF files", "*.pdf")],
                                       initialdir=self.app.v["last_dir"].get())
        if p:
            var.set(p)

    def run(self):
        a, b = self.a.get().strip(), self.b.get().strip()
        if not (os.path.isfile(a) and os.path.isfile(b)):
            messagebox.showwarning("Compare", "Choose two PDF files.", parent=self.win)
            return
        self.summary.config(text="Comparing…")
        self.go_btn.set_enabled(False)

        def ask_pw(path):
            box = {}
            ev = threading.Event()
            self.app.ui(lambda: (box.__setitem__("pw", simpledialog.askstring(
                "Password needed", f"{os.path.basename(path)} is password-protected.\n\nPassword:", show="•",
                parent=self.win)), ev.set()))
            ev.wait()
            return box.get("pw")

        def open_doc(path):
            data = open(path, "rb").read()
            try:
                with PDFIUM_LOCK:
                    return pdfium.PdfDocument(data)
            except pdfium.PdfiumError:
                pw = ask_pw(path)
                with PDFIUM_LOCK:
                    return pdfium.PdfDocument(data, password=pw)

        def work():
            try:
                da, db = open_doc(a), open_doc(b)
                results = []
                n = max(len(da), len(db))
                for i in range(n):
                    r = dict(i=i, a=None, b=None, boxes=[], words=[], status="")
                    with PDFIUM_LOCK:
                        ta = da[i].get_textpage().get_text_range() if i < len(da) else ""
                        tb = db[i].get_textpage().get_text_range() if i < len(db) else ""
                        if i < len(da):
                            r["a"] = da[i].render(scale=1.4).to_pil().convert("RGB")
                        if i < len(db):
                            r["b"] = db[i].render(scale=1.4).to_pil().convert("RGB")
                    if r["a"] is None:
                        r["status"] = "added page"
                    elif r["b"] is None:
                        r["status"] = "removed page"
                    else:
                        ia, ib = r["a"], r["b"]
                        if ia.size != ib.size:
                            ib = ib.resize(ia.size, Image.Resampling.LANCZOS)
                        r["boxes"] = diff_boxes(ia, ib)
                        r["scale_b"] = (r["b"].width / ia.width, r["b"].height / ia.height)
                        r["words"] = word_changes(ta, tb)
                        r["status"] = (f"{len(r['words'])} text change{'s' * (len(r['words']) != 1)}" if r["words"] else
                                       f"{len(r['boxes'])} visual change{'s' * (len(r['boxes']) != 1)}" if r["boxes"] else
                                       "no changes")
                    results.append(r)
                with PDFIUM_LOCK:
                    da.close()
                    db.close()
                self.app.ui(lambda: self.done(results))
            except Exception as e:
                log_error(f"Compare: {type(e).__name__}: {e}")
                self.app.ui(lambda e=e: (self.summary.config(text=f"Couldn't compare: {explain_error(e)}"),
                                         self.go_btn.set_enabled(True)))
        threading.Thread(target=work, daemon=True).start()

    def done(self, results):
        self.go_btn.set_enabled(True)
        self.results = results
        changed = [r for r in results if r["status"] != "no changes"]
        self.summary.config(text=(f"{len(changed)} of {len(results)} pages differ." if changed else
                                  f"No differences found in {len(results)} pages.") +
                            "  Red boxes show where; the list shows the text that changed.")
        self.pages.delete(0, "end")
        for r in results:
            self.pages.insert("end", f"  Page {r['i'] + 1}: {r['status']}")
            if r["status"] != "no changes":
                self.pages.itemconfig("end", foreground="#b42318")
        first = next((r["i"] for r in results if r["status"] != "no changes"), 0)
        self.pages.selection_set(first)
        self.show(first)

    def show(self, k):
        self.k = k
        r = self.results[k]
        c = self.view
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        half = (W - 30) / 2
        self.photos = []
        for col, (key, title) in enumerate((("a", "Original"), ("b", "Changed"))):
            x = 10 + col * (half + 10)
            c.create_text(x + half / 2, 12, text=title, font=UI["bold"], fill=UI["text"])
            im = r[key]
            if im is None:
                c.create_text(x + half / 2, H / 2, text="(no page)", fill=UI["muted"], font=UI["font"])
                continue
            s = min(half / im.width, (H - 40) / im.height)
            shown = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(shown)
            self.photos.append(photo)
            ox, oy = x + (half - shown.width) / 2, 28
            c.create_image(ox, oy, image=photo, anchor="nw")
            c.create_rectangle(ox, oy, ox + shown.width, oy + shown.height, outline="#9d93bf")
            sx, sy = (1, 1) if key == "a" else r.get("scale_b", (1, 1))
            for x0, y0, x1, y1 in r["boxes"]:
                c.create_rectangle(ox + x0 * sx * s, oy + y0 * sy * s, ox + x1 * sx * s, oy + y1 * sy * s,
                                   outline="#e0442b", width=2)
        t = self.changes
        t.config(state="normal")
        t.delete("1.0", "end")
        if r["status"] in ("added page", "removed page"):
            t.insert("end", f"This whole page was {r['status'].split()[0]}.")
        elif not r["words"]:
            t.insert("end", "No text changes." + (" Something else changed (pictures, layout or colours): "
                                                   "see the red boxes." if r["boxes"] else ""))
        for kind, words in r["words"]:
            t.insert("end", ("− " if kind == "removed" else "+ "), kind)
            t.insert("end", words + "\n", kind)
        t.config(state="disabled")


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


def open_file(path):
    """Open a file in its usual app (the PDF reader, for a PDF)."""
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        log_error(f"Open {path}: {e}")


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
    image into a PDF, merge, compress, stamp, password-protect and render, fill,
    comment and sign, edit a page's own text and pictures, and load drag and drop."""
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
        # Fill & Sign: placed text and a typed signature, flattened into the page.
        signed = pikepdf.open(io.BytesIO(image_to_pdf(img, "A4")))
        box, rot = page_boxes(signed)[0]
        sig = typed_signature("Test", next(iter(handwriting_fonts().values()), None))
        marks = [dict(id=1, kind="text", text="Filled in", size=12, x=200, y=700, rot=rot),
                 dict(id=2, kind="signature", image=sig, w=120, size=None, x=300, y=600, rot=rot)]
        signed.pages[0].obj.Annots = pikepdf.Array([mark_annotation(signed, m, box) for m in marks])
        signed.flatten_annotations("all")
        buf = io.BytesIO()
        signed.save(buf)
        doc = pdfium.PdfDocument(buf.getvalue())
        if "Filled in" not in doc[0].get_textpage().get_text_range():
            return 4
        # Comments, a new form field and a certificate signature.
        pdf = pikepdf.open(io.BytesIO(image_to_pdf(img, "A4")))
        page = pdf.pages[0]
        box, rot = page_boxes(pdf)[0]
        page.obj.Annots = pikepdf.Array([comment_annotation(pdf, dict(id=3, kind="highlight", color=(1, 1, 0),
                                                                      rects=[(100, 700, 300, 712)], rot=0), box),
                                         comment_annotation(pdf, dict(id=4, kind="ink", color=(1, 0, 0), width=2,
                                                                      strokes=[[(50, 50), (80, 90), (120, 60)]], rot=0), box)])
        add_form_fields(pdf, page, [dict(id=5, kind="text", name="Name", rect=(100, 500, 300, 520), value="X")], 0)
        pdf.generate_appearance_streams()
        unsigned = os.path.join(tmp, "unsigned.pdf")
        pdf.save(unsigned)
        cert = create_certificate("Self Test", "", "selftest", os.path.join(tmp, "test.p12"))
        signed = os.path.join(tmp, "signed.pdf")
        sign_pdf_file(unsigned, signed, dict(path=cert, password="selftest", page=0, rect=(300, 100, 520, 160),
                                             reason="Self-test", image=sig))
        sigs = check_signatures(open(signed, "rb").read())
        if not (len(sigs) == 1 and sigs[0]["intact"] and sigs[0]["name"] == "Self Test"):
            return 5
        # Edit PDF: change a paragraph's text in place, move a picture, and embed
        # an installed font (fontTools).
        pdf = pikepdf.open(io.BytesIO(blank_pdf(612, 792)))
        page = pdf.pages[0]
        add_text(pdf, page, 72, 720, "Original words here", size=14)
        add_image(pdf, page, Image.new("RGB", (40, 30), (200, 30, 30)), (72, 500, 152, 560))
        _, glyphs, images, _ = read_page_content(pdf, page)
        edit_paragraph(pdf, page, text_blocks(glyphs)[0], "Replaced words")
        _, _, images, _ = read_page_content(pdf, page)
        edit_image(pdf, page, images[0], new_box=(300, 400, 380, 460))
        if find_system_font("ArialMT") and embed_system_font(pdf, "ArialMT", "Hello") is None:
            return 7
        buf = io.BytesIO()
        pdf.save(buf)
        text = pdfium.PdfDocument(buf.getvalue())[0].get_textpage().get_text_range()
        check = pikepdf.open(io.BytesIO(buf.getvalue()))
        _, _, images, _ = read_page_content(check, check.pages[0])
        if "Replaced words" not in text or "Original" in text or \
                [round(v) for v in image_box(images[0]["ctm"])] != [300, 400, 380, 460]:
            return 6
        root = tk.Tk()
        root.withdraw()
        from tkinterdnd2 import TkinterDnD
        TkinterDnD.require(root)
        root.destroy()
        return 0
    except Exception as e:
        log_error(f"Self-test failed: {type(e).__name__}: {e}")
        return 3


SUPPORT_URL = "https://ko-fi.com/collinbrooksdev"


def support_link(parent, bg=None, font=None):
    """A small "Support this project" link that opens the Ko-fi page in the browser."""
    import webbrowser
    kw = {"bg": bg} if bg else {}
    link = tk.Label(parent, text="\u2665 Support this project", fg="#c8364f", cursor="hand2",
                    font=font or ("", 9, "underline"), **kw)
    link.bind("<Button-1>", lambda e: webbrowser.open(SUPPORT_URL))
    return link


# The app's icon (64 px PNG), for the window and the taskbar. The .exe and the Mac app get
# theirs from assets/ when they're built.
APP_ICON = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAOa0lEQVR42s1be4wd5XX/nfPNvbve9frN2ryxHR7OxiEOJDKk"
    "yZpHmj5UECHjVjQoia1EjQE3KkWVolTXK9RUVdMSGii0Cm1FG7XyIkCJAo5CAFeQqgIh2zSIlKoFJAI2UGzv2uvdO985/eOb"
    "xzfPe5d47Y41vrPz/M45v/P6fTOE+S8UhiEfOvRBAoDp6Tdp8eIzFadh8Z89Ojqmk5OhALQwYwnD0IRhaPD/fAnD3SYMd/c9"
    "Tup9Sofd74QAwK99vLPE2iMblcxFotFZCm07rTPAQPyfWzjZZgBSsQ/ZNiO/nzk/jOJ1DABEgOka4res4f8cag+/+MMf7njP"
    "naDU6eyiiYkJed8KCMPQTE5OWgC4atPvf8oYs11UriXms5gDEMiNIfeLmr+Tx1G82cd1tce8bRAUgEgEhRwEm6cAPPCTH/3B"
    "EwkiJie32nkrIBH+qst/72LCoj8j4HrmAFYiqIoCsNkg6gaLsuCNQvvXcLafUSl4dg9AAUMckAkGoRAIsAcqf/Tknq8daFJC"
    "pQLGxzvB3r0T0fjlO3/HwNzPHCzt2lkBoETEBKLSoEuCFYX3BaQGwXudV3wGQbNrVEECImq1FrGIPa6qO5/cs/OBOiWYOuE/"
    "9ZGdO1rc+jtROygSRcRsiIhBTCAuwz9d4cGXQWCQbzniwvme8Ln9XD7XE14rz2EiZiYisjayIAwEwaLrzl177dxj39/+r+Pj"
    "neC11/ZKLQIS2H9y062/3TaD/xLZOavO6OyMXhx0Auq8j4PmC/eiMlG2OAjqWb3kEpSJouk1qmBjW63hYK57/Mt7H7/tu0Uk"
    "UD7a79IrL9u5LhDeB+iQQsGx8D7M8xZFXuiSgFRAC2piRRnuigYlFd0PMSr8e4EgUCUyQmQsG/nYkz+45UCn0+EkO6S5ptMB"
    "AFLqyt1MwWIREYBYXIBxN+UYfum+CtiiAHcQNBZGCfFvceVsBaVrJgzXuER8v1R498zkeod1JhEBmVa7G+k9lUEwgf4VY1+9"
    "Mgjaz1q1lpgMIQ97ioUkykdlAsEExkNGU1aoC2jl9CeiTtEFBGUQR2m/ah5hqYJUbdAaMlZmfv2Zx27dk7hCUKg2doAYKpFC"
    "M2unHhXnGwV58c4N5MjhY7CieWUBFQGMahSSVw4RYWRkCMwcWzI+pshdmyBFNX+/FKXIECsgFTG3ANgDTPp3hV558R0jkZn+"
    "L2YzqlAlF0xTq1IOAVSwNnDDjVdg2fJhiGheeB9s5IUdf9vP5wqwIRyftnjokefRtRbtVhui6glXgYTYDRIEJMjRTAFKZEhV"
    "p0jwgX/78Y5DgFIQIuRJTNrZYObDAcyoFavETFCNrU+p9Sm1dyaDg5fFzV+8CitXLTmpdf3y4fNw34O7MRdZtFqtDGFO2hQQ"
    "aSxQLyAq4piDBDkkKsLBohGh2csBPBaGk8yHxl1XB7GXKAxEYEUAEXcTUYVofENVuH+usk9WKxZHjhyDtYJu18Jaed9rFLnf"
    "9961+OjG9bh121aoWsxFEZg5tmZc/irFY0z8nCAKt8YKUSWouL+tkoAMRHgDABw6dAalWUBE1qi6yCmqEBQE10Qh2ervY0Mw"
    "hk/aGgQGR6cFmz60Hrdt2woVi7luBCLOBEQmqNuHwr74F/F4Y6WJ6hoUWzIRbUtsca0QMBVa4nMkrwgsQBduDOPotOCjG9dh"
    "57YQookSCOI9X3w0CHLjF1CsLIq3ARC3ywoAaQKjJNr7AiaukGgzQUiitIVYiIAgYExNCy7buA5f2/45p4QoAoi8sXljBWW/"
    "ieWVYgW5NfJIk1zTnaQSTU+uUEIcH4oWONkqUAWiLhBFgCrjfw8LLr1kHXZ+4UZEkaAb2VTYxPqJC7v9sdCgdHwudnBO7KwO"
    "EAZMrASqGpBLfapZ9KfYr1ROvvVbbWDFaMyDKABiRJHg01evw+Di63HnXz2KFpss6ifpUbP0qF6mTwMncayEogI4i65xu1Xq"
    "u5NUnBRHSAPLyYU9ACwaAtZvqCpcFVeNnIU/vz/AXFfAQeCsn2W7HFGSWDspwd2xCgUIGJzU3LmTKV+waKZRIqRRdiGWYmhR"
    "BZgIMzNdWLEQGJBS9nzNj1uLFWjcd4CpAgG50pLjP73Kq6SM+IF+4FyAIFhF4TD5fu1ZnbyxeqxSQpg429XFAJ+CKpScWQ3o"
    "azUpNRWK07G4gObWRDDkFVHsUtGkAC60nKXe3KO3yA8sSWSlUyq+JMKTF6+KgldyCJxjnPMISE/kambG68FTRWgdVhceAUi5"
    "BGogTAoKYePWagSYPOlQS1P7CNCMvDil8jsCJYM+1wueohoZymtjQE4BPfg3YlcMCJ9y42e+zQXeocgaccGItTEAnvDczOHV"
    "cnOnFgGpPyuqkVvpAk0xoISAXgwOx9VRfRAUK1nhRISYaoCIZFfE+12uV0eqxCmO2WOXcv5fwRPWUfW5/aauDuAaBDRMUhD1"
    "dAE2XChmHGNkava7Y9Q/AnLprw4FhRjQ7ALc4PfFeYEYAahWQNS1eOHZV3F8ehYKYP2G1Thv/Uocfvc4XnzudYhVcMBYe9EZ"
    "OGftCgDAOwen8NILbwAATsx0cdHGM3HBhaugxbLQnxgpxSrOK8Sfb6BeLoAqehs1CvBndcrWPHG8i4lbHkZ7IMDw4gG8/t/v"
    "4s6/DbH67CW4/abvYe3Fo7CR4O23juK6z1+G2//0N/DSC2/g9pu+h/UbRjF1dBbb/3AcF1y4CiKaukmqfK6Ycap1A47bgDoF"
    "JIUQ98ijpYiqubSSg3/g9t9826/gs1/6GL5wzf149MHnseMb12JwqIXOvZ/FB8ZW45/ueRZ3f2MPrr5uDMtWDGFwqI17Hv4i"
    "Vq0ZyZEjORQQu5k9Ms1Br267ig9wB03ev7hmPs+PwnVBMO4eD/7iKH5+4Bd48/XDOGftCrBh2EigqmgPBLjpq1dgzblLceDf"
    "Xwex67D+8TvP4L4/eQKvvvKOu1Wu5awKgtzHSlmtU18KU+8Y4B9T1CJArGBoZAA/emg/nvz+z/CRzedj+x1b8OZr72XUOoAo"
    "EkRdi4HBIHWnV195BzYSfPIzl8S61DwnnQSzNA1SdS1QcFsi6jMN1roACsFGa+sADhgz03O4cdvHsfUrmzE07Ki41155ByZg"
    "dLsWU0dO4G+++RNMHTmBT/zqRXj7rSkAwNfvuh6rz17i2afoAk2CUqUs1F8z1EcMqKoIa5Zj07MIWgZDw23MnojQHjAQUXS7"
    "Fnfe9giirsXcbIQ//s4NOHfdSvzPy29jdqaLqSMzOGPNCBQop0W/okvdryb3E4HStjgJgqaOD+A+EJAPgkQK0nxgSaA9OBjg"
    "rn/+PFafswyqQLvt5g8vHFuNex/5ElQExhis3zCKxUsHoQpcuvk83PeDbTj7guVgQ6jlW2szVrZNJbfQfBrcUkJAXRlc/QBH"
    "TFB9FjCMSzefX2I4Fi8ZxKYrzs/HizjNLV0xhMs+sbaZFEERASjDvdJ43KsZ6i8IUtqKUmMMcIFQ49tSjtpKo3p866wUdse4"
    "sRqkCgXk4d4YF+ZPiHivvFARfpoVGZUooEr0Uo2ATceqK8E6uDc1Q3W9ANfDnZpQwXTKGSG/3K2Hew0jNJ9egH7JLLCQCiAw"
    "iDlHilaSIX1TYjmtFeFeo+UeMWBBCRE/DTa1wf45bHIKYGBLLgZQuvZRXjKVouqp4wMrSmFuKIGZK3uBHAKoqhLsWRajkRBZ"
    "OB1UtMO+pamuU6zpBZiJ+qaVqKIUptNEiRXSYL0SsiygaV+9xUdA0O1JiNQq4PQEwbrSt6QElIqhroeAp2MI0MFKX2lUQPa3"
    "tYrIClQUojTvmNbfXKFTdvY2WhLQGoiR0ltqDBAdTBUwOjoWT6K2fq5q4d4HrouiqKCd3aCWLRlEYLji7eOTvywfGQAlU+N1"
    "1q52BVIIiMzL7k5PI3CfmQBtY/fPWXmXuLVSkbzrRn20mwpogAcf3o8VS9uO7S0VRoVX5HJFftVrc4XjlE3GEjOmZtTNDLNx"
    "kyPoyxWU2RjbnTnWHhh4HgD24mkhIPuoYOM1D+w2raHPRdGMJaKgX3pJNcLhI0cRRXN5YYgL02tcIlWJ0NjRVSk9aC/C0mXL"
    "wcFAj5iVHVOCNQNL2XaP/fjFey/8DDrKmCAJChOOf82Q0JWA3Jfvu2qxjZWrzujZPSYKIfSRbhuFYohyPYvtk6DetBgxE4L2"
    "vQAQjoEmc5iLNfKhT//DE0Fr+JooOm6JjamddpoXEVnRtNQoB/1DuvlY7uVpWDMwYqQ789yBlWs3A7uA4tviicMFxuwUtSfI"
    "tOKJ4Jo3tfuqFPPXNVeX1OMeFecw9/jbOPSblqqK8MDgLZggCV8aozIrPEEShrt5356bXxLYHaa92Cgb6yifmgckimCqH4RX"
    "XudIFy4MnItla41glcdM9X0BVQ6iYNGKQDS6fd9fnPVcGGrug4kcAiYnt9rx8U7wH4//7t9Hc8e/HrSXBGADBdlmy3AeFd4x"
    "4n4QUle793mMvRI3vq8SWbBBa2hVK5qd+uaLd5//7fHOU8HkJNmeNUiaFX7roa8Qt+8mEwza7oyNYRx/N9Ts/9RPvOirdu/N"
    "9nrbqkQCEMzAiFEVC+nesf/b597lLE92Hp/NOSV8+LpHN1EQfAscXE1kIPYEVFSU3YOKg6NKId+HMkANc/6+IpLiDEzcYm4P"
    "g5gh0v0paXTHvr8856d1wvfx4WT2gdGlNzz+m2roy6oyzmZgGZl2WfDGAXMfluSGtzuopv6PKW+1EDt3lDh4Bhx8d/+3Vj3i"
    "ZKgXvr8yvNNhTOzS5KPkD4ZPrTFsN0HoEiI9E6B26dPW5ENH5vKUNBPyn9l655P/2ax3TsrhceGeBGLqKpu3iNsvQ3jf/rtW"
    "vVFM7ThZHyWjo6eh7Zvn0lEOQzUnuxHLvTQcbgUfOvR072u3/DKSzO/i0THo5M92KXp8LF1c/g+RoejetO5mIwAAAABJRU5E"
    "rkJggg=="
)


def set_app_icon(root):
    try:
        if sys.platform == "win32":
            # Its own taskbar button and icon, not Python's, when run as a script.
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OfflineToolkit.PDFToolkit")
        root._app_icon = tk.PhotoImage(master=root, data=APP_ICON)
        root.iconphoto(True, root._app_icon)
    except Exception:
        pass  # just the default icon


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    root = tk.Tk()
    set_app_icon(root)
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
