import asyncio
import os
import sys

from api.spotify import Spotify
from api.jiosaavn import Jiosaavn


async def test_download(url: str):
    print(f"\n==========================================")
    print(f"Testing URL: {url}")
    print(f"==========================================\n")

    if Spotify.is_spotify_url(url):
        print("Detected: Spotify URL")
        sp = Spotify()
        item_type, item_id = await sp.parse_url(url)
        print(f"Type: {item_type} | ID: {item_id}")

        if item_type == "track":
            print("\n1. Fetching Spotify track metadata...")
            track_data = await sp.get_track(item_id)
            print("Track Metadata:")
            for k, v in track_data.items():
                print(f"  • {k}: {v}")

            download_dir = os.path.abspath("./downloads")
            print(f"\n2. Downloading audio & thumbnail to: {download_dir}")
            audio_path, thumb_path = await sp.download_track(track_data, download_dir, "320kbps")
            print(f"\n[SUCCESS]")
            print(f"Audio file saved to: {audio_path} ({os.path.getsize(audio_path):,} bytes)")
            if os.path.exists(thumb_path):
                print(f"Cover image saved to: {thumb_path}")
            print("\nYou can now open and play the downloaded file!")

        elif item_type in ("album", "playlist"):
            print(f"\n1. Fetching Spotify {item_type} tracks...")
            collection = await sp.get_collection(item_type, item_id)
            print(f"Title: {collection.get('title')}")
            print(f"Total Tracks: {collection.get('total')}")
            tracks = collection.get("tracks", [])
            for i, t in enumerate(tracks[:5], 1):
                print(f"  {i}. {t['title']} - {t['artists']}")
            if len(tracks) > 5:
                print(f"  ... and {len(tracks) - 5} more tracks.")

            if tracks:
                first_track = tracks[0]
                print(f"\n2. Downloading first track as test: '{first_track['title']}'...")
                first_data = await sp.get_track(first_track["id"])
                download_dir = os.path.abspath("./downloads")
                audio_path, thumb_path = await sp.download_track(first_data, download_dir, "320kbps")
                print(f"\n[SUCCESS]")
                print(f"Audio file saved to: {audio_path} ({os.path.getsize(audio_path):,} bytes)")
        else:
            print("Unsupported Spotify item type.")

    else:
        print("Detected: JioSaavn or general link")
        clean_url = url.rstrip("/")
        song_id = clean_url.rsplit("/", 1)[-1]
        print(f"Fetching JioSaavn song ID: {song_id}...")
        js = Jiosaavn()
        song_resp = await js.get_song(song_id=song_id)
        if song_resp and song_resp.get("songs"):
            song_data = song_resp["songs"][0]
            print(f"Title: {song_data.get('title')}")
            download_dir = os.path.abspath("./downloads")
            os.makedirs(download_dir, exist_ok=True)
            out_file = os.path.join(download_dir, f"{song_id}.mp4")
            await js.download_song(song_id, 320, out_file)
            print(f"[SUCCESS] Saved to {out_file} ({os.path.getsize(out_file):,} bytes)")
        else:
            print("Song not found or invalid JioSaavn URL.")


async def test_search(query: str):
    print(f"\n==========================================")
    print(f"Testing Spotify Search Query: '{query}'")
    print(f"==========================================\n")
    sp = Spotify()
    hits = await sp.search(query=query, limit=10)
    print(f"Found {len(hits)} Spotify tracks:\n")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. {h['title']} - {h['artists']} [{h['album']}] (id: {h['id']})")
        print(f"     URL: {h['url']}")

    if hits:
        top_hit = hits[0]
        print(f"\nDownloading top result to verify: '{top_hit['title']}'...")
        download_dir = os.path.abspath("./downloads")
        audio_path, thumb_path = await sp.download_track(top_hit, download_dir, "320kbps")
        print(f"\n[SUCCESS]")
        print(f"Audio file saved to: {audio_path} ({os.path.getsize(audio_path):,} bytes)")
        if os.path.exists(thumb_path):
            print(f"Cover image saved to: {thumb_path}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
    if arg.startswith("http"):
        asyncio.run(test_download(arg))
    else:
        asyncio.run(test_search(arg))
