# Changelog

What changed in each version of Starholm, newest first. The first paragraph under a version is what the app's update
notice shows (`packbuilder/app_manifest.py` reads it), so keep it to one or two short sentences.

## 1.0.6

The Starholm icon is now one colour, and on a Mac the window opens on the screen where you closed it.

- With two screens, Restart now used to open the window on whichever screen macOS chose. Starholm now keeps the
  window's place when it closes and opens it there again, as long as that screen is still connected.
- A screen reader now hears that the update card is in the left panel, above Settings. It still said the bottom-left
  corner.

## 1.0.5

The notice that a new version is ready is now a small card above Settings: one line, with Later and Restart now.

- It no longer repeats what changed or explains the restart. It is as wide as the side panel's buttons and sits at the
  bottom of the panel.
- On a Mac, the Starholm menu has Settings… (⌘,), which opens Settings from anywhere in the app. Check for Updates…
  in the same menu now works too.
- The app says "this computer" where it said "this Mac", so the same words fit on Windows.

## 1.0.4

On a Mac, Starholm no longer asks to find devices on your local network. It never needed that access.

- Starting the app made a name lookup of the computer's own address. On a Mac that counts as using the local
  network, so macOS asked for permission and held the start until you answered. The lookup is gone. Whatever you
  chose before, Starholm works the same.
- Check for updates and Refresh now in Settings keep their size while they work, so the row no longer shifts.

## 1.0.3

The library download is about a third of the size it was, and a dropped connection no longer stops it.

- The guides and the library now download compressed, and Starholm unpacks each file as it arrives. What ends up on
  your computer is the same as before, and so is the space it takes.
- When the connection drops partway through a file, or the server has a moment's trouble, Starholm waits a moment
  and carries on from where that file stopped. It tries up to four times, waiting a little longer each time, while the other files keep downloading.
- The progress bar keeps everything already downloaded, and the time left allows for the wait.
- The download stops with an error and a Resume button only when the last try fails too.
- With "Download updates automatically" on, Starholm checks for a new version when it opens and every hour while it
  is open, downloads it in the background and shows a small notice in the bottom-left corner when it is ready.
- After an update nothing of the old version is left: no old copy, no download. If the new version doesn't start,
  the old one is put back, and the automatic check doesn't download that version again.
- Windows: updates install by themselves after Restart now, into the same folder, with one entry in Apps; a failed
  update puts the previous version back.

## 1.0.2

Library downloads are faster and show a realistic time left. Starholm can now update itself.

- The upgrade's guides and library download together, several files at once, instead of one file at a time.
- The time left counts everything still to download, at the speed of the last half minute. Until there's enough to
  go on, it says it's working out the time left.
- Check for updates (Settings › Status, the Upgrade page, and the Starholm menu on a Mac) looks for a newer Starholm as
  well as newer guides and library.
- "Download updates automatically" in Settings › Status is off unless you turn it on. When it's on, Starholm checks
  when it opens and once a day.
- When a new version is downloaded and checked, Starholm offers Restart now and Later. Restart now replaces the app
  where it is and opens the new one. Your chats, notes and library stay as they are.

## 1.0.1

Released November 25, 2026. The version Starholm launched with.
