# webapp/app.py
import os
import time
from flask import Flask, jsonify, render_template, send_from_directory
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud import storage
from google.auth.transport.requests import Request

# CONFIG - edit these
FIREBASE_CRED_PATH = os.path.join(os.path.dirname(__file__), "..", "embedsystem-ef7e5-firebase-adminsdk-fbsvc-cba8cd679c.json")
GCS_SIGNED_URL_ENABLED = True  # set False if you already have public wav_url in your documents
SIGNED_URL_EXPIRATION_SECONDS = 3600  # 1 hour
GCS_BUCKET_NAME = ""  # only needed for signed URL path method
# END CONFIG

# Flask app
app = Flask(__name__, static_folder="static", template_folder="templates")

import requests

@app.route("/api/images")
def api_images():
    """
    Returns all documents in `images` collection, newest first.
    Includes signed URLs if GCS signing is enabled.
    """
    docs = (
        db.collection("snack_classifications")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .stream()
    )

    out = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id

        img = data.get("image_url")

        # Generate signed URL if gs://
        if GCS_SIGNED_URL_ENABLED and img and img.startswith("gs://"):
            try:
                data["image_signed_url"] = make_signed_url(img)
            except Exception as e:
                print("Signed URL error:", e)
                data["image_signed_url"] = None
        else:
            data["image_signed_url"] = img  # pass-through

        out.append(data)

    return jsonify(out)



THINGSPEAK_CHANNEL_ID = "CHANNEL_ID"
THINGSPEAK_API_KEY = "API_JA"   # OBMIT

@app.route("/api/thingspeak")
def api_thingspeak():
    # Build URL
    if THINGSPEAK_API_KEY:
        url = (f"https://api.thingspeak.com/channels/{THINGSPEAK_CHANNEL_ID}/feeds.json"
               f"?api_key={THINGSPEAK_API_KEY}&results=20")
    else:
        # If channel is public, no API key needed
        url = (f"https://api.thingspeak.com/channels/{THINGSPEAK_CHANNEL_ID}/feeds.json"
               f"?results=20")

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception as json_err:
            print("JSON parse error:", json_err)
            print("Raw response:", resp.text[:300])
            return jsonify({"error": "ThingSpeak JSON error"}), 500

        return jsonify(data)

    except Exception as e:
        print("ThingSpeak fetch error:", e)
        return jsonify({"error": str(e)}), 500



# Initialize Firebase Admin
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Initialize GCS client (for signed urls)
storage_client = storage.Client.from_service_account_json(FIREBASE_CRED_PATH)

def make_signed_url(gcs_url):
    if not gcs_url:
        return None
    # passthrough if already http(s)
    if gcs_url.startswith("http://") or gcs_url.startswith("https://"):
        return gcs_url

    # support gs://bucket/path.wav
    if gcs_url.startswith("gs://"):
        parts = gcs_url[5:].split("/", 1)
        bucket_name = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(path)
        url = blob.generate_signed_url(
            expiration=SIGNED_URL_EXPIRATION_SECONDS,
            version="v4",
            method="GET"
        )
        return url

    # fallback: return as-is
    return gcs_url

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/recordings")
def api_recordings():
    """
    Returns all documents in `recordings` collection, newest first.
    Each doc becomes a dict with id and fields.
    """
    docs = db.collection("recordings").order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
    out = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        # if wav_url is present and is gs://, and signed URLs enabled, convert
        wav = data.get("wav_url")
        if GCS_SIGNED_URL_ENABLED and wav and (wav.startswith("gs://") or wav.startswith("gs:/")):
            try:
                data["wav_signed_url"] = make_signed_url(wav)
            except Exception as e:
                data["wav_signed_url"] = None
        else:
            data["wav_signed_url"] = wav
        out.append(data)
    return jsonify(out)

@app.route("/api/thingspeak_dashboard")
def api_thingspeak_dashboard():
    """
    Returns the latest feed plus history for charts.
    """
    url = f"https://api.thingspeak.com/channels/{THINGSPEAK_CHANNEL_ID}/feeds.json"
    params = {"results": 50}

    if THINGSPEAK_API_KEY:
        params["api_key"] = THINGSPEAK_API_KEY

    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        feeds = data.get("feeds", [])
        latest = feeds[-1] if feeds else {}

        return jsonify({
            "latest": latest,
            "feeds": feeds
        })

    except Exception as e:
        print("Dashboard fetch error:", e)
        return jsonify({"error": str(e)}), 500


# static route for app.js if needed (Flask normally serves static)
@app.route("/static/<path:fn>")
def static_files(fn):
    return send_from_directory(os.path.join(app.root_path, "static"), fn)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
