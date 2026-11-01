#!/usr/bin/env python3
"""黄历每日图片推送：生成速览卡并通过企业微信群机器人发送。"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from lunar_python import Solar
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("huangli")

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 配色（规范）
COLOR_BG = (0xE8, 0xF1, 0xEA)
COLOR_DEEP_GREEN = (0x1F, 0x5C, 0x4D)
COLOR_INK = (0x1C, 0x2B, 0x2A)
COLOR_MUTED = (0x4A, 0x5A, 0x57)
COLOR_STROKE = (0x9F, 0xB0, 0xAB)
COLOR_GOLD = (0xC9, 0x8A, 0x00)
COLOR_RED = (0xC0, 0x39, 0x2B)
COLOR_WHITE = (255, 255, 255)
COLOR_GOLD_DEEP = (0xA0, 0x6E, 0x00)  # 底部宜命中提示

CARD_WIDTH = 750
PAD_X = 48
TOP_BAR_H = 84
CHIP_H = 52
CHIP_GAP = 12
CHIP_RADIUS = 10
CHIP_PAD_X = 18
SECTION_CHIP_GAP = 28  # 「宜」「忌」标签下方到事项芯片的间距

# 设计字体：苹方简体（PingFang SC）。macOS / GitHub macos runner 自带。
PINGFANG_TTC = "/System/Library/Fonts/PingFang.ttc"
# PingFang.ttc face：SC Regular=2，SC Medium=5，SC Semibold=8
PINGFANG_SC_REGULAR = 2
PINGFANG_SC_SEMIBOLD = 8

DEFAULT_FONT_REGULAR = PINGFANG_TTC
DEFAULT_FONT_BOLD = PINGFANG_TTC
FALLBACK_FONTS = [
    PINGFANG_TTC,
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

MAX_ATTEMPTS = 3  # 首次 + 重试 2 次
RETRY_INTERVAL_SEC = 5


@dataclass
class DayInfo:
    target: date
    year: int
    month: int
    day: int
    week_label: str  # 如「星期六」
    lunar_month_day: str  # 农历九月廿九
    gan_zhi_line: str  # 丙午年 己亥月 乙酉日 · 开日
    sheng_xiao: str
    jie_qi: str
    yi: list[str] = field(default_factory=list)
    ji: list[str] = field(default_factory=list)


def parse_keys(raw: str | None) -> list[str]:
    text = (raw if raw is not None else os.environ.get("HIGHLIGHT_KEYS", "交易")).strip()
    if not text:
        return ["交易"]
    return [k.strip() for k in text.split(",") if k.strip()]


def match_keys(items: list[str], keys: list[str]) -> list[str]:
    """返回事项文本中包含任一关键词的项（保持原序、去重）。"""
    if not items or not keys:
        return []
    hit: list[str] = []
    seen: set[str] = set()
    for item in items:
        if any(k in item for k in keys) and item not in seen:
            hit.append(item)
            seen.add(item)
    return hit


def beijing_today() -> date:
    return datetime.now(TZ_SHANGHAI).date()


def parse_target_date(raw: str | None) -> date:
    if not raw:
        return beijing_today()
    return date.fromisoformat(raw.strip())


def fetch_day(target: date) -> DayInfo:
    solar = Solar.fromYmd(target.year, target.month, target.day)
    lunar = solar.getLunar()
    week = f"星期{solar.getWeekInChinese()}"
    lunar_md = f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
    zhi_xing = lunar.getZhiXing()
    gan_zhi_line = (
        f"{lunar.getYearInGanZhi()}年 "
        f"{lunar.getMonthInGanZhi()}月 "
        f"{lunar.getDayInGanZhi()}日 · {zhi_xing}日"
    )
    return DayInfo(
        target=target,
        year=target.year,
        month=target.month,
        day=target.day,
        week_label=week,
        lunar_month_day=lunar_md,
        gan_zhi_line=gan_zhi_line,
        sheng_xiao=lunar.getYearShengXiao(),
        jie_qi=lunar.getJieQi() or "",
        yi=list(lunar.getDayYi() or []),
        ji=list(lunar.getDayJi() or []),
    )


def _ttc_face_index(path: str, *, bold: bool = False) -> int:
    """选择 TTC 中的简体面。苹方优先 SC；Noto CJK OTC 中 SC 多为 index 2。"""
    name = os.path.basename(path).lower()
    if "pingfang" in name:
        return PINGFANG_SC_SEMIBOLD if bold else PINGFANG_SC_REGULAR
    if "noto" in name and "cjk" in name and path.lower().endswith(".ttc"):
        if "sc" in name:
            return 0
        return 2  # JP=0 KR=1 SC=2 TC=3
    return 0


def _load_font(path: str, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    index = _ttc_face_index(path, bold=bold)
    try:
        font = ImageFont.truetype(path, size=size, index=index)
    except OSError:
        font = ImageFont.truetype(path, size=size, index=0)
    return font


def resolve_font_path(env_name: str, default: str) -> str:
    configured = os.environ.get(env_name, "").strip() or default
    candidates = [configured, *FALLBACK_FONTS]
    for path in candidates:
        if path and os.path.isfile(path):
            if path != configured and configured == default:
                logger.warning("默认字体不存在，改用 %s", path)
            elif path != configured:
                logger.warning("%s=%s 不可用，改用 %s", env_name, configured, path)
            return path
    raise FileNotFoundError(
        f"找不到字体：请设置 {env_name}（设计字体为苹方简体，默认 {default}）"
    )


def display_items(items: list[str], empty_label: str) -> list[str]:
    """空列表时用占位文案；占位不参与高亮。"""
    if not items:
        return [empty_label]
    return items


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def layout_chips(
    labels: list[str],
    font: ImageFont.ImageFont,
    max_width: int,
) -> tuple[list[list[tuple[str, int]]], int]:
    """返回行列表[(text, chip_width), ...] 与占用高度。"""
    # 用临时 Image 测宽
    probe = Image.new("RGB", (10, 10), COLOR_BG)
    draw = ImageDraw.Draw(probe)
    rows: list[list[tuple[str, int]]] = []
    row: list[tuple[str, int]] = []
    row_w = 0
    for text in labels:
        tw = _text_width(draw, text, font)
        chip_w = tw + CHIP_PAD_X * 2
        need = chip_w if not row else chip_w + CHIP_GAP
        if row and row_w + need > max_width:
            rows.append(row)
            row = [(text, chip_w)]
            row_w = chip_w
        else:
            row.append((text, chip_w))
            row_w += need
    if row:
        rows.append(row)
    height = 0
    if rows:
        height = len(rows) * CHIP_H + (len(rows) - 1) * CHIP_GAP
    return rows, height


def calc_card_height(
    info: DayInfo,
    yi_rows: int,
    ji_rows: int,
    has_jie_qi: bool,
    day_glyph_h: int = 200,
) -> int:
    """根据行数估算画布高度（与 render 布局公式一致，供单测）。"""
    y = TOP_BAR_H + 28
    if has_jie_qi:
        y += 36 + 12
    y += day_glyph_h + 8
    y += 42  # 农历
    y += 40  # 干支
    # 宜
    y += 36 + SECTION_CHIP_GAP
    if yi_rows:
        y += yi_rows * CHIP_H + max(0, yi_rows - 1) * CHIP_GAP
    y += CHIP_GAP  # draw_section 末行后多加的间距
    y += 8
    # 忌
    y += 36 + SECTION_CHIP_GAP
    if ji_rows:
        y += ji_rows * CHIP_H + max(0, ji_rows - 1) * CHIP_GAP
    y += CHIP_GAP
    y += 16
    y += 32  # 提示行
    y += 40  # 底边距
    return y


def render_card(info: DayInfo, keys: list[str]) -> bytes:
    font_regular_path = resolve_font_path("FONT_REGULAR", DEFAULT_FONT_REGULAR)
    font_bold_path = resolve_font_path("FONT_BOLD", DEFAULT_FONT_BOLD)

    font_top = _load_font(font_bold_path, 32, bold=True)
    font_jie = _load_font(font_bold_path, 24, bold=True)
    font_day = _load_font(font_bold_path, 190, bold=True)
    font_lunar = _load_font(font_regular_path, 34, bold=False)
    font_gz = _load_font(font_regular_path, 26, bold=False)
    font_section = _load_font(font_bold_path, 28, bold=True)
    font_chip = _load_font(font_regular_path, 28, bold=False)
    font_tip = _load_font(font_bold_path, 26, bold=True)
    try:
        fam, style = font_chip.getname()
        logger.info("设计字体: %s %s（目标：苹方简体）", fam, style)
    except Exception:
        pass

    yi_show = display_items(info.yi, "诸事不宜")
    ji_show = display_items(info.ji, "无")
    yi_hit = set(match_keys(info.yi, keys))
    ji_hit = set(match_keys(info.ji, keys))

    content_w = CARD_WIDTH - PAD_X * 2
    yi_layout, _ = layout_chips(yi_show, font_chip, content_w)
    ji_layout, _ = layout_chips(ji_show, font_chip, content_w)

    # 先在足够高的画布上绘制，再按实际内容裁切，避免估算误差裁切文字
    canvas_h = max(
        1200,
        calc_card_height(
            info,
            yi_rows=len(yi_layout),
            ji_rows=len(ji_layout),
            has_jie_qi=bool(info.jie_qi),
            day_glyph_h=220,
        )
        + 200,
    )
    img = Image.new("RGB", (CARD_WIDTH, canvas_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # 1. 顶部条
    draw.rectangle((0, 0, CARD_WIDTH, TOP_BAR_H), fill=COLOR_DEEP_GREEN)
    top_text = f"{info.year}年 {info.month}月  {info.week_label}"
    tw = _text_width(draw, top_text, font_top)
    draw.text(
        ((CARD_WIDTH - tw) / 2, (TOP_BAR_H - 32) / 2 - 2),
        top_text,
        fill=COLOR_WHITE,
        font=font_top,
    )

    y = TOP_BAR_H + 28
    cx = CARD_WIDTH / 2

    # 2. 节气标签
    if info.jie_qi:
        label = info.jie_qi
        lw = _text_width(draw, label, font_jie) + 28
        lh = 36
        draw.rounded_rectangle(
            (PAD_X, y, PAD_X + lw, y + lh),
            radius=10,
            fill=COLOR_DEEP_GREEN,
        )
        draw.text((PAD_X + 14, y + 4), label, fill=COLOR_WHITE, font=font_jie)
        y += lh + 12

    # 3. 大号日期（优先用更清晰的数字字形）
    day_text = str(info.day)
    day_font = font_day
    for path in (
        os.environ.get("FONT_BOLD", "").strip(),
        DEFAULT_FONT_BOLD,
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        font_bold_path,
    ):
        if path and os.path.isfile(path):
            try:
                candidate = _load_font(path, 190, bold=True)
                # 确保该字体能量出与 day_text 匹配的宽度
                if _text_width(draw, day_text, candidate) > 0:
                    day_font = candidate
                    break
            except OSError:
                continue
    dw = _text_width(draw, day_text, day_font)
    bbox = draw.textbbox((0, 0), day_text, font=day_font)
    dh = bbox[3] - bbox[1]
    draw.text((cx - dw / 2, y - bbox[1]), day_text, fill=COLOR_INK, font=day_font)
    y += dh + 8

    # 4. 农历
    lw = _text_width(draw, info.lunar_month_day, font_lunar)
    draw.text((cx - lw / 2, y), info.lunar_month_day, fill=COLOR_INK, font=font_lunar)
    y += 42

    # 5. 干支 · 建除
    gw = _text_width(draw, info.gan_zhi_line, font_gz)
    draw.text((cx - gw / 2, y), info.gan_zhi_line, fill=COLOR_MUTED, font=font_gz)
    y += 40

    def draw_section(
        title: str,
        title_color: tuple[int, int, int],
        rows: list[list[tuple[str, int]]],
        hits: set[str],
        solid_color: tuple[int, int, int],
        start_y: int,
    ) -> int:
        tw_ = _text_width(draw, title, font_section) + 24
        th = 36
        draw.rounded_rectangle(
            (PAD_X, start_y, PAD_X + tw_, start_y + th),
            radius=8,
            fill=title_color,
        )
        draw.text(
            (PAD_X + 12, start_y + 3),
            title,
            fill=COLOR_WHITE,
            font=font_section,
        )
        cy = start_y + th + SECTION_CHIP_GAP
        for row in rows:
            x = PAD_X
            for text, chip_w in row:
                box = (x, cy, x + chip_w, cy + CHIP_H)
                if text in hits:
                    draw.rounded_rectangle(
                        box, radius=CHIP_RADIUS, fill=solid_color
                    )
                    text_color = COLOR_WHITE
                else:
                    draw.rounded_rectangle(
                        box,
                        radius=CHIP_RADIUS,
                        outline=COLOR_STROKE,
                        width=2,
                    )
                    text_color = COLOR_INK
                tw_chip = _text_width(draw, text, font_chip)
                draw.text(
                    (x + (chip_w - tw_chip) / 2, cy + (CHIP_H - 28) / 2 - 2),
                    text,
                    fill=text_color,
                    font=font_chip,
                )
                x += chip_w + CHIP_GAP
            cy += CHIP_H + CHIP_GAP
        return cy

    y = draw_section("宜", COLOR_GOLD, yi_layout, yi_hit, COLOR_GOLD, y)
    y += 8
    y = draw_section("忌", COLOR_RED, ji_layout, ji_hit, COLOR_RED, y)
    y += 16

    # 8. 底部提示
    tip, tip_color = build_tip(info.yi, info.ji, keys)
    tip_w = _text_width(draw, tip, font_tip)
    tip_x = cx - tip_w / 2
    draw.text((tip_x, y), tip, fill=tip_color, font=font_tip)
    # 部分字体 textbbox 偏紧，固定按字号 + 底边距裁切，避免提示被切掉
    final_h = int(y + 26 + 64)
    img = img.crop((0, 0, CARD_WIDTH, min(final_h, canvas_h)))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if len(data) >= 2 * 1024 * 1024:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        data = buf.getvalue()
        if len(data) >= 2 * 1024 * 1024:
            raise RuntimeError("生成图片超过 2MB，无法通过企业微信机器人发送")
    return data


def build_tip(
    yi: list[str],
    ji: list[str],
    keys: list[str],
) -> tuple[str, tuple[int, int, int]]:
    yi_hits = match_keys(yi, keys)
    ji_hits = match_keys(ji, keys)
    # 提示展示命中的关键词（规范示例为「宜：交易」）
    def keys_hit_in(items: list[str]) -> list[str]:
        found: list[str] = []
        for k in keys:
            if any(k in it for it in items) and k not in found:
                found.append(k)
        return found

    yi_keys = keys_hit_in(yi)
    ji_keys = keys_hit_in(ji)
    if yi_keys and ji_keys:
        tip = f"宜：{'、'.join(yi_keys)}；忌：{'、'.join(ji_keys)}"
        return tip, COLOR_GOLD_DEEP
    if yi_keys:
        return f"宜：{'、'.join(yi_keys)}", COLOR_GOLD_DEEP
    if ji_keys:
        return f"忌：{'、'.join(ji_keys)}", COLOR_RED
    return "宜忌中没有你关注的事项", COLOR_MUTED


def _mask_webhook(url: str) -> str:
    if "key=" in url:
        head, _, _rest = url.partition("key=")
        return f"{head}key=***"
    if len(url) > 24:
        return url[:16] + "***"
    return "***"


def push_image(png_bytes: bytes, webhook: str | None = None) -> None:
    hook = (webhook if webhook is not None else os.environ.get("WECOM_HOOK", "")).strip()
    if not hook:
        raise RuntimeError("WECOM_HOOK 未设置")

    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(png_bytes).decode("ascii"),
            "md5": hashlib.md5(png_bytes).hexdigest(),
        },
    }

    last_err = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(hook, json=payload, timeout=15)
        except requests.RequestException as exc:
            last_err = f"网络错误: {type(exc).__name__}"
            logger.warning(
                "推送失败 (%s/%s): %s",
                attempt,
                MAX_ATTEMPTS,
                last_err,
            )
        else:
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                logger.warning(
                    "推送失败 (%s/%s): %s",
                    attempt,
                    MAX_ATTEMPTS,
                    last_err,
                )
            else:
                try:
                    body = resp.json()
                except ValueError:
                    last_err = "响应非 JSON"
                    logger.warning(
                        "推送失败 (%s/%s): %s",
                        attempt,
                        MAX_ATTEMPTS,
                        last_err,
                    )
                else:
                    errcode = body.get("errcode")
                    if errcode == 0:
                        logger.info("推送成功")
                        return
                    last_err = f"errcode={errcode} errmsg={body.get('errmsg')}"
                    logger.warning(
                        "推送失败 (%s/%s): %s",
                        attempt,
                        MAX_ATTEMPTS,
                        last_err,
                    )

        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_INTERVAL_SEC)

    # 确保不泄露 webhook
    logger.error("推送最终失败: %s（目标 %s）", last_err, _mask_webhook(hook))
    raise SystemExit(1)


def env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    target = parse_target_date(os.environ.get("TARGET_DATE"))
    keys = parse_keys(None)
    dry_run = env_flag("DRY_RUN")
    only_hit = env_flag("ONLY_PUSH_WHEN_HIT")

    logger.info("目标日期（北京时间口径）: %s", target.isoformat())
    logger.info("高亮关键词: %s", ",".join(keys))

    info = fetch_day(target)
    yi_hits = match_keys(info.yi, keys)
    ji_hits = match_keys(info.ji, keys)
    hit = bool(yi_hits or ji_hits)
    logger.info(
        "农历=%s 节气=%s 宜命中=%s 忌命中=%s",
        info.lunar_month_day,
        info.jie_qi or "无",
        yi_hits or "无",
        ji_hits or "无",
    )

    png = render_card(info, keys)
    out = os.path.abspath("card.png")
    with open(out, "wb") as f:
        f.write(png)
    logger.info("已生成 %s (%s bytes)", out, len(png))

    if dry_run:
        logger.info("DRY_RUN=1，跳过推送")
        return

    if only_hit and not hit:
        logger.info("ONLY_PUSH_WHEN_HIT=1 且未命中，跳过推送")
        return

    push_image(png)


if __name__ == "__main__":
    main()
