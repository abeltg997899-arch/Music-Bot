import os
import asyncio
import random
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# =========================================================
# CONFIGURATION
# =========================================================

TOKEN = os.getenv("MUSIC_BOT_TOKEN")

if not TOKEN:
    print("ERROR: MUSIC_BOT_TOKEN is not configured.")
    raise SystemExit(1)


# =========================================================
# YOUTUBE / YT-DLP
# =========================================================

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",

    # Use cookies from Chrome
    "cookiesfrombrowser": ("chrome",),

    "extractor_args": {
        "youtube": {
            "player_client": ["web", "android"]
        }
    },
}


# =========================================================
# FFMPEG
# =========================================================

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
    ),
}


# =========================================================
# DISCORD BOT
# =========================================================

intents = discord.Intents.default()
intents.voice_states = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents
)


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


class GuildPlayer:
    def __init__(self):
        self.queue = []
        self.current: Optional[Song] = None
        self.loop = False
        self.volume = 1.0
        self.player_task = None


players = {}


def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()

    return players[guild_id]


# =========================================================
# HELPERS
# =========================================================

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


def get_song(query: str, requester: discord.Member) -> Optional[Song]:

    if not query.startswith(("http://", "https://")):
        query = f"ytsearch1:{query}"

    try:
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)

        if not info:
            return None

        if "entries" in info:
            entries = info.get("entries")

            if not entries:
                return None

            info = entries[0]

        title = info.get("title", "Unknown")

        webpage_url = info.get("webpage_url")

        if not webpage_url:
            webpage_url = info.get("original_url", "")

        duration = info.get("duration") or 0

        stream_url = info.get("url")

        if not stream_url:
            formats = info.get("formats", [])

            audio_formats = [
                f
                for f in formats
                if f.get("url")
                and f.get("acodec") not in (None, "none")
            ]

            if audio_formats:
                audio_formats.sort(
                    key=lambda f: (
                        f.get("abr") or 0,
                        f.get("asr") or 0,
                        f.get("filesize") or 0
                    ),
                    reverse=True
                )

                stream_url = audio_formats[0].get("url")

        if not stream_url:
            return None

        return Song(
            title=title,
            url=stream_url,
            webpage_url=webpage_url,
            duration=duration,
            requester=requester
        )

    except Exception as e:
        print(f"❌ yt-dlp error: {type(e).__name__}: {e}")
        return None


async def ensure_voice(
    interaction: discord.Interaction
) -> Optional[discord.VoiceClient]:

    if not interaction.guild:
        return None

    user = interaction.user

    if not isinstance(user, discord.Member):
        return None

    if not user.voice or not user.voice.channel:
        await interaction.followup.send(
            "❌ You must be in a voice channel first.",
            ephemeral=True
        )
        return None

    voice_channel = user.voice.channel
    voice_client = interaction.guild.voice_client

    try:

        if voice_client:

            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)

            return voice_client

        print(f"Joining voice channel: {voice_channel.name}")

        voice_client = await voice_channel.connect()

        print("✅ Successfully connected to voice.")

        return voice_client

    except Exception as e:

        print(
            f"❌ Voice connection error: "
            f"{type(e).__name__}: {e}"
        )

        await interaction.followup.send(
            f"❌ I couldn't join the voice channel: `{e}`",
            ephemeral=True
        )

        return None


# =========================================================
# PLAYER
# =========================================================

async def play_next(
    guild: discord.Guild,
    voice_client: discord.VoiceClient
):

    player = get_player(guild.id)

    if not voice_client or not voice_client.is_connected():
        player.current = None
        return

    if player.loop and player.current:

        song = player.current

    else:

        if not player.queue:
            player.current = None

            try:
                await voice_client.disconnect()
            except Exception:
                pass

            return

        song = player.queue.pop(0)
        player.current = song

    print(f"🎵 Playing: {song.title}")

    try:

        source = discord.FFmpegPCMAudio(
            song.url,
            **FFMPEG_OPTIONS
        )

        source = discord.PCMVolumeTransformer(
            source,
            volume=player.volume
        )

        def after_playing(error):

            if error:
                print(
                    f"❌ Playback error: "
                    f"{type(error).__name__}: {error}"
                )

            asyncio.run_coroutine_threadsafe(
                play_next(guild, voice_client),
                bot.loop
            )

        voice_client.play(
            source,
            after=after_playing
        )

    except Exception as e:

        print(
            f"❌ Could not start playback: "
            f"{type(e).__name__}: {e}"
        )

        player.current = None

        await play_next(guild, voice_client)


async def start_player(
    guild: discord.Guild,
    voice_client: discord.VoiceClient
):

    if voice_client.is_playing():
        return

    await play_next(guild, voice_client)


# =========================================================
# /PLAY
# =========================================================

@bot.tree.command(
    name="play",
    description="Play a song or add it to the queue"
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

    print(f"🔎 Searching for: {query}")

    song = await asyncio.to_thread(
        get_song,
        query,
        interaction.user
    )

    if not song:

        await interaction.followup.send(
            "❌ I couldn't find or access that song."
        )

        return

    player = get_player(interaction.guild.id)

    was_playing = (
        voice_client.is_playing()
        or voice_client.is_paused()
        or player.current is not None
    )

    player.queue.append(song)

    if was_playing:

        await interaction.followup.send(
            f"🎵 **Added to queue:** {song.title}\n"
            f"⏱️ Duration: `{format_duration(song.duration)}`\n"
            f"📋 Position: `{len(player.queue)}`"
        )

    else:

        await interaction.followup.send(
            f"▶️ **Now playing:** {song.title}\n"
            f"⏱️ Duration: `{format_duration(song.duration)}`"
        )

        await start_player(
            interaction.guild,
            voice_client
        )


# =========================================================
# /PAUSE
# =========================================================

@bot.tree.command(
    name="pause",
    description="Pause the current song"
)
async def pause(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():

        await interaction.response.send_message(
            "❌ Nothing is currently playing.",
            ephemeral=True
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
    description="Resume the current song"
)
async def resume(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_paused():

        await interaction.response.send_message(
            "❌ Music is not paused.",
            ephemeral=True
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
    description="Skip the current song"
)
async def skip(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():

        await interaction.response.send_message(
            "❌ Nothing is currently playing.",
            ephemeral=True
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
    description="Stop music and clear the queue"
)
async def stop(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client
    player = get_player(interaction.guild.id)

    player.queue.clear()
    player.current = None
    player.loop = False

    if voice_client and (
        voice_client.is_playing()
        or voice_client.is_paused()
    ):
        voice_client.stop()

    await interaction.response.send_message(
        "⏹️ Music stopped and queue cleared."
    )


# =========================================================
# /QUEUE
# =========================================================

@bot.tree.command(
    name="queue",
    description="Show the current music queue"
)
async def queue(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    if not player.current and not player.queue:

        await interaction.response.send_message(
            "📋 The queue is empty."
        )

        return

    embed = discord.Embed(
        title="🎵 Music Queue"
    )

    if player.current:

        embed.add_field(
            name="▶️ Now Playing",
            value=(
                f"**{player.current.title}**\n"
                f"`{format_duration(player.current.duration)}`"
            ),
            inline=False
        )

    if player.queue:

        queue_text = ""

        for index, song in enumerate(
            player.queue[:10],
            start=1
        ):

            queue_text += (
                f"`{index}.` **{song.title}** "
                f"`{format_duration(song.duration)}`\n"
            )

        if len(player.queue) > 10:
            queue_text += (
                f"\n...and "
                f"{len(player.queue) - 10} more."
            )

        embed.add_field(
            name="📋 Up Next",
            value=queue_text,
            inline=False
        )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# /NOWPLAYING
# =========================================================

@bot.tree.command(
    name="nowplaying",
    description="Show the currently playing song"
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

    song = player.current

    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{song.title}**"
    )

    embed.add_field(
        name="Duration",
        value=format_duration(song.duration)
    )

    embed.add_field(
        name="Requested by",
        value=song.requester.display_name
    )

    embed.add_field(
        name="Volume",
        value=f"{int(player.volume * 100)}%"
    )

    embed.add_field(
        name="Loop",
        value="Enabled" if player.loop else "Disabled"
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# /VOLUME
# =========================================================

@bot.tree.command(
    name="volume",
    description="Change the music volume"
)
@app_commands.describe(
    volume="Volume from 0 to 100"
)
async def volume(
    interaction: discord.Interaction,
    volume: app_commands.Range[int, 0, 100]
):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    player.volume = volume / 100

    voice_client = interaction.guild.voice_client

    if voice_client and voice_client.source:

        if isinstance(
            voice_client.source,
            discord.PCMVolumeTransformer
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
    description="Toggle loop for the current song"
)
async def loop(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    player.loop = not player.loop

    if player.loop:

        message = "🔁 Loop enabled."

    else:

        message = "➡️ Loop disabled."

    await interaction.response.send_message(
        message
    )


# =========================================================
# /SHUFFLE
# =========================================================

@bot.tree.command(
    name="shuffle",
    description="Shuffle the music queue"
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
    description="Remove a song from the queue"
)
@app_commands.describe(
    position="Position of the song in the queue"
)
async def remove(
    interaction: discord.Interaction,
    position: app_commands.Range[int, 1, 1000]
):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    index = position - 1

    if index < 0 or index >= len(player.queue):

        await interaction.response.send_message(
            "❌ That queue position does not exist.",
            ephemeral=True
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
    description="Clear all songs from the queue"
)
async def clear(interaction: discord.Interaction):

    if not interaction.guild:
        return

    player = get_player(interaction.guild.id)

    amount = len(player.queue)

    player.queue.clear()

    await interaction.response.send_message(
        f"🗑️ Removed **{amount}** song(s) from the queue."
    )


# =========================================================
# /DISCONNECT
# =========================================================

@bot.tree.command(
    name="disconnect",
    description="Disconnect the bot from the voice channel"
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

    else:

        await interaction.response.send_message(
            "❌ I'm not connected to a voice channel.",
            ephemeral=True
        )


# =========================================================
# /LEAVE
# =========================================================

@bot.tree.command(
    name="leave",
    description="Leave the current voice channel"
)
async def leave(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client
    player = get_player(interaction.guild.id)

    if not voice_client:

        await interaction.response.send_message(
            "❌ I'm not in a voice channel.",
            ephemeral=True
        )

        return

    # Stop the current song
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    # Clear music state
    player.queue.clear()
    player.current = None
    player.loop = False

    # Leave voice channel
    await voice_client.disconnect()

    await interaction.response.send_message(
        "👋 I've left the voice channel."
    )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    print("======================================")
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("======================================")

    try:

        synced = await bot.tree.sync()

        print(
            f"✅ Slash commands synchronized: "
            f"{len(synced)}"
        )

        print("Available commands:")

        for command in synced:
            print(f"  /{command.name}")

    except Exception as e:

        print(
            f"❌ Failed to sync slash commands: "
            f"{type(e).__name__}: {e}"
        )

    print("🎵 Music bot is ready.")


# =========================================================
# START
# =========================================================

print("Starting Music Abel Uncopylocked...")

bot.run(TOKEN)