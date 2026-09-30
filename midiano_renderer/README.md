# Midiano Batch Renderer — GitHub Codespaces Cloud Studio & Desktop Client

A high-performance, production-grade desktop application and 4-core cloud parallel rendering pipeline for batch-rendering falling-notes piano videos from MIDI files using [Midiano](https://app.midiano.com).

Derived from and directly mapping all enhancements from the Jupyter notebook `midiano_batch_renderer_gpu_nvenc_fixed (3).ipynb`.

---

## 🌟 Key Architecture & Highlights

- **100% 4-Core CPU & 8GB RAM Cloud Saturation**: Runs in GitHub Codespaces across 4 parallel virtual Xvfb displays (`:99`, `:100`, `:101`, `:102`) with concurrent Playwright Chromium instances and multi-threaded FFmpeg encoding pipelines. Your local PC uses **0% rendering CPU and 0% RAM**.
- **Automated Local-to-Cloud Sync**:
  1. Pick your local MIDI folder and local MP4 output folder on your PC.
  2. The desktop app streams the MIDIs to GitHub Codespaces.
  3. The 4 cloud workers render files simultaneously.
  4. As each video finishes, it automatically streams down into your local MP4 folder.
- **Classic, Clean Old-School Light Desktop GUI**: No dark neon, AI-template gradients, or bloatware. Clean, crisp white-and-gray desktop styling with high-contrast typography, classic borders, and standard engineering UI elements.
- **Live Hardware Saturation Dashboard**:
  - Live total CPU % gauge.
  - Per-core saturation bars (`Core 1`, `Core 2`, `Core 3`, `Core 4`).
  - Cloud RAM meter (`e.g. 5.4 / 8.0 GB - 68%`).
  - Active parallel worker indicator (`4 / 4 Active`).
- **Detailed Multi-Column Queue Table**:
  - Displays every MIDI file in the batch with filename, duration, assigned worker, cloud status (`Pending`, `Uploaded`, `Rendering`, `Retiming`, `Downloading`, `Ready`), and percentage.
- **Precision 1× Retiming Pipeline**: Uses FFmpeg's `setpts` filter and duration probing to mathematically retime browser-recorded video to the exact MIDI length, guaranteeing perfect audio synchronization with external DAW/WAV recordings.
- **Zero Window Jitter / Geometry Fix**: Reuses a single persistent Chromium browser context and window geometry across the entire batch to eliminate window position bugs and display clipping.
- **Graceful Moov Finalization**: Sends `'q'` over stdin to FFmpeg so the MP4 container structure (`moov` atom) is properly written without corruption.

---

## 🏗️ Architecture & File Structure

```
midiano_renderer/
├── main.py                     # Entry point (GUI, Server, CLI, Diagnostics)
├── cloud_server.py             # FastAPI 4-core multi-worker Cloud Agent (runs in Codespaces)
├── start_codespace.sh          # One-click Codespaces launcher script
├── config.py                   # Persistent settings & JSON serializer (Notebook Cell 2)
├── gui/
│   ├── app.py                  # Classic light desktop window & cloud synchronization
│   ├── widgets.py              # Cloud bridge, 4-core CPU/RAM monitor, queue table, log console
│   └── styles.py               # Classic light theme palette, typography, status colors
├── core/
│   ├── cloud_client.py         # HTTP REST + WebSocket bridge connecting PC to Codespaces
│   ├── renderer.py             # Batch engine & Playwright automation (Notebook Cell 3 & 4)
│   ├── ffmpeg_utils.py         # FFmpeg capture, NVENC detection, retiming pass (Notebook Cell 1 & 3)
│   ├── midi_utils.py           # Duration probing & track inspection via mido (Notebook Cell 1 & 3)
│   └── xvfb.py                 # Virtual display server lifecycle (Notebook Cell 3)
├── utils/
│   └── logging.py              # Dual UI/file rotating logger with second timestamps
├── .devcontainer/              # Ready-to-deploy GitHub Codespaces environment
│   ├── devcontainer.json
│   └── setup.sh
├── requirements.txt
└── pyproject.toml
```

---

## 🚀 How Your PC Communicates with GitHub Codespaces

```
┌──────────────────────────────────────────────┐
│  LOCAL PC (Desktop GUI)                      │
│  - Select MIDI Folder & MP4 Output Folder    │
│  - Queue Table with live status              │
│  - Live 4-Core CPU % & RAM Monitor           │
└───────────────┬──────────────────────────────┘
                │ HTTP REST (Upload MIDIs & Download MP4s)
                │ WebSocket (Live Telemetry & Logs)
                ▼ (Port 8000 Forwarded / Public)
┌──────────────────────────────────────────────┐
│  GITHUB CODESPACES (Linux Cloud Instance)     │
│  4 vCPUs • 8 GB RAM • 100% Saturation        │
│                                              │
│  [Worker 0] -> Xvfb :99  -> Midiano -> FFmpeg│
│  [Worker 1] -> Xvfb :100 -> Midiano -> FFmpeg│
│  [Worker 2] -> Xvfb :101 -> Midiano -> FFmpeg│
│  [Worker 3] -> Xvfb :102 -> Midiano -> FFmpeg│
└──────────────────────────────────────────────┘
```

---

## 📖 Step-by-Step Setup Guide

### Step 1: Start GitHub Codespaces (Takes 30 seconds)
1. Push this folder to your GitHub repository (or create a new repository and push this code).
2. On GitHub, click **Code** → **Codespaces** → **Create codespace on main**.
3. Once the Codespace terminal opens, run:
   ```bash
   bash start_codespace.sh
   ```
4. In the bottom VS Code panel, click the **PORTS** tab:
   - Find Port `8000`.
   - Right-click Port 8000 → **Port Visibility** → select **Public**.
   - Copy the Forwarded Address (e.g. `https://<codespace-name>-8000.app.github.dev` or `http://localhost:8000` if using local VS Code).

---

### Step 2: Launch the Classic Desktop App on Your PC
On your local computer:

```bash
cd midiano_renderer
python main.py
```

1. In the **Codespace URL** bar at the top, paste your forwarded address (e.g. `https://<codespace-name>-8000.app.github.dev` or `http://localhost:8000`).
2. Click **Test & Connect**. The status indicator will turn green:
   `● ONLINE — 4 Cores | 8.0GB RAM`.
3. Select your **Local MIDI Input Folder**. All files immediately populate the queue table.
4. Select your **Local MP4 Output Folder**.
5. Select **4 Workers (100% 4-Core Saturation)**.
6. Click **START CLOUD BATCH (CODESPACES)**.

---

### What Happens During the Render:
- The desktop app uploads the MIDIs to Codespaces.
- The 4 cloud workers start on virtual displays `:99`, `:100`, `:101`, `:102`.
- You will see the **4-Core CPU Meter jump to 90–100%** and RAM climb to 4–6 GB in the live dashboard on your desktop.
- The queue table tracks which worker is rendering which song.
- As each video finishes and retimes in the cloud, it is **automatically downloaded directly into your chosen local MP4 output folder**.
- When finished, click **Open Out Folder** or **Play Latest Video**.

---

## 🛠️ Diagnostics & Local Fallback

If you ever wish to check local machine dependencies or download portable FFmpeg:

```bash
# Check local environment:
python main.py --check-deps

# Download portable static FFmpeg with NVENC:
python main.py --download-ffmpeg

# Launch cloud server directly:
python main.py --server --port 8000
```
