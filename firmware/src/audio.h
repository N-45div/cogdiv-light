// The board's voice: speaker (MAX98357A on I2S0) and microphone (INMP441 on I2S1).
//
// - Push to talk: in idle, hold BOOT and speak (up to 8 s). The recording goes to the server as
//   a 16 kHz mono WAV (POST /api/device/{id}/voice); the server transcribes it (Meta Muse Voice
//   Transcribe) and asks the agent.
// - Speaking: each poll may carry "say": {id, path}; a new id is downloaded (WAV) and played.
//   The server writes the words (the agent's fact-checked answer, or code-written news).
//
// Included from main.cpp after the helpers it uses (logLine, beginHttp, devicePath, settings).
#pragma once

#include <driver/i2s.h>

static const int MIC_RATE = 16000;
static const uint32_t MAX_TALK_MS = 8000;
static const uint32_t TALK_AFTER_MS = 350;  // hold this long in idle to start talking
static const float VOLUME = 0.5f;
static const size_t TALK_MAX_SAMPLES = MIC_RATE * MAX_TALK_MS / 1000;

bool speakerReady = false, micReady = false;
bool talking = false, talkedThisPress = false;
uint32_t talkStartMs = 0;
int16_t *talkBuf = nullptr;
size_t talkSamples = 0;
int lastSayId = -2;  // -2: no poll yet (don't replay old speech after a restart)

void setupAudio() {
  i2s_config_t spk = {};
  spk.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  spk.sample_rate = 22050;
  spk.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  spk.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  spk.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  spk.dma_buf_count = 8;
  spk.dma_buf_len = 256;
  spk.tx_desc_auto_clear = true;
  i2s_pin_config_t spkPins = {};
  spkPins.mck_io_num = I2S_PIN_NO_CHANGE;
  spkPins.bck_io_num = PIN_SPK_BCLK;
  spkPins.ws_io_num = PIN_SPK_LRCK;
  spkPins.data_out_num = PIN_SPK_DIN;
  spkPins.data_in_num = I2S_PIN_NO_CHANGE;
  speakerReady = i2s_driver_install(I2S_NUM_0, &spk, 0, nullptr) == ESP_OK &&
                 i2s_set_pin(I2S_NUM_0, &spkPins) == ESP_OK;

  i2s_config_t mic = {};
  mic.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  mic.sample_rate = MIC_RATE;
  mic.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;  // INMP441: 24-bit data in 32-bit slots
  mic.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  mic.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  mic.dma_buf_count = 8;
  mic.dma_buf_len = 256;
  i2s_pin_config_t micPins = {};
  micPins.mck_io_num = I2S_PIN_NO_CHANGE;
  micPins.bck_io_num = PIN_MIC_SCK;
  micPins.ws_io_num = PIN_MIC_WS;
  micPins.data_out_num = I2S_PIN_NO_CHANGE;
  micPins.data_in_num = PIN_MIC_SD;
  micReady = i2s_driver_install(I2S_NUM_1, &mic, 0, nullptr) == ESP_OK &&
             i2s_set_pin(I2S_NUM_1, &micPins) == ESP_OK;
  if (micReady) i2s_stop(I2S_NUM_1);  // runs only while talking
  talkBuf = (int16_t *)ps_malloc(TALK_MAX_SAMPLES * sizeof(int16_t));
  if (!talkBuf) micReady = false;
  logLine("audio: speaker %s, mic %s", speakerReady ? "ok" : "FAILED", micReady ? "ok" : "FAILED");
}

void playMono(const int16_t *samples, size_t count) {
  int16_t stereo[512];
  for (size_t i = 0; i < count;) {
    size_t n = min(count - i, (size_t)256);
    for (size_t j = 0; j < n; j++) {
      int16_t v = (int16_t)(samples[i + j] * VOLUME);
      stereo[2 * j] = stereo[2 * j + 1] = v;
    }
    size_t written;
    i2s_write(I2S_NUM_0, stereo, n * 4, &written, portMAX_DELAY);
    i += n;
  }
}

void tone(int freq, int ms) {
  if (!speakerReady) return;
  const int rate = 16000;
  i2s_set_clk(I2S_NUM_0, rate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
  int16_t buf[256];
  int total = rate * ms / 1000;
  for (int done = 0; done < total;) {
    int n = min(total - done, 256);
    for (int j = 0; j < n; j++) {
      buf[j] = (int16_t)(9000 * sinf(2 * PI * freq * (done + j) / rate));
    }
    playMono(buf, n);
    done += n;
  }
  i2s_zero_dma_buffer(I2S_NUM_0);
}

void chime() {  // ready
  tone(660, 90);
  tone(990, 140);
}

size_t readFully(WiFiClient *s, uint8_t *dst, size_t want, uint32_t timeoutMs = 4000) {
  size_t have = 0;
  uint32_t start = millis();
  while (have < want && millis() - start < timeoutMs) {
    int avail = s->available();
    if (avail > 0) {
      have += s->read(dst + have, min((size_t)avail, want - have));
      start = millis();
    } else if (!s->connected()) {
      break;
    } else {
      delay(1);
    }
  }
  return have;
}

bool playSpeech(const String &path) {
  if (!speakerReady) return false;
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, settings.server + path);
  http.setTimeout(15000);
  int code = http.GET();
  if (code != 200) {
    logLine("speech %s -> HTTP %d", path.c_str(), code);
    http.end();
    return false;
  }
  WiFiClient *s = http.getStreamPtr();
  uint8_t header[44];
  if (readFully(s, header, sizeof(header)) != sizeof(header) || memcmp(header, "RIFF", 4)) {
    logLine("speech: not a WAV");
    http.end();
    return false;
  }
  uint32_t rate = header[24] | (header[25] << 8) | (header[26] << 16) | ((uint32_t)header[27] << 24);
  i2s_set_clk(I2S_NUM_0, rate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
  int remaining = http.getSize() > 44 ? http.getSize() - 44 : -1;
  int16_t buf[256];
  uint32_t samples = 0;
  while (remaining != 0) {
    size_t want = remaining < 0 ? sizeof(buf) : min((size_t)remaining, sizeof(buf));
    size_t got = readFully(s, (uint8_t *)buf, want);
    if (got < 2) break;
    playMono(buf, got / 2);
    samples += got / 2;
    if (remaining > 0) remaining -= got;
  }
  i2s_zero_dma_buffer(I2S_NUM_0);
  http.end();
  logLine("said %.1f s", samples / (float)rate);
  return true;
}

// Called with every state the server sends (polls and button replies).
void maybeSpeak(JsonDocument &doc) {
  JsonVariant say = doc["say"];
  int id = say.isNull() ? -1 : (int)(say["id"] | -1);
  if (lastSayId == -2) {  // first state after boot: whatever is there is old news
    lastSayId = id;
    return;
  }
  if (id < 0 || id == lastSayId) return;
  lastSayId = id;
  playSpeech(say["path"] | "");
}

void startTalking() {
  if (!micReady) {
    logLine("can't listen: no microphone");
    return;
  }
  talking = talkedThisPress = true;
  talkSamples = 0;
  tone(880, 70);  // "listening"
  i2s_start(I2S_NUM_1);
  talkStartMs = millis();
  logLine("listening");
}

void readMic() {
  int32_t raw[256];
  size_t got = 0;
  i2s_read(I2S_NUM_1, raw, sizeof(raw), &got, 20 / portTICK_PERIOD_MS);
  for (size_t i = 0; i < got / 4 && talkSamples < TALK_MAX_SAMPLES; i++) {
    int32_t v = raw[i] >> 13;
    talkBuf[talkSamples++] = (int16_t)constrain(v, -32768, 32767);
  }
}

void putLE(uint8_t *p, uint32_t v, int bytes) {
  for (int i = 0; i < bytes; i++) p[i] = (v >> (8 * i)) & 0xff;
}

void sendVoice(const int16_t *samples, size_t count) {
  size_t dataBytes = count * 2;
  uint8_t *wav = (uint8_t *)ps_malloc(44 + dataBytes);
  if (!wav) {
    logLine("voice: out of memory");
    return;
  }
  memcpy(wav, "RIFF", 4);
  putLE(wav + 4, 36 + dataBytes, 4);
  memcpy(wav + 8, "WAVEfmt ", 8);
  putLE(wav + 16, 16, 4);
  putLE(wav + 20, 1, 2);             // PCM
  putLE(wav + 22, 1, 2);             // mono
  putLE(wav + 24, MIC_RATE, 4);
  putLE(wav + 28, MIC_RATE * 2, 4);  // byte rate
  putLE(wav + 32, 2, 2);             // block align
  putLE(wav + 34, 16, 2);            // bits
  memcpy(wav + 36, "data", 4);
  putLE(wav + 40, dataBytes, 4);
  memcpy(wav + 44, samples, dataBytes);

  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, settings.server + devicePath("voice"));
  http.setTimeout(45000);  // transcription + the agent take ~10 s
  http.addHeader("Content-Type", "audio/wav");
  int code = http.POST(wav, 44 + dataBytes);
  String body = http.getString();
  http.end();
  free(wav);
  if (code == 200) {
    JsonDocument reply;
    deserializeJson(reply, body);
    String heard = reply["transcript"] | "";
    logLine("heard: %s%s", heard.substring(0, 70).c_str(),
            (reply["proposed"] | false) ? " -> plan proposed" : "");
  } else {
    logLine("voice -> HTTP %d", code);
    tone(220, 250);  // error
  }
  nextPollMs = 0;  // pick up the spoken answer now
}

void finishTalking() {
  talking = false;
  i2s_stop(I2S_NUM_1);
  float seconds = talkSamples / (float)MIC_RATE;
  // Drop the first 100 ms (button click and mic start-up).
  size_t skip = min(talkSamples, (size_t)(MIC_RATE / 10));
  if (seconds < 0.6f) {
    logLine("too short to send (%.1f s)", seconds);
    tone(330, 120);
    return;
  }
  tone(660, 70);  // "got it"
  logLine("sending %.1f s of speech", seconds);
  sendVoice(talkBuf + skip, talkSamples - skip);
}
