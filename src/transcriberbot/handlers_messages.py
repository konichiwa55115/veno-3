import config
from database import TBDB
import resources as R

from transcriberbot import tbfilters
from transcriberbot.bot import TranscriberBot
from transcriberbot.bot import get_chat_id, get_message_id, get_language_list, welcome_message, message

import telegram
from telegram.ext import Filters
from transcriberbot import tbfilters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import audiotools
import html
import logging
import traceback
import time
import os

logger = logging.getLogger(__name__)

# Message callbacks
@message(Filters.text & Filters.private)
def private_message(bot, update):
  chat_id = get_chat_id(update)
  bot.send_message(
    chat_id=chat_id,
    text=R.get_string_resource("message_private", TBDB.get_chat_lang(chat_id))
  )

def transcribe_audio_file(bot, update, path):
  chat_id = get_chat_id(update)
  lang = TBDB.get_chat_lang(chat_id)
  message_id = get_message_id(update)
  is_group = chat_id < 0

  api_key = config.get_config_prop("wit").get(lang, None)
  if api_key is None:
    logger.error("Language not found in wit.json %s", lang)
    message = bot.send_message(
        chat_id=chat_id,
        text=R.get_string_resource("unknown_api_key", lang).format(language=lang) + "\n",
        reply_to_message_id=message_id,
        parse_mode="html",
        is_group=is_group
    ).result()
    return
  logger.debug("Using key %s for lang %s", api_key, lang)

  message = bot.send_message(
    chat_id=chat_id,
    text=R.get_string_resource("transcribing", lang) + "\n",
    reply_to_message_id=message_id,
    parse_mode="html",
    is_group=is_group
  ).result()

  TranscriberBot.get().start_thread(message_id)
  logger.debug("Starting thread %d", message_id)

  keyboard = InlineKeyboardMarkup(
    [[InlineKeyboardButton("Stop", callback_data=message_id)]]
  )

  text = ""
  if is_group:
    text = R.get_string_resource("transcription_text", lang) + "\n"
  success = False

  for speech in audiotools.transcribe(path, api_key):
    logger.debug("Thread %d running: %r", message_id, TranscriberBot.get().thread_running(message_id))
    if TranscriberBot.get().thread_running(message_id) is False:
      TranscriberBot.get().del_thread(message_id)
      return

    retry = True
    retry_num = 0

    while retry and TranscriberBot.get().thread_running(message_id):
      try:
        if len(text + " " + speech) >= 9999999999:
          text = R.get_string_resource("transcription_continues", lang) + "\n"
          message = bot.send_message(
            chat_id=chat_id,
            text=text + " " + speech + " <b>[...]</b>",
            reply_to_message_id=message.message_id,
            parse_mode="html",
            is_group=is_group,
            reply_markup=keyboard
          ).result()
        else:
          message = bot.edit_message_text(
            text=text + " " + speech + " <b>[...]</b>",
            chat_id=chat_id,
            message_id=message.message_id,
            parse_mode="html",
            is_group=is_group,
            reply_markup=keyboard
          ).result()

        text += " " + speech
        retry = False
        success = True

      except telegram.error.TimedOut as t:
        logger.error("Timeout error %s", traceback.format_exc())
        retry_num += 1
        if retry_num >= 3:
          retry = False

      except telegram.error.RetryAfter as r:
        logger.warning("Retrying after %d", r.retry_after)
        time.sleep(r.retry_after)

      except telegram.error.TelegramError as te:
        logger.error("Telegram error %s", traceback.format_exc())
        retry = False

      except Exception as e:
        logger.error("Exception %s", traceback.format_exc())
        retry = False

  retry = True
  retry_num = 0
  while retry and TranscriberBot.get().thread_running(message_id):
    try:
      if success:
        bot.edit_message_text(
          text=text,
          chat_id=chat_id,
          message_id=message.message_id,
          parse_mode="html",
          is_group=is_group
        )
      else:
        bot.edit_message_text(
          R.get_string_resource("transcription_failed", lang),
          chat_id=chat_id,
          message_id=message.message_id,
          parse_mode="html",
          is_group=is_group
        )
      retry = False
    except telegram.error.TimedOut as t:
      logger.error("Timeout error %s", traceback.format_exc())
      retry_num += 1
      if retry_num >= 3:
        retry = False

    except telegram.error.RetryAfter as r:
      logger.warning("Retrying after %d", r.retry_after)
      time.sleep(r.retry_after)

    except telegram.error.TelegramError as te:
      logger.error("Telegram error %s", traceback.format_exc())
      retry = False

    except Exception as e:
      logger.error("Exception %s", traceback.format_exc())
      retry = False

  TranscriberBot.get().del_thread(message_id)

def process_media_voice(bot, update, media, name):
  chat_id = get_chat_id(update)
  file_id = media.file_id
  file_path = os.path.join(config.get_config_prop("app")["media_path"], file_id)
  file = bot.get_file(file_id)
  file.download(file_path)

  try:
    transcribe_audio_file(bot, update, file_path)
  except Exception as e:
    logger.error("Exception handling %s from %d: %s", name, chat_id, traceback.format_exc())
  finally:
    os.remove(file_path)

@message(Filters.voice)
def voice(bot, update):
  chat_id = get_chat_id(update)
  voice_enabled = TBDB.get_chat_voice_enabled(chat_id)
  if voice_enabled == 0:
    return

  message = update.message or update.channel_post
  v = message.voice

  if voice_enabled == 2:
    pass
  else:
    TranscriberBot.get().voice_thread_pool.submit(
      process_media_voice, bot, update, v, "voice"
    )

@message(Filters.audio)
def audio(bot, update):
  chat_id = get_chat_id(update)
  voice_enabled = TBDB.get_chat_voice_enabled(chat_id)

  if voice_enabled == 0:
    return

  message = update.message or update.channel_post
  a = message.audio

  if voice_enabled == 2:
    pass
  else:
    TranscriberBot.get().voice_thread_pool.submit(
      process_media_voice, bot, update, a, "audio"
    )


@message(Filters.status_update.new_chat_members)
def new_chat_member(bot, update):
  message = update.message or update.channel_post

  if bot.get_me() in message.new_chat_members:
    welcome_message(bot, update)

@message(Filters.status_update.left_chat_member)
def left_chat_member(bot, update):
  chat_id = get_chat_id(update)
  message = update.message or update.channel_post

  if message.left_chat_member.id == bot.get_me().id:
    logger.info('Marking chat {} as inactive'.format(chat_id))
    bot.active_chats_cache[chat_id] = 0
    TBDB.set_chat_active(chat_id, bot.active_chats_cache[chat_id])
