"""Draw the SimpliEarn Desk logo: ui/icon.png (Dock, 1024px) and AppIcon.icns (app bundle).

    ../strategies/.venv/bin/python make_icon.py

The mark is a rising green line over a dark tile, the same shape as ui/logo.svg.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from AppKit import (NSBezierPath, NSBitmapImageRep, NSColor, NSCompositingOperationSourceOver, NSGradient,
                    NSGraphicsContext, NSPNGFileType, NSMakeRect, NSRoundLineCapStyle, NSRoundLineJoinStyle)

HERE = Path(__file__).resolve().parent
SIZE = 1024
GREEN = (0x6F / 255, 0xD9 / 255, 0x8A / 255)
# polyline in a 100x100 box, y up (same points as logo.svg, which is y down)
POINTS = [(22, 34), (40, 52), (54, 44), (78, 70)]


def rgb(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


def draw(path: Path) -> None:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, SIZE, SIZE, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    ctx.setCompositingOperation_(NSCompositingOperationSourceOver)

    # macOS icon grid: ~10% margin, continuous-corner tile
    inset, radius = 100, 185
    tile = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(inset, inset, SIZE - 2 * inset, SIZE - 2 * inset), radius, radius)
    NSGradient.alloc().initWithStartingColor_endingColor_(rgb(0.09, 0.12, 0.105), rgb(0.02, 0.025, 0.022)).drawInBezierPath_angle_(tile, -90)
    rgb(1, 1, 1, 0.08).setStroke()
    tile.setLineWidth_(4)
    tile.stroke()

    def pt(x, y):
        return (inset + (SIZE - 2 * inset) * x / 100, inset + (SIZE - 2 * inset) * y / 100)

    # soft glow under the line, then the line, then the end dot
    for width, alpha in ((70, 0.10), (44, 0.16), (26, 1.0)):
        line = NSBezierPath.bezierPath()
        line.moveToPoint_(pt(*POINTS[0]))
        for p in POINTS[1:]:
            line.lineToPoint_(pt(*p))
        line.setLineWidth_(width)
        line.setLineCapStyle_(NSRoundLineCapStyle)
        line.setLineJoinStyle_(NSRoundLineJoinStyle)
        rgb(*GREEN, alpha).setStroke()
        line.stroke()
    x, y = pt(*POINTS[-1])
    rgb(*GREEN).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 34, y - 34, 68, 68)).fill()
    rgb(0.03, 0.08, 0.05).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 14, y - 14, 28, 28)).fill()

    NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(str(path), True)


def main() -> None:
    png = HERE / "ui" / "icon.png"
    draw(png)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "AppIcon.icns")], check=True)
    print("wrote ui/icon.png and AppIcon.icns")


if __name__ == "__main__":
    main()
