# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_socketio import SocketIO, join_room, leave_room, emit
from pymongo import MongoClient
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
import uuid
import datetime
import secrets
from dotenv import load_dotenv
from bson.objectid import ObjectId

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(16))
socketio = SocketIO(app)

# MongoDB Atlas connection
client = MongoClient(os.getenv('MONGODB_URI'))
db = client['DocKaro']
rooms_collection = db['rooms']
messages_collection = db['messages']
files_collection = db['files']

# Cloudinary configuration
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    username = request.form.get('username')
    if not username:
        flash('Username is required.')
        return redirect(url_for('index'))
    
    room_id = str(uuid.uuid4())[:8]  # Generate a unique room ID
    
    # Create room in database
    rooms_collection.insert_one({
        'room_id': room_id,
        'created_at': datetime.datetime.now(),
        'created_by': username,
        'members': [username],
        'max_members': 2
    })
    
    session['username'] = username
    session['room'] = room_id
    
    return redirect(url_for('room', room_id=room_id))

@app.route('/join_room', methods=['POST'])
def join_existing_room():
    username = request.form.get('username')
    room_id = request.form.get('room_id')
    
    if not username or not room_id:
        flash('Both username and room ID are required.')
        return redirect(url_for('index'))
    
    # Check if room exists
    room = rooms_collection.find_one({'room_id': room_id})
    if not room:
        flash('Room does not exist.')
        return redirect(url_for('index'))
    
    # Check if room is full (max 2 people)
    if len(room['members']) >= 2 and username not in room['members']:
        flash('Room is full.')
        return redirect(url_for('index'))
    
    # Add user to room if not already a member
    if username not in room['members']:
        rooms_collection.update_one(
            {'room_id': room_id},
            {'$push': {'members': username}}
        )
    
    session['username'] = username
    session['room'] = room_id
    
    return redirect(url_for('room', room_id=room_id))

@app.route('/room/<room_id>')
def room(room_id):
    if 'username' not in session or 'room' not in session:
        return redirect(url_for('index'))
    
    # Check if user is in the room
    room = rooms_collection.find_one({'room_id': room_id})
    if not room or session['username'] not in room['members']:
        return redirect(url_for('index'))
    
    # Get room messages
    messages = list(messages_collection.find({'room_id': room_id}).sort('created_at', 1))
    
    # Get room files
    files = list(files_collection.find({'room_id': room_id}).sort('uploaded_at', -1))
    
    return render_template(
        'room.html', 
        username=session['username'], 
        room=room_id, 
        messages=messages, 
        files=files
    )

@app.route('/upload_file', methods=['POST'])
def upload_file():
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    room_id = session['room']
    username = session['username']
    
    # Check if file is in request
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    
    # Check if file is empty
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Upload file to Cloudinary
    upload_result = cloudinary.uploader.upload(file)
    
    # Save file info to database
    file_id = str(ObjectId())
    file_info = {
        '_id': file_id,
        'room_id': room_id,
        'filename': file.filename,
        'cloudinary_url': upload_result['secure_url'],
        'cloudinary_public_id': upload_result['public_id'],
        'uploaded_by': username,
        'uploaded_at': datetime.datetime.now()
    }
    
    files_collection.insert_one(file_info)
    
    # Notify room members about new file
    socketio.emit('new_file', {
        'username': username,
        'filename': file.filename,
        'url': upload_result['secure_url'],
        'file_id': file_id,
        'uploaded_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }, room=room_id)
    
    return jsonify({
        'success': True, 
        'file_url': upload_result['secure_url'],
        'file_id': file_id
    })

@app.route('/delete_file/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Get file info
    file_info = files_collection.find_one({'_id': file_id})
    if not file_info:
        return jsonify({'error': 'File not found'}), 404
    
    # Check if user is allowed to delete the file
    if file_info['uploaded_by'] != session['username']:
        return jsonify({'error': 'Not allowed to delete this file'}), 403
    
    # Delete from Cloudinary
    cloudinary.uploader.destroy(file_info['cloudinary_public_id'])
    
    # Delete from database
    files_collection.delete_one({'_id': file_id})
    
    # Notify room members about deleted file
    socketio.emit('file_deleted', {'file_id': file_id}, room=session['room'])
    
    return jsonify({'success': True})

@app.route('/leave_room', methods=['POST'])
def leave_existing_room():
    if 'username' not in session or 'room' not in session:
        return redirect(url_for('index'))
    
    room_id = session['room']
    username = session['username']
    
    # Remove user from room
    rooms_collection.update_one(
        {'room_id': room_id},
        {'$pull': {'members': username}}
    )
    
    # Delete room if empty
    empty_room = rooms_collection.find_one({'room_id': room_id, 'members': []})
    if empty_room:
        rooms_collection.delete_one({'room_id': room_id})
    
    # Clear session
    session.pop('username', None)
    session.pop('room', None)
    
    return redirect(url_for('index'))

# SocketIO event handlers
@socketio.on('join')
def on_join(data):
    username = session.get('username')
    room = data['room']
    
    if username and room:
        join_room(room)
        emit('status', {'msg': f'{username} has entered the room.'}, room=room)

@socketio.on('leave')
def on_leave(data):
    username = session.get('username')
    room = data['room']
    
    if username and room:
        leave_room(room)
        emit('status', {'msg': f'{username} has left the room.'}, room=room)

@socketio.on('message')
def on_message(data):
    username = session.get('username')
    room = session.get('room')
    
    if not username or not room:
        return
    
    message = data['message']
    timestamp = datetime.datetime.now()
    
    # Save message to database
    message_id = messages_collection.insert_one({
        'room_id': room,
        'username': username,
        'message': message,
        'created_at': timestamp
    }).inserted_id
    
    # Broadcast message to room
    emit('message', {
        'username': username,
        'message': message,
        'created_at': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        'message_id': str(message_id)
    }, room=room)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))