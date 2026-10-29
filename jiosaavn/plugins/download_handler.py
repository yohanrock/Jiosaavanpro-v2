import os
import html
import time
import shutil
import logging

from jiosaavn.bot import Bot
from api.jiosaavn import Jiosaavn
from api.spotify import Spotify
from jiosaavn.config.settings import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

import aiohttp
import aiofiles
from pyrogram import filters
from mutagen.mp4 import MP4, MP4Cover
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ChatAction

logger = logging.getLogger(__name__)


@Bot.on_callback_query(filters.regex(r"^upload#"))
@Bot.on_message(filters.regex(r"http.*") & filters.private & filters.incoming)
async def download(client: Bot, message: Message | CallbackQuery):
    if isinstance(message, CallbackQuery):
        parts = message.data.split("#")
        item_id = parts[1]
        search_type = parts[2] if len(parts) > 2 else "song"
        msg = await message.message.edit("**Processing...**")
        msg.reply_to_message = message.message.reply_to_message or message.message
    else:
        msg = await message.reply("**Processing...**", quote=True)
        msg.reply_to_message = message
        query = message.text.strip()

        # Check if the URL is a Spotify URL
        if Spotify.is_spotify_url(query):
            sp_client = Spotify(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
            sp_type, sp_id = await sp_client.parse_url(query)

            if not sp_type or not sp_id:
                await msg.edit("⚠️ Invalid or unsupported Spotify link.")
                return

            if sp_type == "track":
                await download_spotify_tool(client, message, msg, sp_id, sp_client)
            elif sp_type in ("album", "playlist"):
                await msg.edit(f"**🔍 Fetching Spotify {sp_type} tracks...**")
                collection = await sp_client.get_collection(sp_type, sp_id)
                if not collection or not collection.get("tracks"):
                    await msg.edit(f"⚠️ Failed to find any tracks in this Spotify {sp_type}.")
                    return

                tracks = collection["tracks"]
                total = len(tracks)
                coll_title = collection.get("title", f"Spotify {sp_type.capitalize()}")
                await msg.edit(f"**Found {total} songs in `{coll_title}`. Starting download...**")

                for idx, track in enumerate(tracks, 1):
                    track_id = track.get("id")
                    if not track_id:
                        continue
                    try:
                        await download_spotify_tool(client, message, msg, track_id, sp_client)
                    except Exception as err:
                        logger.error(f"Error downloading Spotify track {track_id} in {sp_type}: {err}")
            else:
                await msg.edit(f"Spotify {sp_type} upload not supported.")
                return

            if "Failed" not in msg.text:
                try:
                    await msg.delete()
                except Exception:
                    pass
            return

        # JioSaavn URL handling
        clean_url = query.rstrip("/")
        item_id = clean_url.rsplit("/", 1)[-1]
        search_type = "song"
        if "song" in clean_url:
            search_type = "song"
        elif "album" in clean_url:
            search_type = "album"
        elif "featured" in clean_url:
            search_type = "playlist"
        elif "artist" in clean_url:
            search_type = "artist"

    # Callback or JioSaavn path
    if item_id.startswith("sp_"):
        sp_client = Spotify(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        actual_track_id = item_id.replace("sp_", "", 1)
        await download_spotify_tool(client, message, msg, actual_track_id, sp_client)
    elif search_type == "song":
        await download_tool(client, message, msg, item_id)
    elif search_type in ("album", "playlist"):
        page_no = 1
        album_id = item_id if search_type == "album" else None
        playlist_id = item_id if search_type == "playlist" else None

        while True:
            response = await Jiosaavn().get_playlist_or_album(
                album_id=album_id, 
                playlist_id=playlist_id, 
                page_no=page_no
            )
            
            if not response or not response.get("list"):
                break
            
            songs = response["list"]
            for song in songs:
                song_url = song.get("perma_url", "")
                if not song_url:
                    continue
                song_id = song_url.rsplit("/", 1)[-1]
                await download_tool(client, message, msg, song_id)
            page_no += 1
    else:
        await msg.edit("Artists and Podcast upload not supported.")
        return

    if "Failed" not in msg.text:
        try:
            await msg.delete()
        except Exception:
            pass


async def download_spotify_tool(
    client: Bot,
    message: Message | CallbackQuery,
    msg: Message,
    track_id: str,
    spotify: Spotify = None
):
    """
    Downloads and uploads a Spotify track following the exact same caching and UI flow as JioSaavn.
    """
    if spotify is None:
        spotify = Spotify(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

    db_song_id = f"sp_{track_id}"
    is_exist = await client.db.is_song_id_exist(db_song_id)
    user = await client.db.get_user(message.from_user.id)
    quality = user.get('quality', '320kbps')

    # Check cache in DB
    if is_exist:
        song_entry = await client.db.get_song(db_song_id)
        if song_entry and song_entry.get(quality):
            cached = song_entry[quality]
            try:
                song_msg = await client.get_messages(
                    chat_id=int(cached.get('chat_id')),
                    message_ids=int(cached.get('message_id'))
                )
                if not song_msg.empty:
                    reply_to = msg.reply_to_message.id if getattr(msg, "reply_to_message", None) else None
                    is_sent = await song_msg.copy(message.from_user.id, reply_to_message_id=reply_to)
                    if is_sent:
                        return
            except Exception as e:
                logger.warning(f"Error copying cached Spotify song: {e}")

    # Fetch track metadata
    track_data = await spotify.get_track(track_id)
    if not track_data or not track_data.get("title"):
        await msg.edit(f"⚠️ Could not retrieve metadata for Spotify track `{track_id}`.")
        return

    title = track_data.get("title", "Unknown")
    artists = track_data.get("artists", "Unknown Artist")
    album = track_data.get("album", "")
    album_url = track_data.get("album_url", "")
    release_date = track_data.get("release_date", "")
    year = track_data.get("year", "")
    image_url = track_data.get("image", "")
    duration = int(track_data.get("duration", 0))
    song_url = track_data.get("url", f"https://open.spotify.com/track/{track_id}")

    # Create caption (same format as JioSaavn)
    album_line = f"**📚 Album:** [{album}]({album_url})" if album and album_url else (f"**📚 Album:** {album}" if album else "")
    text_data = [
        f"[\u2063]({image_url})" if image_url else "",
        f"**🎧 Song:** [{title}]({song_url})" if title else "",
        f"**🎤 Artist:** {artists}" if artists else "",
        album_line,
        f"**📆 Release Date:** __{release_date}__" if release_date else "",
        f"**📆 Release Year:** __{year}__" if not release_date and year else "",
    ]
    caption = "\n\n".join(filter(None, text_data))

    download_dir = f"./download/{time.time()}_{message.from_user.id}/"
    if not os.path.isdir(download_dir):
        os.makedirs(download_dir, exist_ok=True)

    try:
        await msg.edit(f"__📥 Downloading {title} ({artists})__")
        await client.send_chat_action(
            chat_id=message.from_user.id,
            action=ChatAction.RECORD_AUDIO
        )

        audio_file, thumb_file = await spotify.download_track(
            track_data=track_data,
            download_dir=download_dir,
            quality=quality
        )

        await msg.edit(f"__📤 Uploading {title}__")
        await client.send_chat_action(
            chat_id=message.from_user.id,
            action=ChatAction.UPLOAD_AUDIO
        )

        reply_to_id = msg.reply_to_message.id if getattr(msg, "reply_to_message", None) else None
        song_file = await client.send_audio(
            chat_id=message.from_user.id,
            audio=audio_file,
            caption=caption,
            duration=duration,
            title=title,
            thumb=thumb_file if (thumb_file and os.path.exists(thumb_file)) else None,
            performer=artists,
            reply_to_message_id=reply_to_id,
        )

        if not song_file:
            return await msg.edit(text=f"Failed to upload {title}")

        await client.db.update_song(db_song_id, quality, song_file.chat.id, song_file.id)
    except Exception as e:
        logger.error(f"Error downloading Spotify track {track_id}: {e}")
        await msg.edit(f"⚠️ Error downloading {title}: {e}")
    finally:
        if os.path.isdir(download_dir):
            shutil.rmtree(download_dir, ignore_errors=True)


async def download_tool(client: Bot, message: Message|CallbackQuery, msg: Message, song_id: str):
    is_exist = await client.db.is_song_id_exist(song_id)
    user = await client.db.get_user(message.from_user.id)
    quality = user['quality']
    bitrate = 320 if quality == "320kbps" else 160

    if is_exist:
        song = (await client.db.get_song(song_id)).get(quality)
        if song:
            song_msg = await client.get_messages(chat_id=int(song.get('chat_id')), message_ids=int(song.get('message_id')))
            if not song_msg.empty:
                is_sent = await song_msg.copy(message.from_user.id, reply_to_message_id=msg.reply_to_message.id)
                if is_sent:
                    return

    # Extract song data
    song_response = await Jiosaavn().get_song(song_id=song_id)
    song_data = song_response["songs"][0]

    # Extract metadata
    title = song_data.get("title", "Unknown")
    title = html.unescape(title)
    formatted_title = title.replace(" ", "-")
    language = song_data.get("language", "Unknown")
    more_info = song_data.get("more_info", {})
    album = more_info.get("album", "Unknown")
    artist_map = more_info.get("artistMap", {})
    artists = artist_map.get("artists", [])

    def get_artist_by_role(role: str) -> str:
        return ", ".join(artist.get("name") for artist in artists if artist.get("role") == role)

    music = more_info.get("music") or get_artist_by_role("music")
    singers = get_artist_by_role("singer") or music
    release_date = more_info.get("release_date")
    copyright_text = more_info.get("copyright_text", "Unknown")
    duration = int(more_info.get("duration", "0"))
    release_year = song_data.get("year")
    album_url = more_info.get("album_url", "")
    image_url = song_data.get("image", "").replace("150x150", "500x500")
    song_url = song_data.get('perma_url', f"https://jiosaavn.com/songs/{formatted_title}/{song_id}")

    # Create caption
    text_data = [
        f"[\u2063]({image_url})"
        f"**🎧 Song:** [{title}]({song_url})" if title else '',
        f"**📚 Album:** [{album}]({album_url})" if album else '',
        f"**📰 Language:** {language}" if language else '',
        f"**📆 Release Date:** __{release_date}__" if release_date else '',
        f"**📆 Release Year:** __{release_year}__" if not release_date and release_year else '',
    ]

    caption = "\n\n".join(filter(None, text_data))

    # Download and upload song
    download_dir = f"./download/{time.time()}{message.from_user.id}/"
    if not os.path.isdir(download_dir):
        os.makedirs(download_dir)

    pre_file_name = f"{download_dir}{title}_{quality}.mp4"
    file_name = f"{download_dir}{title}_{quality}.mp3"
    thumbnail_location = f"{download_dir}{title}.jpg"

    await msg.edit(f"__📥 Downloading {title}__")
    await client.send_chat_action(
        chat_id=message.from_user.id,
        action=ChatAction.RECORD_AUDIO
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "Referer": "https://www.jiosaavn.com/"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url, headers=headers) as response:
            async with aiofiles.open(thumbnail_location, "wb") as file:
                await file.write(await response.read())

    pre_audio = await Jiosaavn().download_song(song_id=song_id, bitrate=bitrate, download_location=pre_file_name)
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url, headers=headers) as response:
            cover_art = await response.read()
    audio = MP4(pre_audio)
    audio["\xa9nam"] = title
    audio["\xa9alb"] = album
    audio["\xa9ART"] = singers
    audio["\xa9cmt"] = f"Powered by NS Bots - {song_url}"
    audio["cprt"] = copyright_text
    audio["\xa9day"] = release_year
    audio["covr"] = [MP4Cover(cover_art, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
    os.rename(pre_audio, file_name)

    await msg.edit(f"__📤 Uploading {title}__")
    await client.send_chat_action(
        chat_id=message.from_user.id,
        action=ChatAction.UPLOAD_AUDIO
    )

    song_file = await client.send_audio(
        chat_id=message.from_user.id,
        audio=file_name,
        caption=caption,
        duration=duration,
        title=title,
        thumb=thumbnail_location,
        performer=singers,
        reply_to_message_id=msg.reply_to_message.id,
    )

    if not song_file:
        return await msg.edit(text=f"Failed to upload {song}")

    await client.db.update_song(song_id, quality, song_file.chat.id, song_file.id)
    shutil.rmtree(download_dir)
