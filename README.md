# dsh-usage-display

把 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）每次调用的 **缓存命中率、token 用量、花费、余额** 折成一份 JSON，然后让任何设备去读它 —— 网页、LED 灯带、点阵屏、墨水屏、手表。

一个 Companion 服务，四个终端固件，共用一套协议。换终端不改服务端。

---

## 为什么不是一个 App

因为「可视化」不等于「把四页数据搬到一块小屏上」。240×240 上塞四个指标，翻页翻到手酸，还不如不做。

这个项目按 **注意力成本** 分层：每个指标只需要一种注意力，就别给它更高的配置。

| 档位 | 终端 | 你付出的注意力 | 得到什么 |
|---|---|---|---|
| 余光级 | WS2812 灯带 | 零 | 命中率变成灯带长度，等级变成颜色，扫一眼就知道今天顺不顺 |
| 一瞥级 | MAX7219 点阵 | 半秒 | 三个数字轮播，不用聚焦就能读 |
| 阅读级 | 墨水屏 / 手表 | 几秒 | 完整面板，能看趋势线 |
| 分析级 | 内置网页 | 专注 | 折线、明细、token 构成 |

同一份数据，四种读法。**手腕只负责告警，桌面才负责看清。**

---

## 架构

```
dsh 会话日志 (JSONL / zstd)
        │  宽容解析，不写死 schema
        ▼
  Companion  ──────────────────────────────┐
  Python · 零依赖                          │
        │                                  │
        │ GET /api/usage                   │ 阈值判断
        ▼                                  ▼
  灯带 · 点阵 · 墨水屏 · 手表 · 网页      Bark / ntfy
                                          → 手机 → 手表通知
```

颜色语义、阈值判断全部在服务端算好，终端只负责上色。所以同一个数不会在灯带上显示绿色、在手表上显示红色。

---

## 快速开始

需要 Python 3.11+，无第三方依赖。

```bash
cd companion
pip install -e .

dsh-usage init              # 写一份示例配置到 ~/.dsh-usage/config.toml
dsh-usage mock --count 40   # 灌 40 条模拟数据，先看效果
dsh-usage serve
```

打开 <http://127.0.0.1:8787/> 就是看板。

接真实数据：

```bash
dsh-usage probe    # 扫一遍 ~/.dsh，看能不能解析出事件
```

`probe` 会打印每个日志文件解析出多少条事件、字段对不对得上。dsh 还在预览期，会话格式会变，所以解析层用的是**字段别名 + 递归探测**，不依赖固定 schema。如果 `probe` 一条都认不出来，把输出开个 issue。

如果 dsh 的会话日志不在 `~/.dsh`，改配置里的 `[source] path`。

---

## 四个终端

每个终端都是独立的 PlatformIO 工程，进目录 `pio run -t upload` 就行。都只依赖同一个 `/api/usage`。

### 灯带 · `firmware/led-strip`

30 颗 WS2812。前 24 颗是命中率进度条，后 6 颗是余额条（每颗 5 元）。

- 等级 0 → 青绿常亮
- 等级 1 → 琥珀慢呼吸
- 等级 2+ → 红色急闪

余光级的关键是**不用读**。灯带短了一截、或者开始闪，你就知道该清上下文了。

物料：ESP32 DevKit + WS2812 灯带，约 30 元。

### 点阵 · `firmware/matrix`

4 个 MAX7219 级联成 32×8，三页轮播：命中率 → 余额 → 今日花费，每 4 秒换一页。

单色屏没法上色，所以等级靠**闪烁频率**表达。物料约 20 元。

### 墨水屏 · `firmware/epaper`

微雪 4.2 寸（400×300），完整面板 + 最近 32 次命中的柱状图。

只在数字明显变化时才重绘（命中率变化 ≥ 2%、余额变了），避免墨水屏每 15 秒闪一次。断电后画面还在，所以刷一次能管很久。物料约 150 元。

### 手表 · `firmware/watch`

T-Watch S3（ESP32-S3 + 1.54" 240×240 ST7789），三页轮播：命中率 / 余额 / 最近一次。

引脚通过 `build_flags` 传给 TFT_eSPI，不用改库文件。换板子只改 `platformio.ini` 里那几行 `-DTFT_*`。

物料约 300 元。

> **关于华为 WATCH FIT 3**：不行。它的应用市场是华为审核过的白名单，个人开发者没有上架通道，自定义表盘也只能读系统数据（时间、步数、心率），不能联网。FIT 3 唯一能做的是接收手机通知——所以正确的用法是让 Companion 在出事时推一条 Bark 通知，手表只当告警器。这也是本项目把阈值推送独立出来的原因。

---

## 配置

`~/.dsh-usage/config.toml`：

```toml
host = "0.0.0.0"      # 要让局域网里的 ESP32 连上，必须监听 0.0.0.0
port = 8787
token = "换成一个随机串"

[source]
kind = "dsh-jsonl"    # dsh-jsonl | jsonl | stdin
path = "~/.dsh"

[balance]
enabled = true
api_key = ""          # 留空则读环境变量 DEEPSEEK_API_KEY
interval_seconds = 300
warn_below_cny = 5.0

[notify]
enabled = false
provider = "bark"     # bark | ntfy
bark_url = "https://api.day.app/你的key"
cooldown_seconds = 600

[thresholds]
cache_warn_permille = 950
cache_bad_permille = 800
error_streak = 3

[pricing.default]
cache_hit_per_mtok = 0.10
cache_miss_per_mtok = 3.00
output_per_mtok = 9.00
```

价格表按你实际用的模型改。DeepSeek 的缓存命中与未命中价差很大（V4 Flash 高峰时段差 30 倍），所以命中率不只是性能指标，也是钱。

### 推送到手表

```bash
[notify]
enabled = true
provider = "bark"
bark_url = "https://api.day.app/你的key"
```

只在三种情况推：命中率跌破 `cache_bad_permille`、余额低于 `warn_below_cny`、连续失败 `error_streak` 次。同一个原因在 `cooldown_seconds` 内不重复推——否则手腕会被震麻。

iPhone 上 Bark 通知会自动镜像到配对的华为手表。

---

## 命令行

```
dsh-usage serve      启动 HTTP 服务（默认）
dsh-usage snapshot   打印一次快照 JSON
dsh-usage probe      扫描日志，看字段能不能对上
dsh-usage record     手动追加一条记录
dsh-usage mock       灌模拟数据
dsh-usage init       写示例配置
```

手动记录一条（可以在 dsh 的 hook 里直接调）：

```bash
dsh-usage record --input 12000 --output 500 --cache-hit 11700 --cache-miss 300 --model deepseek-v4-flash
```

或者走 HTTP，给 dsh 插件用：

```bash
curl -X POST http://127.0.0.1:8787/api/record \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input_tokens":12000,"output_tokens":500,"cache_hit_tokens":11700,"cache_miss_tokens":300}'
```

---

## 协议

完整字段见 [docs/protocol.md](docs/protocol.md)。三个约定值得先说：

**全整数，不用浮点。** 千分比（`*_permille`）和微元（`*_micros`）都是整数，ESP32 上省事。

**缺失即零，不许崩。** 所有键始终存在，终端拿不到值就显示 `--`，不能白屏。

**等级由服务端下发。** `level` 0–3 和 `level_reason` 已经在 Companion 里算好，终端只做颜色映射。阈值只在一个地方改。

---

## 目录

```
companion/          数据服务（Python，零依赖）
  dsh_usage/
    sources.py      宽容解析 dsh 会话日志
    metrics.py      命中率 / 花费 / 等级
    balance.py      DeepSeek 余额
    notify.py       阈值推送
    server.py       HTTP + 内置看板
firmware/
  led-strip/        余光级
  matrix/           一瞥级
  epaper/           阅读级
  watch/            阅读级
docs/
  protocol.md       数据协议
  devices.md        设备选型与物料
  wiring.md         接线
```

---

## 已知限制

- **会话格式会变。** dsh 还在开发者预览期，官方明说会有破坏性变更。解析层用别名探测 + 递归搜索来扛，但不保证永远跟得上。`probe` 是排障的第一站。
- **固件未在全部实机验证。** Companion 和网页有测试；四个固件按对应开发板的常规接法写，但引脚、驱动型号请以你手上板子的官方定义为准。
- **局域网明文 HTTP。** token 只防误访问，不能防同网段的嗅探。别把 `8787` 暴露到公网。

---

## 许可

MIT。协议和架构参考了 [wangjian124/cc-usage-display](https://github.com/wangjian124/cc-usage-display) 的分层思路，那个项目面向 Claude Code + CC Switch，这个面向 DeepSeek Harness，并且把终端做成了可替换的。
