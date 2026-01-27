# Abaqus Watcher GUI - Copilot Instructions

## Project Overview
This is a cross-platform Desktop GUI application (Python) to monitor SIMULIA Abaqus simulation jobs. It parses local text files (`.lck`, `.sta`) in real-time and sends status updates, convergence plots, and commands via Telegram.

## Tech Stack & Libraries
* **GUI Framework:** `customtkinter` (ctk). Use `ctk` widgets for all visible elements.
* **Icon Handling:** `PIL` (Pillow) and `ImageTk`.
    * **CRITICAL:** Standard `tkinter` methods like `iconphoto` do NOT accept `ctk.CTkImage`. You must convert images using `ImageTk.PhotoImage` before passing them to window icon functions.
* **Plotting:** `matplotlib`. Backend MUST be set to `Agg` (`matplotlib.use('Agg')`) to prevent main thread blocking.
* **Networking:** `requests` for Telegram API and GitHub updates.
* **Security:** `keyring` for storing credentials (Bot Token, Chat ID) in the OS Vault.
* **System Tray:** `pystray` for background operation.

## Repo Hygiene
* Prefer keeping dependencies in `requirements.txt` and installing via `pip install -r requirements.txt`.

## Coding Standards & Patterns

### 1. Constants & Configuration
* **Module-Level Constants:** Use named constants instead of magic numbers. All performance-related values (file read limits, console line limits, scan ranges) are defined at the module top:
  * `MAX_TAIL_BYTES` - Bytes to read from .sta file tail
  * `MAX_CONSOLE_LINES` - Log console line limit
  * `MAX_SUMMARY_JOBS` - Job limit for /status all
  * `HEADER_SCAN_LINES` / `START_SCAN_LINES` - File scanning limits

### 2. Deployment-Aware Updates
The application has two update modes:
* **Frozen (EXE):** If `getattr(sys, 'frozen', False)` is true, prompt the user to download the new executable from GitHub Releases.
* **Script (.py):** If running as a script, download the raw `.py` file from the repository, validate content, and overwrite `__file__` to self-update.

### 3. Threading Model
* **Main Loop:** The `run_loop` handles file monitoring and must run in a separate `threading.Thread`.
* **Network Calls:** All API calls (updates, telegram polling) must be threaded to avoid freezing the GUI.
* **Safety:** Use `self.after(0, callback)` to update UI elements from background threads.

Additional guidance:
* Keep background threads as `daemon=True` so they don"t prevent process exit.
* Never touch CTk widgets from worker threads.

### 4. File Parsing Logic
* **Status (.sta):** Prefer reading only the tail of `.sta` files (bytes) and scanning backwards for the latest increment data.
* **Start time caching:** When a job is first detected, read the header once for the DATE/TIME line and cache it; do not re-scan headers for every heartbeat.
* **Job History:** To summarize all jobs (`/status all`), scan the directory for all `.sta` files, sort by `mtime` (newest first), and limit processing to the top 15 to prevent API message length errors.

### 5. Secret Management
* **Input:** Use the helper method `self.add_input(..., secret=True)` which adds a toggle visibility button.
* **Storage:** Never save secrets to JSON. Always use `keyring.set_password`.

### 6. Matplotlib Integration
* Never use `plt.show()`.
* Always save the plot to a temporary file, send it via Telegram, and immediately close the figure to avoid memory leaks.
* **Best Practice:** Use explicit figure management with try-finally blocks:
  ```python
  fig = None
  try:
      fig = plt.figure(figsize=(10, 5))
      # ... plotting code ...
      plt.savefig(path)
      return path
  finally:
      if fig is not None:
          plt.close(fig)
  ```

## Security Notes
* **Job Name Validation:** ALWAYS validate job names from Telegram commands (`/status`, `/kill`) using `_is_safe_job_name()` before file operations. Allow only `[A-Za-z0-9._-]` to prevent path traversal attacks.
* Never pass user-supplied input to a shell. Prefer `subprocess.run([...])` with list arguments.

## Type Checker Notes
* Some Tk/customtkinter type stubs are stricter than runtime behavior (e.g., `iconphoto(PhotoImage)` and CTk tuple colors). If needed, use narrow `# type: ignore[...]` on those lines rather than weakening types across the file.

## Specific Implementation Details
* **Icon Color:** The app uses a specific branding color `#6769a2` for generated circular icons.
* **Build System:** The app is built using `pyinstaller` via GitHub Actions.
* **Compatibility:** Ensure code works on Python 3.10+ (avoid pre-release versions due to `pystray`/`keyring` C-API issues).