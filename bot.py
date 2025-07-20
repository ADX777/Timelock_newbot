import os
import uuid  # Để tạo order_id random nếu cần
import sqlite3
import asyncio
from flask import Flask, request, jsonify
from flask_cors import CORS
import telegram
from bscscan import BscScan  # Cài bằng pip install bscscan-python
import logging

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logging.info("Bot module loaded, starting...")

# Khởi tạo Flask app và bật CORS
app = Flask(__name__)
CORS(app)

# Lấy vars từ env (Railway)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BSC_API_KEY = os.getenv("BSC_API_KEY")
USDT_WALLET = os.getenv("USDT_WALLET")
PORT = int(os.getenv("PORT"))  # Không default, Railway cung cấp $PORT

bot = telegram.Bot(token=BOT_TOKEN)

# DB SQLite để lưu status đơn hàng
conn = sqlite3.connect('orders.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, status TEXT, amount REAL)')
conn.commit()

# Event loop cho async tasks
loop = asyncio.get_event_loop()

@app.route('/')
def home():
    return '✅ Bot is running!'

@app.route('/notify', methods=['POST'])
def notify():
    try:
        data = request.json
        logging.info("📥 Nhận dữ liệu từ web: %s", data)

        # Đọc từng trường từ JSON
        coin = data.get("coin")
        target_price = data.get("targetPrice")
        unlock_time = data.get("unlockTime")
        days_locked = data.get("daysLocked")
        amount = data.get("amountToPay")
        current_price = data.get("currentPrice")

        # Tạo order_id nếu web chưa gửi
        order_id = data.get("orderId") or str(uuid.uuid4())[:8]  # Random short ID

        # Lưu status pending vào DB
        cursor.execute('INSERT OR REPLACE INTO orders (order_id, status, amount) VALUES (?, "pending", ?)', (order_id, amount))
        conn.commit()

        # Tạo thông điệp gửi về Telegram (cảnh báo)
        message = (
            f"🔐 Đơn mới ID: {order_id}\n"
            f"🌟 Coin: {coin}\n"
            f"💰 Giá kỳ vọng: {target_price}\n"
            f"📈 Giá hiện tại: {current_price}\n"
            f"⏰ Mở khóa: {unlock_time}\n"
            f"🔒 Số ngày khóa: {days_locked}\n"
            f"💵 Thanh toán: {amount} USDT"
        )

        bot.send_message(chat_id=CHANNEL_ID, text=message)

        # Bắt đầu poll check payment async
        loop.create_task(monitor_payment(order_id, amount))

        return jsonify({'status': 'ok', 'order_id': order_id})  # Trả order_id cho web
    except Exception as e:
        logging.error("❌ Lỗi /notify: %s", e)
        return f"❌ Lỗi: {e}", 500

@app.route('/check-status', methods=['GET'])
def check_status():
    order_id = request.args.get('order_id')
    if not order_id:
        return jsonify({'error': 'Missing order_id'}), 400
    cursor.execute('SELECT status FROM orders WHERE order_id=?', (order_id,))
    result = cursor.fetchone()
    if result:
        return jsonify({'status': result[0]})
    return jsonify({'status': 'not_found'}), 404

# Hàm async poll check payment USDT trên BSC
async def monitor_payment(order_id, amount):
    try:
        logging.info("Bắt đầu monitor_payment cho đơn %s", order_id)
        async with BscScan(BSC_API_KEY) as bsc:
            while True:
                transfers = await bsc.get_bep20_token_transfer_events_by_address(
                    address=USDT_WALLET,
                    contract_address='0x55d398326f99059fF775485246999027B3197955',  # USDT contract BSC
                    sort='desc'
                )
                for tx in transfers[:10]:  # Chỉ check gần nhất để nhanh
                    tx_amount = float(tx['value']) / 10**18  # USDT có 18 decimals
                    if tx_amount >= amount:
                        # Update status paid
                        cursor.execute('UPDATE orders SET status="paid" WHERE order_id=?', (order_id,))
                        conn.commit()
                        # Gửi xác nhận Telegram cho admin
                        bot.send_message(chat_id=CHANNEL_ID, text=f"✅ Đơn {order_id} đã thanh toán! Tx hash: {tx['hash']}\nSố tiền: {tx_amount} USDT")
                        return  # Dừng poll
                await asyncio.sleep(60)  # Check mỗi 1 phút
    except Exception as e:
        logging.error("❌ Lỗi monitor_payment cho %s: %s", order_id, e)

# Không dùng app.run() vì dùng gunicorn ở production
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=PORT)
