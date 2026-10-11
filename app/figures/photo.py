"""原卷裁图：AI 拆题时报告图在第几张照片的哪个框，这里把那一块裁下来存好；家长觉得裁歪了可以重新框。

框用千分比 [左, 上, 右, 下]（0–1000，相对整张照片的宽高），照片换了大小也不变。
裁出来的图按内容的哈希命名存在 data/figs/，同一张图（比如几个小问共用）只存一份。
"""
import hashlib
import io
import logging
import re

from PIL import Image, ImageOps, UnidentifiedImageError

from .. import config
from .base import FigError, Kind, num, register, text

log = logging.getLogger(__name__)
DIR = config.DATA_DIR / "figs"
MAX_PAGE_SIDE = 2400   # 整页照片超过这个边长就缩小（AI 看图也不需要更大）
MAX_FIG_SIDE = 1200
PAD = 15               # 裁的时候四周多留一点（千分比）
NAME = re.compile(r"^[0-9a-f]{20}\.jpg$")


def _jpeg(img: Image.Image, quality=88) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def prepare_page(media_type: str, data: bytes) -> tuple[str, bytes]:
    """上传的整页照片：按手机拍照时的方向摆正、太大的缩小。打不开的图片 raise ValueError。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("图片打不开，请换一张") from e
    if img.getexif().get(0x0112, 1) in (0, 1) and max(img.size) <= MAX_PAGE_SIDE:  # 0x0112：拍照方向
        return media_type, data
    fixed = ImageOps.exif_transpose(img)
    fixed.thumbnail((MAX_PAGE_SIDE, MAX_PAGE_SIDE))
    return "image/jpeg", _jpeg(fixed)


def clean_box(box) -> list[int]:
    if not (isinstance(box, list) and len(box) == 4):
        raise FigError("框要写成 [左, 上, 右, 下]")
    x0, y0, x1, y1 = (num(v, "框的坐标", 0, 1000) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 20 or y1 - y0 < 20:
        raise FigError("框太小")
    return [round(x0), round(y0), round(x1), round(y1)]


def cut(page: bytes, box, page_no: int, alt="") -> dict:
    """从整页照片里裁出一块，存盘，返回题目上的 fig。"""
    box = clean_box(box)
    img = Image.open(io.BytesIO(page))
    img = ImageOps.exif_transpose(img)
    W, H = img.size
    x0, y0, x1, y1 = (max(0, box[0] - PAD), max(0, box[1] - PAD), min(1000, box[2] + PAD), min(1000, box[3] + PAD))
    part = img.crop((round(x0 * W / 1000), round(y0 * H / 1000), round(x1 * W / 1000), round(y1 * H / 1000)))
    part.thumbnail((MAX_FIG_SIDE, MAX_FIG_SIDE))
    data = _jpeg(part, 85)
    name = hashlib.sha1(data).hexdigest()[:20] + ".jpg"
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / name
    if not f.exists():
        f.write_bytes(data)
    return {"kind": "photo", "file": name, "page": page_no, "box": box, "alt": text(alt, 300)}


def from_parse(q: dict, pages: list[bytes]) -> dict | None:
    """AI 拆题结果里的 fig（第几张照片、框）→ 裁好的图；没有图或框不合格就返回 None（题目照样保留）。"""
    fig = q.get("fig")
    if not isinstance(fig, dict) or not pages:
        return None
    try:
        page_no = int(fig.get("page") or q.get("page") or 1)
        if not 1 <= page_no <= len(pages):
            raise FigError("页码超出范围")
        return cut(pages[page_no - 1], fig.get("box"), page_no, fig.get("alt") or "")
    except (FigError, ValueError, TypeError, OSError) as e:
        log.info("裁图失败（第 %s 题）：%s", q.get("label"), e)
        return None


def path(name: str):
    return DIR / name if NAME.match(name or "") else None


@register
class Photo(Kind):
    id, label, drawn = "photo", "原卷上的图", False

    def normalize(self, spec):
        f = path(spec.get("file"))
        if not f or not f.exists():
            raise FigError("裁好的图找不到了")
        return {"kind": self.id, "file": spec["file"], "page": int(num(spec.get("page", 1), "页码", 1, 100)),
                "box": clean_box(spec.get("box")), "alt": text(spec.get("alt"), 300)}

    def describe(self, spec):
        return "题目配图（原卷照片）：" + (spec.get("alt") or "（没有文字描述）")
