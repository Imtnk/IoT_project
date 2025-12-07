import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase Admin SDK
cred = credentials.Certificate("")  # Firebase service account JSON
firebase_admin.initialize_app(cred)

db = firestore.client()  # Firestore client

def save_to_firebase(record_data):
    """
    Save metadata to Firebase Firestore.
    record_data example:
    {
        "timestamp": 1234567890,
        "labels": ["Dog bark", "Siren"],
        "probs": [0.95, 0.80],
        "wav_url": "https://example.com/rec_1234567890.wav"
    }
    """
    collection = db.collection("recordings")
    doc_ref = collection.document(str(record_data["timestamp"]))
    doc_ref.set(record_data)
    print(f"✅ Saved record to Firebase: {record_data['timestamp']}")
