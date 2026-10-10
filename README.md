<h1 align=center>Kroma Player</h1>

<div align=center>

![GitHub last commit](https://img.shields.io/github/last-commit/KromaKobra/kroma-player?style=for-the-badge&labelColor=101418&color=9ccbfb)
![GitHub Repo stars](https://img.shields.io/github/stars/KromaKobra/kroma-player?style=for-the-badge&labelColor=101418&color=b9c8da)
![GitHub repo size](https://img.shields.io/github/repo-size/KromaKobra/kroma-player?style=for-the-badge&labelColor=101418&color=d3bfe6)
[![Ko-Fi donate](https://img.shields.io/badge/donate-kofi?style=for-the-badge&logo=ko-fi&logoColor=ffffff&label=ko-fi&labelColor=101418&color=f16061&link=https%3A%2F%2Fko-fi.com%2Fkromakobra)](https://ko-fi.com/kromakobra)
[![Discord invite](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fdiscordapp.com%2Fapi%2Finvites%2FsGgZGGggmE%3Fwith_counts%3Dtrue&query=approximate_member_count&style=for-the-badge&logo=discord&logoColor=ffffff&label=discord&labelColor=101418&color=96f1f1&link=https%3A%2F%2Fdiscord.gg%2sGgZGGggmE)](https://discord.gg/sGgZGGggmE)

</div>

A desktop library manager for the music you have locally.

Image comming soon!

<img width="1920" height="1032" alt="Image" src="" />

---

This README is intentially bare for now. Kroma Player is undergoing a major generation overhaul.

## Install

### Windows

Download from the [releases page](https://github.com/KromaKobra/kroma-player/releases). There are two downloads:

- **`KromaPlayerSetup-<version>-x86_64.exe`**, the installer. Use this one. It installs for your user only, into `%LOCALAPPDATA%\Programs\Kroma Player`, so it needs no administrator rights. Installed copies update themselves.
- **`kroma-player-<version>-windows-x86_64.zip`**, a portable folder. Unzip it anywhere and run `kroma-player.exe`. A portable copy does not update itself: download the new zip and unpack it over the old folder.

The installer isn't code-signed yet, so Windows SmartScreen may say it "protected your PC". Click **More info**, then **Run anyway**.

#### ffmpeg

Kroma Player uses [ffmpeg](https://ffmpeg.org) to read and convert audio, and it doesn't ship with it. Install it once:

```
winget install Gyan.FFmpeg
```

Then sign out of Windows and back in, so programs see the new `PATH`. If you can't change `PATH`, put `ffmpeg.exe` and `ffprobe.exe` next to `kroma-player.exe` instead. Settings → System shows whether it was found.

#### Updates

Kroma Player checks for a new version once a day, and you can check yourself in Settings → System. When there is one, it asks first. **Back up first** opens the library backup. **Update and restart** downloads the update, checks it, installs it, and reopens the app.

When an update changes how your library is stored, Kroma Player first copies the library database aside. The copy is in `.data\upgrade-snapshots\` inside your vault, and the last three are kept. The copy doesn't include your music, so it doesn't replace a backup.

#### Where things are

| What | Where |
| --- | --- |
| Your library: music, database, settings | the vault folder you chose, e.g. `Music\venomvault` |
| Which vault to open | `%APPDATA%\Kroma\kord-config.json` |
| Log files | `%LOCALAPPDATA%\Kroma\logs\` |
| Copies taken before a library upgrade | `<vault>\.data\upgrade-snapshots\` |
| The app itself | `%LOCALAPPDATA%\Programs\Kroma Player` |

#### Uninstall

Uninstall Kroma Player from **Settings → Apps**, like any other app. This removes the app only. Your vault and your settings stay where they are.

### Linux

Download `kroma-player-<version>-x86_64.AppImage` from the releases page, make it executable, and run it. It updates itself the same way. Install ffmpeg with your package manager.
