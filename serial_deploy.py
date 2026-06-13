#!/usr/bin/env python3
import serial
import time
import sys

PORT = '/dev/cu.usbmodem11401'
BAUD = 115200

# Minimal sketch.py - just enough to test
SKETCH_CODE = """import amy
import amyboard
print("✓ AMYboard ready!")
"""

def send_code(ser, code):
    """Send code to MicroPython REPL line by line"""
    print("Waiting for REPL prompt...")
    time.sleep(2)
    
    # Make sure we're in REPL mode
    ser.write(b'\x03')  # Ctrl-C to interrupt
    time.sleep(0.5)
    ser.write(b'\x04')  # Ctrl-D to soft reset
    time.sleep(1)
    
    # Clear the buffer
    while ser.in_waiting:
        ser.read(ser.in_waiting)
    
    print("Sending code...")
    
    # Send each line
    for line in code.strip().split('\n'):
        print(f"  > {line}")
        ser.write((line + '\n').encode())
        time.sleep(0.1)
    
    # Send final blank line to execute
    print("Executing...")
    ser.write(b'\n')
    time.sleep(0.5)
    
    # Read response
    response = ser.read(ser.in_waiting).decode(errors='ignore')
    print(response)

try:
    print(f"Opening {PORT} at {BAUD} baud...")
    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)
    
    send_code(ser, SKETCH_CODE)
    
    ser.close()
    print("\n✓ Done!")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
