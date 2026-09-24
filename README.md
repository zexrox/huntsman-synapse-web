# Synapse Web for the Huntsman V3 Pro Tenkeyless

Windows, Chrome, and Python. This extension lets [Synapse Web](https://synapse.razer.com/) use the Razer Huntsman V3 Pro Tenkeyless (RZ03-0498, USB `1532:02a7`, interface 3). The page sees `1532:02d0`. The helper opens only `1532:02a7`, interface 3.

Version 0.2.8.

## Setup

1. Close desktop Synapse.
2. Unzip this folder and leave it where it is.
3. Open `chrome://extensions`, turn on Developer mode, and load the unpacked extension from the `extension` folder.
4. Copy the extension ID and run `install.cmd`. Python must be available as `py` (`py -m pip install hidapi`).
5. Open https://synapse.razer.com

The extension card must show version 0.2.8. The corner label reads: `Synapse Web bridge: the page sees 1532:02d0 (RZ03-0552). The keyboard stays 1532:02a7`.

## Extension ID

Load the unpacked extension, then copy the ID Chrome shows on `chrome://extensions`. Paste that ID when `install.cmd` asks. Each person uses the ID from their own Chrome. That ID is not shared.

## Python

Install Python 3 for Windows from [python.org](https://www.python.org/downloads/). On the first installer screen, enable “Add python.exe to PATH”. Then, from the unzipped folder, run `install.cmd`. Install fails if Python is missing from PATH.

The 0498 firmware does not implement Game Mode key lock, single-key Snap Tap, Tournament Mode, analog report, bottom deadzone, or Analog V3.

To remove it, run `uninstall.cmd`, then remove the extension in Chrome.

Unofficial. Not affiliated with or supported by Razer.

This project is not affiliated with, endorsed by, or supported by Razer Inc. Razer, Synapse, and Huntsman are trademarks of their owner. Synapse Web can change, and this bridge can stop working.

