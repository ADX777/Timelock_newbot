import os
import uuid
import sqlite3
import asyncio
import logging
import hmac
import hashlib
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import telegram
from retry import retry
from moralis import evm_api, sol_api  # Sử dụng Moralis cho Solana/IPFS

# Thiết lập logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["http://localhost:3000", "YOUR_FRONTEND_URL"]}})

# Env vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")  # Đăng ký tại moralis.io
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
USDT_WALLET = os.getenv("USDT_WALLET")  # Ví Solana nhận USDT
PORT = int(os.getenv("PORT", 5000))

bot = telegram.Bot(token=BOT_TOKEN)

# SQLite DB
conn = sqlite3.connect('orders.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, status TEXT, amount REAL, note TEXT, coin TEXT, target_price TEXT, unlock_time TEXT, encrypted_payload TEXT, tx_hash TEXT, ipfs_hash TEXT)')
conn.commit()

# Event loop
loop = asyncio.get_event_loop()

@app.route('/')
def home():
    return '✅ Bot is running!'

@app.route('/create-invoice', methods=['POST'])  # Bước 1 & 2: Tạo invoice USDT Solana và QR
def create_invoice():
    try:
        data = request.json
        amount = float(data.get("amount"))
        note = data.get("note")
        coin = data.get("coin")
        target_price = data.get("targetPrice")
        unlock_time = data.get("unlockTime")
        order_id = str(uuid.uuid4())[:8]

        # Mã hóa note (fake AES, thay bằng thực từ frontend)
        encrypted_payload = f"ENC[{note}|{coin}|{target_price}|{unlock_time}]"

        # Tạo invoice NowPayments cho USDT trên Solana
        import requests
        headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
        payload = {
            "price_amount": amount,
            "price_currency": "usdt",
            "pay_currency": "usdt",  # USDT trên Solana (USDT-SPL)
            "pay_chain": "solana",  # Chỉ định mạng Solana
            "order_id": order_id,
            "order_description": "Timelock Encryption",
            "ipn_callback_url": "https://timelocknewbot-production.up.railway.app/webhook"
        }
        response = requests.post("https://api.nowpayments.io/v1/invoice", json=payload, headers=headers)
        response.raise_for_status()
        invoice_data = response.json()

        # Lưu order vào DB
        cursor.execute('INSERT INTO orders (order_id, status, amount, note, coin, target_price, unlock_time, encrypted_payload) VALUES (?, "pending", ?, ?, ?, ?, ?, ?)',
                       (order_id, amount, note, coin, target_price, unlock_time, encrypted_payload))
        conn.commit()

        # Notify Telegram
        message = f"🔐 Đơn mới ID: {order_id}\n🌟 Coin: {coin}\n💰 Giá: {target_price}\n⏰ Mở khóa: {unlock_time}\n💵 Thanh toán: {amount} USDT Solana"
        bot.send_message(chat_id=CHANNEL_ID, text=message)

        return jsonify({
            "status": "ok",
            "order_id": order_id,
            "qrCode": f"{invoice_data['invoice_url']}?qr=1",  # QR từ NowPayments
            "invoiceUrl": invoice_data["invoice_url"]
        })
    except Exception as e:
        logging.error("❌ Lỗi /create-invoice: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route('/webhook', methods=['POST'])  # Bước 3: Ghi blockchain sau thanh toán
def webhook():
    try:
        signature = request.headers.get('x-nowpayments-sig')
        payload_bytes = request.get_data()
        computed_hmac = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), payload_bytes, hashlib.sha512).hexdigest()

        if signature != computed_hmac:
            return jsonify({"error": "Invalid signature"}), 400

        data = request.get_json()
        if data.get('payment_status') == 'finished':
            order_id = data.get('order_id')
            cursor.execute('SELECT amount, note, coin, target_price, unlock_time, encrypted_payload FROM orders WHERE order_id=?', (order_id,))
            order = cursor.fetchone()
            if not order:
                return jsonify({"error": "Order not found"}), 404

            amount, note, coin, target_price, unlock_time, encrypted_payload = order

            # Ghi lên IPFS qua Moralis
            ipfs_params = {
                "path": f"timelock_{order_id}.json",
                "content": base64.b64encode(encrypted_payload.encode()).decode()
            }
            ipfs_result = evm_api.ipfs.upload_folder(api_key=MORALIS_API_KEY, params=ipfs_params)
            ipfs_hash = ipfs_result[0]["path"]

            # Ghi tx lên Solana qua Moralis (transfer với data)
            tx_params = {
                "network": "mainnet",  # Hoặc "devnet" để test
                "sender_address": USDT_WALLET,  # Ví Solana của bạn
                "receiver_address": USDT_WALLET,  # Self-transfer để ghi data
                "transaction_type": "transfer",
                "asset": {
                    "token": {
                        "address": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT trên Solana
                        "amount": "0"
                    }
                },
                "memo": ipfs_hash  # Ghi hash vào memo
            }
            tx_result = sol_api.wallet.transfer(api_key=MORALIS_API_KEY, params=tx_params)
            tx_hash = tx_result["transaction_hash"]

            # Cập nhật DB
            cursor.execute('UPDATE orders SET status="paid", tx_hash=?, ipfs_hash=? WHERE order_id=?', (tx_hash, ipfs_hash, order_id))
            conn.commit()

            # Notify Telegram
            bot.send_message(chat_id=CHANNEL_ID, text=f"✅ Đơn {order_id} thanh toán! Tx: {tx_hash}\nIPFS: {ipfs_hash}")

            return jsonify({"message": "OK"}), 200
    except Exception as e:
        logging.error("❌ Lỗi /webhook: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route('/check-status', methods=['GET'])  # Bước 4: Xuất hash + mã hóa
def check_status():
    order_id = request.args.get('order_id')
    if not order_id:
        return jsonify({'error': 'Missing order_id'}), 400
    cursor.execute('SELECT status, encrypted_payload, tx_hash, ipfs_hash FROM orders WHERE order_id=?', (order_id,))
    result = cursor.fetchone()
    if result:
        status, encrypted_payload, tx_hash, ipfs_hash = result
        return jsonify({
            'status': status,
            'encrypted_payload': encrypted_payload,
            'tx_hash': tx_hash,
            'ipfs_hash': ipfs_hash
        })
    return jsonify({'status': 'not_found'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
