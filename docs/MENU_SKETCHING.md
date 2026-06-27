### Menu sketching

- global "wrapper" menu
    - [active sketch name/menu]
    <!-- whatever the active sketch's config menu is, if there is one; if not, just display the active sketch filename -->
    - load sketch
        - sketches
        <!-- read from "sketches" folder on SD card -->
            - 01_sketchname.py
            - 02_sketchname.py
            - 03_sketchname.py
            - etc.
    - display mode
    <!-- selected display mode takes over after 5 seconds of encoder inactivity -->
        - cc monitor
        - cv monitor
        - screensaver
        - oscilloscope
        - blank


- polysynth menu
    - param control
    <!-- click to display parameter list -->
        - parameter list
        <!-- click to display parameter list --> 
        - parameter list
            <!-- display list of params, turn encoder to highlight param, click to select it for modification or hold to exit param control and return to polysynth menu -->
            - modify parameter
        <!-- turn encoder to change parameter value; values update real-time as knob turns; click or hold to move up one level back to the parameter list -->
    - midi channel
        - 
    - save preset
        - [alphanumeric entry one character at a time: encoder scrolls through a-z (all lowercase, 0-9, space, delete; confirm saves the name]
            <!-- if a user puts in the same name as an existing preset, display "overwrite?" prompt; click for yes, overwrites and returns to polysynth menu; hold cancels and returns to polysynth menu -->
    - load preset
        