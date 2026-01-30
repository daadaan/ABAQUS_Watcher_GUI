# ABAQUS Watcher GUI

Monitor SIMULIA ABAQUS jobs remotely via Telegram.

A cross-platform Python Desktop GUI that watches your ABAQUS working directory for lock files (`.lck`) and status files (`.sta`), then reports job status, convergence and time estimates through a Telegram bot.

**Author:** Souvik Biswas (@daadaan)  \
**License:** Apache 2.0

---

## Key Features

- Real-time job monitoring via `.lck` (lock) and `.sta` (status) files
- Smart notifications: routine updates sent silently; only job completion/abort are noisy
- Time estimation for running jobs (based on ODB frame output; shown in app + Telegram)
- Convergence plotting: Step Time vs. Increment Size graph sent via Telegram
- Remote control via Telegram commands (status queries and termination)
- Secure storage: Bot Token and Chat ID stored in Windows Credential Locker / OS keyring
- System tray mode for unobtrusive background operation
- Single instance enforcement (second launch restores the running instance)
- Deployment-aware updates (script self-update; EXE prompts download)

---

## Screenshots

> **Placeholder:** Main Monitor tab - active jobs list and live activity log.

> **Placeholder:** Config tab - credentials, directory selection, heartbeat and theme.

> **Placeholder:** Telegram chat - status output and convergence plot.
---

## Quick Start

Minimal steps to first run (script mode):

```bash
pip install -r requirements.txt
python abaqus_watcher_gui.py
```

Then configure Telegram credentials and your ABAQUS working directory in the app.

---

## Installation

### Prerequisites

- **Python 3.10 or higher** (tested and confirmed working with Python 3.10, 3.11, 3.12, and 3.14)
  - The application uses modern Python features including `match`/`case` statements and improved typing syntax.
  - Newer Python versions should work as long as all dependencies have compatible wheels available.
- **ABAQUS** installed on the host machine (simulation files must be accessible)
  - The app monitors output files (`.sta`, `.lck`) created by ABAQUS during job execution.
  - No direct ABAQUS API integration—works by parsing file system changes.

### Script install

```bash
git clone https://github.com/daadaan/ABAQUS_Watcher_GUI.git
cd ABAQUS_Watcher_GUI
pip install -r requirements.txt
python abaqus_watcher_gui.py
```

### Run the Python script via a Windows shortcut

If you run the script version regularly, a Windows shortcut makes it one-click.

1. Right-click your Desktop (or a folder) → **New** → **Shortcut**.
2. For **Type the location of the item**, set the shortcut to launch Python and pass the script path.
3. Right-click the new shortcut → **Properties** and set:
  - **Target** (command to run)
  - **Start in** (working directory)
  - Optional: **Run** = *Minimised* (if you do not want a console window in the way)

> **Important**
> Use full paths and keep the quotes, especially if any folder name contains spaces.

**Example A (visible console for troubleshooting)**

- **Target**

```text
"C:\\Path\\To\\Python\\python.exe" "C:\\Path\\To\\ABAQUS_Watcher_GUI\\abaqus_watcher_gui.py"
```

- **Start in**

```text
C:\\Path\\To\\ABAQUS_Watcher_GUI
```

**Example B (no console window: use pythonw.exe)**

If you prefer not to show a console window, use `pythonw.exe` (same Python install):

- **Target**

```text
"C:\\Path\\To\\Python\\pythonw.exe" "C:\\Path\\To\\ABAQUS_Watcher_GUI\\abaqus_watcher_gui.py"
```

> **Note**
> With `pythonw.exe`, errors will not appear in a console window. If the app does not start, switch back to `python.exe` to see any error output.

### Windows EXE install

For Windows users who prefer not to manage Python environments, a pre-compiled `.exe` is available:

1. Go to the [Releases Page](https://github.com/daadaan/ABAQUS_Watcher_GUI/releases).
2. Download `ABAQUSWatcherGUI_vX.X.X.exe`.
3. Double-click to run. No installation required.

### SmartScreen note

> **Warning (Windows SmartScreen)**
> When you first run the `.exe`, **Microsoft SmartScreen** may display a warning like:
>
> **Windows protected your PC**  \
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.  \
> Publisher: Unknown
>
> **Why does this happen?**  \
> The executable is not code-signed with a paid certificate (which costs hundreds of dollars per year). This is common for open-source and personal projects.
>
> **Is it safe?**  \
> ✅ **Yes.** The `.exe` is built automatically by [GitHub Actions](https://github.com/daadaan/ABAQUS_Watcher_GUI/actions) directly from the Python source code in this repository. You can verify this yourself:
> - View the [build workflow](.github/workflows/) to see exactly how the executable is generated.
> - Inspect the [source code](abaqus_watcher_gui.py) – it's fully open and transparent.
>
> **To run anyway:**
> 1. Click **"More info"** on the SmartScreen popup.
> 2. Click **"Run anyway"**.

---

## Configuration

The app has a GUI tab for configuration. You do not need to edit config files manually.

### Telegram bot setup

You need two things: a **Bot Token** and your **Chat ID**.

**Step A: Get Bot Token**
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send the command `/newbot`.
3. Follow the instructions to name your bot.
4. BotFather will give you a long string (e.g., `123456:ABC-DEF1234...`). Copy this Token.

**Step B: Get Chat ID**
1. Search for [@userinfobot](https://t.me/userinfobot) on Telegram.
2. Click "Start" or send any message.
3. It will reply with your "Id" (a number like `123456789`). Copy this ID.

> **Security note**
> Credentials (Bot Token, Chat ID) are encrypted and stored in the **Windows Credential Locker** (or platform keyring), never in plain-text files.

### App configuration steps

1. Open **ABAQUS Watcher GUI**.
2. Go to the **Config** tab.
3. Paste your **Bot Token** and **Chat ID**.
4. **ABAQUS Temp Directory:** browse to the folder where you run your jobs (this is where `.lck` and `.sta` files appear).
5. **Heartbeat:** set how often (in seconds) the bot should send a silent "Still Running" message for long jobs (Default: 3600s).
6. Click **Save Settings**.

---

## Usage

### What happens during monitoring

When the watcher is running, the app:

- Polls the configured directory for `.lck` files (active jobs).
- Reads `.sta` files for status/progress, convergence data, and ODB frame progress.
- Updates the in-app job list (including time remaining when available).
- Sends Telegram responses for commands and event notifications.

### Notifications behaviour

> **Notification behaviour**
> To prevent spamming your phone, **all routine updates (Heartbeats, Status checks) are sent silently.** You will only receive a sound/vibration notification for critical events: **Job Completion** or **Job Abort**.

---

## Telegram Commands

> **Note**
> Job names are case-sensitive and must match the filename exactly (excluding `.lck` or `.sta`).

### Status commands

| Command | Description |
| --- | --- |
| `/status_all` | List **all** recent jobs (running, completed, and errors). Shows time estimates for active jobs. |
| `/status_running` | Show only currently **running** jobs with estimated time remaining. |
| `/status_completed` | Show only successfully **completed** jobs. |
| `/status_error` | Show only **failed** or aborted jobs. |

### Control commands

| Command | Description |
| :--- | :--- |
| `/status <Job-Name>` | Get detailed stats (Time, Increments, **Estimated Time Remaining**) and a **convergence plot** for a specific job. \
*Example:* `/status Job-1` |
| `/kill <Job-Name>` | **Terminate** a specific job remotely. \
*Example:* `/kill Job-1` |

### Setup Telegram command menu

To make the status commands appear in a clickable menu inside your Telegram chat:

1. Open Telegram and search for **@BotFather**.
2. Send the command `/setcommands`.
3. Select your ABAQUS Watcher bot from the list.
4. Copy and paste the following command list when prompted:

```text
status_all - List all recent jobs
status_running - List currently running jobs
status_completed - List successfully finished jobs
status_error - List failed or aborted jobs
```

> **Note**
> Commands like `/status <job>` and `/kill <job>` require specific job names, so they are not included in the preset menu. You must type them manually.

---

## Time Estimation Method

### How it works

The app estimates job completion time using **linear extrapolation** based on ABAQUS's ODB frame output:

1. Tracks progress: monitors the current frame number vs. total frames from the `.sta` file (e.g., "ODB Field Frame Number 25 of 100").
2. Measures elapsed time: extracts CPU time from the latest increment data (HH:MM:SS format).
3. Calculates rate: `seconds_per_frame = elapsed_time / current_frame`.
4. Projects completion: `remaining = (total_frames - current_frame) * seconds_per_frame`.

**Example output**
- In App: `"1h 20m"` (time remaining only)
- In Telegram: `"⏱️ Left: 1h 20m (Tot: 3h 45m)"` (remaining time + total estimated duration)

### Accuracy notes

When it works best (±10-25% accuracy):

- Uniform increment sizes (fixed time stepping)
- Linear analyses with stable convergence
- Constant material properties

When it may be less accurate (±50%+ error):

- Highly adaptive time stepping (early vs. late increments differ significantly)
- Complex contact conditions with friction
- Jobs with severe convergence issues requiring cutbacks
- First 10% of job execution (insufficient data)

### Limitations

- Estimates appear as `"Calculating..."` until the first ODB frame is written.
- Some ABAQUS jobs don't generate ODB frames (no estimation available).
- Updates every 3 seconds as new increment data becomes available.

---

## Architecture Overview

### GUI framework

- Built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) for a modern dark/light mode interface.

### Thread model

- Main thread handles all UI operations (thread-safe).
- Background daemon thread monitors directory every 3 seconds.
- Separate threads for network operations (Telegram polling, update checks).

### File monitoring approach

- Polls directory for `.lck` files (active jobs) and `.sta` files (status/progress data).

### Security model

> **Security note**
> OS-level credential storage via `keyring` library (Windows Credential Manager, macOS Keychain, Linux Secret Service).

---

## Performance Notes

- Tail reading: only reads the last 250KB of `.sta` files instead of loading entire files (10-100x speed-up for large files).
- Header caching: start times and job metadata are cached after first read to avoid repeated parsing.
- Job limiting: summary commands limited to 15 most recent jobs to prevent Telegram message size errors.
- Efficient polling: 3-second intervals balance responsiveness with CPU usage.
- Memory management: console logs limited to 500 lines; matplotlib figures explicitly closed to prevent memory leaks.

---

## Data Storage Locations

| What | Location |
| --- | --- |
| Configuration file (Windows) | `%LOCALAPPDATA%\ABAQUSWatcherGUI\abaqus_watcher_config.json` |
| Configuration folder (Linux/macOS) | `~/.config/ABAQUSWatcherGUI/` |
| Stored settings | Directory path, heartbeat interval, theme preference, tray settings |
| Secure credentials | Bot token and chat ID stored in OS keyring (never in JSON files) |

---

## Update Workflow

The application includes a built-in update checker in the **Config** tab.

### Script mode

- Uses a "Deployment Aware" update system.
- If a new version is found, it will offer to **automatically download** the new code from GitHub, overwrite the current script, and restart itself.

### EXE mode

- Alerts you and opens the GitHub Releases page so you can download the latest executable.

---

## Limitations and Known Constraints

- No direct ABAQUS API integration—works by parsing file system changes.
- Time estimation depends on ODB frame output; some jobs will never show an estimate.
- Single instance enforcement uses port 54321; if another application uses port 54321 (rare), behaviour may be affected.

---

## Troubleshooting

### App Won't Start

- Ensure Python 3.10+ is installed: `python --version`
- Install all dependencies: `pip install -r requirements.txt`
- Check for missing libraries: the error message will indicate which module is missing

### "Keyring Error" on Startup

- Windows: Windows Credential Manager should work automatically
- Linux: install `gnome-keyring` or `kwallet` depending on your desktop environment
- Workaround: run app as administrator or use script mode instead of system keyring

### Watcher Not Detecting Jobs

- Verify directory path: ensure the path points to where ABAQUS writes `.lck` and `.sta` files (typically the working directory where you run `abaqus job=...`)
- Check file permissions: the app needs read access to the directory
- ABAQUS must be running: the app monitors files created by ABAQUS, not ABAQUS itself

### Telegram Bot Not Responding

- Test Connection: click "Test Connection" button in the Monitor tab
- Verify Bot Token: get a fresh token from @BotFather if needed
- Check Chat ID: ensure you copied the correct numeric ID from @userinfobot
- Network issues: the app requires internet access to reach Telegram's servers
- Bot must be started: send `/start` to your bot in Telegram before using it

### Time Estimates Missing or "Calculating..."

- Wait for first frame: estimates require at least one ODB frame to be written
- Check ODB output: some jobs don't write ODB frames (no estimation possible)
- Verify `.sta` file: the app reads frame data from the status file

### Multiple Instances Running

- Should not happen: the app prevents this automatically via port 54321
- If port blocked: another application may be using port 54321 (rare)
- Manual fix: close all instances and restart

### High Memory Usage

- Large `.sta` files: the app only reads the last 250KB, not the entire file
- Memory leak: ensure you're using the latest version (memory management improved in v1.3+)
- Restart app: close and reopen if memory grows over time

### Update Check Fails

- Network required: update check connects to GitHub API
- GitHub rate limiting: try again in a few minutes
- Firewall/Proxy: may block access to api.github.com

---

## FAQ

**Do I need to edit config files by hand?**  \
No. The app features a GUI tab to handle setup.

**Where are my Telegram credentials stored?**  \
In the OS keyring (Windows Credential Locker / platform keyring), not in plain-text files.

**Why are my routine messages silent?**  \
To prevent spamming your phone: Heartbeats and status checks are sent silently; only completion/abort triggers a sound/vibration.

**Why does time remaining show as "Calculating..."?**  \
Time estimates require ODB frame output and at least one frame written.

**Why isn’t there a time estimate for my job?**  \
Some ABAQUS jobs do not generate ODB frames, so no estimate is available.

**Why does Windows warn about the EXE?**  \
The executable is not code-signed; SmartScreen warnings are expected for unsigned binaries.

---

## Contributing and Support

### Contributing

Contributions are welcome! Here's how you can help:

**Bug Reports & Feature Requests**
- Use the [GitHub issue tracker](https://github.com/daadaan/ABAQUS_Watcher_GUI/issues) to report bugs or request features.
- Before opening a new issue, please search existing issues to avoid duplicates.
- Include relevant details: Python version, OS, error messages, `.sta` file samples (if applicable).

**Pull Requests**
- Fork the repository and create a feature branch (`git checkout -b feature/your-feature-name`).
- Follow the coding standards documented in [.github/copilot-instructions.md](.github/copilot-instructions.md).
- Test your changes with Python 3.10+ on your target platform.
- Submit a PR with a clear description of your changes and the problem they solve.

**Code of Conduct**
- Please review the [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

### Support

**Community Support**
- **Issues:** [GitHub issue tracker](https://github.com/daadaan/ABAQUS_Watcher_GUI/issues) for bug reports and feature requests.
- **Discussions:** Use GitHub Discussions for general questions and usage help.
- **Documentation:** This README covers most common use cases and troubleshooting scenarios.

**Sponsorship**
If this tool saves you time or helps your research/work, consider supporting development:

- [GitHub Sponsors](https://github.com/sponsors/daadaan) - Recurring or one-time support
- [Buy Me a Coffee](https://buymeacoffee.com/daadaan) - Quick one-time donation

Your support helps maintain and improve the project!

---
## Citation / Academic Use

If this tool helps your research, please cite the software (and optionally star the repository).

---
## License

This project is licensed under the **Apache License 2.0**.  \
Copyright (c) 2026, Souvik Biswas (daadaan).