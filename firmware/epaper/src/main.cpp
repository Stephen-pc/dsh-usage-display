// dsh-usage 墨水屏终端
//
// 零功耗常亮的桌面看板。墨水屏断电后画面还在，所以刷一次能管很久。
// 只在数字明显变化时才重绘，避免每 15 秒闪一次。

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Fonts/FreeSans9pt7b.h>
#include <Fonts/FreeSansBold12pt7b.h>
#include <Fonts/FreeSansBold24pt7b.h>
#include <GxEPD2_BW.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include "secrets.h"

namespace {

GxEPD2_BW<GxEPD2_420, GxEPD2_420::HEIGHT> display(
    GxEPD2_420(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

constexpr uint32_t kFetchMs = 60000;
constexpr uint32_t kHttpTimeoutMs = 8000;
constexpr uint32_t kMinRefreshMs = EPD_MIN_REFRESH_SECONDS * 1000UL;

struct Usage {
  uint16_t hit_permille = 0;
  uint8_t level = 0;
  uint8_t reason_code = 0;
  bool balance_ok = false;
  uint64_t balance_micros = 0;
  uint64_t cost_micros = 0;
  uint64_t cost_7d_micros = 0;
  uint32_t calls = 0;
  uint32_t errors = 0;
  uint16_t success_permille = 0;
  uint64_t hit_tokens = 0;
  uint64_t miss_tokens = 0;
  bool last_ok = true;
  uint16_t spark[32] = {0};
  uint8_t spark_len = 0;
  bool valid = false;
};

Usage usage;
uint32_t last_fetch = 0;
uint32_t last_draw = 0;
uint16_t drawn_hit = 0xFFFF;
uint64_t drawn_balance = 0;

const char* levelLabel(uint8_t level) {
  switch (level) {
    case 0: return "OK";
    case 1: return "WATCH";
    case 2: return "LOW";
    default: return "ALERT";
  }
}

void drawSparkline(int x, int y, int width, int height) {
  if (usage.spark_len < 2) return;
  const int bar_width = max(2, width / usage.spark_len - 1);
  for (int i = 0; i < usage.spark_len; ++i) {
    const int value = constrain(usage.spark[i], 0, 1000);
    const int bar_height = max(1, value * height / 1000);
    const int bx = x + i * (width / usage.spark_len);
    display.fillRect(bx, y + height - bar_height, bar_width, bar_height, GxEPD_BLACK);
  }
}

void draw() {
  display.setFullWindow();
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);

    display.setFont(&FreeSans9pt7b);
    display.setTextColor(GxEPD_BLACK);
    display.setCursor(16, 26);
    display.print("dsh usage");

    display.setCursor(300, 26);
    display.print(levelLabel(usage.level));

    display.drawLine(16, 36, 384, 36, GxEPD_BLACK);

    display.setFont(&FreeSans9pt7b);
    display.setCursor(16, 62);
    display.print("cache hit rate");

    display.setFont(&FreeSansBold24pt7b);
    display.setCursor(16, 108);
    display.printf("%u.%u%%", usage.hit_permille / 10, usage.hit_permille % 10);

    display.setFont(&FreeSans9pt7b);
    display.setCursor(16, 136);
    display.printf("hit %lluk / miss %lluk",
                   static_cast<unsigned long long>(usage.hit_tokens / 1000),
                   static_cast<unsigned long long>(usage.miss_tokens / 1000));

    display.drawLine(16, 150, 384, 150, GxEPD_BLACK);

    display.setFont(&FreeSansBold12pt7b);
    display.setCursor(16, 180);
    if (usage.balance_ok) {
      display.printf("balance  %.2f", usage.balance_micros / 1000000.0);
    } else {
      display.print("balance  --");
    }

    display.setFont(&FreeSans9pt7b);
    display.setCursor(16, 206);
    display.printf("today  %.2f CNY   %u calls   %.1f%% ok",
                   usage.cost_micros / 1000000.0, usage.calls,
                   usage.success_permille / 10.0);

    display.setCursor(16, 228);
    display.printf("7d     %.2f CNY", usage.cost_7d_micros / 1000000.0);

    display.setCursor(16, 258);
    display.print("last 32 calls");

    drawSparkline(16, 268, 368, 24);
  } while (display.nextPage());

  last_draw = millis();
  drawn_hit = usage.hit_permille;
  drawn_balance = usage.balance_micros;
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
  if (deserializeJson(doc, payload) != DeserializationError::Ok) return false;

  usage.hit_permille = doc["cache_hit_permille"] | 0;
  usage.level = doc["level"] | 0;
  usage.balance_ok = doc["balance_ok"] | false;
  usage.balance_micros = doc["balance_cny_micros"] | 0ULL;
  usage.cost_micros = doc["cost_today_cny_micros"] | 0ULL;
  usage.cost_7d_micros = doc["cost_7d_cny_micros"] | 0ULL;
  usage.calls = doc["calls_today"] | 0;
  usage.errors = doc["errors_today"] | 0;
  usage.success_permille = doc["success_permille"] | 0;
  usage.hit_tokens = doc["cache_hit_today"] | 0ULL;
  usage.miss_tokens = doc["cache_miss_today"] | 0ULL;
  usage.last_ok = doc["last_ok"] | true;

  JsonArray spark = doc["spark"].as<JsonArray>();
  usage.spark_len = 0;
  for (JsonVariant value : spark) {
    if (usage.spark_len >= 32) break;
    usage.spark[usage.spark_len++] = static_cast<uint16_t>(value.as<int>());
  }

  usage.valid = true;
  return true;
}

bool worthRedrawing() {
  if (!usage.valid) return false;
  if (millis() - last_draw >= kMinRefreshMs) return true;
  if (drawn_hit == 0xFFFF) return true;
  const int hit_delta = abs(static_cast<int>(usage.hit_permille) - static_cast<int>(drawn_hit));
  if (hit_delta >= 20) return true;
  return usage.balance_micros != drawn_balance;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  display.init(115200);
  display.setRotation(1);

  display.setFullWindow();
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    display.setFont(&FreeSans9pt7b);
    display.setTextColor(GxEPD_BLACK);
    display.setCursor(16, 40);
    display.print("dsh usage");
    display.setCursor(16, 70);
    display.print("connecting...");
  } while (display.nextPage());

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] 连接 %s\n", WIFI_SSID);
}

void loop() {
  const uint32_t now = millis();

  if (WiFi.status() == WL_CONNECTED && (last_fetch == 0 || now - last_fetch >= kFetchMs)) {
    last_fetch = now;
    fetchUsage();
  } else if (WiFi.status() != WL_CONNECTED && now - last_fetch >= kFetchMs) {
    last_fetch = now;
    WiFi.reconnect();
  }

  if (worthRedrawing()) {
    draw();
  }

  delay(1000);
}
