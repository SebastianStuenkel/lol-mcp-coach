#!/usr/bin/env python3
"""
League of Legends MCP Coach Server
Fetches match data from Riot API and provides coaching analysis via Claude.
"""

import os
import asyncio
from typing import Any
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from dotenv import load_dotenv

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
SUMMONER_NAME = os.getenv("SUMMONER_NAME", "")
REGION = os.getenv("REGION", "euw1")  # euw1, na1, eun1, kr, ...

# Regional routing (für Match-V5 API)
REGIONAL_ROUTING = {
    "euw1": "europe",
    "eun1": "europe",
    "tr1":  "europe",
    "ru":   "europe",
    "na1":  "americas",
    "br1":  "americas",
    "la1":  "americas",
    "la2":  "americas",
    "kr":   "asia",
    "jp1":  "asia",
    "oc1":  "sea",
}

HEADERS = {"X-Riot-Token": RIOT_API_KEY}

app = Server("lol-coach")


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

async def riot_get(url: str) -> dict:
    """Macht einen GET-Request an die Riot API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=HEADERS, timeout=10.0)
        resp.raise_for_status()
        return resp.json()


async def get_puuid(summoner_name: str, tag: str = "EUW") -> str:
    """Holt die PUUID eines Spielers über Riot ID (Name#Tag)."""
    regional = REGIONAL_ROUTING.get(REGION, "europe")
    url = (
        f"https://{regional}.api.riotgames.com/riot/account/v1/accounts/by-riot-id"
        f"/{summoner_name}/{tag}"
    )
    data = await riot_get(url)
    return data["puuid"]


async def get_summoner_by_puuid(puuid: str) -> dict:
    """Holt Summoner-Infos (Level, Icon etc.) über PUUID."""
    url = f"https://{REGION}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    return await riot_get(url)


async def get_match_ids(puuid: str, count: int = 5, queue: int = None) -> list[str]:
    """Holt die letzten N Match-IDs eines Spielers.
    queue=420 → Ranked Solo/Duo
    queue=400 → Normal Draft
    queue=None → Alle Modi
    """
    regional = REGIONAL_ROUTING.get(REGION, "europe")
    queue_param = f"&queue={queue}" if queue else ""
    url = (
        f"https://{regional}.api.riotgames.com/lol/match/v5/matches/by-puuid"
        f"/{puuid}/ids?count={count}{queue_param}"
    )
    return await riot_get(url)


async def get_match_details(match_id: str) -> dict:
    """Holt die vollständigen Details eines Matches."""
    regional = REGIONAL_ROUTING.get(REGION, "europe")
    url = f"https://{regional}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    return await riot_get(url)


async def get_match_timeline(match_id: str) -> dict:
    """Holt die Timeline eines Matches (Frame-by-Frame Events)."""
    regional = REGIONAL_ROUTING.get(REGION, "europe")
    url = f"https://{regional}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    return await riot_get(url)


_champion_cache: dict[int, str] = {}

async def get_champion_id_map() -> dict[int, str]:
    """Fetcht Champion ID → Name Mapping von Data Dragon (wird gecacht)."""
    global _champion_cache
    if _champion_cache:
        return _champion_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5.0)
        latest = resp.json()[0]
        resp2 = await client.get(
            f"https://ddragon.leagueoflegends.com/cdn/{latest}/data/en_US/champion.json",
            timeout=10.0,
        )
        _champion_cache = {int(v["key"]): v["name"] for v in resp2.json()["data"].values()}
    return _champion_cache


async def get_live_game(puuid: str) -> dict:
    """Holt das aktuell laufende Spiel eines Spielers."""
    url = f"https://{REGION}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}"
    return await riot_get(url)


def extract_player_stats(match_data: dict, puuid: str) -> dict:
    """Extrahiert die relevanten Stats des Spielers aus einem Match."""
    participants = match_data["info"]["participants"]
    player = next((p for p in participants if p["puuid"] == puuid), None)

    if not player:
        return {}

    game_duration_min = match_data["info"]["gameDuration"] / 60

    return {
        "champion": player["championName"],
        "win": player["win"],
        "kills": player["kills"],
        "deaths": player["deaths"],
        "assists": player["assists"],
        "kda": round((player["kills"] + player["assists"]) / max(player["deaths"], 1), 2),
        "cs": player["totalMinionsKilled"] + player["neutralMinionsKilled"],
        "cs_per_min": round((player["totalMinionsKilled"] + player["neutralMinionsKilled"]) / game_duration_min, 1),
        "vision_score": player["visionScore"],
        "vision_per_min": round(player["visionScore"] / game_duration_min, 2),
        "damage_dealt": player["totalDamageDealtToChampions"],
        "damage_taken": player["totalDamageTaken"],
        "gold_earned": player["goldEarned"],
        "gold_per_min": round(player["goldEarned"] / game_duration_min, 0),
        "kills_participation": round(
            (player["kills"] + player["assists"]) /
            max(match_data["info"]["teams"][0 if player["teamId"] == 100 else 1]["objectives"]["champion"]["kills"], 1)
            * 100, 1
        ),
        "position": player.get("teamPosition", "UNKNOWN"),
        "game_duration_min": round(game_duration_min, 1),
        "game_result": "WIN" if player["win"] else "LOSS",
        "items": [
            player[f"item{i}"] for i in range(7) if player.get(f"item{i}", 0) != 0
        ],
        "summoner1": player.get("summoner1Id"),
        "summoner2": player.get("summoner2Id"),
        "double_kills": player.get("doubleKills", 0),
        "triple_kills": player.get("tripleKills", 0),
        "quadra_kills": player.get("quadraKills", 0),
        "penta_kills": player.get("pentaKills", 0),
        "first_blood": player.get("firstBloodKill", False),
        "turrets_destroyed": player.get("turretKills", 0),
        "wards_placed": player.get("wardsPlaced", 0),
        "wards_killed": player.get("wardsKilled", 0),
        "control_wards": player.get("visionWardsBoughtInGame", 0),
        # Zeit & Sterben
        "time_spent_dead": player.get("totalTimeSpentDead", 0),
        "time_spent_dead_per_min": round(player.get("totalTimeSpentDead", 0) / game_duration_min, 2),
        "longest_time_alive": player.get("longestTimeSpentLiving", 0),
        # Damage-Breakdown
        "damage_physical": player.get("physicalDamageDealtToChampions", 0),
        "damage_magic": player.get("magicDamageDealtToChampions", 0),
        "damage_true": player.get("trueDamageDealtToChampions", 0),
        "damage_to_objectives": player.get("damageDealtToObjectives", 0),
        "damage_to_turrets": player.get("damageDealtToTurrets", 0),
        "damage_self_mitigated": player.get("damageSelfMitigated", 0),
        # Objectives & Map
        "objectives_stolen": player.get("objectivesStolen", 0),
        "baron_kills": player.get("baronKills", 0),
        "dragon_kills": player.get("dragonKills", 0),
        "inhibitor_kills": player.get("inhibitorKills", 0),
        # CC & Utility
        "time_ccing_others": player.get("timeCCingOthers", 0),
        "total_cc_dealt": player.get("totalTimeCCDealt", 0),
        # Healing
        "total_heal": player.get("totalHeal", 0),
        "total_heals_on_teammates": player.get("totalHealsOnTeammates", 0),
        "total_damage_shielded": player.get("totalDamageShieldedOnTeammates", 0),
        # Consumables & Items
        "consumables_purchased": player.get("consumablesPurchased", 0),
        "items_purchased": player.get("itemsPurchased", 0),
        # Spell casts
        "spell_q_casts": player.get("spell1Casts", 0),
        "spell_w_casts": player.get("spell2Casts", 0),
        "spell_e_casts": player.get("spell3Casts", 0),
        "spell_r_casts": player.get("spell4Casts", 0),
    }


def extract_timeline_stats(match_data: dict, timeline_data: dict, puuid: str) -> dict:
    """Extrahiert Early-Game-Diffs, Tod-Timeline und Objective-Events aus der Match-Timeline."""
    participants = match_data["info"]["participants"]
    player = next((p for p in participants if p["puuid"] == puuid), None)
    if not player:
        return {}

    pid = str(player["participantId"])
    player_team = player["teamId"]
    player_position = player.get("teamPosition", "")

    opponent = next(
        (p for p in participants
         if p["teamId"] != player_team and p.get("teamPosition") == player_position and player_position),
        None,
    )
    opp_pid = str(opponent["participantId"]) if opponent else None

    frames = timeline_data["info"]["frames"]

    def pframe(minute: int, p: str) -> dict:
        if minute >= len(frames):
            return {}
        return frames[minute].get("participantFrames", {}).get(p, {})

    def ts_min(ts: int) -> float:
        return round(ts / 60000, 1)

    stats: dict = {
        "lane_opponent": opponent["championName"] if opponent else None,
        "player_team": player_team,
        "has_opponent_data": opp_pid is not None,
    }

    for minute in [10, 15]:
        pf = pframe(minute, pid)
        gold = pf.get("totalGold", 0)
        cs = pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0)
        stats[f"gold_{minute}"] = gold
        stats[f"cs_{minute}"] = cs
        stats[f"level_{minute}"] = pf.get("level", 0)
        if opp_pid:
            of = pframe(minute, opp_pid)
            stats[f"gold_diff_{minute}"] = gold - of.get("totalGold", 0)
            opp_cs = of.get("minionsKilled", 0) + of.get("jungleMinionsKilled", 0)
            stats[f"cs_diff_{minute}"] = cs - opp_cs

    all_events = [e for frame in frames for e in frame.get("events", [])]

    stats["death_events"] = [
        {"minute": ts_min(e["timestamp"]), "position": e.get("position", {})}
        for e in all_events
        if e["type"] == "CHAMPION_KILL" and str(e.get("victimId", "")) == pid
    ]

    _monster_names = {
        ("DRAGON", "FIRE_DRAGON"): "Infernal Dragon",
        ("DRAGON", "WATER_DRAGON"): "Ocean Dragon",
        ("DRAGON", "AIR_DRAGON"): "Cloud Dragon",
        ("DRAGON", "EARTH_DRAGON"): "Mountain Dragon",
        ("DRAGON", "HEXTECH_DRAGON"): "Hextech Dragon",
        ("DRAGON", "CHEMTECH_DRAGON"): "Chemtech Dragon",
        ("DRAGON", "ELDER_DRAGON"): "Elder Dragon",
        ("BARON_NASHOR", ""): "Baron Nashor",
        ("RIFTHERALD", ""): "Rift Herald",
        ("HORDE", ""): "Voidgrub",
        ("ATAKHAN", ""): "Atakhan",
    }

    stats["objective_events"] = []
    for e in all_events:
        if e["type"] != "ELITE_MONSTER_KILL":
            continue
        mtype = e.get("monsterType", "")
        msub = e.get("monsterSubType", "")
        label = _monster_names.get((mtype, msub)) or _monster_names.get((mtype, "")) or mtype
        stats["objective_events"].append({
            "label": label,
            "minute": ts_min(e["timestamp"]),
            "team": e.get("killerTeamId"),
        })

    stats["tower_events"] = [
        {
            "lane": e.get("laneType", ""),
            "minute": ts_min(e["timestamp"]),
            "lost_by_team": e.get("teamId"),
        }
        for e in all_events
        if e["type"] == "BUILDING_KILL" and e.get("buildingType") == "TOWER_BUILDING"
    ]

    return stats


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_summoner_info",
            description="Holt Profil-Infos eines LoL Spielers (Level, Rank etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "summoner_name": {"type": "string", "description": "Summoner Name (ohne Tag)"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW (Standard: EUW)"},
                },
                "required": ["summoner_name"],
            },
        ),
        Tool(
            name="get_last_matches",
            description="Fetcht die letzten Games eines Spielers mit allen wichtigen Stats. Kann nach Modus filtern.",
            inputSchema={
                "type": "object",
                "properties": {
                    "summoner_name": {"type": "string", "description": "Summoner Name"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                    "count": {"type": "integer", "description": "Anzahl der Games (1-10, Standard: 5)"},
                    "queue_filter": {"type": "string", "description": "Modus-Filter: 'ranked' (Solo/Duo), 'normal' (Normal Draft), 'alle' (alle Modi, Standard)"},
                },
                "required": ["summoner_name"],
            },
        ),
        Tool(
            name="get_single_match",
            description="Analysiert ein einzelnes Match im Detail anhand der Match-ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "match_id": {"type": "string", "description": "Match ID z.B. EUW1_1234567890"},
                    "summoner_name": {"type": "string", "description": "Summoner Name des Spielers"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                },
                "required": ["match_id", "summoner_name"],
            },
        ),
        Tool(
            name="get_ranked_stats",
            description="Holt die aktuellen Ranked Stats (LP, Winrate, Tier) eines Spielers",
            inputSchema={
                "type": "object",
                "properties": {
                    "summoner_name": {"type": "string", "description": "Summoner Name"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                },
                "required": ["summoner_name"],
            },
        ),
        Tool(
            name="get_timeline_analysis",
            description=(
                "Analysiert die Match-Timeline: Gold/CS-Differenz vs. Lane-Gegner @ Min 10 & 15, "
                "Tod-Timeline nach Phase, Objective-Reihenfolge (Dragon/Baron/Herald) und Tower-Timeline."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "match_id": {"type": "string", "description": "Match ID z.B. EUW1_1234567890"},
                    "summoner_name": {"type": "string", "description": "Summoner Name des Spielers"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                },
                "required": ["match_id", "summoner_name"],
            },
        ),
        Tool(
            name="get_champion_pool",
            description=(
                "Analysiert den Champion-Pool eines Spielers über mehrere Games: "
                "Winrate, KDA, CS/min, Damage und Vision pro Champion. "
                "Ideal um Stärken/Schwächen im Champ-Pool zu erkennen."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summoner_name": {"type": "string", "description": "Summoner Name"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                    "count": {"type": "integer", "description": "Anzahl der Games (5-30, Standard: 20)"},
                    "queue_filter": {"type": "string", "description": "'ranked' (Standard), 'normal', 'alle'"},
                },
                "required": ["summoner_name"],
            },
        ),
        Tool(
            name="get_live_game",
            description=(
                "Zeigt das aktuell laufende Spiel eines Spielers: "
                "Champion-Kompositionen beider Teams, Summoner Spells, Bans und Spielmodus. "
                "Ideal für Pre-Game Analyse."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summoner_name": {"type": "string", "description": "Summoner Name"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                },
                "required": ["summoner_name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:

    summoner_name = arguments.get("summoner_name", SUMMONER_NAME)
    tag = arguments.get("tag", "EUW")

    try:
        # ----------------------------------------------------------------
        if name == "get_summoner_info":
            puuid = await get_puuid(summoner_name, tag)
            summoner = await get_summoner_by_puuid(puuid)
            result = (
                f"Summoner: {summoner_name}#{tag}\n"
                f"Level: {summoner['summonerLevel']}\n"
                f"PUUID: {puuid[:20]}...\n"
            )
            return [TextContent(type="text", text=result)]

        # ----------------------------------------------------------------
        elif name == "get_last_matches":
            count = min(arguments.get("count", 5), 10)
            queue_filter = arguments.get("queue_filter", "alle")

            # Queue ID mapping
            queue_map = {"ranked": 420, "normal": 400, "alle": None}
            queue_id = queue_map.get(queue_filter, None)
            queue_label = {"ranked": "Ranked Solo/Duo", "normal": "Normal Draft", "alle": "Alle Modi"}.get(queue_filter, "Alle Modi")

            puuid = await get_puuid(summoner_name, tag)
            match_ids = await get_match_ids(puuid, count, queue=queue_id)

            all_stats = []
            for match_id in match_ids:
                match_data = await get_match_details(match_id)
                stats = extract_player_stats(match_data, puuid)
                stats["match_id"] = match_id
                all_stats.append(stats)

            # Zusammenfassung bauen
            lines = [f"📊 Letzte {count} Games ({queue_label}) von {summoner_name}#{tag}\n"]
            lines.append("=" * 50)

            for i, s in enumerate(all_stats, 1):
                result_emoji = "✅" if s["win"] else "❌"
                lines.append(
                    f"\nGame {i} {result_emoji} | {s['champion']} ({s['position']})"
                    f" | {s['game_duration_min']} min"
                )
                lines.append(
                    f"  KDA: {s['kills']}/{s['deaths']}/{s['assists']} ({s['kda']})"
                    f" | CS: {s['cs']} ({s['cs_per_min']}/min)"
                )
                lines.append(
                    f"  Vision: {s['vision_score']} ({s['vision_per_min']}/min)"
                    f" | KP: {s['kills_participation']}%"
                )
                lines.append(
                    f"  Damage: {s['damage_dealt']:,}"
                    f" | Gold: {s['gold_earned']:,} ({s['gold_per_min']}/min)"
                )
                lines.append(f"  Match ID: {s['match_id']}")

            # Durchschnitte berechnen
            wins = sum(1 for s in all_stats if s["win"])
            avg_kda = round(sum(s["kda"] for s in all_stats) / len(all_stats), 2)
            avg_cs = round(sum(s["cs_per_min"] for s in all_stats) / len(all_stats), 1)
            avg_vision = round(sum(s["vision_per_min"] for s in all_stats) / len(all_stats), 2)

            lines.append("\n" + "=" * 50)
            lines.append(f"📈 Durchschnitt über {count} Games:")
            lines.append(f"  Winrate: {wins}/{count} ({round(wins/count*100)}%)")
            lines.append(f"  Avg KDA: {avg_kda} | Avg CS/min: {avg_cs} | Avg Vision/min: {avg_vision}")

            avg_dead_pct = round(
                sum(s["time_spent_dead"] / (s["game_duration_min"] * 60) * 100 for s in all_stats) / len(all_stats), 1
            )
            avg_damage = round(sum(s["damage_dealt"] for s in all_stats) / len(all_stats))
            avg_cc = round(sum(s["time_ccing_others"] for s in all_stats) / len(all_stats), 1)
            lines.append(f"  Avg Damage an Champs: {avg_damage:,} | Avg Zeit tot: {avg_dead_pct}% der Spielzeit")
            if avg_cc:
                lines.append(f"  Avg CC-Zeit: {avg_cc}s")

            return [TextContent(type="text", text="\n".join(lines))]

        # ----------------------------------------------------------------
        elif name == "get_single_match":
            match_id = arguments["match_id"]
            puuid = await get_puuid(summoner_name, tag)
            match_data = await get_match_details(match_id)
            stats = extract_player_stats(match_data, puuid)

            # Alle Spieler für Team-Kontext
            participants = match_data["info"]["participants"]
            team_blue = [p for p in participants if p["teamId"] == 100]
            team_red = [p for p in participants if p["teamId"] == 200]

            lines = [f"🎮 Match Analyse: {match_id}"]
            lines.append(f"Spieler: {summoner_name}#{tag} | Champion: {stats['champion']}")
            lines.append(f"Ergebnis: {stats['game_result']} | Dauer: {stats['game_duration_min']} min\n")

            lines.append("── Deine Stats ──")
            lines.append(f"KDA: {stats['kills']}/{stats['deaths']}/{stats['assists']} = {stats['kda']}")
            lines.append(f"CS: {stats['cs']} ({stats['cs_per_min']}/min)")
            lines.append(f"Vision Score: {stats['vision_score']} | Wards: {stats['wards_placed']} platziert, {stats['wards_killed']} zerstört")
            lines.append(f"Control Wards: {stats['control_wards']}")
            lines.append(f"Kill Participation: {stats['kills_participation']}%")
            lines.append(f"Gold: {stats['gold_earned']:,} ({stats['gold_per_min']}/min)")

            lines.append("\n── Damage Breakdown ──")
            lines.append(f"Gesamt an Champions: {stats['damage_dealt']:,}")
            lines.append(f"  Physisch: {stats['damage_physical']:,} | Magisch: {stats['damage_magic']:,} | True: {stats['damage_true']:,}")
            lines.append(f"Damage genommen: {stats['damage_taken']:,} | Selbst mitigiert: {stats['damage_self_mitigated']:,}")
            lines.append(f"Damage an Objectives: {stats['damage_to_objectives']:,} | an Türmen: {stats['damage_to_turrets']:,}")

            lines.append("\n── Sterben & Überleben ──")
            dead_pct = round(stats['time_spent_dead'] / (stats['game_duration_min'] * 60) * 100, 1)
            lines.append(f"Zeit tot: {stats['time_spent_dead']}s ({dead_pct}% der Spielzeit) | {stats['time_spent_dead_per_min']}s/min")
            lines.append(f"Längste Lebenszeit am Stück: {stats['longest_time_alive']}s")

            if stats["time_ccing_others"] or stats["total_heal"] or stats["total_heals_on_teammates"]:
                lines.append("\n── Utility ──")
                if stats["time_ccing_others"]:
                    lines.append(f"CC-Zeit auf Gegnern: {stats['time_ccing_others']}s")
                if stats["total_heal"]:
                    lines.append(f"Heal (gesamt): {stats['total_heal']:,} | an Teammates: {stats['total_heals_on_teammates']:,}")
                if stats["total_damage_shielded"]:
                    lines.append(f"Shields auf Teammates: {stats['total_damage_shielded']:,}")

            lines.append("\n── Objectives & Map ──")
            lines.append(f"Türme zerstört: {stats['turrets_destroyed']} | Inhibitoren: {stats['inhibitor_kills']}")
            lines.append(f"Baron Kills: {stats['baron_kills']} | Dragon Kills: {stats['dragon_kills']}")
            if stats["objectives_stolen"]:
                lines.append(f"Objectives gestohlen: {stats['objectives_stolen']}")

            lines.append("\n── Spell Casts ──")
            lines.append(f"Q: {stats['spell_q_casts']}x | W: {stats['spell_w_casts']}x | E: {stats['spell_e_casts']}x | R: {stats['spell_r_casts']}x")
            lines.append(f"Consumables gekauft: {stats['consumables_purchased']} | Items insgesamt: {stats['items_purchased']}")

            if stats["penta_kills"]: lines.append(f"\n🏆 PENTA KILL!")
            elif stats["quadra_kills"]: lines.append(f"\n🔥 Quadra Kill!")
            elif stats["triple_kills"]: lines.append(f"\nTriple Kill: {stats['triple_kills']}x")

            lines.append("\n── Team Übersicht ──")
            player_team_id = next(p["teamId"] for p in participants if p["puuid"] == puuid)
            own_team = team_blue if player_team_id == 100 else team_red
            enemy_team = team_red if player_team_id == 100 else team_blue

            lines.append("Dein Team:")
            for p in own_team:
                marker = " ← DU" if p["puuid"] == puuid else ""
                lines.append(f"  {p['championName']} ({p['teamPosition']}) - {p['kills']}/{p['deaths']}/{p['assists']}{marker}")

            lines.append("Gegner:")
            for p in enemy_team:
                lines.append(f"  {p['championName']} ({p['teamPosition']}) - {p['kills']}/{p['deaths']}/{p['assists']}")

            return [TextContent(type="text", text="\n".join(lines))]

        # ----------------------------------------------------------------
        elif name == "get_ranked_stats":
            puuid = await get_puuid(summoner_name, tag)
            summoner = await get_summoner_by_puuid(puuid)
            summoner_id = summoner["id"]

            url = f"https://{REGION}.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"
            ranked_data = await riot_get(url)

            if not ranked_data:
                return [TextContent(type="text", text=f"{summoner_name} ist noch nicht ranked diese Season.")]

            lines = [f"🏆 Ranked Stats: {summoner_name}#{tag}"]
            for entry in ranked_data:
                queue = "Solo/Duo" if entry["queueType"] == "RANKED_SOLO_5x5" else "Flex"
                winrate = round(entry["wins"] / (entry["wins"] + entry["losses"]) * 100, 1)
                lines.append(f"\n{queue}:")
                lines.append(f"  Tier: {entry['tier']} {entry['rank']} - {entry['leaguePoints']} LP")
                lines.append(f"  Winrate: {entry['wins']}W / {entry['losses']}L ({winrate}%)")
                if entry.get("hotStreak"):
                    lines.append("  🔥 Hot Streak!")
                if entry.get("veteran"):
                    lines.append("  ⚔️ Veteran")

            return [TextContent(type="text", text="\n".join(lines))]

        # ----------------------------------------------------------------
        elif name == "get_timeline_analysis":
            match_id = arguments["match_id"]
            puuid = await get_puuid(summoner_name, tag)
            match_data, timeline_data = await asyncio.gather(
                get_match_details(match_id),
                get_match_timeline(match_id),
            )
            base = extract_player_stats(match_data, puuid)
            tl = extract_timeline_stats(match_data, timeline_data, puuid)

            p_team = tl.get("player_team", 100)
            lines = [f"🕐 Timeline Analyse: {match_id}"]
            lines.append(f"Spieler: {summoner_name}#{tag} | {base['champion']} ({base['position']})")
            if tl.get("lane_opponent"):
                lines.append(f"Lane-Gegner: {tl['lane_opponent']}")
            lines.append(f"Ergebnis: {base['game_result']} | {base['game_duration_min']} min\n")

            # ── Laning Phase ──
            lines.append("── Frühphase (Laning) ──")
            game_long_enough = tl.get("gold_10", 0) > 0

            def diff_str(val: int) -> str:
                return f"+{val:,}" if val >= 0 else f"{val:,}"

            if game_long_enough:
                has_15 = tl.get("gold_15", 0) > 0
                col_10 = "@ Min 10"
                col_15 = "@ Min 15" if has_15 else ""
                lines.append(f"{'':16} {col_10:>10}{'':4}{col_15:>10}")

                def row(label, key10, key15, fmt=lambda v: f"{v:>10,}"):
                    r = f"{label:<16} {fmt(tl.get(key10, 0))}"
                    if has_15:
                        r += f"    {fmt(tl.get(key15, 0))}"
                    return r

                def diff_row(label, key10, key15):
                    r = f"{label:<16} {diff_str(tl.get(key10, 0)):>10}"
                    if has_15:
                        r += f"    {diff_str(tl.get(key15, 0)):>10}"
                    return r

                lines.append(row("Gold:", "gold_10", "gold_15"))
                if tl.get("has_opponent_data"):
                    lines.append(diff_row("  vs Gegner:", "gold_diff_10", "gold_diff_15"))
                lines.append(row("CS:", "cs_10", "cs_15", fmt=lambda v: f"{v:>10}"))
                if tl.get("has_opponent_data"):
                    lines.append(diff_row("  vs Gegner:", "cs_diff_10", "cs_diff_15"))
                lines.append(row("Level:", "level_10", "level_15", fmt=lambda v: f"{v:>10}"))
            else:
                lines.append("  (Spiel zu kurz für Laning-Daten)")

            # ── Tod-Timeline ──
            deaths = tl.get("death_events", [])
            lines.append("\n── Tod-Timeline ──")
            if deaths:
                phases = [
                    ("Early (0-15 min)", lambda m: m <= 15),
                    ("Mid   (15-25 min)", lambda m: 15 < m <= 25),
                    ("Late  (25+ min)", lambda m: m > 25),
                ]
                for phase_name, pred in phases:
                    times = [d["minute"] for d in deaths if pred(d["minute"])]
                    count_str = f"{len(times)}x" if times else "0x"
                    timing_str = f" ({', '.join(f'min {t}' for t in times)})" if times else ""
                    lines.append(f"  {phase_name}: {count_str}{timing_str}")
            else:
                lines.append("  Keine Tode — perfektes Spiel!")

            # ── Objectives ──
            objectives = tl.get("objective_events", [])
            if objectives:
                lines.append("\n── Objektive ──")
                for obj in objectives:
                    team_label = "Dein Team  " if obj["team"] == p_team else "Gegner     "
                    lines.append(f"  min {obj['minute']:>5}  │  {obj['label']:<20} →  {team_label}")

            # ── Tower Timeline ──
            towers = tl.get("tower_events", [])
            if towers:
                lines.append("\n── Tower Timeline ──")
                lane_map = {"TOP_LANE": "Top", "MID_LANE": "Mid", "BOT_LANE": "Bot"}
                for t in towers:
                    lane = lane_map.get(t["lane"], t["lane"])
                    loser = "Dein Team" if t["lost_by_team"] == p_team else "Gegner   "
                    lines.append(f"  min {t['minute']:>5}  │  {lane} Tower  →  {loser} verloren")

            return [TextContent(type="text", text="\n".join(lines))]

        # ----------------------------------------------------------------
        elif name == "get_champion_pool":
            count = min(arguments.get("count", 10), 20)  # Max 20, Standard 10
            queue_filter = arguments.get("queue_filter", "alle")

            queue_map = {"ranked": 420, "normal": 400, "alle": None}
            queue_id = queue_map.get(queue_filter, None)

            puuid = await get_puuid(summoner_name, tag)
            match_ids = await get_match_ids(puuid, count, queue=queue_id)

            champion_stats = {}
            for match_id in match_ids:
                await asyncio.sleep(1.2)  # Rate limit: max 20 req/s aber sicher bleiben
                match_data = await get_match_details(match_id)
                stats = extract_player_stats(match_data, puuid)
                champ = stats["champion"]

                if champ not in champion_stats:
                    champion_stats[champ] = {
                        "games": 0, "wins": 0, "kills": 0, "deaths": 0,
                        "assists": 0, "cs_per_min": [], "damage": [],
                        "vision_per_min": []
                    }

                c = champion_stats[champ]
                c["games"] += 1
                c["wins"] += 1 if stats["win"] else 0
                c["kills"] += stats["kills"]
                c["deaths"] += stats["deaths"]
                c["assists"] += stats["assists"]
                c["cs_per_min"].append(stats["cs_per_min"])
                c["damage"].append(stats["damage_dealt"])
                c["vision_per_min"].append(stats["vision_per_min"])

            lines = [f"🏆 Champion Pool Analyse: {summoner_name}#{tag} (letzte {count} Games)\n"]
            lines.append("=" * 50)

            for champ, c in sorted(champion_stats.items(), key=lambda x: -x[1]["games"]):
                winrate = round(c["wins"] / c["games"] * 100)
                kda = round((c["kills"] + c["assists"]) / max(c["deaths"], 1), 2)
                avg_cs = round(sum(c["cs_per_min"]) / len(c["cs_per_min"]), 1)
                avg_dmg = round(sum(c["damage"]) / len(c["damage"]))
                avg_vision = round(sum(c["vision_per_min"]) / len(c["vision_per_min"]), 2)

                lines.append(f"\n{champ} — {c['games']} Games | {winrate}% WR")
                lines.append(f"  KDA: {c['kills']}/{c['deaths']}/{c['assists']} = {kda}")
                lines.append(f"  CS/min: {avg_cs} | Avg Damage: {avg_dmg:,} | Vision/min: {avg_vision}")

            return [TextContent(type="text", text="\n".join(lines))]

        # ----------------------------------------------------------------
        elif name == "get_live_game":
            puuid = await get_puuid(summoner_name, tag)

            # Parallel: Champion-Map cachen + Live-Game fetchen
            # Live-Game 404 = Spieler nicht in einem Spiel → eigene Fehlermeldung
            try:
                champ_map, game_data = await asyncio.gather(
                    get_champion_id_map(),
                    get_live_game(puuid),
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return [TextContent(type="text", text=f"❌ {summoner_name}#{tag} ist gerade nicht in einem Spiel.")]
                raise

            queue_names = {
                420: "Ranked Solo/Duo", 440: "Ranked Flex",
                400: "Normal Draft", 430: "Normal Blind",
                450: "ARAM", 700: "Clash",
            }
            spell_names = {
                1: "Cleanse", 3: "Exhaust", 4: "Flash", 6: "Ghost",
                7: "Heal", 11: "Smite", 12: "Teleport", 13: "Clarity",
                14: "Ignite", 21: "Barrier", 32: "Mark",
            }

            queue_id = game_data.get("gameQueueConfigId", 0)
            queue_name = queue_names.get(queue_id, f"Queue {queue_id}")
            game_length = game_data.get("gameLength", 0)
            mins, secs = divmod(game_length, 60)

            participants = game_data.get("participants", [])
            player = next((p for p in participants if p.get("puuid") == puuid), None)
            p_team = player["teamId"] if player else 100
            own_team = [p for p in participants if p["teamId"] == p_team]
            enemy_team = [p for p in participants if p["teamId"] != p_team]

            bans = game_data.get("bannedChampions", [])
            def ban_names(team_id):
                return [
                    champ_map.get(b["championId"], f"#{b['championId']}")
                    for b in bans if b["teamId"] == team_id and b["championId"] != -1
                ]

            def fmt_player(p: dict, mark_self: bool = False) -> str:
                champ = champ_map.get(p["championId"], f"#{p['championId']}")
                sp1 = spell_names.get(p.get("spell1Id", 0), str(p.get("spell1Id", "?")))
                sp2 = spell_names.get(p.get("spell2Id", 0), str(p.get("spell2Id", "?")))
                name = p.get("riotId", "Unknown")
                marker = "  ← DU" if mark_self else ""
                return f"  {champ:<16}  {sp1} + {sp2:<9}  {name}{marker}"

            lines = [f"🔴 Live Game: {summoner_name}#{tag}"]
            lines.append(f"Modus: {queue_name}  |  Spielzeit: {mins}:{secs:02d}\n")

            own_bans = ban_names(p_team)
            enemy_bans = ban_names(200 if p_team == 100 else 100)
            if own_bans:
                lines.append(f"Dein Team Bans: {', '.join(own_bans)}")
            if enemy_bans:
                lines.append(f"Gegner Bans:    {', '.join(enemy_bans)}")
            if own_bans or enemy_bans:
                lines.append("")

            lines.append("── Dein Team ──")
            for p in own_team:
                lines.append(fmt_player(p, mark_self=p.get("puuid") == puuid))

            lines.append("\n── Gegner ──")
            for p in enemy_team:
                lines.append(fmt_player(p))

            return [TextContent(type="text", text="\n".join(lines))]

        # ----------------------------------------------------------------
        else:
            return [TextContent(type="text", text=f"Unbekanntes Tool: {name}")]

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return [TextContent(type="text", text="❌ API Key ungültig oder abgelaufen. Bitte neuen Key auf developer.riotgames.com generieren.")]
        elif e.response.status_code == 404:
            return [TextContent(type="text", text=f"❌ Spieler '{summoner_name}#{tag}' nicht gefunden.")]
        elif e.response.status_code == 429:
            return [TextContent(type="text", text="❌ Rate Limit erreicht. Bitte kurz warten.")]
        else:
            return [TextContent(type="text", text=f"❌ API Fehler: {e.response.status_code}")]
    except Exception as e:
        return [TextContent(type="text", text=f"❌ Fehler: {str(e)}")]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass