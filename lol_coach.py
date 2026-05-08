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


async def get_match_ids(puuid: str, count: int = 5) -> list[str]:
    """Holt die letzten N Match-IDs eines Spielers."""
    regional = REGIONAL_ROUTING.get(REGION, "europe")
    url = (
        f"https://{regional}.api.riotgames.com/lol/match/v5/matches/by-puuid"
        f"/{puuid}/ids?queue=420&count={count}"  # 420 = Solo/Duo Ranked
    )
    return await riot_get(url)


async def get_match_details(match_id: str) -> dict:
    """Holt die vollständigen Details eines Matches."""
    regional = REGIONAL_ROUTING.get(REGION, "europe")
    url = f"https://{regional}.api.riotgames.com/lol/match/v5/matches/{match_id}"
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
    }


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
            description="Fetcht die letzten Solo/Duo Ranked Games eines Spielers mit allen wichtigen Stats",
            inputSchema={
                "type": "object",
                "properties": {
                    "summoner_name": {"type": "string", "description": "Summoner Name"},
                    "tag": {"type": "string", "description": "Riot Tag z.B. EUW"},
                    "count": {"type": "integer", "description": "Anzahl der Games (1-10, Standard: 5)"},
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
            puuid = await get_puuid(summoner_name, tag)
            match_ids = await get_match_ids(puuid, count)

            all_stats = []
            for match_id in match_ids:
                match_data = await get_match_details(match_id)
                stats = extract_player_stats(match_data, puuid)
                stats["match_id"] = match_id
                all_stats.append(stats)

            # Zusammenfassung bauen
            lines = [f"📊 Letzte {count} Ranked Games von {summoner_name}#{tag}\n"]
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
            lines.append(f"Damage dealt: {stats['damage_dealt']:,} | Damage taken: {stats['damage_taken']:,}")
            lines.append(f"Gold: {stats['gold_earned']:,} ({stats['gold_per_min']}/min)")

            if stats["penta_kills"]: lines.append(f"🏆 PENTA KILL!")
            elif stats["quadra_kills"]: lines.append(f"🔥 Quadra Kill!")
            elif stats["triple_kills"]: lines.append(f"Triple Kill: {stats['triple_kills']}x")

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
    asyncio.run(main())