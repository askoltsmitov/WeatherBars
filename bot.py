from __future__ import unicode_literals
from discord.ext import commands
import discord
import pyowm
import schedule
import time
from pyowm import timeutils

import asyncio
import itertools
import sys
import traceback
import os
from async_timeout import timeout
from functools import partial
from youtube_dl import YoutubeDL
from discord import opus

OPUS_LIBS = ['libopus-0.x86.dll', 'libopus-0.x64.dll',
             'libopus-0.dll', 'libopus.so.0', 'libopus.0.dylib']

bot = commands.Bot(command_prefix='-')
owm = pyowm.OWM('8638c55431d913688db69d830ed8d17b', language='ru')

fc = owm.daily_forecast('Angarsk,RU')
times = timeutils.tomorrow(14)
send_Resume = ""
sent = ""

def load_opus_lib(opus_libs=OPUS_LIBS):
    if opus.is_loaded():
        return True

    for opus_lib in opus_libs:
            try:
                opus.load_opus(opus_lib)
                return
            except OSError:
                pass

    raise RuntimeError('Could not load an opus lib. Tried %s' %
                       (', '.join(opus_libs)))
load_opus_lib()

ytdlopts = {
	'format': 'bestaudio/best',
	'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
	'restrictfilenames': True,
	'noplaylist': True,
	'nocheckcertificate': True,
	'ignoreerrors': False,
	'logtostderr': False,
	'quiet': True,
	'no_warnings': True,
	'default_search': 'auto',
	'source_address': '0.0.0.0'
}

ffmpegopts = {
		'before_options': '-nostdin',
		'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)

class VoiceConnectionError(commands.CommandError):
  """Custom Exception class for connection errors."""


class InvalidVoiceChannel(VoiceConnectionError):
  """Exception for cases of invalid Voice Channels."""


class YTDLSource(discord.PCMVolumeTransformer):

	def __init__(self, source, *, data, requester):
		super().__init__(source)
		self.requester = requester

		self.title = data.get('title')
		self.web_url = data.get('webpage_url')

		# YTDL info dicts (data) have other useful information you might want
		# https://github.com/rg3/youtube-dl/blob/master/README.md

	def __getitem__(self, item: str):
		"""Allows us to access attributes similar to a dict.
		This is only useful when you are NOT downloading.
		"""
		return self.__getattribute__(item)

	@classmethod
	async def create_source(cls, ctx, search: str, *, loop, download=False):
		loop = loop or asyncio.get_event_loop()

		to_run = partial(ytdl.extract_info, url=search, download=download)
		data = await loop.run_in_executor(None, to_run)

		if 'entries' in data:
			# take first item from a playlist
			data = data['entries'][0]

		await ctx.send(f'```ini\n[{data["title"]}] - добавлена в очередь\n```', delete_after=15)

		if download:
			source = ytdl.prepare_filename(data)
		else:
			return {'webpage_url': data['webpage_url'], 'requester': ctx.author.name, 'title': data['title']}

		return cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author.name)

	@classmethod
	async def regather_stream(cls, data, *, loop):
		"""Used for preparing a stream, instead of downloading.
		Since Youtube Streaming links expire."""
		loop = loop or asyncio.get_event_loop()
		requester = data['requester']

		to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
		data = await loop.run_in_executor(None, to_run)

		return cls(discord.FFmpegPCMAudio(data['url']), data=data, requester=requester)

class MusicPlayer:
	"""A class which is assigned to each guild using the bot for Music.
	This class implements a queue and loop, which allows for different guilds to listen to different playlists
	simultaneously.
	When the bot disconnects from the Voice it's instance will be destroyed.
	"""

	__slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

	def __init__(self, ctx):
		self.bot = ctx.bot
		self._guild = ctx.guild
		self._channel = ctx.channel
		self._cog = ctx.cog

		self.queue = asyncio.Queue()
		self.next = asyncio.Event()

		self.np = None  # Now playing message
		self.volume = .1
		self.current = None

		ctx.bot.loop.create_task(self.player_loop())

	async def player_loop(self):
		"""Our main player loop."""
		await self.bot.wait_until_ready()

		while not self.bot.is_closed():
			self.next.clear()

			try:
				# Wait for the next song. If we timeout cancel the player and disconnect...
				async with timeout(300):  # 5 minutes...
					source = await self.queue.get()
			except asyncio.TimeoutError:
				return self.destroy(self._guild)

			if not isinstance(source, YTDLSource):
				# Source was probably a stream (not downloaded)
				# So we should regather to prevent stream expiration
				try:
					source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
				except Exception as e:
					await self._channel.send(f'There was an error processing your song.\n'
																	f'```css\n[{e}]\n```')
					continue

			source.volume = self.volume
			self.current = source

			self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
			self.np = await self._channel.send(f'**Сейчас играет:** `{source.title}` by **{source.requester}**')
			await self.next.wait()

			# Make sure the FFmpeg process is cleaned up.
			source.cleanup()
			self.current = None

			try:
				# We are no longer playing this song...
				await self.np.delete()
			except discord.HTTPException:
				pass

	def destroy(self, guild):
		"""Disconnect and cleanup the player."""
		return self.bot.loop.create_task(self._cog.cleanup(guild))

class Music:
	"""Music related commands."""

	__slots__ = ('bot', 'players')

	def __init__(self, bot):
		self.bot = bot
		self.players = {}

	async def cleanup(self, guild):
		try:
			await guild.voice_client.disconnect()
		except AttributeError:
			pass

		try:
				del self.players[guild.id]
		except KeyError:
				pass

	async def __local_check(self, ctx):
		"""A local check which applies to all commands in this cog."""
		if not ctx.guild:
			raise commands.NoPrivateMessage
		return True

	async def __error(self, ctx, error):
		"""A local error handler for all errors arising from commands in this cog."""
		if isinstance(error, commands.NoPrivateMessage):
			try:
				return await ctx.send('This command can not be used in Private Messages.')
			except discord.HTTPException:
				pass
		elif isinstance(error, InvalidVoiceChannel):
			await ctx.send('Error connecting to Voice Channel. '
										 'Please make sure you are in a valid channel or provide me with one')

		print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
		traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

	def get_player(self, ctx):
		"""Retrieve the guild player, or generate one."""
		try:
			player = self.players[ctx.guild.id]
		except KeyError:
			player = MusicPlayer(ctx)
			self.players[ctx.guild.id] = player

		return player

	@commands.command(name='connect', aliases=['join'])
	async def connect(self, ctx, *, channel: discord.VoiceChannel=None):
		"""Подключение к чату
		Parameters
		------------
		channel: discord.VoiceChannel [Optional]
				The channel to connect to. If a channel is not specified, an attempt to join the voice channel you are in
				will be made.
		This command also handles moving the bot to different channels.
		"""
		await ctx.message.delete()
		if not channel:
			try:
				channel = bot.get_channel(199459074243297281)
			except AttributeError:
				raise InvalidVoiceChannel('No channel to join. Please either specify a valid channel or join one.', delete_after=15)

			vc = ctx.voice_client

			if vc:
				if vc.channel.id == channel.id:
					return
				try:
					await vc.move_to(channel)
				except asyncio.TimeoutError:
					raise VoiceConnectionError(f'Moving to channel: <{channel}> timed out.')
			else:
				try:
					await channel.connect()
				except asyncio.TimeoutError:
					raise VoiceConnectionError(f'Connecting to channel: <{channel}> timed out.')

			await ctx.send(f'**Я родился!**', delete_after=15)

	@commands.command(name='play', aliases=['здфн'])
	async def play(self, ctx, *, search: str):
			"""Добавление песни в очередь
			This command attempts to join a valid voice channel if the bot is not already in one.
			Uses YTDL to automatically search and retrieve a song.
			Parameters
			------------
			search: str [Required]
					The song to search and retrieve using YTDL. This could be a simple search, an ID or URL.
			"""
			await ctx.message.delete()
			await ctx.trigger_typing()

			vc = ctx.voice_client

			if not vc:
				channel = bot.get_channel(199459074243297281)
				await channel.connect()
			#	await ctx.invoke(self.connect)

			player = self.get_player(ctx)

			# If download is False, source will be a dict which will be used later to regather the stream.
			# If download is True, source will be a discord.FFmpegPCMAudio with a VolumeTransformer.
			source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=False)

			await player.queue.put(source)

	@commands.command(name='pause', aliases=['зфгыу'])
	async def pause(self, ctx):
			"""Пауза"""
			global send_Resume
			await ctx.message.delete()
			vc = ctx.voice_client

			if not vc or not vc.is_playing():
					return await ctx.send('Я сейчас ничего не играю!', delete_after=20)
			elif vc.is_paused():
					return

			vc.pause()
			send_Resume = await ctx.send(f'**{ctx.author.name}**: Поставил на паузу!')

	@commands.command(name='resume', aliases=['куыгьу'])
	async def resume(self, ctx):
			"""Продолжить"""
			await send_Resume.delete()
			await ctx.message.delete()
			vc = ctx.voice_client

			if not vc or not vc.is_connected():
					return await ctx.send('Я сейчас ничего не играю!', delete_after=20)
			elif not vc.is_paused():
					return

			vc.resume()
			await ctx.send(f'**{ctx.author.name}**: Возобновил песню!', delete_after=15)

	@commands.command(name='skip', aliases=['ылшз'])
	async def skip(self, ctx):
			"""Пропуск песни"""
			await ctx.message.delete()
			vc = ctx.voice_client

			if not vc or not vc.is_connected():
					return await ctx.send('Я сейчас ничего не играю!', delete_after=20)

			if vc.is_paused():
					pass
			elif not vc.is_playing():
					return

			vc.stop()
			await ctx.send(f'**{ctx.author.name}**: Не хочет эту песню!', delete_after=15)

	@commands.command(name='queue', aliases=['йгугу'])
	async def queue_info(self, ctx):
			"""Показ очереди"""
			await ctx.message.delete()
			vc = ctx.voice_client

			if not vc or not vc.is_connected():
					return await ctx.send('Я не подключен к голосовому чату', delete_after=20)

			player = self.get_player(ctx)
			if player.queue.empty():
					return await ctx.send('Очередь пуста...', delete_after=15)

			# Grab up to 5 entries from the queue...
			upcoming = list(itertools.islice(player.queue._queue, 0, 5))

			fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
			embed = discord.Embed(title=f'В ожидании: {len(upcoming)}', description=fmt)

			await ctx.send(embed=embed, delete_after=20)

	@commands.command(name='np', aliases=['тз'])
	async def now_playing(self, ctx):
			"""Играющая песня"""
			await ctx.message.delete()
			vc = ctx.voice_client

			if not vc or not vc.is_connected():
					return await ctx.send('Я не подключен к голосовому чату!', delete_after=20)

			player = self.get_player(ctx)
			if not player.current:
					return await ctx.send('Я сейчас ничего не играю!')

			try:
					# Remove our previous now_playing message.
					await player.np.delete()
			except discord.HTTPException:
					pass

			player.np = await ctx.send(f'**Играет:** `{vc.source.title}` '
																 f'requested by `{vc.source.requester}`')

	@commands.command(name='vol', aliases=['мщд'])
	async def change_volume(self, ctx, *, vol: float):
		"""Изменение громкости
		Parameters
		------------
		volume: float or int [Required]
				The volume to set the player to in percentage. This must be between 1 and 100.
		"""
		await ctx.message.delete()
		vc = ctx.voice_client

		if not vc or not vc.is_connected():
				return await ctx.send('Я не подключен к голосовому чату!', delete_after=20)

		if not 0 < vol < 101:
				return await ctx.send('Значение может быть от 1 до 100.', delete_after=15)

		player = self.get_player(ctx)

		if vc.source:
				vc.source.volume = vol / 100

		player.volume = vol / 100
		await ctx.send(f'**{ctx.author.name}**: Поставил громкость **{vol}%**', delete_after=15)

	@commands.command(name='stop', aliases=['ыещз'])
	async def stop(self, ctx):
			"""Остановка песни.
			!Warning!
					This will destroy the player assigned to your guild, also deleting any queued songs and settings.
			"""
			await ctx.message.delete()
			vc = ctx.voice_client

			if not vc or not vc.is_connected():
				return await ctx.send('Я сейчас ничего не играю!', delete_after=20)
			else:
				await vc.disconnect()

			await self.cleanup(ctx.guild)


class Mute:
	"""Mute commands"""

	__slots__ = ('bot')

	def __init__(self, bot):
		self.bot = bot

		# <--- Mute Command --->
	@commands.command(name='mute')
	async def mute(self, ctx, member: discord.Member):
		'''Мут'''
		global sent
		await ctx.message.delete()
		role = discord.utils.get(member.guild.roles, name="Muted")
		embed = discord.Embed(title="Uh oh retard alert!", description="-unmute {}".format(member.name) , color=0xFF0000)
		embed.set_footer(text="Shut up, please by " + str(ctx.author.name))
		embed.set_thumbnail(url=member.avatar_url)
		await member.add_roles(role)
		channel = bot.get_channel(199459074243297280)
		sent = await channel.send(embed=embed)

	# <--- Unmute Command --->
	@commands.command(name='unmute')
	async def unmute(self, ctx, member: discord.Member):
		'''Анмут'''
		await ctx.message.delete()
		await sent.delete()
		role = discord.utils.get(member.guild.roles, name="Muted")
		await member.remove_roles(role)

def changeWeather():
	print("Test 1")
	@bot.event
	async def on_ready():
		print("Test 2")
		# Нахождение температуры
		weather_cry = ""
		w = fc.get_weather_at(times)
		start = str((w.get_temperature('celsius'))).find(" ")
		end = str((w.get_temperature('celsius'))).find(",")
		temp = str((w.get_temperature('celsius')))[start:end-1]

		if fc.will_be_rainy_at(times):
			weather_cry = " , возможен :cloud_rain:"

		if fc.will_be_snowy_at(times):
			weather_cry = " , возможен :cloud_snow:"

		channel = bot.get_channel(199459074243297280)
		await channel.send("Температура на завтра: " + temp + " C" + weather_cry)


bot.add_cog(Music(bot))
bot.add_cog(Mute(bot))
bot.run(os.getenv('TOKEN'))

schedule.every().day.at("02:10").do(changeWeather)

while True:
	schedule.run_pending()
	time.sleep(10)
