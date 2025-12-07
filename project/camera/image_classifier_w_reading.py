import base64
import requests
import sys
import os
import json
import time

# --- ThingSpeak Config ---
THINGSPEAK_CHANNEL_ID = "" #OBMITTED
THINGSPEAK_API_KEY = ""   # OBMITTED

THINGSPEAK_READ_URL = (
    f"https://api.thingspeak.com/channels/{THINGSPEAK_CHANNEL_ID}/fields/2.json"
    f"?api_key={THINGSPEAK_API_KEY}&results=1"
)

def read_button_state():
    """
    Returns the latest value of ButtonState (field2) from ThingSpeak.
    If error, returns 0 (safe default).
    """
    try:
        r = requests.get(THINGSPEAK_READ_URL, timeout=10)
        if r.status_code != 200:
            return 0

        data = r.json()
        feeds = data.get("feeds", [])
        if not feeds:
            return 0

        value = feeds[0].get("field2", "0")
        return int(value) if value and value.isdigit() else 0

    except Exception as e:
        print("ThingSpeak read error:", e)
        return 0



import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud import storage

BUCKET_NAME = ""

# Use same Firebase/Google service account JSON
client = storage.Client.from_service_account_json(
    ""
)

bucket = client.bucket(BUCKET_NAME)

def upload_image_to_gcs(local_path, blob_name):
    """
    Uploads an image to Google Cloud Storage and makes it public.
    local_path: path to JPG/PNG on disk
    blob_name: where to store inside the bucket (e.g. images/12345.jpg)
    """

    blob = bucket.blob(blob_name)

    try:
        blob.upload_from_filename(local_path)
        blob.make_public()

        print(f"📸 Uploaded image to GCS: gs://{BUCKET_NAME}/{blob_name}")
        print(f"🌐 Public Image URL: {blob.public_url}")

        return blob.public_url

    except Exception as e:
        print(f"❌ GCS image upload failed: {e}")
        return None




try:
    import cv2
except ImportError:
    print("Warning: 'opencv-python' is not installed. Webcam capture functionality is disabled.")
    print("To enable capture, install it: pip install opencv-python")
    cv2 = None  

# --- Configuration ---
API_URL_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/'
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'

# --- Your classification labels ---
CLASSIFICATION_LABELS = ['tomato_crackers', 'bento', 'atori']

DEFAULT_PROMPT = (
    f"Analyze the product or object in the image. Which of the following labels best applies: "
    f"{', '.join(CLASSIFICATION_LABELS)}? Provide only the selected label (e.g., 'bento') or 'NONE' "
    f"if no label fits."
)

SYSTEM_INSTRUCTION = (
    "You are a specialized image classification model. "
    "Your response must be concise and strictly adhere to the user's instructions."
)

# --- Reference Image Directory ---
REF_DIR = "ref"

# --- Firebase / Firestore config ---
FIREBASE_CRED_PATH = ""
SNACK_COLLECTION = "snack_classifications"

# Initialize Firebase Admin SDK (only once)
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()  # Firestore client


def save_snack_log(label: str, raw_text: str, image_url: str):
    ts = int(time.time())

    record_data = {
        "timestamp": ts,
        "label": label,
        "raw_text": raw_text,
        "image_url": image_url,  # changed
    }

    collection = db.collection(SNACK_COLLECTION)
    doc_ref = collection.document(str(ts))
    doc_ref.set(record_data)

    print(f"🔥 Saved snack log to Firestore with image URL: {image_url}")



# ---------------------------------------------------------
# LOAD LOCAL REFERENCE IMAGES
# ---------------------------------------------------------
def load_reference_images():
    """
    Loads reference images from:

        ref/<label>/*.jpg|jpeg|png|webp

    Returns:
        {
            "label1": [ { "mimeType": "..", "data": "<b64>" }, ... ],
            "label2": [ ... ]
        }
    """
    references = {}

    if not os.path.isdir(REF_DIR):
        print(f"Warning: Reference directory '{REF_DIR}' not found. No reference images loaded.")
        return references

    for label in CLASSIFICATION_LABELS:
        label_dir = os.path.join(REF_DIR, label)
        refs = []

        if os.path.isdir(label_dir):
            for fname in os.listdir(label_dir):
                fpath = os.path.join(label_dir, fname)

                if not os.path.isfile(fpath):
                    continue

                ext = os.path.splitext(fname)[1].lower()
                mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp"
                }.get(ext)

                if mime is None:
                    print(f"Skipping unsupported file format: {fpath}")
                    continue

                try:
                    with open(fpath, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    refs.append({"mimeType": mime, "data": b64})
                except Exception as e:
                    print(f"Error loading ref image '{fpath}': {e}")

        if refs:
            references[label] = refs
            print(f"Loaded {len(refs)} reference images for label '{label}'.")

    return references


REFERENCE_IMAGES = load_reference_images()


# ---------------------------------------------------------
# WEBCAM CAPTURE (auto_capture option)
# ---------------------------------------------------------
def capture_image_from_webcam(filename="temp_capture.jpg", auto_capture=False):
    if cv2 is None:
        print("Error: OpenCV is required for webcam capture. Please run: pip install opencv-python")
        sys.exit(1)

    print("\n--- Starting Webcam Capture ---")
    camera_index = -1
    MAX_CAMERA_ATTEMPTS = 3
    cap = None

    for i in range(MAX_CAMERA_ATTEMPTS):
        print(f"Trying camera index {i}...")
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            camera_index = i
            break
        if cap:
            cap.release()

    if camera_index == -1:
        print("Error: Unable to open webcam.")
        sys.exit(1)

    if auto_capture:
        # Warm up camera
        WARMUP_FRAMES = 10
        for _ in range(WARMUP_FRAMES):
            ret, frame = cap.read()
            if not ret:
                print("Failed to read frame during warm-up.")
                cap.release()
                return None
            # Optional: tiny sleep to allow exposure adjustment
            time.sleep(0.05)

        # Capture the actual frame
        ret, frame = cap.read()
        if not ret:
            print("Failed to capture frame automatically.")
            cap.release()
            return None

        cv2.imwrite(filename, frame)
        print(f"✅ Auto-captured and saved image to {filename}")
        cap.release()
        cv2.destroyAllWindows()
        return filename


    # --- Manual capture mode ---
    print("Press SPACE to capture, ESC to cancel.")
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to capture frame.")
            break

        cv2.imshow("Capture (SPACE to take photo)", frame)
        key = cv2.waitKey(1)

        if key % 256 == 32:  # SPACE
            cv2.imwrite(filename, frame)
            print(f"Saved capture to {filename}")
            break
        elif key % 256 == 27:  # ESC
            print("Cancelled.")
            cap.release()
            cv2.destroyAllWindows()
            sys.exit(0)

    cap.release()
    cv2.destroyAllWindows()
    return filename


# ---------------------------------------------------------
# FILE TO BASE64
# ---------------------------------------------------------
def file_to_base64(file_path):
    try:
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except FileNotFoundError:
        print(f"Input image not found: {file_path}")
        sys.exit(1)


# ---------------------------------------------------------
# CLASSIFICATION FUNCTION
# ---------------------------------------------------------
def classify_image(api_key, image_path, custom_prompt=None):
    print(f"\n--- Starting Classification for: {os.path.basename(image_path)} ---")

    base64_data = file_to_base64(image_path)
    ext = os.path.splitext(image_path)[1].lower()

    mime_type = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png', '.webp': 'image/webp'
    }.get(ext, 'image/jpeg')

    user_query = custom_prompt if custom_prompt else DEFAULT_PROMPT

    # ----------------------------
    # Build message parts
    # ----------------------------
    parts = [
        {"text": user_query},
        {
            "inlineData": {
                "mimeType": mime_type,
                "data": base64_data
            }
        }
    ]

    # ----------------------------
    # Embed Local Reference Images
    # ----------------------------
    if REFERENCE_IMAGES:
        for label, imgs in REFERENCE_IMAGES.items():
            parts.append({"text": f"Reference images for '{label}':"})
            for ref in imgs:
                parts.append({
                    "inlineData": {
                        "mimeType": ref["mimeType"],
                        "data": ref["data"]
                    }
                })

    # ----------------------------
    # Construct payload
    # ----------------------------
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": parts
            }
        ],
        "systemInstruction": {
            "parts": [{"text": SYSTEM_INSTRUCTION}]
        },
        "tools": [{"google_search": {}}],
    }

    # ----------------------------
    # API CALL w/ exponential backoff
    # ----------------------------
    api_url = f"{API_URL_BASE}{MODEL_NAME}:generateContent?key={api_key}"
    MAX_RETRIES = 5
    delay = 1.0

    for i in range(MAX_RETRIES):
        try:
            print(f"Request attempt {i+1}/{MAX_RETRIES}...")
            response = requests.post(
                api_url,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(payload),
                timeout=30
            )

            if response.status_code == 200:
                print("API responded successfully.")
                return response.json()

            if response.status_code in (429,) or response.status_code >= 500:
                if i < MAX_RETRIES - 1:
                    print(f"Server busy ({response.status_code}). Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    delay *= 2
                    continue

            response.raise_for_status()

        except requests.exceptions.RequestException as e:
            if i < MAX_RETRIES - 1:
                print(f"Network/API error: {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"Fatal error after {MAX_RETRIES} attempts: {e}")
                sys.exit(1)

    return None


# ---------------------------------------------------------
# PARSE & PRINT RESULTS + SAVE LOG
# ---------------------------------------------------------
def process_result(result, image_path, save_log=True):
    if not result:
        print("\nFailed: No result returned.")
        return None

    candidate = result.get('candidates', [{}])[0]
    parts = candidate.get('content', {}).get('parts', [])

    # Concatenate all returned text parts
    text = ""
    for p in parts:
        if "text" in p:
            text += p["text"]

    text = text.strip()

    print("\n--- AI Classification Result ---")
    print("Classification:", text)

    label = text  # The model outputs only the label or "NONE"

    # Save to Firestore + Upload
    if save_log:
        # Create timestamped filename
        timestamp = int(time.time())
        blob_name = f"img_{timestamp}.jpg"

        # Upload to GCS
        gcs_url = upload_to_gcs(image_path, blob_name)

        # Save Firestore entry
        save_snack_log(label, text, gcs_url)

    # Optional grounding print
    grounding = candidate.get('groundingMetadata', {})
    attrs = grounding.get('groundingAttributions', [])
    if attrs:
        print("\n--- Grounding Sources ---")
        for a in attrs:
            web = a.get('web', {})
            if web.get('uri'):
                print(f"- {web.get('title')} ({web.get('uri')})")

    print("--------------------------------")
    return label

def upload_to_gcs(local_path, blob_name):
    blob = bucket.blob(blob_name)

    # upload and make public
    blob.upload_from_filename(local_path)
    blob.make_public()

    print(f"☁️ Uploaded to GCS: gs://{BUCKET_NAME}/{blob_name}")
    print(f"🌐 Public URL: {blob.public_url}")

    return blob.public_url




# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python image_classifier.py <API_KEY>")
        sys.exit(1)

    api_key = sys.argv[1]

    print("🔄 Starting continuous monitoring loop...")
    print("Polling ThingSpeak for ButtonState...")

    while True:
        button = read_button_state()

        if button == 1:
            print("\n🟢 ButtonState = 1 → Capturing + Classifying\n")

            filename = "temp_capture.jpg"
            capture_image_from_webcam(filename, auto_capture=True)

            result = classify_image(api_key, filename, custom_prompt=None)
            process_result(result, filename, save_log=True)

            print("🧹 Reset ButtonState on device-side or wait before next trigger.\n")
            time.sleep(10)

        # Sleep between polls
        time.sleep(10)
