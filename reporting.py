from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_FONT_CANDIDATES = (
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size, index=1 if bold and path.suffix == ".ttc" else 0)
            except OSError:
                continue
    return ImageFont.load_default()


def write_pdf_report(path: Path, result: dict, annotated_bgr) -> None:
    """Generate a local, rasterized A4 report with stable Chinese rendering."""
    import cv2

    page = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(page)
    navy = "#15303b"
    blue = "#1c6681"
    muted = "#61727a"
    line = "#c9d4d9"
    title_font = _font(46, bold=True)
    heading_font = _font(25, bold=True)
    body_font = _font(20)
    small_font = _font(16)
    count_font = _font(34, bold=True)

    draw.text((90, 70), "颗粒度检测报告", font=title_font, fill=navy)
    draw.text((90, 132), "PARTICLE SIZE INSPECTION REPORT", font=small_font, fill=blue)
    draw.line((90, 175, 1150, 175), fill=blue, width=4)

    sample = result.get("sample", {})
    info = (
        ("样品编号", sample.get("sample_id") or "未填写"),
        ("批次编号", sample.get("batch_id") or "未填写"),
        ("检测人员", sample.get("operator") or "未填写"),
        ("检测日期", sample.get("inspection_date") or "未填写"),
    )
    for index, (label, value) in enumerate(info):
        col = index % 2
        row = index // 2
        x = 90 + col * 530
        y = 215 + row * 62
        draw.text((x, y), label, font=small_font, fill=muted)
        draw.text((x + 120, y - 2), str(value)[:40], font=body_font, fill=navy)

    draw.text((90, 365), "检测结果", font=heading_font, fill=navy)
    draw.line((90, 405, 1150, 405), fill=line, width=2)
    card_width = 210
    for index, bin_item in enumerate(result["bins"]):
        x = 90 + index * (card_width + 18)
        draw.rectangle((x, 430, x + card_width, 545), outline=bin_item["color"], width=4)
        draw.text((x + 14, 446), bin_item["label"], font=small_font, fill=muted)
        draw.text((x + 14, 480), str(bin_item["count"]), font=count_font, fill=navy)
    draw.rectangle((1002, 430, 1150, 545), fill="#eef4f6", outline=blue, width=4)
    draw.text((1018, 446), "合计", font=small_font, fill=muted)
    draw.text((1018, 480), str(result["total"]), font=count_font, fill=navy)

    rgb = cv2.cvtColor(annotated_bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    image.thumbnail((1060, 680), Image.Resampling.LANCZOS)
    image_x = 90 + (1060 - image.width) // 2
    image_y = 600 + (680 - image.height) // 2
    page.paste(image, (image_x, image_y))
    draw.rectangle((90, 600, 1150, 1280), outline=line, width=2)

    draw.text((90, 1325), "校准与追溯", font=heading_font, fill=navy)
    calibration = (
        f"比例换算：{result['scale_px']} px = {result['scale_um']} μm；"
        f"1 px = {result['um_per_px']} μm"
    )
    draw.text((90, 1370), calibration, font=body_font, fill=navy)
    audit = result.get("review_audit", [])
    active_corrections = sum(
        item.get("type") in {"add", "remove", "split"} and not item.get("undone")
        for item in audit
    )
    draw.text(
        (90, 1410),
        f"算法版本：{result.get('algorithm_version', '未知')}　复核操作：{len(audit)} 项　当前修正：{active_corrections} 项",
        font=body_font,
        fill=navy,
    )
    source = result.get("source", {})
    draw.text((90, 1450), f"原图 SHA-256：{source.get('sha256', '未记录')}", font=small_font, fill=muted)
    notes = str(sample.get("notes") or "无")[:120]
    draw.text((90, 1500), f"备注：{notes}", font=body_font, fill=navy)
    draw.line((90, 1605, 1150, 1605), fill=line, width=2)
    draw.text(
        (90, 1630),
        f"报告生成：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}　结果应由授权检测人员复核。",
        font=small_font,
        fill=muted,
    )
    page.save(path, "PDF", resolution=150.0)
