import os
import uuid
import sqlite3
import asyncio
from flask import Flask, request, jsonify
from flask_cors import CORS
import telegram
from bscscan import BscScan
from retry import retry
import logging
import hmac
import hashlib
import requests  # Thêm để gọi NowPayments và Tatum

# Thiết lập logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["http://localhost:3000", "YOUR_FRONTEND_URL"]}})

# Env vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BSC_API_KEY = os.getenv("BSC_API_KEY")
USDT_WALLET = os.getenv("USDT_WALLET")
TATUM_API_KEY = os.getenv("TATUM_API_KEY")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
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

@app.route('/create-invoice', methods=['POST'])  # Bước 1 & 2: Tạo invoice USDT-BSC và QR
def create_invoice():
    try:
        data = request.json
        amount = float(data.get("amount"))
        note = data.get("note")
        coin = data.get("coin")
        target_price = data.get("targetPrice")
        unlock_time = data.get("unlockTime")
        order_id = str(uuid.uuid4())[:8]

        # Mã hóa tạm thời (thay bằng AES từ frontend)
        encrypted_payload = f"ENC[{note}|{coin}|{target_price}|{unlock_time}]"

        # Tạo invoice NowPayments cho USDT trên BSC
        headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
        payload = {
            "price_amount": amount,
            "price_currency": "usdt",
            "pay_currency": "usdtbsc",  # USDT trên BSC
            "order_id": order_id,
            "order_description": "Timelock Encryption",
            "ipn_callback_url": "https://timelocknewbot-production.up.railway.app/webhook"
        }
        response = requests.post("https://api.nowpayments.io/v1/invoice", json=payload, headers=headers)
        response.raise_for_status()
        invoice_data = response.json()

        # Lưu vào DB
        cursor.execute('INSERT INTO orders (order_id, status, amount, note, coin, target_price, unlock_time, encrypted_payload) VALUES (?, "pending", ?, ?, ?, ?, ?, ?)',
                       (order_id, amount, note, coin, target_price, unlock_time, encrypted_payload))
        conn.commit()

        # Notify Telegram
        message = f"🔐 Đơn mới ID: {order_id}\n🌟 Coin: {coin}\n💰 Giá: {target_price}\n⏰ Mở khóa: {unlock_time}\n💵 Thanh toán: {amount} USDT-BSC"
        bot.send_message(chat_id=CHANNEL_ID, text=message)

        # Start monitor fallback (nếu webhook fail)
        loop.create_task(monitor_payment(order_id, amount))

        return jsonify({
            "status": "ok",
            "order_id": order_id,
            "qrCode": f"{invoice_data['invoice_url']}?qr=1",
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

            # Ghi lên IPFS qua Tatum
            ipfs_headers = {"x-api-key": TATUM_API_KEY, "Content-Type": "multipart/form-data"}
            ipfs_form = {'file': ( 'timelock.json', encrypted_payload.encode()) }
            ipfs_response = requests.post("https://api.tatum.io/v3/ipfs", files=ipfs_form, headers=ipfs_headers)
            ipfs_response.raise_for_status()
            ipfs_hash = ipfs_response.json()["ipfsHash"]

            # Ghi tx lên BSC qua Tatum
            tx_headers = {"x-api-key": TATUM_API_KEY, "Content-Type": "application/json"}
            tx_payload = {
                "chain": "bsc",
                "fromPrivateKey": "YOUR_BSC_WALLET_PRIVATE_KEY",  # Thêm env var mới nếu cần
                "to": USDT_WALLET,  # Self-transfer
                "value": "0",
                "data": ipfs_hash.encode().hex()  # Ghi hash vào data
            }
            tx_response = requests.post("https://api.tatum.io/v3/blockchain/transaction", json=tx_payload, headers=tx_headers)
            tx_response.raise_for_status()
            tx_hash = tx_response.json()["txId"]

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
            'tx_hash': tx_hash or '',
            'ipfs_hash': ipfs_hash or ''
        })
    return jsonify({'status': 'not_found'}), 404

@retry(tries=3, delay=2)
async def monitor_payment(order_id, amount):  # Fallback monitor BSC
    try:
        async with BscScan(BSC_API_KEY) as bsc:
            while True:
                transfers = await bsc.get_bep20_token_transfer_events_by_address(
                    address=USDT_WALLET,
                    contract_address='0x55d398326f99059fF775485246999027B3197955',
                    sort='
