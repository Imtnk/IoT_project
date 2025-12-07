import smtplib
from email.mime.text import MIMEText
import os
file_path = './sound/passwords.txt'
with open(file_path, 'r') as file:
    lines = file.readlines()
    SENDER_EMAIL = lines[0].strip()
    SENDER_PASSWORD = lines[1].strip()
    
# --- CONFIG ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
RECIPIENT_EMAIL = ""

PROJECT_ID = ""
COLLECTION = "recordings"


def generate_firestore_link(timestamp):
    return (
        f"https://console.firebase.google.com/project/{PROJECT_ID}/firestore/"
        f"data/~2F{COLLECTION}~2F{timestamp}"
    )


def send_alert_email(timestamp, labels, probs, wav_url):
    doc_link = generate_firestore_link(timestamp)

    # Construct email body
    msg_body = f"""
🔥 Loud Noise Detected!

Timestamp: {timestamp}

Top Predictions:
1. {labels[0]} ({probs[0]:.3f})
2. {labels[1]} ({probs[1]:.3f})
3. {labels[2]} ({probs[2]:.3f})

WAV File: {wav_url}

Firestore Record:
{doc_link}

----------------------------------------
Automatic alert from your Sound Monitor
"""

    msg = MIMEText(msg_body)
    msg["Subject"] = f"🚨 Loud Sound Alert ({labels[0]})"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("📧 Alert email sent successfully.")

    except Exception as e:
        print("❌ Email failed:", e)
