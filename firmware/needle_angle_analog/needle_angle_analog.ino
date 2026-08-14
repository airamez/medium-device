/*
 * Fallback if I2C scan finds nothing.
 * Uses AS5600 analog OUT pin (not SDA/SCL).
 *
 * Wiring:
 *   AS5600 VCC -> Nano 5V  (or 3.3V if module is 3.3V-only)
 *   AS5600 GND -> Nano GND
 *   AS5600 OUT -> Nano A0
 *   Leave SDA/SCL unconnected for this test.
 */

void setup() {
  Serial.begin(115200);
  analogReference(DEFAULT);
}

static int readAveraged() {
  long sum = 0;
  for (int i = 0; i < 16; i++) {
    sum += analogRead(A0);
    delay(1);
  }
  return (int)(sum / 16);
}

void loop() {
  int raw = readAveraged();
  float deg = raw * (360.0 / 1023.0);

  Serial.print("a=");
  Serial.println(deg, 1);

  delay(20);
}
