import os
import re
import html
import shutil
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple, List
import aiohttp
import aiofiles

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, COMM
from mutagen.mp4 import MP4, MP4Cover

try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
except ImportError:
    spotipy = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

logger = logging.getLogger(__name__)

SPOTIFY_TRACK_PATTERN = re.compile(r"https?://open\.spotify\.com/(?:intl-[a-zA-Z]{2}/)?track/([a-zA-Z0-9]+)")
SPOTIFY_ALBUM_PATTERN = re.compile(r"https?://open\.spotify\.com/(?:intl-[a-zA-Z]{2}/)?album/([a-zA-Z0-9]+)")
SPOTIFY_PLAYLIST_PATTERN = re.compile(r"https?://open\.spotify\.com/(?:intl-[a-zA-Z]{2}/)?playlist/([a-zA-Z0-9]+)")
SPOTIFY_SHORT_PATTERN = re.compile(r"https?://spotify\.link/[a-zA-Z0-9]+")


class Spotify:
    """
    Spotify client for parsing Spotify links (tracks, albums, playlists),
    retrieving metadata, and downloading matching audio using yt-dlp.
    """

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self._sp = None
        if self.client_id and self.client_secret and spotipy:
            try:
                auth_mgr = SpotifyClientCredentials(
                    client_id=self.client_id,
                    client_secret=self.client_secret
                )
                self._sp = spotipy.Spotify(client_credentials_manager=auth_mgr)
            except Exception as e:
                logger.warning(f"Failed to initialize spotipy client: {e}")

    @staticmethod
    def is_spotify_url(url: str) -> bool:
        if not url:
            return False
        return bool(
            SPOTIFY_TRACK_PATTERN.search(url)
            or SPOTIFY_ALBUM_PATTERN.search(url)
            or SPOTIFY_PLAYLIST_PATTERN.search(url)
            or SPOTIFY_SHORT_PATTERN.search(url)
        )

    async def resolve_short_url(self, url: str) -> str:
        """Resolve spotify.link shortened URL to open.spotify.com URL."""
        if not SPOTIFY_SHORT_PATTERN.search(url):
            return url
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            async with aiohttp.ClientSession() as session:
                async with session.head(url, allow_redirects=True, headers=headers) as response:
                    return str(response.url)
        except Exception as e:
            logger.error(f"Error resolving Spotify short URL {url}: {e}")
            return url

    async def parse_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parses a Spotify URL and returns (type, item_id).
        type can be: 'track', 'album', 'playlist' or None.
        """
        url = await self.resolve_short_url(url)

        m = SPOTIFY_TRACK_PATTERN.search(url)
        if m:
            return "track", m.group(1)

        m = SPOTIFY_ALBUM_PATTERN.search(url)
        if m:
            return "album", m.group(1)

        m = SPOTIFY_PLAYLIST_PATTERN.search(url)
        if m:
            return "playlist", m.group(1)

        return None, None

    async def _fetch_embed_entity(self, item_type: str, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches entity data from Spotify embed page without needing API credentials.
        """
        embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(embed_url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    html_text = await resp.text()

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_text)
            if match:
                import json
                parsed = json.loads(match.group(1))
                return parsed.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
        except Exception as e:
            logger.error(f"Error fetching Spotify embed entity for {item_type}/{item_id}: {e}")
        return None

    async def get_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves track metadata for a given Spotify track ID.
        """
        # Try official Spotipy if configured
        if self._sp:
            try:
                loop = asyncio.get_running_loop()
                track_info = await loop.run_in_executor(None, self._sp.track, track_id)
                artists = ", ".join(a["name"] for a in track_info.get("artists", []))
                album_info = track_info.get("album", {})
                album_name = album_info.get("name", "Unknown Album")
                album_url = album_info.get("external_urls", {}).get("spotify", "")
                images = album_info.get("images", [])
                image_url = images[0]["url"] if images else ""
                release_date = album_info.get("release_date", "")
                year = release_date.split("-")[0] if release_date else ""
                duration = int(track_info.get("duration_ms", 0) / 1000)

                return {
                    "id": track_id,
                    "title": html.unescape(track_info.get("name", "")),
                    "artists": artists,
                    "album": album_name,
                    "album_url": album_url,
                    "release_date": release_date,
                    "year": year,
                    "duration": duration,
                    "image": image_url,
                    "url": f"https://open.spotify.com/track/{track_id}",
                    "source": "spotify"
                }
            except Exception as e:
                logger.warning(f"Spotipy get_track failed for {track_id}, trying embed fallback: {e}")

        # Fallback to embed data
        entity = await self._fetch_embed_entity("track", track_id)
        if not entity:
            # Fallback to oembed for basic info
            return await self._get_oembed_track(track_id)

        title = entity.get("title") or entity.get("name") or "Unknown Title"
        artists_data = entity.get("artists", [])
        if artists_data and isinstance(artists_data, list):
            artists = ", ".join(a.get("name", "") for a in artists_data if isinstance(a, dict))
        elif entity.get("subtitle"):
            artists = entity.get("subtitle")
        else:
            artists = "Unknown Artist"

        release_date = (entity.get("releaseDate") or {}).get("isoString", "")
        if release_date:
            release_date = release_date.split("T")[0]
        year = release_date.split("-")[0] if release_date else ""

        duration_ms = entity.get("duration", 0)
        duration = int(duration_ms / 1000) if duration_ms else 0

        # High-res image if available
        image_url = ""
        visual_identity = entity.get("visualIdentity", {})
        images = visual_identity.get("image", [])
        if images and isinstance(images, list):
            # Prefer largest
            images_sorted = sorted(images, key=lambda x: x.get("maxWidth", 0), reverse=True)
            image_url = images_sorted[0].get("url", "")

        return {
            "id": track_id,
            "title": html.unescape(title),
            "artists": artists,
            "album": entity.get("album", {}).get("name") if isinstance(entity.get("album"), dict) else "",
            "album_url": "",
            "release_date": release_date,
            "year": year,
            "duration": duration,
            "image": image_url,
            "url": f"https://open.spotify.com/track/{track_id}",
            "source": "spotify"
        }

    async def _get_oembed_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Fallback to Spotify oEmbed."""
        oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(oembed_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        title = data.get("title", "Unknown Title")
                        return {
                            "id": track_id,
                            "title": title,
                            "artists": "Spotify Artist",
                            "album": "",
                            "album_url": "",
                            "release_date": "",
                            "year": "",
                            "duration": 0,
                            "image": data.get("thumbnail_url", ""),
                            "url": f"https://open.spotify.com/track/{track_id}",
                            "source": "spotify"
                        }
        except Exception as e:
            logger.error(f"oembed failed for track {track_id}: {e}")
        return None

    async def get_collection(self, item_type: str, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves tracks from a Spotify album or playlist.
        """
        # Try Spotipy official first
        if self._sp:
            try:
                loop = asyncio.get_running_loop()
                if item_type == "album":
                    album = await loop.run_in_executor(None, self._sp.album, item_id)
                    title = album.get("name", "Unknown Album")
                    images = album.get("images", [])
                    image_url = images[0]["url"] if images else ""
                    tracks = []
                    for item in album.get("tracks", {}).get("items", []):
                        tid = item.get("id")
                        if not tid:
                            continue
                        t_artists = ", ".join(a["name"] for a in item.get("artists", []))
                        tracks.append({
                            "id": tid,
                            "title": item.get("name", ""),
                            "artists": t_artists,
                            "duration": int(item.get("duration_ms", 0) / 1000),
                            "url": f"https://open.spotify.com/track/{tid}"
                        })
                    return {
                        "title": title,
                        "image": image_url,
                        "total": len(tracks),
                        "tracks": tracks
                    }
                elif item_type == "playlist":
                    playlist = await loop.run_in_executor(None, self._sp.playlist, item_id)
                    title = playlist.get("name", "Unknown Playlist")
                    images = playlist.get("images", [])
                    image_url = images[0]["url"] if images else ""
                    tracks = []
                    for item in playlist.get("tracks", {}).get("items", []):
                        track = item.get("track")
                        if not track or not track.get("id"):
                            continue
                        tid = track["id"]
                        t_artists = ", ".join(a["name"] for a in track.get("artists", []))
                        tracks.append({
                            "id": tid,
                            "title": track.get("name", ""),
                            "artists": t_artists,
                            "duration": int(track.get("duration_ms", 0) / 1000),
                            "url": f"https://open.spotify.com/track/{tid}"
                        })
                    return {
                        "title": title,
                        "image": image_url,
                        "total": len(tracks),
                        "tracks": tracks
                    }
            except Exception as e:
                logger.warning(f"Spotipy get_collection failed for {item_type}/{item_id}: {e}")

        # Fallback to embed
        entity = await self._fetch_embed_entity(item_type, item_id)
        if not entity:
            return None

        title = entity.get("title") or entity.get("name") or f"Unknown {item_type.capitalize()}"
        visual_identity = entity.get("visualIdentity", {})
        images = visual_identity.get("image", [])
        image_url = ""
        if images and isinstance(images, list):
            images_sorted = sorted(images, key=lambda x: x.get("maxWidth", 0), reverse=True)
            image_url = images_sorted[0].get("url", "")

        tracks = []
        for t in entity.get("trackList", []):
            uri = t.get("uri", "")
            tid = uri.split(":")[-1] if "track:" in uri else t.get("uid")
            if not tid:
                continue
            tracks.append({
                "id": tid,
                "title": t.get("title", ""),
                "artists": t.get("subtitle", ""),
                "duration": int(t.get("duration", 0) / 1000),
                "url": f"https://open.spotify.com/track/{tid}"
            })

        return {
            "title": title,
            "image": image_url,
            "total": len(tracks),
            "tracks": tracks
        }

    async def download_track(
        self,
        track_data: Dict[str, Any],
        download_dir: str,
        quality: str = "320kbps"
    ) -> Tuple[str, str]:
        """
        Downloads a Spotify track via YouTube search matching and tags the file.
        Returns:
            Tuple[str, str]: (audio_file_path, thumbnail_file_path)
        """
        if not yt_dlp:
            raise RuntimeError("yt-dlp is not installed. Please install yt-dlp.")

        os.makedirs(download_dir, exist_ok=True)

        title = track_data.get("title", "Unknown Track")
        artists = track_data.get("artists", "Unknown Artist")
        album = track_data.get("album", "")
        year = track_data.get("year", "")
        image_url = track_data.get("image", "")

        # Clean filename
        safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip()
        safe_title = safe_title or "track"

        thumb_path = os.path.join(download_dir, f"{safe_title}.jpg")
        output_template = os.path.join(download_dir, f"{safe_title}.%(ext)s")

        # 1. Download cover image
        cover_art = None
        if image_url:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url, headers=headers) as response:
                        if response.status == 200:
                            cover_art = await response.read()
                            async with aiofiles.open(thumb_path, "wb") as f:
                                await f.write(cover_art)
            except Exception as e:
                logger.warning(f"Failed to download cover image: {e}")

        # 2. Download audio via yt-dlp in a thread
        query = f"ytsearch1:{artists} - {title} audio"
        logger.info(f"Searching & downloading for Spotify track: {query}")

        has_ffmpeg = bool(shutil.which("ffmpeg"))
        bitrate_str = "320" if quality == "320kbps" else "160"

        ydl_opts = {
            "format": "m4a/bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        if has_ffmpeg:
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate_str,
            }]

        loop = asyncio.get_running_loop()

        def _run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
                return info

        await loop.run_in_executor(None, _run_ydl)

        # 3. Locate the downloaded file
        downloaded_file = None
        for f in os.listdir(download_dir):
            if f.endswith((".mp3", ".m4a", ".opus", ".webm")) and safe_title in f:
                downloaded_file = os.path.join(download_dir, f)
                break

        if not downloaded_file:
            # Fallback to any audio file in the folder
            for f in os.listdir(download_dir):
                if f.endswith((".mp3", ".m4a", ".opus", ".webm")):
                    downloaded_file = os.path.join(download_dir, f)
                    break

        if not downloaded_file:
            raise FileNotFoundError("Audio download completed but file was not found on disk.")

        # 4. Tag the audio file using mutagen
        try:
            if downloaded_file.endswith(".m4a") or downloaded_file.endswith(".mp4"):
                audio = MP4(downloaded_file)
                audio["\xa9nam"] = title
                if album:
                    audio["\xa9alb"] = album
                if artists:
                    audio["\xa9ART"] = artists
                if year:
                    audio["\xa9day"] = str(year)
                if cover_art:
                    audio["covr"] = [MP4Cover(cover_art, imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()
            elif downloaded_file.endswith(".mp3"):
                audio = MP3(downloaded_file, ID3=ID3)
                try:
                    audio.add_tags()
                except Exception:
                    pass
                audio.tags.add(TIT2(encoding=3, text=title))
                audio.tags.add(TPE1(encoding=3, text=artists))
                if album:
                    audio.tags.add(TALB(encoding=3, text=album))
                if year:
                    audio.tags.add(TDRC(encoding=3, text=str(year)))
                if cover_art:
                    audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_art))
                audio.save()
        except Exception as e:
            logger.warning(f"Mutagen tagging failed: {e}")

        return downloaded_file, thumb_path
