import os
import telegram
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = telegram.Bot(token=BOT_TOKEN)

@app.route('/')
def home():
    return '✅ Bot is running!'

@app.route('/notify', methods=['POST'])
def notify():
    try:
        data = request.json

        # Đọc từng trường và tạo tin nhắn
        coin = data.get('coin')
        target_price = data.get('targetPrice')
        unlock_time = data.get('unlockTime')
        days_locked = data.get('daysLocked')
        current_price = data.get('currentPrice')
        amount = data.get('amountToPay')

        message = (
            f"🔐 Coin: {coin}\n"
            f"💰 Giá kỳ vọng: {target_price}\n"
            f"📈 Giá hiện tại: {current_price}\n"
            f"⏰ Mở khóa: {unlock_time}\n"
            f"🔒 Số ngày khóa: {days_locked}\n"
            f"💵 Thanh toán: {amount} USDT"
        )

        bot.send_message(chat_id=CHANNEL_ID, text=message)
        return '✅ Đã gửi Telegram!'
    except Exception as e:
        print("❌ Lỗi /notify:", e)
        return f"Lỗi: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Railway cần dòng này
    app.run(host="0.0.0.0", port=port)
