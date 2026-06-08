#!/usr/bin/env python3
"""music-cli — macOS 音乐控制 CLI（酷狗 / Spotify）"""
import os
import sys
import json
import re
import subprocess
import argparse
import urllib.parse
from typing import Optional

from providers import (
    MusicProvider, get_provider,
    MR_CMD_PLAY, MR_CMD_PAUSE, MR_CMD_TOGGLE, MR_CMD_NEXT, MR_CMD_PREV,
)

MOOD_MAP = {
    "focused": ["lofi hip hop", "深夜coding专注", "专注背景音乐"],
    "relaxed": ["轻音乐放松", "咖啡馆背景音乐", "ambient chill"],
    "hyped":   ["运动励志音乐", "电子舞曲", "榜单热歌"],
    "sad":     ["治愈系音乐", "温柔民谣", "安慰系歌曲"],
    "debug":   ["摇滚发泄", "燃烧金属", "punk rock"],
}


def out(data: dict):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def err(msg: str, code: str = "error"):
    out({"ok": False, "error": code, "message": msg})


# ── 状态检测（通用兜底）──────────────────────────────────────────────────────
def _nowplaying() -> Optional[dict]:
    try:
        r = subprocess.run(["nowplaying-cli", "get-raw"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        raw = json.loads(r.stdout)
        title = raw.get("kMRMediaRemoteNowPlayingInfoTitle", "")
        if not title:
            return None
        return {
            "title":   title,
            "artist":  raw.get("kMRMediaRemoteNowPlayingInfoArtist", ""),
            "album":   raw.get("kMRMediaRemoteNowPlayingInfoAlbum", ""),
            "playing": float(raw.get("kMRMediaRemoteNowPlayingInfoPlaybackRate", 0)) > 0,
            "source":  "mediaremote",
        }
    except (json.JSONDecodeError, ValueError):
        return None


def _kugou_window() -> Optional[dict]:
    script = '''
tell application "System Events"
    set procs to every process whose name contains "KuGou"
    if length of procs > 0 then
        set p to item 1 of procs
        if (count of windows of p) > 0 then
            return title of window 1 of p
        end if
    end if
end tell
return ""'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    title = r.stdout.strip()
    if not title:
        return None
    parts = re.split(r"\s*[-—]\s*", title)
    if len(parts) >= 2:
        song, artist = parts[0].strip(), parts[1].strip()
        if song and song not in ("酷狗音乐", "KuGou Music", "KuGou"):
            return {"title": song, "artist": artist, "album": "", "playing": None, "source": "window_title"}
    return None


def detect_status(provider: Optional[MusicProvider] = None) -> Optional[dict]:
    if provider:
        info = provider.status()
        if info:
            return info
    return _nowplaying() or _kugou_window()


# ── 子命令 ────────────────────────────────────────────────────────────────────
def cmd_status(args):
    info = detect_status(args.provider_obj)
    if info:
        out({"ok": True, "provider": args.provider_obj.name, **info})
    else:
        err("未检测到正在播放的音乐", "no_player")
    return 0


def cmd_next(args):
    ok = args.provider_obj.control(MR_CMD_NEXT)
    out({"ok": ok, "provider": args.provider_obj.name, "action": "next"})
    return 0


def cmd_prev(args):
    ok = args.provider_obj.control(MR_CMD_PREV)
    out({"ok": ok, "provider": args.provider_obj.name, "action": "prev"})
    return 0


def cmd_play(args):
    ok = args.provider_obj.control(MR_CMD_TOGGLE)
    out({"ok": ok, "provider": args.provider_obj.name, "action": "toggle_play"})
    return 0


def cmd_search(args):
    provider = args.provider_obj
    query    = " ".join(args.query)
    results  = provider.search(query, page_size=10)
    if results:
        out({"ok": True, "provider": provider.name, "query": query, "results": results})
    else:
        err(f"搜索无结果：{query}", "no_results")
    return 0


def cmd_recommend(args):
    provider = args.provider_obj
    mood     = args.mood.lower()
    results, used_kw = [], ""
    for kw in MOOD_MAP[mood]:
        results = provider.search(kw, page_size=6)
        if results:
            used_kw = kw
            break
    out({"ok": True, "provider": provider.name, "mood": mood,
         "keyword": used_kw, "results": results})
    return 0


def cmd_play_by(args):
    provider = args.provider_obj
    query    = " ".join(args.title)
    if provider.name == "spotify":
        uri = f"spotify:search:{urllib.parse.quote(query)}"
        ok, used = provider.play_song(uri)
        out({"ok": ok, "provider": provider.name, "query": query, "url": used})
        return 0 if ok else 1
    songs = provider.search(query, page_size=5)
    if not songs:
        err(f"未找到：{query}", "not_found")
        return 1
    ok, used = provider.play_song(songs[0]["id"])
    out({"ok": ok, "provider": provider.name, "song": songs[0], "url": used})
    return 0 if ok else 1


def cmd_auth(args):
    if args.provider_obj.name != "spotify":
        err("auth 命令仅支持 --provider spotify", "unsupported")
        return 1
    return 0 if args.provider_obj.auth_login() else 1


# ── 入口 ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="music-cli",
        description="macOS 音乐控制 CLI（酷狗 / Spotify）",
    )
    parser.add_argument(
        "--provider", choices=["kugou", "spotify"],
        default=os.environ.get("MUSIC_PROVIDER", "spotify"),
        help="音乐源（默认 kugou；可用 MUSIC_PROVIDER 环境变量覆盖）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth",   help="Spotify 授权登录（仅 --provider spotify）")
    sub.add_parser("status", help="当前播放信息")
    sub.add_parser("next",   help="下一首")
    sub.add_parser("prev",   help="上一首")
    sub.add_parser("play",   help="播放 / 暂停")

    rec = sub.add_parser("recommend", help="按心情推荐歌曲")
    rec.add_argument("mood", choices=list(MOOD_MAP), metavar="mood",
                     help="心情：" + " / ".join(MOOD_MAP))

    sch = sub.add_parser("search", help="搜索歌曲")
    sch.add_argument("query", nargs="+")

    pb = sub.add_parser("play-by", help="搜索并播放")
    pb.add_argument("title", nargs="+", help="歌名（可加艺术家）")

    op = sub.add_parser("open", help="同 play-by（兼容旧版）")
    op.add_argument("title", nargs="+")

    args = parser.parse_args()
    args.provider_obj = get_provider(args.provider)

    fn = {
        "auth":      cmd_auth,
        "status":    cmd_status,
        "next":      cmd_next,
        "prev":      cmd_prev,
        "play":      cmd_play,
        "recommend": cmd_recommend,
        "search":    cmd_search,
        "play-by":   cmd_play_by,
        "open":      cmd_play_by,
    }[args.command]
    sys.exit(fn(args))


if __name__ == "__main__":
    main()
