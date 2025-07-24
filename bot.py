import os
import uuid
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
import telegram
from bscscan import BscScan
from retry import retry
import logging
import hmac
import hashlib
import requests

# Thiết lập logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

# Biến môi trường
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BSC_API_KEY = os.getenv("BSC_API_KEY")
USDT_WALLET = os.getenv("USDT_WALLET")
TATUM_API_KEY = os.getenv("TATUM_API_KEY")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")

bot = telegram.Bot(token=BOT_TOKEN)

# SQLite
conn = sqlite3.connect('orders.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    status TEXT,
    amount REAL,
    note TEXT,
    coin TEXT,
    target_price TEXT,
    unlock_time TEXT,
    encrypted_payload TEXT,
    tx_hash TEXT,
    ipfs_hash TEXT
)
""")
conn.commit()

@app.route('/')
def home():
    return '✅ Bot is running!'

@app.route('/check-status')
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
