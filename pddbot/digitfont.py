"""数据中心数字反爬字体解码。

商品数据接口里的访客数、成交金额等数字，返回的是私有区字符（如 \\ue487），
页面再用一个反爬字体（spider-font）把它们画成数字；字符与数字的对应关系每次加载都变。

解法：拿到本次加载的字体文件，把每个字形的轮廓在网格上采样成点阵，
和内置的 0~9 参考点阵比对（汉明距离最近者），得到「私有区字符 → 数字」映射。
只比形状、不依赖字符编码，所以编码怎么换都能解。
"""
from __future__ import annotations

import io

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.pointInsidePen import PointInsidePen
from fontTools.ttLib import TTFont

GRID_W, GRID_H = 10, 14

# 0~9 的参考点阵（GRID_W×GRID_H，按行展开），由一次真实加载的 spider-font 生成。
REF: dict[str, str] = {
    "0": "00011110000111001110011000011011000000111100000011110000001111000000111100000011110000001111000000111100000011011000011001110111100001111000",
    "1": "00000001110000001111000111111111111111110000000111000000011100000001110000000111000000011100000001110000000111000000011100000001110000000111",
    "2": "00011111000011001110011000001111000000111100000011000000001100000001100000001100000011100000111000000111000000011000000011110000001111111111",
    "3": "00011110000111001110011000011001000000100000000110000000111000001111000000001110000000001110000000111100000011110000001101110011100011111000",
    "4": "00000011000000001100000001110000001111000001101100000100110000100011000110001100110000110011111111111111111111000000110000000011000000001100",
    "5": "00111111100010000000011000000001100000000110000000011111110001100001100000000011000000001100000000111100000011110000011001111111100011111000",
    "6": "00011111000011001110011000011001000000111100000000110111110011110011101110000011110000001111000000111100000011011000001101111011100001111100",
    "7": "11111111111111111111000000011000000011000000001000000001100000001100000000110000000110000000011000000001100000001100000000110000000011000000",
    "8": "00011110000111101110011000011001000000100110000110011100110000111111000111001110110000001111000000111100000011111000001101111111100011111100",
    "9": "00011110000111011110111000011011000000111100000011110000001111100001110111001111001111101100000000111100000011010000011001110111100011111000",
}


class DecodeError(Exception):
    """字体解析失败或字形与参考点阵对不上（字体换了样式），此时不应使用数据。"""


def _bitmap(glyph_set, name: str) -> str:
    bp = BoundsPen(glyph_set)
    glyph_set[name].draw(bp)
    if not bp.bounds:
        return "0" * (GRID_W * GRID_H)
    x0, y0, x1, y1 = bp.bounds
    bits = []
    for j in range(GRID_H):  # 从上到下
        y = y1 - (j + 0.5) * (y1 - y0) / GRID_H
        for i in range(GRID_W):
            x = x0 + (i + 0.5) * (x1 - x0) / GRID_W
            pen = PointInsidePen(glyph_set, (x, y))
            glyph_set[name].draw(pen)
            bits.append("1" if pen.getResult() else "0")
    return "".join(bits)


def glyph_bitmaps(font_bytes: bytes) -> dict[int, str]:
    """字体里每个私有区字符 → 点阵。"""
    try:
        font = TTFont(io.BytesIO(font_bytes))
        cmap = font.getBestCmap()
        gs = font.getGlyphSet()
    except Exception as e:
        raise DecodeError(f"字体文件解析失败：{e}") from e
    return {cp: _bitmap(gs, name) for cp, name in cmap.items() if 0xE000 <= cp <= 0xF8FF}


def _dist(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


# 超过这个比例的点不一致就认为不是同一个数字（字体样式变了）
MAX_DIFF = 0.10


def build_map(font_bytes: bytes) -> dict[str, str]:
    """返回 {私有区字符: '0'~'9'}。对不上时抛 DecodeError。"""
    bitmaps = glyph_bitmaps(font_bytes)
    if len(bitmaps) < 10:
        raise DecodeError(f"字体里只有 {len(bitmaps)} 个数字字形")
    limit = int(GRID_W * GRID_H * MAX_DIFF)
    mapping: dict[str, str] = {}
    for cp, bm in bitmaps.items():
        digit, d = min(((k, _dist(bm, v)) for k, v in REF.items()), key=lambda t: t[1])
        if d > limit:
            raise DecodeError(f"字形 {hex(cp)} 与参考数字差异过大（{d} 点），字体可能换了样式")
        mapping[chr(cp)] = digit
    if sorted(mapping.values()) != list("0123456789"):
        raise DecodeError(f"解出的数字不是 0~9 各一个：{sorted(mapping.values())}")
    return mapping


def decode(s, mapping: dict[str, str]):
    """把接口返回的字符串还原成正常数字字符串；非字符串原样返回。"""
    if not isinstance(s, str):
        return s
    out = []
    for c in s:
        if 0xE000 <= ord(c) <= 0xF8FF:
            if c not in mapping:
                raise DecodeError(f"遇到字体里没有的字符 {hex(ord(c))}")
            out.append(mapping[c])
        else:
            out.append(c)
    return "".join(out)


def to_number(s) -> float | None:
    """'1,234' / '5.02%' / '485.40' → float（百分比转成 0~1）；空值返回 None。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = s.strip().replace(",", "")
    if not t or t in ("-", "--"):
        return None
    pct = t.endswith("%")
    try:
        v = float(t.rstrip("%"))
    except ValueError:
        return None
    return v / 100 if pct else v


def _gen_ref(font_path: str, known: dict[int, str]) -> None:
    """开发用：用一份已知对应关系的字体生成 REF。"""
    bms = glyph_bitmaps(open(font_path, "rb").read())
    for cp, digit in sorted(known.items(), key=lambda t: t[1]):
        print(f'    "{digit}": "{bms[cp]}",')
