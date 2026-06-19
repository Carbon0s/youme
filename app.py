# ЭТИ ДВЕ СТРОКИ ДОЛЖНЫ БЫТЬ В САМОМ НАЧАЛЕ ФАЙЛА
import eventlet
eventlet.monkey_patch()

import os
from datetime import datetime
from flask import Flask, request, redirect, url_for, flash, session, jsonify, render_template_string, \
    send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash

# ==========================================
# КОНФИГУРАЦИЯ ПРИЛОЖЕНИЯ
# ==========================================
app = Flask(__name__)

# На Render лучше передавать секретный ключ через переменные окружения
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-key-for-youme-12345')

# Подключение к PostgreSQL Aiven с безопасной заменой префикса
db_url = os.environ.get(
    'DATABASE_URL', 
    "postgresql://avnadmin:AVNS_A094KJpWYOSX9t3_eM6@youme-krossmag.l.aivencloud.com:25520/defaultdb?sslmode=require"
)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ОПТИМИЗАЦИЯ ДЛЯ RENDER + AIVEN (Решает проблему огромных задержек)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,             # Количество одновременных постоянных соединений
    'pool_recycle': 280,         # Пересоздавать соединения каждые 280 секунд (до того как Render убьет их за неактивность)
    'pool_pre_ping': True,       # Быстрая проверка "живо ли соединение" перед выполнением запроса
    'pool_timeout': 20,          # Максимальное время ожидания свободного соединения
    'max_overflow': 15           # Дополнительные временные соединения при пиковых нагрузках
}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')


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
    last_name = db.Column(db.String(50), nullable=False)
    class_name = db.Column(db.String(20))

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


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ==========================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ==========================================
connected_users = {}

# ==========================================
# HTML ШАБЛОНЫ (Jinja2 + Tailwind + Alpine)
# ==========================================
BASE_HTML_HEAD = """
<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
<body class="bg-gray-900 text-gray-100 h-screen overflow-hidden flex flex-col font-sans">
    {% if session.get('original_admin_id') %}
    <div class="bg-red-600 text-white text-center py-2 text-sm font-bold flex justify-center items-center gap-4 z-50 shadow-lg">
        Внимание: Режим от лица {{ current_user.first_name }} {{ current_user.last_name }}!
        <a href="{{ url_for('revert_impersonate') }}" class="bg-white text-red-600 px-3 py-1 rounded-md hover:bg-gray-200 transition">Вернуться</a>
    </div>
    {% endif %}
"""

LOGIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex items-center justify-center bg-gray-900 px-4" x-data="{ isLogin: true }">
        <div class="bg-gray-800 p-8 rounded-xl shadow-2xl w-full max-w-md border border-gray-700">
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
                    <input type="text" name="username" placeholder="Логин (@username)" required class="w-full bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 rounded transition">Войти</button>
                <p class="text-center text-sm text-gray-400 mt-4">Нет аккаунта? <a href="#" @click.prevent="isLogin = false" class="text-blue-400 hover:underline">Регистрация</a></p>
            </form>

            <form x-show="!isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4" style="display: none;">
                <input type="hidden" name="action" value="register">
                <div>
                    <input type="text" name="username" placeholder="Придумайте логин (только латиница)" required class="w-full bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div class="flex gap-2">
                    <input type="text" name="first_name" placeholder="Имя" required class="w-1/2 bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                    <input type="text" name="last_name" placeholder="Фамилия" required class="w-1/2 bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <div>
                    <input type="text" name="class_name" placeholder="Класс (напр. 10А)" class="w-full bg-gray-700 border border-gray-600 rounded p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-green-600 hover:bg-green-700 text-white font-bold py-2 rounded transition">Зарегистрироваться</button>
                <p class="text-center text-sm text-gray-400 mt-4">Уже есть аккаунт? <a href="#" @click.prevent="isLogin = true" class="text-blue-400 hover:underline">Войти</a></p>
            </form>
        </div>
    </div>
</body>
</html>
"""

APP_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex overflow-hidden" x-data="messengerApp()">

        <div class="w-80 bg-gray-900 border-r border-gray-800 flex flex-col flex-shrink-0">
            <div class="p-4 border-b border-gray-800 flex justify-between items-center relative">
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
                    <a href="{{ url_for('admin_panel') }}" class="text-gray-400 hover:text-white" title="Админ Панель">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
                    </a>
                    {% endif %}
                    <a href="{{ url_for('logout') }}" class="text-gray-400 hover:text-red-500" title="Выйти">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                    </a>
                </div>
            </div>

            <div class="p-3">
                <input type="text" autocomplete="new-password" spellcheck="false" x-model="searchQuery" @input.debounce.300ms="searchUsers()" placeholder="Поиск (@username или имя)..." class="w-full bg-gray-800 text-sm text-gray-200 rounded-full px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>

            <div class="flex-1 overflow-y-auto">
                <template x-if="searchQuery.length > 0">
                    <div>
                        <div class="px-4 py-2 text-xs font-semibold text-gray-500 uppercase">Результаты</div>
                        <template x-for="user in searchResults" :key="user.id">
                            <div @click="startChat(user.id)" class="flex items-center gap-3 px-4 py-3 hover:bg-gray-800 cursor-pointer transition">
                                <div class="w-10 h-10 rounded-full bg-gradient-to-tr from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold overflow-hidden">
                                    <img x-show="user.avatar" :src="user.avatar" class="w-full h-full object-cover">
                                    <span x-show="!user.avatar" x-text="user.first_name[0]"></span>
                                </div>
                                <div class="flex-1 min-w-0">
                                    <div class="flex items-center gap-2">
                                        <div class="text-sm font-semibold truncate" x-text="user.first_name + ' ' + user.last_name"></div>
                                        <template x-if="user.is_admin">
                                            <span class="admin-badge">Admin</span>
                                        </template>
                                    </div>
                                    <div class="text-xs text-gray-400 truncate" x-text="'@' + user.username"></div>
                                </div>
                            </div>
                        </template>
                        <div x-show="searchResults.length === 0" class="px-4 text-sm text-gray-500">Поиск от 3-х символов...</div>
                    </div>
                </template>

                <template x-if="searchQuery.length === 0">
                    <div>
                        <template x-for="chat in chats" :key="chat.chat_id">
                            <div @click="openChat(chat)" class="flex items-center gap-3 px-4 py-3 hover:bg-gray-800 cursor-pointer transition" :class="currentChat && currentChat.chat_id === chat.chat_id ? 'bg-gray-800' : ''">
                                <div class="relative w-12 h-12 flex-shrink-0">
                                    <div class="w-full h-full rounded-full bg-gray-700 flex items-center justify-center text-white font-bold text-lg shadow-inner overflow-hidden">
                                        <img x-show="chat.partner_avatar" :src="chat.partner_avatar" class="w-full h-full object-cover">
                                        <span x-show="!chat.partner_avatar" x-text="chat.partner_name[0]"></span>
                                    </div>
                                    <div x-show="chat.is_online" class="absolute bottom-0 right-0 w-3.5 h-3.5 bg-blue-500 border-2 border-gray-900 rounded-full z-10"></div>
                                </div>

                                <div class="flex-1 min-w-0">
                                    <div class="flex justify-between items-center mb-1">
                                        <div class="text-sm font-semibold truncate flex items-center gap-2">
                                            <span x-text="chat.partner_name"></span>
                                            <template x-if="chat.partner_is_admin"><span class="admin-badge">Admin</span></template>
                                        </div>
                                        <div class="text-xs text-gray-500" x-text="chat.last_time"></div>
                                    </div>
                                    <div class="text-xs text-gray-400 truncate" x-text="chat.last_message || 'Нет сообщений'"></div>
                                </div>
                            </div>
                        </template>
                    </div>
                </template>
            </div>
        </div>

        <div class="flex-1 bg-[#0f172a] bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] flex flex-col relative" style="background-blend-mode: overlay;">

            <template x-if="!currentChat">
                <div class="flex-1 flex items-center justify-center text-gray-500">
                    <div class="bg-gray-900/60 px-4 py-2 rounded-full backdrop-blur-sm">Выберите чат для начала общения</div>
                </div>
            </template>

            <template x-if="currentChat">
                <div class="flex-1 flex flex-col h-full">
                    <div class="h-16 px-6 bg-gray-900/95 backdrop-blur-md border-b border-gray-800 flex items-center justify-between shadow-sm z-10 cursor-pointer" @click="openUserProfile(currentChat.partner_id)">
                        <div class="flex items-center gap-4">
                            <div class="flex flex-col">
                                <div class="flex items-center gap-2">
                                    <div class="text-white font-semibold" x-text="currentChat.partner_name"></div>
                                    <template x-if="currentChat.partner_is_admin"><span class="admin-badge">Admin</span></template>
                                </div>
                                <div class="text-xs flex items-center gap-1">
                                    <span :class="typing[currentChat.chat_id] ? 'text-blue-400 italic animate-pulse' : (currentChat.is_online ? 'text-blue-400' : 'text-gray-400')" 
                                          x-text="typing[currentChat.chat_id] ? 'печатает...' : (currentChat.is_online ? 'в сети' : 'был(а) ' + (currentChat.last_seen || 'недавно'))"></span>
                                </div>
                            </div>
                        </div>
                        <div class="w-10 h-10 rounded-full overflow-hidden bg-gray-700 flex items-center justify-center text-white">
                             <img x-show="currentChat.partner_avatar" :src="currentChat.partner_avatar" class="w-full h-full object-cover">
                             <span x-show="!currentChat.partner_avatar" x-text="currentChat.partner_name[0]"></span>
                        </div>
                    </div>

                    <div class="flex-1 overflow-y-auto p-6 space-y-4" id="messagesBox">
                        <template x-for="msg in messages" :key="msg.id">
                            <div class="flex" :class="msg.sender_id === {{ current_user.id }} ? 'justify-end' : 'justify-start'">
                                <div class="max-w-[70%] rounded-2xl px-4 py-2 shadow-md relative group"
                                     :class="msg.sender_id === {{ current_user.id }} ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-gray-800 text-gray-100 rounded-tl-sm'">

                                    <template x-if="msg.image_base64">
                                        <img :src="msg.image_base64" class="rounded-lg mb-2 max-w-full h-auto cursor-pointer">
                                    </template>

                                    <div class="text-[15px] leading-relaxed break-words" x-text="msg.text"></div>

                                    <div class="text-[10px] text-right mt-1 flex items-center justify-end gap-1 opacity-70" :class="msg.sender_id === {{ current_user.id }} ? 'text-blue-200' : 'text-gray-400'">
                                        <span x-text="msg.time"></span>
                                        <template x-if="msg.sender_id === {{ current_user.id }}">
                                            <span class="font-bold text-[11px]" :class="msg.is_read ? 'text-[#4da3ff]' : 'text-blue-200'" x-text="msg.is_read ? '✓✓' : '✓'"></span>
                                        </template>
                                    </div>

                                </div>
                            </div>
                        </template>
                    </div>

                    <div class="bg-gray-900 p-4 border-t border-gray-800">
                        <div x-show="imagePreview" class="mb-3 relative inline-block">
                            <img :src="imagePreview" class="h-20 rounded-lg border border-gray-600">
                            <button @click="imagePreview = null" class="absolute -top-2 -right-2 bg-red-500 text-white rounded-full w-6 h-6 flex items-center justify-center text-xs shadow">x</button>
                        </div>

                        <div class="flex items-center gap-3 max-w-4xl mx-auto">
                            <label class="cursor-pointer text-gray-400 hover:text-blue-500 transition">
                                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                                <input type="file" class="hidden" accept="image/*" @change="handleImageSelect">
                            </label>

                            <input type="text" x-model="newMessage" @keydown.enter="sendMessage()" @input="sendTyping()" placeholder="Написать сообщение..." class="flex-1 bg-gray-800 text-white rounded-full px-5 py-3 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner">

                            <button @click="sendMessage()" class="bg-blue-600 hover:bg-blue-500 text-white rounded-full w-12 h-12 flex items-center justify-center transition shadow-lg" :disabled="!newMessage.trim() && !imagePreview">
                                <svg class="w-5 h-5 ml-1 transform -rotate-45" fill="currentColor" viewBox="0 0 20 20"><path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"></path></svg>
                            </button>
                        </div>
                    </div>
                </div>
            </template>
        </div>

        <div x-show="showProfileModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" @click.self="closeProfileModal()">
            <div class="bg-[#242f3d] w-full max-w-sm rounded-lg shadow-2xl overflow-hidden flex flex-col relative text-gray-100 animate-fade-in-up">

                <div class="absolute top-4 right-4 flex gap-4 z-20">
                    <button x-show="isMyProfile" @click="editMode = true" class="text-white hover:text-blue-400 drop-shadow-md">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg>
                    </button>
                    <button @click="closeProfileModal()" class="text-white hover:text-red-400 drop-shadow-md">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>

                <div x-show="!editMode" class="flex flex-col">
                    <div class="relative pb-6 bg-gradient-to-b from-[#1c242f] to-[#242f3d]">
                        <div class="w-32 h-32 mx-auto mt-8 rounded-full bg-blue-600 flex items-center justify-center text-4xl font-bold shadow-lg overflow-hidden border-2 border-transparent">
                            <img x-show="viewProfileData.avatar" :src="viewProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!viewProfileData.avatar" x-text="viewProfileData.first_name ? viewProfileData.first_name[0] : ''"></span>
                        </div>
                        <div class="text-center mt-4">
                            <div class="text-xl font-bold flex items-center justify-center gap-2">
                                 <span x-text="viewProfileData.first_name + ' ' + viewProfileData.last_name"></span>
                                <template x-if="viewProfileData.is_admin"><span class="admin-badge">Admin</span></template>
                            </div>
                             <div class="text-sm mt-1" :class="viewProfileData.is_online ? 'text-blue-400' : 'text-gray-400'" 
                                 x-text="viewProfileData.is_online ? 'в сети' : 'был(а) ' + (viewProfileData.last_seen || 'недавно')"></div>
                        </div>
                    </div>

                    <div class="px-6 pb-6 space-y-4">
                        <template x-if="viewProfileData.phone">
                            <div class="border-b border-gray-700 pb-2">
                                <div class="text-[15px] font-medium" x-text="viewProfileData.phone"></div>
                                <div class="text-xs text-gray-500">Телефон</div>
                            </div>
                        </template>

                        <template x-if="viewProfileData.about_me">
                             <div class="border-b border-gray-700 pb-2">
                                <div class="text-[15px] whitespace-pre-wrap" x-text="viewProfileData.about_me"></div>
                                <div class="text-xs text-gray-500">О себе</div>
                             </div>
                        </template>

                        <div class="border-b border-gray-700 pb-2">
                            <div class="text-[15px] text-blue-400" x-text="'@' + viewProfileData.username"></div>
                            <div class="text-xs text-gray-500">Имя пользователя</div>
                        </div>

                        <template x-if="viewProfileData.formatted_bday">
                            <div class="border-b border-gray-700 pb-2">
                                <div class="text-[15px]" x-text="viewProfileData.formatted_bday"></div>
                                <div class="text-xs text-gray-500">День рождения</div>
                            </div>
                        </template>

                        <template x-if="!isMyProfile && !viewProfileData.phone && !viewProfileData.about_me && !viewProfileData.formatted_bday">
                            <div class="text-center text-gray-500 text-sm mt-4 italic">Дополнительная информация скрыта или не указана</div>
                        </template>
                    </div>
                </div>

                <div x-show="editMode" class="p-6 overflow-y-auto max-h-[80vh]">
                    <h3 class="text-lg font-bold mb-4 text-blue-400">Редактирование профиля</h3>

                    <div class="flex flex-col items-center mb-4">
                        <div class="w-24 h-24 rounded-full bg-blue-600 mb-2 flex items-center justify-center text-3xl font-bold overflow-hidden relative group">
                            <img x-show="editProfileData.avatar" :src="editProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!editProfileData.avatar" x-text="editProfileData.first_name[0]"></span>

                            <label class="absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center cursor-pointer transition">
                                <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 16V8a2 2 0 012-2h3l1-2h6l1 2h3a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 13a3 3 0 100-6 3 3 0 000 6z"></path></svg>
                                <input type="file" class="hidden" accept="image/*" @change="handleAvatarSelect">
                            </label>
                        </div>
                        <div class="text-xs text-gray-400">Нажмите для изменения фото</div>
                    </div>

                    <div class="space-y-4">
                         <div>
                            <label class="text-xs text-gray-400">Имя</label>
                            <input type="text" x-model="editProfileData.first_name" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-xs text-gray-400">Фамилия</label>
                            <input type="text" x-model="editProfileData.last_name" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-xs text-gray-400">Имя пользователя (никнейм)</label>
                            <input type="text" x-model="editProfileData.username" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-xs text-gray-400">День рождения</label>
                            <div class="flex gap-2">
                                <input type="number" x-model="editProfileData.birth_day" placeholder="День" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                                <input type="number" x-model="editProfileData.birth_month" placeholder="Мес (1-12)" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                                <input type="number" x-model="editProfileData.birth_year" placeholder="Год" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                            </div>
                        </div>

                        <div>
                            <label class="text-xs text-gray-400">Телефон</label>
                            <input type="text" x-model="editProfileData.phone" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500">
                        </div>
                        <div>
                            <label class="text-xs text-gray-400">О себе</label>
                            <textarea x-model="editProfileData.about_me" rows="2" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500"></textarea>
                        </div>

                        <div class="mt-4 pt-4 border-t border-gray-700">
                            <h4 class="text-sm font-semibold mb-2 text-gray-300">Настройки приватности</h4>
                            <p class="text-[10px] text-gray-500 mb-2">Отметьте, что могут видеть другие пользователи (Аватар и Ник видны всегда)</p>

                            <label class="flex items-center gap-2 mb-1">
                                <input type="checkbox" x-model="editProfileData.show_phone" class="rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500">
                                <span class="text-sm text-gray-300">Показывать Телефон</span>
                            </label>
                            <label class="flex items-center gap-2 mb-1">
                                <input type="checkbox" x-model="editProfileData.show_about" class="rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500">
                                <span class="text-sm text-gray-300">Показывать "О себе"</span>
                            </label>
                            <label class="flex items-center gap-2">
                                <input type="checkbox" x-model="editProfileData.show_birth_date" class="rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500">
                                <span class="text-sm text-gray-300">Показывать День рождения</span>
                            </label>
                        </div>

                        <div class="mt-4 pt-4 border-t border-gray-700">
                             <h4 class="text-sm font-semibold mb-2 text-gray-300">Смена пароля</h4>
                             <input type="password" x-model="editProfileData.new_password" placeholder="Новый пароль (оставьте пустым если нет)" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500">
                        </div>

                        <div class="flex gap-2 pt-4">
                            <button @click="saveProfile()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-2 rounded text-sm font-bold transition">Сохранить</button>
                            <button @click="editMode = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2 rounded text-sm font-bold transition">Отмена</button>
                        </div>
                    </div>
                </div>

            </div>
        </div>

    </div>

    <script>
        function messengerApp() {
            return {
                socket: null,
                myId: {{ current_user.id }},
                chats: [],
                searchQuery: '',
                searchResults: [],
                currentChat: null,
                messages: [],
                newMessage: '',
                imagePreview: null,
                typing: {},

                // Профиль
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
                    this.socket.on('new_message', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            if(data.sender_id !== this.myId) {
                                fetch('/api/chat/' + data.chat_id + '/messages').then(res => res.json()).then(msgs => {
                                    this.messages = msgs;
                                    this.scrollToBottom();
                                });
                            } else {
                                this.messages.push(data);
                                this.scrollToBottom();
                            }
                        }
                        this.loadChats();
                    });
                    this.socket.on('messages_read', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            this.messages.forEach(m => {
                                if (m.sender_id === this.myId) m.is_read = true;
                            });
                        }
                        this.loadChats();
                    });
                    this.socket.on('typing_status', (data) => {
                        this.typing[data.chat_id] = data.is_typing;
                        setTimeout(() => { this.typing[data.chat_id] = false }, 3000);
                    });
                    this.socket.on('status_update', (data) => {
                         let chat = this.chats.find(c => c.partner_id === data.user_id);
                         if (chat) {
                             chat.is_online = data.status === 'online';
                             if(data.last_seen) chat.last_seen = data.last_seen;
                         }
                         if (this.currentChat && this.currentChat.partner_id === data.user_id) {
                             this.currentChat.is_online = data.status === 'online';
                             if(data.last_seen) this.currentChat.last_seen = data.last_seen;
                         }
                         if (this.viewProfileData.id === data.user_id) {
                             this.viewProfileData.is_online = data.status === 'online';
                             if(data.last_seen) this.viewProfileData.last_seen = data.last_seen;
                         }
                    });
                },

                async fetchMyProfile() {
                    const res = await fetch('/api/profile/me');
                    this.myProfileData = await res.json();
                },

                openMyProfile() {
                    this.isMyProfile = true;
                    this.editMode = false;
                    this.viewProfileData = { ...this.myProfileData };
                    this.editProfileData = { ...this.myProfileData, new_password: '' };
                    this.showProfileModal = true;
                },

                async openUserProfile(userId) {
                    if(userId === this.myId) {
                        this.openMyProfile();
                        return;
                    }
                    this.isMyProfile = false;
                    this.editMode = false;
                    const res = await fetch('/api/profile/' + userId);
                    this.viewProfileData = await res.json();
                    this.showProfileModal = true;
                },

                closeProfileModal() {
                    this.showProfileModal = false;
                    this.editMode = false;
                },

                handleAvatarSelect(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (e) => { this.editProfileData.avatar = e.target.result; };
                    reader.readAsDataURL(file);
                },

                async saveProfile() {
                    const res = await fetch('/api/profile/me', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.editProfileData)
                    });
                    if(res.ok) {
                        await this.fetchMyProfile();
                        this.viewProfileData = { ...this.myProfileData };
                        this.editMode = false;
                    }
                },

                async loadChats() {
                    const res = await fetch('/api/chats');
                    this.chats = await res.json();
                },

                async searchUsers() {
                    let q = this.searchQuery.trim();
                    if (!q) { this.searchResults = []; return; }

                    if (q.startsWith('@') && q.length < 4) { this.searchResults = []; return; }
                    if (!q.startsWith('@') && q.length < 3) { this.searchResults = []; return; }

                    const res = await fetch('/api/search_users?q=' + encodeURIComponent(q));
                    this.searchResults = await res.json();
                },

                async startChat(userId) {
                    const res = await fetch('/api/chat/start/' + userId, { method: 'POST' });
                    const chatData = await res.json();
                    this.searchQuery = '';
                    this.searchResults = [];
                    await this.loadChats();
                    const chat = this.chats.find(c => c.chat_id === chatData.chat_id);
                    if (chat) this.openChat(chat);
                },

                async openChat(chat) {
                    this.currentChat = chat;
                    const res = await fetch('/api/chat/' + chat.chat_id + '/messages');
                    this.messages = await res.json();
                    this.scrollToBottom();
                },

                handleImageSelect(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (e) => { this.imagePreview = e.target.result; };
                    reader.readAsDataURL(file);
                },

                sendMessage() {
                    if (!this.newMessage.trim() && !this.imagePreview) return;
                    const payload = {
                        chat_id: this.currentChat.chat_id,
                        text: this.newMessage.trim(),
                        image_base64: this.imagePreview
                    };
                    this.socket.emit('send_message', payload);

                    this.newMessage = '';
                    this.imagePreview = null;
                },

                sendTyping() {
                    if (this.currentChat) {
                        this.socket.emit('typing', { chat_id: this.currentChat.chat_id });
                    }
                },

                scrollToBottom() {
                    setTimeout(() => {
                        const box = document.getElementById('messagesBox');
                        if (box) box.scrollTop = box.scrollHeight;
                    }, 100);
                }
            }
        }
    </script>
</body>
</html>
"""

ADMIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="container mx-auto p-6 pt-10">
        <div class="flex justify-between items-center mb-8">
            <h1 class="text-3xl font-bold text-white">Панель Администратора</h1>
            <a href="{{ url_for('index') }}" class="text-blue-400 hover:text-blue-300 transition">&larr; В мессенджер</a>
        </div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="bg-blue-500/20 text-blue-300 p-3 rounded mb-4 text-sm border border-blue-500">
              {% for message in messages %}{{ message }}<br>{% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        <div class="bg-gray-800 rounded-xl shadow-xl border border-gray-700 overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-900 border-b border-gray-700 text-gray-400 uppercase text-xs">
                        <th class="p-4">ID</th>
                        <th class="p-4">Пользователь / Ник</th>
                        <th class="p-4">Статус</th>
                        <th class="p-4 text-right">Действия</th>
                    </tr>
                </thead>
                <tbody class="text-sm">
                    {% for u in users %}
                    <tr class="border-b border-gray-700 hover:bg-gray-750 transition">
                        <td class="p-4 text-gray-500">#{{ u.id }}</td>
                        <td class="p-4">
                            <div class="font-semibold text-white flex items-center gap-2">
                                {{ u.first_name }} {{ u.last_name }}
                                {% if u.is_admin %}<span class="admin-badge">Admin</span>{% endif %}
                            </div>
                            <div class="text-xs text-blue-400">@{{ u.username }}</div>
                        </td>
                        <td class="p-4 text-gray-500">
                            {% if u.id in connected %} <span class="text-blue-500 font-bold">В сети</span> 
                            {% else %} Был(а) {{ u.last_seen.strftime('%H:%M %d.%m') if u.last_seen else '-' }}
                            {% endif %}
                        </td>
                        <td class="p-4 text-right space-x-2">
                            {% if u.id != current_user.id %}
                                {% if u.is_admin %}
                                    {% if current_user.promoted_by_id == u.id %}
                                        <span class="text-gray-600 text-xs italic" title="Этот администратор назначил вас.">Недоступно</span>
                                    {% else %}
                                        <a href="{{ url_for('admin_action', target_id=u.id, action='demote') }}" class="inline-block bg-red-900/50 hover:bg-red-800 text-red-300 border border-red-700 px-3 py-1.5 rounded text-xs transition">Разжаловать</a>
                                    {% endif %}
                                {% else %}
                                    <a href="{{ url_for('admin_action', target_id=u.id, action='promote') }}" class="inline-block bg-green-900/50 hover:bg-green-800 text-green-300 border border-green-700 px-3 py-1.5 rounded text-xs transition">Назначить Админом</a>
                                {% endif %}

                                <a href="{{ url_for('impersonate', target_id=u.id) }}" class="inline-block bg-blue-600 hover:bg-blue-500 text-white px-3 py-1.5 rounded text-xs transition shadow">Войти как</a>
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
</body>
</html>
"""

# ==========================================
# МАРШРУТЫ (АВТОРИЗАЦИЯ)
# ==========================================
@app.route('/logo.png')
def serve_logo():
    return send_from_directory(os.getcwd(), 'logo.png')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and not session.get('original_admin_id'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username')
        password = request.form.get('password')

        if action == 'register':
            if User.query.filter_by(username=username).first():
                flash('Пользователь с таким логином уже существует')
                return redirect(url_for('login'))

            new_user = User(
                username=username,
                password=password,
                first_name=request.form.get('first_name'),
                last_name=request.form.get('last_name'),
                class_name=request.form.get('class_name'),
                last_seen=datetime.utcnow()
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for('index'))

        elif action == 'login':
            clean_username = username.lstrip('@')
            user = User.query.filter_by(username=clean_username, password=password).first()
            if user:
                login_user(user)
                return redirect(url_for('index'))
            flash('Неверный логин или пароль')

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
@login_required
def logout():
    session.pop('original_admin_id', None)
    current_user.last_seen = datetime.utcnow()
    db.session.commit()
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template_string(APP_TEMPLATE)

# ==========================================
# API ДЛЯ ПРОФИЛЯ
# ==========================================
@app.route('/api/profile/me', methods=['GET', 'POST'])
@login_required
def my_profile():
    if request.method == 'POST':
        data = request.json
        current_user.first_name = data.get('first_name', current_user.first_name)
        current_user.last_name = data.get('last_name', current_user.last_name)

        new_username = data.get('username')
        if new_username:
            if not new_username.startswith('@'):
                new_username = '@' + new_username
            current_user.username = new_username

        b_day = data.get('birth_day')
        b_month = data.get('birth_month')
        b_year = data.get('birth_year')
        if b_day and b_month and b_year:
            current_user.birth_date = f"{b_day}.{b_month}.{b_year}"

        current_user.phone = data.get('phone')
        current_user.about_me = data.get('about_me')
        if data.get('avatar'): current_user.avatar_url = data.get('avatar')

        current_user.show_phone = data.get('show_phone', False)
        current_user.show_about = data.get('show_about', True)
        current_user.show_birth_date = data.get('show_birth_date', False)

        new_pwd = data.get('new_password')
        if new_pwd and new_pwd.strip() != "":
            current_user.password = new_pwd.strip()

        db.session.commit()
        return jsonify({'status': 'ok'})

    bd = current_user.birth_date
    b_day, b_month, b_year = "", "", ""
    if bd and "." in bd:
        parts = bd.split(".")
        if len(parts) == 3:
            b_day, b_month, b_year = parts

    return jsonify({
        'id': current_user.id,
        'first_name': current_user.first_name,
        'last_name': current_user.last_name,
        'username': current_user.username,
        'avatar': current_user.avatar_url,
        'phone': current_user.phone,
        'about_me': current_user.about_me,
        'birth_day': b_day,
        'birth_month': b_month,
        'birth_year': b_year,
        'formatted_bday': format_bday(current_user.birth_date),
        'show_phone': current_user.show_phone,
        'show_about': current_user.show_about,
        'show_birth_date': current_user.show_birth_date,
        'is_admin': current_user.is_admin,
        'is_online': True
    })

@app.route('/api/profile/<int:user_id>')
@login_required
def get_user_profile(user_id):
    user = User.query.get_or_404(user_id)
    last_seen_str = user.last_seen.strftime('%H:%M') if user.last_seen else ''

    data = {
        'id': user.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'username': user.username,
        'avatar': user.avatar_url,
        'is_admin': user.is_admin,
        'is_online': user.id in connected_users,
        'last_seen': last_seen_str,
        'phone': user.phone if user.show_phone else None,
        'about_me': user.about_me if user.show_about else None,
        'formatted_bday': format_bday(user.birth_date) if user.show_birth_date else None
    }
    return jsonify(data)

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
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == cid,
                                                  ChatParticipant.user_id != current_user.id).first()
        if not partner_cp: continue

        partner = User.query.get(partner_cp.user_id)
        last_msg = Message.query.filter_by(chat_id=cid).order_by(Message.timestamp.desc()).first()

        chats_data.append({
            'chat_id': cid,
            'partner_id': partner.id,
            'partner_name': f"{partner.first_name} {partner.last_name}",
            'partner_avatar': partner.avatar_url,
            'partner_is_admin': partner.is_admin,
            'last_message': last_msg.text if last_msg else ('[Фото]' if last_msg and last_msg.image_base64 else ''),
            'last_time': last_msg.timestamp.strftime('%H:%M') if last_msg else '',
            'is_online': partner.id in connected_users,
            'last_seen': partner.last_seen.strftime('%H:%M') if partner.last_seen else ''
        })
    return jsonify(chats_data)

@app.route('/api/search_users')
@login_required
def search_users():
    q = request.args.get('q', '').strip()
    if not q: return jsonify([])

    if q.startswith('@'):
        q = q[1:]
        users = User.query.filter(User.id != current_user.id, User.username.ilike(f'%{q}%')).limit(10).all()
    else:
        users = User.query.filter(
            (User.id != current_user.id) &
            (User.first_name.ilike(f'%{q}%') | User.last_name.ilike(f'%{q}%'))
        ).limit(10).all()

    return jsonify([{
        'id': u.id,
        'first_name': u.first_name,
        'last_name': u.last_name,
        'username': u.username,
        'avatar': u.avatar_url,
        'is_admin': u.is_admin
    } for u in users])

@app.route('/api/chat/start/<int:target_id>', methods=['POST'])
@login_required
def start_chat(target_id):
    my_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=current_user.id).all())
    target_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=target_id).all())
    common = my_chats.intersection(target_chats)

    if common:
        chat_id = list(common)[0]
    else:
        new_chat = Chat(type='private')
        db.session.add(new_chat)
        db.session.commit()
        chat_id = new_chat.id
        db.session.add_all([
            ChatParticipant(chat_id=chat_id, user_id=current_user.id),
            ChatParticipant(chat_id=chat_id, user_id=target_id),
            Contact(user_id=current_user.id, contact_id=target_id),
            Contact(user_id=target_id, contact_id=current_user.id)
        ])
        db.session.commit()
    return jsonify({'chat_id': chat_id})

@app.route('/api/chat/<int:chat_id>/messages')
@login_required
def get_messages(chat_id):
    unread_msgs = Message.query.filter(Message.chat_id == chat_id, Message.sender_id != current_user.id,
                                       Message.is_read == False).all()
    if unread_msgs:
        for msg in unread_msgs:
            msg.is_read = True
        db.session.commit()

        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id,
                                                  ChatParticipant.user_id != current_user.id).first()
        if partner_cp:
            socketio.emit('messages_read', {'chat_id': chat_id}, room=f"user_{partner_cp.user_id}")

    messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    return jsonify([{
        'id': m.id,
        'sender_id': m.sender_id,
        'text': m.text,
        'image_base64': m.image_base64,
        'time': m.timestamp.strftime('%H:%M'),
        'is_read': m.is_read
    } for m in messages])

# ==========================================
# АДМИН ПАНЕЛЬ
# ==========================================
@app.route('/admin')
@login_required
def admin_panel():
    is_real_admin = current_user.is_admin or 'original_admin_id' in session
    if not is_real_admin:
        flash("Доступ запрещен")
        return redirect(url_for('index'))

    users = User.query.order_by(User.id.desc()).all()
    return render_template_string(ADMIN_TEMPLATE, users=users, connected=connected_users)

@app.route('/admin/action/<int:target_id>/<action>')
@login_required
def admin_action(target_id, action):
    if not current_user.is_admin: return "Access denied", 403
    target = User.query.get_or_404(target_id)

    if action == 'promote':
        target.is_admin = True
        target.promoted_by_id = current_user.id
        flash(f'Пользователь @{target.username} назначен администратором.')
    elif action == 'demote':
        if current_user.promoted_by_id == target.id:
            flash('Ошибка: Нельзя разжаловать администратора, который назначил вас.')
        else:
            target.is_admin = False
            target.promoted_by_id = None
            flash(f'Пользователь @{target.username} разжалован.')

    db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/impersonate/<int:target_id>')
@login_required
def impersonate(target_id):
    if not current_user.is_admin and 'original_admin_id' not in session:
        return "Access denied", 403
    if 'original_admin_id' not in session:
        session['original_admin_id'] = current_user.id
    target_user = User.query.get_or_404(target_id)
    login_user(target_user)
    return redirect(url_for('index'))

@app.route('/admin/revert')
@login_required
def revert_impersonate():
    admin_id = session.pop('original_admin_id', None)
    if admin_id:
        admin_user = User.query.get(admin_id)
        if admin_user: login_user(admin_user)
    return redirect(url_for('admin_panel'))

# ==========================================
# SOCKET.IO СЕРВЕРНАЯ ЛОГИКА
# ==========================================
@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        user_room = f"user_{current_user.id}"
        join_room(user_room)
        connected_users[current_user.id] = request.sid

        current_user.last_seen = datetime.utcnow()
        db.session.commit()
        emit('status_update', {'user_id': current_user.id, 'status': 'online'}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        if current_user.id in connected_users:
            del connected_users[current_user.id]

        u = User.query.get(current_user.id)
        if u:
            u.last_seen = datetime.utcnow()
            db.session.commit()
            last_time = u.last_seen.strftime('%H:%M')
            emit('status_update', {'user_id': current_user.id, 'status': 'offline', 'last_seen': last_time}, broadcast=True)

@socketio.on('typing')
def handle_typing(data):
    chat_id = data.get('chat_id')
    partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id,
                                              ChatParticipant.user_id != current_user.id).first()
    if partner_cp:
        emit('typing_status', {'chat_id': chat_id, 'is_typing': True}, room=f"user_{partner_cp.user_id}")

@socketio.on('send_message')
def handle_message(data):
    chat_id = data.get('chat_id')
    msg = Message(chat_id=chat_id, sender_id=current_user.id, text=data.get('text', ''),
                  image_base64=data.get('image_base64'), is_read=False)
    db.session.add(msg)
    db.session.commit()

    msg_data = {
        'id': msg.id, 'chat_id': chat_id, 'sender_id': current_user.id,
        'text': msg.text, 'image_base64': msg.image_base64,
        'time': msg.timestamp.strftime('%H:%M'), 'is_read': False
    }

    for p in ChatParticipant.query.filter_by(chat_id=chat_id).all():
        emit('new_message', msg_data, room=f"user_{p.user_id}")

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И ЗАПУСК
# ==========================================
def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin', password='admin',
                first_name='Admin', last_name='System',
                is_admin=True, class_name='Administration',
                last_seen=datetime.utcnow()
            )
            db.session.add(admin)
            db.session.commit()
            print(">>> База инициализирована. Создан admin:admin")

init_db()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
