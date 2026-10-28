// dsh-usage 手表终端（T-Watch S3 / 240×240）
//
// 阅读级：三页轮播，命中率 / 余额 / 最近一次。
// 这块屏只有 240×240，所以每页只讲一件事——信息密度靠翻页，不靠塞。

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <TFT_eSPI.h>
#include <WiFi.h>

#include "secrets.h"

namespace {

constexpr uint32_t kFetchMs = 15000;
constexpr uint32_t kHttpTimeoutMs = 8000;
constexpr int kPageCount = 3;

const uint16_t kBg = 0x0882;       // #0d1117
const uint16_t kPanel = 0x10E4;    // #161b22
const uint16_t kText = 0xEF7E;     // #e6edf3
const uint16_t kMuted = 0x7C32;    // #7d8590
const uint16_t kOk = 0x3DC9;       // #3fb950
const uint16_t kWarn = 0xD4C4;     // #d29922
const uint16_t kBad = 0xF8A9;      // #f85149

TFT_eSPI tft = TFT_eSPI();

struct Usage {
  uint16_t hit_permille = 0;
  uint8_t level = 0;
  bool balance_ok = false;
  uint64_t balance_micros = 0;
  uint64_t cost_micros = 0;
  uint32_t calls = 0;
  uint16_t success_permille = 0;
  uint64_t hit_tokens = 0;
  uint64_t miss_tokens = 0;
  uint32_t last_latency = 0;
  uint32_t last_ttft = 0;
  uint16_t last_hit = 0;
  bool last_ok = true;
  uint32_t stale = 0;
  char model[24] = "-";
  uint16_t spark[32] = {0};
  uint8_t spark_len = 0;
  bool valid = false;
};

Usage usage;
uint32_t last_fetch = 0;
uint32_t last_page_switch = 0;
int page = 0;
bool dirty = true;

uint16_t levelColor() {
  if (usage.level >= 2) return kBad;
  if (usage.level == 1) return kWarn;
  return kOk;
}

void drawHeader() {
  tft.fillRect(0, 0, 240, 30, kBg);
  tft.setTextDatum(ML_DATUM);
  tft.setTextColor(kMuted, kBg);
  tft.drawString("dsh usage", 12, 15, 2);

  tft.setTextDatum(MR_DATUM);
  char stamp[12];
  if (!usage.valid) {
    strlcpy(stamp, "--", sizeof(stamp));
  } else if (usage.stale < 60) {
    snprintf(stamp, sizeof(stamp), "%us", usage.stale);
  } else if (usage.stale < 3600) {
    snprintf(stamp, sizeof(stamp), "%um", usage.stale / 60);
  } else {
    snprintf(stamp, sizeof(stamp), "%uh", usage.stale / 3600);
  }
  tft.drawString(stamp, 214, 15, 2);
  tft.fillCircle(226, 15, 4, levelColor());
}

void drawDots() {
  tft.fillRect(0, 218, 240, 22, kBg);
  const int total = kPageCount * 16;
  const int start = 120 - total / 2 + 8;
  for (int i = 0; i < kPageCount; ++i) {
    const uint16_t color = (i == page) ? kText : 0x2104;
    tft.fillCircle(start + i * 16, 228, 3, color);
  }
}

void drawSparkline(int x, int y, int w, int h) {
  if (usage.spark_len < 2) return;
  const float step = static_cast<float>(w) / (usage.spark_len - 1);
  int prev_x = x;
  int prev_y = y + h - usage.spark[0] * h / 1000;
  for (int i = 1; i < usage.spark_len; ++i) {
    const int cx = x + static_cast<int>(i * step);
    const int cy = y + h - usage.spark[i] * h / 1000;
    tft.drawLine(prev_x, prev_y, cx, cy, levelColor());
    prev_x = cx;
    prev_y = cy;
  }
}

void drawPageOne() {
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(kMuted, kBg);
  tft.drawString("CACHE HIT", 120, 50, 2);

  char buffer[12];
  snprintf(buffer, sizeof(buffer), "%u%%", usage.hit_permille / 10);
  tft.setTextColor(levelColor(), kBg);
  tft.drawString(buffer, 120, 100, 7);

  tft.setTextColor(kMuted, kBg);
  tft.drawString("hit / miss tokens", 120, 142, 2);
  snprintf(buffer, sizeof(buffer), "%lluk / %lluk",
           static_cast<unsigned long long>(usage.hit_tokens / 1000),
           static_cast<unsigned long long>(usage.miss_tokens / 1000));
  tft.setTextColor(kText, kBg);
  tft.drawString(buffer, 120, 160, 2);

  drawSparkline(24, 178, 192, 34);
}

void drawPageTwo() {
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(kMuted, kBg);
  tft.drawString("BALANCE", 120, 50, 2);

  char buffer[16];
  if (usage.balance_ok) {
    snprintf(buffer, sizeof(buffer), "%.2f", usage.balance_micros / 1000000.0);
    tft.setTextColor(usage.balance_micros < 5000000ULL ? kBad : kOk, kBg);
  } else {
    snprintf(buffer, sizeof(buffer), "--");
    tft.setTextColor(kMuted, kBg);
  }
  tft.drawString(buffer, 120, 100, 7);

  tft.setTextColor(kMuted, kBg);
  tft.drawString("TODAY", 120, 146, 2);
  snprintf(buffer, sizeof(buffer), "%.2f CNY", usage.cost_micros / 1000000.0);
  tft.setTextColor(kText, kBg);
  tft.drawString(buffer, 120, 168, 4);

  snprintf(buffer, sizeof(buffer), "%u calls", usage.calls);
  tft.setTextColor(kMuted, kBg);
  tft.drawString(buffer, 120, 196, 2);
}

void drawPageThree() {
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(kMuted, kBg);
  tft.drawString("LAST CALL", 120, 48, 2);

  tft.setTextColor(usage.last_ok ? kOk : kBad, kBg);
  tft.drawString(usage.last_ok ? "OK" : "FAILED", 120, 78, 4);

  tft.setTextColor(kText, kBg);
  tft.drawString(usage.model, 120, 110, 2);

  char buffer[24];
  snprintf(buffer, sizeof(buffer), "latency %ums", usage.last_latency);
  tft.setTextColor(kMuted, kBg);
  tft.drawString(buffer, 120, 138, 2);

  snprintf(buffer, sizeof(buffer), "ttft %ums", usage.last_ttft);
  tft.drawString(buffer, 120, 158, 2);

  snprintf(buffer, sizeof(buffer), "hit %u%%", usage.last_hit / 10);
  tft.drawString(buffer, 120, 178, 2);

  snprintf(buffer, sizeof(buffer), "success %u%%", usage.success_permille / 10);
  tft.drawString(buffer, 120, 198, 2);
}

void draw() {
  tft.fillRect(0, 0, 240, 240, kBg);
  drawHeader();
  switch (page) {
    case 0: drawPageOne(); break;
    case 1: drawPageTwo(); break;
    default: drawPageThree(); break;
  }
  drawDots();
  dirty = false;
}

void drawSplash(const char* message) {
  tft.fillScreen(kBg);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(kText, kBg);
  tft.drawString("dsh usage", 120, 100, 4);
  tft.setTextColor(kMuted, kBg);
  tft.drawString(message, 120, 130, 2);
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
  usage.calls = doc["calls_today"] | 0;
  usage.success_permille = doc["success_permille"] | 0;
  usage.hit_tokens = doc["cache_hit_today"] | 0ULL;
  usage.miss_tokens = doc["cache_miss_today"] | 0ULL;
  usage.last_latency = doc["last_latency_ms"] | 0;
  usage.last_ttft = doc["last_ttft_ms"] | 0;
  usage.last_hit = doc["last_cache_hit_permille"] | 0;
  usage.last_ok = doc["last_ok"] | true;
  usage.stale = doc["stale_seconds"] | 0;
  strlcpy(usage.model, doc["last_model"] | "-", sizeof(usage.model));

  JsonArray spark = doc["spark"].as<JsonArray>();
  usage.spark_len = 0;
  for (JsonVariant value : spark) {
    if (usage.spark_len >= 32) break;
    usage.spark[usage.spark_len++] = static_cast<uint16_t>(value.as<int>());
  }

  usage.valid = true;
  dirty = true;
  return true;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(TFT_BL_PIN, OUTPUT);
  digitalWrite(TFT_BL_PIN, HIGH);

  tft.init();
  tft.setRotation(0);
  tft.fillScreen(kBg);
  drawSplash("connecting...");

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
    dirty = true;
  }

  if (last_page_switch == 0 || now - last_page_switch >= PAGE_INTERVAL_MS) {
    last_page_switch = now;
    page = (page + 1) % kPageCount;
    dirty = true;
  }

  // BOOT 键（GPIO0）手动翻页
  if (digitalRead(0) == LOW) {
    delay(30);
    if (digitalRead(0) == LOW) {
      page = (page + 1) % kPageCount;
      last_page_switch = now;
      dirty = true;
      while (digitalRead(0) == LOW) delay(10);
    }
  }

  if (dirty && usage.valid) {
    draw();
  }

  delay(20);
}
