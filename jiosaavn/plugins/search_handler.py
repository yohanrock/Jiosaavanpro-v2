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


@Bot.on_callback_query(filters.regex(r"^switch_provider#"))
async def switch_provider_cb(client: Bot, callback: CallbackQuery):
    await callback.answer()
    new_provider = callback.data.split("#")[1]
    await client.db.update_user(callback.from_user.id, "provider", new_provider)
    await search(client, callback)


@Bot.on_callback_query(filters.regex(r"^search#"))
@Bot.on_message(
    filters.text & filters.incoming & filters.private & 
    ~filters.regex(r'^http.*') & ~filters.via_bot & 
    ~filters.command(["start", "settings", "help", "about"])
)
async def search(client: Bot, message: Message | CallbackQuery):
    if isinstance(message, Message):
        send_msg = await message.reply("__**Processing... ⏳**__", quote=True)
        query = message.text.strip()
    else:
        try:
            await message.answer()
        except Exception:
            pass
        send_msg = message.message
        if getattr(message.message, "reply_to_message", None) and message.message.reply_to_message.text:
            query = message.message.reply_to_message.text.strip()
        else:
            query = ""
            for line in (message.message.text or "").split("\n"):
                if "Search Query:" in line or "Results for:" in line:
                    query = line.split(":", 1)[1].strip(" *`")
                    break

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

    # 1. Spotify Search Provider
    if provider == "spotify":
        sp = Spotify(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        try:
            hits = await sp.search(query=query, limit=10)
        except Exception as e:
            logger.error(f"Spotify search error: {e}")
            return await send_msg.edit(f"⚠️ Error searching Spotify: {e}")

        if not hits:
            buttons = [
                [InlineKeyboardButton("Switch to JioSaavn 🎵", callback_data="switch_provider#jiosaavn")],
                [InlineKeyboardButton("Close ❌", callback_data="close")]
            ]
            return await send_msg.edit(
                f"🔎 No Spotify results found for `{query}`",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        buttons = []
        for hit in hits:
            title = hit.get("title", "Unknown")
            artists = hit.get("artists", "Unknown")
            track_id = hit.get("id")
            button_label = f"🎧 {title} - {artists}"
            if len(button_label) > 60:
                button_label = button_label[:57] + "..."
            buttons.append([InlineKeyboardButton(text=button_label, callback_data=f"upload#sp_{track_id}#song")])

        buttons.append([
            InlineKeyboardButton("Switch to JioSaavn 🎵", callback_data="switch_provider#jiosaavn"),
            InlineKeyboardButton("Close ❌", callback_data="close")
        ])
        text = f"**🎧 Spotify Results for:** `{query}`\n\n__Click a track below to download & upload 👇__"
        return await send_msg.edit(text, reply_markup=InlineKeyboardMarkup(buttons))

    # 2. JioSaavn Search Provider
    try:
        if search_type in ('all', 'topquery'):
            response = await Jiosaavn().search_all_types(query=query)
        else:
            response = await Jiosaavn().search(query=query, search_type=search_type, page_no=page_no)
    except RuntimeError as e:
        logger.error(e)
        traceback.print_exc()
        return await send_msg.edit("Connection refused by jiosaavn api. Please try again")

    if not response:
        buttons = [
            [InlineKeyboardButton("Switch to Spotify 🎧", callback_data="switch_provider#spotify")],
            [InlineKeyboardButton("Close ❌", callback_data="close")]
        ]
        return await send_msg.edit(
            f'🔎 No JioSaavn search result found for your query `{query}`',
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
        text = f"**🔍 Search Query:** {query}\n\n__Please select one category 👇__"
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

        text = f"**📈 Total Results:** {total_results}\n\n**🔍 Search Query:** {query}\n\n**📜 Page No:** {page_no}"
        navigation_buttons = []
        if page_no > 1:
            navigation_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"search#{search_type}#{page_no-1}"))
        if total_results > 10 * page_no:
            navigation_buttons.append(InlineKeyboardButton("➡️", callback_data=f"search#{search_type}#{page_no+1}"))
        if navigation_buttons:
            buttons.append(navigation_buttons)

    if not buttons:
        buttons = [[InlineKeyboardButton("Switch to Spotify 🎧", callback_data="switch_provider#spotify")]]
        buttons.append([InlineKeyboardButton('Close ❌', callback_data="close")])
        return await send_msg.edit(f'🔎 No search result found for your query `{query}`', reply_markup=InlineKeyboardMarkup(buttons))

    buttons.append([
        InlineKeyboardButton("Switch to Spotify 🎧", callback_data="switch_provider#spotify"),
        InlineKeyboardButton('Close ❌', callback_data="close")
    ])
    try:
        if send_msg.text != text or send_msg.reply_markup != InlineKeyboardMarkup(buttons):
            await send_msg.edit(text, reply_markup=InlineKeyboardMarkup(buttons))
    except MessageNotModified:
        logger.warning("Message not modified in search_handler")
    except Exception as e:
        logger.error(f"Failed to edit message in search_handler: {e}")
        await send_msg.edit("An error occurred while updating search results.")
