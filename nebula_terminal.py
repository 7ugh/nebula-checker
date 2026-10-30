#!/usr/bin/env python3
"""Nebula Terminal - a clean terminal tool that checks lists of usernames on public sites.

Platforms: Discord, Roblox, YouTube, X, Minecraft, Steam, GitHub, Kick, Bluesky, Linktree.
Pick a platform, pick a list (yours or generated), watch a live screen, then view what it found.

  python3 nebula_terminal.py            menu (arrow keys + Enter, or type a number)
  python3 nebula_terminal.py --view     show everything found so far

What it does NOT do: it never logs in, never claims a name for you, uses no proxies and does not
get around rate limits - when a site asks it to slow down, it waits. It sends nothing anywhere
except the availability request to the site you picked. No accounts, keys or tokens are used.
A name that looks free can still be unavailable (reserved, banned...) - always confirm in the app.

Your own list: put names (one per line) in my_list.txt or in the lists/ folder, or choose
'My own file' in the list menu and drag a file in. Python 3.8+, nothing to install.
"""
from __future__ import annotations
import itertools, json, os, random, re, shutil, sqlite3, string, subprocess, sys, threading, time
import urllib.error, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "nebula_terminal.db"
MY_LIST, LIST_DIR = ROOT / "my_list.txt", ROOT / "lists"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
NAME_OK = re.compile(r"^[a-z0-9_.]{2,30}$")

# ---------------------------------------------------------------- checks
# each returns 'free' | 'taken' | 'invalid' | ('rate_limited', seconds_to_wait)
def http(url, method="GET", body=None, headers=None, limit=None):
    """Returns (status_code, text, retry_after_or_None). status 0 = network error."""
    req = urllib.request.Request(url, body, {"User-Agent": UA, **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, r.read(limit).decode("utf-8", "ignore"), None
    except urllib.error.HTTPError as e:
        try: text = e.read(limit).decode("utf-8", "ignore")
        except Exception: text = ""
        ra = e.headers.get("Retry-After")
        return e.code, text, float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
    except Exception: return 0, "", None

def status_check(url_fmt, taken=(200, 410), free=(404,)):
    """Existing name -> 200, missing -> 404. 403/429 = rate limited or blocked: wait, never bypass."""
    def check(n):
        code, _, ra = http(url_fmt.format(n=n), limit=2000)
        if code in taken: return "taken"
        if code in free: return "free"
        if code in (429, 403): return ("rate_limited", ra or 300.0)
        return ("rate_limited", 30.0)
    return check

def check_discord(n):
    """Discord's public sign-up availability check (no login)."""
    code, text, ra = http("https://discord.com/api/v9/unique-username/username-attempt-unauthed", "POST",
                          json.dumps({"username": n}).encode(), {"Content-Type": "application/json"})
    if code == 429:
        try: return ("rate_limited", float(json.loads(text).get("retry_after", ra or 30)))
        except Exception: return ("rate_limited", ra or 30.0)
    if code == 400: return "invalid"
    if code != 200: return ("rate_limited", 15.0)
    try: return "taken" if json.loads(text).get("taken") else "free"
    except Exception: return ("rate_limited", 15.0)

def check_roblox(n):
    """Roblox's public sign-up username validator (no login)."""
    code, text, ra = http(f"https://auth.roblox.com/v1/usernames/validate?birthday=2000-01-01&context=Signup&username={n}")
    if code == 429: return ("rate_limited", ra or 30.0)
    if code != 200: return ("rate_limited", 15.0)
    try: c = json.loads(text).get("code")
    except Exception: return ("rate_limited", 15.0)
    return "free" if c == 0 else "taken" if c == 1 else "invalid"

def check_minecraft(n):
    code, _, ra = http(f"https://api.mojang.com/users/profiles/minecraft/{n}")
    if code == 200: return "taken"
    if code in (404, 204): return "free"
    if code == 400: return "invalid"
    return ("rate_limited", ra or 60.0)

def check_steam(n):
    code, text, ra = http(f"https://steamcommunity.com/id/{n}")
    if code == 200: return "free" if "could not be found" in text else "taken"
    if code == 429: return ("rate_limited", ra or 120.0)
    return ("rate_limited", 30.0)

PLATS = {
    "discord": dict(title="Discord", check=check_discord, delay=20.0, workers=1, verify="Discord -> User Settings -> My Account -> Username"),
    "roblox": dict(title="Roblox", check=check_roblox, delay=1.5, workers=1, verify="roblox.com/signup, or Settings to rename"),
    "youtube": dict(title="YouTube (@handle)", check=status_check("https://www.youtube.com/@{n}"), delay=1.5, workers=2, verify="YouTube Studio -> Customization -> Handle"),
    "x": dict(title="X", check=status_check("https://x.com/{n}"), delay=3.0, workers=1, verify="X -> Settings -> Your account -> Username (X needs 5+ characters)"),
    "minecraft": dict(title="Minecraft", check=check_minecraft, delay=1.0, workers=1, verify="minecraft.net -> profile -> change name"),
    "steam": dict(title="Steam", check=check_steam, delay=1.5, workers=2, verify="Steam -> Edit Profile -> Custom URL"),
    "github": dict(title="GitHub", check=status_check("https://github.com/{n}"), delay=1.5, workers=2, verify="github.com -> Settings -> Change username"),
    "kick": dict(title="Kick", check=status_check("https://kick.com/api/v2/channels/{n}"), delay=1.5, workers=2, verify="Kick -> Settings -> Profile"),
    "bluesky": dict(title="Bluesky", check=status_check("https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={n}.bsky.social", free=(400,)),
                    delay=1.5, workers=2, verify="Bluesky -> Settings -> Change handle"),
    "linktree": dict(title="Linktree", check=status_check("https://linktr.ee/{n}"), delay=1.5, workers=2, verify="Linktree -> Settings -> Username"),
}

# ---------------------------------------------------------------- name generators
L, DG = string.ascii_lowercase, string.digits
V, CO = "aeiou", "bcdfghjklmnprstvwz"
def _prod(al, n): return ["".join(t) for t in itertools.product(al, repeat=n)]
def _pron(pat): return ["".join(t) for t in itertools.product(*[(V if c == "v" else CO) for c in pat])]
def _semi(n):
    return [c[:i] + sym + c[i:] for c in _prod(L, n) for sym in "_." for i in range(n + 1) if not (sym == "." and i in (0, n))]
def _random_alnum(n, k): return list({"".join(random.choice(L + DG) for _ in range(n)) for _ in range(k)})

GENERATORS = [("2 letters", lambda: _prod(L, 2)), ("3 letters", lambda: _prod(L, 3)), ("3 numbers", lambda: _prod(DG, 3)),
              ("3 letters/numbers", lambda: _prod(L + DG, 3)), ("4 letters (all)", lambda: _prod(L, 4)), ("4 numbers", lambda: _prod(DG, 4)),
              ("4 letters/numbers (random 50,000)", lambda: _random_alnum(4, 50000)), ("3 letters + _ or .", lambda: _semi(3)),
              ("pronounceable 4 (cvcv, vcvc...)", lambda: _pron("cvcv") + _pron("vcvc") + _pron("cvvc") + _pron("cvcc")),
              ("pronounceable 5 (cvcvc)", lambda: _pron("cvcvc")), ("5 letters/numbers (random 50,000)", lambda: _random_alnum(5, 50000))]

# ---------------------------------------------------------------- terminal helpers
def rgb(r, g, b, bg=False): return f"\033[{48 if bg else 38};2;{r};{g};{b}m"
R, B = "\033[0m", "\033[1m"
ACC, ACC2, OK, WARN, MUTE, TXT = rgb(139, 92, 246), rgb(196, 181, 253), rgb(52, 211, 153), rgb(251, 191, 36), rgb(113, 113, 130), rgb(236, 236, 242)
SELBG = rgb(36, 30, 58, True)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
def vis(s): return len(ANSI.sub("", s))
def pad(s, w): return s + " " * max(w - vis(s), 0)
def width(): return max(shutil.get_terminal_size((80, 24)).columns, 40)
def bw(): return min(width() - 4, 96)
def clear(): sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()
def now(): return datetime.now().astimezone().isoformat(timespec="seconds")

def grad(text, c1=(139, 92, 246), c2=(52, 211, 153)):
    n = max(len(text) - 1, 1)
    return "".join(rgb(*(int(c1[k] + (c2[k] - c1[k]) * i / n) for k in range(3))) + ch for i, ch in enumerate(text)) + R

def box(lines, title="", w=None, color=MUTE):
    w = w or bw(); inner = w - 4
    top = f"{color}╭─{R}" + (f" {ACC2}{B}{title}{R} " if title else "") + f"{color}{'─' * max(w - 4 - (vis(title) + 2 if title else 0), 0)}─╮{R}"
    return ["  " + l for l in [top] + [f"{color}│{R} {pad(l, inner)} {color}│{R}" for l in lines] + [f"{color}╰{'─' * (w - 2)}╯{R}"]]

def header(crumb=""):
    w = bw(); left = f"{ACC}◆{R} " + grad("NEBULA TERMINAL", (167, 139, 250), (52, 211, 153)); right = f"{MUTE}{crumb}{R}"
    print(f"\n  {left}{' ' * max(w - vis(left) - vis(right), 2)}{right}\n  {MUTE}{'─' * w}{R}\n")

FONT = {"N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"], "E": ["█████", "█    ", "████ ", "█    ", "█████"],
        "B": ["████ ", "█   █", "████ ", "█   █", "████ "], "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
        "L": ["█    ", "█    ", "█    ", "█    ", "█████"], "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
        "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "], "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
        "I": ["█████", "  █  ", "  █  ", "  █  ", "█████"], "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"]}
SHADES = [(124, 92, 246), (139, 92, 246), (150, 110, 246), (167, 139, 250), (186, 160, 252)]

def splash():
    """Title screen. Waits for Enter."""
    cols, rows = shutil.get_terminal_size((80, 24))
    big = lambda word: [" ".join(FONT[c][r] for c in word) for r in range(5)]
    lines = big("NEBULA") + [""] + big("TERMINAL")
    out = ["\033[2J\033[H" + "\n" * max((rows - len(lines) - 8) // 2, 1)]
    for i, l in enumerate(lines): out.append(f"{rgb(*SHADES[i % 5])}{l.center(cols)}{R}\n")
    out.append(f"\n{ACC}{'username checker'.center(cols)}{R}\n\n{ACC}{B}{'> Press Enter'.center(cols)}{R}\n")
    sys.stdout.write("".join(out)); sys.stdout.flush()
    try: input()
    except EOFError: pass
    clear()

def read_key():
    import termios, tty
    fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd); return os.read(fd, 8).decode(errors="ignore")
    finally: termios.tcsetattr(fd, termios.TCSADRAIN, old)

def menu(crumb, title, items, cols=1, hint=""):
    """items = (label, note, disabled_reason). Arrow keys + Enter, or a number then Enter, q = back."""
    if not sys.stdin.isatty():
        clear(); header(crumb); print(f"  {B}{title}{R}")
        for i, (l, n, _) in enumerate(items, 1): print(f"   {i:>2}  {l}  {n}")
        raw = input("\n  Number: ").strip()
        return int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= len(items) else None
    rows = -(-len(items) // cols); sel = next((i for i, it in enumerate(items) if not it[2]), 0); typed = ""; colw = (bw() - 4) // cols
    while True:
        clear(); header(crumb); body = []
        for r in range(rows):
            line = ""
            for c in range(cols):
                i = c * rows + r
                if i >= len(items): line += " " * colw; continue
                label, note, why = items[i]; lab = f"{i + 1:>2}  {label}"; gap = " " * max(colw - len(lab) - len(note) - 4, 1)
                if i == sel: cell = f"{SELBG}{ACC}▌{TXT}{B} {lab}{R}{SELBG}{gap}{MUTE}{note} {R}"
                elif why: cell = f"{MUTE}  {lab}{gap}{note} {R}"
                else: cell = f"{TXT}  {lab}{R}{gap}{MUTE}{note} {R}"
                line += pad(cell, colw)
            body.append(line)
        for l in box(body, title): print(l)
        why = items[sel][2]
        print(f"\n  {WARN + why + R if why else MUTE + hint + R}\n")
        print(f"  {MUTE}↑ ↓ ← →  move     Enter  select     number  jump     q  back{R}   {ACC2}{typed}{R}")
        k = read_key()
        if k in ("\r", "\n"):
            if typed.isdigit() and 1 <= int(typed) <= len(items): sel = int(typed) - 1
            typed = ""
            if not items[sel][2]: return sel
        elif k in ("q", "Q", "\x03"): return None
        elif k == "\x1b[A": sel = (sel - 1) % len(items)
        elif k == "\x1b[B": sel = (sel + 1) % len(items)
        elif k == "\x1b[C" and cols > 1 and sel + rows < len(items): sel += rows
        elif k == "\x1b[D" and cols > 1 and sel - rows >= 0: sel -= rows
        elif k.isdigit(): typed = (typed + k)[-2:]
        elif k in ("\x7f", "\b"): typed = typed[:-1]

# ---------------------------------------------------------------- data
def connect():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS results (platform TEXT, username TEXT, status TEXT, checked_at TEXT, PRIMARY KEY (platform, username))")
    db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v REAL)")
    db.commit(); return db

def meta_get(db, k, default=0.0):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone(); return r[0] if r else default
def meta_set(db, k, v): db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v)); db.commit()

def load(path):
    seen, out = set(), []
    for line in Path(path).read_text().splitlines():
        n = line.strip().lower()
        if n and not n.startswith(("#", "=")) and n not in seen and NAME_OK.match(n) and n[0] != "." and n[-1] != "." and ".." not in n:
            seen.add(n); out.append(n)
    return out

def count_lines(path):
    try: return f"{sum(1 for l in Path(path).open() if l.strip() and l[0] not in '#=') :,} names"
    except Exception: return ""

def resolve_path(raw):
    p = Path(raw.strip().strip("'\"").replace("\\ ", " ")).expanduser(); return p if p.is_file() else None

# ---------------------------------------------------------------- menus
def pick_platform():
    keys = list(PLATS)
    i = menu("platform", "Choose a platform", [(PLATS[k]["title"], "ready", None) for k in keys], cols=2, hint="Each site is checked at its own safe pace.")
    return keys[i] if i is not None else None

def pick_names():
    LIST_DIR.mkdir(exist_ok=True)
    files = sorted(LIST_DIR.glob("*.txt"))
    items = [(f"lists/{p.name}", count_lines(p), None) for p in files]
    mine = len(load(MY_LIST)) if MY_LIST.exists() else 0
    items += [("my_list.txt", f"{mine:,} names" if mine else "empty - add names to the file", None),
              ("My own file...  (type the path or drag it in)", "", None), ("Generate names...", "", None)]
    i = menu("list", "Choose a list", items, hint="Lists come from the lists/ folder, my_list.txt, a file you drag in, or a generator.")
    if i is None: return None, ""
    if i < len(files): return load(files[i]), files[i].name
    if i == len(files): return (load(MY_LIST) if MY_LIST.exists() else None), "my_list.txt"
    if i == len(files) + 1:
        p = resolve_path(input("\n  File path (or drag it here): ")); return (load(p), p.name) if p else (None, "")
    g = menu("list > generate", "Generate names", [(t, "", None) for t, _ in GENERATORS], cols=1)
    if g is None: return None, ""
    print(f"\n  {MUTE}generating...{R}")
    return [n for n in dict.fromkeys(GENERATORS[g][1]()) if NAME_OK.match(n) and n[0] != "." and n[-1] != "." and ".." not in n], GENERATORS[g][0]

# ---------------------------------------------------------------- run screen
def bar(frac, w):
    fill = int(frac * w)
    return (grad("█" * fill) + f"{MUTE}{'░' * (w - fill)}{R}") if fill else f"{MUTE}{'░' * w}{R}"

def eta(sec):
    if sec <= 0 or sec > 86400 * 30: return "--"
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {int(sec % 60):02d}s"

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

def run(db, plat, names, list_name=""):
    P = PLATS[plat]
    done = {r[0] for r in db.execute("SELECT username FROM results WHERE platform=?", (plat,))}
    todo = [n for n in names if n not in done]
    if not todo:
        print(f"\n  {WARN}Nothing new - every name in that list was already tried on {P['title']}.{R}"); input("  Enter to continue "); return
    st = {"tried": 0, "taken": 0, "free": 0, "invalid": 0, "pause": meta_get(db, f"{plat}_block"), "last": "", "t0": time.time(), "pend": 0, "hits": []}
    adapt = plat == "discord"     # Discord's check is strict: start slow, speed up carefully, back off after a pause
    if adapt: st["interval"], st["ok"] = meta_get(db, "discord_interval", 20.0), 0
    lock, stop, it = threading.Lock(), threading.Event(), iter(todo)

    def worker():
        while not stop.is_set():
            with lock: name = next(it, None)
            if name is None: return
            while not stop.is_set():
                wait = st["pause"] - time.time()
                if wait > 0: stop.wait(wait)
                res = P["check"](name)
                if isinstance(res, tuple):
                    with lock:
                        st["pause"] = max(st["pause"], time.time() + min(max(res[1] or 30, 5), 3600)); meta_set(db, f"{plat}_block", st["pause"])
                        if adapt: st["interval"] = min(st["interval"] * 2, 300.0); st["ok"] = 0; meta_set(db, "discord_interval", st["interval"])
                    continue
                break
            if stop.is_set(): return
            with lock:
                db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?)", (plat, name, res, now()))
                st["pend"] += 1
                if st["pend"] >= 20: db.commit(); st["pend"] = 0
                st["tried"] += 1; st["last"] = name; st[res] = st.get(res, 0) + 1
                if res == "free": st["hits"].append(name)
                if adapt:
                    st["ok"] += 1
                    if st["ok"] >= 25: st["ok"] = 0; st["interval"] = max(st["interval"] * 0.9, 4.0); meta_set(db, "discord_interval", st["interval"])
            gap = st["interval"] if adapt else P["delay"]
            if gap: stop.wait(gap)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(P["workers"])]
    for t in threads: t.start()
    clear()
    try:
        while any(t.is_alive() for t in threads):
            w = bw(); el = max(time.time() - st["t0"], 1); rate = st["tried"] / el; left = st["pause"] - time.time()
            frac = st["tried"] / len(todo); tw = (w - 4) // 4
            cells = [("FREE", f"{st['free']}", OK), ("TAKEN", f"{st['taken']:,}", TXT), ("SPEED", f"{rate:.1f}/s", ACC2), ("ETA", eta((len(todo) - st["tried"]) / rate if rate else 0), TXT)]
            spin = SPIN[int(time.time() * 10) % len(SPIN)]
            status = (f"{WARN}⏸  {P['title']} asked for a pause · {int(left) // 60:02d}:{int(left) % 60:02d} left · continues by itself{R}" if left > 0
                      else f"{ACC}{spin}{R} {MUTE}checking{R} {TXT}{st['last']}{R}" + (f"   {MUTE}pace: 1 check / {st['interval']:.0f}s, adjusts itself{R}" if adapt else ""))
            live = [f"{bar(frac, w - 28)}  {TXT}{B}{frac * 100:5.1f}%{R}  {MUTE}{st['tried']:,}/{len(todo):,}{R}", "",
                    "".join(pad(f"{MUTE}{a}{R}", tw) for a, _, _ in cells), "".join(pad(f"{c}{B}{b}{R}", tw) for _, b, c in cells), "", status]
            hits = st["hits"][-24:]
            found = ["   ".join(f"{OK}{n:<10}{R}" for n in hits[i:i + 4]) for i in range(0, 24, 4)] if hits else [f"{MUTE}none yet - free names appear here{R}"]
            right = f"{P['title']} · {list_name}"
            out = ["\033[H\n", f"  {ACC}◆{R} {grad('NEBULA TERMINAL', (167, 139, 250), (52, 211, 153))}{' ' * max(w - 15 - len(right) - 2, 2)}{MUTE}{right}{R}\033[K\n",
                   f"  {MUTE}{'─' * w}{R}\033[K\n\033[K\n"]
            out += [l + "\033[K\n" for l in box(live, "Live", w)] + ["\033[K\n"]
            out += [l + "\033[K\n" for l in box(found, f"Free names · {len(st['hits'])}", w, OK if hits else MUTE)]
            out.append(f"\033[K\n  {MUTE}Ctrl-C  stop · everything is saved and resumes next time{R}\033[K\n\033[J")
            sys.stdout.write("".join(out)); sys.stdout.flush(); time.sleep(0.25)
    except KeyboardInterrupt:
        stop.set()
    finally:
        for t in threads: t.join(timeout=2)
        with lock: db.commit()
    print(f"\n  {OK}Done: tried {st['tried']:,} · taken {st['taken']:,} · free {st['free']}{R}")
    time.sleep(1)

# ---------------------------------------------------------------- results
def view(db, plat=None):
    """Everything found so far, per platform. ← → switch platform, c copies, q quits."""
    while True:
        counts = dict(db.execute("SELECT platform, COUNT(*) FROM results WHERE status='free' GROUP BY platform"))
        clear(); header("results")
        if not counts: print(f"  {MUTE}Nothing found yet.{R}\n"); return
        keys = sorted(counts, key=lambda k: -counts[k]); plat = plat if plat in counts else keys[0]
        title = lambda k: PLATS.get(k, {"title": k})["title"]
        print("  " + "  ".join((f"{SELBG}{ACC2}{B} {title(k)} · {counts[k]} {R}" if k == plat else f"{MUTE} {title(k)} · {counts[k]} {R}") for k in keys) + "\n")
        rows = [r[0] for r in db.execute("SELECT username FROM results WHERE platform=? AND status='free' ORDER BY checked_at", (plat,))]
        grid = ["   ".join(f"{OK}{n:<10}{R}" for n in rows[i:i + 5]) for i in range(0, len(rows), 5)]
        for l in box(grid[:18] + ([f"{MUTE}... and {len(rows) - 90} more in the saved file{R}"] if len(rows) > 90 else []), f"{len(rows)} free on {title(plat)}", color=OK): print(l)
        out = ROOT / f"Free_{plat}.txt"; out.write_text("\n".join(rows) + "\n")
        v = PLATS.get(plat, {}).get("verify", "")
        print(f"\n  {MUTE}{('claim: ' + v) if v else ''}\n  saved to {out.name}{R}\n\n  {MUTE}← →  switch platform     c  copy all     q  quit{R}")
        k = read_key() if sys.stdin.isatty() else "q"
        if k == "c" and sys.platform == "darwin": subprocess.run(["pbcopy"], input="\n".join(rows).encode())
        elif k == "\x1b[C": plat = keys[(keys.index(plat) + 1) % len(keys)]
        elif k == "\x1b[D": plat = keys[(keys.index(plat) - 1) % len(keys)]
        elif k in ("q", "Q", "\x03", "\r"): return

def main():
    db = connect()
    if "--view" in sys.argv: view(db); return
    splash()
    try:
        while True:
            plat = pick_platform()
            if not plat: break
            names, label = pick_names()
            if not names: continue
            order = menu(f"{PLATS[plat]['title']} > {len(names):,} names", "Order", [("As listed", "", None), ("Shuffled", "", None)])
            if order is None: continue
            if order == 1: random.shuffle(names)
            run(db, plat, names, f"{label} · {len(names):,} names"); view(db, plat)
    except KeyboardInterrupt: pass
    clear()

if __name__ == "__main__": main()
