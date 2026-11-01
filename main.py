#!/usr/bin/env python3
"""每日黄历宜忌推送（Server酱 Turbo）。"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from lunar_python import Solar

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_RETRIES = 3
REQUEST_TIMEOUT = 10
RETRY_BASE_SECONDS = 2


def today_solar() -> Solar:
    """按 Asia/Shanghai 取「今天」的公历日期。"""
    now = datetime.now(TZ_SHANGHAI)
    return Solar.fromYmd(now.year, now.month, now.day)


def join_list(items: list[str], empty: str = "无") -> str:
    return "、".join(items) if items else empty


def build_message(solar: Solar) -> tuple[str, str]:
    lunar = solar.getLunar()

    week = f"周{solar.getWeekInChinese()}"
    solar_date = f"{solar.getYear()}年{solar.getMonth()}月{solar.getDay()}日"
    lunar_date = f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"

    title = f"📅 {solar_date} {week} | {lunar_date}"

    gan_zhi = (
        f"{lunar.getYearInGanZhi()}年 "
        f"{lunar.getMonthInGanZhi()}月 "
        f"{lunar.getDayInGanZhi()}日"
    )
    sheng_xiao = lunar.getYearShengXiao()

    lines: list[str] = [
        f"**公历**：{solar_date} {week}",
        f"**农历**：{lunar_date}",
        f"**干支**：{gan_zhi}",
        f"**生肖**：{sheng_xiao}",
    ]

    jie_qi = lunar.getJieQi()
    if jie_qi:
        lines.append(f"**节气**：{jie_qi}")

    festivals: list[str] = []
    for part in (
        solar.getFestivals(),
        solar.getOtherFestivals(),
        lunar.getFestivals(),
        lunar.getOtherFestivals(),
    ):
        for name in part:
            if name and name not in festivals:
                festivals.append(name)
    if festivals:
        lines.append(f"**节日**：{join_list(festivals)}")

    yi = join_list(lunar.getDayYi())
    ji = join_list(lunar.getDayJi())
    chong_sha = f"冲{lunar.getDayChongDesc()} 煞{lunar.getDaySha()}"
    positions = (
        f"财神{lunar.getPositionCai()}（{lunar.getPositionCaiDesc()}） · "
        f"喜神{lunar.getPositionXi()}（{lunar.getPositionXiDesc()}） · "
        f"福神{lunar.getPositionFu()}（{lunar.getPositionFuDesc()}）"
    )
    peng_zu = f"{lunar.getPengZuGan()} {lunar.getPengZuZhi()}"

    desp = "\n\n".join(
        [
            "\n".join(lines),
            f"**宜**\n{yi}",
            f"**忌**\n{ji}",
            f"**冲煞**\n{chong_sha}",
            f"**吉神方位**\n{positions}",
            f"**彭祖百忌**\n{peng_zu}",
        ]
    )
    return title, desp


class PermanentPushError(Exception):
    """业务失败，不应重试（如密钥错误、额度用尽）。"""


class TransientPushError(Exception):
    """网络/服务瞬时错误，可重试。"""


def _safe_request_error(exc: BaseException, send_key: str) -> str:
    """异常信息里可能含带 SendKey 的 URL，打印前脱敏。"""
    text = f"{type(exc).__name__}: {exc}"
    if send_key:
        text = text.replace(send_key, "***")
    return text


def push_once(send_key: str, title: str, desp: str) -> None:
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    try:
        resp = requests.post(
            url,
            data={"title": title, "desp": desp},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TransientPushError(
            f"网络请求失败: {_safe_request_error(exc, send_key)}"
        ) from None

    if resp.status_code == 429:
        raise PermanentPushError("HTTP 429：触发频率限制，停止重试")
    if resp.status_code >= 500:
        raise TransientPushError(f"HTTP {resp.status_code}：服务端错误")
    if resp.status_code != 200:
        raise PermanentPushError(f"HTTP {resp.status_code}：{resp.text[:200]}")

    try:
        payload = resp.json()
    except ValueError:
        raise TransientPushError(
            f"响应不是合法 JSON: {resp.text[:200]}"
        ) from None

    code = payload.get("code")
    message = payload.get("message") or payload.get("msg") or ""
    if code == 0:
        return
    raise PermanentPushError(f"Server酱返回 code={code} message={message}")


def push_with_retry(send_key: str, title: str, desp: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            push_once(send_key, title, desp)
            print(f"推送成功（第 {attempt} 次尝试）")
            return
        except PermanentPushError as exc:
            print(f"推送失败（不可重试）: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        except TransientPushError as exc:
            last_error = exc
            print(
                f"推送失败（第 {attempt}/{MAX_RETRIES} 次）: {exc}",
                file=sys.stderr,
            )
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_SECONDS ** attempt
                time.sleep(delay)

    print(f"推送重试 {MAX_RETRIES} 次后仍失败: {last_error}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    send_key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if not send_key:
        print("环境变量 SERVERCHAN_KEY 未设置", file=sys.stderr)
        raise SystemExit(1)

    try:
        solar = today_solar()
        title, desp = build_message(solar)
    except Exception as exc:
        print(f"生成黄历内容失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"标题: {title}", flush=True)
    print("--- 正文预览 ---", flush=True)
    print(desp, flush=True)
    print("----------------", flush=True)

    push_with_retry(send_key, title, desp)


if __name__ == "__main__":
    main()
