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
static const uint32_t TALK_AFTER_MS = 350;   // hold this long to start talking
static const uint32_t HANDS_FREE_MS = 900;   // released sooner: keep listening until quiet
static const uint32_t QUIET_END_MS = 1200;   // hands-free: this much quiet after speech ends it
static const uint32_t NO_SPEECH_MS = 4500;   // hands-free: give up if nobody speaks
static const float VOLUME = 0.5f;
static const size_t TALK_MAX_SAMPLES = MIC_RATE * MAX_TALK_MS / 1000;

bool speakerReady = false, micReady = false;
bool talking = false, talkedThisPress = false, handsFree = false, heardSpeech = false;
uint32_t talkStartMs = 0, lastLoudMs = 0;
int32_t noiseLevel = 0, peakLevel = 0;
int16_t *talkBuf = nullptr;
size_t talkSamples = 0;
int lastSayId = -2;  // -2: no poll yet (don't replay old speech after a restart)

uint32_t speakerRate = 0;
uint32_t speakerStalls = 0;

bool installSpeaker() {
  i2s_config_t spk = {};
  spk.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  spk.sample_rate = 22050;
  spk.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  spk.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  spk.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  spk.dma_buf_count = 16;
  spk.dma_buf_len = 512;
  spk.tx_desc_auto_clear = true;
  i2s_pin_config_t spkPins = {};
  spkPins.mck_io_num = I2S_PIN_NO_CHANGE;
  spkPins.bck_io_num = PIN_SPK_BCLK;
  spkPins.ws_io_num = PIN_SPK_LRCK;
  spkPins.data_out_num = PIN_SPK_DIN;
  spkPins.data_in_num = I2S_PIN_NO_CHANGE;
  speakerRate = spk.sample_rate;
  return i2s_driver_install(I2S_NUM_0, &spk, 0, nullptr) == ESP_OK &&
         i2s_set_pin(I2S_NUM_0, &spkPins) == ESP_OK;
}

// The legacy I2S driver restarts the peripheral on every i2s_set_clk: only do it on a change.
void setSpeakerRate(uint32_t rate) {
  if (rate == speakerRate) return;
  i2s_set_clk(I2S_NUM_0, rate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
  speakerRate = rate;
}

void restartSpeaker() {
  i2s_driver_uninstall(I2S_NUM_0);
  speakerReady = installSpeaker();
}

void setupAudio() {
  speakerReady = installSpeaker();

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
    size_t written = 0;
    // Never wait forever: a wedged driver would hang the whole board.
    i2s_write(I2S_NUM_0, stereo, n * 4, &written, 300 / portTICK_PERIOD_MS);
    if (written < n * 4) {
      speakerStalls++;
      logLine("speaker stalled (%u): restarting it", (unsigned)speakerStalls);
      restartSpeaker();
      return;
    }
    i += n;
  }
}

// Let what's queued play out: the DMA buffers hold up to half a second, so push that much
// silence after it (clearing them instead would erase a short beep before it is heard).
void finishSound() {
  static int16_t silence[512] = {0};
  size_t frames = speakerRate / 2, written = 0;
  while (frames > 0) {
    size_t n = min(frames, (size_t)256);
    i2s_write(I2S_NUM_0, silence, n * 4, &written, 300 / portTICK_PERIOD_MS);
    if (written < n * 4) break;
    frames -= n;
  }
}

void tone(int freq, int ms) {
  if (!speakerReady) return;
  const int rate = 16000;
  setSpeakerRate(rate);
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
  delay(ms + 30);  // the beep went into idle buffers: it plays now
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

// IMA ADPCM (the server's compact speech format, see backend/wind_monitor/voice.py).
static const int16_t ADPCM_STEPS[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
    73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408,
    449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630,
    9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767};
static const int8_t ADPCM_INDEX[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

struct Adpcm {
  int32_t predictor = 0;
  int index = 0;
  int16_t next(uint8_t code) {
    int step = ADPCM_STEPS[index];
    int delta = step >> 3;
    if (code & 4) delta += step;
    if (code & 2) delta += step >> 1;
    if (code & 1) delta += step >> 2;
    predictor += (code & 8) ? -delta : delta;
    predictor = constrain(predictor, -32768, 32767);
    index = constrain(index + ADPCM_INDEX[code], 0, 88);
    return (int16_t)predictor;
  }
};

bool playSpeechWav(const String &path);

// Plays the server's compact speech as it downloads: about 8 KB per second of speech, so the
// download keeps ahead of the speaker after a short head start. Falls back to the WAV.
bool playSpeech(const String &path) {
  if (!speakerReady) return false;
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, settings.server + path + "?format=adpcm");
  http.setTimeout(15000);
  uint32_t started = millis();
  int code = http.GET();
  int size = http.getSize();
  uint32_t gotHeaders = millis() - started;
  if (code != 200 || size <= 12 || size > 2 * 1024 * 1024) {
    logLine("speech %s -> HTTP %d, %d bytes: trying WAV", path.c_str(), code, size);
    http.end();
    return playSpeechWav(path);
  }
  uint8_t *data = (uint8_t *)ps_malloc(size);
  if (!data) {
    logLine("speech: out of memory");
    http.end();
    return false;
  }
  WiFiClient *s = http.getStreamPtr();
  // Head start: the header plus about 0.75 s of speech (or the whole clip if shorter).
  size_t have = readFully(s, data, min((size_t)size, (size_t)(12 + 6000)), 8000), used = 12;
  if (have < 12 || memcmp(data, "WBA1", 4)) {
    logLine("speech: compact failed (%u of %d bytes, starts %02x%02x%02x%02x, headers %u ms, "
            "connected %d): trying WAV",
            (unsigned)have, size, have > 0 ? data[0] : 0, have > 1 ? data[1] : 0,
            have > 2 ? data[2] : 0, have > 3 ? data[3] : 0, (unsigned)gotHeaders,
            (int)s->connected());
    free(data);
    http.end();
    return playSpeechWav(path);
  }
  uint32_t rate = data[4] | (data[5] << 8) | (data[6] << 16) | ((uint32_t)data[7] << 24);
  uint32_t total = data[8] | (data[9] << 8) | (data[10] << 16) | ((uint32_t)data[11] << 24);
  setSpeakerRate(rate);
  Adpcm decoder;
  int16_t pcm[512];
  uint32_t played = 0, underruns = 0;
  while (played < total) {
    // Fetch the next block (about 64 ms of speech; the link brings it in about 40 ms, and the
    // speaker's DMA holds half a second, so it never runs dry).
    if (have < (size_t)size && have - used < 2048) {
      size_t got = readFully(s, data + have, min((size_t)size - have, (size_t)512), 3000);
      have += got;
      if (got == 0) underruns++;
    }
    if (used >= have) break;  // nothing more arrived
    size_t bytes = min(have - used, (size_t)256);
    size_t n = 0;
    for (size_t i = 0; i < bytes && played + n < total; i++) {
      pcm[n++] = decoder.next(data[used + i] & 15);
      if (played + n < total) pcm[n++] = decoder.next(data[used + i] >> 4);
    }
    used += bytes;
    playMono(pcm, n);
    played += n;
  }
  finishSound();
  free(data);
  http.end();
  logLine("said %.1f s (%d bytes, %u ms, %u waits)", played / (float)rate, size,
          (unsigned)(millis() - started), (unsigned)underruns);
  return played > 0;
}

bool playSpeechWav(const String &path) {  // the plain WAV route (not used by default)
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
  // Download the whole clip first (PSRAM), then play it: playing while downloading over HTTPS
  // stutters whenever the network falls behind the speaker.
  int size = http.getSize();
  if (size <= 44 || size > 3 * 1024 * 1024) {
    logLine("speech: bad size %d", size);
    http.end();
    return false;
  }
  uint8_t *clip = (uint8_t *)ps_malloc(size);
  if (!clip) {
    logLine("speech: out of memory");
    http.end();
    return false;
  }
  uint32_t started = millis();
  size_t got = readFully(http.getStreamPtr(), clip, size, 8000);
  http.end();
  if (got != (size_t)size || memcmp(clip, "RIFF", 4)) {
    logLine("speech: got %u of %d bytes", (unsigned)got, size);
    free(clip);
    return false;
  }
  uint32_t rate = clip[24] | (clip[25] << 8) | (clip[26] << 16) | ((uint32_t)clip[27] << 24);
  size_t samples = (size - 44) / 2;
  setSpeakerRate(rate);
  playMono((const int16_t *)(clip + 44), samples);
  finishSound();
  free(clip);
  logLine("said %.1f s (downloaded in %u ms)", samples / (float)rate, (unsigned)(millis() - started));
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
  handsFree = heardSpeech = false;
  talkSamples = 0;
  noiseLevel = peakLevel = 0;
  tone(880, 70);  // "listening"
  i2s_start(I2S_NUM_1);
  talkStartMs = lastLoudMs = millis();
  logLine("listening");
}

void finishTalking();

void readMic() {
  int32_t raw[256];
  size_t got = 0;
  i2s_read(I2S_NUM_1, raw, sizeof(raw), &got, 20 / portTICK_PERIOD_MS);
  size_t n = got / 4;
  int64_t energy = 0;
  for (size_t i = 0; i < n && talkSamples < TALK_MAX_SAMPLES; i++) {
    int32_t v = constrain(raw[i] >> 13, -32768, 32767);
    talkBuf[talkSamples++] = (int16_t)v;
    energy += (int64_t)v * v;
  }
  if (talkSamples >= TALK_MAX_SAMPLES) {
    finishTalking();
    return;
  }
  if (n == 0) return;
  int32_t level = (int32_t)sqrtf((float)(energy / (int64_t)n));
  uint32_t now = millis();
  if (now - talkStartMs < 250) {  // the first moments set the room's noise level
    noiseLevel = max(noiseLevel, level);
    return;
  }
  peakLevel = max(peakLevel, level);
  if (level > max(noiseLevel * 3, (int32_t)500)) {
    heardSpeech = true;
    lastLoudMs = now;
  }
  if (handsFree) {  // released early: end on quiet after speech, or if nobody speaks
    if ((heardSpeech && now - lastLoudMs > QUIET_END_MS) ||
        (!heardSpeech && now - talkStartMs > NO_SPEECH_MS)) {
      finishTalking();
    }
  }
}

// Button released while listening: a quick release keeps listening hands-free.
void releaseTalking() {
  if (millis() - talkStartMs < HANDS_FREE_MS) {
    handsFree = true;
    logLine("hands-free: listening until you stop talking");
    return;
  }
  finishTalking();
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
  if (!talking) return;
  talking = handsFree = false;
  i2s_stop(I2S_NUM_1);
  float seconds = talkSamples / (float)MIC_RATE;
  logLine("recorded %.1f s, mic level: room %d, loudest %d", seconds, (int)noiseLevel,
          (int)peakLevel);
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
