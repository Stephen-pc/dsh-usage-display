// dsh-usage 点阵终端
//
// 一瞥级：32×8 的 MAX7219，轮播三个数字。
// 命中率、余额、今日花费，每 4 秒换一页。等级靠亮度闪动表达——
// 单色屏没法上色，闪烁频率就是它的「颜色」。

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <MD_Parola.h>
#include <MD_MAX72xx.h>
#include <SPI.h>
#include <WiFi.h>

#include "secrets.h"

namespace {

constexpr uint32_t kRefreshMs = 15000;
constexpr uint32_t kPageMs = 4000;
constexpr uint32_t kHttpTimeoutMs = 8000;

MD_Parola display(MD_MAX72XX::FC16_HW, CS_PIN, MAX_DEVICES);

struct Usage {
  uint16_t hit_permille = 0;
  uint8_t level = 0;
  bool balance_ok = false;
  uint64_t balance_micros = 0;
  uint64_t cost_micros = 0;
  bool last_ok = true;
  bool valid = false;
};

Usage usage;
uint32_t last_fetch = 0;
uint32_t last_page = 0;
uint8_t page = 0;
char shown[24] = "";
bool display_ready = false;

void showPage(uint8_t index) {
  char buffer[16];
  switch (index % 3) {
    case 0:
      if (!usage.last_ok) {
        snprintf(buffer, sizeof(buffer), "ERR");
      } else {
        snprintf(buffer, sizeof(buffer), "%u%%", usage.hit_permille / 10);
      }
      break;
    case 1:
      if (usage.balance_ok) {
        snprintf(buffer, sizeof(buffer), "%.1f", usage.balance_micros / 1000000.0);
      } else {
        snprintf(buffer, sizeof(buffer), "N/A");
      }
      break;
    default:
      snprintf(buffer, sizeof(buffer), "%.2f", usage.cost_micros / 1000000.0);
      break;
  }
  if (strcmp(buffer, shown) == 0) return;
  strlcpy(shown, buffer, sizeof(shown));
  display.displayText(shown, PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
  display.displayReset();
}

void applyIntensity(uint32_t now) {
  if (!usage.valid) {
    display.setIntensity(2);
    return;
  }
  switch (usage.level) {
    case 0:
      display.setIntensity(6);
      break;
    case 1:
      // 慢闪：2 秒一个周期
      display.setIntensity(((now / 500) % 4 < 2) ? 10 : 3);
      break;
    default:
      // 快闪：半秒一个周期
      display.setIntensity(((now / 250) % 2) ? 12 : 0);
      break;
  }
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
  usage.last_ok = doc["last_ok"] | true;
  usage.valid = true;
  shown[0] = '\0';  // 强制下一帧重画
  return true;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  display.begin();
  display.setIntensity(3);
  display.displayClear();
  display.displayText("dsh", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] 连接 %s\n", WIFI_SSID);
}

void loop() {
  const uint32_t now = millis();

  if (WiFi.status() == WL_CONNECTED && (last_fetch == 0 || now - last_fetch >= kRefreshMs)) {
    last_fetch = now;
    fetchUsage();
  } else if (WiFi.status() != WL_CONNECTED && now - last_fetch >= kRefreshMs) {
    last_fetch = now;
    WiFi.reconnect();
  }

  if (usage.valid && (last_page == 0 || now - last_page >= kPageMs)) {
    last_page = now;
    showPage(page++);
  }

  applyIntensity(now);
  display.displayAnimate();
  delay(10);
}
