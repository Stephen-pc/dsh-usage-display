# 数据协议 v1

Companion 是所有终端的唯一数据源。任何终端（灯带、点阵、墨水屏、手表、网页、手机）都只认这一份 JSON，换终端不改服务端。

## 传输

```
GET http://<companion-host>:8787/api/usage
Authorization: Bearer <token>
```

返回 `application/json`，无嵌套，全部整数。默认 15 秒轮询一次即可。

## 字段

### 元信息

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | int | 协议版本，当前恒为 `1` |
| `generated_at` | int | 生成时刻，Unix 秒 |
| `utc_offset_minutes` | int | 本地时区偏移，分钟 |
| `source` | string | 数据来源标识，如 `dsh-jsonl`、`stdin` |
| `stale_seconds` | int | 距离最近一条真实记录过去了多少秒，越大说明越久没动静 |

### 今日累计

| 字段 | 类型 | 说明 |
|---|---|---|
| `calls_today` | int | 今日模型调用次数 |
| `errors_today` | int | 今日失败次数 |
| `success_permille` | int | 成功率千分比，`0`–`1000` |
| `input_today` | int | 今日输入 token |
| `output_today` | int | 今日输出 token |
| `cache_hit_today` | int | 今日命中缓存的输入 token |
| `cache_miss_today` | int | 今日未命中缓存的输入 token |
| `cache_hit_permille` | int | 缓存命中率千分比，`hit / (hit + miss)` |
| `cost_today_cny_micros` | int | 今日估算花费，人民币微元（`1e-6` 元） |

### 近 7 天

`calls_7d`、`input_7d`、`output_7d`、`cost_7d_cny_micros`，含义同上。

### 最近一次调用

| 字段 | 类型 | 说明 |
|---|---|---|
| `last_at` | int | 该次调用时刻，Unix 秒 |
| `last_model` | string | 模型名，缺失为 `"-"` |
| `last_ok` | bool | 是否成功 |
| `last_latency_ms` | int | 端到端耗时 |
| `last_ttft_ms` | int | 首 token 延迟，拿不到为 `0` |
| `last_cache_hit_permille` | int | 该次的缓存命中率 |
| `last_cost_cny_micros` | int | 该次花费 |

### 账户余额

| 字段 | 类型 | 说明 |
|---|---|---|
| `balance_ok` | bool | 是否成功取到余额 |
| `balance_cny_micros` | int | 可用余额，人民币微元 |
| `balance_at` | int | 取余额的时刻 |

余额是唯一不在本地的东西，需要单独请求 DeepSeek 的 `/user/balance`。取不到时 `balance_ok` 为 `false`，其余字段保持上一次的值或 `0`。

### 趋势

| 字段 | 类型 | 说明 |
|---|---|---|
| `spark` | int[] | 最近若干次调用的命中率千分比，旧→新，最多 32 个，给点阵/灯带/迷你折线用 |

## 设计约定

**全整数，不用浮点。** ESP32 上浮点解析和格式化都麻烦，千分比和微元已经够精度。

**缺失即零，不许崩。** 任何字段缺失时终端应当回退到 `0` / `false` / `"-"`，不能因为少一个键就白屏。服务端也保证所有键始终存在。

**颜色语义统一。** 三个终端都用同一套阈值，避免同一个数在不同设备上显示成不同颜色：

- 命中率 ≥ 950‰ → 正常（青绿）
- 800‰ – 950‰ → 注意（琥珀）
- < 800‰ → 警告（红）
- 余额 < 5 元 → 警告（红）
- `last_ok == false` → 闪一下红再回落

阈值可在 Companion 的 `config.toml` 里改，终端不做二次判断，只按服务端给的 `level` 上色。

### 服务端下发的等级

为免各终端各写一套阈值，服务端直接算好：

| 字段 | 取值 | 说明 |
|---|---|---|
| `level` | `0`–`3` | `0` 正常、`1` 注意、`2` 警告、`3` 严重（余额耗尽或连续失败） |
| `level_reason` | string | 触发原因，如 `cache_low`、`balance_low`、`error_streak` |

终端只需要把 `level` 映射成颜色，逻辑留在服务端一处。
