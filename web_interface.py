import os
import sys
import json
import time
import threading
import subprocess
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject, pyqtSignal, QThread

# Import MacAttack class
from MacAttack import MacAttack

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variable to store the MacAttack instance
mac_attack = None

class WebInterface(QObject):
    update_signal = pyqtSignal(str, dict)
    
    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        self.mac_attack = MacAttack()
        self.update_signal.connect(self.handle_update)
        
        # Connect MacAttack signals to our handlers
        self.mac_attack.update_mac_label_signal.connect(self.on_mac_update)
        self.mac_attack.update_hits_label_signal.connect(self.on_hits_update)
        self.mac_attack.update_output_text_signal.connect(self.on_output_update)
        self.mac_attack.update_error_text_signal.connect(self.on_error_update)
    
    def on_mac_update(self, text):
        self.update_signal.emit('mac_update', {'text': text})
    
    def on_hits_update(self, text):
        self.update_signal.emit('hits_update', {'text': text})
    
    def on_output_update(self, text):
        self.update_signal.emit('output_update', {'text': text})
    
    def on_error_update(self, text):
        self.update_signal.emit('error_update', {'text': text})
    
    def handle_update(self, update_type, data):
        socketio.emit(update_type, data)
    
    def run(self):
        self.mac_attack.show()
        return self.app.exec_()

# Global variable to store the web interface
web_interface = None

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('start_attack')
def handle_start_attack(data):
    if web_interface and web_interface.mac_attack:
        web_interface.mac_attack.BigMacAttack()
        return {'status': 'started'}
    return {'status': 'error', 'message': 'MacAttack not initialized'}

@socketio.on('stop_attack')
def handle_stop_attack():
    if web_interface and web_interface.mac_attack:
        web_interface.mac_attack.GiveUp()
        return {'status': 'stopped'}
    return {'status': 'error', 'message': 'MacAttack not initialized'}

def start_flask():
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False)

def start_qt():
    global web_interface
    web_interface = WebInterface()
    web_interface.run()

if __name__ == '__main__':
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=start_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start Qt in the main thread
    start_qt()
