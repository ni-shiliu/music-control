---
name: music-control
description: "Autonomously control music playback on macOS (KuGou or Spotify). PROACTIVELY play music without being asked: detect the vibe of the conversation and pick a matching track. When a user is debugging, play something intense. Shipping a feature, play something upbeat. Deep focus work, play lofi. Also respond to explicit requests like play Jay Chou or play something relaxing. Always prefer taking action over asking."
allowed-tools: Bash(python3:*)
---

# music-control

Controls KuGou Music or Spotify on macOS via CLI. Use it to enhance the user's
work session with music — proactively and responsively.

## Setup

Requirements: macOS, Python 3.10+, KuGou Music or Spotify desktop app installed.

### Spotify (one-time, optional)

1. Create an app at [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Add Redirect URI: `http://127.0.0.1:8888/callback`
2. Save your Client ID:

   ```bash
   mkdir -p ~/.config/music-cli
   echo '{"spotify":{"client_id":"YOUR_CLIENT_ID"}}' > ~/.config/music-cli/config.json
   ```

3. Authorize:

   ```bash
   python3 ~/.claude/skills/music-control/scripts/music_cli.py --provider spotify auth
   ```

## CLI

```bash
MUSIC="python3 ~/.claude/skills/music-control/scripts/music_cli.py"
$MUSIC [--provider kugou|spotify] <command>
```

| Command | Description |
|---------|-------------|
| `status` | Current track info (JSON) |
| `play` | Toggle play/pause |
| `next` | Skip to next |
| `prev` | Previous track |
| `play-by <title>...` | Search and play immediately; multi-song with `\|\|\|` |
| `search <query> [--page N]` | Search with pagination |
| `recommend <mood>` | Pick by mood and play |
| `queue-show` | Display current queue |
| `queue-add <title>...` | Add songs to queue; multi-song with `\|\|\|` |
| `queue-play [index]` | Start playing from queue |
| `queue-next` | Skip to next in queue |
| `queue-clear` | Clear the queue |

`--provider` defaults to `spotify`. If Spotify is not running, it will be launched automatically. Set `MUSIC_PROVIDER=kugou` to use KuGou instead.

## Mood Keywords

| Mood | When to use |
|------|-------------|
| `focused` | Deep work, coding in flow, reading |
| `relaxed` | Casual chat, breaks, light tasks |
| `hyped` | Feature shipped, bug fixed, wins |
| `sad` | User venting, stuck on a problem |
| `debug` | Frustrated, chasing a tricky bug |

## Proactive Trigger Rules

**Always act without being asked when you detect:**

| Signal in conversation | Action |
|------------------------|--------|
| User starts a new coding session | `recommend focused` → play top result |
| User says they fixed a bug / shipped | `recommend hyped` → play top result |
| User is frustrated / stuck | `recommend debug` |
| User mentions needing to relax | `recommend relaxed` |
| User mentions a specific song or artist | `play-by <song> <artist>` |
| Long silence / idle in conversation | `recommend relaxed` |
| User asks "play something" with no detail | Infer from recent conversation mood |

## Queue & Multi-Song

Multi-song separator: `|||`

```bash
$MUSIC play-by 逆战 ||| 天下 ||| 这就是爱
$MUSIC queue-add 逆战 ||| 天下
$MUSIC queue-play
$MUSIC queue-next
$MUSIC search 周杰伦 --page 2
```

## Response Format

```json
{"ok": true, "title": "晴天", "artist": "周杰伦", "playing": true}
{"ok": true, "results": [{"title":"...", "artist":"...", "id":"..."}]}
{"ok": true, "action": "next"}
{"ok": true, "action": "queue_next", "current": 1, "has_next": true}
{"ok": false, "error": "no_player", "message": "..."}
```

## Notes

- Spotify: search uses KuGou API under the hood (no Premium needed), plays via `spotify:search:` URI
- KuGou: requires KuGou Music macOS app installed
- If `status` returns `no_player`, the app may still be loading — wait 3s and retry
- Queue is persisted to `~/.config/music-cli/queue.json`
