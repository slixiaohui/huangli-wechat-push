"""match_keys 与卡片高度计算单测。"""

from __future__ import annotations

from datetime import date

from huangli import (
    CHIP_GAP,
    CHIP_H,
    DayInfo,
    calc_card_height,
    display_items,
    layout_chips,
    match_keys,
    parse_keys,
)
from PIL import ImageFont


def test_match_keys_substring():
    items = ["开市", "立券", "交易", "嫁娶"]
    assert match_keys(items, ["交易"]) == ["交易"]
    assert match_keys(items, ["开市", "立券"]) == ["开市", "立券"]
    assert match_keys(items, ["市"]) == ["开市"]  # 子串
    assert match_keys(items, ["没有"]) == []


def test_match_keys_dedupe_and_order():
    items = ["交易", "开市", "交易"]
    assert match_keys(items, ["交易", "开市"]) == ["交易", "开市"]


def test_parse_keys_default_and_env(monkeypatch):
    monkeypatch.delenv("HIGHLIGHT_KEYS", raising=False)
    assert parse_keys(None) == ["交易"]
    monkeypatch.setenv("HIGHLIGHT_KEYS", "开市, 立券")
    assert parse_keys(None) == ["开市", "立券"]
    assert parse_keys("祭祀,祈福") == ["祭祀", "祈福"]


def test_display_items_empty_placeholders():
    assert display_items([], "诸事不宜") == ["诸事不宜"]
    assert display_items([], "无") == ["无"]
    assert display_items(["嫁娶"], "诸事不宜") == ["嫁娶"]


def test_calc_card_height_grows_with_rows():
    info = DayInfo(
        target=date(2026, 1, 3),
        year=2026,
        month=1,
        day=3,
        week_label="星期六",
        lunar_month_day="农历冬月十五",
        gan_zhi_line="丙午年 庚子月 甲子日 · 建日",
        sheng_xiao="马",
        jie_qi="",
        yi=["a"] * 3,
        ji=["b"] * 2,
    )
    h1 = calc_card_height(info, yi_rows=2, ji_rows=1, has_jie_qi=False)
    h2 = calc_card_height(info, yi_rows=6, ji_rows=4, has_jie_qi=False)
    h3 = calc_card_height(info, yi_rows=6, ji_rows=4, has_jie_qi=True)
    assert h2 > h1
    assert h3 > h2


def test_layout_chips_wraps():
    # 使用内置默认字体，不依赖系统 CJK 字体文件
    font = ImageFont.load_default()
    labels = [f"item{i:02d}" for i in range(12)]
    rows, height = layout_chips(labels, font, max_width=120)
    assert len(rows) >= 2
    assert height == len(rows) * CHIP_H + (len(rows) - 1) * CHIP_GAP
    flat = [t for row in rows for t, _ in row]
    assert flat == labels
