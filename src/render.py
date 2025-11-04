import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import freetype
import requests
import tinycss2
import uharfbuzz as hb
import unicodedata
from PIL import Image, ImageDraw
from fontTools.ttLib import woff2
from tqdm import tqdm

from src.consts import (
    ASSETS_FOLDER,
    BUILD_FOLDER,
    CONFIG_PATH,
    OUTPUT_MANIFEST_PATH,
    OUTPUT_FOLDER,
    OUTPUT_ICONS_FOLDER,
    PROJECT_NAME,
    PROJECT_VERSION,
    SCALE_FACTOR,
    STREAM_DECK_CONFIG_PATH,
    STREAM_DOCK_CONFIG_PATH,
)


def read_json(path: Path) -> dict | list:
    with open(path, "r") as f:
        return json.load(f)


def download_file(url: str) -> Path:
    response = requests.get(url, stream=True)
    path = ASSETS_FOLDER / Path(urlparse(url).path).name

    with open(path, "wb") as f:
        f.write(response.content)

    return path


def get_woff2_url(path: Path) -> str | None:
    styles = path.read_text()
    rules = tinycss2.parse_stylesheet(styles, True, True)

    for rule in rules:
        if rule.type != "at-rule" or rule.at_keyword != "font-face":
            continue

        declarations = tinycss2.parse_declaration_list(rule.content)

        if declaration := next(
            (
                declaration for declaration in declarations
                if declaration.type == "declaration" and declaration.name == "src"
            ), None,
        ):
            return declaration.value[0].value

    return None


def unpack_woff2(path: Path):
    output_path = path.with_suffix(".ttf")
    woff2.decompress(path, output_path)

    return output_path


def parse_glyphs(path: Path) -> dict[str, list[str]]:
    glyphs = {}
    styles = path.read_text()
    rules = tinycss2.parse_stylesheet(styles, True, True)

    for rule in rules:
        if rule.type != "qualified-rule":
            continue

        selectors = tinycss2.serialize(rule.prelude).strip().split(",")

        if not all(selector.startswith(".fa-") for selector in selectors):
            continue

        declarations = tinycss2.parse_declaration_list(rule.content)

        if not (declaration := next(
            (
                declaration
                for declaration in declarations
                if declaration.type == "declaration" and declaration.name == "--fa"
            ), None,
        )):
            continue

        value = declaration.value[0].value
        names = [selector.replace(".fa-", "") for selector in selectors]
        glyphs[value] = names

    return glyphs


def get_glyph_id(font, char: str, primary: bool = True) -> int:
    buf = hb.Buffer()
    buf.add_str(char)
    buf.guess_segment_properties()
    features = {"ss01": not primary}
    hb.shape(font, buf, features)

    return buf.glyph_infos[0].codepoint


def get_glyph_offset(face: freetype.Face, glyph_id: int):
    face.load_glyph(glyph_id)

    return face.glyph.bitmap_left, face.glyph.bitmap_top


def render_glyph(face, glyph_id: int, color: str, offset: tuple) -> Image.Image | None:
    if glyph_id is None:
        return None

    face.load_glyph(glyph_id)

    if face.glyph.bitmap.width + face.glyph.bitmap.rows == 0:
        return None

    size = (face.glyph.bitmap.width, face.glyph.bitmap.rows)
    img = Image.frombytes("L", size, bytes(face.glyph.bitmap.buffer))
    glyph = Image.new("RGBA", img.size, color)
    glyph.putalpha(img)

    canvas = Image.new(
        "RGBA", (
            face.glyph.bitmap.width + face.glyph.bitmap_left - offset[0],
            face.glyph.bitmap.rows + offset[1] - face.glyph.bitmap_top
        ), (0, 0, 0, 0),
    )
    canvas.paste(glyph, (face.glyph.bitmap_left - offset[0], offset[1] - face.glyph.bitmap_top), glyph)

    return canvas


def render_icon(
    face, glyph_ids: tuple, primary_color: str, secondary_color: str | None, canvas_size: tuple[int, int],
    canvas_color: str, canvas_radius: int,
) -> Image.Image:
    primary_glyph_offset = get_glyph_offset(face, glyph_ids[0])
    secondary_glyph_offset = get_glyph_offset(face, glyph_ids[1])
    left_offset = min(primary_glyph_offset[0], secondary_glyph_offset[0])
    top_offset = max(primary_glyph_offset[1], secondary_glyph_offset[1])
    primary_glyph = render_glyph(face, glyph_ids[0], primary_color, (left_offset, top_offset))
    secondary_glyph = render_glyph(face, glyph_ids[1], secondary_color, (left_offset, top_offset))

    width = max(primary_glyph.size[0] if primary_glyph else 0, secondary_glyph.size[0] if secondary_glyph else 0)
    height = max(primary_glyph.size[1] if primary_glyph else 0, secondary_glyph.size[1] if secondary_glyph else 0)

    glyph = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    if primary_glyph:
        glyph.paste(primary_glyph, (0, 0), primary_glyph)

    if secondary_glyph:
        glyph.paste(secondary_glyph, (0, 0), secondary_glyph)

    icon = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    mask = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (canvas_size[0] - 1, canvas_size[1] - 1)], radius=canvas_radius, fill=255)
    background = Image.new("RGBA", canvas_size, canvas_color)
    icon.paste(background, (0, 0), mask)
    icon.paste(glyph, ((canvas_size[0] - width) // 2, (canvas_size[1] - height) // 2), glyph)

    return icon


def zip_folder(input_folder: Path, output_path: Path):
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(input_folder):
            for file in files:
                full_path = os.path.join(root, file)
                relative_path = os.path.relpath(full_path, input_folder)
                zipf.write(full_path, f"{input_folder.name}\\{relative_path}")


def slugify(value: str) -> str:
    value = unicodedata.normalize('NFKD', value)
    value = value.encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^a-zA-Z0-9]+', '-', value)

    return value.strip('-').lower()


def render_icon_pack(font, variant, config, glyphs, face, hb_font):
    face.set_char_size(config["icon_size"] * SCALE_FACTOR * 64)
    canvas_size = config["canvas_size"]

    shutil.rmtree(OUTPUT_FOLDER, True)
    OUTPUT_ICONS_FOLDER.mkdir(parents=True)
    icons = []

    glyphs = {
        glyph: glyph_names
        for glyph, glyph_names in glyphs.items()
        if get_glyph_id(hb_font, glyph) or get_glyph_id(hb_font, glyph, False)
    }

    for glyph, glyph_names in tqdm(
        glyphs.items(),
        desc=f"Rendering {font['name']} – {variant['name']} for {config['device']}",
        mininterval=1,
        unit="glyph",
        dynamic_ncols=True,
        colour=variant["primary_color"][:7],
    ):
        primary_glyph_id = get_glyph_id(hb_font, glyph)

        if not primary_glyph_id:
            continue

        secondary_glyph_id = get_glyph_id(hb_font, glyph, False)

        if primary_glyph_id == secondary_glyph_id:
            secondary_glyph_id = None

        icon = render_icon(
            face,
            (primary_glyph_id, secondary_glyph_id),
            variant["primary_color"],
            variant["secondary_color"],
            (canvas_size * SCALE_FACTOR, canvas_size * SCALE_FACTOR),
            variant.get("canvas_color"),
            config["canvas_radius"] * SCALE_FACTOR,
        )
        icon_path = OUTPUT_ICONS_FOLDER / f"{glyph_names[0]}.png"
        icon.resize((canvas_size, canvas_size)).save(icon_path)

        if "house" in glyph_names or "spotify" in glyph_names:
            category_icon_size = round(config["icon_size"] * 1.25 * SCALE_FACTOR)
            icon = render_icon(
                face,
                (primary_glyph_id, secondary_glyph_id),
                variant["primary_color"],
                variant["secondary_color"],
                (category_icon_size, category_icon_size),
                "#00000000",
                0,
            )
            icon.resize((config["category_icon_size"], config["category_icon_size"])).save(OUTPUT_FOLDER / "icon.png")

        icons.append(
            {
                "path": icon_path.name,
                "name": icon_path.with_suffix("").name,
                "tags": glyph_names,
            },
        )

    with open(OUTPUT_FOLDER / "icons.json", "w") as f:
        f.write(json.dumps(icons, indent=4))

    font_name = f"Font Awesome {font['name']} – {variant['name']}"
    manifest = {
        "Name": font_name,
        "Version": PROJECT_VERSION,
        "Description": f"{font_name} Icon Pack consist of {len(glyphs)} various icons.",
        "URL": f"https://github.com/lufton/{PROJECT_NAME}",
        "Author": "Maksym Dubovyk (@Lufton)",
        "Icon": "icon.png",
        "Images": "icons",
    }

    with open(OUTPUT_MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=4)

    icons = [f for f in OUTPUT_ICONS_FOLDER.iterdir() if f.is_file()][:16 * 9]
    image = Image.new("RGBA", (canvas_size * 16, canvas_size * 9), (0, 0, 0, 0))

    for row in range(9):
        for column in range(16):
            icon = Image.open(icons[row * 16 + column])
            image.paste(icon, (column * canvas_size, row * canvas_size), icon)

    image.save(OUTPUT_FOLDER / "thumb_16x9.png")
    image = Image.new("RGBA", (canvas_size * 10, canvas_size * 10), (0, 0, 0, 0))

    for row in range(10):
        for column in range(10):
            icon = Image.open(icons[row * 10 + column])
            image.paste(icon, (column * canvas_size, row * canvas_size), icon)

    image.save(OUTPUT_FOLDER / "thumb_10x10.png")

    output_folder = Path(f"com.github.lufton.{PROJECT_NAME}.{slugify(variant['name'])}.sdIconPack")
    OUTPUT_FOLDER.rename(output_folder)
    zip_folder(output_folder, BUILD_FOLDER / f"{slugify(variant['name'])}.{config['extension']}")
    shutil.rmtree(output_folder, True)


def main():
    shutil.rmtree(ASSETS_FOLDER, True)
    ASSETS_FOLDER.mkdir(exist_ok=True)
    shutil.rmtree(BUILD_FOLDER, True)
    BUILD_FOLDER.mkdir(exist_ok=True)

    stream_deck_config = read_json(STREAM_DECK_CONFIG_PATH)
    stream_dock_config = read_json(STREAM_DOCK_CONFIG_PATH)
    config = read_json(CONFIG_PATH)
    fonts = config["fonts"]
    variants = config["variants"]

    for font in fonts:
        stylesheet = f"https://site-assets.fontawesome.com/releases/{config['version']}/css/{font['stylesheet']}"
        fa_style_path = download_file(urljoin(stylesheet, "./fontawesome.css"))
        font_style_path = download_file(stylesheet)
        glyphs = parse_glyphs(fa_style_path)
        glyphs.update(parse_glyphs(font_style_path))

        if woff2_relative_url := get_woff2_url(font_style_path):
            woff2_path = download_file(urljoin(stylesheet, woff2_relative_url))
            ttf_path = unpack_woff2(woff2_path)
            face = freetype.Face(str(ttf_path))

            hb_blob = hb.Blob.from_file_path(ttf_path)
            hb_face = hb.Face(hb_blob)
            hb_font = hb.Font(hb_face)

            glyphs = {
                glyph: glyph_names
                for glyph, glyph_names in glyphs.items()
                if get_glyph_id(hb_font, glyph) or get_glyph_id(hb_font, glyph, False)
            }

            for variant in variants:
                render_icon_pack(font, variant, stream_deck_config, glyphs, face, hb_font)
                render_icon_pack(font, variant, stream_dock_config, glyphs, face, hb_font)
            else:
                print(
                    "This doesn't look like a valid font style. CSS should contain @font-family rule with src property pointing to woff2 font file.",
                )

            del face, hb_font, hb_face, hb_blob


if __name__ == '__main__':
    main()
