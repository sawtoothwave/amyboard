# Architecture

## High-Level System Design

### MIDI Control Flow

```
Controllers → MIDI → AMYboard → Audio Output
  ├── Keystep Pro (Keyboard)
  ├── Hermod+ (Sequencer)
  └── Oxi One 16 (Parameters)
```

### Core Components

- **MIDI Handler**: Routes and interprets MIDI messages from connected controllers
- **Voice Manager**: Manages polyphonic voice allocation
- **Parameter Control**: Handles real-time parameter changes and modulation
- **Hardware Interface**: Communicates with onboard components (OLED, joystick, etc.)

### Future Enhancements

- OLED display for onboard visual feedback
- I2C joystick for parameter editing and navigation
- Local storage for preset management

## Development Approach

Prioritizes clarity and simplicity to reduce cognitive load for new contributors.
