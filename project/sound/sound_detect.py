import pyaudio
import numpy as np
import time
import torch
import torch.nn as nn
import torchaudio
import os
import wave
import csv
from .cloud_upload import save_to_firebase
from .email_alert import send_alert_email
from .cloud_uploader_gcs import upload_wav_to_gcs


# run with python3 -m sound.sound_detect from project root directory


# =========================
# SETTINGS
# =========================
CHUNK = 2048
RATE = 32000
PEAK_THRESHOLD = 30000
RMS_THRESHOLD = 7200
MIN_GAP = 0.30
RECORD_SECONDS = 2.0
DEVICE = "cpu"

MODEL_PATH = "./sound/cnn14_32k.pth"
LABELS_CSV_PATH = "./sound/class_labels_indices.csv"

print("🎧 Loud sound detector with CNN14 classification\n")

# =========================
# LOAD AUDIOSET LABELS
# =========================
def load_audioset_labels(csv_path):
    labels = {}
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            index = int(row['index'])
            display_name = row['display_name']
            labels[index] = display_name
    return labels

print("Loading AudioSet labels...")
LABELS = load_audioset_labels(LABELS_CSV_PATH)
print(f"✅ Loaded {len(LABELS)} AudioSet labels.\n")

# =========================
# CNN14 ARCHITECTURE
# =========================
class ConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False)
        self.conv2 = nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)

    def forward(self, x, pool_size=(2, 2)):
        x = nn.functional.relu_(self.bn1(self.conv1(x)))
        x = nn.functional.relu_(self.bn2(self.conv2(x)))
        x = nn.functional.avg_pool2d(x, kernel_size=pool_size)
        return x

class CNN14(nn.Module):
    def __init__(self, classes_num=527):
        super().__init__()
        self.conv_block1 = ConvBlock(1, 64)
        self.conv_block2 = ConvBlock(64, 128)
        self.conv_block3 = ConvBlock(128, 256)
        self.conv_block4 = ConvBlock(256, 512)
        self.conv_block5 = ConvBlock(512, 1024)
        self.conv_block6 = ConvBlock(1024, 2048)
        self.fc1 = nn.Linear(2048, 2048)
        self.fc_audioset = nn.Linear(2048, classes_num)

    def forward(self, x):
        x = self.conv_block1(x)
        x = self.conv_block2(x)
        x = self.conv_block3(x)
        x = self.conv_block4(x)
        x = self.conv_block5(x)
        x = self.conv_block6(x)
        x = torch.mean(x, dim=3)
        x1, _ = torch.max(x, dim=2)
        x2 = torch.mean(x, dim=2)
        x = x1 + x2
        x = nn.functional.relu_(self.fc1(x))
        x = torch.sigmoid(self.fc_audioset(x))
        return x

# =========================
# LOAD MODEL
# =========================
print("Loading CNN14 model...")
cnn14 = CNN14()
torch.serialization.add_safe_globals([np._core.multiarray._reconstruct])
checkpoint = torch.load(MODEL_PATH, map_location=DEVICE, weights_only=False)
state_dict = checkpoint["model"] if isinstance(checkpoint, dict) and "model" in checkpoint else checkpoint
cnn14.load_state_dict(state_dict, strict=False)
cnn14.to(DEVICE)
cnn14.eval()
print("✅ CNN14 model loaded.\n")

# =========================
# AUDIO HELPERS
# =========================
def record_audio(duration, stream):
    frames = []
    num_chunks = int(RATE / CHUNK * duration)
    for _ in range(num_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(np.frombuffer(data, dtype=np.int16))
    return np.concatenate(frames)

def save_recording(audio_np, timestamp):
    os.makedirs("./sound/recordings", exist_ok=True)
    filename = f"./sound/recordings/rec_{timestamp}.wav"
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(audio_np.tobytes())
    print(f"[SAVED WAV] {filename}")
    return filename

def save_labels(timestamp, top_labels, top_probs):
    os.makedirs("./sound/recording_data", exist_ok=True)
    filename = f"./sound/recording_data/rec_{timestamp}.txt"
    with open(filename, 'w') as f:
        for label, prob in zip(top_labels, top_probs):
            f.write(f"{label}: {prob:.3f}\n")
    print(f"[SAVED LABELS] {filename}")
    return filename

def preprocess_waveform(waveform):
    waveform = torch.tensor(waveform.astype(np.float32)/32768.0)
    if len(waveform.shape) == 1:
        waveform = waveform.unsqueeze(0)
    mel = torchaudio.transforms.MelSpectrogram(
        sample_rate=RATE, n_fft=1024, hop_length=320, n_mels=64
    )(waveform)
    mel = mel.unsqueeze(1)
    return mel

def classify_audio(waveform_np, top_k=5):
    mel = preprocess_waveform(waveform_np).to(DEVICE)
    with torch.no_grad():
        output = cnn14(mel).squeeze(0)
    # Get top K
    top_probs, top_idx = torch.topk(output, top_k)
    top_labels = [LABELS.get(idx.item(), f"Class {idx.item()}") for idx in top_idx]
    top_probs = top_probs.tolist()
    return top_labels, top_probs

# =========================
# AUDIO STREAM SETUP
# =========================
p = pyaudio.PyAudio()
stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                input=True, frames_per_buffer=CHUNK)

last_trigger = 0
print("🎧 Listening for loud sounds...\n")

# =========================
# MAIN LOOP
# =========================
try:
    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        samples = np.frombuffer(data, dtype=np.int16)
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        peak = np.max(np.abs(samples))
        now = time.time()

        if (peak > PEAK_THRESHOLD or rms > RMS_THRESHOLD) and (now - last_trigger) > MIN_GAP:
            last_trigger = now
            timestamp = int(time.time())
            print(f"\n🔊 Loud sound detected! Peak={peak}, RMS={int(rms)}")
            print("Recording...")
            audio_np = record_audio(RECORD_SECONDS, stream)
            wav_path = save_recording(audio_np, timestamp)

            print("Classifying...")
            top_labels, top_probs = classify_audio(audio_np, top_k=5)

            # Console output: top 3
            print("➡ Top 3 predictions:")
            for label, prob in zip(top_labels[:3], top_probs[:3]):
                print(f"   {label}: {prob:.3f}")

            # Save locally (optional)
            save_labels(timestamp, top_labels[:3], top_probs[:3])

            # --- NEW: Use WAV URL instead of uploading ---
            # Example: If you have a server hosting WAVs:
            # wav_url = f"https://your-server.com/rec_{timestamp}.wav"
            # wav_url = f"https://storage.googleapis.com/iot-audio-recordings/rec_{timestamp}.wav"
            blob_name = f"rec_{timestamp}.wav"
            wav_url = upload_wav_to_gcs(wav_path, blob_name)



            record_data = {
                "timestamp": timestamp,
                "labels": top_labels[:3],
                "probs": top_probs[:3],
                "wav_url": wav_url
            }

            save_to_firebase(record_data)
            send_alert_email(timestamp, top_labels[:3], top_probs[:3], wav_url)



except KeyboardInterrupt:
    print("\nStopping...")

finally:
    stream.stop_stream()
    stream.close()
    p.terminate()
