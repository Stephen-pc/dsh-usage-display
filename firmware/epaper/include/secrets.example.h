#pragma once

// 复制成 secrets.h 再填。

#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-password"

#define USAGE_URL "http://192.168.1.10:8787/api/usage"
#define USAGE_TOKEN ""

// 微雪 4.2 寸墨水屏的接线（ESP32 DevKit）
#define EPD_CS 15
#define EPD_DC 27
#define EPD_RST 26
#define EPD_BUSY 25
// SCK=13, MOSI=14 走默认 SPI

// 至少多久刷一次（秒）。墨水屏刷一次要两秒，别太勤。
#define EPD_MIN_REFRESH_SECONDS 300
