// Screen bring-up test (built only with -DSCREEN_TEST).
//
// Pins are known (read over JTAG from the shipped firmware): clock 43, data 44, CS 45,
// backlight 42; D/C and reset are 38 and 1. Commands reach the panel (it wakes and goes black)
// but pixel data didn't land at full SPI speed, so these steps drive it slowly (bit-banged
// "software SPI", a few MHz) or at 2 MHz. Each fills the screen with its own colour and a big
// number and logs "screen test N: ..." to the server.
#pragma once

#include <Adafruit_ST7789.h>

static const uint32_t SCREEN_TEST_MS = 6UL * 60UL * 1000UL;
static const uint32_t SCREEN_STEP_MS = 9000;

// Software SPI: (cs, dc, mosi, sclk, rst). Constructors only store pins.
Adafruit_ST7735 swA(PIN_TFT_CS, 38, PIN_TFT_MOSI, PIN_TFT_SCLK, 1);
Adafruit_ST7735 swB(PIN_TFT_CS, 1, PIN_TFT_MOSI, PIN_TFT_SCLK, 38);
Adafruit_ST7789 sw89(PIN_TFT_CS, 38, PIN_TFT_MOSI, PIN_TFT_SCLK, 1);

uint32_t screenTestEndMs = 0, nextStepMs = 0;
int screenStep = 0;
bool hwSpiOn = true;

void useHwSpi(bool on) {
  if (on && !hwSpiOn) SPI.begin(PIN_TFT_SCLK, -1, PIN_TFT_MOSI, PIN_TFT_CS);
  if (!on && hwSpiOn) SPI.end();
  hwSpiOn = on;
}

void showStep(Adafruit_ST77xx &d, int n, uint16_t color) {
  d.setRotation(0);
  d.fillScreen(color);
  d.setTextColor(color == ST77XX_YELLOW || color == ST77XX_CYAN ? ST77XX_BLACK : ST77XX_WHITE);
  d.setTextSize(9);
  d.setCursor(40, 45);
  d.print(n);
  d.setTextSize(1);
  d.setCursor(8, 140);
  d.print("screen test");
}

void runScreenStep(int n) {
  const char *name = "";
  switch (n) {
    case 1:
      name = "slow sw SPI, D/C 38, reset 1, ST7735 black tab";
      useHwSpi(false);
      swA.initR(INITR_BLACKTAB);
      showStep(swA, 1, ST77XX_RED);
      break;
    case 2:
      name = "slow sw SPI, D/C 1, reset 38, ST7735 black tab";
      useHwSpi(false);
      swB.initR(INITR_BLACKTAB);
      showStep(swB, 2, ST77XX_GREEN);
      break;
    case 3:
      name = "slow sw SPI, D/C 38, reset 1, ST7789 init";
      useHwSpi(false);
      sw89.init(128, 160);
      showStep(sw89, 3, ST77XX_BLUE);
      break;
    case 4:
      name = "hw SPI at 2 MHz, D/C 38, reset 1, ST7735 black tab";
      useHwSpi(true);
      tft.initR(INITR_BLACKTAB);
      tft.setSPISpeed(2000000);
      showStep(tft, 4, ST77XX_YELLOW);
      break;
    default:
      name = "slow sw SPI, D/C 38, reset 1, ST7735 green tab";
      useHwSpi(false);
      swA.initR(INITR_GREENTAB);
      showStep(swA, 5, ST77XX_MAGENTA);
      break;
  }
  logLine("screen test %d: %s", n, name);
}

// Called from loop(); returns true while the test owns the screen.
bool screenTestTick() {
  if (screenTestEndMs == 0) return false;
  uint32_t now = millis();
  if ((int32_t)(now - screenTestEndMs) >= 0) {
    screenTestEndMs = 0;
    useHwSpi(true);
    tft.initR(INITR_BLACKTAB);
    tft.setRotation(settings.rotation);
    logLine("screen test finished");
    return false;
  }
  if ((int32_t)(now - nextStepMs) >= 0) {
    screenStep = screenStep % 5 + 1;
    runScreenStep(screenStep);
    nextStepMs = millis() + SCREEN_STEP_MS;
    nextTelemetryMs = 0;  // send the log line now
  }
  return true;
}

void startScreenTest() {
  screenTestEndMs = millis() + SCREEN_TEST_MS;
  nextStepMs = 0;
  screenStep = 0;
  logLine("screen test started: 5 slow steps, %u s each, for 6 min", SCREEN_STEP_MS / 1000);
}
