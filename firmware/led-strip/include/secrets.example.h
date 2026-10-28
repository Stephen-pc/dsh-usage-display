#pragma once

// 复制成 secrets.h 再填。secrets.h 已被 .gitignore 排除。

#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-password"

// Companion 地址。灯带只跑在局域网里，别暴露到公网。
#define USAGE_URL "http://192.168.1.10:8787/api/usage"
#define USAGE_TOKEN ""  // Companion 配了 token 就填上

// 灯带接在哪个引脚、多少颗
#define LED_PIN 5
#define LED_COUNT 30
#define LED_BRIGHTNESS 120  // 0-255，夜里别太刺眼
