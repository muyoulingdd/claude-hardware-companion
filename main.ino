const int LED_PIN = LED_BUILTIN;

String currentState = "IDLE";
unsigned long lastBlinkMs = 0;
bool ledState = false;

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.begin(115200);
  while (!Serial) {
    delay(10);
  }

  currentState = "IDLE";
}

void loop() {
  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    handleCommand(line);
  }

  updateLed();
}

void handleCommand(const String& command) {
  // 兼容原版事件，保持默认示例行为不变。
  if (command == "PERMISSION_WAIT") {
    currentState = "WAITING_PERMISSION";
  } else if (command == "TASK_DONE") {
    currentState = "TASK_DONE";
  } else if (command == "ROUND_STOP") {
    currentState = "ROUND_STOP";
  }
  // 新增的更解耦 signal 如果当前固件未处理，会被自然忽略。
}

void updateLed() {
  unsigned long now = millis();

  if (currentState == "WAITING_PERMISSION") {
    if (now - lastBlinkMs >= 250) {
      lastBlinkMs = now;
      ledState = !ledState;
      digitalWrite(LED_PIN, ledState ? HIGH : LOW);
    }
    return;
  }

  if (currentState == "TASK_DONE") {
    digitalWrite(LED_PIN, HIGH);
    return;
  }

  if (currentState == "ROUND_STOP") {
    if (now - lastBlinkMs >= 800) {
      lastBlinkMs = now;
      ledState = !ledState;
      digitalWrite(LED_PIN, ledState ? HIGH : LOW);
    }
    return;
  }

  digitalWrite(LED_PIN, LOW);
}
