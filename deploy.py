#!/usr/bin/env python3
"""
Deploy sketch.py to AMYboard via a manually pasted MicroPython REPL payload.

This keeps deployment explicit and simple: generate a board-safe command block,
paste it into an existing mpremote REPL session, then reset the board.
"""

import base64
import sys


CHUNK_SIZE = 180

def create_deploy_command():
    """Read sketch.py and create a deployable REPL command"""
    
    try:
        with open('sketch.py', 'r') as f:
            code = f.read()
    except FileNotFoundError:
        print("Error: sketch.py not found in current directory")
        sys.exit(1)
    
    # Encode the code in base64 to avoid quote/escaping issues
    encoded = base64.b64encode(code.encode('utf-8')).decode('ascii')
    
    chunks = [encoded[i:i + CHUNK_SIZE] for i in range(0, len(encoded), CHUNK_SIZE)]
    chunk_list = ',\n'.join(f"    '{chunk}'" for chunk in chunks)

    # Create a REPL-safe deploy command split into manageable chunks.
    deploy_cmd = f"""chunks = [
{chunk_list}
]
import ubinascii
f = open('/user/current/sketch.py', 'w')
f.write(ubinascii.a2b_base64(''.join(chunks)).decode())
f.close()
print(len(open('/user/current/sketch.py').read()))
"""
    
    return deploy_cmd, code

def main():
    deploy_cmd, orig_code = create_deploy_command()
    
    print("=" * 70)
    print("AMYboard sketch.py Deploy Instructions")
    print("=" * 70)
    print()
    print("1. Connect to AMYboard REPL using mpremote:")
    print("   $ source .venv/bin/activate")
    print("   $ mpremote connect /dev/cu.usbmodem1101 repl")
    print()
    print("2. You should see the Python >>> prompt")
    print()
    print("3. Copy the command below and paste it into the REPL:")
    print("-" * 70)
    print(deploy_cmd)
    print("-" * 70)
    print()
    print("4. Press Enter. You should see the file size in bytes.")
    print()
    print("5. Reset the board (press the physical RST button)")
    print()
    print("6. Exit mpremote REPL with Ctrl-X")
    print()
    print("=" * 70)
    print()
    print("File size:", len(orig_code), "bytes")
    print("Encoded size:", len(deploy_cmd), "bytes")
    print()

if __name__ == '__main__':
    main()
