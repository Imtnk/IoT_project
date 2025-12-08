#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>              // for fabsf
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

#define WIFI_SSID       "2e"
#define WIFI_PASSWORD   "Thumb1234"


const char* CLASSIFICATION_API_URL = "http://10.248.108.149:5001/api/images";

unsigned long lastClassificationCheck = 0;
unsigned long CLASSIFICATION_CHECK_INTERVAL = 5000;
String lastImageID = "";

// -------------------------------------------------------------
// THINGSPEAK CONFIG
// -------------------------------------------------------------
const char* THINGSPEAK_API_KEY = "8JY8J009G99P8PBI";
const char* THINGSPEAK_SERVER  = "http://api.thingspeak.com/update";

unsigned long lastSendMillis      = 0; 
const unsigned long SEND_INTERVAL = 20000;

// -------------------------------------------------------------
// PIN DEFINES
// -------------------------------------------------------------
#define MAGNETIC_PIN   18
#define BUZZER_PIN     19
#define BUTTON_PIN     25
#define LIGHT_PIN      34

#define RED_LED        12
#define GREEN_LED      13
#define BLUE_LED       14

// Ultrasonic pins
#define US_TRIG_PIN    22
#define US_ECHO_PIN    23

// -------------------------------------------------------------
// THRESHOLDS
// -------------------------------------------------------------
const int   LIGHT_THRESHOLD            = 2000;
const float HAND_DIST_THRESHOLD_CM     = 35.0f;

// Timing windows
const unsigned long DOOR_OPEN_GRACE_MS       = 60000;
const unsigned long ITEM_ON_COUNTER_GRACE_MS = 90000;
const unsigned long PICKUP_WAIT_MS           = 60000;

// -------------------------------------------------------------
// STATE TRACKING
// -------------------------------------------------------------
unsigned long doorOpenSince   = 0;
bool          doorWasOpen     = false;
bool          hadItemAtDoorOpen = false;
bool          handDuringOpen    = false;

unsigned long itemOnCounterSince = 0;
bool          itemWasOnCounter   = false;
// "itemProcessed" = user pressed button for CURRENT item (classification requested)
bool          itemProcessed      = false;

unsigned long lastButtonPress = 0;
// we now track RAW button for edge detection
int           lastButtonRaw = 0;

bool          waitingAfterPickup     = false;
unsigned long pickupDoorCloseSince   = 0;

int handDetectLatched = 0;

// AI state: true when AI already finished classification for current item
bool          aiDoneForCurrentItem   = false;

// latched “virtual” button state that you want:
// 1 after press, stays 1 until AI done, then back to 0
bool          buttonLatched          = false;

// -------------------------------------------------------------
// SYSTEM STATES
// -------------------------------------------------------------
enum SystemState {
  STATE_NORMAL     = 0,
  STATE_PROCESSING = 1,
  STATE_WAITING    = 2,
  STATE_ABNORMAL   = 3
};

SystemState currentSystemState = STATE_NORMAL;

// -------------------------------------------------------------
// LED PWM CONFIG
// -------------------------------------------------------------
const int LEDC_CHANNEL_RED   = 0;
const int LEDC_CHANNEL_GREEN = 1;
const int LEDC_CHANNEL_BLUE  = 2;
const int LEDC_TIMER_BIT     = 8;
const int LEDC_BASE_FREQ     = 5000;

void setRGB(uint8_t r, uint8_t g, uint8_t b) {
  ledcWrite(LEDC_CHANNEL_RED,   r);
  ledcWrite(LEDC_CHANNEL_GREEN, g);
  ledcWrite(LEDC_CHANNEL_BLUE,  b);
}

// -------------------------------------------------------------
// APPLY LED STATE + BUZZER
// -------------------------------------------------------------
void applyState(SystemState s) {
  currentSystemState = s;

  if (s == STATE_ABNORMAL) {
    setRGB(255, 0, 0);
    digitalWrite(BUZZER_PIN, HIGH);
  }
  else if (s == STATE_NORMAL) {
    setRGB(0, 255, 0);
    digitalWrite(BUZZER_PIN, LOW);
  }
  else if (s == STATE_PROCESSING) {
    setRGB(0, 0, 255);
    digitalWrite(BUZZER_PIN, LOW);
  }
  else if (s == STATE_WAITING) {
    setRGB(255, 120, 0);
    digitalWrite(BUZZER_PIN, LOW);
  }
}

// -------------------------------------------------------------
// ULTRASONIC SENSOR
// -------------------------------------------------------------
float readUltrasonicCM() {
  digitalWrite(US_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(US_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(US_TRIG_PIN, LOW);

  long duration = pulseIn(US_ECHO_PIN, HIGH, 30000);
  if (duration == 0) return -1.0f;

  return (duration * 0.0343f) / 2.0f;
}

// -------------------------------------------------------------
// SEND TO THINGSPEAK
// -------------------------------------------------------------
void sendToThingSpeak(int magnetic,
                      int buttonState,
                      int lightVal,
                      int lightState,
                      float distanceCM,
                      int distanceState)
{
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;

  String url = String(THINGSPEAK_SERVER) +
    "?api_key=" + THINGSPEAK_API_KEY +
    "&field1=" + String(magnetic) +
    "&field2=" + String(buttonState) +
    "&field3=" + String(lightVal) +
    "&field4=" + String(lightState) +
    "&field5=" + String(distanceCM) +
    "&field6=" + String(distanceState);

  http.begin(url);
  http.GET();
  http.end();
}

// -------------------------------------------------------------
// CHECK LOCAL API FOR NEW CLASSIFIED IMAGE
// -------------------------------------------------------------
bool checkForNewClassifiedImage() {
  unsigned long now = millis();
  if (now - lastClassificationCheck < CLASSIFICATION_CHECK_INTERVAL) return false;
  lastClassificationCheck = now;

  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.begin(CLASSIFICATION_API_URL);

  int code = http.GET();
  if (code <= 0) {
    Serial.print("HTTP Error: ");
    Serial.println(http.errorToString(code));
    http.end();
    return false;
  }

  String payload = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.print("JSON Error: ");
    Serial.println(err.c_str());
    return false;
  }

  if (!doc.is<JsonArray>() || doc.size() == 0) {
    Serial.println("No images in API.");
    return false;
  }

  JsonObject newest = doc[0];
  String imageID = newest["id"] | "";

  Serial.print("📸 API newest image id = ");
  Serial.println(imageID);

  if (lastImageID != imageID) {
    if (lastImageID != "") {
      Serial.println("🔥 NEW CLASSIFICATION DETECTED!");
      Serial.print("Label = ");
      Serial.println((const char*) newest["label"]);
      lastImageID = imageID;
      return true;    // NEW image compared to last call
    }
    lastImageID = imageID;      // first run
  }

  return false;
}

// -------------------------------------------------------------
// SETUP
// -------------------------------------------------------------
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  Serial.begin(115200);
  Serial.println("=== ESP32 Smart Box Booting ===");

  pinMode(MAGNETIC_PIN, INPUT_PULLDOWN);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LIGHT_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  ledcSetup(LEDC_CHANNEL_RED,   LEDC_BASE_FREQ, LEDC_TIMER_BIT);
  ledcSetup(LEDC_CHANNEL_GREEN, LEDC_BASE_FREQ, LEDC_TIMER_BIT);
  ledcSetup(LEDC_CHANNEL_BLUE,  LEDC_BASE_FREQ, LEDC_TIMER_BIT);
  ledcAttachPin(RED_LED,   LEDC_CHANNEL_RED);
  ledcAttachPin(GREEN_LED, LEDC_CHANNEL_GREEN);
  ledcAttachPin(BLUE_LED,  LEDC_CHANNEL_BLUE);

  pinMode(US_TRIG_PIN, OUTPUT);
  pinMode(US_ECHO_PIN, INPUT);
  digitalWrite(US_TRIG_PIN, LOW);

  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(300);
  }
  Serial.println("\nWiFi connected!");

  applyState(STATE_NORMAL);
}

// -------------------------------------------------------------
// LOOP
// -------------------------------------------------------------
void loop() {
  unsigned long now = millis();

  // ==============================================================  
  // RAW SENSOR READINGS
  // ==============================================================  
  int magnetic      = digitalRead(MAGNETIC_PIN);

  int buttonRawLevel   = digitalRead(BUTTON_PIN);       // HIGH = not pressed (pullup)
  int buttonRawPressed = (buttonRawLevel == HIGH) ? 1 : 0;

  int   lightVal   = analogRead(LIGHT_PIN);
  int   lightState = (lightVal > LIGHT_THRESHOLD) ? 1 : 0;
  float distanceRaw = readUltrasonicCM();
  float distance    = (distanceRaw < 0) ? 9999.0 : distanceRaw;

  bool doorOpen      = (magnetic == LOW);
  bool counterEmpty  = (lightState == 1);
  bool itemOnCounter = !counterEmpty;

  // physical hand detection
  bool handInPath = false;
  if (doorOpen && distanceRaw > 0 && distance < HAND_DIST_THRESHOLD_CM) handInPath = true;
  if (handInPath) handDetectLatched = 1;

  // ==============================================================  
  // BUTTON EDGE → LATCH "buttonState"
  // ==============================================================  
  if (buttonRawPressed == 1 && lastButtonRaw == 0) {
    // physical rising edge
    lastButtonPress = now;
    Serial.println("🔘 Physical button PRESS detected.");

    if (itemOnCounter) {
      Serial.println("🟧 ITEM ON COUNTER → classification requested, latch buttonState=1, go WAITING.");
      buttonLatched          = true;   // stays 1 until AI done
      itemProcessed          = true;
      aiDoneForCurrentItem   = false;  // new request
    } else {
      Serial.println("⚠ Button pressed with NO item on counter (will be abnormal).");
      // we do NOT latch button in this case
    }
  }
  lastButtonRaw = buttonRawPressed;

  // This is your logical buttonState (for serial + ThingSpeak)
  int buttonState = buttonLatched ? 1 : 0;
  bool buttonPressed = (buttonState == 1);   // "pressed" in logical sense

  // ==============================================================  
  // CHECK FOR NEW CLASSIFIED IMAGE FROM API
  // ==============================================================  
  bool newImageDetected = checkForNewClassifiedImage();
  if (newImageDetected && itemProcessed) {
    // Only relevant if this box has actually sent a classification request
    aiDoneForCurrentItem = true;
    buttonLatched        = false;      // ← reset logical buttonState to 0
    Serial.println("🔵 AI finished classification for current item → buttonState reset to 0.");
  }

  // Recompute logical buttonState after potential reset
  buttonState  = buttonLatched ? 1 : 0;
  buttonPressed = (buttonState == 1);

  bool doorClose = !doorOpen;

  // -------------- DOOR OPEN LOGIC ----------------
  if (doorOpen && !doorWasOpen) {
    doorWasOpen       = true;
    doorOpenSince     = now;
    handDuringOpen    = false;
    hadItemAtDoorOpen = itemOnCounter;
  }
  else if (!doorOpen && doorWasOpen) {
    doorWasOpen = false;
    if (handDuringOpen && counterEmpty) {
      waitingAfterPickup   = true;
      pickupDoorCloseSince = now;
    }
    handDuringOpen = false;
  }

  if (doorOpen && handInPath) handDuringOpen = true;

  unsigned long doorOpenFor = doorWasOpen ? (now - doorOpenSince) : 0;

  // -------------- ITEM TRACKING ----------------
  if (itemOnCounter && !itemWasOnCounter) {
    // new item placed
    itemWasOnCounter     = true;
    itemOnCounterSince   = now;
    itemProcessed        = false;      // new item → need new press
    aiDoneForCurrentItem = false;
    buttonLatched        = false;      // logical button back to 0
  } 
  else if (!itemOnCounter && itemWasOnCounter) {
    // item just removed
    itemWasOnCounter = false;
    // decision: NORMAL vs ABNORMAL handled below
  }

  unsigned long itemOnCounterFor = itemWasOnCounter ? (now - itemOnCounterSince) : 0;

  if (waitingAfterPickup && itemOnCounter) waitingAfterPickup = false;

  // ==============================================================  
  // FINAL SYSTEM STATE
  // ==============================================================  
  SystemState systemState = currentSystemState;
  bool abnormal   = false;
  bool waiting    = false;
  bool processing = false;

  // --- New abnormal: item removed before AI finished ---
  if (!itemOnCounter && itemProcessed && !aiDoneForCurrentItem) {
    abnormal = true;
    Serial.println("❌ Item removed BEFORE AI finished → ABNORMAL.");
  }

  // Old abnormal rules
  if (doorOpen && counterEmpty && doorOpenFor > DOOR_OPEN_GRACE_MS) abnormal = true;
  if (buttonRawPressed && !itemOnCounter) abnormal = true; // physical press w/out item
  if (waitingAfterPickup && counterEmpty && (now - pickupDoorCloseSince > PICKUP_WAIT_MS)) abnormal = true;
  if (itemOnCounter && !itemProcessed && itemOnCounterFor > ITEM_ON_COUNTER_GRACE_MS) abnormal = true;
  if (doorOpen && handDuringOpen && itemOnCounter) abnormal = true;

  // Classification-in-progress flag
  bool classificationWaiting =
    itemOnCounter && itemProcessed && !aiDoneForCurrentItem;

  // Classification-done-but-still-on-counter flag
  bool classificationDoneOnCounter =
    itemOnCounter && itemProcessed && aiDoneForCurrentItem;

  if (!abnormal) {
    bool waitingPickup =
      waitingAfterPickup && counterEmpty &&
      (now - pickupDoorCloseSince <= PICKUP_WAIT_MS);

    bool waitingItemTooLong =
      itemOnCounter && !itemProcessed &&
      (itemOnCounterFor <= ITEM_ON_COUNTER_GRACE_MS);

    if (waitingPickup || waitingItemTooLong || classificationWaiting) {
      waiting = true;  // WAITING dominates while AI still working
    }
  }

  if (!abnormal && !waiting) {
    // If AI is done and item is still there → PROCESSING (blue) until pickup
    if (classificationDoneOnCounter) {
      processing = true;
    } else {
      if (doorOpen) processing = true;
      // we don’t rely on latched button here anymore
    }
  }

  if (abnormal)      systemState = STATE_ABNORMAL;
  else if (waiting)  systemState = STATE_WAITING;
  else if (processing) systemState = STATE_PROCESSING;
  else               systemState = STATE_NORMAL;

  // If item was classified AND now counter is empty → back to normal
  if (!itemOnCounter && itemProcessed && aiDoneForCurrentItem && !abnormal) {
    Serial.println("🟢 Item removed AFTER AI done → NORMAL.");
    itemProcessed        = false;
    aiDoneForCurrentItem = false;
    buttonLatched        = false;
    systemState          = STATE_NORMAL;
  }

  applyState(systemState);

  // --------- Serial debug ---------
  Serial.println("---------------");
  Serial.print("Magnetic (0=open,1=close): ");   Serial.println(magnetic);
  Serial.print("DoorOpen: ");                    Serial.println(doorOpen ? 1 : 0);
  Serial.print("ButtonRawPressed (1=physical now): "); Serial.println(buttonRawPressed);
  Serial.print("ButtonState latched (1=request active): "); Serial.println(buttonState);
  Serial.print("LightVal: ");                    Serial.println(lightVal);
  Serial.print("LightState (1=empty): ");        Serial.println(lightState);
  Serial.print("Distance: ");                    Serial.println(distance);
  Serial.print("handInPath: ");                  Serial.println(handInPath ? 1 : 0);
  Serial.print("doorOpenFor(ms): ");             Serial.println(doorOpenFor);
  Serial.print("itemOnCounter: ");               Serial.println(itemOnCounter ? 1 : 0);
  Serial.print("itemOnCounterFor(ms): ");        Serial.println(itemOnCounterFor);
  Serial.print("itemProcessed (btn pressed): "); Serial.println(itemProcessed ? 1 : 0);
  Serial.print("aiDoneForCurrentItem: ");        Serial.println(aiDoneForCurrentItem ? 1 : 0);
  Serial.print("waitingAfterPickup: ");          Serial.println(waitingAfterPickup ? 1 : 0);
  Serial.print("SystemState (0=N,1=P,2=W,3=A): "); Serial.println((int)systemState);

  // ==============================================================  
  // THINGSPEAK SEND
  // ==============================================================  
  if (now - lastSendMillis >= SEND_INTERVAL) {
    lastSendMillis = now;
    sendToThingSpeak(
      magnetic, buttonState, lightVal, lightState, distance, handDetectLatched
    );
    handDetectLatched = 0;
  }

  delay(200);
}
