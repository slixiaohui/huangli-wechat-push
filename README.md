# 黄历每日图片推送

每天北京时间 **07:30** 生成一张黄历速览图，通过**企业微信群机器人**推送到群。当「宜 / 忌」中出现关注事项（默认：`交易`）时，在图中高亮并在底部提示。

> 数据来自 `lunar_python`，与常见黄历 App 的宜忌列表不一定完全一致（例如有的 App 写「余事勿取」，库里可能列出具体事项）。这是数据源差异，不是缺陷。

## 仓库结构

```
huangli-wechat-push/          # 即规范中的 huangli-bot
├── huangli.py
├── requirements.txt
├── tests/test_match.py
├── .github/workflows/daily.yml
└── README.md
```

## 获取企业微信 Webhook

1. 打开企业微信群 → **群设置** → **群机器人** → **添加**
2. 复制 Webhook，形如：
   `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx`
3. 在 GitHub 仓库：**Settings → Secrets and variables → Actions**
   - Secret 名：`WECOM_HOOK`，值为完整 URL
   - （可选）Variables 名：`HIGHLIGHT_KEYS`，如 `交易` 或 `开市,立券`

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `WECOM_HOOK` | 是（非 DRY_RUN） | — | 企业微信机器人 Webhook |
| `HIGHLIGHT_KEYS` | 否 | `交易` | 高亮关键词，逗号分隔 |
| `ONLY_PUSH_WHEN_HIT` | 否 | `0` | `1` = 仅命中时推送 |
| `TARGET_DATE` | 否 | 当天（北京时间） | `YYYY-MM-DD`，便于测试 |
| `DRY_RUN` | 否 | `0` | `1` = 只生成 `card.png`，不推送 |
| `FONT_REGULAR` | 否 | Noto Sans CJK Regular | 字体路径 |
| `FONT_BOLD` | 否 | Noto Sans CJK Bold | 字体路径 |

## 本地测试

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# macOS 可指定系统字体
export FONT_REGULAR=/System/Library/Fonts/PingFang.ttc
export FONT_BOLD=/System/Library/Fonts/PingFang.ttc

# 验收：立冬
TARGET_DATE=2026-11-07 DRY_RUN=1 python huangli.py
# → 生成 card.png，应含节气「立冬」、日期 7、农历九月廿九

# 含「交易」的日期（宜高亮）
TARGET_DATE=2026-01-03 DRY_RUN=1 python huangli.py

# 不含「交易」
TARGET_DATE=2026-01-01 DRY_RUN=1 python huangli.py

# 多关键词
TARGET_DATE=2019-05-05 HIGHLIGHT_KEYS=开市,立券 DRY_RUN=1 python huangli.py

# 单元测试
pytest -q
```

## GitHub Actions

- 定时：`cron: 30 23 * * *`（UTC）= 北京时间 07:30
- 手动：Actions → Daily Huangli Push → Run workflow（会上传 `card.png` artifact）

## 常见问题

**Q: 日志里能看到 Webhook 吗？**  
不会打印完整地址，最多显示 `key=***`。

**Q: 推送失败会怎样？**  
HTTP 非 200 或 `errcode != 0` 时重试 2 次（间隔 5 秒），仍失败则以退出码 1 结束。

**Q: 免费微信服务号卡片只显示标题？**  
本项目直连**企业微信群机器人**发图片，不经过 Server酱服务号通道。

**Q: Ubuntu / Actions 缺字体？**  
workflow 已安装 `fonts-noto-cjk`；本地 Linux 可 `sudo apt-get install fonts-noto-cjk`。

## 验收记录（本地 DRY_RUN）

在 macOS 上设置 `FONT_REGULAR` / `FONT_BOLD` 为 PingFang 后验证：

| # | 命令要点 | 结果 |
|---|---|---|
| 1 | `TARGET_DATE=2026-11-07 DRY_RUN=1` | 节气「立冬」，日期 7，农历九月廿九，星期六 |
| 2 | `TARGET_DATE=2026-01-03` | 「交易」金色实心；底栏「宜：交易」 |
| 3 | `TARGET_DATE=2026-01-01` | 全部描边；底栏「宜忌中没有你关注的事项」 |
| 4 | `TARGET_DATE=2019-05-05 HIGHLIGHT_KEYS=开市,立券` | 开市、立券均高亮 |
| 5 | `TARGET_DATE=2026-01-03`（宜 23 项） | 画布增高，无裁切 |
| 6 | 空列表占位 | `display_items` 单测覆盖「诸事不宜」「无」 |
| 7 | `TZ=UTC` 下 `beijing_today()` | 仍为 Asia/Shanghai 当天 |
| 8 | 错误 `WECOM_HOOK` | 重试 2 次后 exit 1，日志仅 `key=***` |
| 9 | `ONLY_PUSH_WHEN_HIT=1` 且未命中 | exit 0，不推送 |
| 10 | `pytest -q` | `tests/test_match.py` 全部通过 |

