# Repository Guidelines

## **FOR EVERY USER REQUEST THAT REQUIRES CHANGING PRODUCTION CODE OR TESTS PERFORM THIS RITUAL**

1. Repeat back to the user what you think is in scope for this request and what's out of scope
2. Once the user has agreed, make an explicit promise to the user that if you think you need to do something out of scope to make the app better, simpler, more elegant or more robust, that you'll stop working and explain why expanding the scope accords with our organizing principle of lowering cognitive load for new devs and prioritizing simplicity over cleverness.
3. When you complete a task, after you run tests, ask the user if they want you to "perform a code review and see if any cleanup is required now that this task complete to see if there is anything messy or inelegant?" If they say yes, review uncommitted changes.
4. Update this document to keep it in parallel with requests the user makes of your behavior.

## User Guidance

- The user is an experienced technologist but is not a professional developer; explain why you are making each meaningful architectural choice.
- When a direction could reasonably apply to both user-facing and internal code (for example renaming UI labels vs. domain modules), pause and confirm the intended scope before proceeding.

## Big Picture

- This repository is intended to create the instrument/control code for an AMYboard synthesizer board, described here: https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md
- The available documentation for the AMY language and the hardware is here: https://github.com/shorepine/amy
- The user owns an AMYboard and wants to use it within their Eurorack synthesizer system, primarily as a polyphonic synthesizer engine that they will control (keyboard, sequencing, parameter changes) via MIDI. The primary keyboard instrument will be the Arturia Keystep Pro; the primary sequencer will be the Squarp Hermod+ (although it might also be driven by MIDI from a variety of other sources, routed through the Hermod+ and into the AMYboard); the primary MIDI parameter controller will be the Oxi e16.
- The user plans to add an Adafruit 128x128 OLED screen (https://www.adafruit.com/product/4741) to the board, as well as a M5Stack I2C joystick (https://shop.m5stack.com/products/i2c-joystick-unit-v1-1-mega8a), to increase the amount of onboard context and control available, but for now those parts are unavailable. The user will tell you when it's time to install those parts.


## Safety Rules (Workspace Hygiene)

- Treat changes you did not author as intentional collaborator work.
- Do not revert or remove others' changes without explicit user approval.
- Do not use destructive git commands.
- **NEVER EVER commit with `--no-verify`.**
- **NEVER EVER push with `--no-verify`.**
- Do not delete files unless explicitly instructed.
- Prefer to use `--3way` style analysis for merge conflicts
- Never commit secrets.