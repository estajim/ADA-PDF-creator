"""Generate ada_pdf_icon.icns for macOS packaging. Called by CI and local builds."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

try:
    from PIL import Image, ImageDraw
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
    from PIL import Image, ImageDraw

SIZES = [16, 32, 64, 128, 256, 512, 1024]
ISET  = ROOT / "build" / "ADA_PDF_Converter.iconset"
ICNS  = ROOT / "build" / "ada_pdf_icon.icns"
ISET.mkdir(parents=True, exist_ok=True)

for sz in SIZES:
    img  = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad  = sz // 10
    draw.ellipse([pad, pad, sz - pad, sz - pad], fill=(91, 79, 232, 255))
    pw, ph = int(sz * .38), int(sz * .46)
    px, py = (sz - pw) // 2, (sz - ph) // 2 - int(sz * .04)
    c = max(1, sz // 20)
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=c, fill=(255, 255, 255, 240))
    dog = int(pw * .28)
    draw.polygon([px + pw - dog, py, px + pw, py + dog, px + pw - dog, py + dog],
                 fill=(200, 195, 245, 255))
    bh = max(1, sz // 28)
    bw = int(pw * .55)
    bx = px + int(pw * .14)
    sp = max(2, sz // 18)
    for i in range(3):
        by2 = py + ph // 3 + i * sp
        draw.rectangle([bx, by2, bx + bw, by2 + bh], fill=(91, 79, 232, 200))
    img.save(ISET / f"icon_{sz}x{sz}.png")
    if sz <= 512:
        img.resize((sz * 2, sz * 2), Image.LANCZOS).save(ISET / f"icon_{sz}x{sz}@2x.png")

subprocess.check_call(["iconutil", "-c", "icns", str(ISET), "-o", str(ICNS)])
print(f"Icon generated: {ICNS}")
