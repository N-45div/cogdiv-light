// Wind Buddy: shows the wind-aware charging plan, asks "hold: yes / tap: no", and switches the
// load when the plan says so.
//
// The device is deliberately simple: the server (backend/wind_monitor/device_api.py) decides
// everything, including the screen text, and the device polls it every few seconds:
//   GET  {server}/api/device/{id}/state   -> what to show, and whether the relay is on
//   POST {server}/api/device/{id}/event   -> button presses (approve/reject/stop), telemetry
//
// First boot (or BOOT held for 10 s): the board opens a Wi-Fi hotspot "WindBuddy-Setup"
// (password "windsetup"). Join it from a phone, pick your Wi-Fi, and enter the server URL,
// device id and device token. Nothing secret is compiled into the firmware.
//
// Safety: if the server can't be reached for 10 minutes, the relay switches off.

#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WiFiManager.h>

#include "board.h"

#ifndef FW_VERSION
#define FW_VERSION "dev"
#endif

// ---- settings --------------------------------------------------------------------------------

static const char *SETUP_AP = "WindBuddy-Setup";
static const char *SETUP_PASSWORD = "windsetup";
static const uint32_t TAP_MAX_MS = 700;           // shorter press = tap
static const uint32_t HOLD_MS = 1000;             // longer press = hold (fires while held)
static const uint32_t RESET_HOLD_MS = 10000;      // hold this long: forget Wi-Fi, open setup
static const uint32_t TELEMETRY_EVERY_MS = 60000;
static const uint32_t OFFLINE_RELAY_OFF_MS = 10UL * 60UL * 1000UL;

struct Settings {
  String server;  // e.g. https://wind-buddy.example.com (no trailing slash)
  String device = "desk-1";
  String token;
  int rotation = 0;  // 0-3; 0 matches OceanLabz's mounting (mirror X+Y, RGB)
} settings;

Preferences prefs;
Adafruit_ST7735 tft(&SPI, PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST);

// ---- what the server last said ---------------------------------------------------------------

struct View {
  int version = -1;
  bool relay = false;
  String mode = "boot";
  String title;
  String lines[6];
  int lineCount = 0;
  String prompt;
  String proposalId;
  String nowUk;
  uint32_t pollMs = 5000;
} view;

uint32_t lastOkMs = 0, nextPollMs = 0, nextTelemetryMs = 0;
int failedPolls = 0;
bool relayState = false;

// ---- relay and LED ---------------------------------------------------------------------------

void setRelay(bool on) {
  relayState = on;
  digitalWrite(PIN_RELAY, (on == (RELAY_ACTIVE_HIGH == 1)) ? HIGH : LOW);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  rgbLedWrite(PIN_RGB_LED, 0, on ? 40 : 0, 0);  // dim green while the load is on
#else
  neopixelWrite(PIN_RGB_LED, 0, on ? 40 : 0, 0);
#endif
}

// ---- screen ----------------------------------------------------------------------------------

static const uint16_t COLOR_TEAL = 0x0410, COLOR_DARKGREEN = 0x03E0, COLOR_GREY = 0x7BEF;

uint16_t modeColor(const String &mode) {
  if (mode == "proposal") return ST77XX_ORANGE;
  if (mode == "charging") return COLOR_DARKGREEN;
  if (mode == "waiting") return COLOR_TEAL;
  if (mode == "done") return COLOR_GREY;
  if (mode == "offline" || mode == "error") return ST77XX_RED;
  return ST77XX_BLUE;  // idle, setup, boot
}

void drawHeader() {
  tft.fillRect(0, 0, tft.width(), 18, modeColor(view.mode));
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(4, 5);
  tft.print(view.title.substring(0, 15));
  if (view.nowUk.length()) {
    tft.setCursor(tft.width() - 6 * view.nowUk.length() - 3, 5);
    tft.print(view.nowUk);
  }
}

void drawAll() {
  tft.fillScreen(ST77XX_BLACK);
  drawHeader();
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);
  for (int i = 0; i < view.lineCount; i++) {
    tft.setCursor(2, 26 + i * 14);
    tft.print(view.lines[i]);
  }
  // The load indicator: what the relay is doing right now.
  int y = tft.height() - 46;
  uint16_t box = relayState ? COLOR_DARKGREEN : 0x2104;
  tft.fillRoundRect(8, y, tft.width() - 16, 22, 5, box);
  tft.setTextSize(2);
  tft.setTextColor(ST77XX_WHITE);
  const char *label = relayState ? "LOAD ON" : "load off";
  tft.setCursor((tft.width() - 12 * strlen(label)) / 2, y + 4);
  tft.print(label);
  if (view.prompt.length()) {
    tft.setTextSize(1);
    tft.setTextColor(ST77XX_YELLOW);
    tft.setCursor((tft.width() - 6 * view.prompt.length()) / 2, tft.height() - 14);
    tft.print(view.prompt);
  }
}

void showMessage(const String &mode, const String &title, std::initializer_list<String> lines) {
  view.mode = mode;
  view.title = title;
  view.lineCount = 0;
  for (const String &line : lines) {
    if (view.lineCount < 6) view.lines[view.lineCount++] = line;
  }
  view.prompt = "";
  view.version = -1;  // the next good poll redraws
  drawAll();
}

// ---- settings storage ------------------------------------------------------------------------

void loadSettings() {
  prefs.begin("windbuddy", true);
  settings.server = prefs.getString("server", "");
  settings.device = prefs.getString("device", "desk-1");
  settings.token = prefs.getString("token", "");
  settings.rotation = prefs.getInt("rotation", 0);
  prefs.end();
}

void saveSettings() {
  prefs.begin("windbuddy", false);
  prefs.putString("server", settings.server);
  prefs.putString("device", settings.device);
  prefs.putString("token", settings.token);
  prefs.putInt("rotation", settings.rotation);
  prefs.end();
}

bool shouldSave = false;

void connectWifi(bool forcePortal) {
  WiFiManager wm;
  char rotation[2] = {char('0' + settings.rotation), 0};
  WiFiManagerParameter pServer("server", "Server URL (https://...)", settings.server.c_str(), 120);
  WiFiManagerParameter pDevice("device", "Device id (a-z, 0-9, -)", settings.device.c_str(), 32);
  WiFiManagerParameter pToken("token", "Device token (if the server uses one)",
                              settings.token.c_str(), 64);
  WiFiManagerParameter pRotation("rotation", "Screen rotation 0-3 (0 usually)", rotation, 1);
  wm.addParameter(&pServer);
  wm.addParameter(&pDevice);
  wm.addParameter(&pToken);
  wm.addParameter(&pRotation);
  wm.setSaveConfigCallback([]() { shouldSave = true; });
  wm.setConfigPortalTimeout(300);

  showMessage("setup", "Wi-Fi setup", {"Join Wi-Fi on phone:", SETUP_AP,
                                       String("password ") + SETUP_PASSWORD,
                                       "then open 192.168.4.1", "(if no page pops up)"});
  bool ok = forcePortal ? wm.startConfigPortal(SETUP_AP, SETUP_PASSWORD)
                        : wm.autoConnect(SETUP_AP, SETUP_PASSWORD);
  if (shouldSave) {
    settings.server = String(pServer.getValue());
    settings.server.trim();
    while (settings.server.endsWith("/")) settings.server.remove(settings.server.length() - 1);
    settings.device = String(pDevice.getValue());
    settings.token = String(pToken.getValue());
    settings.rotation = constrain(atoi(pRotation.getValue()), 0, 3);
    saveSettings();
    tft.setRotation(settings.rotation);
  }
  if (!ok) {
    showMessage("error", "Setup timed out", {"Restarting..."});
    delay(2000);
    ESP.restart();
  }
}

// ---- talking to the server -------------------------------------------------------------------

bool request(const String &method, const String &path, const String &body, JsonDocument &out) {
  if (settings.server.isEmpty()) return false;
  String url = settings.server + path;
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  if (url.startsWith("https://")) {
    // Hackathon trade-off: the server certificate is not checked. Pin a root CA before real use.
    secure.setInsecure();
    http.begin(secure, url);
  } else {
    http.begin(plain, url);  // e.g. a laptop on the same network
  }
  http.setTimeout(15000);
  if (settings.token.length()) http.addHeader("X-Device-Token", settings.token);
  int code;
  if (method == "POST") {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body);
  } else {
    code = http.GET();
  }
  bool ok = code == 200 && !deserializeJson(out, http.getString());
  if (!ok) Serial.printf("%s %s -> HTTP %d\n", method.c_str(), path.c_str(), code);
  http.end();
  return ok;
}

void apply(JsonDocument &doc) {
  bool relay = doc["relay"] | false;
  int version = doc["version"] | 0;
  String mode = doc["mode"] | "idle";
  String nowUk = doc["now_uk"] | "";
  String lines[6];
  int count = 0;
  for (JsonVariant line : doc["lines"].as<JsonArray>()) {
    if (count < 6) lines[count++] = line.as<String>();
  }
  bool changed = version != view.version || mode != view.mode || relay != relayState ||
                 count != view.lineCount;
  for (int i = 0; !changed && i < count; i++) changed = lines[i] != view.lines[i];

  view.version = version;
  view.mode = mode;
  view.title = doc["title"] | "";
  view.prompt = doc["prompt"] | "";
  view.proposalId = doc["proposal_id"] | "";
  view.pollMs = 1000UL * (uint32_t)(doc["poll_seconds"] | 5);
  view.lineCount = count;
  for (int i = 0; i < count; i++) view.lines[i] = lines[i];

  if (relay != relayState) setRelay(relay);
  if (changed) {
    drawAll();
  } else if (nowUk != view.nowUk) {
    view.nowUk = nowUk;
    drawHeader();
  }
  view.nowUk = nowUk;
}

String devicePath(const char *what) {
  return String("/api/device/") + settings.device + "/" + what;
}

void poll() {
  JsonDocument doc;
  if (request("GET", devicePath("state"), "", doc)) {
    lastOkMs = millis();
    failedPolls = 0;
    apply(doc);
    return;
  }
  failedPolls++;
  if (failedPolls == 3) {
    showMessage("offline", "No connection",
                {settings.server.isEmpty() ? "No server URL set" : "Server not reachable",
                 "Retrying...", "Hold BOOT 10 s", "to change settings"});
  }
  if (relayState && millis() - lastOkMs > OFFLINE_RELAY_OFF_MS) {
    setRelay(false);  // fail safe: never leave the load on without the server
    drawAll();
  }
}

void sendEvent(const char *type, const String &proposalId = "") {
  JsonDocument event;
  event["type"] = type;
  if (proposalId.length()) event["proposal_id"] = proposalId;
  String body;
  serializeJson(event, body);
  JsonDocument doc;
  if (request("POST", devicePath("event"), body, doc)) {
    lastOkMs = millis();
    apply(doc);
  }
}

void sendTelemetry() {
  JsonDocument event;
  event["type"] = "telemetry";
  event["relay"] = relayState;
  event["rssi"] = WiFi.RSSI();
  event["uptime_s"] = millis() / 1000;
  event["firmware"] = FW_VERSION;
  String body;
  serializeJson(event, body);
  JsonDocument doc;
  request("POST", devicePath("event"), body, doc);
}

// ---- button ----------------------------------------------------------------------------------

uint32_t pressedAt = 0;
bool pressed = false, holdFired = false;

void onTap() {
  Serial.println("tap");
  if (view.mode == "proposal") {
    sendEvent("reject", view.proposalId);
  } else {
    nextPollMs = 0;  // refresh now
  }
}

void onHold() {
  Serial.println("hold");
  if (view.mode == "proposal") {
    sendEvent("approve", view.proposalId);
  } else if (view.mode == "waiting" || view.mode == "charging") {
    sendEvent("stop");
  }
}

void readButton() {
  bool down = digitalRead(PIN_BUTTON) == LOW;
  uint32_t now = millis();
  if (down && !pressed) {
    pressed = true;
    holdFired = false;
    pressedAt = now;
  } else if (down && pressed) {
    if (!holdFired && now - pressedAt >= HOLD_MS) {
      holdFired = true;
      onHold();
    }
    if (now - pressedAt >= RESET_HOLD_MS) {
      showMessage("setup", "Reset Wi-Fi", {"Opening setup..."});
      WiFiManager wm;
      wm.resetSettings();
      delay(500);
      connectWifi(true);
      pressed = false;
    }
  } else if (!down && pressed) {
    pressed = false;
    if (!holdFired && now - pressedAt <= TAP_MAX_MS) onTap();
  }
}

// ---- main ------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  Serial.printf("\nWind Buddy %s\n", FW_VERSION);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_RELAY, OUTPUT);
  setRelay(false);
  pinMode(PIN_TFT_BL, OUTPUT);
  digitalWrite(PIN_TFT_BL, HIGH);

  loadSettings();
  SPI.begin(PIN_TFT_SCLK, -1, PIN_TFT_MOSI, PIN_TFT_CS);
  tft.initR(INITR_BLACKTAB);
  tft.setRotation(settings.rotation);
  showMessage("boot", "Wind Buddy", {String("firmware ") + FW_VERSION, "Connecting Wi-Fi..."});

  connectWifi(false);
  showMessage("boot", "Wind Buddy", {"Wi-Fi connected", WiFi.localIP().toString(),
                                     settings.server.isEmpty() ? "No server URL set!"
                                                               : "Asking the server..."});
  lastOkMs = millis();
}

void loop() {
  readButton();
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    delay(500);
    return;
  }
  if ((int32_t)(now - nextPollMs) >= 0) {
    poll();
    nextPollMs = millis() + view.pollMs;
  }
  if ((int32_t)(now - nextTelemetryMs) >= 0) {
    sendTelemetry();
    nextTelemetryMs = millis() + TELEMETRY_EVERY_MS;
  }
  delay(20);
}
