# Changelog
## 0.2.9
- Fn+F10 toggles a Windows-side Game Mode lock (Left Win, Right Win, Alt+Tab, Alt+F4). It starts off. The keyboard firmware still does not implement Game Mode.
- Fn is read from interface 1 (`mi_01`). Interface 0 stays closed.
- The Game Mode switch on Synapse Web follows that same lock. Get Game Mode Selection (`00:da`) is answered by the host. Clicking the switch and pressing Fn+F10 stay one state.
- The Game Mode LED uses Set LED State `03:00`, LED id 8.
## 0.2.8
- Synapse Web bridge for the Huntsman V3 Pro Tenkeyless (RZ03-0498). The page sees `1532:02d0`. The helper opens `1532:02a7`, interface 3.
