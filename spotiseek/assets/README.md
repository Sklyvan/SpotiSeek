# App assets

Place the application logo here as:

```
spotiseek/assets/icon.png
```

- **Format:** PNG with a **transparent background**.
- **Shape:** square (e.g. 1024×1024), subject centered with a little padding.

When present, `icon.png` is picked up automatically by the desktop GUI and used
as the window / dock / taskbar icon (see `spotiseek/gui.py:_app_icon`). If the
file is missing, the GUI still runs — just without a custom icon.
