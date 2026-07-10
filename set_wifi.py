#!/usr/bin/env python3
"""Write /user/wifi.json to a connected AMYboard over the serial REPL.

Run this in your own terminal so your WiFi password is typed locally (it is not
echoed, and it is never written to this repo -- /user/ is gitignored and the
file lives only on the board's flash):

    python set_wifi.py                 # prompts for everything
    python set_wifi.py --ssid my-net   # prompt only for the passwords

The launcher reads this file when you toggle WiFi on from the global menu
(AMYBOARD -> WiFi -> Turn WiFi On), and auto-reconnects on every boot while
enabled. See docs/WIFI.md.
"""

import argparse
import getpass
import json
import sys

from board_serial import BoardSerialError, BoardSerialSession, detect_port

CONF_PATH = '/user/wifi.json'


def main():
    ap = argparse.ArgumentParser(description='Write /user/wifi.json to the AMYboard.')
    ap.add_argument('--port', help='Serial port (auto-detected if omitted).')
    ap.add_argument('--ssid', help='WiFi network name (prompted if omitted).')
    ap.add_argument('--webrepl-password', default=None,
                    help="WebREPL login password (prompted; default 'amyboard').")
    args = ap.parse_args()

    ssid = args.ssid or input('WiFi SSID: ').strip()
    if not ssid:
        print('SSID is required.', file=sys.stderr)
        return 1
    password = getpass.getpass('WiFi password: ')
    webrepl_password = args.webrepl_password
    if webrepl_password is None:
        webrepl_password = getpass.getpass('WebREPL password [amyboard]: ') or 'amyboard'

    conf = {'ssid': ssid, 'password': password, 'webrepl_password': webrepl_password}

    try:
        port = args.port or detect_port()
    except BoardSerialError as e:
        print(f'Serial error: {e}', file=sys.stderr)
        return 1

    # Send the config as a Python literal (repr escapes any special characters),
    # so no quoting/injection issues regardless of what's in the password.
    script = (
        'import json\n'
        f'_c = {conf!r}\n'
        f"with open({CONF_PATH!r}, 'w') as _f:\n"
        '    json.dump(_c, _f)\n'
        'print("__WIFI_WRITE_OK__")\n'
    )
    # Verify by reading back -- print only the SSID + which keys are present, so
    # the password is never echoed to the terminal.
    verify = (
        f"import json;_d=json.load(open({CONF_PATH!r}));"
        "print('__WIFI_VERIFY__', _d.get('ssid'), sorted(_d.keys()))"
    )

    print(f'Writing {CONF_PATH} to board on {port} ...')
    try:
        with BoardSerialSession(port) as s:
            out = s.run_paste_script(script)
            if '__WIFI_WRITE_OK__' not in out:
                print('Write did not confirm. Board output:\n' + out, file=sys.stderr)
                return 2
            vout = s.run_command(verify)
    except BoardSerialError as e:
        print(f'Serial error: {e}', file=sys.stderr)
        return 1

    line = next((l for l in vout.splitlines() if '__WIFI_VERIFY__' in l), '')
    print('Verified on board:', line.replace('__WIFI_VERIFY__', '').strip() or '(no readback)')
    print('\nDone. Now: deploy the updated launcher if you have not yet, then on the')
    print('board hold the encoder -> WiFi -> Turn WiFi On.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
