#!/usr/bin/env python3
"""music-cli provider 抽象层：KuGouProvider / SpotifyProvider。"""
import os
import sys
import json
import base64
import hashlib
import secrets
import subprocess
import time
import urllib.request
import urllib.parse
from abc import ABC, abstractmethod
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

# ── MediaRemote 命令常量 ──────────────────────────────────────────────────────
MR_CMD_PLAY   = 0
MR_CMD_PAUSE  = 1
MR_CMD_TOGGLE = 2
MR_CMD_NEXT   = 3
MR_CMD_PREV   = 4


# ── 抽象接口 ──────────────────────────────────────────────────────────────────
class MusicProvider(ABC):
    name: str = ""
    proc_name: str = ""

    @abstractmethod
    def search(self, keyword: str, page_size: int = 8, page: int = 1) -> list:
        """返回 [{title, artist, album, id, duration}]，失败返回 []。"""

    @abstractmethod
    def play_song(self, song_id: str) -> tuple:
        """播放指定歌曲，返回 (ok: bool, url: str)。"""

    @abstractmethod
    def control(self, mr_cmd: int) -> bool:
        """执行 next/prev/toggle/play/pause。"""

    def status(self) -> Optional[dict]:
        """返回 {title, artist, album, playing, source}，无则 None。"""
        return None


# ── KuGou ─────────────────────────────────────────────────────────────────────
_KUGOU_MENU = {
    MR_CMD_NEXT:   "下一首",
    MR_CMD_PREV:   "上一首",
    MR_CMD_TOGGLE: "播放/暂停",
    MR_CMD_PLAY:   "播放/暂停",
    MR_CMD_PAUSE:  "播放/暂停",
}


def _mr_send(cmd: int) -> bool:
    import ctypes
    path = "/System/Library/PrivateFrameworks/MediaRemote.framework/Versions/Current/MediaRemote"
    try:
        lib = ctypes.CDLL(path)
        lib.MRMediaRemoteSendCommand.argtypes = [ctypes.c_int, ctypes.c_void_p]
        lib.MRMediaRemoteSendCommand.restype  = ctypes.c_bool
        return bool(lib.MRMediaRemoteSendCommand(cmd, None))
    except OSError:
        return False


class KuGouProvider(MusicProvider):
    name = "kugou"
    proc_name = "KugouMusic"

    def search(self, keyword: str, page_size: int = 8, page: int = 1) -> list:
        try:
            params = urllib.parse.urlencode({
                "keyword": keyword, "page": page,
                "pagesize": page_size, "showtype": 1,
            })
            req = urllib.request.Request(
                f"http://mobilecdn.kugou.com/api/v3/search/song?{params}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            return [
                {
                    "title":    s.get("songname", ""),
                    "artist":   s.get("singername", ""),
                    "album":    s.get("album_name", ""),
                    "id":       s.get("hash", ""),
                    "duration": int(s.get("duration", 0)),
                }
                for s in data.get("data", {}).get("info", [])
            ]
        except Exception:
            return []

    def play_song(self, song_id: str) -> tuple:
        if not song_id:
            return False, ""
        for url in [
            f"mackugou://play?hash={song_id}",
            f"mackugou://openurl?hash={song_id}",
            f"kugou://play?hash={song_id}",
        ]:
            if subprocess.run(["open", url], capture_output=True).returncode == 0:
                return True, url
        return False, ""

    def control(self, mr_cmd: int) -> bool:
        return self._menu_control(mr_cmd) or _mr_send(mr_cmd)

    def _menu_control(self, mr_cmd: int) -> bool:
        item = _KUGOU_MENU.get(mr_cmd)
        if not item:
            return False
        script = (
            'tell application "System Events"\n'
            f'  tell process "{self.proc_name}"\n'
            f'    click menu item "{item}" of menu "播放控制" of menu bar 1\n'
            '  end tell\n'
            'end tell'
        )
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0


# ── Spotify ───────────────────────────────────────────────────────────────────
_CFG_PATH  = os.path.expanduser("~/.config/music-cli/config.json")
_CB_PORT   = 8888
_CB_PATH   = "/callback"
_SCOPES    = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


def _load_cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_cfg(data: dict):
    os.makedirs(os.path.dirname(_CFG_PATH), exist_ok=True)
    with open(_CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(_CFG_PATH, 0o600)


def _client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    return cid or _load_cfg().get("spotify", {}).get("client_id", "").strip()


def _token_data() -> dict:
    return _load_cfg().get("spotify", {}).get("token", {})


def _save_token(td: dict):
    cfg = _load_cfg()
    cfg.setdefault("spotify", {})["token"] = td
    _save_cfg(cfg)


def _pkce_pair() -> tuple:
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _token_request(params: dict) -> dict:
    body = urllib.parse.urlencode(params).encode()
    req  = urllib.request.Request(
        "https://accounts.spotify.com/api/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


class _CBHandler(BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in qs:
            _CBHandler.code = qs["code"][0]
            body = b"<h2>Authorization successful! You can close this tab.</h2>"
        else:
            _CBHandler.error = qs.get("error", ["unknown"])[0]
            body = b"<h2>Authorization failed. Please retry.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _wait_callback(timeout: int = 120) -> str:
    _CBHandler.code = _CBHandler.error = None
    server = HTTPServer(("127.0.0.1", _CB_PORT), _CBHandler)
    server.timeout = 1
    deadline = time.time() + timeout
    while time.time() < deadline:
        server.handle_request()
        if _CBHandler.code:
            server.server_close()
            return _CBHandler.code
        if _CBHandler.error:
            server.server_close()
            raise RuntimeError(f"Spotify 拒绝授权: {_CBHandler.error}")
    server.server_close()
    raise TimeoutError("等待授权超时（120 秒），请重试")


_SP_VERBS = {
    MR_CMD_NEXT:   "next track",
    MR_CMD_PREV:   "previous track",
    MR_CMD_TOGGLE: "playpause",
    MR_CMD_PLAY:   "play",
    MR_CMD_PAUSE:  "pause",
}


class SpotifyProvider(MusicProvider):
    name = "spotify"
    proc_name = "Spotify"

    def __init__(self):
        self._access_token = ""
        self._token_expiry = 0.0
        self._focus_app    = "iTerm2"

    # ── 授权 ─────────────────────────────────────────────────────────────────
    def auth_login(self) -> bool:
        cid = _client_id()
        if not cid:
            print('错误：未配置 client_id，请在 ~/.config/music-cli/config.json 设置：\n'
                  '  {"spotify": {"client_id": "YOUR_ID"}}', file=sys.stderr)
            return False

        verifier, challenge = _pkce_pair()
        redirect = f"http://127.0.0.1:{_CB_PORT}{_CB_PATH}"
        params = urllib.parse.urlencode({
            "client_id": cid, "response_type": "code",
            "redirect_uri": redirect, "scope": _SCOPES,
            "code_challenge_method": "S256", "code_challenge": challenge,
        })
        url = f"https://accounts.spotify.com/authorize?{params}"
        print(f"正在打开浏览器授权 Spotify…\n{url}")
        subprocess.run(["open", url])
        print("等待授权回调（最长 120 秒）…")

        try:
            code = _wait_callback()
            data = _token_request({
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier,
            })
        except Exception as e:
            print(f"授权失败：{e}", file=sys.stderr)
            return False

        _save_token({
            "access_token":  data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at":    int(time.time()) + int(data.get("expires_in", 3600)),
        })
        print("✓ 授权成功，token 已保存到 ~/.config/music-cli/config.json")
        return True

    # ── Token ────────────────────────────────────────────────────────────────
    def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 30:
            return self._access_token
        td = _token_data()
        if not td:
            return ""
        if time.time() < td.get("expires_at", 0) - 30:
            self._access_token = td["access_token"]
            self._token_expiry = td["expires_at"]
            return self._access_token
        # 刷新
        cid = _client_id()
        rt  = td.get("refresh_token", "")
        if not cid or not rt:
            return ""
        try:
            data = _token_request({
                "grant_type": "refresh_token", "refresh_token": rt, "client_id": cid,
            })
            new_td = {
                "access_token":  data["access_token"],
                "refresh_token": data.get("refresh_token", rt),
                "expires_at":    int(time.time()) + int(data.get("expires_in", 3600)),
            }
            _save_token(new_td)
            self._access_token = new_td["access_token"]
            self._token_expiry = new_td["expires_at"]
            return self._access_token
        except Exception:
            return ""

    def has_credentials(self) -> bool:
        return bool(_client_id() and _token_data().get("access_token"))

    # ── 搜索（Spotify Web API，需授权）──────────────────────────────────────
    def search(self, keyword: str, page_size: int = 8, page: int = 1) -> list:
        token = self._get_token()
        if not token:
            return []
        params = urllib.parse.urlencode({"q": keyword, "type": "track", "limit": page_size})
        req = urllib.request.Request(
            f"https://api.spotify.com/v1/search?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
            return [
                {
                    "title":    it.get("name", ""),
                    "artist":   it["artists"][0]["name"] if it.get("artists") else "",
                    "album":    it.get("album", {}).get("name", ""),
                    "id":       it.get("uri", ""),
                    "duration": it.get("duration_ms", 0) // 1000,
                }
                for it in data.get("tracks", {}).get("items", [])
            ]
        except Exception:
            return []

    # ── 播放（AppleScript，无需 token）───────────────────────────────────────
    def capture_focus(self):
        """记住当前前台应用（排除 Spotify），供播放后恢复焦点用。"""
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events"\n'
             f'  set p to first process where (frontmost is true) and (name is not "{self.proc_name}")\n'
             '  return name of p\n'
             'end tell'],
            capture_output=True, text=True,
        )
        app = r.stdout.strip()
        if r.returncode == 0 and app and app != self.proc_name:
            self._focus_app = app

    def _ensure_running(self) -> bool:
        """确保 Spotify 在运行，没有则启动并等待就绪。"""
        r = subprocess.run(["pgrep", "-x", self.proc_name], capture_output=True)
        if r.returncode == 0:
            return True
        # 启动 Spotify
        subprocess.run(["open", "-a", self.proc_name], capture_output=True)
        # 等待最多 10 秒直到进程出现
        for _ in range(20):
            time.sleep(0.5)
            r = subprocess.run(["pgrep", "-x", self.proc_name], capture_output=True)
            if r.returncode == 0:
                time.sleep(2)  # 再等 2 秒让 Spotify 完成初始化
                return True
        return False

    def play_song(self, song_id: str) -> tuple:
        if not song_id:
            return False, ""
        if not self._ensure_running():
            return False, ""
        # 播放 → 隐藏 Spotify 窗口 → 激活回终端（Spotify 弹出时会短暂闪烁，已是最优）
        script = (
            f'tell application "{self.proc_name}" to play track "{song_id}"\n'
            f'tell application "System Events" to set visible of process "{self.proc_name}" to false\n'
            f'tell application "{self._focus_app}" to activate\n'
        )
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.returncode == 0, song_id

    def control(self, mr_cmd: int) -> bool:
        verb = _SP_VERBS.get(mr_cmd)
        if not verb:
            return False
        return subprocess.run(
            ["osascript", "-e", f'tell application "{self.proc_name}" to {verb}'],
            capture_output=True,
        ).returncode == 0

    def status(self) -> Optional[dict]:
        r = subprocess.run([
            "osascript",
            "-e", f'tell application "{self.proc_name}"',
            "-e", "if it is running then",
            "-e", 'return (player state as string) & "|" & (name of current track) & "|" & (artist of current track) & "|" & (album of current track)',
            "-e", "end if",
            "-e", "end tell",
        ], capture_output=True, text=True)
        out = r.stdout.strip()
        if not out:
            return None
        parts = out.split("|")
        if len(parts) < 4 or not parts[1].strip():
            return None
        return {
            "title":   parts[1].strip(),
            "artist":  parts[2].strip(),
            "album":   parts[3].strip(),
            "playing": parts[0].strip() == "playing",
            "source":  "spotify_applescript",
        }


# ── 注册表 ────────────────────────────────────────────────────────────────────
def get_provider(name: str) -> MusicProvider:
    return SpotifyProvider() if (name or "").lower() == "spotify" else KuGouProvider()
