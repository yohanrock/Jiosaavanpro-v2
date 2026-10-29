import html
import logging
import traceback

from api.jiosaavn import Jiosaavn
from api.spotify import Spotify
from jiosaavn.config.settings import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
from jiosaavn.bot import Bot

from pyrogram import filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import MessageNotModified

logger = logging.getLogger(__name__)


def extract_query(message: Message | CallbackQuery) -> str:
    """Extract search query safely from a message or callback."""
    if isinstance(message, Message):
        text = message.text or ""
        if text.startswith(("/", "!")):
            parts = text.split(maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else ""
        return text.strip()

    # CallbackQuery
    msg = getattr(message, "message", None)
    if not msg:
        return ""

    if getattr(msg, "reply_to_message", None) and msg.reply_to_message.text:
        text = msg.reply_to_message.text.strip()
        if text.startswith(("/", "!")):
            parts = text.split(maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else ""
        return text

    for line in (msg.text or "").split("\n"):
        if "Search Query:" in line or "Results for:" in line:
            return line.split(":", 1)[1].strip(" *`")
    return ""


async def perform_spotify_search(client: Bot, send_msg: Message, query: str, page_no: int = 1):
    """Executes a search on Spotify and renders paginated results."""
    sp = Spotify(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
    try:
        hits = await sp.search(query=query, limit=35)
    except Exception as e:
        logger.error(f"Spotify search error: {e}")
        return await send_msg.edit(f"⚠️ Error searching Spotify: {e}")

    if not hits:
        buttons = [
            [InlineKeyboardButton("Switch to JioSaavn 🎵", callback_data="switch_provider#jiosaavn")],
            [InlineKeyboardButton("Close ❌", callback_data="close")]
        ]
        return await send_msg.edit(
            f"**🎧 Spotify Results**\n**🔍 Search Query:** `{query}`\n\n🔎 No Spotify results found for your query.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    page_size = 7
    total_hits = len(hits)
    total_pages = (total_hits + page_size - 1) // page_size or 1
    page_no = max(1, min(page_no, total_pages))
    start_idx = (page_no - 1) * page_size
    page_hits = hits[start_idx:start_idx + page_size]

    buttons = []
    for hit in page_hits:
        title = hit.get("title", "Unknown")
        artists = hit.get("artists", "Unknown")
        track_id = hit.get("id")
        button_label = f"🎧 {title} - {artists}"
        if len(button_label) > 55:
            button_label = button_label[:52] + "..."
        buttons.append([InlineKeyboardButton(text=button_label, callback_data=f"upload#sp_{track_id}#song")])

    nav_buttons = []
    if page_no > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search#spotify#{page_no-1}"))
    if page_no < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"search#spotify#{page_no+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton("Switch to JioSaavn 🎵", callback_data="switch_provider#jiosaavn"),
        InlineKeyboardButton("Close ❌", callback_data="close")
    ])

    text = (
        f"**🎧 Spotify Results**\n"
        f"**🔍 Search Query:** `{query}`\n"
        f"**📈 Total Results:** `{total_hits}`\n"
        f"**📜 Page No:** `{page_no}/{total_pages}`\n\n"
        f"__Click a track below to download & upload 👇__"
    )
    try:
        await send_msg.edit(text, reply_markup=InlineKeyboardMarkup(buttons))
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error editing message in perform_spotify_search: {e}")


async def perform_jiosaavn_search(client: Bot, send_msg: Message, query: str, search_type: str = "all", page_no: int = 1):
    """Executes a search on JioSaavn and renders results."""
    try:
        if search_type in ('all', 'topquery'):
            response = await Jiosaavn().search_all_types(query=query)
        else:
            response = await Jiosaavn().search(query=query, search_type=search_type, page_no=page_no)
    except RuntimeError as e:
        logger.error(e)
        return await send_msg.edit("Connection refused by JioSaavn API. Please try again.")

    if not response:
        buttons = [
            [InlineKeyboardButton("Switch to Spotify 🎧", callback_data="switch_provider#spotify")],
            [InlineKeyboardButton("Close ❌", callback_data="close")]
        ]
        return await send_msg.edit(
            f"**🎵 JioSaavn Results**\n**🔍 Search Query:** `{query}`\n\n🔎 No JioSaavn results found for your query.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    buttons = []
    if search_type == "all" or search_type == "topquery":
        button_song_type_map = {
            "songs": ("🎙 Songs", "search#songs"),
            "albums": ("📚 Albums", "search#albums"),
            "playlists": ("💾 Playlists", "search#playlists"),
            "artists": ("👨‍🎤 Artists", "search#artists"),
            "topquery": ("✨ Top Result", "search#topquery"),
        }

        if search_type == 'topquery':
            sub_sorted_data = sorted(
                response.get("topquery", {}).get("data", []),
                key=lambda x: x.get("position", 0)
            )
            for data in sub_sorted_data:
                title = data.get("title", "unknown")
                title = html.unescape(title)
                album = data.get("album")
                item_type = data.get("type")
                item_id = data.get("url", "/").rsplit("/", 1)[1]
                type_emoji_map = {
                    "song": "🎙",
                    "album": "📚",
                    "playlist": "💾",
                    "artist": "👨‍🎤",
                }
                if item_type not in type_emoji_map:
                    continue
                emoji = type_emoji_map[item_type]
                button_text = f"{emoji} {title} from {album}" if album else f"{emoji} {title}"
                callback_data = f"{item_type}#{item_id}#topquery"
                buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
        else:
            sorted_data = sorted(response.items(), key=lambda value: value[1].get("position", 0))
            for result_type, result in sorted_data:
                if result_type not in button_song_type_map:
                    continue

                if result.get("data"):
                    button_label, callback_data = button_song_type_map.get(result_type, (None, None))
                    buttons.append([InlineKeyboardButton(text=button_label, callback_data=callback_data)])
        text = f"**🎵 JioSaavn Results**\n**🔍 Search Query:** `{query}`\n\n__Please select one category 👇__"
    else:
        total_results = response.get("total", 0)

        for result in response.get("results", []):
            item_id = result.get("perma_url", "/").rsplit("/", 1)[1]
            title = result.get("title", "unknown")
            title = html.unescape(title)
            result_type = result.get("type", "unknown")
            artist = result.get("name", "unknown")
            artist = html.unescape(artist)
            more_info = result.get("more_info", {})
            album = more_info.get("album", "")

            button_label_map = {
                "song": f"🎙 {title} from '{album}'" if album else f"🎙 {title}",
                "album": f"📚 {title}",
                "playlist": f"💾 {title}",
                "artist": f"👨‍🎤 {artist}",
            }

            button_label = button_label_map.get(result_type)
            if button_label:
                buttons.append([InlineKeyboardButton(text=button_label, callback_data=f"{result_type}#{item_id}")])

        text = (
            f"**🎵 JioSaavn Results**\n"
            f"**🔍 Search Query:** `{query}`\n"
            f"**📈 Total Results:** `{total_results}`\n"
            f"**📜 Page No:** `{page_no}`"
        )
        navigation_buttons = []
        if page_no > 1:
            navigation_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search#{search_type}#{page_no-1}"))
        if total_results > 10 * page_no:
            navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"search#{search_type}#{page_no+1}"))
        if navigation_buttons:
            buttons.append(navigation_buttons)

    if not buttons:
        buttons = [[InlineKeyboardButton("Switch to Spotify 🎧", callback_data="switch_provider#spotify")]]
        buttons.append([InlineKeyboardButton('Close ❌', callback_data="close")])
        return await send_msg.edit(
            f"**🎵 JioSaavn Results**\n**🔍 Search Query:** `{query}`\n\n🔎 No JioSaavn results found for your query.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    buttons.append([
        InlineKeyboardButton("Switch to Spotify 🎧", callback_data="switch_provider#spotify"),
        InlineKeyboardButton('Close ❌', callback_data="close")
    ])
    try:
        await send_msg.edit(text, reply_markup=InlineKeyboardMarkup(buttons))
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error editing message in perform_jiosaavn_search: {e}")


@Bot.on_message(filters.command(["spotify", "sp"]) & filters.private & filters.incoming)
async def spotify_search_cmd(client: Bot, message: Message):
    """Shortcut command to search Spotify directly (/sp <song name>)."""
    if len(message.command) < 2:
        return await message.reply("⚠️ **Usage:** `/sp <song name>` or `/spotify <song name>`", quote=True)
    query = " ".join(message.command[1:]).strip()
    send_msg = await message.reply("__**Searching Spotify... ⏳**__", quote=True)
    await perform_spotify_search(client, send_msg, query, page_no=1)


@Bot.on_message(filters.command(["saavn", "jiosaavn", "js"]) & filters.private & filters.incoming)
async def saavn_search_cmd(client: Bot, message: Message):
    """Shortcut command to search JioSaavn directly (/js <song name>)."""
    if len(message.command) < 2:
        return await message.reply("⚠️ **Usage:** `/js <song name>` or `/saavn <song name>`", quote=True)
    query = " ".join(message.command[1:]).strip()
    send_msg = await message.reply("__**Searching JioSaavn... ⏳**__", quote=True)
    user_data = await client.db.get_user(message.from_user.id)
    search_type = user_data.get('type', 'all')
    await perform_jiosaavn_search(client, send_msg, query, search_type=search_type, page_no=1)


@Bot.on_callback_query(filters.regex(r"^switch_provider#"))
async def switch_provider_cb(client: Bot, callback: CallbackQuery):
    """Handles switching provider on the fly from search results."""
    await callback.answer()
    new_provider = callback.data.split("#")[1]
    await client.db.update_user(callback.from_user.id, "provider", new_provider)
    query = extract_query(callback)

    if not query:
        return await callback.message.edit(f"Provider switched to **{new_provider.capitalize()}**! Please send your song name.")

    user_data = await client.db.get_user(callback.from_user.id)
    if new_provider == "spotify":
        await perform_spotify_search(client, callback.message, query, page_no=1)
    else:
        search_type = user_data.get('type', 'all')
        await perform_jiosaavn_search(client, callback.message, query, search_type=search_type, page_no=1)


@Bot.on_callback_query(filters.regex(r"^search#"))
@Bot.on_message(
    filters.text & filters.incoming & filters.private & 
    ~filters.regex(r'^http.*') & ~filters.via_bot & 
    ~filters.command(["start", "settings", "help", "about", "spotify", "sp", "saavn", "jiosaavn", "js"])
)
async def search(client: Bot, message: Message | CallbackQuery):
    """Default text search handler based on user's selected provider."""
    query = extract_query(message)
    if isinstance(message, Message):
        send_msg = await message.reply("__**Processing... ⏳**__", quote=True)
    else:
        try:
            await message.answer()
        except Exception:
            pass
        send_msg = message.message

    if not query:
        return await send_msg.edit("⚠️ Please send a valid search text.")

    user_data = await client.db.get_user(message.from_user.id)
    provider = user_data.get('provider', 'jiosaavn')
    search_type = user_data.get('type', 'all')
    page_no = 1

    if isinstance(message, CallbackQuery) and message.data.startswith("search#"):
        data = message.data.split('#')
        search_type = data[1]
        if len(data) == 3:
            page_no = int(data[2])

    if search_type == "spotify" or provider == "spotify":
        await perform_spotify_search(client, send_msg, query, page_no=page_no)
    else:
        await perform_jiosaavn_search(client, send_msg, query, search_type=search_type, page_no=page_no)
