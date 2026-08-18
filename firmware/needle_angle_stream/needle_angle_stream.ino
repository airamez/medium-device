/*
 * Needle angle stream — Phase 2 electronics test
 *
 * Reads AS5600 over I2C and prints: a=123.45
 * Baud: 115200, about every 20 ms.
 *
 * Smoothing (does not add bits; cuts LSB flicker when the needle is still):
 *   CONF hysteresis = 2 LSB
 *   CONF slow filter = 16x, fast-filter off
 *   Read ANGLE (0x0E, filtered), not RAW ANGLE (0x0C)
 *   Average 8 samples spaced 2 ms apart
 *
 * Wiring (AS5600 -> Nano):
 *   VCC -> 5V   (use 3.3V if your module is labeled 3.3V only)
 *   GND -> GND
 *   SDA -> A4
 *   SCL -> A5
 *   DIR -> GND  (do not leave DIR floating — angle will jump)
 */

#include <Wire.h>

static const uint8_t AS5600_ADDR = 0x36;
static const uint8_t AS5600_CONF = 0x07;
static const uint8_t AS5600_ANGLE = 0x0E;
static const uint8_t AVG_SAMPLES = 8;
static const uint8_t AVG_GAP_MS = 2;

// CONF: 1:0 PM, 3:2 HYST, 5:4 OUTS, 7:6 PWMF, 9:8 SF, 12:10 FTH, 13 WD
static const uint16_t CONF_HYST_MASK = 0x000C;
static const uint16_t CONF_HYST_2LSB = (2 << 2);
static const uint16_t CONF_SF_MASK = 0x0300;
static const uint16_t CONF_SF_16X = (0 << 8);
static const uint16_t CONF_FTH_MASK = 0x1C00;
static const uint16_t CONF_FTH_SLOW_ONLY = (0 << 10);

static uint8_t scanBus() {
  uint8_t found = 0;
  Serial.println("scan:");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("  found 0x");
      Serial.println(addr, HEX);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("  none (check VCC GND SDA->A4 SCL->A5 DIR->GND)");
  }
  return found;
}

static uint16_t readReg16(uint8_t reg) {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return 0xFFFF;
  }
  if (Wire.requestFrom(AS5600_ADDR, (uint8_t)2) != 2) {
    return 0xFFFF;
  }
  uint16_t high = Wire.read();
  uint16_t low = Wire.read();
  return (high << 8) | low;
}

static bool writeConf(uint16_t conf) {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(AS5600_CONF);
  Wire.write((uint8_t)((conf >> 8) & 0x3F));
  Wire.write((uint8_t)(conf & 0xFF));
  return Wire.endTransmission() == 0;
}

static void configureFilter() {
  uint16_t conf = readReg16(AS5600_CONF);
  if (conf == 0xFFFF) {
    Serial.println("filter: CONF read failed (using chip defaults)");
    return;
  }
  conf &= 0x3FFF;
  conf = (uint16_t)((conf & ~CONF_HYST_MASK) | CONF_HYST_2LSB);
  conf = (uint16_t)((conf & ~CONF_SF_MASK) | CONF_SF_16X);
  conf = (uint16_t)((conf & ~CONF_FTH_MASK) | CONF_FTH_SLOW_ONLY);
  if (!writeConf(conf)) {
    Serial.println("filter: CONF write failed (using chip defaults)");
    return;
  }
  delay(2);
  uint16_t check = readReg16(AS5600_CONF);
  if (check == 0xFFFF || ((check & 0x3FFF) != conf)) {
    Serial.println("filter: CONF verify failed (using chip defaults)");
    return;
  }
  Serial.println("filter: hyst=2lsb sf=16x angle-reg avg=8");
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  delay(200);
  if (scanBus()) {
    configureFilter();
  }
}

static uint16_t readAngle() {
  uint16_t v = readReg16(AS5600_ANGLE);
  if (v == 0xFFFF) {
    return 0xFFFF;
  }
  return v & 0x0FFF;
}

static uint16_t averageAngle() {
  uint16_t first = 0xFFFF;
  int32_t acc = 0;
  uint8_t n = 0;
  for (uint8_t i = 0; i < AVG_SAMPLES; i++) {
    uint16_t a = readAngle();
    if (i + 1 < AVG_SAMPLES) {
      delay(AVG_GAP_MS);
    }
    if (a == 0xFFFF) {
      continue;
    }
    if (first == 0xFFFF) {
      first = a;
    }
    int16_t d = (int16_t)a - (int16_t)first;
    if (d > 2048) {
      d -= 4096;
    } else if (d < -2048) {
      d += 4096;
    }
    acc += d;
    n++;
  }
  if (n == 0) {
    return 0xFFFF;
  }
  int32_t mean = (int32_t)first + acc / (int32_t)n;
  if (mean < 0) {
    mean += 4096;
  } else if (mean >= 4096) {
    mean -= 4096;
  }
  return (uint16_t)mean;
}

void loop() {
  uint16_t raw = averageAngle();
  if (raw == 0xFFFF) {
    Serial.println("a=ERR");
    static uint8_t err_count = 0;
    if (++err_count >= 25) {
      err_count = 0;
      scanBus();
    }
  } else {
    float deg = raw * (360.0 / 4096.0);
    Serial.print("a=");
    Serial.println(deg, 2);
  }
}
