import os
import telegram
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = telegram.Bot(token=BOT_TOKEN)

@app.route('/')
def home():
    return 'Bot is running!'

@app.route('/notify', methods=['POST'])
def notify():
    data = request.json
    msg = f"🔐 Coin: {data['coin']}\n⏰ Mở khóa: {data['unlockTime']}\n💰 Giá kỳ vọng: {data['targetPrice']}"
    bot.send_message(chat_id=CHANNEL_ID, text=msg)
    return 'ok'

if __name__ == "__main__":
    app.run()
