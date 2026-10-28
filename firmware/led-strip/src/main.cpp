// dsh-usage 灯带终端
//
// 余光级：不用看字，扫一眼就知道今天顺不顺。
// 前 24 颗是命中率进度条，后 6 颗是余额条，颜色由服务端下发的 level 决定。
// 颜色语义统一在 Companion 里算，这边不做二次判断。

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include "secrets.h"

namespace {

constexpr uint32_t kRefreshMs = 15000;
constexpr uint32_t kHttpTimeoutMs = 8000;
constexpr int kHitLeds = 24;
constexpr int kBalanceLeds = 6;
constexpr int kBalanceUnitCny = 5;  // 每颗余额灯代表 5 元

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

struct Usage {
  uint16_t hit_permille = 0;
  uint8_t level = 0;
  bool balance_ok = false;
  uint64_t balance_micros = 0;
  bool last_ok = true;
  bool valid = false;
};

Usage usage;
uint32_t last_fetch = 0;
uint32_t last_blink = 0;
bool fetching = false;

uint32_t levelColor(uint8_t level) {
  if (level >= 2) return strip.Color(220, 24, 16);
  if (level == 1) return strip.Color(210, 140, 0);
  return strip.Color(0, 190, 110);
}

// 等级越高，闪得越急；0 级恒定。
float breathFactor(uint8_t level, uint32_t now) {
  if (level == 0) return 1.0f;
  const uint32_t period = level >= 2 ? 700 : 2200;
  const float phase = (now % period) / static_cast<float>(period);
  const float wave = 0.5f - 0.5f * cosf(phase * 2.0f * PI);
  return 0.25f + 0.75f * wave;
}

void render() {
  const uint32_t now = millis();

  if (!usage.valid) {
    // 没数据：整条熄灭，每 3 秒在最前端点一颗蓝灯，表示还活着。
    strip.clear();
    if (now - last_blink > 3000) last_blink = now;
    if (now - last_blink < 120) strip.setPixelColor(0, strip.Color(0, 40, 120));
    strip.show();
    return;
  }

  const float factor = breathFactor(usage.level, now);
  const uint32_t color = levelColor(usage.level);
  const uint32_t dim = strip.Color(8, 8, 8);

  const int lit = constrain(
      static_cast<int>(roundf(usage.hit_permille / 1000.0f * kHitLeds)), 0, kHitLeds);

  for (int i = 0; i < kHitLeds; ++i) {
    if (i < lit) {
      const uint32_t base = color;
      const uint8_t r = ((base >> 16) & 0xFF) * factor;
      const uint8_t g = ((base >> 8) & 0xFF) * factor;
      const uint8_t b = (base & 0xFF) * factor;
      strip.setPixelColor(i, strip.Color(r, g, b));
    } else {
      strip.setPixelColor(i, dim);
    }
  }

  // 余额条：够 6 颗就全亮，低了就只剩一两颗。
  int balance_lit = 0;
  if (usage.balance_ok) {
    const int units = static_cast<int>(usage.balance_micros / (kBalanceUnitCny * 1000000ULL));
    balance_lit = constrain(units, 0, kBalanceLeds);
  }
  const uint32_t balance_color =
      usage.balance_ok && balance_lit > 0 ? strip.Color(0, 90, 200) : strip.Color(120, 0, 0);
  for (int i = 0; i < kBalanceLeds; ++i) {
    const int index = kHitLeds + i;
    strip.setPixelColor(index, i < balance_lit ? balance_color : dim);
  }

  strip.show();
}

bool fetchUsage() {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, USAGE_URL)) return false;
  http.setTimeout(kHttpTimeoutMs);
  if (strlen(USAGE_TOKEN) > 0) {
    http.addHeader("Authorization", String("Bearer ") + USAGE_TOKEN);
  }

  const int status = http.GET();
  if (status != HTTP_CODE_OK) {
    http.end();
    Serial.printf("[net] HTTP %d\n", status);
    return false;
  }

  const String payload = http.getString();
  http.end();

  JsonDocument doc;
  if (deserializeJson(doc, payload) != DeserializationError::Ok) {
    Serial.println("[net] JSON 解析失败");
    return false;
  }

  usage.hit_permille = doc["cache_hit_permille"] | 0;
  usage.level = doc["level"] | 0;
  usage.balance_ok = doc["balance_ok"] | false;
  usage.balance_micros = doc["balance_cny_micros"] | 0ULL;
  usage.last_ok = doc["last_ok"] | true;
  usage.valid = true;

  Serial.printf("[net] 命中 %u‰ level=%u 余额 ¥%.2f\n", usage.hit_permille, usage.level,
                usage.balance_micros / 1000000.0);
  return true;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  strip.clear();
  strip.show();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] 连接 %s\n", WIFI_SSID);
}

void loop() {
  const uint32_t now = millis();

  if (WiFi.status() == WL_CONNECTED) {
    if (last_fetch == 0 || now - last_fetch >= kRefreshMs) {
      last_fetch = now;
      fetchUsage();
    }
  } else if (now - last_fetch >= kRefreshMs) {
    last_fetch = now;
    WiFi.reconnect();
  }

  render();
  delay(20);
}
