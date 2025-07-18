import os
from flask import Flask, request
from flask_cors import CORS
import telegram

# Khởi tạo Flask app và bật CORS
app = Flask(__name__)
CORS(app)

# Lấy token và kênh Telegram từ biến môi trường
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
        print("📥 Nhận dữ liệu từ web:", data)

        # Đọc từng trường từ JSON
        coin = data.get("coin")
        target_price = data.get("targetPrice")
        unlock_time = data.get("unlockTime")
        days_locked = data.get("daysLocked")
        amount = data.get("amountToPay")
        current_price = data.get("currentPrice")

        # Tạo thông điệp gửi về Telegram
        message = (
            f"🔐 Coin: {coin}\n"
            f"💰 Giá kỳ vọng: {target_price}\n"
            f"📈 Giá hiện tại: {current_price}\n"
            f"⏰ Mở khóa: {unlock_time}\n"
            f"🔒 Số ngày khóa: {days_locked}\n"
            f"💵 Thanh toán: {amount} USDT"
        )

        bot.send_message(chat_id=CHANNEL_ID, text=message)
        return '✅ Gửi thành công!'
    except Exception as e:
        print("❌ Lỗi /notify:", e)
        return f"❌ Lỗi: {e}", 500
