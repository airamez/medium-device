/*
 * Wire check — find what is wrong when scan finds nothing.
 *
 * Arduino IDE: File → Open this file → Upload → Tools → Serial Monitor → 115200
 * Or: python host/capture.py
 *
 * Normal 5 wires:
 *   AS5600 VCC -> Nano 5V   (or 3.3V if the module says 3.3V-only)
 *   AS5600 GND -> Nano GND
 *   AS5600 SDA -> Nano A4
 *   AS5600 SCL -> Nano A5
 *   AS5600 DIR -> Nano GND  (or a short jumper to the module GND)
 *
 * Extra diagnostic wire (add this for the test):
 *   AS5600 OUT -> Nano A0
 *   If your module has no OUT pin, skip it.
 */

#include <Wire.h>

static const uint8_t PIN_SDA = A4;
static const uint8_t PIN_SCL = A5;
static const uint8_t PIN_OUT = A0;

static void printPinLevel(const char *name, uint8_t pin) {
  pinMode(pin, INPUT_PULLUP);
  delay(5);
  int v = digitalRead(pin);
  Serial.print("  ");
  Serial.print(name);
  Serial.print(" (pin ");
  Serial.print(pin);
  Serial.print(") = ");
  Serial.println(v == HIGH ? "HIGH (ok idle)" : "LOW (stuck or shorted to GND)");
}

static uint8_t scanI2C() {
  uint8_t found = 0;
  Serial.println("I2C scan:");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("  device at 0x");
      Serial.println(addr, HEX);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("  no device");
  }
  return found;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("=== wire_check ===");
}

void loop() {
  Serial.println();
  Serial.println("--- test ---");

  // 1) Nano pin idle test (Wire not started)
  Serial.println("1) Nano A4/A5 with internal pull-up:");
  printPinLevel("SDA/A4", PIN_SDA);
  printPinLevel("SCL/A5", PIN_SCL);

  pinMode(PIN_SDA, INPUT_PULLUP);
  pinMode(PIN_SCL, INPUT_PULLUP);
  delay(5);
  int sda = digitalRead(PIN_SDA);
  int scl = digitalRead(PIN_SCL);

  // 2) Analog OUT: if VCC+GND reach the module, OUT is rarely exactly 0
  int out = analogRead(PIN_OUT);
  Serial.print("2) A0 (AS5600 OUT if you connected it) analog=");
  Serial.print(out);
  Serial.print("  (~");
  Serial.print(out * (5.0 / 1023.0), 2);
  Serial.println(" V)");

  // 3) I2C
  Wire.begin();
  delay(20);
  uint8_t found = scanI2C();
  Wire.end();
  pinMode(PIN_SDA, INPUT);
  pinMode(PIN_SCL, INPUT);

  Serial.println("3) Verdict:");
  if (found) {
    Serial.println("  I2C works. Use needle_angle_stream.ino");
  } else if (sda == LOW || scl == LOW) {
    Serial.println("  A4 and/or A5 sit LOW.");
    Serial.println("  That pin is shorted to GND, or the jumper is in the wrong row");
    Serial.println("  and touching a GND pin. Move SDA/SCL jumpers.");
    if (sda == LOW) {
      Serial.println("  -> suspect SDA jumper (AS5600 SDA <-> Nano A4)");
    }
    if (scl == LOW) {
      Serial.println("  -> suspect SCL jumper (AS5600 SCL <-> Nano A5)");
    }
  } else if (out > 80 && out < 1000) {
    Serial.println("  Module looks POWERED (OUT on A0 is not 0).");
    Serial.println("  VCC and GND jumpers are probably OK.");
    Serial.println("  Problem is SDA or SCL (wrong pin, loose, or swapped).");
    Serial.println("  Swap SDA and SCL, run this again.");
  } else if (out <= 80) {
    Serial.println("  I2C dead AND A0 near 0.");
    Serial.println("  Either:");
    Serial.println("   a) OUT is not connected to A0 (add that jumper and rerun), or");
    Serial.println("   b) VCC or GND to the AS5600 is open (module has no power).");
    Serial.println("  Check VCC -> 5V (or 3.3V) and GND -> GND first.");
  } else {
    Serial.println("  A0 is maxed out. Check OUT is not tied to 5V.");
    Serial.println("  Still no I2C: check SDA->A4 and SCL->A5.");
  }

  delay(2500);
}
