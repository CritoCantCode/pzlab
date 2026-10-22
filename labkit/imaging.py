"""Image saving with Pillow when available (fast), else the stdlib PNG fallback.
Set PZLAB_NO_PIL=1 to force the fallback."""

import os
import shutil

from . import png


def have_pil():
    if os.environ.get("PZLAB_NO_PIL"):
        return False
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _crop_scale_pil(img, crop, scale):
    if crop:
        x, y, w, h = crop
        img = img.crop((x, y, x + w, y + h))
    if scale:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    return img


def save_png_file(src, dest, crop=None, scale=None):
    """Copies a PNG to dest, cropped/scaled if asked."""
    if not crop and not scale:
        shutil.copyfile(src, dest)
    elif have_pil():
        from PIL import Image
        with Image.open(src) as img:
            _crop_scale_pil(img, crop, scale).save(dest)
    else:
        width, height, channels, pixels = png.read(src)
        width, height, pixels = png.crop_scale(width, height, channels, pixels, crop, scale)
        png.write(dest, width, height, pixels, channels)


def save_bgrx(dest, width, height, buffer, crop=None, scale=None):
    """Saves a top-down 32-bit BGRX buffer (as returned by GetDIBits) as PNG."""
    if have_pil():
        from PIL import Image
        img = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
        _crop_scale_pil(img, crop, scale).save(dest)
        return
    raw = memoryview(buffer)
    rgb = bytearray(width * height * 3)
    rgb[0::3] = raw[2::4]
    rgb[1::3] = raw[1::4]
    rgb[2::3] = raw[0::4]
    width, height, rgb = png.crop_scale(width, height, 3, rgb, crop, scale)
    png.write(dest, width, height, rgb, 3)
