// Pins of the OceanLabz "AI Explorer ESP32-S3-CAM Board V3.2" with the 1.8" TFT module.
//
// Source: read from the board itself. With OceanLabz's shipped firmware (XiaoZhi v2.2.2,
// "bread-compact-wifi-s3cam") running and its screen working, the GPIO matrix was dumped over
// JTAG (GPIO_FUNCn_OUT_SEL_CFG): SPI3 clock -> 43, SPI3 data -> 44, SPI3 CS0 -> 45, LEDC PWM -> 42,
// plain outputs 38 (D/C) and 1 (reset). This matches OceanLabz's "ESP32 S3 AI Expansion" page,
// not the upstream XiaoZhi config or their camera-board page (19/20/47/21/38), which leave
// the screen white on this board and drive the USB pins.
#pragma once

// 1.8" ST7735 128x160 TFT (SPI). 43/44 are UART0's default pins, so the console runs over USB.
#define PIN_TFT_CS 45
#define PIN_TFT_DC 38
#define PIN_TFT_RST 1
#define PIN_TFT_SCLK 43
#define PIN_TFT_MOSI 44
#define PIN_TFT_BL 42

// BOOT button: pressed = LOW. Tap / hold for the device's yes-no prompts.
#define PIN_BUTTON 0

// Free pin (XiaoZhi's "lamp" pin, not driven by the shipped firmware): the relay or a MOSFET.
#define PIN_RELAY 14
#define RELAY_ACTIVE_HIGH 1

// No status LED is driven: GPIO 48 belongs to the speaker amp on this board.
#define PIN_RGB_LED -1

// Speaker amp (MAX98357A, I2S0) and microphone (INMP441, I2S1), from the same JTAG dump:
// I2S0O_BCK -> 48, I2S0O_WS -> 21, I2S0O_SD -> 47; I2S1I_BCK -> 40, I2S1I_WS -> 39. The mic's
// data input (41) is from OceanLabz's expansion page (inputs don't show in the output matrix).
#define PIN_SPK_BCLK 48
#define PIN_SPK_LRCK 21
#define PIN_SPK_DIN 47
#define PIN_MIC_SCK 40
#define PIN_MIC_WS 39
#define PIN_MIC_SD 41

// Not used yet, listed so nothing else claims them.
// Camera (OV3660): D0-D7 11,9,8,10,12,18,17,16; XCLK 15; PCLK 13; VSYNC 6; HREF 7; SDA 4; SCL 5
// USB (19/20) stays free, so USB serial, JTAG and the boot log keep working while it runs.
