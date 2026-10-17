## Tests

- Tests run offscreen by default; `tests/conftest.py` forces it. Don't override that.
- Only use `KROMA_TEST_ONSCREEN=1` when the bug involves the window manager or
  compositor: title bar dragging and snapping, maximize, window geometry, native
  frameless behaviour. Ask before running on-screen tests, since they open windows
  on the user's desktop.
