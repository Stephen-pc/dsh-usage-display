# 接线与联调

四个终端都只做一件事：`GET /api/usage`，解析 JSON，上色。所以先把 Companion 跑通、用浏览器确认数据对，再接硬件——否则你分不清是固件问题还是数据问题。

---

## 先让设备能连上

Companion 默认监听 `127.0.0.1`，只有本机能访问。ESP32 要连进来，必须改成 `0.0.0.0`：

```toml
host = "0.0.0.0"
port = 8787
token = "随便一串随机字符"
```

查本机局域网 IP：

```bash
# Windows
ipconfig | findstr IPv4
# macOS / Linux
ip addr | grep "inet "
```

假设是 `192.168.1.10`，先在本机确认接口活着：

```bash
curl http://192.168.1.10:8787/api/health
```

不通的话大概率是 Windows 防火墙拦了 8787 端口。允许一下入站，别直接把整个 Python 放行。

然后在手机浏览器打开 `http://192.168.1.10:8787/`，能看到看板就说明网络这条路是通的。

最后把地址填进 `firmware/<终端>/include/secrets.h`：

```c
#define USAGE_URL "http://192.168.1.10:8787/api/usage"
#define USAGE_TOKEN "和 config.toml 里一致的 token"
```

> 注意 ESP32 只支持 2.4G Wi-Fi，5G 频段连不上。另外记得把 `secrets.example.h` 复制成 `secrets.h`——后者已在 `.gitignore` 里，不会被提交。

---

## 灯带 · WS2812

```
ESP32            灯带
GPIO5   ────────  DIN
GND     ────────  GND
5V 外部电源 ────  5V
```

- `DIN` 是数据输入，接灯带的**输入端**，不是输出端。灯带上箭头方向指向远离你的那一头。
- **别用开发板的 5V 引脚供电**。30 颗全白峰值约 1.8A，开发板的 USB 供不起，会重启或者烧稳压。
- 外部电源的 GND 必须和 ESP32 的 GND 连在一起，否则数据信号没有参考电平，灯会乱闪。
- 数据线长于半米时，在 DIN 和 5V 之间加一个 330Ω 电阻，或者在 DIN 和 GND 之间加 470Ω 下拉，能压掉大部分毛刺。

改引脚和灯珠数在 `secrets.h` 里：`LED_PIN`、`LED_COUNT`。

---

## 点阵 · MAX7219

```
ESP32            第一个模块
GPIO15  ────────  CS
GPIO23  ────────  DIN
GPIO18  ────────  CLK
3V3     ────────  VCC
GND     ────────  GND
```

模块之间：前一个的 `DOUT` → 后一个的 `DIN`，`CS`/`CLK`/`VCC`/`GND` 全部并联。

- 最常见的故障是**只有第一个模块亮**：DOUT/DIN 接反了，或者方向搞错（信号从 CS 那一端进，从另一端出）。
- 显示方向颠倒的话，改 `MD_MAX72XX::FC16_HW` 为 `MD_MAX72XX::PAROLA_HW` 或 `GENERIC_HW`，不同批次的模块接线顺序不一样。
- 4 个模块全亮时电流约 300mA，3V3 引脚够用，但如果你级联 8 个以上，建议外部 5V。

---

## 墨水屏 · 微雪 4.2 寸

```
ESP32            墨水屏
GPIO15  ────────  CS
GPIO27  ────────  DC
GPIO26  ────────  RST
GPIO25  ────────  BUSY
GPIO13  ────────  SCK
GPIO14  ────────  MOSI
3V3     ────────  VCC
GND     ────────  GND
```

- **BUSY 必须接**，不接的话程序会一直卡在等待刷新完成。
- 墨水屏**不能热插拔**，上电状态下拔线很容易烧驱动芯片。
- 全刷时会黑白翻转闪一下，这是正常的，不是坏了。
- 如果画面全是噪点或者花屏，先确认型号：`GxEPD2_420` 对应微雪 4.2 寸黑白屏。三色屏要换成 `GxEPD2_3C<GxEPD2_420c, ...>`，刷新会慢一倍多。

---

## 手表 · T-Watch S3

不用接线，USB-C 直接烧。但有两件事要注意：

**背光**：T-Watch S3 的屏幕背光受 AXP2101 电源管理芯片控制，如果烧完屏幕全黑但串口有日志，多半是背光没打开。代码里用 `digitalWrite(TFT_BL_PIN, HIGH)` 直接拉高，部分批次的板子需要先通过 AXP2101 打开 ALDO 通道才能亮。遇到这种情况，参考 LilyGO 官方仓库的 `TWatch2020` 示例，把 PMU 初始化那几行搬过来。

**引脚**：`platformio.ini` 里的 `-DTFT_*` 是按 T-Watch S3 写的。如果你的板子屏幕不亮或者花屏，以 LilyGO 官方仓库里对应型号的 board 定义为准，改那几行就行——不用动 TFT_eSPI 库里的 `User_Setup.h`。

---

## 排障顺序

固件不工作时，按这个顺序查，能省很多时间：

1. 电脑浏览器能不能打开 `http://<IP>:8787/`？不能 → 网络/防火墙/`host` 配置
2. `curl http://<IP>:8787/api/usage` 返回的 JSON 对不对？不对 → Companion 的数据源问题，跑 `dsh-usage probe`
3. 串口有没有打印 `[net] HTTP 200`？没有 → 设备连不上 Wi-Fi，或者 token 不匹配（会返回 401）
4. 有 200 但屏幕不显示 → 硬件接线或驱动型号问题，跟数据无关
