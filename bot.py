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

def getRun():
	bot.run(os.getenv('TOKEN'))

@bot.event
async def on_ready():
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

schedule.every().day.at("20:07").do(getRun)

while True:
	schedule.run_pending()
	time.sleep(10)
	print("Сплю")
