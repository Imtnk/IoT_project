// Field config (ThingSpeak: field1..6)
const FIELD_CONFIG = [
  { key: "field1", label: "Magnetic",    unit: "",  type: "binary" },
  { key: "field2", label: "ButtonState",   unit: "",  type: "binary" },
  { key: "field3", label: "lightVal",    unit: "",  type: "analog" },   // will /100 on graph
  { key: "field4", label: "lightState",  unit: "",  type: "binary" },
  { key: "field5", label: "Distance",    unit: "cm",type: "analog" },
  { key: "field6", label: "DetectState", unit: "",  type: "binary" },
];

let tsBinaryChart = null;
let tsAnalogChart = null;

// Wait for Flask + ThingSpeak to be ready
async function waitForApiReady() {
  while (true) {
    try {
      const res = await fetch("/api/thingspeak_dashboard", { method: "GET" });
      if (res.ok) {
        console.log("API ready");
        return;
      }
    } catch (err) {
      console.log("API not ready yet… retrying…");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function startup() {
  await waitForApiReady();
  showApp();

  loadRecords();
  loadImages();                  // <-- NEW
  loadThingSpeakDashboard();

  setInterval(loadThingSpeakDashboard, 15000);
  setInterval(loadRecords, 15000);
  setInterval(loadImages, 15000); // <-- NEW
}


async function loadImages() {
  const res = await fetch("/api/images");
  const data = await res.json();

  const div = document.getElementById("image-gallery");
  div.innerHTML = "";

  // Filter out images with label "none"
  let filtered = data.filter(img =>
    img.label && img.label.toLowerCase() !== "none"
  );

  // Sort by timestamp newest → oldest
  filtered = filtered.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

  // Limit to 5 newest
  filtered = filtered.slice(0, 5);

  if (!filtered.length) {
    div.innerHTML = "<p class='muted'>No labeled images found.</p>";
    return;
  }

  filtered.forEach((img) => {
    const ts = new Date((img.timestamp || 0) * 1000).toLocaleString();
    const url = img.image_signed_url || img.image_url || "";

    const card = document.createElement("div");
    card.className = "img-card";
    card.innerHTML = `
      <div class="img-card-inner">
        <img src="${url}" class="img-preview" alt="Image"/>
        <div class="img-meta">
          <div>${ts}</div>
          <div class="muted">${img.label}</div>
        </div>
      </div>
    `;
    div.appendChild(card);
  });
}




// ----------------- Firestore recordings table -----------------
async function loadRecords() {
  const res = await fetch("/api/recordings");
  const data = await res.json();
  const tbody = document.querySelector("#records tbody");
  tbody.innerHTML = "";

  data.forEach((rec) => {
    const tr = document.createElement("tr");

    const ts = new Date((rec.timestamp || 0) * 1000).toLocaleString();
    const labels = (rec.labels || []).slice(0, 3).join(", ");
    const probs = (rec.probs || [])
      .slice(0, 3)
      .map((p) => p.toFixed(3))
      .join(", ");
    const audioUrl = rec.wav_signed_url || rec.wav_url || "";

    tr.innerHTML = `
      <td>${ts}<br><span class="muted">${rec.id}</span></td>
      <td class="labels">${labels}</td>
      <td>${probs}</td>
      <td>${
        audioUrl
          ? `<audio controls src="${audioUrl}"></audio>`
          : '<span class="muted">No audio</span>'
      }</td>
      <td><a target="_blank" href="https://console.firebase.google.com/project/embedsystem-ef7e5/firestore/databases/-default-/data/~2Frecordings~2F${
        rec.id
      }">Open</a></td>
    `;
    tbody.appendChild(tr);
  });
}

// ----------------- ThingSpeak dashboard -----------------
async function loadThingSpeakDashboard() {
  const res = await fetch("/api/thingspeak_dashboard");
  const data = await res.json();

  if (!data.feeds || data.feeds.length === 0) {
    document.getElementById("ts-cards").innerHTML = "No data available.";
    return;
  }

  const feeds = data.feeds;
  const latest = data.latest;

  // ---- Latest value cards ----
  const cardsDiv = document.getElementById("ts-cards");
  cardsDiv.innerHTML = "";

  FIELD_CONFIG.forEach((cfg) => {
    let val = latest[cfg.key];
    if (val === null || val === undefined || val === "") {
      val = "-";
    }

    const card = document.createElement("div");
    card.className = "ts-card";
    card.innerHTML = `
      <div class="ts-card-title">${cfg.label}</div>
      <div class="ts-card-value">${val}</div>
      <div class="ts-card-unit">${cfg.unit}</div>
    `;
    cardsDiv.appendChild(card);
  });

  // ---- Build datasets ----
  const labels = feeds.map((f) => f.created_at);
  const binaryDatasets = [];
  const analogDatasets = [];

  FIELD_CONFIG.forEach((cfg) => {
    const values = feeds.map((f) => {
      const raw = f[cfg.key];
      const num = Number(raw);
      if (isNaN(num)) return null;

      // scale lightVal on the graph only
      if (cfg.type === "analog" && cfg.key === "field3") {
        return num / 100.0;
      }
      return num;
    });

    const dataset = {
      label: cfg.label,
      data: values,
      borderWidth: 2,
      spanGaps: true,
      // no explicit color → Chart.js chooses
    };

    if (cfg.type === "binary") {
      binaryDatasets.push(dataset);
    } else {
      analogDatasets.push(dataset);
    }
  });

  const binaryCtx = document
    .getElementById("ts-binary-chart")
    .getContext("2d");
  const analogCtx = document
    .getElementById("ts-analog-chart")
    .getContext("2d");

  // Destroy previous charts to avoid stacking
  if (tsBinaryChart) tsBinaryChart.destroy();
  if (tsAnalogChart) tsAnalogChart.destroy();

  // Digital 0/1 chart
  tsBinaryChart = new Chart(binaryCtx, {
    type: "line",
    data: {
      labels,
      datasets: binaryDatasets,
    },
    options: {
      responsive: true,
      interaction: {
        mode: "nearest",
        intersect: false,
      },
      scales: {
        x: { display: false },
        y: {
          suggestedMin: -0.1,
          suggestedMax: 1.1,
          ticks: { stepSize: 1 },
        },
      },
    },
  });

  // Analog chart (lightVal/100 and Distance)
  tsAnalogChart = new Chart(analogCtx, {
    type: "line",
    data: {
      labels,
      datasets: analogDatasets,
    },
    options: {
      responsive: true,
      interaction: {
        mode: "nearest",
        intersect: false,
      },
      scales: {
        x: { display: false },
        y: {
          beginAtZero: false,
        },
      },
    },
  });
}

startup();
