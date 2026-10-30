# Nebula Terminal

A clean terminal tool that checks lists of usernames on public sites.

**Platforms:** Discord, Roblox, YouTube, X, Minecraft, Steam, GitHub, Kick, Bluesky, Linktree.

Pick a platform, pick a list (your own or generated), watch a live screen, then view what it found.

## Run

Python 3.8+ only. Nothing to install, one file:

    python3 nebula_terminal.py

Press Enter on the title screen, then use the arrow keys and Enter (or type a number).
`python3 nebula_terminal.py --view` shows everything found so far.

## Your own list

Put names, one per line, in `my_list.txt`, or drop `.txt` files into `lists/`,
or choose "My own file" in the list menu and drag a file in.

## What it does and doesn't do

- It sends only the public availability request to the site you picked. It uses no accounts, keys or tokens
  and sends nothing anywhere else.
- It never logs in and never claims a name for you.
- No proxies, no workarounds. When a site asks it to slow down, it waits and continues.
- Discord's check is strict: its pace starts slow, speeds up carefully and backs off after a pause.
- A name that looks free can still be unavailable (reserved, banned, or a rule the check can't see).
  Always confirm in the app before relying on it.
- Instagram, TikTok and PlayStation are not supported: they have no safe public check.

Use it for your own accounts and follow each platform's terms of service.
