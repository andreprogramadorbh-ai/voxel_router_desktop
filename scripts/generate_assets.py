"""Gera derivados técnicos do ativo institucional, preservando integralmente o logotipo de origem."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "static" / "img" / "logo-voxel-pacs.png"
TARGET = ROOT / "frontend" / "static" / "img" / "router.ico"
SPLASH = ROOT / "frontend" / "static" / "img" / "splash.bmp"


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    canvas = Image.new("RGBA", (512, 512), "#0d1b2a")
    # Mantém toda a marca dentro do quadro do ícone, sem corte de conteúdo.
    source.thumbnail((456, 248), Image.Resampling.LANCZOS)
    card = Image.new("RGBA", (472, 278), "white")
    card.paste(source, ((472 - source.width) // 2, (278 - source.height) // 2), source)
    canvas.alpha_composite(card, (20, 110))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((24, 24, 488, 88), radius=14, fill="#075a9e")
    draw.text((45, 43), "VOXEL ROUTER", fill="white", font=None)
    canvas.save(TARGET, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    splash = Image.new("RGB", (164, 314), "#0d1b2a")
    splash_logo = Image.open(SOURCE).convert("RGB")
    splash_logo.thumbnail((146, 105), Image.Resampling.LANCZOS)
    splash_card = Image.new("RGB", (154, 118), "white")
    splash_card.paste(splash_logo, ((154 - splash_logo.width) // 2, (118 - splash_logo.height) // 2))
    splash.paste(splash_card, (5, 98))
    splash.save(SPLASH, format="BMP")


if __name__ == "__main__":
    main()
