#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GLÜCKSGRUBE – Multiplayer-Slot-Roguelike mit Lobby, Beitritts-Code und Sabotage.

Starten:   python Glucksgrube.py
Aufrufen:  http://localhost:5000   (Freunde im selben Netz: http://<deine-IP>:5000)

Spielidee
    Jede Runde hast du ein paar Hebelzüge am Automaten. Am Rundenende kommt der
    Eintreiber und will seine Schulden. Wer nicht zahlen kann, verliert ein Leben.
    Dazwischen kaufst du im Shop Glücksbringer (bauen deinen Automaten um) und
    Sabotage-Items gegen deine Mitspieler. Letzter Überlebender gewinnt.

Aufbau
    Glucksgrube.py    Server (Flask), Spiellogik, Speicherung
    index.html        Oberfläche
    static/style.css  Design
    static/app.js     Client-Logik
    spielstand.json   Spielerkonten (wird automatisch angelegt)

Einstellungen per Umgebungsvariable: HOST, PORT, GRUBE_DATA (Pfad der JSON-Datei).
"""

import functools
import json
import math
import os
import random
import re
import secrets
import socket
import tempfile
import threading
import time
from collections import OrderedDict

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.environ.get("GRUBE_DATA", os.path.join(BASE_DIR, "spielstand.json"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))

MAX_PLAYERS = 6
MAX_LOBBIES = 100
START_COINS = 25
START_LIVES = 2
PITY_COINS = 15            # Trostpflaster, wenn der Eintreiber leer ausgeht
BASE_SPINS = 5
MAX_SPINS = 12
BASE_HOLDS = 1
BASE_GAMBLES = 1
GAMBLE_BASE_ODDS = 0.45
DEBT_BASE = 18
DEBT_GROWTH = 1.40
POT_START = 40
POT_PER_SPIN = 4
BIG_WIN = 100
MEGA_WIN = 300
SPIN_BASE_SECONDS = 20
SPIN_PER_SPIN_SECONDS = 9
GRACE_SECONDS = 7
COLLECT_SECONDS = 9
SHOP_SECONDS = 45
STARTER_SECONDS = 30
ABSENT_SECONDS = 150
ROUND_CHOICES = (6, 10, 15)
EMOTES = ("😂", "😱", "👏", "💀", "🤑", "😈", "🍀", "🔥")
MAX_SABOTAGES = 3
PRICE_GROWTH = 1.18         # Preise steigen pro Runde um 18 % (Glücksbringer, Sabotage, Mischen)
STACK_GROWTH = 0.45         # jede weitere Kopie desselben Glücksbringers +45 %
SELL_RATE = 0.4             # Verkaufen bringt 40 % des aktuellen Preises
REROLL_BASE = 6
LEGENDARY_FROM = 4          # legendäre Glücksbringer erscheinen ab dem Shop nach Runde 4
EXTRA_OFFER_FROM = 6        # ab Runde 6 gibt es 5 statt 4 Angebote
EVENT_EVERY = 3             # jede 3. Runde ist eine Ereignis-Runde
INTEREST_CAP = 60
SAVE_CAP = 0.6

LINE_MULT = {3: 1, 4: 3, 5: 10}
SKULL_PEN = {3: 10, 4: 30, 5: 75}

SYMBOLS = OrderedDict([
    ("cherry", dict(emoji="🍒", name="Kirsche", value=10, weight=22)),
    ("lemon", dict(emoji="🍋", name="Zitrone", value=10, weight=22)),
    ("clover", dict(emoji="🍀", name="Kleeblatt", value=20, weight=16)),
    ("bell", dict(emoji="🔔", name="Glocke", value=35, weight=12)),
    ("diamond", dict(emoji="💎", name="Diamant", value=75, weight=8)),
    ("seven", dict(emoji="7️⃣", name="Glückssieben", value=150, weight=5)),
    ("wild", dict(emoji="✨", name="Joker", value=0, weight=0)),
    ("skull", dict(emoji="💀", name="Pechsymbol", value=0, weight=8)),
])
REELS = 5
ROWS = 3
LINES = [
    [(0, c) for c in range(REELS)],
    [(1, c) for c in range(REELS)],
    [(2, c) for c in range(REELS)],
    [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)],
    [(2, 0), (1, 1), (0, 2), (1, 3), (2, 4)],
]

# Glücksbringer: fx wird pro Stapel angewendet.
#   mult{sym}   Auszahlung ×x^n        weight{sym}  Gewicht +x*n
#   line ×x^n   alle Gewinne           spins/holds/gambles +x*n
#   flat +x*n   Münzen pro Dreh        interest +x*n Zinsen auf Restmünzen
#   skull_pen ×x^n   Pech-Strafen      debt ×x^n    Schulden
#   streak +x*n Bonus je Gewinn-Serie  gamble_odds +x*n   pot ×x^n  Jackpot
#   lives +x*n  sofort beim Kauf       lenmult{3|4|5} ×x^n  Linien dieser Länge
#   consolation +x*n  Münzen bei Drehs ohne Gewinn      first/last ×x^n  erster/letzter Dreh
#   discount ×x^n  Shop-Preise         guard ×x^n   Diebstahl     interest_cap +x*n
#   save +x*n  Chance, beim Eintreiber kein Leben zu verlieren    skull_pay +x*n  Pechlinien zahlen
CHARMS = OrderedDict([
    # ---- Gewöhnlich
    ("clover", dict(emoji="🍀", name="Kleeblatt-Amulett", rarity="common", price=16, max=4,
                    desc="Kleeblätter zahlen ×1,5 und erscheinen öfter.",
                    fx=dict(mult={"clover": 1.5}, weight={"clover": 4}))),
    ("cherry", dict(emoji="🍒", name="Kirschkorb", rarity="common", price=13, max=4,
                    desc="Kirschen zahlen ×2 und erscheinen öfter.",
                    fx=dict(mult={"cherry": 2.0}, weight={"cherry": 5}))),
    ("lemon", dict(emoji="🍋", name="Zitronenpresse", rarity="common", price=13, max=4,
                   desc="Zitronen zahlen ×2 und erscheinen öfter.",
                   fx=dict(mult={"lemon": 2.0}, weight={"lemon": 5}))),
    ("bell", dict(emoji="🔔", name="Goldglocke", rarity="common", price=19, max=4,
                  desc="Glocken zahlen ×2 und erscheinen öfter.",
                  fx=dict(mult={"bell": 2.0}, weight={"bell": 3}))),
    ("pouch", dict(emoji="👛", name="Münzbeutel", rarity="common", price=16, max=4,
                   desc="+4 Münzen bei jedem Dreh – egal ob Gewinn oder nicht.",
                   fx=dict(flat=4))),
    ("ward", dict(emoji="🧿", name="Pechbann", rarity="common", price=19, max=3,
                  desc="Weniger 💀 und 30 % weniger Strafe bei Pechlinien.",
                  fx=dict(weight={"skull": -3}, skull_pen=0.7))),
    ("grip", dict(emoji="✋", name="Haltegriff", rarity="common", price=19, max=3,
                  desc="+1 Walze halten pro Runde.",
                  fx=dict(holds=1))),
    ("dice", dict(emoji="🎲", name="Falschspieler", rarity="common", price=21, max=3,
                  desc="+1 Risiko-Versuch pro Runde und bessere Chancen (+5 %).",
                  fx=dict(gambles=1, gamble_odds=0.05))),
    ("fishes", dict(emoji="🐟", name="Kleine Fische", rarity="common", price=15, max=4,
                    desc="Alle 3er-Linien zahlen ×1,4.",
                    fx=dict(lenmult={3: 1.4}))),
    ("salad", dict(emoji="🥗", name="Obstsalat", rarity="common", price=15, max=3,
                   desc="🍒 und 🍋 zahlen ×1,4 und erscheinen etwas öfter.",
                   fx=dict(mult={"cherry": 1.4, "lemon": 1.4}, weight={"cherry": 2, "lemon": 2}))),
    ("cookie", dict(emoji="🥠", name="Glückskeks", rarity="common", price=17, max=3,
                    desc="+1 Walze halten und +1 Risiko-Versuch pro Runde.",
                    fx=dict(holds=1, gambles=1))),
    ("coin", dict(emoji="🪙", name="Trostmünze", rarity="common", price=14, max=4,
                  desc="+5 Münzen bei jedem Dreh ohne Linien-Gewinn.",
                  fx=dict(consolation=5))),
    ("coupon", dict(emoji="🏷️", name="Rabattmarke", rarity="common", price=18, max=3,
                    desc="Alle Shop-Preise −12 % (auch Sabotage und Mischen).",
                    fx=dict(discount=0.88))),
    ("vault", dict(emoji="🔐", name="Tresor", rarity="common", price=16, max=3,
                   desc="Diebe erbeuten 50 % weniger, Zins-Obergrenze +40.",
                   fx=dict(guard=0.5, interest_cap=40))),
    # ---- Selten
    ("diamond", dict(emoji="💎", name="Diamantschleifer", rarity="rare", price=24, max=3,
                     desc="Diamanten zahlen ×1,6 und erscheinen öfter.",
                     fx=dict(mult={"diamond": 1.6}, weight={"diamond": 3}))),
    ("seven", dict(emoji="7️⃣", name="Glückszahl", rarity="rare", price=26, max=3,
                   desc="Sieben zahlen ×2 und erscheinen öfter (mehr Jackpots!).",
                   fx=dict(mult={"seven": 2.0}, weight={"seven": 2}))),
    ("spin", dict(emoji="🎟️", name="Extra-Hebel", rarity="rare", price=34, max=3,
                  desc="+1 Dreh pro Runde.",
                  fx=dict(spins=1))),
    ("piggy", dict(emoji="🐷", name="Sparschwein", rarity="rare", price=21, max=3,
                   desc="+15 % Zinsen auf deine Restmünzen nach dem Eintreiber (gedeckelt, steigt mit der Runde).",
                   fx=dict(interest=0.15))),
    ("hat", dict(emoji="🎩", name="Zauberhut", rarity="rare", price=29, max=3,
                 desc="Joker ✨ erscheinen auf den Walzen und ersetzen jedes Symbol.",
                 fx=dict(weight={"wild": 6}))),
    ("streak", dict(emoji="🔥", name="Heißer Lauf", rarity="rare", price=26, max=3,
                    desc="+15 % auf alle Gewinne für jeden Gewinn-Dreh in Folge (max. +90 %).",
                    fx=dict(streak=0.15))),
    ("hunter", dict(emoji="🎯", name="Potjäger", rarity="rare", price=24, max=3,
                    desc="Jackpot-Treffer zahlen +50 % mehr aus dem Topf.",
                    fx=dict(pot=1.5))),
    ("map", dict(emoji="🗺️", name="Schatzkarte", rarity="rare", price=25, max=3,
                 desc="💎 und 7️⃣ erscheinen öfter, 💎 zahlen ×1,2.",
                 fx=dict(weight={"diamond": 2, "seven": 1}, mult={"diamond": 1.2}))),
    ("fiver", dict(emoji="🃏", name="Fünferpack", rarity="rare", price=27, max=3,
                   desc="5er-Linien zahlen ×2.",
                   fx=dict(lenmult={5: 2.0}))),
    ("early", dict(emoji="⚡", name="Kaltstart", rarity="rare", price=23, max=3,
                   desc="Der erste Dreh jeder Runde zahlt ×2.",
                   fx=dict(first=2.0))),
    ("finale", dict(emoji="🌙", name="Finale", rarity="rare", price=25, max=3,
                    desc="Der letzte Dreh jeder Runde zahlt ×2,5.",
                    fx=dict(last=2.5))),
    # ---- Episch
    ("angel", dict(emoji="😇", name="Schutzengel", rarity="epic", price=42, max=3, sell=False,
                   desc="Sofort +1 Leben. (Nicht verkaufbar.)",
                   fx=dict(lives=1))),
    ("glove", dict(emoji="🧤", name="Goldene Hand", rarity="epic", price=37, max=3,
                   desc="Alle Gewinne ×1,25.",
                   fx=dict(line=1.25))),
    ("shark", dict(emoji="🦈", name="Schuldenstreicher", rarity="epic", price=34, max=3,
                   desc="Der Eintreiber will 12 % weniger.",
                   fx=dict(debt=0.88))),
    ("cat", dict(emoji="🐈‍⬛", name="Katzenleben", rarity="epic", price=40, max=2,
                 desc="35 % Chance, beim Eintreiber trotz Pleite kein Leben zu verlieren.",
                 fx=dict(save=0.35))),
    ("grave", dict(emoji="⚰️", name="Totengräber", rarity="epic", price=38, max=2,
                   desc="Pechlinien 💀 zahlen dir das Doppelte ihrer Strafe aus, statt Münzen zu kosten.",
                   fx=dict(skull_pay=2))),
    # ---- Legendär (erst ab Runde 4 im Shop)
    ("crown", dict(emoji="👑", name="Midas-Krone", rarity="legendary", price=70, max=2,
                   desc="Alle Gewinne ×1,4 und +6 Münzen pro Dreh.",
                   fx=dict(line=1.4, flat=6))),
    ("pact", dict(emoji="😈", name="Teufelspakt", rarity="legendary", price=55, max=2,
                  desc="Alle Gewinne ×2 – aber der Eintreiber will 20 % mehr.",
                  fx=dict(line=2.0, debt=1.2))),
    ("hourglass", dict(emoji="⏳", name="Zeitkristall", rarity="legendary", price=75, max=2,
                       desc="+2 Dreh pro Runde.",
                       fx=dict(spins=2))),
    ("star", dict(emoji="🌟", name="Sternschnuppe", rarity="legendary", price=60, max=2,
                  desc="Joker ✨ erscheinen öfter und Jackpots zahlen doppelt.",
                  fx=dict(weight={"wild": 4}, pot=2.0))),
])
RARITY_ORDER = ("common", "rare", "epic", "legendary")


def rarity_weights(round_no):
    """Je später die Runde, desto öfter tauchen starke Glücksbringer auf."""
    r = max(0, round_no)
    return {"common": 10.0, "rare": 6.0 + 0.45 * r, "epic": 2.5 + 0.4 * r,
            "legendary": (1.0 + 0.35 * (r - LEGENDARY_FROM)) if r >= LEGENDARY_FROM else 0.0}


SABOTAGE = OrderedDict([
    ("thief", dict(emoji="🧲", name="Taschendieb", price=24, target=True,
                   desc="Klaut einem Gegner sofort Münzen (15 + 15 % seines Vermögens, steigt mit der Runde).")),
    ("frost", dict(emoji="🧊", name="Frostbeule", price=29, target=True,
                   desc="Der Gegner hat nächste Runde 1 Dreh weniger.")),
    ("curse", dict(emoji="🕷️", name="Pechfluch", price=21, target=True,
                   desc="Beim Gegner erscheinen nächste Runde 2,5× so viele 💀.")),
    ("tax", dict(emoji="📜", name="Steuerprüfer", price=27, target=True,
                 desc="Der Eintreiber will vom Gegner nächste Runde 20 % mehr.")),
    ("chain", dict(emoji="⛓️", name="Kettenschloss", price=20, target=True,
                   desc="Der Gegner kann nächste Runde keine Walzen halten.")),
    ("mirror", dict(emoji="🪞", name="Zauberspiegel", price=26, target=False,
                    desc="Schützt dich: Der nächste Angriff trifft den Angreifer.")),
    ("bribe", dict(emoji="🤝", name="Bestechung", price=30, target=False,
                   desc="Der Eintreiber will nächste Runde 25 % weniger von dir.")),
])

# Ereignisse: jede EVENT_EVERY-te Runde für alle am Tisch (werden im Shop davor angekündigt)
EVENTS = OrderedDict([
    ("gold", dict(emoji="💰", name="Goldrausch", desc="Alle Gewinne ×1,5.", fx=dict(line=1.5))),
    ("plague", dict(emoji="🦠", name="Pestnacht", desc="Viel mehr 💀 auf den Walzen.", fx=dict(weight={"skull": 8}))),
    ("bonus", dict(emoji="🎁", name="Spendierlaune", desc="+2 Dreh für alle.", fx=dict(spins=2))),
    ("drought", dict(emoji="🌵", name="Flaute", desc="−1 Dreh, dafür Gewinne ×1,3.", fx=dict(spins=-1, line=1.3))),
    ("jokers", dict(emoji="🎭", name="Maskenball", desc="Joker ✨ tauchen bei allen auf.", fx=dict(weight={"wild": 6}))),
    ("mercy", dict(emoji="🕊️", name="Gnadenfrist", desc="Der Eintreiber will 25 % weniger.", fx=dict(debt=0.75))),
    ("greed", dict(emoji="💸", name="Gier", desc="Eintreiber will 25 % mehr – aber Gewinne ×1,6.", fx=dict(debt=1.25, line=1.6))),
])

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LOCK = threading.RLock()
RNG = random.SystemRandom()
DB = {"players": {}}
SESSIONS = {}
LOBBIES = {}
PLAYER_LOBBY = {}
THROTTLE = {}
_bg_thread = None
_bg_stop = threading.Event()


class GameError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.msg = msg
        self.status = status


# --------------------------------------------------------------------------- #
# Speicherung
# --------------------------------------------------------------------------- #

def load_db():
    with LOCK:
        DB["players"] = {}
        if not os.path.exists(DATA_FILE):
            return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            players = raw.get("players", {}) if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            try:
                os.replace(DATA_FILE, DATA_FILE + ".defekt")
            except OSError:
                pass
            return
        for key, pl in players.items():
            if isinstance(pl, dict) and "name" in pl and "pin_hash" in pl:
                pl.setdefault("ruhm", 0)
                st = pl.setdefault("stats", {})
                for k in ("games", "wins", "best_round", "best_win", "total_earned", "jackpots"):
                    st.setdefault(k, 0)
                DB["players"][key] = pl


def save_db():
    with LOCK:
        folder = os.path.dirname(DATA_FILE) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".spielstand-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(DB, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def reset_state(data_file=None):
    """Nur für Tests: setzt alles zurück."""
    global DATA_FILE
    with LOCK:
        if data_file:
            DATA_FILE = data_file
        SESSIONS.clear()
        LOBBIES.clear()
        PLAYER_LOBBY.clear()
        THROTTLE.clear()
        load_db()


# --------------------------------------------------------------------------- #
# Slot-Logik (reine Funktionen)
# --------------------------------------------------------------------------- #

MUL_KEYS = ("line", "skull_pen", "debt", "pot", "first", "last", "discount", "guard")
ADD_KEYS = ("spins", "holds", "gambles", "flat", "interest", "streak", "gamble_odds", "consolation",
            "interest_cap", "save", "skull_pay")


def compute_mods(charms, extra=None):
    """Summe aller Effekte aus Glücksbringern (n Stapel) plus optionalen Zusatz-Effekten (Ereignis)."""
    m = dict(mult={s: 1.0 for s in SYMBOLS}, weight={s: 0 for s in SYMBOLS}, lenmult={3: 1.0, 4: 1.0, 5: 1.0})
    for k in MUL_KEYS:
        m[k] = 1.0
    for k in ADD_KEYS:
        m[k] = 0
    sources = [(CHARMS[cid]["fx"], n) for cid, n in charms.items() if cid in CHARMS]
    for fx in (extra or []):
        sources.append((fx, 1))
    for fx, n in sources:
        for k, v in fx.items():
            if k == "mult":
                for s, x in v.items():
                    m["mult"][s] *= x ** n
            elif k == "weight":
                for s, x in v.items():
                    m["weight"][s] += x * n
            elif k == "lenmult":
                for ln, x in v.items():
                    m["lenmult"][int(ln)] *= x ** n
            elif k in MUL_KEYS:
                m[k] *= v ** n
            elif k in ADD_KEYS:
                m[k] += v * n
    m["debt"] = max(0.5, m["debt"])
    m["skull_pen"] = max(0.2, m["skull_pen"])
    m["gamble_odds"] = min(0.7, m["gamble_odds"])
    m["discount"] = max(0.6, m["discount"])
    m["save"] = min(SAVE_CAP, m["save"])
    return m


def price_scale(round_no):
    return PRICE_GROWTH ** max(0, round_no - 1)


def debt_for(round_no, mods):
    return max(1, math.ceil(DEBT_BASE * DEBT_GROWTH ** (round_no - 1) * mods["debt"]))


def symbol_weights(mods, round_no, cursed=False):
    w = {s: max(0, d["weight"] + mods["weight"][s]) for s, d in SYMBOLS.items()}
    w["skull"] = max(1, w["skull"] + (round_no - 1) // 2)
    if cursed:
        w["skull"] = int(w["skull"] * 2.5)
    return w


def roll_reel(weights):
    syms = [s for s, x in weights.items() if x > 0]
    ws = [weights[s] for s in syms]
    return RNG.choices(syms, ws, k=ROWS)


def random_grid(weights):
    cols = [roll_reel(weights) for _ in range(REELS)]
    return [[cols[c][r] for c in range(REELS)] for r in range(ROWS)]


def eval_line(syms):
    """Gibt (symbol, anzahl) für die Linie von links zurück. Joker ersetzen alles außer 💀."""
    s = None
    n = 0
    for sym in syms:
        if sym == "wild":
            if s == "skull":
                break
            n += 1
            continue
        if s is None:
            s = sym
            n += 1
        elif sym == s:
            n += 1
        else:
            break
    if s is None:
        s = "seven"
    if s == "skull":
        n = 0
        for sym in syms:
            if sym == "skull":
                n += 1
            else:
                break
    return s, n


def evaluate(grid, mods, streak=0, boost=1.0):
    lines = []
    total = 0.0
    pen = 0
    factor = boost
    if mods["streak"] > 0:
        factor *= 1 + min(0.9, mods["streak"] * streak)
    for li, cells in enumerate(LINES):
        s, n = eval_line([grid[r][c] for r, c in cells])
        if n < 3:
            continue
        if s == "skull":
            if mods["skull_pay"] > 0:
                coins = SKULL_PEN[n] * mods["skull_pay"] * mods["line"] * factor
                total += coins
                lines.append(dict(line=li, sym="skull", n=n, cells=[list(x) for x in cells[:n]], coins=coins))
                continue
            p = max(1, round(SKULL_PEN[n] * mods["skull_pen"]))
            pen += p
            lines.append(dict(line=li, sym="skull", n=n, cells=[list(x) for x in cells[:n]], coins=-p))
        else:
            coins = (SYMBOLS[s]["value"] * LINE_MULT[n] * mods["mult"][s] * mods["line"]
                     * mods["lenmult"][n] * factor)
            total += coins
            lines.append(dict(line=li, sym=s, n=n, cells=[list(x) for x in cells[:n]], coins=coins))
    win = int(round(total))
    for ln in lines:
        if ln["coins"] > 0:
            ln["coins"] = max(1, int(round(ln["coins"])))
    sevens = sum(1 for row in grid for x in row if x == "seven")
    return dict(lines=lines, win=win, pen=pen, sevens=sevens)


# --------------------------------------------------------------------------- #
# Spieler & Lobby
# --------------------------------------------------------------------------- #

class Player:
    def __init__(self, key, name):
        self.key = key
        self.name = name
        self.reset()
        self.seen = time.time()

    def reset(self):
        self.coins = START_COINS
        self.lives = START_LIVES
        self.charms = {}
        self.status = "alive"       # alive | out
        self.out_round = None
        self.quit = False
        self.spins_left = 0
        self.holds_left = 0
        self.gambles_left = 0
        self.gamble_pending = 0
        self.streak = 0
        self.done = False
        self.ready = False
        self.offers = []
        self.rerolls = 0
        self.sabos_used = 0
        self.mirrors = 0
        self.frost = 0
        self.curse_next = False
        self.cursed = False
        self.tax_next = 0
        self.taxed = 0
        self.chain_next = False
        self.chained = False
        self.bribe_next = 0
        self.bribed = 0
        self.spin_no = 0
        self.saves = 0
        self.grid = None
        self.last = None
        self.spin_id = 0
        self.total_earned = 0
        self.best_win = 0
        self.jackpots = 0
        self.rounds_survived = 0
        self.emote = None
        self.emote_at = 0.0
        self.paid_history = []

    def mods(self):
        return compute_mods(self.charms)

    @property
    def alive(self):
        return self.status == "alive"


class Lobby:
    def __init__(self, code, host_key, name, rounds):
        self.code = code
        self.host = host_key
        self.name = name
        self.max_rounds = rounds
        self.players = OrderedDict()
        self.phase = "wait"        # wait | shop | spin | collect | over
        self.round = 0
        self.deadline = None
        self.grace_at = None
        self.pot = POT_START
        self.events = []
        self.eid = 0
        self.v = 1
        self.result = None
        self.final = None
        self.starter = False
        self.multi = False
        self.event = None          # Ereignis der laufenden Runde
        self.next_event = None     # im Shop angekündigtes Ereignis der nächsten Runde
        self.created = time.time()

    # -- Helfer -------------------------------------------------------------- #
    def touch(self):
        self.v += 1

    def log(self, kind, txt, who=None, amt=0):
        self.eid += 1
        self.events.append(dict(id=self.eid, k=kind, txt=txt, who=who, amt=amt))
        del self.events[:-60]
        self.touch()

    def alive_players(self):
        return [p for p in self.players.values() if p.alive]

    def get(self, key):
        p = self.players.get(key)
        if not p:
            raise GameError("Du sitzt nicht an diesem Tisch.", 403)
        return p

    # -- Skalierung & Effekte -------------------------------------------------- #
    def mods_of(self, p, next_round=False):
        ev = self.next_event if next_round else self.event
        return compute_mods(p.charms, [EVENTS[ev]["fx"]] if ev else None)

    def due_of(self, p, round_no, mods, taxed=0, bribed=0):
        due = debt_for(round_no, mods) * (1.2 ** taxed) * (0.75 ** bribed)
        return max(1, int(math.ceil(due)))

    def scale(self):
        return price_scale(self.round)

    def charm_price(self, p, cid):
        if self.starter:
            return 0
        c = CHARMS[cid]
        base = c["price"] * (1 + STACK_GROWTH * p.charms.get(cid, 0)) * self.scale() * p.mods()["discount"]
        return max(1, int(round(base)))

    def sabo_price(self, p, sid):
        return max(1, int(round(SABOTAGE[sid]["price"] * self.scale() * p.mods()["discount"])))

    def reroll_cost(self, p):
        return max(1, int(round(REROLL_BASE * (1 + p.rerolls) * self.scale() * p.mods()["discount"])))

    def sell_value(self, p, cid):
        n = p.charms.get(cid, 0)
        if n <= 0 or not CHARMS[cid].get("sell", True):
            return 0
        val = CHARMS[cid]["price"] * (1 + STACK_GROWTH * (n - 1)) * self.scale() * SELL_RATE
        return max(1, int(val))

    def event_view(self, ev):
        if not ev:
            return None
        e = EVENTS[ev]
        return dict(id=ev, emoji=e["emoji"], name=e["name"], desc=e["desc"])

    # -- Beitritt ------------------------------------------------------------ #
    def add_player(self, key, name):
        if key in self.players:
            return self.players[key]
        if self.phase != "wait":
            raise GameError("Das Spiel läuft schon. Warte auf die nächste Runde.")
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("Der Tisch ist voll (%d Spieler)." % MAX_PLAYERS)
        p = Player(key, name)
        self.players[key] = p
        self.log("info", "%s ist beigetreten." % name, name)
        return p

    def remove_player(self, key):
        p = self.players.get(key)
        if not p:
            return
        if self.phase in ("wait", "over"):
            del self.players[key]
            self.log("info", "%s hat den Tisch verlassen." % p.name, p.name)
        else:
            if p.alive:
                p.status = "out"
                p.quit = True
                p.out_round = self.round
            self.log("out", "%s hat aufgegeben." % p.name, p.name)
        if self.host == key:
            rest = [x for x in self.players.values() if not x.quit]
            if rest:
                self.host = rest[0].key
        self.touch()

    # -- Spielstart ---------------------------------------------------------- #
    def start_game(self, rounds=None):
        if self.phase not in ("wait", "over"):
            raise GameError("Das Spiel läuft schon.")
        if rounds in ROUND_CHOICES:
            self.max_rounds = rounds
        for p in self.players.values():
            p.reset()
        self.multi = len(self.players) > 1
        self.round = 0
        self.pot = POT_START
        self.result = None
        self.final = None
        self.event = None
        self.next_event = None
        self.log("info", "Die Grube öffnet! %d Runden – möge das Glück mit euch sein." % self.max_rounds)
        self.begin_shop(starter=True)

    # -- Shop ---------------------------------------------------------------- #
    def offer_count(self):
        if self.starter:
            return 3
        return 5 if self.round >= EXTRA_OFFER_FROM else 4

    def gen_offers(self, p, n=4):
        rw = rarity_weights(self.round)
        pool = [cid for cid, c in CHARMS.items() if p.charms.get(cid, 0) < c["max"] and rw[c["rarity"]] > 0]
        ws = [rw[CHARMS[c]["rarity"]] for c in pool]
        out = []
        while pool and len(out) < n:
            cid = RNG.choices(pool, ws)[0]
            i = pool.index(cid)
            pool.pop(i)
            ws.pop(i)
            out.append(cid)
        return out

    def begin_shop(self, starter=False):
        self.phase = "shop"
        self.starter = starter
        self.result = None
        now = time.time()
        self.deadline = now + (STARTER_SECONDS if starter else SHOP_SECONDS)
        upcoming = self.round + 1
        self.next_event = None
        if upcoming % EVENT_EVERY == 0 and upcoming <= self.max_rounds:
            self.next_event = RNG.choice(list(EVENTS))
        for p in self.players.values():
            p.ready = not p.alive
            p.rerolls = 0
            p.sabos_used = 0
            p.offers = self.gen_offers(p, self.offer_count()) if p.alive else []
        if starter:
            self.log("info", "Wähle deinen Start-Glücksbringer – gratis!")
        else:
            self.log("info", "Der Shop ist offen – Preisniveau ×%s. Kauft Glücksbringer oder macht den anderen das Leben schwer."
                     % ("%.2f" % self.scale()).replace(".", ","))
        if self.round == LEGENDARY_FROM:
            self.log("info", "👑 Ab jetzt tauchen legendäre Glücksbringer im Shop auf!")
        if self.next_event:
            e = EVENTS[self.next_event]
            self.log("event", "%s Nächste Runde: %s – %s" % (e["emoji"], e["name"], e["desc"]))

    def buy_charm(self, p, cid):
        if self.phase != "shop" or not p.alive:
            raise GameError("Der Shop ist gerade geschlossen.")
        if cid not in p.offers:
            raise GameError("Dieses Angebot gibt es nicht mehr.")
        if self.starter and p.ready:
            raise GameError("Du hast schon gewählt.")
        price = self.charm_price(p, cid)
        if p.coins < price:
            raise GameError("Dafür fehlen dir %d Münzen." % (price - p.coins))
        p.coins -= price
        p.charms[cid] = p.charms.get(cid, 0) + 1
        c = CHARMS[cid]
        if c["fx"].get("lives"):
            p.lives += c["fx"]["lives"]
        p.offers.remove(cid)
        if self.starter:
            p.ready = True
        self.log("buy", "%s kauft %s %s." % (p.name, c["emoji"], c["name"]), p.name, price)

    def reroll(self, p):
        if self.phase != "shop" or not p.alive or self.starter:
            raise GameError("Neu mischen geht jetzt nicht.")
        cost = self.reroll_cost(p)
        if p.coins < cost:
            raise GameError("Neu mischen kostet %d Münzen." % cost)
        p.coins -= cost
        p.rerolls += 1
        p.offers = self.gen_offers(p, self.offer_count())
        self.touch()

    def sell_charm(self, p, cid):
        if self.phase != "shop" or not p.alive or self.starter:
            raise GameError("Verkaufen geht nur im Schwarzmarkt.")
        if p.charms.get(cid, 0) <= 0:
            raise GameError("Diesen Glücksbringer hast du nicht.")
        if not CHARMS[cid].get("sell", True):
            raise GameError("Diesen Glücksbringer kannst du nicht verkaufen.")
        value = self.sell_value(p, cid)
        p.charms[cid] -= 1
        if p.charms[cid] <= 0:
            del p.charms[cid]
        p.coins += value
        c = CHARMS[cid]
        self.log("sell", "%s verkauft %s %s für %d Münzen." % (p.name, c["emoji"], c["name"], value), p.name, value)
        return value

    def buy_sabotage(self, p, sid, target_key):
        if self.phase != "shop" or not p.alive or self.starter:
            raise GameError("Sabotage geht gerade nicht.")
        item = SABOTAGE.get(sid)
        if not item:
            raise GameError("Unbekanntes Item.")
        if p.sabos_used >= MAX_SABOTAGES:
            raise GameError("Maximal %d Items pro Shop-Phase." % MAX_SABOTAGES)
        price = self.sabo_price(p, sid)
        if p.coins < price:
            raise GameError("Dafür fehlen dir %d Münzen." % (price - p.coins))
        if sid == "bribe" and p.bribe_next >= 2:
            raise GameError("Mehr als zweimal lässt sich der Eintreiber nicht bestechen.")
        dst = None
        if item["target"]:
            dst = self.players.get(target_key)
            if not dst or not dst.alive or dst.key == p.key:
                raise GameError("Such dir einen lebenden Gegner aus.")
        p.coins -= price
        p.sabos_used += 1
        if sid == "mirror":
            p.mirrors += 1
            self.log("buy", "%s stellt einen Zauberspiegel auf. 🪞" % p.name, p.name)
            return
        if sid == "bribe":
            p.bribe_next += 1
            self.log("buy", "🤝 %s steckt dem Eintreiber etwas zu …" % p.name, p.name)
            return
        if dst.mirrors > 0:
            dst.mirrors -= 1
            self.log("mirror", "🪞 %s spiegelt den Angriff von %s zurück!" % (dst.name, p.name), dst.name)
            self.apply_sabotage(sid, dst, p)
        else:
            self.apply_sabotage(sid, p, dst)

    def apply_sabotage(self, sid, src, dst):
        item = SABOTAGE[sid]
        if sid == "thief":
            amt = min(dst.coins, int((15 * self.scale() + dst.coins * 0.15) * dst.mods()["guard"]))
            dst.coins -= amt
            src.coins += amt
            self.log("steal", "%s %s bestiehlt %s um %d Münzen!" % (item["emoji"], src.name, dst.name, amt), src.name, amt)
        elif sid == "frost":
            dst.frost += 1
            self.log("frost", "%s %s friert %s ein: nächste Runde 1 Dreh weniger." % (item["emoji"], src.name, dst.name), src.name)
        elif sid == "curse":
            dst.curse_next = True
            self.log("curse", "%s %s verflucht %s: Totenköpfe regnen." % (item["emoji"], src.name, dst.name), src.name)
        elif sid == "tax":
            dst.tax_next += 1
            self.log("curse", "%s %s hetzt %s den Steuerprüfer auf den Hals: +20 %% Schulden." % (item["emoji"], src.name, dst.name), src.name)
        elif sid == "chain":
            dst.chain_next = True
            self.log("frost", "%s %s legt %s in Ketten: nächste Runde kein Halten." % (item["emoji"], src.name, dst.name), src.name)

    def set_ready(self, p):
        if self.phase != "shop" or not p.alive:
            raise GameError("Gerade nichts zu bestätigen.")
        if self.starter and not p.ready:
            raise GameError("Wähle zuerst deinen Start-Glücksbringer.")
        p.ready = True
        self.touch()

    def end_shop(self):
        if self.starter:
            for p in self.alive_players():
                if not p.ready and p.offers:
                    cid = RNG.choice(p.offers)
                    p.coins += 0
                    self.buy_charm(p, cid)
        self.begin_spin_phase()

    # -- Dreh-Phase ---------------------------------------------------------- #
    def begin_spin_phase(self):
        self.round += 1
        self.phase = "spin"
        self.starter = False
        self.grace_at = None
        self.event = self.next_event
        self.next_event = None
        most = 1
        for p in self.players.values():
            if not p.alive:
                continue
            m = self.mods_of(p)
            p.spins_left = max(1, min(MAX_SPINS, BASE_SPINS + m["spins"]) - p.frost)
            p.frost = 0
            p.chained = p.chain_next
            p.chain_next = False
            p.holds_left = 0 if p.chained else BASE_HOLDS + m["holds"]
            p.gambles_left = BASE_GAMBLES + m["gambles"]
            p.gamble_pending = 0
            p.streak = 0
            p.spin_no = 0
            p.done = False
            p.cursed = p.curse_next
            p.curse_next = False
            p.taxed = p.tax_next
            p.tax_next = 0
            p.bribed = p.bribe_next
            p.bribe_next = 0
            most = max(most, p.spins_left)
            if p.grid is None:
                p.grid = random_grid(symbol_weights(m, self.round))
        self.deadline = time.time() + SPIN_BASE_SECONDS + SPIN_PER_SPIN_SECONDS * most
        self.log("info", "Runde %d von %d – der Eintreiber will %d Münzen." % (
            self.round, self.max_rounds, debt_for(self.round, compute_mods({}, self.event_fx()))))
        if self.event:
            e = EVENTS[self.event]
            self.log("event", "%s EREIGNIS: %s – %s" % (e["emoji"], e["name"], e["desc"]))

    def event_fx(self, next_round=False):
        ev = self.next_event if next_round else self.event
        return [EVENTS[ev]["fx"]] if ev else None

    def spin(self, p, holds=None, auto=False):
        if self.phase != "spin" or not p.alive:
            raise GameError("Gerade kannst du nicht drehen.")
        if p.spins_left <= 0:
            raise GameError("Keine Dreh mehr in dieser Runde.")
        holds = sorted(set(int(h) for h in (holds or []) if isinstance(h, int) or str(h).isdigit()))
        holds = [h for h in holds if 0 <= h < REELS]
        if auto:
            holds = []
        if len(holds) > p.holds_left:
            raise GameError("Du hast nur noch %d Halten übrig." % p.holds_left)
        if len(holds) >= REELS:
            raise GameError("Du kannst nicht alle Walzen halten.")
        m = self.mods_of(p)
        w = symbol_weights(m, self.round, p.cursed)
        new = random_grid(w)
        for h in holds:
            for r in range(ROWS):
                new[r][h] = p.grid[r][h]
        p.holds_left -= len(holds)
        p.grid = new
        boost = 1.0
        if p.spin_no == 0:
            boost *= m["first"]
        if p.spins_left == 1:
            boost *= m["last"]
        p.spins_left -= 1
        p.spin_no += 1
        p.spin_id += 1
        res = evaluate(new, m, p.streak, boost)
        line_win = res["win"]
        flat = int(m["flat"]) + (int(m["consolation"]) if line_win == 0 else 0)
        net = line_win + flat - res["pen"]
        p.streak = p.streak + 1 if line_win > 0 else 0
        jackpot = 0
        if res["sevens"] >= 3:
            jackpot = int(self.pot * m["pot"])
            self.pot = int(POT_START * self.scale())
        else:
            self.pot += int(round(POT_PER_SPIN * self.scale()))
        gain = net + jackpot
        before = p.coins
        p.coins = max(0, p.coins + gain)
        if p.coins > before:
            p.total_earned += p.coins - before
        p.best_win = max(p.best_win, net + jackpot)
        if jackpot:
            p.jackpots += 1
        p.gamble_pending = net if (net > 0 and p.gambles_left > 0) else 0
        p.last = dict(id=p.spin_id, grid=new, lines=res["lines"], win=line_win, flat=flat, pen=res["pen"],
                      jackpot=jackpot, net=net, sevens=res["sevens"], streak=p.streak, auto=auto, holds=holds,
                      boost=round(boost, 2))
        total = net + jackpot
        if jackpot:
            self.log("jackpot", "🎰 JACKPOT! %s knackt den Topf: +%d Münzen!" % (p.name, jackpot), p.name, jackpot)
        elif total >= MEGA_WIN:
            self.log("big", "💥 MEGA-GEWINN! %s kassiert %d Münzen!" % (p.name, total), p.name, total)
        elif total >= BIG_WIN:
            self.log("big", "🔥 %s gewinnt %d Münzen!" % (p.name, total), p.name, total)
        elif res["pen"] > 0 and line_win == 0:
            self.log("skull", "💀 %s zieht eine Pechlinie: −%d." % (p.name, res["pen"]), p.name, -res["pen"])
        else:
            self.touch()
        return p.last

    def gamble(self, p):
        if self.phase != "spin" or not p.alive:
            raise GameError("Gerade kein Risiko möglich.")
        if p.gamble_pending <= 0 or p.gambles_left <= 0:
            raise GameError("Es gibt nichts zu verdoppeln.")
        stake = p.gamble_pending
        p.gambles_left -= 1
        odds = GAMBLE_BASE_ODDS + self.mods_of(p)["gamble_odds"]
        won = RNG.random() < odds
        if won:
            p.coins += stake
            p.total_earned += stake
            p.gamble_pending = stake * 2 if p.gambles_left > 0 else 0
            p.best_win = max(p.best_win, stake * 2)
            self.log("gamble", "🎲 %s wagt es und verdoppelt auf %d!" % (p.name, stake * 2), p.name, stake)
        else:
            p.coins = max(0, p.coins - stake)
            p.gamble_pending = 0
            self.log("gamble", "🎲 %s riskiert alles und verliert %d. Autsch!" % (p.name, stake), p.name, -stake)
        return dict(won=won, stake=stake, odds=odds)

    def finish(self, p):
        if self.phase != "spin" or not p.alive:
            raise GameError("Gerade nichts zu beenden.")
        if p.spins_left > 0:
            raise GameError("Du hast noch Dreh übrig.")
        p.done = True
        self.touch()

    # -- Eintreiber ---------------------------------------------------------- #
    def end_spin_phase(self):
        self.phase = "collect"
        self.deadline = time.time() + COLLECT_SECONDS
        rows = []
        for p in self.players.values():
            if not p.alive:
                continue
            m = self.mods_of(p)
            due = self.due_of(p, self.round, m, p.taxed, p.bribed)
            if p.coins >= due:
                p.coins -= due
                bonus = 0
                if m["interest"] > 0:
                    cap = int(INTEREST_CAP * self.scale()) + int(m["interest_cap"])
                    bonus = min(cap, int(p.coins * m["interest"]))
                    p.coins += bonus
                p.rounds_survived += 1
                rows.append(dict(name=p.name, key=p.key, due=due, paid=True, bonus=bonus, coins=p.coins,
                                 lives=p.lives, out=False))
                self.log("paid", "✅ %s zahlt %d Münzen." % (p.name, due), p.name, due)
            elif m["save"] > 0 and RNG.random() < m["save"]:
                lost = p.coins
                p.coins = PITY_COINS
                p.saves += 1
                rows.append(dict(name=p.name, key=p.key, due=due, paid=False, saved=True, bonus=0, coins=p.coins,
                                 lives=p.lives, out=False, lost=lost))
                self.log("paid", "🐈‍⬛ %s kann nicht zahlen – entwischt dem Eintreiber aber mit einem Katzenleben!" % p.name, p.name)
            else:
                p.lives -= 1
                lost = p.coins
                p.coins = PITY_COINS
                out = p.lives <= 0
                if out:
                    p.status = "out"
                    p.out_round = self.round
                    p.coins = 0
                rows.append(dict(name=p.name, key=p.key, due=due, paid=False, bonus=0, coins=p.coins,
                                 lives=max(0, p.lives), out=out, lost=lost))
                self.log("fail", ("☠️ %s kann nicht zahlen und scheidet aus!" if out
                                  else "💔 %s kann nicht zahlen und verliert ein Leben!") % p.name, p.name, due)
        self.result = dict(round=self.round, due=debt_for(self.round, compute_mods({}, self.event_fx())), rows=rows)
        self.touch()

    def after_collect(self):
        alive = self.alive_players()
        if self.round >= self.max_rounds or not alive or (self.multi and len(alive) <= 1):
            self.finish_game()
        else:
            self.begin_shop()

    # -- Spielende ----------------------------------------------------------- #
    def finish_game(self):
        self.phase = "over"
        self.deadline = None
        plist = list(self.players.values())

        def rank_key(p):
            return (1 if p.alive else 0, p.coins if p.alive else 0, p.out_round or 0, p.total_earned)

        plist.sort(key=rank_key, reverse=True)
        survivors = [p for p in plist if p.alive]
        rows = []
        bonus_place = [50, 30, 20, 10, 5, 5]
        for i, p in enumerate(plist):
            place = i + 1
            ruhm = p.rounds_survived * 12 + min(100, p.total_earned // 20)
            if self.multi:
                ruhm += bonus_place[i]
            elif p.alive and self.round >= self.max_rounds:
                ruhm += 40
            won = (p.alive and (place == 1 if self.multi else self.round >= self.max_rounds))
            rows.append(dict(name=p.name, key=p.key, place=place, alive=p.alive, coins=p.coins,
                             rounds=p.rounds_survived, earned=p.total_earned, ruhm=ruhm, won=bool(won),
                             best_win=p.best_win, jackpots=p.jackpots))
            prof = DB["players"].get(p.key)
            if prof:
                st = prof["stats"]
                prof["ruhm"] += ruhm
                st["games"] += 1
                st["wins"] += 1 if won else 0
                st["best_round"] = max(st["best_round"], p.rounds_survived)
                st["best_win"] = max(st["best_win"], p.best_win)
                st["total_earned"] += p.total_earned
                st["jackpots"] += p.jackpots
        save_db()
        self.final = dict(rows=rows, rounds=self.round, survivors=len(survivors))
        champ = next((r for r in rows if r["won"]), None)
        if champ:
            self.log("info", "🏆 %s gewinnt die Glücksgrube!" % champ["name"], champ["name"])
        else:
            self.log("info", "Die Grube hat alle verschluckt. Niemand gewinnt.")

    def restart(self):
        if self.phase != "over":
            raise GameError("Das Spiel ist noch nicht vorbei.")
        self.phase = "wait"
        self.round = 0
        self.final = None
        self.result = None
        self.pot = POT_START
        self.event = None
        self.next_event = None
        for k in [k for k, p in self.players.items() if p.quit]:
            del self.players[k]
            PLAYER_LOBBY.pop(k, None)
        for p in self.players.values():
            p.reset()
        self.touch()

    # -- Zeitsteuerung ------------------------------------------------------- #
    def tick(self, now):
        for p in list(self.players.values()):
            if p.alive and self.phase not in ("wait", "over") and now - p.seen > ABSENT_SECONDS:
                self.remove_player(p.key)
                PLAYER_LOBBY.pop(p.key, None)
        if self.phase in ("spin", "shop", "collect") and not self.alive_players():
            self.finish_game()
            return
        if self.phase == "spin":
            alive = self.alive_players()
            if all(p.spins_left <= 0 for p in alive):
                if self.grace_at is None:
                    self.grace_at = now + GRACE_SECONDS
                    self.touch()
                if all(p.done for p in alive) or now >= self.grace_at:
                    self.end_spin_phase()
                    return
            if self.deadline and now >= self.deadline:
                for p in alive:
                    while p.spins_left > 0:
                        self.spin(p, auto=True)
                self.end_spin_phase()
        elif self.phase == "shop":
            alive = self.alive_players()
            if all(p.ready for p in alive) or (self.deadline and now >= self.deadline):
                self.end_shop()
        elif self.phase == "collect":
            if self.deadline and now >= self.deadline:
                self.after_collect()


# --------------------------------------------------------------------------- #
# Hilfsfunktionen für die API
# --------------------------------------------------------------------------- #

def clean_name(raw):
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not re.fullmatch(r"[\w .\-]{2,16}", name):
        raise GameError("Der Name braucht 2–16 Zeichen (Buchstaben, Zahlen, Leerzeichen, . oder -).")
    return name


def clean_pin(raw):
    pin = str(raw or "")
    if len(pin) < 4 or len(pin) > 64:
        raise GameError("Die PIN braucht mindestens 4 Zeichen.")
    return pin


def level_of(ruhm):
    return 1 + int(math.sqrt(max(0, ruhm) / 40))


def throttle_check(key):
    now = time.time()
    n, t = THROTTLE.get(key, (0, now))
    if now - t > 60:
        n, t = 0, now
    if n >= 8:
        raise GameError("Zu viele Versuche. Warte kurz.", 429)
    THROTTLE[key] = (n, t)


def throttle_fail(key):
    now = time.time()
    n, t = THROTTLE.get(key, (0, now))
    THROTTLE[key] = (n + 1, t)


def profile_view(key):
    pl = DB["players"][key]
    return dict(name=pl["name"], ruhm=pl["ruhm"], level=level_of(pl["ruhm"]), stats=pl["stats"],
                lobby=PLAYER_LOBBY.get(key))


def new_code():
    for _ in range(200):
        code = "".join(RNG.choice(ALPHABET) for _ in range(5))
        if code not in LOBBIES:
            return code
    raise GameError("Gerade sind alle Tische belegt.")


def normalize_code(raw):
    return re.sub(r"[^A-Z0-9]", "", str(raw or "").upper())


def prune_lobby(code):
    lobby = LOBBIES.get(code)
    if lobby and not lobby.players:
        del LOBBIES[code]


def leave_current(key):
    code = PLAYER_LOBBY.pop(key, None)
    lobby = LOBBIES.get(code) if code else None
    if lobby:
        lobby.remove_player(key)
        prune_lobby(code)


def catalog():
    return dict(
        symbols=[dict(id=s, **{k: v for k, v in d.items() if k != "weight"}) for s, d in SYMBOLS.items()],
        line_mult={str(k): v for k, v in LINE_MULT.items()},
        skull_pen={str(k): v for k, v in SKULL_PEN.items()},
        lines=LINES,
        charms=[dict(id=c, **{k: v for k, v in d.items() if k != "fx"}) for c, d in CHARMS.items()],
        sabotage=[dict(id=s, **d) for s, d in SABOTAGE.items()],
        events=[dict(id=e, **{k: v for k, v in d.items() if k != "fx"}) for e, d in EVENTS.items()],
        rarities=list(RARITY_ORDER), legendary_from=LEGENDARY_FROM, event_every=EVENT_EVERY,
        price_growth=PRICE_GROWTH,
        emotes=list(EMOTES),
        rounds=list(ROUND_CHOICES),
        reels=REELS, rows=ROWS, max_players=MAX_PLAYERS,
        base_spins=BASE_SPINS, pot_start=POT_START, big_win=BIG_WIN, mega_win=MEGA_WIN,
        start_coins=START_COINS, start_lives=START_LIVES,
    )


def player_view(lobby, p, now, me=False):
    m = lobby.mods_of(p)
    d = dict(
        key=p.key, name=p.name, coins=p.coins, lives=p.lives, alive=p.alive, quit=p.quit,
        charms=p.charms, grid=p.grid, last=p.last, spins_left=p.spins_left, ready=p.ready,
        done=p.done, streak=p.streak, host=(p.key == lobby.host), online=(now - p.seen) < 12,
        due=lobby.due_of(p, max(1, lobby.round), m, p.taxed, p.bribed), earned=p.total_earned,
        emote=(p.emote if now - p.emote_at < 4 else None), emote_at=p.emote_at, mirrors=p.mirrors,
        level=level_of(DB["players"].get(p.key, {}).get("ruhm", 0)),
        cursed=p.cursed, out_round=p.out_round, chained=p.chained, taxed=p.taxed,
        marked=dict(frost=p.frost, curse=p.curse_next, tax=p.tax_next, chain=p.chain_next),
    )
    if me:
        w = symbol_weights(m, max(1, lobby.round), p.cursed)
        tot = float(sum(w.values())) or 1.0
        d.update(
            holds_left=p.holds_left, gambles_left=p.gambles_left, gamble_pending=p.gamble_pending,
            gamble_odds=GAMBLE_BASE_ODDS + m["gamble_odds"],
            offers=[dict(id=c, price=lobby.charm_price(p, c)) for c in p.offers],
            reroll_cost=lobby.reroll_cost(p), sabos_left=MAX_SABOTAGES - p.sabos_used,
            sabo_prices={s: lobby.sabo_price(p, s) for s in SABOTAGE},
            sell_values={c: lobby.sell_value(p, c) for c in p.charms},
            odds={s: round(100 * x / tot, 1) for s, x in w.items() if x > 0},
            mult={s: round(x, 2) for s, x in m["mult"].items() if abs(x - 1) > 1e-9},
            due_next=lobby.due_of(p, lobby.round + 1, lobby.mods_of(p, next_round=True), p.tax_next, p.bribe_next),
            bribe_next=p.bribe_next, bribed=p.bribed, saves=p.saves,
            flat=m["flat"], interest=m["interest"], spins_total=min(MAX_SPINS, BASE_SPINS + m["spins"]),
            streak_bonus=m["streak"], frost=p.frost, curse_next=p.curse_next,
        )
    return d


def lobby_view(lobby, key):
    now = time.time()
    me = lobby.players.get(key)
    if me:
        me.seen = now
    return dict(
        code=lobby.code, name=lobby.name, phase=lobby.phase, round=lobby.round, max_rounds=lobby.max_rounds,
        host=lobby.host, you=key, pot=lobby.pot, v=lobby.v, starter=lobby.starter,
        deadline_in=(max(0.0, lobby.deadline - now) if lobby.deadline else None),
        grace=(lobby.grace_at is not None and lobby.phase == "spin"),
        due_base=debt_for(max(1, lobby.round), compute_mods({}, lobby.event_fx())),
        event=lobby.event_view(lobby.event), next_event=lobby.event_view(lobby.next_event),
        price_scale=round(lobby.scale(), 2), offer_rarity=rarity_weights(lobby.round),
        multi=lobby.multi,
        players=[player_view(lobby, p, now) for p in lobby.players.values()],
        me=(player_view(lobby, me, now, me=True) if me else None),
        events=lobby.events[-30:], result=lobby.result, final=lobby.final,
    )


# --------------------------------------------------------------------------- #
# Hintergrund-Ticker
# --------------------------------------------------------------------------- #

def tick_all():
    now = time.time()
    with LOCK:
        for code in list(LOBBIES.keys()):
            lobby = LOBBIES.get(code)
            if not lobby:
                continue
            try:
                lobby.tick(now)
            except GameError:
                pass
            idle = max([now - p.seen for p in lobby.players.values()] or [9999])
            if idle > 600:
                for k in list(lobby.players):
                    PLAYER_LOBBY.pop(k, None)
                del LOBBIES[code]


def _background_loop():
    while not _bg_stop.wait(0.4):
        try:
            tick_all()
        except Exception:      # der Ticker darf nie sterben
            pass


def start_background():
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_stop.clear()
    _bg_thread = threading.Thread(target=_background_loop, name="grube-ticker", daemon=True)
    _bg_thread.start()


def stop_background():
    _bg_stop.set()


# --------------------------------------------------------------------------- #
# Flask
# --------------------------------------------------------------------------- #

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="/static")
app.config["JSON_AS_ASCII"] = False


def auth(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Token", "")
        with LOCK:
            key = SESSIONS.get(token)
            if not key or key not in DB["players"]:
                raise GameError("Bitte melde dich neu an.", 401)
        g.key = key
        g.token = token
        return view(*args, **kwargs)
    return wrapper


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def lobby_of(code, key):
    lobby = LOBBIES.get(normalize_code(code))
    if not lobby or key not in lobby.players:
        raise GameError("Diesen Tisch gibt es nicht (mehr).", 404)
    return lobby


def state_response(lobby, key):
    return jsonify(lobby_view(lobby, key))


@app.before_request
def _ensure_background():
    if os.environ.get("GRUBE_NO_BG") != "1":
        start_background()


@app.after_request
def _headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.errorhandler(GameError)
def _game_error(err):
    return jsonify(error=err.msg), err.status


@app.errorhandler(404)
def _not_found(_e):
    return jsonify(error="Nicht gefunden."), 404


@app.errorhandler(Exception)
def _server_error(e):
    if hasattr(e, "code") and isinstance(e.code, int):
        return jsonify(error=getattr(e, "description", "Fehler")), e.code
    app.logger.exception("Serverfehler")
    return jsonify(error="Interner Fehler."), 500


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/health")
def health():
    return jsonify(ok=True)


@app.route("/api/catalog")
def api_catalog():
    return jsonify(catalog())


@app.route("/api/register", methods=["POST"])
def register():
    data = body()
    name = clean_name(data.get("name"))
    pin = clean_pin(data.get("pin"))
    key = name.lower()
    with LOCK:
        throttle_check("reg:" + request.remote_addr if request.remote_addr else "reg")
        if key in DB["players"]:
            raise GameError("Diesen Namen gibt es schon.")
        DB["players"][key] = dict(name=name, pin_hash=generate_password_hash(pin), ruhm=0,
                                  stats=dict(games=0, wins=0, best_round=0, best_win=0, total_earned=0, jackpots=0),
                                  created=int(time.time()))
        save_db()
        token = secrets.token_urlsafe(24)
        SESSIONS[token] = key
        return jsonify(token=token, me=profile_view(key))


@app.route("/api/login", methods=["POST"])
def login():
    data = body()
    name = str(data.get("name") or "").strip()
    pin = str(data.get("pin") or "")
    key = name.lower()
    with LOCK:
        throttle_check("log:" + key)
        pl = DB["players"].get(key)
        if not pl or not check_password_hash(pl["pin_hash"], pin):
            throttle_fail("log:" + key)
            raise GameError("Name oder PIN stimmt nicht.", 401)
        token = secrets.token_urlsafe(24)
        SESSIONS[token] = key
        return jsonify(token=token, me=profile_view(key))


@app.route("/api/logout", methods=["POST"])
@auth
def logout():
    with LOCK:
        SESSIONS.pop(g.token, None)
    return jsonify(ok=True)


@app.route("/api/me")
@auth
def me():
    with LOCK:
        return jsonify(me=profile_view(g.key))


@app.route("/api/leaderboard")
@auth
def leaderboard():
    with LOCK:
        rows = sorted(DB["players"].values(), key=lambda p: (-p["ruhm"], p["name"].lower()))[:10]
        return jsonify(rows=[dict(name=p["name"], ruhm=p["ruhm"], level=level_of(p["ruhm"]),
                                  wins=p["stats"]["wins"], games=p["stats"]["games"]) for p in rows])


def _join(key, lobby):
    old = PLAYER_LOBBY.get(key)
    if old and old != lobby.code:
        leave_current(key)
    lobby.add_player(key, DB["players"][key]["name"])
    PLAYER_LOBBY[key] = lobby.code


@app.route("/api/lobby/create", methods=["POST"])
@auth
def lobby_create():
    data = body()
    rounds = data.get("rounds")
    rounds = rounds if rounds in ROUND_CHOICES else 10
    with LOCK:
        if len(LOBBIES) >= MAX_LOBBIES:
            raise GameError("Gerade sind alle Tische belegt.")
        old = PLAYER_LOBBY.get(g.key)
        if old and old in LOBBIES and LOBBIES[old].phase not in ("wait", "over"):
            raise GameError("Du sitzt noch in einem laufenden Spiel.")
        leave_current(g.key)
        name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:24] or "Tisch von " + DB["players"][g.key]["name"]
        lobby = Lobby(new_code(), g.key, name, rounds)
        LOBBIES[lobby.code] = lobby
        _join(g.key, lobby)
        return jsonify(code=lobby.code, state=lobby_view(lobby, g.key))


@app.route("/api/lobby/join", methods=["POST"])
@auth
def lobby_join():
    code = normalize_code(body().get("code"))
    with LOCK:
        lobby = LOBBIES.get(code)
        if not lobby:
            raise GameError("Diesen Code gibt es nicht.", 404)
        if g.key in lobby.players:
            PLAYER_LOBBY[g.key] = lobby.code
            return jsonify(code=lobby.code, state=lobby_view(lobby, g.key))
        old = PLAYER_LOBBY.get(g.key)
        if old and old in LOBBIES and LOBBIES[old].phase not in ("wait", "over"):
            raise GameError("Du sitzt noch in einem laufenden Spiel.")
        _join(g.key, lobby)
        return jsonify(code=lobby.code, state=lobby_view(lobby, g.key))


@app.route("/api/lobby/<code>/state")
@auth
def lobby_state(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.tick(time.time())
        seen = request.args.get("v", type=int)
        lobby.players[g.key].seen = time.time()
        if seen is not None and seen == lobby.v:
            return jsonify(same=True, v=lobby.v)
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/leave", methods=["POST"])
@auth
def lobby_leave(code):
    with LOCK:
        lobby = LOBBIES.get(normalize_code(code))
        if lobby and g.key in lobby.players:
            leave_current(g.key)
        else:
            PLAYER_LOBBY.pop(g.key, None)
    return jsonify(ok=True)


@app.route("/api/lobby/<code>/rounds", methods=["POST"])
@auth
def lobby_rounds(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        if lobby.host != g.key:
            raise GameError("Nur der Host darf das ändern.", 403)
        if lobby.phase not in ("wait", "over"):
            raise GameError("Das Spiel läuft schon.")
        r = body().get("rounds")
        if r not in ROUND_CHOICES:
            raise GameError("Wähle 6, 10 oder 15 Runden.")
        lobby.max_rounds = r
        lobby.touch()
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/start", methods=["POST"])
@auth
def lobby_start(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        if lobby.host != g.key:
            raise GameError("Nur der Host darf starten.", 403)
        lobby.start_game()
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/restart", methods=["POST"])
@auth
def lobby_restart(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        if lobby.host != g.key:
            raise GameError("Nur der Host darf neu starten.", 403)
        lobby.restart()
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/spin", methods=["POST"])
@auth
def lobby_spin(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        p = lobby.get(g.key)
        raw = body().get("hold")
        holds = raw if isinstance(raw, list) else []
        lobby.spin(p, [h for h in holds if isinstance(h, int) and not isinstance(h, bool)])
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/gamble", methods=["POST"])
@auth
def lobby_gamble(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.gamble(lobby.get(g.key))
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/finish", methods=["POST"])
@auth
def lobby_finish(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.finish(lobby.get(g.key))
        lobby.tick(time.time())
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/buy", methods=["POST"])
@auth
def lobby_buy(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.buy_charm(lobby.get(g.key), str(body().get("id")))
        lobby.tick(time.time())
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/sell", methods=["POST"])
@auth
def lobby_sell(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.sell_charm(lobby.get(g.key), str(body().get("id")))
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/reroll", methods=["POST"])
@auth
def lobby_reroll(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.reroll(lobby.get(g.key))
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/sabotage", methods=["POST"])
@auth
def lobby_sabotage(code):
    data = body()
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.buy_sabotage(lobby.get(g.key), str(data.get("id")), str(data.get("target") or "").lower())
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/ready", methods=["POST"])
@auth
def lobby_ready(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        lobby.set_ready(lobby.get(g.key))
        lobby.tick(time.time())
        return state_response(lobby, g.key)


@app.route("/api/lobby/<code>/emote", methods=["POST"])
@auth
def lobby_emote(code):
    with LOCK:
        lobby = lobby_of(code, g.key)
        p = lobby.get(g.key)
        e = body().get("e")
        if e not in EMOTES:
            raise GameError("Unbekanntes Emoji.")
        now = time.time()
        if now - p.emote_at < 1.0:
            raise GameError("Nicht so schnell.", 429)
        p.emote = e
        p.emote_at = now
        lobby.touch()
        return state_response(lobby, g.key)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "deine-IP"


def main():
    load_db()
    start_background()
    print("\n  ♣ GLÜCKSGRUBE läuft!")
    print("  Lokal:        http://localhost:%d" % PORT)
    print("  Für Freunde:  http://%s:%d   (gleiches WLAN)\n" % (lan_ip(), PORT))
    app.run(host=HOST, port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
else:
    load_db()
