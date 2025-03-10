# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
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

# MongoDB Atlas connection
client = MongoClient(os.getenv('MONGODB_URI'))
db = client['room_sharing_app']
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
    
    # Add join message
    messages_collection.insert_one({
        'room_id': room_id,
        'type': 'system',
        'content': f'{username} has created the room.',
        'created_at': datetime.datetime.now()
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
        
        # Add join message
        messages_collection.insert_one({
            'room_id': room_id,
            'type': 'system',
            'content': f'{username} has joined the room.',
            'created_at': datetime.datetime.now()
        })
    
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
    
    return render_template('room.html', 
                          username=session['username'], 
                          room=room_id,
                          room_data=room)

@app.route('/api/messages', methods=['GET'])
def get_messages():
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    room_id = session.get('room')
    
    # Get last message ID for pagination
    last_id = request.args.get('last_id', None)
    limit = int(request.args.get('limit', 50))
    
    query = {'room_id': room_id}
    if last_id:
        last_message = messages_collection.find_one({'_id': ObjectId(last_id)})
        if last_message:
            query['created_at'] = {'$gt': last_message['created_at']}
    
    # Get messages
    messages = list(messages_collection.find(query)
                    .sort('created_at', 1)
                    .limit(limit))
    
    # Convert ObjectId to string for JSON serialization
    for message in messages:
        message['_id'] = str(message['_id'])
        message['created_at'] = message['created_at'].isoformat()
    
    return jsonify(messages)

@app.route('/api/messages', methods=['POST'])
def send_message():
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    room_id = session.get('room')
    username = session.get('username')
    content = request.json.get('content')
    
    if not content:
        return jsonify({'error': 'Message content is required'}), 400
    
    # Save message to database
    message = {
        'room_id': room_id,
        'type': 'user',
        'username': username,
        'content': content,
        'created_at': datetime.datetime.now()
    }
    
    result = messages_collection.insert_one(message)
    message['_id'] = str(result.inserted_id)
    message['created_at'] = message['created_at'].isoformat()
    
    return jsonify(message)

@app.route('/api/files', methods=['GET'])
def get_files():
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    room_id = session.get('room')
    
    # Get files
    files = list(files_collection.find({'room_id': room_id})
                .sort('uploaded_at', -1))
    
    # Convert ObjectId to string for JSON serialization
    for file in files:
        file['_id'] = str(file['_id'])
        file['uploaded_at'] = file['uploaded_at'].isoformat()
    
    return jsonify(files)

@app.route('/api/files', methods=['POST'])
def upload_file():
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    room_id = session.get('room')
    username = session.get('username')
    
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
    file_info = {
        'room_id': room_id,
        'filename': file.filename,
        'cloudinary_url': upload_result['secure_url'],
        'cloudinary_public_id': upload_result['public_id'],
        'uploaded_by': username,
        'uploaded_at': datetime.datetime.now()
    }
    
    result = files_collection.insert_one(file_info)
    file_info['_id'] = str(result.inserted_id)
    file_info['uploaded_at'] = file_info['uploaded_at'].isoformat()
    
    # Add system message about file upload
    message = {
        'room_id': room_id,
        'type': 'system',
        'content': f'{username} uploaded a file: {file.filename}',
        'file_id': str(result.inserted_id),
        'created_at': datetime.datetime.now()
    }
    messages_collection.insert_one(message)
    
    return jsonify(file_info)

@app.route('/api/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    if 'username' not in session or 'room' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    room_id = session.get('room')
    username = session.get('username')
    
    # Get file info
    file_info = files_collection.find_one({'_id': ObjectId(file_id), 'room_id': room_id})
    if not file_info:
        return jsonify({'error': 'File not found'}), 404
    
    # Check if user is allowed to delete the file
    if file_info['uploaded_by'] != username:
        return jsonify({'error': 'Not allowed to delete this file'}), 403
    
    # Delete from Cloudinary
    cloudinary.uploader.destroy(file_info['cloudinary_public_id'])
    
    # Delete from database
    files_collection.delete_one({'_id': ObjectId(file_id)})
    
    # Add system message about file deletion
    message = {
        'room_id': room_id,
        'type': 'system',
        'content': f'{username} deleted a file: {file_info["filename"]}',
        'created_at': datetime.datetime.now()
    }
    messages_collection.insert_one(message)
    
    return jsonify({'success': True})

@app.route('/leave_room', methods=['POST'])
def leave_room():
    if 'username' not in session or 'room' not in session:
        return redirect(url_for('index'))
    
    room_id = session.get('room')
    username = session.get('username')
    
    # Add leave message
    messages_collection.insert_one({
        'room_id': room_id,
        'type': 'system',
        'content': f'{username} has left the room.',
        'created_at': datetime.datetime.now()
    })
    
    # Remove user from room
    rooms_collection.update_one(
        {'room_id': room_id},
        {'$pull': {'members': username}}
    )
    
    # Delete room if empty
    room = rooms_collection.find_one({'room_id': room_id})
    if room and len(room['members']) == 0:
        rooms_collection.delete_one({'room_id': room_id})
    
    # Clear session
    session.pop('username', None)
    session.pop('room', None)
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))