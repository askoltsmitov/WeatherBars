from __future__ import unicode_literals
from discord.ext import commands
import discord
import pyowm
from pyowm import timeutils
import schedule
import time
import asyncio
import itertools
import os
from async_timeout import timeout
from functools import partial

bot = commands.Bot(command_prefix='-')
owm = pyowm.OWM('8638c55431d913688db69d830ed8d17b', language='ru')

fc = owm.daily_forecast('Angarsk,RU')
times = timeutils.tomorrow(14)
sent = ""

def getRun():
	bot.run(os.getenv('TOKEN'))

@bot.event
async def on_ready():
	# Нахождение температуры
	global sent
	weather_cry = ""
	w = fc.get_weather_at(times)
	mor_start = str((w.get_temperature('celsius'))).find("'morn'")
	mpr_end 	= str((w.get_temperature('celsius'))).find("}")
	mor_temp 	= str((w.get_temperature('celsius')))[mor_start+8:mpr_end-1]
	day_start = str((w.get_temperature('celsius'))).find("'day'")
	day_end 	= str((w.get_temperature('celsius'))).find(", 'min'")
	day_temp 	= str((w.get_temperature('celsius')))[day_start+7:day_end-1]
	eve_start = str((w.get_temperature('celsius'))).find("'eve'")
	eve_end 	= str((w.get_temperature('celsius'))).find(", 'morn'")
	eve_temp 	= str((w.get_temperature('celsius')))[eve_start+7:eve_end-1]
	
	if fc.will_be_rainy_at(times):
		weather_cry = "\nВозможен :cloud_rain:"

	if fc.will_be_snowy_at(times):
		weather_cry = "\nВозможен :cloud_snow:"

	channel = bot.get_channel(199459074243297280)
	try:
		await sent.delete()
	except AttributeError:
		print("В 1-й раз")
	sent = await channel.send(str(w) + "\nТемпература на завтра:\nУтром:     " + mor_temp + "\nДнём:       "+ day_temp + "\nВечером: " + eve_temp + weather_cry)
	"""
	try:
		await bot.close()
	except RuntimeError:
		print("Потом разберёмся.")
	"""

schedule.every().day.at("16:03").do(getRun)

while True:
	schedule.run_pending()
	time.sleep(10)
