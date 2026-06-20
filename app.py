# ПАТЧ ДЛЯ GEVENT - ДОЛЖЕН БЫТЬ ПЕРВОЙ СТРОКОЙ
from gevent import monkey
monkey.patch_all()

import os
from datetime import datetime
from flask import Flask, request, redirect, url_for, flash, session, jsonify, render_template_string, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash

# ==========================================
# КОНФИГУРАЦИЯ ПРИЛОЖЕНИЯ
# ==========================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-key-for-youme-12345')

db_url = os.environ.get(
    'DATABASE_URL', 
    "postgresql://avnadmin:AVNS_A094KJpWYOSX9t3_eM6@youme-krossmag.l.aivencloud.com:25520/defaultdb?sslmode=require"
)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,             
    'pool_recycle': 280,         
    'pool_pre_ping': True,       
    'pool_timeout': 20,          
}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*")

# ==========================================
# ВСПОМОГАТЕЛЬНАЯ ЛОГИКА
# ==========================================
def format_bday(bd_str):
    if not bd_str or "." not in bd_str:
        return "Не указана"
    try:
        d, m, y = bd_str.split(".")
        months = {1: "янв.", 2: "февр.", 3: "мар.", 4: "апр.", 5: "мая", 6: "июня", 7: "июля", 8: "авг.", 9: "сент.",
                  10: "окт.", 11: "нояб.", 12: "дек."}
        m_str = months.get(int(m), m)
        return f"{d} {m_str} {y}г."
    except:
        return bd_str

# ==========================================
# МОДЕЛИ БАЗЫ ДАННЫХ
# ==========================================
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=True)
    class_name = db.Column(db.String(20), nullable=True)

    avatar_url = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    about_me = db.Column(db.Text, nullable=True)
    birth_date = db.Column(db.String(20), nullable=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

    show_phone = db.Column(db.Boolean, default=False)
    show_about = db.Column(db.Boolean, default=True)
    show_birth_date = db.Column(db.Boolean, default=False)

    is_admin = db.Column(db.Boolean, default=False)
    promoted_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Contact(db.Model):
    __tablename__ = 'contacts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

class Chat(db.Model):
    __tablename__ = 'chats'
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), default='private')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ChatParticipant(db.Model):
    __tablename__ = 'chat_participants'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=True)
    image_base64 = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    is_deleted = db.Column(db.Boolean, default=False)
    is_edited = db.Column(db.Boolean, default=False)
    original_text = db.Column(db.Text, nullable=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True)
    forwarded_from_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

connected_users = {}

# ==========================================
# HTML ШАБЛОНЫ (Jinja2 + Tailwind + Alpine)
# ==========================================
BASE_HTML_HEAD = """
<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>You`me</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = { darkMode: 'class' }
    </script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #4B5563; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #374151; }
        .admin-badge {
            background-color: #3f2224;
            border: 1px solid #cc3033;
            color: #f76d70;
            padding: 0.1rem 0.4rem;
            border-radius: 0.375rem;
            font-size: 0.7rem;
            font-weight: 600;
            display: inline-block;
            line-height: 1;
        }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 h-[100dvh] w-screen overflow-hidden flex flex-col font-sans fixed inset-0 select-none">
    {% if session.get('original_admin_id') %}
    <div class="bg-red-600 text-white text-center py-2 text-xs md:text-sm font-bold flex justify-center items-center gap-2 md:gap-4 z-50 shadow-lg px-2 flex-shrink-0">
        Внимание: Режим от лица {{ current_user.first_name }}!
        <a href="{{ url_for('revert_impersonate') }}" class="bg-white text-red-600 px-2 py-1 rounded-md hover:bg-gray-200 transition">Вернуться</a>
    </div>
    {% endif %}
"""

LOGIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex items-center justify-center bg-gray-900 px-4" x-data="{ isLogin: true }">
        <div class="bg-gray-800 p-6 md:p-8 rounded-xl shadow-2xl w-full max-w-md border border-gray-700">
            <h1 class="text-3xl font-bold text-center text-blue-500 mb-6 font-serif tracking-widest">You`me</h1>

            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="bg-red-500/20 border border-red-500 text-red-200 p-3 rounded mb-4 text-center text-sm">
                  {% for message in messages %}{{ message }}<br>{% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <form x-show="isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4">
                <input type="hidden" name="action" value="login">
                <div>
                    <input type="text" name="username" placeholder="Логин (@username)" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 md:py-2 rounded transition">Войти</button>
                <p class="text-center text-sm text-gray-400 mt-4">Нет аккаунта? <a href="#" @click.prevent="isLogin = false" class="text-blue-400 hover:underline">Регистрация</a></p>
            </form>

            <form x-show="!isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4" style="display: none;">
                <input type="hidden" name="action" value="register">
                <div>
                    <input type="text" name="username" placeholder="Придумайте логин (только латиница)" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div class="flex flex-col gap-4 md:gap-2">
                    <input type="text" name="first_name" placeholder="Имя" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                    <input type="text" name="last_name" placeholder="Фамилия (необязательно)" class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-green-600 hover:bg-green-700 text-white font-bold py-3 md:py-2 rounded transition">Зарегистрироваться</button>
                <p class="text-center text-sm text-gray-400 mt-4">Уже есть аккаунт? <a href="#" @click.prevent="isLogin = true" class="text-blue-400 hover:underline">Войти</a></p>
            </form>
        </div>
    </div>
</body>
</html>
"""

APP_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex overflow-hidden w-full h-full max-h-full" x-data="messengerApp()">

        <div class="bg-gray-900 border-r border-gray-800 flex flex-col flex-shrink-0 w-full md:w-80 h-full max-h-full"
             :class="currentChat ? 'hidden md:flex' : 'flex'">
             
            <div class="p-4 border-b border-gray-800 flex justify-between items-center flex-shrink-0 relative">
                <div class="flex items-center gap-3">
                    <div @click="openMyProfile()" class="w-10 h-10 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold cursor-pointer overflow-hidden shadow-md hover:ring-2 hover:ring-blue-400 transition">
                        <img x-show="myProfileData.avatar" :src="myProfileData.avatar" class="w-full h-full object-cover">
                        <span x-show="!myProfileData.avatar">{{ current_user.first_name[0] }}</span>
                    </div>
                    <img src="/logo.png" alt="You'Me" class="h-8 object-contain" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                    <div class="text-xl font-bold text-blue-500 tracking-wider" style="display:none;">You`me</div>
                </div>

                <div class="flex gap-2">
                    {% if current_user.is_admin %}
                    <a href="{{ url_for('admin_panel') }}" class="p-1 text-gray-400 hover:text-white" title="Админ Панель">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.427.738-3.2 2.23-2.47z"></path></svg>
                    </a>
                    {% endif %}
                    <a href="{{ url_for('logout') }}" class="p-1 text-gray-400 hover:text-red-500" title="Выйти">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                    </a>
                </div>
            </div>

            <div class="p-3 border-b border-gray-800 flex-shrink-0 relative">
                <input type="text" x-model="searchQuery" @input="searchUsers()" placeholder="Поиск пользователей..." 
                       class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                
                <div x-show="searchResults.length > 0" class="absolute left-0 right-0 mt-2 mx-3 bg-gray-800 border border-gray-700 rounded-lg shadow-2xl z-50 max-h-60 overflow-y-auto" style="display: none;" @click.away="searchResults = []">
                    <template x-for="user in searchResults" :key="user.id">
                        <div @click="startChatWithUser(user)" class="p-3 flex items-center gap-3 hover:bg-gray-700 cursor-pointer border-b border-gray-700/50 last:border-0">
                            <div class="w-9 h-9 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold overflow-hidden flex-shrink-0">
                                <img x-show="user.avatar_url" :src="user.avatar_url" class="w-full h-full object-cover">
                                <span x-show="!user.avatar_url" x-text="user.first_name[0]"></span>
                            </div>
                            <div class="flex-1 min-w-0">
                                <div class="flex items-center gap-1">
                                    <span class="font-semibold text-sm truncate text-white" x-text="user.first_name + ' ' + (user.last_name || '')"></span>
                                    <span x-show="user.is_admin" class="admin-badge">Admin</span>
                                </div>
                                <div class="text-xs text-gray-400 truncate" x-text="'@' + user.username"></div>
                            </div>
                        </div>
                    </template>
                </div>
            </div>

            <div class="flex-1 overflow-y-auto">
                <template x-for="chat in chats" :key="chat.chat_id">
                    <div @click="selectChat(chat)" 
                         class="p-3 flex items-center gap-3 cursor-pointer border-b border-gray-800/60 transition"
                         :class="currentChat && currentChat.chat_id === chat.chat_id ? 'bg-gray-800' : 'hover:bg-gray-800/40'">
                        <div class="w-11 h-11 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold overflow-hidden flex-shrink-0 relative shadow-sm">
                            <img x-show="chat.partner_avatar" :src="chat.partner_avatar" class="w-full h-full object-cover">
                            <span x-show="!chat.partner_avatar" x-text="chat.partner_name[0]"></span>
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="flex justify-between items-baseline mb-0.5">
                                <div class="flex items-center gap-1 min-w-0">
                                    <h3 class="font-bold text-sm truncate text-gray-200" x-text="chat.partner_name"></h3>
                                    <span x-show="chat.partner_is_admin" class="admin-badge">Admin</span>
                                </div>
                                <span class="text-[10px] text-gray-500 flex-shrink-0" x-text="chat.last_message_time"></span>
                            </div>
                            <div class="flex justify-between items-center">
                                <p class="text-xs text-gray-400 truncate flex-1 pr-2" x-text="chat.last_message || 'Нет сообщений'"></p>
                                <span x-show="chat.unread_count > 0" class="bg-blue-600 text-white font-bold text-[10px] h-4 min-w-4 px-1 rounded-full flex items-center justify-center flex-shrink-0" x-text="chat.unread_count"></span>
                            </div>
                        </div>
                    </div>
                </template>
            </div>
        </div>

        <div class="flex-1 bg-gray-950 flex flex-col h-full max-h-full min-w-0 relative" :class="currentChat ? 'flex' : 'hidden md:flex'">
            
            <div x-show="!currentChat" class="flex-1 flex flex-col items-center justify-center text-gray-500 bg-gray-900/40">
                <svg class="w-16 h-16 mb-2 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                <p class="text-sm font-medium">Выберите чат, чтобы начать общение</p>
            </div>

            <div x-show="currentChat" class="h-14 border-b border-gray-800 bg-gray-900 px-3 flex items-center justify-between flex-shrink-0 z-10" style="display: none;">
                <div class="flex items-center gap-3 min-w-0">
                    <button @click="currentChat = null" class="md:hidden text-gray-400 hover:text-white mr-1 flex-shrink-0">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                    </button>
                    <div @click="openUserProfile(currentChat.partner_id)" class="w-9 h-9 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold overflow-hidden cursor-pointer shadow-inner flex-shrink-0">
                        <img x-show="currentChat && currentChat.partner_avatar" :src="currentChat.partner_avatar" class="w-full h-full object-cover">
                        <span x-show="currentChat && !currentChat.partner_avatar" x-text="currentChat.partner_name[0]"></span>
                    </div>
                    <div class="min-w-0 cursor-pointer" @click="openUserProfile(currentChat.partner_id)">
                        <div class="flex items-center gap-1">
                            <h2 class="font-bold text-sm text-gray-100 truncate" x-text="currentChat ? currentChat.partner_name : ''"></h2>
                            <span x-show="currentChat && currentChat.partner_is_admin" class="admin-badge">Admin</span>
                        </div>
                        <p class="text-[11px] text-gray-400 truncate">
                            <span x-show="typing[currentChat?.partner_id]" class="text-green-400 font-medium">Печатает...</span>
                            <span x-show="!typing[currentChat?.partner_id]" x-text="currentChat ? (chatOnlineStatus ? 'В сети' : 'Был(а) в сети недавно') : ''"></span>
                        </p>
                    </div>
                </div>
            </div>

            <div x-show="currentChat" id="messagesBox" class="flex-1 overflow-y-auto p-4 space-y-3 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-gray-900/60 via-gray-950 to-gray-950 select-text" style="display: none;">
                <template x-for="msg in messages" :key="msg.id">
                    <div class="flex w-full" :class="msg.sender_id === {{ current_user.id }} ? 'justify-end' : 'justify-start'">
                        <div class="max-w-[85%] md:max-w-[70%] rounded-2xl px-3 py-2 shadow-md relative group flex flex-col"
                             :class="msg.is_deleted ? 'bg-red-950/40 border border-red-900 text-red-200 rounded-sm' : (msg.sender_id === {{ current_user.id }} ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-gray-800 text-gray-100 rounded-tl-sm')"
                             @contextmenu.prevent="openContextMenu($event, msg, false)"
                             @touchstart="handleTouchStart($event, msg)"
                             @touchend="handleTouchEnd()"
                             @touchmove="handleTouchEnd()">
                            
                            <template x-if="msg.forwarded_from_name">
                                <div class="text-[11px] opacity-75 italic mb-1 border-l-2 border-white/40 pl-1.5">
                                    Переслано от <span class="font-semibold" x-text="msg.forwarded_from_name"></span>
                                </div>
                            </template>

                            <template x-if="msg.reply_to_msg">
                                <div class="text-[11px] opacity-80 bg-black/15 rounded px-2 py-1 mb-1.5 border-l-2 border-white/60 truncate max-w-full">
                                    <span class="font-bold block" x-text="msg.reply_to_msg.sender_name"></span>
                                    <span x-text="msg.reply_to_msg.is_deleted ? 'Сообщение удалено' : (msg.reply_to_msg.text || '[Фото]')"></span>
                                </div>
                            </template>

                            <template x-if="msg.image_base64 && !msg.is_deleted">
                                <img :src="msg.image_base64" class="rounded-lg max-w-full max-h-64 object-contain mb-1 shadow-inner cursor-pointer" @click="window.open(msg.image_base64, '_blank')">
                            </template>

                            <p class="text-sm break-words leading-relaxed pr-8" x-text="msg.text"></p>

                            <div class="absolute bottom-1 right-2 flex items-center gap-0.5 select-none text-[9px] opacity-70">
                                <span x-show="msg.is_edited && !msg.is_deleted" class="italic font-light cursor-pointer underline" @click.stop="openContextMenu($event, msg, false); actionShowHistory()">ред.</span>
                                <span x-text="msg.time"></span>
                                <template x-if="msg.sender_id === {{ current_user.id }} && !msg.is_deleted">
                                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path x-show="!msg.is_read" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>
                                        <path x-show="msg.is_read" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7m-7 0l4 4L23 9"></path>
                                    </svg>
                                </template>
                            </div>
                        </div>
                    </div>
                </template>
            </div>

            <div x-show="currentChat" class="p-3 border-t border-gray-800 bg-gray-900 flex flex-col gap-2 flex-shrink-0 relative" style="display: none;">
                
                <div x-show="replyToMessage" class="bg-gray-800/80 px-3 py-1.5 rounded-lg flex justify-between items-center text-xs text-gray-300 border-l-4 border-blue-500 animate-pulse">
                    <div class="truncate">Ответ на сообщение пользователя <span class="font-semibold text-blue-400" x-text="replyToMessage ? replyToMessage.sender_name : ''"></span></div>
                    <button @click="replyToMessage = null" class="text-gray-400 hover:text-white ml-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button>
                </div>

                <div x-show="editMessage" class="bg-gray-800/80 px-3 py-1.5 rounded-lg flex justify-between items-center text-xs text-gray-300 border-l-4 border-yellow-500 animate-pulse">
                    <div class="truncate">Редактирование сообщения...</div>
                    <button @click="editMessage = null; newMessage = ''" class="text-gray-400 hover:text-white ml-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button>
                </div>

                <div x-show="imagePreview" class="relative inline-block self-start mt-1 bg-gray-800 p-1 rounded-lg border border-gray-700 shadow-md" style="display: none;">
                    <img :src="imagePreview" class="h-16 w-16 object-cover rounded-md">
                    <button @click="imagePreview = null" class="absolute -top-1.5 -right-1.5 bg-red-600 hover:bg-red-700 text-white rounded-full p-0.5 shadow-md"><svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button>
                </div>

                <div class="flex items-center gap-2">
                    <label class="p-2.5 bg-gray-800 text-gray-400 hover:text-white rounded-xl cursor-pointer border border-gray-700/60 flex-shrink-0 transition">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.415-6.585a6 6 0 10-8.486-8.486L20.5 13"></path></svg>
                        <input type="file" class="hidden" accept="image/*" @change="handleImageSelect">
                    </label>

                    <input type="text" x-model="newMessage" @keydown.enter="sendMessage()" @input="sendTyping()" placeholder="Сообщение..." 
                           class="flex-1 bg-gray-800 border border-gray-700/80 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500 placeholder-gray-500">

                    <button @click="sendMessage()" class="p-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl flex-shrink-0 transition shadow-md">
                        <svg class="w-5 h-5 transform rotate-90" fill="currentColor" viewBox="0 0 20 20"><path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"></path></svg>
                    </button>
                </div>
            </div>
        </div>

        <div x-show="contextMenu.show" 
             @click.away="contextMenu.show = false" 
             class="absolute bg-gray-800 border border-gray-700 rounded-xl shadow-2xl z-50 w-48 overflow-hidden py-1 select-none backdrop-blur-md"
             :style="'left: ' + contextMenu.x + 'px; top: ' + contextMenu.y + 'px;'" style="display: none;">
            
            <template x-if="contextMenu.msg && !contextMenu.msg.is_deleted">
                <button @click="actionReply()" class="w-full text-left px-4 py-2.5 text-xs hover:bg-gray-700 font-medium flex items-center gap-2"><svg class="w-4 h-4 opacity-75" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6"></path></svg>Ответить</button>
            </template>
            <template x-if="contextMenu.msg && contextMenu.msg.sender_id === {{ current_user.id }} && !contextMenu.msg.is_deleted">
                <button @click="actionEdit()" class="w-full text-left px-4 py-2.5 text-xs hover:bg-gray-700 font-medium flex items-center gap-2 text-yellow-400"><svg class="w-4 h-4 opacity-75" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>Редактировать</button>
            </template>
            <template x-if="contextMenu.msg && !contextMenu.msg.is_deleted">
                <button @click="actionForward()" class="w-full text-left px-4 py-2.5 text-xs hover:bg-gray-700 font-medium flex items-center gap-2"><svg class="w-4 h-4 opacity-75" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>Переслать</button>
            </template>
            <template x-if="contextMenu.msg && contextMenu.msg.is_edited && !contextMenu.msg.is_deleted">
                <button @click="actionShowHistory()" class="w-full text-left px-4 py-2.5 text-xs hover:bg-gray-700 font-medium flex items-center gap-2"><svg class="w-4 h-4 opacity-75" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>История изменений</button>
            </template>
            <template x-if="contextMenu.msg && (contextMenu.msg.sender_id === {{ current_user.id }} || {{ 'true' if current_user.is_admin else 'false' }}) && !contextMenu.msg.is_deleted">
                <button @click="actionDelete()" class="w-full text-left px-4 py-2.5 text-xs hover:bg-gray-700 font-semibold flex items-center gap-2 text-red-400 border-t border-gray-700/50 mt-1"><svg class="w-4 h-4 opacity-75" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-16v1a3 3 0 003 3h10M9 3h6m2 4h-14"></path></svg>Удалить</button>
            </template>
        </div>

        <div x-show="forwardModal" class="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" style="display: none;">
            <div class="bg-gray-800 border border-gray-700 rounded-xl max-w-sm w-full p-5 shadow-2xl relative" @click.away="forwardModal = false">
                <h3 class="text-base font-bold text-gray-100 mb-4 flex items-center gap-2"><svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>Переслать сообщение в...</h3>
                <div class="space-y-1 max-h-60 overflow-y-auto pr-1">
                    <template x-for="chat in chats" :key="chat.chat_id">
                        <div @click="confirmForward(chat.chat_id)" class="p-2.5 flex items-center gap-3 hover:bg-gray-700 rounded-lg cursor-pointer transition">
                            <div class="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold overflow-hidden"><img x-show="chat.partner_avatar" :src="chat.partner_avatar" class="w-full h-full object-cover"><span x-show="!chat.partner_avatar" x-text="chat.partner_name[0]"></span></div>
                            <span class="text-sm font-medium truncate text-gray-200" x-text="chat.partner_name"></span>
                        </div>
                    </template>
                </div>
                <button @click="forwardModal = false" class="mt-4 w-full bg-gray-700 hover:bg-gray-600 text-white font-medium text-xs py-2 rounded-lg transition">Отмена</button>
            </div>
        </div>

        <div x-show="showHistoryModal" class="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" style="display: none;">
            <div class="bg-gray-800 border border-gray-700 rounded-xl max-w-md w-full p-5 shadow-2xl" @click.away="showHistoryModal = false">
                <h3 class="text-base font-bold text-gray-100 mb-3 flex items-center gap-1.5"><svg class="w-5 h-5 text-yellow-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg> Исходный текст сообщения</h3>
                <div class="bg-gray-900 border border-gray-700/80 p-3 rounded-lg text-sm text-gray-300 break-words select-text font-mono max-h-48 overflow-y-auto" x-text="historyText"></div>
                <button @click="showHistoryModal = false" class="mt-4 w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs py-2 rounded-lg transition">Закрыть</button>
            </div>
        </div>

        <div x-show="showProfileModal" class="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" style="display: none;">
            <div class="bg-gray-800 border border-gray-700 rounded-2xl max-w-md w-full p-5 shadow-2xl relative" @click.away="showProfileModal = false">
                
                <button @click="showProfileModal = false" class="absolute top-4 right-4 text-gray-400 hover:text-white"><svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button>

                <div x-show="!editMode" class="flex flex-col items-center text-center">
                    <div class="w-24 h-24 rounded-full bg-blue-600 flex items-center justify-center text-white text-3xl font-bold overflow-hidden shadow-xl mb-3 border-2 border-gray-700">
                        <img x-show="viewProfileData.avatar" :src="viewProfileData.avatar" class="w-full h-full object-cover">
                        <span x-show="!viewProfileData.avatar" x-text="viewProfileData.first_name ? viewProfileData.first_name[0] : ''"></span>
                    </div>
                    <div class="flex items-center gap-1.5 justify-center mb-1">
                        <h3 class="text-xl font-bold text-white" x-text="viewProfileData.first_name + ' ' + (viewProfileData.last_name || '')"></h3>
                        <span x-show="viewProfileData.is_admin" class="admin-badge">Admin</span>
                    </div>
                    <p class="text-xs text-blue-400 font-mono mb-4" x-text="'@' + viewProfileData.username"></p>

                    <div class="w-full space-y-2.5 text-left text-sm border-t border-gray-700 pt-4">
                        <div><span class="text-xs text-gray-500 block">Класс / Подразделение:</span> <span class="font-medium text-gray-200" x-text="viewProfileData.class_name || 'Не указан'"></span></div>
                        <div><span class="text-xs text-gray-500 block">Телефон:</span> <span class="font-medium text-gray-200" x-text="viewProfileData.phone || 'Скрыт настройками'"></span></div>
                        <div><span class="text-xs text-gray-500 block">День рождения:</span> <span class="font-medium text-gray-200" x-text="viewProfileData.birth_date || 'Скрыт настройками'"></span></div>
                        <div><span class="text-xs text-gray-500 block">О себе:</span> <p class="text-sm text-gray-300 font-light mt-0.5 break-words" x-text="viewProfileData.about_me || 'Информации нет'"></p></div>
                    </div>

                    <template x-if="isMyProfile">
                        <button @click="editMode = true" class="mt-5 w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-xl transition shadow-md">Редактировать профиль</button>
                    </template>
                </div>

                <div x-show="editMode" style="display: none;" class="space-y-3.5">
                    <h3 class="text-base font-bold text-gray-100 border-b border-gray-700 pb-2">Редактирование личных данных</h3>
                    
                    <div class="flex items-center gap-3">
                        <div class="w-14 h-14 rounded-full bg-gray-700 flex items-center justify-center text-white text-lg font-bold overflow-hidden border border-gray-600 flex-shrink-0 relative">
                            <img x-show="editProfileData.avatar" :src="editProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!editProfileData.avatar" x-text="editProfileData.first_name ? editProfileData.first_name[0] : ''"></span>
                        </div>
                        <label class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded-lg text-xs font-medium text-gray-200 cursor-pointer transition">Изменить аватар<input type="file" class="hidden" accept="image/*" @change="handleAvatarChange"></label>
                        <button x-show="editProfileData.avatar" @click="editProfileData.avatar = null" class="text-xs text-red-400 hover:underline">Удалить</button>
                    </div>

                    <div class="grid grid-cols-2 gap-2">
                        <div><label class="text-[10px] text-gray-400 block mb-0.5">Имя</label><input type="text" x-model="editProfileData.first_name" class="w-full bg-gray-900 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"></div>
                        <div><label class="text-[10px] text-gray-400 block mb-0.5">Фамилия</label><input type="text" x-model="editProfileData.last_name" class="w-full bg-gray-900 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"></div>
                    </div>

                    <div><label class="text-[10px] text-gray-400 block mb-0.5">Класс / Должность</label><input type="text" x-model="editProfileData.class_name" placeholder="Например: 11-А" class="w-full bg-gray-900 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"></div>
                    <div><label class="text-[10px] text-gray-400 block mb-0.5">Телефон</label><input type="text" x-model="editProfileData.phone" placeholder="+7 (999) 000-00-00" class="w-full bg-gray-900 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"></div>
                    <div><label class="text-[10px] text-gray-400 block mb-0.5">День рождения (ДД.ММ.ГГГГ)</label><input type="text" x-model="editProfileData.birth_date" placeholder="25.10.2008" class="w-full bg-gray-900 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"></div>
                    <div><label class="text-[10px] text-gray-400 block mb-0.5">О себе</label><textarea x-model="editProfileData.about_me" rows="2" class="w-full bg-gray-900 border border-gray-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500 resize-none"></textarea></div>

                    <div class="bg-gray-900/60 border border-gray-700/50 p-2.5 rounded-xl space-y-2">
                        <h4 class="text-[11px] uppercase font-bold tracking-wider text-gray-400 mb-1">Приватность данных</h4>
                        <label class="flex items-center justify-between text-xs cursor-pointer"><span class="text-gray-300">Показывать номер телефона</span><input type="checkbox" x-model="editProfileData.show_phone" class="rounded bg-gray-800 border-gray-700 text-blue-600 focus:ring-0 w-4 h-4"></label>
                        <label class="flex items-center justify-between text-xs cursor-pointer"><span class="text-gray-300">Показывать дату рождения</span><input type="checkbox" x-model="editProfileData.show_birth_date" class="rounded bg-gray-800 border-gray-700 text-blue-600 focus:ring-0 w-4 h-4"></label>
                        <label class="flex items-center justify-between text-xs cursor-pointer"><span class="text-gray-300">Показывать описание "О себе"</span><input type="checkbox" x-model="editProfileData.show_about" class="rounded bg-gray-800 border-gray-700 text-blue-600 focus:ring-0 w-4 h-4"></label>
                    </div>

                    <div class="flex gap-2 pt-1">
                        <button @click="editMode = false" class="w-1/3 bg-gray-700 hover:bg-gray-600 text-white font-semibold text-xs py-2 rounded-lg transition">Отмена</button>
                        <button @click="saveProfile()" class="w-2/3 bg-green-600 hover:bg-green-700 text-white font-bold text-xs py-2 rounded-lg transition shadow-md">Сохранить изменения</button>
                    </div>
                </div>

            </div>
        </div>

    </div>

    <script>
        function messengerApp() {
            return {
                socket: null,
                chats: [],
                searchQuery: '',
                searchResults: [],
                currentChat: null,
                messages: [],
                newMessage: '',
                imagePreview: null,
                typing: {},
                chatOnlineStatus: false,
                
                contextMenu: { show: false, x: 0, y: 0, msg: null },
                longPressTimer: null,
                touchX: 0,
                touchY: 0,
                
                replyToMessage: null,
                editMessage: null,
                forwardModal: false,
                forwardMessageTarget: null,
                showHistoryModal: false,
                historyText: '',
                
                showProfileModal: false,
                isMyProfile: false,
                editMode: false,
                myProfileData: {},
                viewProfileData: {},
                editProfileData: {},

                init() {
                    this.fetchMyProfile();
                    this.socket = io();

                    this.socket.on('connect', () => {
                        this.loadChats();
                    });

                    this.socket.on('new_message', (msg) => {
                        if (this.currentChat && msg.chat_id === this.currentChat.chat_id) {
                            this.messages.push(msg);
                            this.scrollToBottom();
                            this.socket.emit('read_messages', { chat_id: this.currentChat.chat_id });
                        }
                        this.loadChats();
                    });

                    this.socket.on('message_edited', (data) => {
                        if (this.currentChat && data.chat_id === this.currentChat.chat_id) {
                            let m = this.messages.find(msg => msg.id === data.message_id);
                            if (m) {
                                m.text = data.text;
                                m.is_edited = true;
                                m.original_text = data.original_text;
                            }
                        }
                    });

                    this.socket.on('message_deleted', (data) => {
                        if (this.currentChat && data.chat_id === this.currentChat.chat_id) {
                            let m = this.messages.find(msg => msg.id === data.message_id);
                            if (m) {
                                m.text = "Сообщение удалено";
                                m.is_deleted = true;
                                m.image_base64 = null;
                            }
                        }
                        this.loadChats();
                    });

                    this.socket.on('user_typing', (data) => {
                        if (this.currentChat && data.chat_id === this.currentChat.chat_id && data.user_id !== {{ current_user.id }}) {
                            this.typing[data.user_id] = true;
                            setTimeout(() => { this.typing[data.user_id] = false; }, 3000);
                        }
                    });

                    this.socket.on('update_online_status', (data) => {
                        if (this.currentChat && data.user_id === this.currentChat.partner_id) {
                            this.chatOnlineStatus = data.is_online;
                        }
                    });
                },

                loadChats() {
                    fetch('/api/chats')
                        .then(r => r.json())
                        .then(data => { 
                            this.chats = data; 
                            if (this.currentChat) {
                                let updated = this.chats.find(c => c.chat_id === this.currentChat.chat_id);
                                if (updated) this.chatOnlineStatus = updated.partner_is_online;
                            }
                        });
                },

                searchUsers() {
                    if (!this.searchQuery.trim()) { this.searchResults = []; return; }
                    fetch(`/api/search_users?q=${encodeURIComponent(this.searchQuery)}`)
                        .then(r => r.json())
                        .then(data => { this.searchResults = data; });
                },

                selectChat(chat) {
                    this.currentChat = chat;
                    this.replyToMessage = null;
                    this.editMessage = null;
                    this.newMessage = '';
                    this.imagePreview = null;
                    this.chatOnlineStatus = chat.partner_is_online;
                    
                    fetch(`/api/get_messages?chat_id=${chat.chat_id}`)
                        .then(r => r.json())
                        .then(data => {
                            this.messages = data;
                            this.scrollToBottom();
                            this.socket.emit('read_messages', { chat_id: chat.chat_id });
                            this.loadChats();
                        });
                },

                startChatWithUser(user) {
                    this.searchResults = [];
                    this.searchQuery = '';
                    fetch('/api/add_contact', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ contact_id: user.id })
                    })
                    .then(r => r.json())
                    .then(chat => {
                        this.loadChats();
                        this.selectChat(chat);
                    });
                },

                sendMessage() {
                    if (!this.newMessage.trim() && !this.imagePreview) return;

                    if (this.editMessage) {
                        this.socket.emit('edit_message', {
                            message_id: this.editMessage.id,
                            text: this.newMessage.trim()
                        });
                        this.editMessage = null;
                    } else {
                        const payload = {
                            chat_id: this.currentChat.chat_id,
                            text: this.newMessage.trim(),
                            image_base64: this.imagePreview,
                            reply_to_id: this.replyToMessage ? this.replyToMessage.id : null
                        };
                        this.socket.emit('send_message', payload);
                        this.replyToMessage = null;
                    }
                    this.newMessage = '';
                    this.imagePreview = null;
                },

                sendTyping() {
                    if (this.currentChat) {
                        this.socket.emit('typing', { chat_id: this.currentChat.chat_id });
                    }
                },

                handleImageSelect(e) {
                    const file = e.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (event) => { this.imagePreview = event.target.result; };
                    reader.readAsDataURL(file);
                },

                openContextMenu(e, msg, isTouch) {
                    this.contextMenu = {
                        show: true,
                        x: isTouch ? this.touchX : e.clientX,
                        y: isTouch ? this.touchY : e.clientY,
                        msg: msg
                    };
                },

                handleTouchStart(e, msg) {
                    this.touchX = e.touches[0].clientX;
                    this.touchY = e.touches[0].clientY;
                    this.longPressTimer = setTimeout(() => { this.openContextMenu(e, msg, true); }, 600);
                },

                handleTouchEnd() {
                    if (this.longPressTimer) clearTimeout(this.longPressTimer);
                },

                actionReply() {
                    this.contextMenu.show = false;
                    this.replyToMessage = this.contextMenu.msg;
                    this.editMessage = null;
                },

                actionEdit() {
                    this.contextMenu.show = false;
                    if (this.contextMenu.msg.sender_id !== {{ current_user.id }}) return;
                    this.editMessage = this.contextMenu.msg;
                    this.newMessage = this.editMessage.text;
                    this.replyToMessage = null;
                },

                actionForward() {
                    this.contextMenu.show = false;
                    this.forwardMessageTarget = this.contextMenu.msg;
                    this.forwardModal = true;
                },

                confirmForward(chatId) {
                    this.socket.emit('forward_message', {
                        chat_id: chatId,
                        text: this.forwardMessageTarget.text,
                        image_base64: this.forwardMessageTarget.image_base64,
                        forwarded_from_id: this.forwardMessageTarget.sender_id
                    });
                    this.forwardModal = false;
                    this.forwardMessageTarget = null;
                    this.loadChats();
                },

                actionDelete() {
                    this.contextMenu.show = false;
                    if (confirm("Удалить это сообщение?")) {
                        this.socket.emit('delete_message', { message_id: this.contextMenu.msg.id });
                    }
                },

                actionShowHistory() {
                    this.contextMenu.show = false;
                    this.historyText = this.contextMenu.msg.original_text || 'История изменений отсутствует.';
                    this.showHistoryModal = true;
                },

                fetchMyProfile() {
                    fetch('/api/profile')
                        .then(r => r.json())
                        .then(data => { this.myProfileData = data; });
                },

                openMyProfile() {
                    this.isMyProfile = true;
                    this.editMode = false;
                    this.editProfileData = Object.assign({}, this.myProfileData);
                    this.viewProfileData = this.myProfileData;
                    this.showProfileModal = true;
                },

                openUserProfile(userId) {
                    this.isMyProfile = false;
                    this.editMode = false;
                    fetch(`/api/profile?user_id=${userId}`)
                        .then(r => r.json())
                        .then(data => {
                            this.viewProfileData = data;
                            this.showProfileModal = true;
                        });
                },

                saveProfile() {
                    fetch('/api/edit_profile', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.editProfileData)
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (data.success) {
                            this.fetchMyProfile();
                            this.showProfileModal = false;
                        } else {
                            alert(data.error || "Ошибка сохранения");
                        }
                    });
                },

                handleAvatarChange(e) {
                    const file = e.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (event) => { this.editProfileData.avatar = event.target.result; };
                    reader.readAsDataURL(file);
                },

                scrollToBottom() {
                    setTimeout(() => {
                        const box = document.getElementById('messagesBox');
                        if (box) box.scrollTop = box.scrollHeight;
                    }, 100);
                }
            };
        }
    </script>
</body>
</html>
"""

ADMIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="container mx-auto p-4 md:p-6 pt-10 select-text">
        <div class="flex justify-between items-center mb-6">
            <h1 class="text-xl md:text-2xl font-bold text-red-500 flex items-center gap-2">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"></path></svg>
                Панель управления системой
            </h1>
            <a href="{{ url_for('index') }}" class="bg-gray-800 border border-gray-700 px-4 py-2 rounded-lg text-xs font-semibold hover:bg-gray-700 transition">В мессенджер</a>
        </div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="bg-blue-600/20 border border-blue-500 text-blue-200 p-3 rounded-lg mb-4 text-sm font-medium">
              {% for message in messages %}{{ message }}{% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        <div class="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden shadow-xl">
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse text-xs md:text-sm">
                    <thead>
                        <tr class="bg-gray-900 border-b border-gray-700 text-gray-400 font-semibold">
                            <th class="p-3">ID</th>
                            <th class="p-3">Пользователь</th>
                            <th class="p-3">Логин</th>
                            <th class="p-3">Класс</th>
                            <th class="p-3">Статус</th>
                            <th class="p-3 text-center">Действия</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-700/60">
                        {% for user in users %}
                        <tr class="hover:bg-gray-700/20 transition">
                            <td class="p-3 font-mono text-gray-500">{{ user.id }}</td>
                            <td class="p-3 font-bold text-white">{{ user.first_name }} {{ user.last_name or '' }}</td>
                            <td class="p-3 text-blue-400">@{{ user.username }}</td>
                            <td class="p-3 text-gray-300">{{ user.class_name or '-' }}</td>
                            <td class="p-3">
                                {% if user.id in connected %}
                                    <span class="text-green-400 font-semibold flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>Онлайн</span>
                                {% else %}
                                    <span class="text-gray-500">Офлайн</span>
                                {% endif %}
                            </td>
                            <td class="p-3 flex justify-center gap-2">
                                {% if user.id != current_user.id %}
                                    {% if user.is_admin %}
                                        <a href="{{ url_for('admin_action', target_id=user.id, action='demote') }}" class="bg-yellow-600/20 text-yellow-400 border border-yellow-600/50 px-2 py-1 rounded text-[11px] hover:bg-yellow-600 transition">Снять админа</a>
                                    {% else %}
                                        <a href="{{ url_for('admin_action', target_id=user.id, action='promote') }}" class="bg-red-900/40 text-red-300 border border-red-700/60 px-2 py-1 rounded text-[11px] hover:bg-red-700 transition">Назначить админа</a>
                                    {% endif %}
                                    <a href="{{ url_for('impersonate_user', target_id=user.id) }}" class="bg-blue-600/20 text-blue-400 border border-blue-600/50 px-2 py-1 rounded text-[11px] hover:bg-blue-600 transition">Войти как...</a>
                                {% else %}
                                    <span class="text-gray-600 text-xs italic">Это вы</span>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>
"""

# ==========================================
# МАРШРУТЫ (АВТОРИЗАЦИЯ И СТАТИКА)
# ==========================================
@app.route('/logo.png')
def serve_logo():
    return send_from_directory(os.getcwd(), 'logo.png')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')

        if not username or not password:
            flash("Заполните обязательные поля.")
            return render_template_string(LOGIN_TEMPLATE)

        if action == 'login':
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password, password):
                login_user(user)
                return redirect(url_for('index'))
            flash("Неверный логин или пароль.")
        
        elif action == 'register':
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip() or None
            
            if not first_name:
                flash("Имя обязательно для заполнения.")
                return render_template_string(LOGIN_TEMPLATE)
                
            if User.query.filter_by(username=username).first():
                flash("Этот логин уже занят.")
                return render_template_string(LOGIN_TEMPLATE)

            new_user = User(
                username=username,
                password=generate_password_hash(password),
                first_name=first_name,
                last_name=last_name
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for('index'))

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template_string(APP_TEMPLATE)

# ==========================================
# API ДЛЯ ЧАТОВ И ПОИСКА
# ==========================================
@app.route('/api/chats')
@login_required
def get_chats():
    participants = ChatParticipant.query.filter_by(user_id=current_user.id).all()
    chat_ids = [p.chat_id for p in participants]
    chats_data = []

    for cid in chat_ids:
        partner_cp = ChatParticipant.query.filter(
            ChatParticipant.chat_id == cid, 
            ChatParticipant.user_id != current_user.id
        ).first()
        if not partner_cp:
            continue
            
        partner = User.query.get(partner_cp.user_id)
        if not partner:
            continue
            
        last_msg = Message.query.filter_by(chat_id=cid).order_by(Message.timestamp.desc()).first()
        unread_count = Message.query.filter_by(chat_id=cid, sender_id=partner.id, is_read=False).count()
        
        msg_text = ""
        if last_msg:
            if last_msg.is_deleted:
                msg_text = "Сообщение удалено"
            elif last_msg.text:
                msg_text = last_msg.text
            elif last_msg.image_base64:
                msg_text = "[Фото]"

        chats_data.append({
            'chat_id': cid,
            'partner_id': partner.id,
            'partner_name': f"{partner.first_name} {partner.last_name or ''}".strip(),
            'partner_avatar': partner.avatar_url,
            'partner_is_admin': partner.is_admin,
            'partner_is_online': partner.id in connected_users,
            'last_message': msg_text,
            'last_message_time': last_msg.timestamp.strftime('%H:%M') if last_msg else '',
            'unread_count': unread_count
        })
    
    return jsonify(chats_data)

@app.route('/api/search_users')
@login_required
def search_users():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify([])
    
    results = User.query.filter(
        (User.id != current_user.id) & 
        ((User.username.like(f"%{q}%")) | (User.first_name.like(f"%{q}%")) | (User.last_name.like(f"%{q}%")))
    ).limit(15).all()
    
    return jsonify([{
        'id': u.id,
        'username': u.username,
        'first_name': u.first_name,
        'last_name': u.last_name,
        'avatar_url': u.avatar_url,
        'is_admin': u.is_admin
    } for u in results])

@app.route('/api/add_contact', methods=['POST'])
@login_required
def add_contact():
    data = request.get_json() or {}
    contact_id = data.get('contact_id')
    if not contact_id:
        return jsonify({'error': 'Missing user ID'}), 400

    # Проверка существования приватного чата
    my_p = ChatParticipant.query.filter_by(user_id=current_user.id).all()
    my_c_ids = [p.chat_id for p in my_p]
    
    existing = ChatParticipant.query.filter(
        (ChatParticipant.chat_id.in_(my_c_ids)) & 
        (ChatParticipant.user_id == contact_id)
    ).first()

    if existing:
        chat = Chat.query.get(existing.chat_id)
    else:
        chat = Chat(type='private')
        db.session.add(chat)
        db.session.commit()

        p1 = ChatParticipant(chat_id=chat.id, user_id=current_user.id)
        p2 = ChatParticipant(chat_id=chat.id, user_id=contact_id)
        db.session.add_all([p1, p2])
        
        # Добавляем в список контактов
        if not Contact.query.filter_by(user_id=current_user.id, contact_id=contact_id).first():
            db.session.add(Contact(user_id=current_user.id, contact_id=contact_id))
        if not Contact.query.filter_by(user_id=contact_id, contact_id=current_user.id).first():
            db.session.add(Contact(user_id=contact_id, contact_id=current_user.id))
            
        db.session.commit()

    partner = User.query.get(contact_id)
    return jsonify({
        'chat_id': chat.id,
        'partner_id': partner.id,
        'partner_name': f"{partner.first_name} {partner.last_name or ''}".strip(),
        'partner_avatar': partner.avatar_url,
        'partner_is_admin': partner.is_admin,
        'partner_is_online': partner.id in connected_users
    })

@app.route('/api/get_messages')
@login_required
def get_messages():
    chat_id = request.args.get('chat_id')
    if not chat_id:
        return jsonify([])

    # Верификация доступа к чату
    access = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    if not access:
        return jsonify([])

    messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    res = []
    for m in messages:
        sender = User.query.get(m.sender_id)
        
        reply_to_msg = None
        if m.reply_to_id:
            orig = Message.query.get(m.reply_to_id)
            if orig:
                orig_sender = User.query.get(orig.sender_id)
                reply_to_msg = {
                    'text': orig.text,
                    'is_deleted': orig.is_deleted,
                    'sender_name': orig_sender.first_name if orig_sender else "Пользователь"
                }

        forwarded_from_name = None
        if m.forwarded_from_id:
            f_user = User.query.get(m.forwarded_from_id)
            if f_user:
                forwarded_from_name = f_user.first_name

        res.append({
            'id': m.id,
            'chat_id': m.chat_id,
            'sender_id': m.sender_id,
            'sender_name': sender.first_name if sender else "Удаленный аккаунт",
            'text': "Сообщение удалено" if m.is_deleted else m.text,
            'image_base64': None if m.is_deleted else m.image_base64,
            'time': m.timestamp.strftime('%H:%M'),
            'is_read': m.is_read,
            'is_deleted': m.is_deleted,
            'is_edited': m.is_edited,
            'original_text': m.original_text,
            'reply_to_msg': reply_to_msg,
            'forwarded_from_name': forwarded_from_name
        })
    return jsonify(res)

@app.route('/api/profile')
@login_required
def get_profile():
    user_id = request.args.get('user_id', type=int)
    if not user_id or user_id == current_user.id:
        # Свой собственный профиль (все данные без ограничений)
        return jsonify({
            'id': current_user.id,
            'username': current_user.username,
            'first_name': current_user.first_name,
            'last_name': current_user.last_name,
            'class_name': current_user.class_name,
            'avatar': current_user.avatar_url,
            'phone': current_user.phone,
            'about_me': current_user.about_me,
            'birth_date': current_user.birth_date,
            'show_phone': current_user.show_phone,
            'show_birth_date': current_user.show_birth_date,
            'show_about': current_user.show_about,
            'is_admin': current_user.is_admin
        })
    
    # Чужой профиль (с учетом настроек видимости)
    u = User.query.get_or_404(user_id)
    return jsonify({
        'id': u.id,
        'username': u.username,
        'first_name': u.first_name,
        'last_name': u.last_name,
        'class_name': u.class_name,
        'avatar': u.avatar_url,
        'phone': u.phone if u.show_phone else "Скрыт настройками",
        'birth_date': format_bday(u.birth_date) if u.show_birth_date else "Не указана",
        'about_me': u.about_me if u.show_about else "Информация скрыта",
        'is_admin': u.is_admin
    })

@app.route('/api/edit_profile', methods=['POST'])
@login_required
def edit_profile():
    data = request.get_json() or {}
    try:
        current_user.first_name = data.get('first_name', current_user.first_name).strip()
        current_user.last_name = data.get('last_name', '').strip() or None
        current_user.class_name = data.get('class_name', '').strip() or None
        current_user.phone = data.get('phone', '').strip() or None
        current_user.birth_date = data.get('birth_date', '').strip() or None
        current_user.about_me = data.get('about_me', '').strip() or None
        current_user.avatar_url = data.get('avatar', current_user.avatar_url)

        current_user.show_phone = bool(data.get('show_phone', current_user.show_phone))
        current_user.show_birth_date = bool(data.get('show_birth_date', current_user.show_birth_date))
        current_user.show_about = bool(data.get('show_about', current_user.show_about))

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

# ==========================================
# МАРШРУТЫ АДМИНИСТРАТОРА
# ==========================================
@app.route('/admin')
@login_required
def admin_panel():
    is_real_admin = current_user.is_admin or 'original_admin_id' in session
    if not is_real_admin:
        flash("Доступ запрещен.")
        return redirect(url_for('index'))
    users = User.query.order_by(User.id.desc()).all()
    return render_template_string(ADMIN_TEMPLATE, users=users, connected=connected_users)

@app.route('/admin/action/<int:target_id>/<action>')
@login_required
def admin_action(target_id, action):
    if not current_user.is_admin:
        return "Access denied", 403
    target = User.query.get_or_404(target_id)
    if action == 'promote':
        target.is_admin = True
        target.promoted_by_id = current_user.id
        flash(f"Пользователю @{target.username} выданы права администратора.")
    elif action == 'demote':
        target.is_admin = False
        flash(f"С пользователя @{target.username} сняты права администратора.")
    db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/impersonate/<int:target_id>')
@login_required
def impersonate_user(target_id):
    if not current_user.is_admin and 'original_admin_id' not in session:
        return "Access denied", 403
        
    target = User.query.get_or_404(target_id)
    if 'original_admin_id' not in session:
        session['original_admin_id'] = current_user.id
        
    login_user(target)
    flash(f"Вы успешно вошли в систему от лица {target.first_name}.")
    return redirect(url_for('index'))

@app.route('/admin/revert_impersonate')
@login_required
def revert_impersonate():
    orig_id = session.get('original_admin_id')
    if not orig_id:
        return redirect(url_for('index'))
        
    orig_admin = User.query.get(orig_id)
    session.pop('original_admin_id', None)
    login_user(orig_admin)
    flash("Вы вернулись в свой профиль администратора.")
    return redirect(url_for('admin_panel'))

# ==========================================
# ОБРАБОТЧИКИ SOCKET.IO СОБЫТИЙ
# ==========================================
@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        connected_users[current_user.id] = request.sid
        join_room(f"user_{current_user.id}")
        emit('update_online_status', {'user_id': current_user.id, 'is_online': True}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        connected_users.pop(current_user.id, None)
        leave_room(f"user_{current_user.id}")
        emit('update_online_status', {'user_id': current_user.id, 'is_online': False}, broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    chat_id = data.get('chat_id')
    text_content = data.get('text', '').strip()
    image_base64 = data.get('image_base64')
    reply_to_id = data.get('reply_to_id')

    if not chat_id or (not text_content and not image_base64):
        return

    msg = Message(
        chat_id=chat_id,
        sender_id=current_user.id,
        text=text_content,
        image_base64=image_base64,
        reply_to_id=reply_to_id,
        is_read=False
    )
    db.session.add(msg)
    db.session.commit()

    reply_to_msg = None
    if msg.reply_to_id:
        orig = Message.query.get(msg.reply_to_id)
        if orig:
            orig_sender = User.query.get(orig.sender_id)
            reply_to_msg = {
                'text': orig.text,
                'is_deleted': orig.is_deleted,
                'sender_name': orig_sender.first_name if orig_sender else "Пользователь"
            }

    msg_data = {
        'id': msg.id,
        'chat_id': chat_id,
        'sender_id': current_user.id,
        'sender_name': current_user.first_name,
        'text': msg.text,
        'image_base64': msg.image_base64,
        'time': msg.timestamp.strftime('%H:%M'),
        'is_read': False,
        'is_deleted': False,
        'is_edited': False,
        'reply_to_msg': reply_to_msg,
        'forwarded_from_name': None
    }

    participants = ChatParticipant.query.filter_by(chat_id=chat_id).all()
    for p in participants:
        socketio.emit('new_message', msg_data, room=f"user_{p.user_id}")

@socketio.on('edit_message')
def handle_edit_message(data):
    msg_id = data.get('message_id')
    new_text = data.get('text', '').strip()
    if not msg_id or not new_text:
        return

    msg = Message.query.get(msg_id)
    if msg and msg.sender_id == current_user.id and not msg.is_deleted:
        if not msg.is_edited:
            msg.original_text = msg.text
            msg.is_edited = True
        msg.text = new_text
        db.session.commit()

        edit_data = {
            'message_id': msg.id,
            'chat_id': msg.chat_id,
            'text': msg.text,
            'original_text': msg.original_text
        }
        
        participants = ChatParticipant.query.filter_by(chat_id=msg.chat_id).all()
        for p in participants:
            socketio.emit('message_edited', edit_data, room=f"user_{p.user_id}")

@socketio.on('delete_message')
def handle_delete_message(data):
    msg_id = data.get('message_id')
    if not msg_id:
        return

    msg = Message.query.get(msg_id)
    if msg:
        if msg.sender_id == current_user.id or current_user.is_admin:
            msg.is_deleted = True
            db.session.commit()

            del_data = {
                'message_id': msg.id,
                'chat_id': msg.chat_id
            }
            
            participants = ChatParticipant.query.filter_by(chat_id=msg.chat_id).all()
            for p in participants:
                socketio.emit('message_deleted', del_data, room=f"user_{p.user_id}")

@socketio.on('forward_message')
def handle_forward_message(data):
    chat_id = data.get('chat_id')
    text_content = data.get('text')
    image_base64 = data.get('image_base64')
    forwarded_from_id = data.get('forwarded_from_id')

    if not chat_id:
        return

    msg = Message(
        chat_id=chat_id,
        sender_id=current_user.id,
        text=text_content,
        image_base64=image_base64,
        forwarded_from_id=forwarded_from_id,
        is_read=False
    )
    db.session.add(msg)
    db.session.commit()

    f_user = User.query.get(forwarded_from_id) if forwarded_from_id else None

    msg_data = {
        'id': msg.id,
        'chat_id': chat_id,
        'sender_id': current_user.id,
        'sender_name': current_user.first_name,
        'text': msg.text,
        'image_base64': msg.image_base64,
        'time': msg.timestamp.strftime('%H:%M'),
        'is_read': False,
        'is_deleted': False,
        'is_edited': False,
        'reply_to_msg': None,
        'forwarded_from_name': f_user.first_name if f_user else "Пользователь"
    }

    participants = ChatParticipant.query.filter_by(chat_id=chat_id).all()
    for p in participants:
        socketio.emit('new_message', msg_data, room=f"user_{p.user_id}")

@socketio.on('read_messages')
def handle_read_messages(data):
    chat_id = data.get('chat_id')
    if not chat_id:
        return
    
    # Помечаем сообщения партнера как прочитанные
    unread = Message.query.filter_by(chat_id=chat_id, is_read=False).filter(Message.sender_id != current_user.id).all()
    if unread:
        for m in unread:
            m.is_read = True
        db.session.commit()

@socketio.on('typing')
def handle_typing(data):
    chat_id = data.get('chat_id')
    if chat_id:
        participants = ChatParticipant.query.filter_by(chat_id=chat_id).all()
        for p in participants:
            socketio.emit('user_typing', {'chat_id': chat_id, 'user_id': current_user.id}, room=f"user_{p.user_id}")

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И ЗАПУСК СЕРВЕРА
# ==========================================
def init_db():
    with app.app_context():
        db.create_all()
        
        # Автоматическая синхронизация структуры для расширенных полей (если таблицы создавались ранее)
        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS show_phone BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS show_about BOOLEAN DEFAULT TRUE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS show_birth_date BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_edited BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS original_text TEXT;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS forwarded_from_id INTEGER;"))
            db.session.commit()
            print("База данных успешно синхронизирована (колонки добавлены/проверены).")
        except Exception as e:
            db.session.rollback()
            print(f"Ошибка при автоматической миграции структуры: {e}")

        # Проверка и автоматическое создание администратора по умолчанию
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin', 
                password=generate_password_hash('admin'),
                first_name='Admin', 
                last_name='System',
                is_admin=True, 
                class_name='Administration',
                last_seen=datetime.utcnow()
            )
            db.session.add(admin)
            db.session.commit()
            print("Создана учетная запись администратора по умолчанию (admin:admin).")

init_db()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
