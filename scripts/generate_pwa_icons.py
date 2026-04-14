from pathlib import Path

from PIL import Image


BASE_DIR = Path(__file__).resolve().parents[1]
IMG_DIR = BASE_DIR / "static" / "assets" / "img"
SOURCE = IMG_DIR / "mwandeticon.jpg"


def main():
    image = Image.open(SOURCE).convert("RGBA")
    for size, filename in ((192, "pwa-192.png"), (512, "pwa-512.png")):
        resized = image.resize((size, size), Image.LANCZOS)
        resized.save(IMG_DIR / filename, format="PNG")
    print("Generated PWA icons.")


if __name__ == "__main__":
    main()
