import os
import asyncio
import random
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


TOKEN = os.getenv("MUSIC_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("MUSIC_BOT_TOKEN is not set.")


intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["web_embedded"]
        }
    }
}


FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": (
        "-vn "
        "-ar 48000 "
        "-ac 2 "
        "-b:a 192k"
    )
}


ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class Song:
    def __init__(self, title, url, stream_url, requester):
        self.title = title
        self.url = url
        self.stream_url = stream_url
        self.requester = requester


class GuildMusic:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.loop = "off"


music_data = {}


def get_music(guild_id):
    if guild_id not in music_data:
        music_data[guild_id] = GuildMusic()

    return music_data[guild_id]


async def extract_song(query, requester):
    loop = asyncio.get_running_loop()

    def extract():
        return ytdl.extract_info(query, download=False)

    info = await loop.run_in_executor(None, extract)

    if not info:
        raise RuntimeError("No song was found.")

    if "entries" in info:
        if not info["entries"]:
            raise RuntimeError("No song was found.")

        info = info["entries"][0]

    if not info or "url" not in info:
        raise RuntimeError("No playable audio stream was found.")

    return Song(
        title=info.get("title", "Unknown"),
        url=info.get("webpage_url", query),
        stream_url=info["url"],
        requester=requester
    )


async def play_next(guild):
    data = get_music(guild.id)
    voice = guild.voice_client

    if voice is None or not voice.is_connected():
        data.current = None
        return

    if data.loop == "track" and data.current:
        song = data.current

    elif data.queue:
        song = data.queue.popleft()

    else:
        data.current = None
        return

    data.current = song

    source = discord.FFmpegPCMAudio(
        song.stream_url,
        **FFMPEG_OPTIONS
    )

    def after_playing(error):
        asyncio.run_coroutine_threadsafe(
            handle_song_finished(guild, error),
            bot.loop
        )

    voice.play(
        source,
        after=after_playing
    )


async def handle_song_finished(guild, error):
    data = get_music(guild.id)

    if error:
        print(f"Playback error: {error}")

    if data.loop == "track" and data.current:
        await asyncio.sleep(0.5)
        await play_next(guild)
        return

    if data.current and data.loop == "queue":
        data.queue.append(data.current)

    data.current = None

    await asyncio.sleep(0.5)
    await play_next(guild)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")

    except Exception as e:
        print(f"Slash command sync error: {e}")


@bot.tree.command(
    name="play",
    description="Play a song or add it to the queue."
)
@app_commands.describe(
    query="Song name or YouTube URL"
)
async def play(
    interaction: discord.Interaction,
    query: str
):
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server."
        )
        return

    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You must be in a voice channel first."
        )
        return

    if not interaction.user.voice.channel:
        await interaction.response.send_message(
            "❌ You must be in a voice channel first."
        )
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    voice = interaction.guild.voice_client

    try:
        if voice is None:
            voice = await channel.connect()

        elif voice.channel != channel:
            await voice.move_to(channel)

        song = await extract_song(
            query,
            interaction.user
        )

        data = get_music(
            interaction.guild.id
        )

        data.queue.append(song)

        if not voice.is_playing() and not voice.is_paused():
            await play_next(interaction.guild)

        await interaction.followup.send(
            f"🎵 Added to queue: **{song.title}**"
        )

    except Exception as e:
        print(f"Play error: {e}")

        await interaction.followup.send(
            f"❌ Could not play that song.\n`{e}`"
        )


@bot.tree.command(
    name="pause",
    description="Pause the current song."
)
async def pause(
    interaction: discord.Interaction
):
    voice = interaction.guild.voice_client

    if voice and voice.is_playing():
        voice.pause()

        await interaction.response.send_message(
            "⏸️ Music paused."
        )

    else:
        await interaction.response.send_message(
            "❌ Nothing is playing."
        )


@bot.tree.command(
    name="resume",
    description="Resume the current song."
)
async def resume(
    interaction: discord.Interaction
):
    voice = interaction.guild.voice_client

    if voice and voice.is_paused():
        voice.resume()

        await interaction.response.send_message(
            "▶️ Music resumed."
        )

    else:
        await interaction.response.send_message(
            "❌ Music is not paused."
        )


@bot.tree.command(
    name="skip",
    description="Skip the current song."
)
async def skip(
    interaction: discord.Interaction
):
    voice = interaction.guild.voice_client

    if voice and (
        voice.is_playing()
        or voice.is_paused()
    ):
        voice.stop()

        await interaction.response.send_message(
            "⏭️ Song skipped."
        )

    else:
        await interaction.response.send_message(
            "❌ Nothing is playing."
        )


@bot.tree.command(
    name="stop",
    description="Stop music and clear the queue."
)
async def stop(
    interaction: discord.Interaction
):
    voice = interaction.guild.voice_client
    data = get_music(interaction.guild.id)

    data.queue.clear()
    data.current = None
    data.loop = "off"

    if voice and (
        voice.is_playing()
        or voice.is_paused()
    ):
        voice.stop()

    await interaction.response.send_message(
        "⏹️ Music stopped and queue cleared."
    )


@bot.tree.command(
    name="queue",
    description="Show the current music queue."
)
async def queue_command(
    interaction: discord.Interaction
):
    data = get_music(interaction.guild.id)

    if not data.current and not data.queue:
        await interaction.response.send_message(
            "📭 The queue is empty."
        )
        return

    lines = []

    if data.current:
        lines.append(
            f"🎵 **Now:** {data.current.title}"
        )

    if data.queue:
        lines.append("\n📋 **Up next:**")

        for index, song in enumerate(
            list(data.queue)[:10],
            start=1
        ):
            lines.append(
                f"`{index}.` {song.title}"
            )

    await interaction.response.send_message(
        "\n".join(lines)
    )


@bot.tree.command(
    name="nowplaying",
    description="Show the current song."
)
async def nowplaying(
    interaction: discord.Interaction
):
    data = get_music(interaction.guild.id)

    if data.current:
        await interaction.response.send_message(
            f"🎵 Now playing: **{data.current.title}**"
        )

    else:
        await interaction.response.send_message(
            "❌ Nothing is currently playing."
        )


@bot.tree.command(
    name="volume",
    description="Change the volume."
)
@app_commands.describe(
    percent="Volume from 1 to 200"
)
async def volume(
    interaction: discord.Interaction,
    percent: app_commands.Range[int, 1, 200]
):
    await interaction.response.send_message(
        f"🔊 Volume set to **{percent}%**.\n"
        "Note: volume control is not applied to the current stream."
    )


@bot.tree.command(
    name="loop",
    description="Set loop mode."
)
@app_commands.describe(
    mode="off, track, or queue"
)
@app_commands.choices(
    mode=[
        app_commands.Choice(
            name="Off",
            value="off"
        ),
        app_commands.Choice(
            name="Track",
            value="track"
        ),
        app_commands.Choice(
            name="Queue",
            value="queue"
        )
    ]
)
async def loop_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str]
):
    data = get_music(interaction.guild.id)

    data.loop = mode.value

    await interaction.response.send_message(
        f"🔁 Loop mode: **{mode.name}**"
    )


@bot.tree.command(
    name="shuffle",
    description="Shuffle the queue."
)
async def shuffle(
    interaction: discord.Interaction
):
    data = get_music(interaction.guild.id)

    if len(data.queue) < 2:
        await interaction.response.send_message(
            "❌ Not enough songs to shuffle."
        )
        return

    songs = list(data.queue)

    random.shuffle(songs)

    data.queue = deque(songs)

    await interaction.response.send_message(
        "🔀 Queue shuffled."
    )


@bot.tree.command(
    name="remove",
    description="Remove a song from the queue."
)
@app_commands.describe(
    position="Queue position"
)
async def remove(
    interaction: discord.Interaction,
    position: app_commands.Range[int, 1, 100]
):
    data = get_music(interaction.guild.id)

    if position > len(data.queue):
        await interaction.response.send_message(
            "❌ That queue position does not exist."
        )
        return

    songs = list(data.queue)

    removed = songs.pop(position - 1)

    data.queue = deque(songs)

    await interaction.response.send_message(
        f"🗑️ Removed **{removed.title}**"
    )


@bot.tree.command(
    name="clear",
    description="Clear the music queue."
)
async def clear(
    interaction: discord.Interaction
):
    data = get_music(interaction.guild.id)

    data.queue.clear()

    await interaction.response.send_message(
        "🧹 Queue cleared."
    )


@bot.tree.command(
    name="disconnect",
    description="Disconnect the bot from voice."
)
async def disconnect(
    interaction: discord.Interaction
):
    data = get_music(interaction.guild.id)
    voice = interaction.guild.voice_client

    data.queue.clear()
    data.current = None
    data.loop = "off"

    if voice:
        if voice.is_playing() or voice.is_paused():
            voice.stop()

        await voice.disconnect()

    await interaction.response.send_message(
        "👋 I've disconnected from the voice channel."
    )


@bot.tree.command(
    name="leave",
    description="Leave the voice channel."
)
async def leave(
    interaction: discord.Interaction
):
    data = get_music(interaction.guild.id)
    voice = interaction.guild.voice_client

    data.queue.clear()
    data.current = None
    data.loop = "off"

    if voice:
        if voice.is_playing() or voice.is_paused():
            voice.stop()

        await voice.disconnect()

    await interaction.response.send_message(
        "👋 I've left the voice channel."
    )


bot.run(TOKEN)