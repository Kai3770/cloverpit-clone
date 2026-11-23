"""Tests für die Glücksgrube.  Starten:  pytest -q"""
import json
import os
import random
import time

import pytest

os.environ["GRUBE_NO_BG"] = "1"
import Glucksgrube as G  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    G.reset_state(str(tmp_path / "spielstand.json"))
    G.RNG = random.Random(7)
    yield
    G.RNG = random.SystemRandom()


def make_lobby(*names, rounds=6):
    lobby = G.Lobby("TEST1", names[0].lower(), "t", rounds)
    for n in names:
        G.DB["players"][n.lower()] = dict(
            name=n, pin_hash="x", ruhm=0,
            stats=dict(games=0, wins=0, best_round=0, best_win=0, total_earned=0, jackpots=0))
        lobby.add_player(n.lower(), n)
        G.PLAYER_LOBBY[n.lower()] = lobby.code
    G.LOBBIES[lobby.code] = lobby
    return lobby


def to_spin(lobby):
    lobby.start_game()
    for p in lobby.players.values():
        lobby.buy_charm(p, p.offers[0])
    lobby.end_shop() if lobby.phase == "shop" else None
    assert lobby.phase == "spin"


# --------------------------------------------------------------------------- #
# Linien & Auszahlung
# --------------------------------------------------------------------------- #

def test_eval_line_basic_and_wild():
    assert G.eval_line(["cherry"] * 5) == ("cherry", 5)
    assert G.eval_line(["cherry", "cherry", "lemon", "cherry", "cherry"]) == ("cherry", 2)
    assert G.eval_line(["wild", "cherry", "cherry", "bell", "bell"]) == ("cherry", 3)
    assert G.eval_line(["cherry", "wild", "cherry", "wild", "lemon"]) == ("cherry", 4)
    assert G.eval_line(["wild"] * 5) == ("seven", 5)


def test_skulls_are_not_replaced_by_wild():
    assert G.eval_line(["skull", "skull", "skull", "bell", "bell"]) == ("skull", 3)
    assert G.eval_line(["skull", "wild", "skull", "skull", "skull"]) == ("skull", 1)
    s, n = G.eval_line(["wild", "skull", "skull", "skull", "skull"])
    assert s == "skull" and n == 0


def test_evaluate_three_cherries_on_middle_row():
    grid = [["lemon", "bell", "diamond", "lemon", "bell"],
            ["cherry", "cherry", "cherry", "bell", "lemon"],
            ["bell", "lemon", "lemon", "diamond", "diamond"]]
    res = G.evaluate(grid, G.compute_mods({}))
    assert res["win"] == G.SYMBOLS["cherry"]["value"] * G.LINE_MULT[3]
    assert res["pen"] == 0 and len(res["lines"]) == 1


def test_charm_multiplier_stacks():
    grid = [["x"] * 5, ["cherry"] * 5, ["x"] * 5]
    grid[0] = ["lemon", "bell", "diamond", "lemon", "bell"]
    grid[2] = ["bell", "lemon", "lemon", "diamond", "diamond"]
    base = G.evaluate(grid, G.compute_mods({}))["win"]
    one = G.evaluate(grid, G.compute_mods({"cherry": 1}))["win"]
    two = G.evaluate(grid, G.compute_mods({"cherry": 2}))["win"]
    assert base == 10 * 10 and one == 2 * base and two == 4 * base


def test_skull_penalty_reduced_by_ward():
    grid = [["skull"] * 5, ["lemon", "bell", "lemon", "bell", "lemon"], ["bell", "lemon", "bell", "lemon", "bell"]]
    assert G.evaluate(grid, G.compute_mods({}))["pen"] == G.SKULL_PEN[5]
    assert G.evaluate(grid, G.compute_mods({"ward": 1}))["pen"] < G.SKULL_PEN[5]


def test_debt_grows_and_shark_discounts():
    m0 = G.compute_mods({})
    ds = [G.debt_for(r, m0) for r in range(1, 8)]
    assert ds == sorted(ds) and ds[0] == G.DEBT_BASE
    assert G.debt_for(3, G.compute_mods({"shark": 1})) < G.debt_for(3, m0)


def test_every_charm_has_valid_fx():
    for cid, c in G.CHARMS.items():
        for key in c["fx"]:
            assert key in {"mult", "weight", "lenmult", "lives"} | set(G.MUL_KEYS) | set(G.ADD_KEYS), (cid, key)
        assert c["rarity"] in G.RARITY_ORDER
    for eid, e in G.EVENTS.items():
        for key in e["fx"]:
            assert key in {"mult", "weight", "lenmult"} | set(G.MUL_KEYS) | set(G.ADD_KEYS), (eid, key)
    G.compute_mods({cid: c["max"] for cid, c in G.CHARMS.items()}, [e["fx"] for e in G.EVENTS.values()])


def test_base_ev_is_sane():
    m = G.compute_mods({})
    w = G.symbol_weights(m, 1)
    total = sum(G.evaluate(G.random_grid(w), m)["win"] - G.evaluate(G.random_grid(w), m)["pen"] for _ in range(4000))
    assert 1.5 < total / 4000 < 8


# --------------------------------------------------------------------------- #
# Lobby-Ablauf
# --------------------------------------------------------------------------- #

def test_solo_flow_and_starter_pick():
    lobby = make_lobby("Solo")
    lobby.start_game()
    p = lobby.players["solo"]
    assert lobby.phase == "shop" and lobby.starter and len(p.offers) == 3
    cid = p.offers[0]
    lobby.buy_charm(p, cid)
    assert p.charms[cid] == 1 and p.coins == G.START_COINS and p.ready
    lobby.tick(time.time())
    assert lobby.phase == "spin" and lobby.round == 1 and p.spins_left >= G.BASE_SPINS


def test_starter_autopick_on_timeout():
    lobby = make_lobby("A", "B")
    lobby.start_game()
    lobby.deadline = time.time() - 1
    lobby.tick(time.time())
    assert lobby.phase == "spin"
    assert all(sum(p.charms.values()) == 1 for p in lobby.players.values())


def test_spin_rules_and_holds():
    lobby = make_lobby("A")
    to_spin(lobby)
    p = lobby.players["a"]
    lobby.spin(p, [])
    g1 = [row[:] for row in p.grid]
    holds_before = p.holds_left
    lobby.spin(p, [2])
    assert [r[2] for r in p.grid] == [r[2] for r in g1]
    assert p.holds_left == holds_before - 1
    with pytest.raises(G.GameError):
        lobby.spin(p, [0, 1, 2])          # mehr als erlaubt
    while p.spins_left > 0:
        lobby.spin(p, [])
    with pytest.raises(G.GameError):
        lobby.spin(p, [])


def test_pot_grows_and_jackpot_pays_out():
    lobby = make_lobby("A")
    to_spin(lobby)
    p = lobby.players["a"]
    start = lobby.pot
    lobby.spin(p, [])
    if p.last["jackpot"] == 0:
        assert lobby.pot == start + G.POT_PER_SPIN
    forced = [["seven", "seven", "seven", "lemon", "bell"], ["lemon", "bell", "lemon", "bell", "lemon"], ["bell", "lemon", "bell", "lemon", "bell"]]
    G.random_grid = lambda w, f=forced: [r[:] for r in f]
    try:
        lobby.pot = 200
        before = p.coins
        p.spins_left = 1
        lobby.spin(p, [])
        assert p.last["jackpot"] == 200 and p.coins >= before + 200 and lobby.pot == G.POT_START
    finally:
        import importlib
        importlib.reload(G)


def test_timeout_autospins_everyone_and_collects():
    lobby = make_lobby("A", "B")
    to_spin(lobby)
    lobby.deadline = time.time() - 1
    lobby.tick(time.time())
    assert lobby.phase == "collect" and lobby.result and len(lobby.result["rows"]) == 2
    assert all(p.spins_left == 0 for p in lobby.players.values())


def test_everyone_done_triggers_grace_then_collect():
    lobby = make_lobby("A")
    to_spin(lobby)
    p = lobby.players["a"]
    while p.spins_left:
        lobby.spin(p, [])
    lobby.tick(time.time())
    assert lobby.phase == "spin" and lobby.grace_at is not None
    lobby.finish(p)
    lobby.tick(time.time())
    assert lobby.phase == "collect"


def test_collector_pays_or_takes_life():
    lobby = make_lobby("Rich", "Poor")
    to_spin(lobby)
    rich, poor = lobby.players["rich"], lobby.players["poor"]
    rich.coins, poor.coins = 500, 0
    for p in (rich, poor):
        p.spins_left = 0
    lobby.end_spin_phase()
    due = G.debt_for(1, rich.mods())
    assert rich.coins == 500 - due + 0 or rich.coins >= 500 - due
    assert poor.lives == G.START_LIVES - 1 and poor.coins == G.PITY_COINS and poor.alive
    assert lobby.result["rows"][0]["paid"] and not lobby.result["rows"][1]["paid"]


def test_second_failure_eliminates_and_last_standing_wins():
    lobby = make_lobby("Rich", "Poor", rounds=10)
    to_spin(lobby)
    rich, poor = lobby.players["rich"], lobby.players["poor"]
    for _ in range(G.START_LIVES):
        rich.coins, poor.coins = 999, 0
        for p in (rich, poor):
            p.spins_left = 0
        lobby.phase = "spin"
        lobby.end_spin_phase()
        lobby.deadline = time.time() - 1
        lobby.tick(time.time())
        if lobby.phase == "shop":
            lobby.deadline = time.time() - 1
            lobby.tick(time.time())
    assert not poor.alive
    assert lobby.phase == "over" and lobby.final
    rows = {r["key"]: r for r in lobby.final["rows"]}
    assert rows["rich"]["place"] == 1 and rows["rich"]["won"] and not rows["poor"]["won"]
    assert G.DB["players"]["rich"]["stats"]["wins"] == 1 and G.DB["players"]["rich"]["ruhm"] > 0
    saved = json.load(open(G.DATA_FILE, encoding="utf-8"))
    assert saved["players"]["rich"]["stats"]["games"] == 1


def test_solo_reaching_last_round_wins():
    lobby = make_lobby("Solo", rounds=6)
    to_spin(lobby)
    p = lobby.players["solo"]
    for _ in range(6):
        p.coins = 99999
        p.spins_left = 0
        lobby.phase = "spin"
        lobby.end_spin_phase()
        lobby.deadline = time.time() - 1
        lobby.tick(time.time())
        if lobby.phase == "shop":
            lobby.deadline = time.time() - 1
            lobby.tick(time.time())
    assert lobby.phase == "over" and lobby.final["rows"][0]["won"]


# --------------------------------------------------------------------------- #
# Shop & Sabotage
# --------------------------------------------------------------------------- #

def shop_lobby():
    lobby = make_lobby("Ann", "Ben")
    to_spin(lobby)
    for p in lobby.players.values():
        p.spins_left = 0
        p.coins = 500
    lobby.end_spin_phase()
    lobby.deadline = time.time() - 1
    lobby.tick(time.time())
    assert lobby.phase == "shop" and not lobby.starter
    return lobby, lobby.players["ann"], lobby.players["ben"]


def test_buy_charm_costs_coins_and_removes_offer():
    lobby, a, b = shop_lobby()
    cid = a.offers[0]
    price = lobby.charm_price(a, cid)
    before = a.coins
    lobby.buy_charm(a, cid)
    assert a.coins == before - price and cid not in a.offers
    with pytest.raises(G.GameError):
        lobby.buy_charm(a, cid)
    a.coins = 0
    with pytest.raises(G.GameError):
        lobby.buy_charm(a, a.offers[0])


def test_angel_gives_life_and_reroll_costs():
    lobby, a, b = shop_lobby()
    a.offers = ["angel"]
    lobby.buy_charm(a, "angel")
    assert a.lives == G.START_LIVES + 1 or a.lives >= G.START_LIVES + 1
    before = a.coins
    lobby.reroll(a)
    assert a.coins == before - 6 and len(a.offers) == 4
    cost2 = 6 + 6 * a.rerolls
    lobby.reroll(a)
    assert a.coins == before - 6 - cost2


def test_thief_steals_and_mirror_reflects():
    lobby, a, b = shop_lobby()
    a.coins, b.coins = 100, 100
    lobby.buy_sabotage(a, "thief", "ben")
    stolen = min(100, 15 + 15)
    assert b.coins == 100 - stolen and a.coins == 100 - G.SABOTAGE["thief"]["price"] + stolen
    b.coins, a.coins = 100, 100
    lobby.buy_sabotage(b, "mirror", "")
    assert b.mirrors == 1
    lobby.buy_sabotage(a, "thief", "ben")
    assert b.mirrors == 0 and a.coins < 100 - G.SABOTAGE["thief"]["price"]    # Angreifer wurde selbst bestohlen


def test_frost_and_curse_apply_next_round():
    lobby, a, b = shop_lobby()
    lobby.buy_sabotage(a, "frost", "ben")
    lobby.buy_sabotage(a, "curse", "ben")
    assert b.frost == 1 and b.curse_next
    for p in lobby.players.values():
        p.ready = True
    lobby.tick(time.time())
    assert lobby.phase == "spin"
    assert b.spins_left == a.spins_left - 1 and b.cursed and not b.curse_next
    wa = G.symbol_weights(a.mods(), lobby.round, False)["skull"]
    wb = G.symbol_weights(b.mods(), lobby.round, True)["skull"]
    assert wb > wa


def test_sabotage_validation():
    lobby, a, b = shop_lobby()
    with pytest.raises(G.GameError):
        lobby.buy_sabotage(a, "thief", "ann")       # nicht selbst
    with pytest.raises(G.GameError):
        lobby.buy_sabotage(a, "thief", "nobody")
    with pytest.raises(G.GameError):
        lobby.buy_sabotage(a, "bogus", "ben")
    a.coins = 1
    with pytest.raises(G.GameError):
        lobby.buy_sabotage(a, "thief", "ben")
    a.coins = 9999
    for _ in range(G.MAX_SABOTAGES):
        lobby.buy_sabotage(a, "curse", "ben")
    with pytest.raises(G.GameError):
        lobby.buy_sabotage(a, "curse", "ben")


def test_shop_closes_when_all_ready():
    lobby, a, b = shop_lobby()
    lobby.set_ready(a)
    lobby.tick(time.time())
    assert lobby.phase == "shop"
    lobby.set_ready(b)
    lobby.tick(time.time())
    assert lobby.phase == "spin" and lobby.round == 2


def test_gamble_doubles_or_loses():
    lobby = make_lobby("A")
    to_spin(lobby)
    p = lobby.players["a"]
    p.gamble_pending, p.gambles_left, p.coins = 50, 2, 100
    G.RNG = type("R", (), {"random": lambda s: 0.0, "choices": random.Random(1).choices, "choice": random.Random(1).choice})()
    lobby.gamble(p)
    assert p.coins == 150 and p.gamble_pending == 100
    G.RNG = type("R", (), {"random": lambda s: 0.99, "choices": random.Random(1).choices, "choice": random.Random(1).choice})()
    lobby.gamble(p)
    assert p.coins == 50 and p.gamble_pending == 0
    with pytest.raises(G.GameError):
        lobby.gamble(p)


def test_leave_and_host_handover():
    lobby = make_lobby("A", "B")
    lobby.remove_player("a")
    assert lobby.host == "b" and "a" not in lobby.players
    lobby = make_lobby("C", "D")
    to_spin(lobby)
    lobby.remove_player("c")
    assert not lobby.players["c"].alive and lobby.host == "d"


def test_cannot_join_running_or_full_game():
    lobby = make_lobby("A")
    lobby.start_game()
    with pytest.raises(G.GameError):
        lobby.add_player("x", "X")
    lobby2 = make_lobby(*["P%d" % i for i in range(G.MAX_PLAYERS)])
    with pytest.raises(G.GameError):
        lobby2.add_player("extra", "Extra")


def test_restart_resets_players():
    lobby = make_lobby("A", rounds=6)
    to_spin(lobby)
    lobby.finish_game()
    lobby.restart()
    p = lobby.players["a"]
    assert lobby.phase == "wait" and p.coins == G.START_COINS and p.charms == {}


# --------------------------------------------------------------------------- #
# HTTP-API
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    return G.app.test_client()


def reg(client, name, pin="geheim1"):
    r = client.post("/api/register", json=dict(name=name, pin=pin))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


def hdr(tok):
    return {"X-Token": tok}


def test_register_login_and_validation(client):
    assert client.post("/api/register", json=dict(name="x", pin="1234")).status_code == 400
    assert client.post("/api/register", json=dict(name="Okay", pin="12")).status_code == 400
    reg(client, "Okay")
    assert client.post("/api/register", json=dict(name="okay", pin="geheim1")).status_code == 400
    assert client.post("/api/login", json=dict(name="okay", pin="falsch")).status_code == 401
    r = client.post("/api/login", json=dict(name="okay", pin="geheim1"))
    assert r.status_code == 200 and r.get_json()["me"]["name"] == "Okay"
    assert "pin_hash" not in json.dumps(r.get_json())
    assert client.get("/api/me").status_code == 401


def test_pin_is_hashed_in_file(client):
    reg(client, "Hasher", "supergeheim")
    raw = open(G.DATA_FILE, encoding="utf-8").read()
    assert "supergeheim" not in raw and "pbkdf2" in raw or "scrypt" in raw


def test_http_full_round_two_players(client):
    ta, tb = reg(client, "Alice"), reg(client, "Bobby")
    r = client.post("/api/lobby/create", json=dict(name="Test", rounds=6), headers=hdr(ta)).get_json()
    code = r["code"]
    assert len(code) == 5
    assert client.post("/api/lobby/join", json=dict(code="ZZZZZ"), headers=hdr(tb)).status_code == 404
    assert client.post("/api/lobby/join", json=dict(code=code.lower()), headers=hdr(tb)).status_code == 200
    assert client.post("/api/lobby/%s/start" % code, headers=hdr(tb)).status_code == 403
    st = client.post("/api/lobby/%s/start" % code, headers=hdr(ta)).get_json()
    assert st["phase"] == "shop" and st["starter"] and len(st["me"]["offers"]) == 3
    for tok in (ta, tb):
        offer = client.get("/api/lobby/%s/state" % code, headers=hdr(tok)).get_json()["me"]["offers"][0]["id"]
        assert client.post("/api/lobby/%s/buy" % code, json=dict(id=offer), headers=hdr(tok)).status_code == 200
    st = client.get("/api/lobby/%s/state" % code, headers=hdr(ta)).get_json()
    assert st["phase"] == "spin" and st["round"] == 1
    # Dreh
    n0 = st["me"]["spins_left"]
    st = client.post("/api/lobby/%s/spin" % code, json=dict(hold=[]), headers=hdr(ta)).get_json()
    assert st["me"]["spins_left"] == n0 - 1 and st["me"]["last"]["grid"]
    # Gegner sieht das Raster, aber keine privaten Daten
    other = client.get("/api/lobby/%s/state" % code, headers=hdr(tb)).get_json()
    alice = next(p for p in other["players"] if p["name"] == "Alice")
    assert alice["grid"] == st["me"]["grid"] and "offers" not in alice
    # same-Antwort
    v = other["v"]
    assert client.get("/api/lobby/%s/state?v=%d" % (code, v), headers=hdr(tb)).get_json().get("same") is True
    # Fremde dürfen nicht
    tc = reg(client, "Carol")
    assert client.get("/api/lobby/%s/state" % code, headers=hdr(tc)).status_code == 404
    assert client.post("/api/lobby/%s/spin" % code, json={}, headers=hdr(tc)).status_code == 404
    # ungültige Eingaben
    assert client.post("/api/lobby/%s/spin" % code, json=dict(hold="abc"), headers=hdr(ta)).status_code == 200
    assert client.post("/api/lobby/%s/emote" % code, json=dict(e="<script>"), headers=hdr(ta)).status_code == 400
    assert client.post("/api/lobby/%s/emote" % code, json=dict(e="🔥"), headers=hdr(ta)).status_code == 200


def test_cannot_open_second_table_while_in_game(client):
    ta = reg(client, "Dora")
    code = client.post("/api/lobby/create", json={}, headers=hdr(ta)).get_json()["code"]
    client.post("/api/lobby/%s/start" % code, headers=hdr(ta))
    assert client.post("/api/lobby/create", json={}, headers=hdr(ta)).status_code == 400


def test_leaderboard_and_catalog(client):
    t = reg(client, "Eve")
    assert client.get("/api/leaderboard", headers=hdr(t)).get_json()["rows"][0]["name"] == "Eve"
    cat = client.get("/api/catalog").get_json()
    assert len(cat["charms"]) == len(G.CHARMS) and "fx" not in cat["charms"][0]


def test_names_are_not_html(client):
    r = client.post("/api/register", json=dict(name="<b>x</b>", pin="geheim1"))
    assert r.status_code == 400


def test_index_and_static_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


# --------------------------------------------------------------------------- #
# Erweiterung: Skalierung, Verkaufen, Ereignisse, neue Items
# --------------------------------------------------------------------------- #

def test_prices_scale_with_round_and_stacks():
    lobby, a, b = shop_lobby()
    a.offers = ["clover"]
    p1 = lobby.charm_price(a, "clover")
    s1 = lobby.sabo_price(a, "thief")
    r1 = lobby.reroll_cost(a)
    lobby.round = 8
    p8 = lobby.charm_price(a, "clover")
    assert p8 == round(G.CHARMS["clover"]["price"] * G.PRICE_GROWTH ** 7)
    assert p8 > p1 * 2 and lobby.sabo_price(a, "thief") > s1 * 2 and lobby.reroll_cost(a) > r1 * 2
    a.charms["clover"] = 2
    assert lobby.charm_price(a, "clover") > p8 * 1.8           # Stapel kosten extra
    a.charms["coupon"] = 1
    assert lobby.charm_price(a, "clover") < round(G.CHARMS["clover"]["price"] * 1.9 * G.PRICE_GROWTH ** 7)


def test_buy_sabotage_uses_scaled_price():
    lobby, a, b = shop_lobby()
    lobby.round = 6
    a.coins = 1000
    price = lobby.sabo_price(a, "curse")
    lobby.buy_sabotage(a, "curse", "ben")
    assert a.coins == 1000 - price and price > G.SABOTAGE["curse"]["price"]


def test_rarity_weights_and_legendary_gate():
    assert G.rarity_weights(0)["legendary"] == 0 and G.rarity_weights(G.LEGENDARY_FROM - 1)["legendary"] == 0
    assert G.rarity_weights(G.LEGENDARY_FROM)["legendary"] > 0
    assert G.rarity_weights(12)["epic"] > G.rarity_weights(2)["epic"]
    lobby, a, b = shop_lobby()
    lobby.round = 1
    for _ in range(60):
        assert all(G.CHARMS[c]["rarity"] != "legendary" for c in lobby.gen_offers(a, 5))
    lobby.round = 10
    seen = set()
    for _ in range(200):
        seen.update(G.CHARMS[c]["rarity"] for c in lobby.gen_offers(a, 5))
    assert "legendary" in seen
    lobby.round = G.EXTRA_OFFER_FROM
    assert lobby.offer_count() == 5


def test_starter_never_offers_legendary():
    for seed in range(40):
        G.RNG = random.Random(seed)
        lobby = make_lobby("Ann", "Ben")
        lobby.start_game()
        for p in lobby.players.values():
            assert len(p.offers) == 3 and all(G.CHARMS[c]["rarity"] != "legendary" for c in p.offers)
        G.LOBBIES.clear()


def test_sell_charm():
    lobby, a, b = shop_lobby()
    a.charms = {"clover": 2, "angel": 1}
    lobby.round = 5
    val = lobby.sell_value(a, "clover")
    assert val == int(G.CHARMS["clover"]["price"] * (1 + G.STACK_GROWTH) * G.PRICE_GROWTH ** 4 * G.SELL_RATE)
    before = a.coins
    assert lobby.sell_charm(a, "clover") == val
    assert a.coins == before + val and a.charms["clover"] == 1
    lobby.sell_charm(a, "clover")
    assert "clover" not in a.charms
    with pytest.raises(G.GameError):
        lobby.sell_charm(a, "clover")
    with pytest.raises(G.GameError):
        lobby.sell_charm(a, "angel")                       # Schutzengel ist unverkäuflich
    assert lobby.sell_value(a, "angel") == 0
    # Kauf + sofortiger Verkauf darf nie Gewinn bringen
    a.charms = {}
    a.coins = 10000
    for cid in G.CHARMS:
        a.offers = [cid]
        price = lobby.charm_price(a, cid)
        if G.CHARMS[cid]["fx"].get("lives"):
            continue
        lobby.buy_charm(a, cid)
        assert lobby.sell_value(a, cid) < price
        lobby.sell_charm(a, cid)


def test_sell_only_in_shop():
    lobby = make_lobby("Ann", "Ben")
    to_spin(lobby)
    a = lobby.players["ann"]
    cid = next(iter(a.charms))
    with pytest.raises(G.GameError):
        lobby.sell_charm(a, cid)


def test_events_announced_and_applied():
    lobby = make_lobby("Ann", "Ben", rounds=10)
    to_spin(lobby)
    G.RNG = random.Random(3)
    rounds_with_event = []
    for _ in range(9):
        for p in lobby.players.values():
            p.coins = 10 ** 7
            while p.spins_left > 0:
                lobby.spin(p)
        lobby.end_spin_phase()
        lobby.after_collect()
        if lobby.phase != "shop":
            break
        upcoming = lobby.round + 1
        assert (lobby.next_event is not None) == (upcoming % G.EVENT_EVERY == 0)
        announced = lobby.next_event
        view = G.lobby_view(lobby, "ann")
        assert (view["next_event"] or {}).get("id") == announced
        lobby.end_shop()
        assert lobby.event == announced and lobby.next_event is None
        if announced:
            rounds_with_event.append(lobby.round)
            assert G.lobby_view(lobby, "ann")["event"]["id"] == announced
    assert rounds_with_event == [3, 6, 9]


def test_event_effects_on_spins_and_debt():
    lobby = make_lobby("Ann", rounds=6)
    to_spin(lobby)
    a = lobby.players["ann"]
    a.charms = {}
    lobby.event = "mercy"
    m = lobby.mods_of(a)
    assert lobby.due_of(a, 4, m) < G.debt_for(4, G.compute_mods({}))
    lobby.event = "bonus"
    assert lobby.mods_of(a)["spins"] == 2
    lobby.event = "drought"
    assert lobby.mods_of(a)["spins"] == -1


def test_tax_chain_bribe():
    lobby, a, b = shop_lobby()
    a.coins = b.coins = 5000
    lobby.buy_sabotage(a, "tax", "ben")
    lobby.buy_sabotage(a, "chain", "ben")
    lobby.buy_sabotage(b, "bribe", "")
    assert b.tax_next == 1 and b.chain_next and b.bribe_next == 1
    lobby.buy_sabotage(b, "bribe", "")
    with pytest.raises(G.GameError):
        lobby.buy_sabotage(b, "bribe", "")                 # max 2 Bestechungen
    for p in lobby.players.values():
        p.ready = True
    lobby.tick(time.time())
    assert lobby.phase == "spin" and b.chained and b.holds_left == 0 and b.taxed == 1 and b.bribed == 2
    with pytest.raises(G.GameError):
        lobby.spin(b, [0])
    base = G.debt_for(lobby.round, lobby.mods_of(b))
    assert lobby.due_of(b, lobby.round, lobby.mods_of(b), b.taxed, b.bribed) == max(1, -(-base * 1.2 * 0.75 ** 2 // 1))
    for p in lobby.players.values():
        while p.spins_left > 0:
            lobby.spin(p)
    b.coins = 10 ** 6
    lobby.end_spin_phase()
    row = next(r for r in lobby.result["rows"] if r["key"] == "ben")
    assert row["due"] == lobby.due_of(b, lobby.round, lobby.mods_of(b), 1, 2)


def test_thief_scales_and_vault_guards():
    lobby, a, b = shop_lobby()
    lobby.round = 6
    a.coins, b.coins = 1000, 200
    lobby.apply_sabotage("thief", a, b)
    full = int(15 * G.price_scale(6) + 200 * 0.15)
    assert b.coins == 200 - full
    b.coins = 200
    b.charms["vault"] = 1
    lobby.apply_sabotage("thief", a, b)
    assert b.coins == 200 - int(full * 0.5)


def test_new_effects_in_evaluate():
    grid = [["cherry"] * 3 + ["lemon", "bell"], ["lemon", "bell", "clover", "diamond", "cherry"],
            ["bell", "clover", "diamond", "cherry", "lemon"]]
    base = G.evaluate(grid, G.compute_mods({}))["win"]
    assert G.evaluate(grid, G.compute_mods({"fishes": 1}))["win"] == round(base * 1.4)
    assert G.evaluate(grid, G.compute_mods({}), boost=2.0)["win"] == base * 2
    skulls = [["skull"] * 3 + ["lemon", "bell"], ["lemon", "bell", "clover", "diamond", "cherry"],
              ["bell", "clover", "diamond", "cherry", "lemon"]]
    r = G.evaluate(skulls, G.compute_mods({}))
    assert r["pen"] == G.SKULL_PEN[3] and r["win"] == 0
    r = G.evaluate(skulls, G.compute_mods({"grave": 1}))
    assert r["pen"] == 0 and r["win"] == G.SKULL_PEN[3] * 2 and r["lines"][0]["coins"] > 0


def test_first_last_boost_and_consolation():
    lobby = make_lobby("Ann", rounds=6)
    to_spin(lobby)
    a = lobby.players["ann"]
    a.charms = {"early": 1, "finale": 1, "coin": 1}
    a.spins_left, a.spin_no = 3, 0
    boosts = []
    for _ in range(3):
        last = lobby.spin(a)
        boosts.append(last["boost"])
        if last["win"] == 0:
            assert last["flat"] == 5
    assert boosts == [2.0, 1.0, 2.5]


def test_cat_can_save_a_life():
    lobby = make_lobby("Ann", rounds=6)
    to_spin(lobby)
    a = lobby.players["ann"]
    a.charms = {"cat": 2}
    for p in lobby.players.values():
        while p.spins_left > 0:
            lobby.spin(p)

    class Lucky:
        def random(self):
            return 0.0
    old = G.RNG
    G.RNG = Lucky()
    a.coins = 0
    lives = a.lives
    lobby.end_spin_phase()
    G.RNG = old
    assert a.lives == lives and a.saves == 1 and lobby.result["rows"][0]["saved"]


def test_pot_scales():
    lobby = make_lobby("Ann", rounds=10)
    to_spin(lobby)
    lobby.round = 7
    a = lobby.players["ann"]
    before = lobby.pot
    G.RNG = random.Random(1)
    last = lobby.spin(a)
    if not last["jackpot"]:
        assert lobby.pot - before == round(G.POT_PER_SPIN * G.price_scale(7))


def test_http_sell_and_catalog(client):
    tok = client.post("/api/register", json={"name": "Seller", "pin": "1234"}).get_json()["token"]
    h = {"X-Token": tok}
    cat = client.get("/api/catalog").get_json()
    assert len(cat["charms"]) >= 30 and len(cat["sabotage"]) == 7 and len(cat["events"]) >= 6
    assert "legendary" in cat["rarities"]
    code = client.post("/api/lobby/create", json={"rounds": 6}, headers=h).get_json()["code"]
    lobby = G.LOBBIES[code]
    lobby.start_game()
    p = lobby.players["seller"]
    lobby.buy_charm(p, p.offers[0])
    lobby.end_shop()
    for _ in range(2):
        while p.spins_left > 0:
            lobby.spin(p)
        p.coins = 10 ** 5
        lobby.end_spin_phase()
        lobby.after_collect()
    assert lobby.phase == "shop"
    st = client.get("/api/lobby/%s/state" % code, headers=h).get_json()
    cid = next(iter(st["me"]["charms"]))
    val = st["me"]["sell_values"][cid]
    r = client.post("/api/lobby/%s/sell" % code, json={"id": cid}, headers=h)
    assert r.status_code == 200 and r.get_json()["me"]["coins"] == st["me"]["coins"] + val
    assert "price_scale" in st and set(st["me"]["sabo_prices"]) == set(G.SABOTAGE)
    assert client.post("/api/lobby/%s/sell" % code, json={"id": "bogus"}, headers=h).status_code == 400
