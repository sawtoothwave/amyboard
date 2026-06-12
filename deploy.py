#!/usr/bin/env python3
"""
Deploy sketch.py to AMYboard via REPL

This generates a command that can be pasted directly into the MicroPython REPL
to deploy the sketch to /user/current/sketch.py
"""

import base64
import sys

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
    
    # Create a compact deploy command
    deploy_cmd = f"""import base64,os
os.makedirs('/user/current',exist_ok=True)
with open('/user/current/sketch.py','w') as f:f.write(base64.b64decode('{encoded}').decode('utf-8'))
print('✓ sketch.py deployed to /user/current/sketch.py')
"""
    
    return deploy_cmd, code

def main():
    deploy_cmd, orig_code = create_deploy_command()
    
    print("=" * 70)
    print("AMYboard sketch.py Deployment Instructions")
    print("=" * 70)
    print()
    print("1. Connect to AMYboard REPL using screen:")
    print("   $ screen /dev/cu.usbmodem11401 115200")
    print()
    print("2. You should see the Python >>> prompt")
    print()
    print("3. Copy the command below and paste it into the REPL:")
    print("-" * 70)
    print(deploy_cmd)
    print("-" * 70)
    print()
    print("4. Press Enter. You should see:")
    print("   ✓ sketch.py deployed to /user/current/sketch.py")
    print()
    print("5. Reset the board (press Ctrl-D or physically reset)")
    print()
    print("6. Exit screen: Press Ctrl-A, then Ctrl-\\ (or Ctrl-A, then 'quit')")
    print()
    print("=" * 70)
    print()
    print("File size:", len(orig_code), "bytes")
    print("Encoded size:", len(deploy_cmd), "bytes")
    print()

if __name__ == '__main__':
    main()
