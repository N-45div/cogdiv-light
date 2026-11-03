// Wind Buddy: shows the wind-aware charging plan, asks "hold: yes / tap: no", and switches the
// load when the plan says so.
//
// The device is deliberately simple: the server (backend/wind_monitor/device_api.py) decides
// everything, including the screen text, and the device polls it every few seconds:
//   GET  {server}/api/device/{id}/state     -> what to show, whether the relay is on, and
//                                              firmware to install (only when asked for)
//   POST {server}/api/device/{id}/event     -> button presses (approve/reject/stop), telemetry,
//                                              and the log lines collected since the last event
//   GET  {server}/api/device/{id}/firmware  -> the new app image, for over-the-air updates
//
// Remote care: after one flash from the browser, the board can be updated and debugged over
// the internet. A new firmware runs "on trial": it is kept once it has reached the server, and
// if it can't within 3 minutes (or crashes) the board goes back to the previous one by itself.
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
#include <Update.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WiFiManager.h>
#include <esp_ota_ops.h>
#include <stdarg.h>

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
static const uint32_t TELEMETRY_EVERY_MS = 30000;
static const uint32_t OFFLINE_RELAY_OFF_MS = 10UL * 60UL * 1000UL;
static const uint32_t TRIAL_MS = 3UL * 60UL * 1000UL;            // new firmware must reach the
                                                                  // server within this, or roll back
static const uint32_t UPDATE_RETRY_MS = 10UL * 60UL * 1000UL;    // after a failed download

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
int lastHttp = 0;        // last HTTP status from the server (reported in telemetry)
uint32_t presses = 0;    // button presses since boot (reported in telemetry)
bool relayState = false;

// ---- log -------------------------------------------------------------------------------------
// Lines go to the serial port and ride along with the next event to the server
// (GET /api/device/{id}/logs), so the board can be debugged without a cable.

static const int LOG_KEEP = 30;
String logRing[LOG_KEEP];
int logStart = 0, logCount = 0;

void logLine(const char *fmt, ...) {
  char text[160];
  va_list args;
  va_start(args, fmt);
  vsnprintf(text, sizeof(text), fmt, args);
  va_end(args);
  String line = String("[") + (millis() / 1000) + "s] " + text;
  Serial.println(line);
  if (logCount == LOG_KEEP) {  // full: drop the oldest
    logStart = (logStart + 1) % LOG_KEEP;
    logCount--;
  }
  logRing[(logStart + logCount) % LOG_KEEP] = line;
  logCount++;
}

int attachLogs(JsonDocument &event) {
  JsonArray lines = event["log"].to<JsonArray>();
  for (int i = 0; i < logCount; i++) lines.add(logRing[(logStart + i) % LOG_KEEP]);
  return logCount;
}

void dropLogs(int sent) {  // the server has these now
  sent = min(sent, logCount);
  logStart = (logStart + sent) % LOG_KEEP;
  logCount -= sent;
}

// ---- relay and LED ---------------------------------------------------------------------------

void setRelay(bool on) {
  relayState = on;
  digitalWrite(PIN_RELAY, (on == (RELAY_ACTIVE_HIGH == 1)) ? HIGH : LOW);
#if PIN_RGB_LED >= 0
  neopixelWrite(PIN_RGB_LED, 0, on ? 40 : 0, 0);  // dim green while the load is on
#endif
}

// ---- screen ----------------------------------------------------------------------------------

static const uint16_t COLOR_TEAL = 0x0410, COLOR_DARKGREEN = 0x03E0, COLOR_GREY = 0x7BEF;

bool screenOwnedByTest = false;  // the screen test (SCREEN_TEST builds) is drawing

uint16_t modeColor(const String &mode) {
  if (mode == "proposal") return ST77XX_ORANGE;
  if (mode == "charging") return COLOR_DARKGREEN;
  if (mode == "waiting") return COLOR_TEAL;
  if (mode == "done") return COLOR_GREY;
  if (mode == "offline" || mode == "error") return ST77XX_RED;
  if (mode == "test") return ST77XX_MAGENTA;
  if (mode == "update") return 0x780F;  // purple
  return ST77XX_BLUE;  // idle, setup, boot
}

void drawHeader() {
  if (screenOwnedByTest) return;
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
  if (screenOwnedByTest) return;
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

void beginHttp(HTTPClient &http, WiFiClientSecure &secure, WiFiClient &plain, const String &url) {
  if (url.startsWith("https://")) {
    // Hackathon trade-off: the server certificate is not checked. Pin a root CA before real use.
    secure.setInsecure();
    http.begin(secure, url);
  } else {
    http.begin(plain, url);  // e.g. a laptop on the same network
  }
  if (settings.token.length()) http.addHeader("X-Device-Token", settings.token);
}

bool request(const String &method, const String &path, const String &body, JsonDocument &out) {
  if (settings.server.isEmpty()) return false;
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, settings.server + path);
  http.setTimeout(15000);
  int code;
  if (method == "POST") {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body);
  } else {
    code = http.GET();
  }
  bool ok = code == 200 && !deserializeJson(out, http.getString());
  // Log changes only, so an offline board doesn't fill the log with the same line.
  if (!ok && code != lastHttp) logLine("%s %s -> HTTP %d", method.c_str(), path.c_str(), code);
  lastHttp = code;
  http.end();
  return ok;
}

void apply(JsonDocument &doc) {
  String previousMode = view.mode;
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

  if (mode != previousMode) logLine("screen: %s (v%d)", mode.c_str(), version);
  if (relay != relayState) {
    logLine("relay %s", relay ? "ON" : "off");
    setRelay(relay);
  }
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

void maybeUpdate(JsonDocument &doc);
void maybeSpeak(JsonDocument &doc);
void confirmFirmware();
bool onTrial = false;  // running new over-the-air firmware that hasn't reached the server yet

void poll() {
  JsonDocument doc;
  if (request("GET", devicePath("state"), "", doc)) {
    if (failedPolls >= 3) logLine("server back after %d failed polls", failedPolls);
    lastOkMs = millis();
    failedPolls = 0;
    if (onTrial) confirmFirmware();
    apply(doc);
    maybeSpeak(doc);
    maybeUpdate(doc);
    return;
  }
  failedPolls++;
  if (failedPolls == 3) {
    logLine("offline: 3 polls failed (HTTP %d)", lastHttp);
    showMessage("offline", "No connection",
                {settings.server.isEmpty() ? "No server URL set" : "Server not reachable",
                 "Retrying...", "Hold BOOT 10 s", "to change settings"});
  }
  if (relayState && millis() - lastOkMs > OFFLINE_RELAY_OFF_MS) {
    setRelay(false);  // fail safe: never leave the load on without the server
    logLine("relay off: no server for 10 min");
    drawAll();
  }
}

void sendEvent(const char *type, const String &proposalId = "") {
  JsonDocument event;
  event["type"] = type;
  if (proposalId.length()) event["proposal_id"] = proposalId;
  int sent = attachLogs(event);
  String body;
  serializeJson(event, body);
  JsonDocument doc;
  if (request("POST", devicePath("event"), body, doc)) {
    dropLogs(sent);
    lastOkMs = millis();
    apply(doc);
    maybeSpeak(doc);
  } else {
    logLine("%s not accepted (HTTP %d)", type, lastHttp);
  }
}

void sendTelemetry() {
  JsonDocument event;
  event["type"] = "telemetry";
  event["relay"] = relayState;
  event["rssi"] = WiFi.RSSI();
  event["uptime_s"] = millis() / 1000;
  event["firmware"] = FW_VERSION;
  // Enough to check the board remotely: is it talking, drawing, and switching?
  event["free_heap"] = ESP.getFreeHeap();
  event["last_http"] = lastHttp;
  event["mode"] = view.mode;
  event["version"] = view.version;
  event["presses"] = presses;
  event["relay_pin"] = digitalRead(PIN_RELAY) == (RELAY_ACTIVE_HIGH ? HIGH : LOW);
  event["ip"] = WiFi.localIP().toString();
  event["partition"] = esp_ota_get_running_partition()->label;  // app0 / app1: flips per update
  int sent = attachLogs(event);
  String body;
  serializeJson(event, body);
  JsonDocument doc;
  if (request("POST", devicePath("event"), body, doc)) dropLogs(sent);
}

// ---- over-the-air updates --------------------------------------------------------------------

String triedMd5;            // an update that restarted but never confirmed: don't retry it
String failedMd5;           // a download that failed just now: retry after UPDATE_RETRY_MS
uint32_t retryUpdateAtMs = 0;
bool skipLogged = false;

void drawProgress(size_t done, size_t total) {
  int width = tft.width() - 16;
  int filled = total ? (int)((uint64_t)width * done / total) : 0;
  tft.fillRect(8, 100, filled, 8, ST77XX_GREEN);
}

void installUpdate(const String &version, const String &path, size_t size, const String &md5) {
  logLine("update %s: downloading %u bytes", version.c_str(), (unsigned)size);
  setRelay(false);  // the load stays off while the board restarts; the plan resumes after
  showMessage("update", "Updating", {String("firmware ") + version, "Don't unplug", "~30 s"});
  tft.drawRect(7, 99, tft.width() - 14, 10, ST77XX_WHITE);

  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, settings.server + path);
  http.setTimeout(30000);
  int code = http.GET();
  String error;
  if (code != 200) {
    error = String("HTTP ") + code;
  } else if (http.getSize() != (int)size) {
    error = String("size ") + http.getSize() + ", expected " + size;
  } else if (!Update.begin(size)) {
    error = Update.errorString();
  } else {
    Update.setMD5(md5.c_str());  // Update.end() fails if the image doesn't match
    Update.onProgress(drawProgress);
    size_t written = Update.writeStream(*http.getStreamPtr());
    if (written != size) {
      error = String("got ") + written + " of " + size + " bytes";
      Update.abort();
    } else if (!Update.end()) {
      error = Update.errorString();
    }
  }
  http.end();

  if (error.length()) {
    logLine("update %s failed: %s", version.c_str(), error.c_str());
    failedMd5 = md5;
    retryUpdateAtMs = millis() + UPDATE_RETRY_MS;
    showMessage("error", "Update failed", {error.substring(0, 21), "Still on " FW_VERSION,
                                           "Retry in 10 min"});
    delay(3000);
    nextTelemetryMs = 0;  // tell the server soon
    return;
  }
  prefs.begin("windbuddy", false);
  prefs.putString("otamd5", md5);  // cleared by the new firmware once it reaches the server
  prefs.putString("otaver", version);
  prefs.end();
  logLine("update %s: written and checked, restarting", version.c_str());
  sendTelemetry();  // so the server has the log before the restart
  showMessage("update", "Updated", {String("firmware ") + version, "Restarting..."});
  delay(1000);
  ESP.restart();
}

void maybeUpdate(JsonDocument &doc) {
  JsonVariant offer = doc["update"];
  if (offer.isNull()) return;
  String version = offer["version"] | "";
  String path = offer["path"] | "";
  String md5 = offer["md5"] | "";
  size_t size = offer["size"] | 0;
  if (version.isEmpty() || version == FW_VERSION || path.isEmpty() || md5.length() != 32) return;
  if (md5 == triedMd5) {
    if (!skipLogged) logLine("not retrying %s: it was rolled back", version.c_str());
    skipLogged = true;
    return;
  }
  if (md5 == failedMd5 && (int32_t)(millis() - retryUpdateAtMs) < 0) return;
  installUpdate(version, path, size, md5);
}

// The app keeps a new firmware only after confirmFirmware() (see verifyRollbackLater below).
bool runningOnTrial() {
  esp_ota_img_states_t state;
  return esp_ota_get_state_partition(esp_ota_get_running_partition(), &state) == ESP_OK &&
         state == ESP_OTA_IMG_PENDING_VERIFY;
}

void confirmFirmware() {
  esp_ota_mark_app_valid_cancel_rollback();
  onTrial = false;
  triedMd5 = "";
  prefs.begin("windbuddy", false);
  prefs.remove("otamd5");
  prefs.remove("otaver");
  prefs.end();
  logLine("firmware %s confirmed: it reached the server", FW_VERSION);
}

void giveUpTrial() {
  logLine("firmware %s never reached the server: rolling back", FW_VERSION);
  setRelay(false);
  showMessage("error", "Update failed", {"No server in 3 min", "Going back to the", "old firmware"});
  delay(2000);
  esp_ota_mark_app_invalid_rollback_and_reboot();  // only returns if there's nothing to go back to
  onTrial = false;
  logLine("no previous firmware to go back to: keeping %s", FW_VERSION);
}

// Tells the Arduino core not to accept a new firmware at boot: confirmFirmware() does that.
bool verifyRollbackLater() { return true; }

// ---- button ----------------------------------------------------------------------------------

#include "audio.h"

uint32_t pressedAt = 0;
bool pressed = false, holdFired = false;

void onTap() {
  logLine("tap (%s)", view.mode.c_str());
  presses++;
  nextTelemetryMs = 0;  // report the press soon
  if (view.mode == "proposal") {
    sendEvent("reject", view.proposalId);
  } else {
    nextPollMs = 0;  // refresh now
  }
}

void onHold() {
  logLine("hold (%s)", view.mode.c_str());
  presses++;
  nextTelemetryMs = 0;
  if (view.mode == "proposal") {
    sendEvent("approve", view.proposalId);
  }  // elsewhere a hold is push-to-talk ("stop charging" is said, not pressed)
}

bool canTalk() { return view.mode != "proposal" && view.mode != "test"; }

void readButton() {
  bool down = digitalRead(PIN_BUTTON) == LOW;
  uint32_t now = millis();
  if (down && !pressed) {
    pressed = true;
    holdFired = false;
    talkedThisPress = false;
    pressedAt = now;
  } else if (down && pressed) {
    // In idle, holding the button is push-to-talk; otherwise hold = yes / stop.
    if (!holdFired && canTalk() && now - pressedAt >= TALK_AFTER_MS) {
      holdFired = true;
      startTalking();
    }
    if (!holdFired && now - pressedAt >= HOLD_MS) {
      holdFired = true;
      onHold();
    }
    if (talking && now - talkStartMs >= MAX_TALK_MS) finishTalking();
    if (!talkedThisPress && now - pressedAt >= RESET_HOLD_MS) {
      showMessage("setup", "Reset Wi-Fi", {"Opening setup..."});
      WiFiManager wm;
      wm.resetSettings();
      delay(500);
      connectWifi(true);
      pressed = false;
    }
  } else if (!down && pressed) {
    pressed = false;
    if (talking) {
      releaseTalking();
    } else if (!holdFired && now - pressedAt <= TAP_MAX_MS) {
      onTap();
    }
  }
}

// ---- main ------------------------------------------------------------------------------------

const char *resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power on";
    case ESP_RST_SW: return "restart";
    case ESP_RST_PANIC: return "crash";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_EXT: return "reset pin";
    default: return "other";
  }
}

#ifdef SCREEN_TEST
#include "screen_test.h"
#endif

void setup() {
  Serial.begin(115200);  // USB serial: UART0's pins (43/44) drive the screen
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // never wait for a USB host that isn't reading
#endif
  logLine("Wind Buddy %s, %s, running %s", FW_VERSION, resetReason(),
          esp_ota_get_running_partition()->label);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_RELAY, OUTPUT);
  setRelay(false);
  pinMode(PIN_TFT_BL, OUTPUT);
  digitalWrite(PIN_TFT_BL, HIGH);

  loadSettings();
  onTrial = runningOnTrial();
  prefs.begin("windbuddy", true);
  triedMd5 = prefs.getString("otamd5", "");
  String triedVersion = prefs.getString("otaver", "");
  prefs.end();
  if (onTrial) {
    logLine("new firmware on trial: must reach the server within 3 min");
  } else if (triedMd5.length() && triedVersion == FW_VERSION) {
    confirmFirmware();  // the bootloader doesn't keep updates on trial: this one booted fine
  } else if (triedMd5.length()) {
    logLine("update %s was rolled back: staying on %s", triedVersion.c_str(), FW_VERSION);
  }
  SPI.begin(PIN_TFT_SCLK, -1, PIN_TFT_MOSI, PIN_TFT_CS);
  tft.initR(INITR_BLACKTAB);
  tft.setRotation(settings.rotation);
  showMessage("boot", "Wind Buddy", {String("firmware ") + FW_VERSION, "Connecting Wi-Fi..."});

  setupAudio();
  connectWifi(false);
  logLine("Wi-Fi %s, %d dBm, ip %s", WiFi.SSID().c_str(), WiFi.RSSI(),
          WiFi.localIP().toString().c_str());
  chime();
  showMessage("boot", "Wind Buddy", {"Wi-Fi connected", WiFi.localIP().toString(),
                                     settings.server.isEmpty() ? "No server URL set!"
                                                               : "Asking the server..."});
  lastOkMs = millis();
#ifdef SCREEN_TEST
  startScreenTest();
#endif
}

bool wifiWasDown = false;

void loop() {
  readButton();
  if (talking) {  // nothing else while recording: keep the microphone fed
    readMic();
    return;
  }
#ifdef SCREEN_TEST
  screenOwnedByTest = screenTestTick();
#endif
  uint32_t now = millis();
  if (onTrial && now > TRIAL_MS) giveUpTrial();
  if (WiFi.status() != WL_CONNECTED) {
    if (!wifiWasDown) logLine("Wi-Fi lost, reconnecting");
    wifiWasDown = true;
    WiFi.reconnect();
    delay(500);
    return;
  }
  if (wifiWasDown) logLine("Wi-Fi back, %d dBm", WiFi.RSSI());
  wifiWasDown = false;
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
