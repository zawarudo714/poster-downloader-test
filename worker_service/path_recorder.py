"""
Record mouse paths that get past a marketplace's interstitial wall.

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
════════════════════════════════════════════════════════════════════════════
TeePublic puts a full-page wall in front of the account page. Its "No Thanks"
control is a checkbox sealed inside a CLOSED shadow root, which means Selenium
cannot find it and page JavaScript cannot reach it. There is no selector to
write and no amount of waiting produces one.

A real click does not need a selector — it aims at a POSITION and the browser
works out what is underneath. So the position comes from here: you move the
mouse to it yourself, and the agent replays that movement afterwards.

════════════════════════════════════════════════════════════════════════════
NO CLICK IS EVER RECORDED, AND THAT IS THE WHOLE TRICK
════════════════════════════════════════════════════════════════════════════
F9 starts, F9 stops. The mouse BUTTON is never pressed, so the wall is never
dismissed, so the page does not change — and you can record fifty paths in a
row without reloading anything. The click is added by the agent at replay
time; it was never yours to record.

════════════════════════════════════════════════════════════════════════════
PAGE COORDINATES, NOT SCREEN COORDINATES
════════════════════════════════════════════════════════════════════════════
Your mouse moves in screen coordinates — "1240 pixels across the monitor".
What gets stored is "340 pixels in from the left edge of the WEB PAGE".

The difference between the two is wherever the Chrome window happens to sit,
plus the height of the tab strip and address bar. Screen coordinates break
when the window moves, when it is resized, when Chrome grows a bookmarks bar,
or when the desktop resolution changes — and they break SILENTLY, clicking
empty space while reporting success.

Which is why this tool OPENS CHROME ITSELF, at a known size and position, and
asks Chrome where the page sits. If you opened the browser yourself and
dragged it somewhere, that conversion would be guesswork.

Run it by double-clicking RECORD_PATHS.bat next to this file.
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent

# The window is pinned so that every recording shares one frame of reference,
# and so replay meets the same layout. Matches the agent's own --window-size.
WINDOW_W, WINDOW_H = 1920, 1080
WINDOW_X, WINDOW_Y = 0, 0

# How often the mouse is sampled while recording. 120/second is far finer than
# a hand can move and keeps a four-second path to a few hundred points.
SAMPLE_HZ = 120


def load_config() -> dict:
    """The agent's own config, so this tool cannot disagree with it."""
    for candidate in (HERE / "config.json", HERE.parent / "config.json"):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return {}


def agent_running() -> bool:
    """The agent drives these same profiles; running both at once locks them."""
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            line = " ".join(proc.info.get("cmdline") or []).lower()
            if "worker_service.agent" in line or "worker_service\\agent.py" in line:
                return True
        except Exception:
            continue
    return False


class Recorder(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Record mouse paths")
        self.geometry("620x430+40+40")
        self.attributes("-topmost", True)

        self.config_data = load_config()
        self.client = None
        self.driver = None
        self.offset = (0, 0)
        self.marketplace = "teepublic"

        self.recording = False
        self.points: list[list[int]] = []
        self.saved = 0

        self._build()
        self._refresh_list()

    # ── Layout ──────────────────────────────────────────────────────────
    def _build(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Button(top, text="OPEN THE WALL", command=self.open_browser,
                  height=2).pack(side="left")
        tk.Label(top, text="  then press F9 to start, F9 again to stop",
                 fg="#555").pack(side="left")

        self.status = tk.Label(self, text="Chrome is not open yet.", anchor="w",
                               fg="#a33", wraplength=580, justify="left")
        self.status.pack(fill="x", padx=10)

        self.counter = tk.Label(self, text="Paths saved: 0",
                                font=("Segoe UI", 14, "bold"), anchor="w")
        self.counter.pack(fill="x", padx=10, pady=(6, 0))

        columns = ("label", "points", "seconds", "used", "failed")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        for key, text, width in (("label", "Path", 200), ("points", "Points", 70),
                                 ("seconds", "Length", 70), ("used", "Used", 60),
                                 ("failed", "Failed", 60)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=8)

        row = tk.Frame(self)
        row.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(row, text="Replay selected", command=self.replay_selected).pack(side="left")
        tk.Button(row, text="Delete selected", command=self.delete_selected).pack(side="left", padx=6)
        tk.Button(row, text="Refresh", command=self._refresh_list).pack(side="left")

        # F9 works while this window has focus. The browser is a separate
        # window, so the hotkey is also registered globally where possible —
        # otherwise you would have to click back here between every recording.
        self.bind("<F9>", lambda _e: self.toggle())
        self._install_global_hotkey()

    def _install_global_hotkey(self):
        try:
            from pynput import keyboard
        except ImportError:
            self.status.config(
                text="pynput is not installed, so F9 only works while THIS "
                     "window is in front. Install it for a global hotkey: "
                     "pip install pynput")
            return

        def on_press(key):
            if key == keyboard.Key.f9:
                self.after(0, self.toggle)

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()

    # ── The browser ─────────────────────────────────────────────────────
    def open_browser(self):
        if agent_running():
            messagebox.showwarning(
                "The agent is running",
                "Stop the agent first, or it and this tool will fight over "
                "the same Chrome profile.", parent=self)
            return
        try:
            from .client import PipelineClient
            from .uploader import MarketplaceUploader
        except ImportError:
            from client import PipelineClient
            from uploader import MarketplaceUploader

        cfg = self.config_data
        if not cfg.get("server_url") or not cfg.get("token"):
            messagebox.showerror("No config.json",
                                 "This tool needs the same config.json the "
                                 "agent uses.", parent=self)
            return

        self.client = PipelineClient(cfg["server_url"], cfg["token"])
        try:
            info = self.client.get("/wall/record-target",
                                   marketplace=self.marketplace)
        except Exception as e:
            messagebox.showerror("Could not reach the server", str(e), parent=self)
            return

        self.status.config(text="Opening Chrome…", fg="#555")
        self.update_idletasks()

        try:
            uploader = MarketplaceUploader(
                account=info["account"], settings=info["settings"],
                config=cfg, client=self.client, log=lambda *_a, **_k: None,
                job_id=None,
            )
            uploader.start()
            self.driver = uploader.driver
            self.uploader = uploader
            self.driver.set_window_position(WINDOW_X, WINDOW_Y)
            self.driver.set_window_size(WINDOW_W, WINDOW_H)
            self.driver.get(info["url"])
            time.sleep(3)
            self.offset = uploader.page_offset()
        except Exception as e:
            messagebox.showerror("Could not open the page", str(e), parent=self)
            return

        self.status.config(
            text=f"Chrome is open. Page corner is at {self.offset} on screen. "
                 f"Do NOT move the window — every recording is measured from "
                 f"that corner.", fg="#276")

    # ── Recording ───────────────────────────────────────────────────────
    def toggle(self):
        if self.driver is None:
            self.status.config(text="Press OPEN THE WALL first.", fg="#a33")
            return
        if self.recording:
            self.stop()
        else:
            self.start()

    def start(self):
        self.points = []
        self.recording = True
        self.status.config(text="RECORDING — move to the checkbox, then F9.",
                           fg="#a33")
        threading.Thread(target=self._sample_loop, daemon=True).start()

    def _sample_loop(self):
        """
        Follow the cursor and convert to page coordinates as we go.

        Converted here rather than at save time so that a window moved
        mid-session corrupts only the paths recorded after the move, and the
        check below can catch even that.
        """
        try:
            from pynput.mouse import Controller
        except ImportError:
            self.after(0, lambda: messagebox.showerror(
                "pynput is required",
                "Install it on this machine:  pip install pynput",
                parent=self))
            self.recording = False
            return

        mouse = Controller()
        started = time.time()
        off_x, off_y = self.offset
        interval = 1.0 / SAMPLE_HZ
        while self.recording:
            sx, sy = mouse.position
            ms = int((time.time() - started) * 1000)
            point = [int(sx) - off_x, int(sy) - off_y, ms]
            # Skip a repeat position: a hand that pauses should cost one point
            # and a timestamp, not two hundred identical ones.
            if not self.points or self.points[-1][:2] != point[:2]:
                self.points.append(point)
            time.sleep(interval)

    def stop(self):
        self.recording = False
        time.sleep(0.05)
        points = list(self.points)

        if len(points) < 5:
            self.status.config(
                text=f"Only {len(points)} points — that is a jump, not a "
                     f"movement. Try again.", fg="#a33")
            return

        # A path that ends outside the page was recorded against a window that
        # moved, or with the cursor off-screen. Caught here rather than at
        # 6am, where it would look like the wall had changed.
        last_x, last_y, _ms = points[-1]
        if not (0 <= last_x <= WINDOW_W and 0 <= last_y <= WINDOW_H):
            self.status.config(
                text=f"That path ends at ({last_x}, {last_y}), which is "
                     f"outside the page. Did the Chrome window move?", fg="#a33")
            return

        try:
            reply = self.client.post("/wall/paths", {
                "marketplace": self.marketplace,
                "points": points,
                "page_width": WINDOW_W,
                "page_height": WINDOW_H,
            })
        except Exception as e:
            self.status.config(text=f"Could not save it: {e}", fg="#a33")
            return

        self.saved += 1
        seconds = points[-1][2] / 1000.0
        self.counter.config(text=f"Paths saved: {self.saved}")
        self.status.config(
            text=f"Saved {reply.get('label')} — {len(points)} points, "
                 f"{seconds:.1f}s, ending at ({last_x}, {last_y}). "
                 f"F9 to record another.", fg="#276")
        self._refresh_list()

    # ── The list ────────────────────────────────────────────────────────
    def _refresh_list(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if self.client is None:
            return
        try:
            data = self.client.get("/wall/paths", marketplace=self.marketplace)
        except Exception:
            return
        for p in data.get("paths", []):
            self.tree.insert("", "end", iid=str(p["id"]), values=(
                p["label"], p["points"], f"{p['duration_ms'] / 1000:.1f}s",
                p["used"], p["failed"]))

    def _selected_id(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def replay_selected(self):
        """
        Watch a saved path play back.

        Worth having: a path that looks fine as a row of numbers can still be
        one that leaves the page or lands beside the checkbox, and the only
        cheap way to know is to see it.
        """
        path_id = self._selected_id()
        if path_id is None or self.driver is None:
            return
        try:
            data = self.client.get("/wall/paths", marketplace=self.marketplace)
            match = next((p for p in data.get("paths", [])
                          if p["id"] == path_id), None)
            if match is None:
                return
            full = self.client.get("/wall/path", path_id=path_id)
            self.uploader.replay_path(full["points"])
            self.status.config(text=f"Replayed {match['label']}. If the wall "
                                    f"is gone, it landed.", fg="#276")
            self._refresh_list()
        except Exception as e:
            self.status.config(text=f"Could not replay: {e}", fg="#a33")

    def delete_selected(self):
        path_id = self._selected_id()
        if path_id is None:
            return
        try:
            self.client._request("DELETE", f"/wall/paths/{path_id}")
        except Exception as e:
            self.status.config(text=f"Could not delete: {e}", fg="#a33")
            return
        self._refresh_list()


def main() -> None:
    Recorder().mainloop()


if __name__ == "__main__":
    main()
