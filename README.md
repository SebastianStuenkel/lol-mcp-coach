# LoL MCP Coach

An MCP (Model Context Protocol) server that connects Claude to the Riot Games API, enabling AI-powered League of Legends coaching and match analysis.

## What it does

This server exposes your League of Legends match history and ranked stats to Claude as tools. Once connected, you can ask Claude to analyze your recent games, identify patterns in your play, compare performance across matches, and receive coaching feedback — all grounded in your actual match data.

## Tools exposed to Claude

| Tool | Description |
|---|---|
| `get_summoner_info` | Player profile: summoner name, level, PUUID |
| `get_last_matches` | Last N ranked Solo/Duo games with KDA, CS, vision, damage, gold |
| `get_single_match` | Full breakdown of a specific match by ID |
| `get_ranked_stats` | Current season LP, tier, rank, and winrate for Solo/Duo and Flex |

## Prerequisites

- Python 3.10+
- A Riot Games Developer API key (see below)
- Claude Desktop (or another MCP client)

## Getting a Riot API Key

1. Go to [developer.riotgames.com](https://developer.riotgames.com/)
2. Sign in with your Riot Games account
3. On the dashboard, a **Development API Key** is shown automatically — copy it
4. Development keys expire every 24 hours and have a rate limit of 20 requests/second. Regenerate it from the same dashboard page whenever it expires.

> For permanent keys without expiry, you can apply for a **Personal API Key** through the Riot developer portal by registering an application. Approval is not guaranteed and takes time, so the development key is fine for personal use.

## Installation

```bash
# Clone or navigate to the project
cd lol-mcp-coach

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install mcp httpx python-dotenv
```

## Configuration

Create a `.env` file in the project root:

```env
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SUMMONER_NAME=YourSummonerName
REGION=euw1
```

| Variable | Required | Description |
|---|---|---|
| `RIOT_API_KEY` | Yes | Your Riot developer API key |
| `SUMMONER_NAME` | No | Default summoner name used when none is passed to a tool |
| `REGION` | No | Default region (defaults to `euw1`) |

**Supported regions:** `euw1`, `eun1`, `na1`, `kr`, `br1`, `la1`, `la2`, `jp1`, `tr1`, `ru`, `oc1`

## Connecting to Claude Desktop

Add the server to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "lol-coach": {
      "command": "/absolute/path/to/lol-mcp-coach/venv/bin/python",
      "args": ["/absolute/path/to/lol-mcp-coach/lol_coach.py"]
    }
  }
}
```

Restart Claude Desktop after saving. The tools will appear automatically when the server is running.

## Running the server manually

```bash
source venv/bin/activate
python lol_coach.py
```

The server communicates over stdio and is designed to be launched by an MCP client. Running it directly will start it in a waiting state.

## Example prompts

Once connected to Claude:

- *"Analyze my last 5 ranked games and tell me what I should focus on."*
- *"What is my average KDA this week and how does my CS compare to average?"*
- *"Look at my ranked stats and give me a win condition based on my playstyle."*
- *"Break down match EUW1_1234567890 — what went wrong in the mid game?"*
