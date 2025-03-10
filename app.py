# app.py
import os
import secrets
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, join_room, leave_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(16)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['DATA_FOLDER'] = 'data'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
socketio = SocketIO(app)

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DATA_FOLDER'], exist_ok=True)

# Helper functions for persistent storage
def save_room_data(room_id, room_data):
    with open(os.path.join(app.config['DATA_FOLDER'], f"{room_id}.json"), 'w') as f:
        json.dump(room_data, f)

def load_room_data(room_id):
    file_path = os.path.join(app.config['DATA_FOLDER'], f"{room_id}.json")
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return None

def save_room_files(room_id, files_data):
    with open(os.path.join(app.config['DATA_FOLDER'], f"{room_id}_files.json"), 'w') as f:
        json.dump(files_data, f)

def load_room_files(room_id):
    file_path = os.path.join(app.config['DATA_FOLDER'], f"{room_id}_files.json")
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    room_id = secrets.token_urlsafe(8)
    username = request.form.get('username', 'Anonymous')
    
    # Initialize room with creator as first member
    room_data = {
        'members': [username],
        'messages': [],
        'created_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Save room data to disk
    save_room_data(room_id, room_data)
    save_room_files(room_id, [])
    
    session['username'] = username
    session['room_id'] = room_id
    
    flash(f'Room created successfully! Share this room ID: {room_id}')
    return redirect(url_for('room', room_id=room_id))

@app.route('/join_room', methods=['POST'])
def join_existing_room():
    room_id = request.form.get('room_id')
    username = request.form.get('username', 'Anonymous')
    
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if not room_id or not room_data:
        flash('Invalid room ID. Please try again.')
        return redirect(url_for('index'))
    
    # Check if room is full (max 2 people)
    if len(room_data['members']) >= 2:
        flash('Room is full. Please try another room.')
        return redirect(url_for('index'))
    
    # Add user to room
    if username not in room_data['members']:
        room_data['members'].append(username)
        save_room_data(room_id, room_data)
    
    session['username'] = username
    session['room_id'] = room_id
    
    return redirect(url_for('room', room_id=room_id))

@app.route('/room/<room_id>')
def room(room_id):
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if not room_data:
        flash('Room not found.')
        return redirect(url_for('index'))
    
    username = session.get('username')
    if not username or username not in room_data['members']:
        flash('You are not a member of this room.')
        return redirect(url_for('index'))
    
    # Load files data from disk
    files_data = load_room_files(room_id)
    
    return render_template(
        'room.html', 
        room_id=room_id, 
        username=username,
        messages=room_data['messages'],
        files=files_data,
        members=room_data['members']
    )

@app.route('/upload/<room_id>', methods=['POST'])
def upload_file(room_id):
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if not room_data:
        return redirect(url_for('index'))
    
    username = session.get('username')
    if not username or username not in room_data['members']:
        return redirect(url_for('index'))
    
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('room', room_id=room_id))
    
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('room', room_id=room_id))
    
    if file:
        filename = secure_filename(file.filename)
        # Add timestamp to filename to avoid collisions
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{timestamp}_{filename}"
        
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        file_info = {
            'filename': filename,
            'original_name': secure_filename(file.filename),
            'uploaded_by': username,
            'uploaded_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'size': os.path.getsize(file_path)
        }
        
        # Load files data, add new file, and save
        files_data = load_room_files(room_id)
        files_data.append(file_info)
        save_room_files(room_id, files_data)
        
        # Notify room members about new file
        socketio.emit('file_uploaded', {
            'file': file_info,
            'uploader': username
        }, to=room_id)
        
        flash('File uploaded successfully!')
    
    return redirect(url_for('room', room_id=room_id))

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/leave_room/<room_id>')
def leave_room_route(room_id):
    username = session.get('username')
    
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if room_data and username in room_data['members']:
        room_data['members'].remove(username)
        save_room_data(room_id, room_data)
        
        # Notify others that user left
        socketio.emit('user_left', {
            'username': username
        }, to=room_id)
    
    session.pop('username', None)
    session.pop('room_id', None)
    
    flash('You have left the room.')
    return redirect(url_for('index'))

@socketio.on('join')
def on_join(data):
    username = session.get('username')
    room_id = data.get('room_id')
    
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if not room_id or not room_data or not username:
        return
    
    join_room(room_id)
    emit('user_joined', {'username': username}, to=room_id)

@socketio.on('leave')
def on_leave(data):
    username = session.get('username')
    room_id = data.get('room_id')
    
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if not room_id or not room_data or not username:
        return
    
    leave_room(room_id)

@socketio.on('send_message')
def on_send_message(data):
    username = session.get('username')
    room_id = data.get('room_id')
    message = data.get('message')
    
    # Load room data from disk
    room_data = load_room_data(room_id)
    
    if not room_id or not room_data or not username or not message:
        return
    
    # Save message to room history
    msg_data = {
        'sender': username,
        'content': message,
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    room_data['messages'].append(msg_data)
    save_room_data(room_id, room_data)
    
    # Broadcast message to room
    emit('new_message', msg_data, to=room_id)

if __name__ == '__main__':
    # For production with Render, use the PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)