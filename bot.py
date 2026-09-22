import os
import asyncio
import random
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# =========================================================
# TOKEN
# =========================================================

TOKEN = os.getenv("MUSIC_BOT_TOKEN")

if not TOKEN:
    print("ERROR: MUSIC_BOT_TOKEN is not configured.")
    raise SystemExit(1)


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.voice_states = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents
)


# =========================================================
# YT-DLP
# =========================================================

YTDL_OPTIONS = {
    # Prioritize the best audio-only stream available.
    "format": "bestaudio/best",

    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,

    # Search YouTube when the user enters a normal search.
    "default_search": "ytsearch1",

    "source_address": "0.0.0.0",

    # Helps yt-dlp select a good YouTube client.
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    },
}


FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),

    # Discord voice uses 48 kHz stereo.
    # Audio is streamed directly; no music file is saved.
    "options": (
        "-vn "
        "-ar 48000 "
        "-ac 2 "
        "-b:a 192k"
    ),
}


# =========================================================
# DATA
# =========================================================

@dataclass
class Song:
    title: str
    url: str
    webpage_url: str
    duration: int
    requester: discord.Member


@dataclass
class GuildPlayer:
    queue: list[Song] = field(default_factory=list)
    current: Optional[Song] = None
    loop: bool = False
    volume: float = 1.0
    player_task: Optional[asyncio.Task] = None


players: dict[int, GuildPlayer] = {}


# =========================================================
# HELPERS
# =========================================================

def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()

    return players[guild_id]


def format_duration(seconds: int) -> str:
    if not seconds:
        return "Unknown"

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"

    return f"{minutes}:{secs:02d}"


async def get_song(query: str, requester: discord.Member) -> Optional[Song]:
    print(f"Searching for: {query}")

    search = query

    if not query.startswith(("http://", "https://")):
        search = f"ytsearch1:{query}"

    def extract():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            return ydl.extract_info(search, download=False)

    try:
        info = await asyncio.to_thread(extract)

        if not info:
            return None

        if "entries" in info:
            entries = info.get("entries")

            if not entries:
                return None

            info = entries[0]

        title = info.get("title", "Unknown title")
        webpage_url = info.get("webpage_url") or info.get("original_url") or query
        duration = info.get("duration") or 0

        stream_url = info.get("url")

        # Fallback: manually find the best audio format.
        if not stream_url:
            formats = info.get("formats", [])

            audio_formats = [
                fmt
                for fmt in formats
                if fmt.get("url")
                and fmt.get("acodec")
                and fmt.get("acodec") != "none"
            ]

            if not audio_formats:
                return None

            audio_formats.sort(
                key=lambda fmt: (
                    fmt.get("abr") or 0,
                    fmt.get("asr") or 0,
                    fmt.get("filesize") or 0
                ),
                reverse=True
            )

            stream_url = audio_formats[0]["url"]

        print(f"Found: {title}")
        print(f"Duration: {format_duration(duration)}")

        return Song(
            title=title,
            url=stream_url,
            webpage_url=webpage_url,
            duration=duration,
            requester=requester
        )

    except Exception as error:
        print("❌ YT-DLP ERROR")
        print(f"Type: {type(error).__name__}")
        print(f"Error: {error}")

        return None


async def ensure_voice(
    interaction: discord.Interaction
) -> Optional[discord.VoiceClient]:

    if not interaction.guild:
        return None

    if not interaction.user.voice:
        await interaction.followup.send(
            "❌ You need to be in a voice channel first.",
            ephemeral=True
        )
        return None

    channel = interaction.user.voice.channel

    voice_client = interaction.guild.voice_client

    try:
        if voice_client:

            if voice_client.channel != channel:
                print(f"Moving to voice channel: {channel.name}")
                await voice_client.move_to(channel)

            return voice_client

        print(f"Joining voice channel: {channel.name}")

        voice_client = await channel.connect()

        print("✅ Successfully connected to voice.")

        return voice_client

    except Exception as error:
        print("❌ VOICE CONNECTION ERROR")
        print(f"Type: {type(error).__name__}")
        print(f"Error: {error}")

        await interaction.followup.send(
            "❌ I couldn't join your voice channel.\n"
            f"Error: `{type(error).__name__}: {error}`",
            ephemeral=True
        )

        return None


# =========================================================
# PLAYBACK
# =========================================================

async def play_next(guild: discord.Guild):

    player = get_player(guild.id)
    voice_client = guild.voice_client

    if not voice_client or not voice_client.is_connected():
        player.current = None
        return

    if voice_client.is_playing():
        return

    if player.loop and player.current:
        song = player.current

    else:
        if not player.queue:
            player.current = None
            return

        song = player.queue.pop(0)
        player.current = song

    try:
        source = discord.FFmpegPCMAudio(
            song.url,
            **FFMPEG_OPTIONS
        )

        source = discord.PCMVolumeTransformer(
            source,
            volume=player.volume
        )

        def after_play(error):
            if error:
                print(
                    f"Playback error: "
                    f"{type(error).__name__}: {error}"
                )

            asyncio.run_coroutine_threadsafe(
                play_next(guild),
                bot.loop
            )

        voice_client.play(
            source,
            after=after_play
        )

        print(f"▶️ Now playing: {song.title}")

    except Exception as error:
        print("❌ PLAYBACK ERROR")
        print(f"Type: {type(error).__name__}")
        print(f"Error: {error}")

        player.current = None

        await play_next(guild)


async def start_player(guild: discord.Guild):

    player = get_player(guild.id)

    if player.player_task and not player.player_task.done():
        return

    async def runner():
        await play_next(guild)

    player.player_task = asyncio.create_task(runner())


# =========================================================
# /PLAY
# =========================================================

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

    await interaction.response.defer()

    if not interaction.guild:
        await interaction.followup.send(
            "❌ This command can only be used in a server."
        )
        return

    voice_client = await ensure_voice(interaction)

    if not voice_client:
        return

    song = await get_song(
        query,
        interaction.user
    )

    if not song:
        await interaction.followup.send(
            "❌ I couldn't find that song."
        )
        return

    player = get_player(interaction.guild.id)

    if voice_client.is_playing() or voice_client.is_paused():

        player.queue.append(song)

        position = len(player.queue)

        await interaction.followup.send(
            f"➕ Added to queue: **{song.title}**\n"
            f"Position: **#{position}**"
        )

        return

    player.queue.append(song)

    await start_player(interaction.guild)

    await interaction.followup.send(
        f"▶️ Playing: **{song.title}**\n"
        f"Duration: `{format_duration(song.duration)}`"
    )


# =========================================================
# /PAUSE
# =========================================================

@bot.tree.command(
    name="pause",
    description="Pause the current song."
)
async def pause(interaction: discord.Interaction):

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message(
            "❌ Nothing is currently playing."
        )
        return

    voice_client.pause()

    await interaction.response.send_message(
        "⏸️ Music paused."
    )


# =========================================================
# /RESUME
# =========================================================

@bot.tree.command(
    name="resume",
    description="Resume the current song."
)
async def resume(interaction: discord.Interaction):

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_paused():
        await interaction.response.send_message(
            "❌ Music isn't paused."
        )
        return

    voice_client.resume()

    await interaction.response.send_message(
        "▶️ Music resumed."
    )


# =========================================================
# /SKIP
# =========================================================

@bot.tree.command(
    name="skip",
    description="Skip the current song."
)
async def skip(interaction: discord.Interaction):

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message(
            "❌ Nothing is currently playing."
        )
        return

    voice_client.stop()

    await interaction.response.send_message(
        "⏭️ Song skipped."
    )


# =========================================================
# /STOP
# =========================================================

@bot.tree.command(
    name="stop",
    description="Stop music and clear the queue."
)
async def stop(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client
    player = get_player(interaction.guild.id)

    player.queue.clear()
    player.current = None
    player.loop = False

    if voice_client and voice_client.is_playing():
        voice_client.stop()

    await interaction.response.send_message(
        "⏹️ Music stopped and queue cleared."
    )


# =========================================================
# /QUEUE
# =========================================================

@bot.tree.command(
    name="queue",
    description="Show the current music queue."
)
async def queue(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    if not player.current and not player.queue:
        await interaction.response.send_message(
            "📭 The queue is empty."
        )
        return

    lines = []

    if player.current:
        lines.append(
            f"🎵 **Now playing:** {player.current.title}"
        )

    if player.queue:

        lines.append("")
        lines.append("**Up next:**")

        for index, song in enumerate(
            player.queue[:10],
            start=1
        ):
            lines.append(
                f"`{index}.` {song.title} "
                f"`[{format_duration(song.duration)}]`"
            )

        if len(player.queue) > 10:
            lines.append(
                f"\n...and {len(player.queue) - 10} more."
            )

    await interaction.response.send_message(
        "\n".join(lines)
    )


# =========================================================
# /NOWPLAYING
# =========================================================

@bot.tree.command(
    name="nowplaying",
    description="Show the currently playing song."
)
async def nowplaying(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    if not player.current:
        await interaction.response.send_message(
            "❌ Nothing is currently playing."
        )
        return

    await interaction.response.send_message(
        f"🎵 **{player.current.title}**\n"
        f"Duration: `{format_duration(player.current.duration)}`\n"
        f"Requested by: {player.current.requester.mention}"
    )


# =========================================================
# /VOLUME
# =========================================================

@bot.tree.command(
    name="volume",
    description="Change the music volume."
)
@app_commands.describe(
    volume="Volume from 1 to 100"
)
async def volume(
    interaction: discord.Interaction,
    volume: app_commands.Range[int, 1, 100]
):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    player.volume = volume / 100

    voice_client = interaction.guild.voice_client

    if (
        voice_client
        and voice_client.source
        and isinstance(
            voice_client.source,
            discord.PCMVolumeTransformer
        )
    ):
        voice_client.source.volume = player.volume

    await interaction.response.send_message(
        f"🔊 Volume set to **{volume}%**."
    )


# =========================================================
# /LOOP
# =========================================================

@bot.tree.command(
    name="loop",
    description="Toggle loop mode."
)
async def loop(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    player.loop = not player.loop

    if player.loop:
        await interaction.response.send_message(
            "🔁 Loop mode **enabled**."
        )
    else:
        await interaction.response.send_message(
            "➡️ Loop mode **disabled**."
        )


# =========================================================
# /SHUFFLE
# =========================================================

@bot.tree.command(
    name="shuffle",
    description="Shuffle the music queue."
)
async def shuffle(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    if len(player.queue) < 2:
        await interaction.response.send_message(
            "❌ You need at least 2 songs in the queue."
        )
        return

    random.shuffle(player.queue)

    await interaction.response.send_message(
        "🔀 Queue shuffled."
    )


# =========================================================
# /REMOVE
# =========================================================

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

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    index = position - 1

    if index >= len(player.queue):
        await interaction.response.send_message(
            "❌ That queue position doesn't exist."
        )
        return

    song = player.queue.pop(index)

    await interaction.response.send_message(
        f"🗑️ Removed **{song.title}** from the queue."
    )


# =========================================================
# /CLEAR
# =========================================================

@bot.tree.command(
    name="clear",
    description="Clear the music queue."
)
async def clear(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    amount = len(player.queue)

    player.queue.clear()

    await interaction.response.send_message(
        f"🗑️ Cleared **{amount}** songs from the queue."
    )


# =========================================================
# /DISCONNECT
# =========================================================

@bot.tree.command(
    name="disconnect",
    description="Disconnect the bot from the voice channel."
)
async def disconnect(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client
    player = get_player(interaction.guild.id)

    player.queue.clear()
    player.current = None
    player.loop = False

    if voice_client:
        if voice_client.is_playing():
            voice_client.stop()

        await voice_client.disconnect()

        await interaction.response.send_message(
            "👋 Disconnected from the voice channel."
        )
        return

    await interaction.response.send_message(
        "❌ I'm not connected to a voice channel."
    )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    print(
        f"🎵 Music bot connected as {bot.user}"
    )

    try:
        synced = await bot.tree.sync()

        print(
            f"✅ Slash commands synchronized: {len(synced)}"
        )

    except Exception as error:

        print("❌ Slash command sync error:")
        print(
            f"{type(error).__name__}: {error}"
        )

    print("🎵 Music bot is ready!")


# =========================================================
# START
# =========================================================

bot.run(TOKEN)