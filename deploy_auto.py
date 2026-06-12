#!/usr/bin/env python3
"""
Automated deployment of sketch.py to AMYboard via serial REPL
"""

import serial
import time
import sys
import base64

def deploy_sketch():
    """Deploy sketch.py to AMYboard"""
    
    PORT = '/dev/cu.usbmodem11401'
    BAUD = 115200
    
    # Read sketch.py and encode it
    with open('sketch.py', 'r') as f:
        code = f.read()
    
    encoded = base64.b64encode(code.encode('utf-8')).decode('ascii')
    
    print("Connecting to AMYboard...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=2)
    except Exception as e:
        print(f"Error: Could not open {PORT}: {e}")
        return False
    
    time.sleep(1)
    
    # Clear the input buffer
    ser.reset_input_buffer()
    time.sleep(0.2)
    
    # Send Ctrl-C to interrupt any running code
    print("Interrupting...")
    ser.write(b'\x03')
    time.sleep(0.3)
    
    # Read any output
    output = ser.read(256).decode('utf-8', errors='ignore')
    print(f"[Device] {output[:100]}")
    
    # Build the deployment command
    deploy_cmd = f"""import base64,os
os.makedirs('/user/current',exist_ok=True)
with open('/user/current/sketch.py','w') as f:f.write(base64.b64decode('{encoded}').decode('utf-8'))
print('DEPLOY_OK')
"""
    
    print("\nSending deployment command...")
    print(f"Code size: {len(code)} bytes")
    print(f"Encoded size: {len(encoded)} bytes")
    
    # Send the command line by line
    for line in deploy_cmd.split('\n'):
        if line.strip():
            print(f">>> {line}")
            ser.write((line + '\n').encode('utf-8'))
            time.sleep(0.1)
    
    # Press Enter to execute
    ser.write(b'\n')
    time.sleep(0.5)
    
    # Read response
    response = ser.read(512).decode('utf-8', errors='ignore')
    print(f"\n[Device Response]\n{response}")
    
    if 'DEPLOY_OK' in response:
        print("\n✓ Deployment successful!")
        
        # Send soft reset
        print("\nSoft-resetting board...")
        ser.write(b'\x04')  # Ctrl-D
        time.sleep(1)
        
        reset_response = ser.read(512).decode('utf-8', errors='ignore')
        print(f"[After reset]\n{reset_response[:200]}")
        
        ser.close()
        return True
    else:
        print("\n✗ Deployment may have failed. Checking...")
        ser.close()
        return False

if __name__ == '__main__':
    try:
        success = deploy_sketch()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
