// Pins of the OceanLabz ESP32-S3 AI Explorer camera board (v1.2).
//
// Source: the firmware OceanLabz ships for this board is XiaoZhi v2.2.2 built with the
// `bread-compact-wifi-s3cam` board config (github.com/78/xiaozhi-esp32,
// main/boards/bread-compact-wifi-s3cam/config.h), which also matches OceanLabz's own
// wiring page for the 1.8" TFT.
#pragma once

// 1.8" ST7735 128x160 TFT (SPI)
#define PIN_TFT_CS 45
#define PIN_TFT_DC 47
#define PIN_TFT_RST 21
#define PIN_TFT_SCLK 19
#define PIN_TFT_MOSI 20
#define PIN_TFT_BL 38

// BOOT button: pressed = LOW. Tap / hold for the device's yes-no prompts.
#define PIN_BUTTON 0

// Spare pin XiaoZhi reserves for "control a lamp": drives the relay (or a MOSFET) here.
#define PIN_RELAY 14
#define RELAY_ACTIVE_HIGH 1

// Addressable RGB LED on the board.
#define PIN_RGB_LED 48

// Not used yet (voice and camera come later), listed so nothing else claims them.
// Mic (INMP441):    WS 1, SCK 2, SD 42      Speaker amp (MAX98357A): DOUT 39, BCLK 40, LRCK 41
// Camera (OV3660):  D0-D7 11,9,8,10,12,18,17,16; XCLK 15; PCLK 13; VSYNC 6; HREF 7; SDA 4; SCL 5
