# WiFi & WebREPL

The global launcher (`wrapper_sketch.py`) can bring the AMYboard online at boot
and start a [WebREPL](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/python.md)
server, so you can leave the board racked in your modular and reach it wirelessly
— deploy sketches or drop into the REPL with no serial cable.

WiFi is **off by default** and is toggled entirely from the on-device menu. The
choice is persisted to flash, so once you turn it on it comes back up on **every**
boot until you turn it off.

## One-time setup: credentials

Credentials are **not** stored in the committed launcher (so the repo stays
secret-free). They live in a JSON file on the board's flash, `/user/wifi.json`,
which sits under the gitignored `/user/` tree. Create it once from the REPL:

```python
import json
json.dump(
    {'ssid': 'my-network', 'password': 'my-wifi-password',
     'webrepl_password': 'amyboard'},
    open('/user/wifi.json', 'w'))
```

- `ssid` / `password` — your network.
- `webrepl_password` — the WebREPL login (defaults to `amyboard` if omitted).
  **WebREPL gives anyone on the network with this password full control of the
  board — use a non-trivial password and only trusted networks.**

## Turning it on / off

From the global launcher menu (hold the encoder to reach it):

```
AMYBOARD
  Resume
  Load Sketch
  WiFi           <- click
```

The **WiFi** submenu shows the current status and a single toggle:

```
WIFI
  Status: off            (or the board's IP once connected, e.g. 192.168.1.42)
  Turn WiFi On           (toggles to "Turn WiFi Off" when on)
```

Clicking the toggle flips the persisted flag and connects/disconnects
immediately. The join is blocking for a few seconds; under the overlay AMY keeps
sounding any held notes meanwhile.

## Behavior notes

- **Boot:** if WiFi was left enabled, the launcher joins the network and starts
  WebREPL once at boot, *after* the synth/menu is up — so audio comes alive first
  and the join adds a few seconds before the board is reachable.
- **Fail-safe:** every WiFi call is fully guarded. A missing/bad `wifi.json`, a
  failed join, or a firmware without `webrepl` can never raise out of the boot
  path and brick the board — it just stays offline (check the serial log for a
  `WiFi ...` line).
- **Reaching it:** once connected, point a WebREPL client at `ws://<ip>:8266/`
  (the IP is shown on the WiFi submenu's status line).

## Deploying over WiFi

`deploy_wifi.py` pushes a file to the board over WebREPL and verifies a
byte-for-byte readback — the wireless counterpart to `deploy_auto.py` (serial).
It has no dependencies beyond the stdlib (it speaks just enough of the WebSocket +
WebREPL binary file protocol itself).

```
python deploy_wifi.py --host <ip> --password <webrepl_pw> \
    --sketch wrapper_sketch.py --dest /user/current/sketch.py [--reboot]
```

**Important:** this firmware services WebREPL's *file transfer* but **not** its
interactive REPL while the launcher is running, so a deploy cannot issue
`machine.reset()` directly. A pushed file therefore takes effect on the **next
reboot**, not immediately. Two ways to reboot:

- **`--reboot` (wireless):** after a verified push, drops a sentinel file
  (`/user/reboot_request`) that the launcher's `loop()` polls for (~every 2s) and,
  when it sees it, deletes and resets. Because WiFi *remembers its last setting*,
  the board rejoins the network a few seconds later — fully wireless iteration.
  *Caveat:* this only works once a launcher that **has** the reboot hook is already
  running. The very first deploy of the hook must be activated manually (below).
- **On-device gesture:** at the global menu root, **keep holding the encoder** for
  ~5 seconds — a "HOLD TO REBOOT" countdown appears, then it resets. A turn or
  release cancels. Handy for a clean reboot without power-cycling.

If neither applies (e.g. first-time hook install), power-cycle the board or use the
on-device **Load Sketch**, which also resets.
