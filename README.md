# ABAQUS Watcher GUI

A modern, cross-platform Desktop Application to monitor SIMULIA ABAQUS jobs remotely via Telegram. This tool watches your ABAQUS working directory for lock files (`.lck`) and status files (`.sta`), providing real-time updates, convergence plots, and remote termination capabilities.

**Author:** Souvik Biswas
**License:** MIT

---

## Features

- **Smart Notifications:** To prevent spamming your phone, **all routine updates (Heartbeats, Status checks) are sent silently.** You will only receive a sound/vibration notification for critical events: **Job Completion** or **Job Abort**.
- **Real-time Monitoring:** Automatically detects when jobs start, finish, or error out.
- **Convergence Plotting:** Generates and sends a graph (Step Time vs. Increment Size) via Telegram to visualize stability.
- **Remote Control:** Check status or kill jobs remotely using Telegram commands.
- **Secure Storage:** Credentials (Bot Token, Chat ID) are encrypted and stored in the **Windows Credential Locker**, not in plain-text files.
- **System Tray Mode:** The app minimizes to the system tray, running unobtrusively in the background.

---

## Installation & Prerequisites

This application is written in Python and runs on **Windows, Linux, and macOS**.

### Prerequisites
* **Python 3.10** or higher.
* **Abaqus** installed on the host machine (must be runnable via terminal).

### Installation Steps
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/daadaan/ABAQUS_Watcher_GUI.git
    cd ABAQUS_Watcher_GUI
    ```
2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Run the application:**
    ```bash
    python abaqus_watcher_gui.py
    ```

### Bonus for Windows Users
For Windows users who prefer not to manage Python environments, a pre-compiled `.exe` is available.
1.  Go to the [**Releases Page**](https://github.com/daadaan/ABAQUS_Watcher_GUI/releases).
2.  Download `ABAQUSWatcherGUI_vX.X.X.exe`.
3.  Double-click to run. No installation required.

#### ⚠️ SmartScreen Warning

When you first run the `.exe`, **Microsoft SmartScreen** may display a warning like:

> **Windows protected your PC**  
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.  
> Publisher: Unknown

**Why does this happen?**  
The executable is not code-signed with a paid certificate (which costs hundreds of dollars per year). This is common for open-source and personal projects.

**Is it safe?**  
✅ **Yes.** The `.exe` is built automatically by [GitHub Actions](https://github.com/daadaan/ABAQUS_Watcher_GUI/actions) directly from the Python source code in this repository. You can verify this yourself:
- View the [build workflow](.github/workflows/) to see exactly how the executable is generated.
- Inspect the [source code](abaqus_watcher_gui.py) – it's fully open and transparent.

**To run anyway:**
1. Click **"More info"** on the SmartScreen popup.
2. Click **"Run anyway"**.

---

## Configuration Guide

The app features a GUI tab to handle setup easily. You do not need to edit config files manually.

### 1. Setting up the Telegram Bot
You need two things: a **Bot Token** and your **Chat ID**.

**Step A: Get Bot Token**
1.  Open Telegram and search for **[@BotFather](https://t.me/BotFather)**.
2.  Send the command `/newbot`.
3.  Follow the instructions to name your bot.
4.  BotFather will give you a long string (e.g., `123456:ABC-DEF1234...`). **Copy this Token.**

**Step B: Get Chat ID**
1.  Search for **[@userinfobot](https://t.me/userinfobot)** on Telegram.
2.  Click "Start" or send any message.
3.  It will reply with your "Id" (a number like `123456789`). **Copy this ID.**

### 2. Configure the App
1.  Open **ABAQUS Watcher GUI**.
2.  Go to the **Config** tab.
3.  Paste your **Bot Token** and **Chat ID**.
4.  **ABAQUS Temp Directory:** Browse to the folder where you run your jobs (this is where `.lck` and `.sta` files appear).
5.  **Heartbeat:** Set how often (in seconds) the bot should send a silent "Still Running" message for long jobs (Default: 3600s).
6.  Click **Save Settings**.

---

## Update Workflow

The application includes a built-in update checker in the **Config** tab.

* **If running as Script:** The app uses a "Deployment Aware" update system. If a new version is found, it will offer to **automatically download** the new code from GitHub, overwrite the current script, and restart itself.
* **If running as EXE:** The app will alert you and open the GitHub Releases page so you can download the latest executable.

---

## Telegram Commands

There is a **Help** tab inside the app for quick reference.

| Command | Usage | Description |
| :--- | :--- | :--- |
| **Status (All)** | `/status all` | Scans the directory and lists **ALL** jobs (Running, Completed, and Aborted). Includes Start/End times and final status. |
| **Status (Job)** | `/status Job-1` | Generates a **Convergence Plot** 📉 and sends detailed stats (Step, Time, KE, Total Energy) for that specific job. |
| **Kill** | `/kill Job-1` | **Immediately terminates** the specified job on the workstation using the `abaqus terminate` command. |

---

## License

This project is licensed under the **MIT License**.
Copyright (c) 2026, Souvik Biswas (daadaan).