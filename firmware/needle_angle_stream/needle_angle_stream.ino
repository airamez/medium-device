/*
 * Needle angle stream — Phase 2 electronics test
 *
 * Reads AS5600 over I2C and prints: a=123.4
 * Baud: 115200, about every 20 ms.
 *
 * Wiring (AS5600 -> Nano):
 *   VCC -> 5V   (use 3.3V if your module is labeled 3.3V only)
 *   GND -> GND
 *   SDA -> A4
 *   SCL -> A5
 */

#include <Wire.h>

static const uint8_t AS5600_ADDR = 0x36;
static const uint8_t AS5600_RAW_ANGLE = 0x0C;

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
    Serial.println("  none (check VCC GND SDA->A4 SCL->A5)");
  }
  return found;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  delay(200);
  scanBus();
}

static uint16_t readRawAngle() {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(AS5600_RAW_ANGLE);
  if (Wire.endTransmission(false) != 0) {
    return 0xFFFF;
  }
  if (Wire.requestFrom(AS5600_ADDR, (uint8_t)2) != 2) {
    return 0xFFFF;
  }
  uint16_t high = Wire.read();
  uint16_t low = Wire.read();
  return ((high & 0x0F) << 8) | low;
}

void loop() {
  uint16_t raw = readRawAngle();
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
    Serial.println(deg, 1);
  }
  delay(20);
}
