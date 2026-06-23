# ПАТЧ ДЛЯ GEVENT - ДОЛЖЕН БЫТЬ ПЕРВОЙ СТРОКОЙ
from gevent import monkey
monkey.patch_all()

import os
import json
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, flash, session, jsonify, render_template_string, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room
from sqlalchemy import text 

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
# ВСПОМОГАТЕЛЬНАЯ ЛОГИКА И ПРАВА
# ==========================================
def now_msk():
    return datetime.utcnow() + timedelta(hours=3)

def get_msk_today_str():
    return now_msk().strftime('%Y-%m-%d')

def format_last_seen_str(dt):
    if not dt:
        return "недавно"
    now = now_msk()
    if dt.date() == now.date():
        return f"в {dt.strftime('%H:%M')}"
    else:
        months = {1: "янв.", 2: "февр.", 3: "мар.", 4: "апр.", 5: "мая", 6: "июн.", 
                  7: "июл.", 8: "авг.", 9: "сен.", 10: "окт.", 11: "ноя.", 12: "дек."}
        m_str = months.get(dt.month, "")
        t_str = dt.strftime("%H:%M")
        return f"{dt.day} {m_str} {t_str}"

def format_bday(bd_str):
    if not bd_str or "." not in bd_str:
        return "Не указана"
    try:
        d, m, y = bd_str.split(".")
        months = {1: "янв.", 2: "февр.", 3: "мар.", 4: "апр.", 5: "мая", 6: "июня", 7: "июля", 8: "авг.", 9: "сент.", 10: "окт.", 11: "нояб.", 12: "дек."}
        m_str = months.get(int(m), m)
        return f"{d} {m_str} {y}г."
    except:
        return bd_str

def has_admin_priv():
    return current_user.is_admin or 'original_admin_id' in session

def can_see_deleted():
    return has_admin_priv() or current_user.perm_deleted_messages

def can_see_edits():
    return has_admin_priv() or current_user.is_moderator or current_user.perm_edit_history

def can_see_chatting():
    return has_admin_priv() or current_user.perm_see_chatting_with

def can_ban_users():
    return has_admin_priv() or current_user.perm_ban_users

def check_user_banned(u):
    if not u or not u.banned_until:
        return False, None, False
    if u.banned_until > now_msk():
        is_perm = u.banned_until.year >= 9999
        return True, u.banned_until, is_perm
    u.banned_until = None
    db.session.commit()
    return False, None, False

def is_allowed_to_see(target_user, privacy_setting, viewer_id):
    if viewer_id == target_user.id or has_admin_priv():
        return True
    if privacy_setting == 'nobody':
        return False
    if privacy_setting == 'contacts':
        c = Contact.query.filter_by(user_id=target_user.id, contact_id=viewer_id, is_explicit=True).first()
        return c is not None
    return True

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

    avatar_url = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    about_me = db.Column(db.Text, nullable=True)
    birth_date = db.Column(db.String(20), nullable=True)
    last_seen = db.Column(db.DateTime, default=now_msk)

    privacy_phone = db.Column(db.String(20), default='everyone')
    privacy_bday = db.Column(db.String(20), default='everyone')
    privacy_last_seen = db.Column(db.String(20), default='everyone')

    is_admin = db.Column(db.Boolean, default=False)
    is_moderator = db.Column(db.Boolean, default=False)
    perm_edit_history = db.Column(db.Boolean, default=False)
    perm_deleted_messages = db.Column(db.Boolean, default=False)
    perm_see_chatting_with = db.Column(db.Boolean, default=False)
    perm_ban_users = db.Column(db.Boolean, default=False)
    perm_grant_gifts = db.Column(db.Boolean, default=False)      
    perm_grant_lightnings = db.Column(db.Boolean, default=False) 

    banned_until = db.Column(db.DateTime, nullable=True)
    lightnings = db.Column(db.Integer, default=0)
    q3_claimed = db.Column(db.Boolean, default=False)

    promoted_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=now_msk)

class Contact(db.Model):
    __tablename__ = 'contacts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    custom_name = db.Column(db.String(50), nullable=True)
    is_explicit = db.Column(db.Boolean, default=False)
    added_at = db.Column(db.DateTime, default=now_msk)

class PersonalBlock(db.Model):
    __tablename__ = 'personal_blocks'
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=now_msk)

class Chat(db.Model):
    __tablename__ = 'chats'
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), default='private')
    created_at = db.Column(db.DateTime, default=now_msk)
    # Для групп
    name = db.Column(db.String(100), nullable=True)
    avatar_url = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    global_send_text = db.Column(db.Boolean, default=True)
    global_send_photos = db.Column(db.Boolean, default=True)
    global_send_voice = db.Column(db.Boolean, default=True)
    global_send_emoji = db.Column(db.Boolean, default=True)
    global_add_members = db.Column(db.Boolean, default=True)
    global_change_profile = db.Column(db.Boolean, default=False)

class ChatParticipant(db.Model):
    __tablename__ = 'chat_participants'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # Роли и права в группе
    is_admin = db.Column(db.Boolean, default=False)
    role_tag = db.Column(db.String(50), nullable=True)
    perm_change_profile = db.Column(db.Boolean, default=False)
    perm_delete_msgs = db.Column(db.Boolean, default=False)
    perm_ban_users = db.Column(db.Boolean, default=False)
    perm_change_tags = db.Column(db.Boolean, default=False)
    perm_assign_admins = db.Column(db.Boolean, default=False)

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=True)
    image_base64 = db.Column(db.Text, nullable=True)
    video_base64 = db.Column(db.Text, nullable=True)
    file_base64 = db.Column(db.Text, nullable=True)
    file_name = db.Column(db.Text, nullable=True)
    voice_base64 = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=now_msk)
    is_read = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    is_edited = db.Column(db.Boolean, default=False)
    original_text = db.Column(db.Text, nullable=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True)
    forwarded_from_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

class GiftDefinition(db.Model):
    __tablename__ = 'gift_definitions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    image_filename = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)

class UserGift(db.Model):
    __tablename__ = 'user_gifts'
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    gift_def_id = db.Column(db.Integer, db.ForeignKey('gift_definitions.id'), nullable=False)
    is_pinned = db.Column(db.Boolean, default=False)
    slot_index = db.Column(db.Integer, nullable=True) 
    is_for_sale = db.Column(db.Boolean, default=False)
    sale_price = db.Column(db.Integer, nullable=True)
    acquired_at = db.Column(db.DateTime, default=now_msk)
    gift_def = db.relationship('GiftDefinition', backref='instances')
    owner = db.relationship('User', backref='gifts')

class QuestProgress(db.Model):
    __tablename__ = 'quest_progress'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date_str = db.Column(db.String(10), nullable=False)
    messages_sent = db.Column(db.Integer, default=0)
    replies_received = db.Column(db.Integer, default=0)
    photos_sent = db.Column(db.Integer, default=0)
    q1_claimed = db.Column(db.Boolean, default=False)
    q2_claimed = db.Column(db.Boolean, default=False)
    q4_claimed = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

connected_users = {}
active_chat_views = {} 

def get_or_create_quest(user_id):
    today = get_msk_today_str()
    q = QuestProgress.query.filter_by(user_id=user_id, date_str=today).first()
    if not q:
        q = QuestProgress(user_id=user_id, date_str=today)
        db.session.add(q)
        db.session.commit()
    return q

def hook_track_message(sender_id, reply_to_msg_id=None, has_photo=False):
    try:
        q_sender = get_or_create_quest(sender_id)
        q_sender.messages_sent += 1
        if has_photo: q_sender.photos_sent += 1
        db.session.commit()
        if reply_to_msg_id:
            rm = Message.query.get(reply_to_msg_id)
            if rm and rm.sender_id != sender_id:
                q_rec = get_or_create_quest(rm.sender_id)
                q_rec.replies_received += 1
                db.session.commit()
    except Exception as e:
        pass

# ==========================================
# HTML ШАБЛОНЫ
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
            background-color: #3f2224; border: 1px solid #cc3033; color: #f76d70;
            padding: 0.1rem 0.4rem; border-radius: 0.375rem; font-size: 0.7rem; font-weight: 600; display: inline-block; line-height: 1;
        }
        .mod-badge {
            background-color: #1a3f20; border: 1px solid #28a745; color: #4ade80;
            padding: 0.1rem 0.4rem; border-radius: 0.375rem; font-size: 0.7rem; font-weight: 600; display: inline-block; line-height: 1;
        }
        .group-tag-admin { background-color: #0c4a6e; border: 1px solid #0284c7; color: #7dd3fc; padding: 0.1rem 0.3rem; border-radius: 0.25rem; font-size: 0.65rem; font-weight: bold; }
        .group-tag-user { background-color: #374151; border: 1px solid #4b5563; color: #d1d5db; padding: 0.1rem 0.3rem; border-radius: 0.25rem; font-size: 0.65rem; font-weight: bold; }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 h-[100dvh] max-h-[100dvh] w-screen overflow-hidden flex flex-col font-sans fixed inset-0 select-none">
    {% if session.get('original_admin_id') %}
    <div class="bg-red-600 text-white text-center py-2 text-xs md:text-sm font-bold flex justify-center items-center gap-2 md:gap-4 z-50 shadow-lg px-2 flex-shrink-0">
        Внимание: Режим от лица {{ current_user.first_name }}!
        <a href="{{ url_for('revert_impersonate') }}" class="bg-white text-red-600 px-2 py-1 rounded-md hover:bg-gray-200 transition">Вернуться</a>
    </div>
    {% endif %}
"""

BANNED_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex items-center justify-center bg-gray-900 px-4">
        <div class="bg-gray-800 border border-red-950 p-8 rounded-2xl shadow-2xl max-w-md w-full flex flex-col items-center text-center">
            <div class="w-20 h-20 rounded-full border-4 border-red-600 bg-black flex items-center justify-center shadow-lg mb-6">
                <span class="text-white font-black text-4xl leading-none">!</span>
            </div>
            <h2 class="text-2xl font-bold text-red-500 mb-4">Вход ограничен</h2>
            <p class="text-gray-200 text-base md:text-lg mb-6 font-medium leading-relaxed">
                {% if is_permanent %}Вы были заблокированы навсегда
                {% else %}Вы были заблокированы до<br>
                <span class="font-mono font-bold text-red-400 text-lg block mt-2">{{ ban_date_str }}</span>{% endif %}
            </p>
            <div class="w-full border-t border-gray-700/80 pt-4 mt-2">
                <span class="text-xs text-gray-400 tracking-wider">Администрация You`Me</span>
            </div>
            <div class="mt-6">
                <a href="{{ url_for('logout') }}" class="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-4 py-2 rounded-full transition">Выйти из аккаунта</a>
            </div>
        </div>
    </div>
</body></html>
"""

MICRO_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex flex-col bg-gray-900 text-gray-100 p-4 md:p-6 max-w-4xl mx-auto w-full h-full select-none" x-data="{ step: 1, total: 5 }">
        <div class="flex items-center justify-between pb-4 mb-4 border-b border-gray-800 flex-shrink-0">
            <a href="{{ url_for('index') }}" class="flex items-center gap-2 text-blue-400 hover:text-blue-300 transition font-medium text-sm md:text-base">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path></svg>
                <span>Назад на главную</span>
            </a>
            <h1 class="text-base md:text-xl font-bold text-white">Инструкция по включению микрофона</h1>
            <div class="w-20 hidden md:block"></div>
        </div>
        <div class="flex-1 flex flex-col items-center justify-center min-h-0 relative bg-black/40 rounded-xl border border-gray-800 p-2 md:p-4 overflow-hidden">
            <img :src="'/screen' + step" alt="Шаг инструкции" class="max-w-full max-h-full object-contain rounded-lg shadow-2xl transition-all duration-300" onerror="this.src='https://placehold.co/600x400/1e293b/ffffff?text=Скриншот+' + step + '+отсутствует'">
        </div>
        <div class="flex items-center justify-between pt-4 mt-4 border-t border-gray-800 flex-shrink-0 gap-2 md:gap-4">
            <button @click="if(step > 1) step--" :disabled="step === 1" :class="step === 1 ? 'opacity-30 cursor-not-allowed bg-gray-800 text-gray-500' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg'" class="flex items-center gap-1 md:gap-2 px-4 md:px-6 py-2.5 rounded-full font-bold text-xs md:text-sm transition">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg><span>Назад</span>
            </button>
            <div class="font-mono text-xs md:text-base font-bold text-gray-400 bg-gray-800/80 px-4 py-1.5 rounded-full border border-gray-700">
                Шаг <span class="text-white" x-text="step"></span> из <span x-text="total"></span>
            </div>
            <button @click="if(step < total) step++" :disabled="step === total" :class="step === total ? 'opacity-30 cursor-not-allowed bg-gray-800 text-gray-500' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg'" class="flex items-center gap-1 md:gap-2 px-4 md:px-6 py-2.5 rounded-full font-bold text-xs md:text-sm transition">
                <span>Вперед</span><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
            </button>
        </div>
    </div>
</body></html>
"""

LOGIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex items-center justify-center bg-gray-900 px-4" x-data="{ isLogin: true }">
        <div class="bg-gray-800 p-6 md:p-8 rounded-xl shadow-2xl w-full max-w-md border border-gray-700">
            <h1 class="text-3xl font-bold text-center text-blue-500 mb-6 font-serif tracking-widest">You`me</h1>
            {% with messages = get_flashed_messages() %}{% if messages %}
                <div class="bg-red-500/20 border border-red-500 text-red-200 p-3 rounded mb-4 text-center text-sm">
                  {% for message in messages %}{{ message }}<br>{% endfor %}
                </div>
            {% endif %}{% endwith %}
            <form x-show="isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4">
                <input type="hidden" name="action" value="login">
                <div><input type="text" name="username" placeholder="Логин (@username)" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500"></div>
                <div><input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500"></div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 md:py-2 rounded transition">Войти</button>
                <p class="text-center text-sm text-gray-400 mt-4">Нет аккаунта? <a href="#" @click.prevent="isLogin = false" class="text-blue-400 hover:underline">Регистрация</a></p>
            </form>
            <form x-show="!isLogin" action="{{ url_for('login') }}" method="POST" class="space-y-4" style="display: none;">
                <input type="hidden" name="action" value="register">
                <div><input type="text" name="username" placeholder="Придумайте логин (только латиница)" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500"></div>
                <div><input type="password" name="password" placeholder="Пароль" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500"></div>
                <div class="flex flex-col gap-4 md:gap-2">
                    <input type="text" name="first_name" placeholder="Имя" required class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                    <input type="text" name="last_name" placeholder="Фамилия (необязательно)" class="w-full bg-gray-700 border border-gray-600 rounded p-3 md:p-2 text-white focus:outline-none focus:border-blue-500">
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-3 md:py-2 rounded transition">Зарегистрироваться</button>
                <p class="text-center text-sm text-gray-400 mt-4">Уже есть аккаунт? <a href="#" @click.prevent="isLogin = true" class="text-blue-400 hover:underline">Войти</a></p>
            </form>
        </div>
    </div>
</body></html>
"""

APP_TEMPLATE = BASE_HTML_HEAD + """
    <div class="flex-1 flex overflow-hidden w-full h-full max-h-full" x-data="messengerApp()">

        <div class="bg-gray-900 border-r border-gray-800 flex-col flex-shrink-0 w-full md:w-80 h-full max-h-full relative" :class="currentChat ? 'hidden md:flex' : 'flex'">
             
            <div class="p-4 border-b border-gray-800 flex justify-between items-center flex-shrink-0 relative">
                <div class="flex items-center gap-3">
                    <div @click="openMyProfile()" class="w-10 h-10 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold cursor-pointer overflow-hidden shadow-md hover:ring-2 hover:ring-blue-400 transition">
                        <img x-show="myProfileData.avatar" :src="myProfileData.avatar" class="w-full h-full object-cover">
                        <span x-show="!myProfileData.avatar">{{ current_user.first_name[0] }}</span>
                    </div>
                    <img src="/logo.png" alt="You'Me" class="h-10 md:h-12 object-contain" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                    <div class="text-xl font-bold text-blue-500 tracking-wider" style="display:none;">You`me</div>
                </div>
                <div class="flex items-center gap-2">
                    <div @click="openQuestsModal()" class="flex items-center gap-1.5 bg-blue-500/15 hover:bg-blue-500/25 border border-blue-500/40 px-3 py-1 rounded-full cursor-pointer transition shadow-sm" title="Ежедневные задания">
                        <img src="/molniya.png" class="w-5 h-5 md:w-6 md:h-6 object-contain" alt="⚡">
                        <span class="text-white font-mono font-bold text-xs md:text-sm" x-text="myLightnings"></span>
                    </div>
                    {% if current_user.is_admin or current_user.is_moderator or current_user.perm_ban_users or current_user.perm_grant_gifts or current_user.perm_grant_lightnings or session.get('original_admin_id') %}
                    <a href="{{ url_for('admin_panel') }}" class="p-1 text-gray-400 hover:text-white" title="Панель Управления">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.427.738-3.2 2.23-2.47z"></path></svg>
                    </a>
                    {% endif %}
                    <a href="{{ url_for('logout') }}" class="p-1 text-gray-400 hover:text-red-500" title="Выйти">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                    </a>
                </div>
            </div>

            <div class="p-3 flex-shrink-0">
                <input type="text" autocomplete="new-password" spellcheck="false" x-model="searchQuery" @input.debounce.300ms="searchUsers()" placeholder="Поиск (@username или имя)..." class="w-full bg-gray-800 text-sm text-gray-200 rounded-full px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>

            <div class="flex-1 overflow-y-auto max-h-full">
                <template x-if="searchQuery.length > 0">
                    <div>
                        <div class="px-4 py-2 text-xs font-semibold text-gray-500 uppercase">Пользователи</div>
                        <template x-for="user in searchResults" :key="user.id">
                            <div @click="startChat(user.id)" class="flex items-center gap-3 px-4 py-3 hover:bg-gray-800 cursor-pointer transition">
                                <div class="w-10 h-10 rounded-full bg-gradient-to-tr from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold overflow-hidden flex-shrink-0">
                                    <img x-show="user.avatar" :src="user.avatar" class="w-full h-full object-cover">
                                    <span x-show="!user.avatar" x-text="user.first_name[0]"></span>
                                </div>
                                <div class="flex-1 min-w-0">
                                    <div class="flex items-center gap-2">
                                        <div class="text-sm font-semibold truncate" x-text="user.first_name + ' ' + (user.last_name || '')"></div>
                                        <template x-if="user.is_admin"><span class="admin-badge">Admin</span></template>
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
                                    <div class="w-full h-full rounded-full bg-gray-700 flex items-center justify-center text-white font-bold text-lg shadow-inner overflow-hidden" :class="chat.is_group ? 'bg-gradient-to-tr from-green-500 to-blue-500' : ''">
                                        <img x-show="chat.partner_avatar" :src="chat.partner_avatar" class="w-full h-full object-cover">
                                        <span x-show="!chat.partner_avatar && chat.is_group">👥</span>
                                        <span x-show="!chat.partner_avatar && !chat.is_group" x-text="chat.partner_name[0]"></span>
                                    </div>
                                    <div x-show="!chat.is_group && chat.is_online && !chat.partner_is_banned" class="absolute bottom-0 right-0 w-3.5 h-3.5 bg-blue-500 border-2 border-gray-900 rounded-full z-10"></div>
                                </div>

                                <div class="flex-1 min-w-0">
                                    <div class="flex justify-between items-center mb-1">
                                        <div class="text-sm font-semibold truncate flex items-center gap-2 pr-2">
                                            <span class="truncate" :class="chat.partner_is_banned ? 'line-through text-red-500' : ''" x-text="chat.partner_name"></span>
                                        </div>
                                        <div class="text-[10px] text-gray-500 whitespace-nowrap flex-shrink-0" x-text="chat.last_time"></div>
                                    </div>
                                    <div class="text-xs text-gray-400 truncate" :class="chat.custom_status ? 'text-blue-300 italic' : ''" x-text="chat.custom_status ? chat.custom_status : (chat.last_message || 'Нет сообщений')"></div>
                                </div>
                            </div>
                        </template>
                    </div>
                </template>
            </div>

            <button @click="showGroupCreateModal = true" class="absolute bottom-6 right-6 w-14 h-14 bg-blue-600 hover:bg-blue-500 text-white rounded-full flex items-center justify-center shadow-2xl transition-transform hover:scale-105 z-20">
                <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
            </button>
        </div>

        <div class="flex-1 flex-col relative bg-[#0f172a] bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] h-full w-full max-h-full overflow-hidden" style="background-blend-mode: overlay;" :class="currentChat ? 'flex' : 'hidden md:flex'">

            <div x-show="contextMenu.show" style="display:none;" @click.away="contextMenu.show = false" x-transition.opacity.duration.200ms class="fixed bg-gray-800 border border-gray-700 rounded-xl shadow-2xl z-50 flex flex-col w-48 text-sm overflow-hidden" :style="'top: ' + contextMenu.y + 'px; left: ' + contextMenu.x + 'px;'">
                <button @click="actionReply()" class="px-4 py-3 md:py-2 text-left hover:bg-gray-700 text-white transition">Ответить</button>
                <template x-if="contextMenu.msg && contextMenu.msg.sender_id === myId">
                    <button @click="actionEdit()" class="px-4 py-3 md:py-2 text-left hover:bg-gray-700 text-white transition border-t border-gray-700">Изменить</button>
                </template>
                <button @click="actionForward()" class="px-4 py-3 md:py-2 text-left hover:bg-gray-700 text-white transition border-t border-gray-700">Переслать</button>
                <template x-if="myProfileData.has_admin_priv || myProfileData.is_admin || myProfileData.can_see_deleted">
                    <button @click="actionShowHistory()" class="px-4 py-3 md:py-2 text-left hover:bg-gray-700 text-blue-400 border-t border-gray-700 transition">История</button>
                </template>
                <template x-if="contextMenu.canDelete">
                    <button @click="actionDelete()" class="px-4 py-3 md:py-2 text-left hover:bg-gray-700 text-red-500 border-t border-gray-700 transition font-bold">Удалить</button>
                </template>
            </div>

            <template x-if="!currentChat">
                <div class="flex-1 flex items-center justify-center text-gray-500">
                    <div class="bg-gray-900/60 px-4 py-2 rounded-full backdrop-blur-sm text-sm md:text-base">Выберите чат для начала общения</div>
                </div>
            </template>

            <template x-if="currentChat">
                <div class="flex-1 flex flex-col h-full w-full max-h-full overflow-hidden">
                    
                    <div class="h-16 px-3 md:px-6 bg-gray-900/95 backdrop-blur-md border-b border-gray-800 flex items-center justify-between shadow-sm z-10 flex-shrink-0">
                        <div class="flex items-center gap-2 md:gap-4 min-w-0 cursor-pointer" @click="currentChat.is_group ? openGroupProfile(currentChat.chat_id) : openUserProfile(currentChat.partner_id)">
                            <button @click.stop="closeChat()" class="md:hidden p-2 -ml-2 text-gray-400 hover:text-white transition flex-shrink-0">
                                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                            </button>
                            
                            <div class="w-10 h-10 rounded-full overflow-hidden flex items-center justify-center text-white flex-shrink-0" :class="currentChat.is_group ? 'bg-gradient-to-tr from-green-500 to-blue-500' : 'bg-gray-700'">
                                 <img x-show="currentChat.partner_avatar" :src="currentChat.partner_avatar" class="w-full h-full object-cover">
                                 <span x-show="!currentChat.partner_avatar && currentChat.is_group">👥</span>
                                 <span x-show="!currentChat.partner_avatar && !currentChat.is_group" x-text="currentChat.partner_name[0]"></span>
                            </div>
                            <div class="flex flex-col min-w-0">
                                <div class="flex items-center gap-2 min-w-0">
                                    <div class="text-white font-semibold text-sm md:text-base truncate flex items-center">
                                        <span x-text="currentChat.partner_name"></span>
                                        <template x-if="currentChat.partner_is_banned">
                                            <span class="text-red-700 font-bold ml-1 flex-shrink-0"> — Заблокирован</span>
                                        </template>
                                    </div>
                                </div>
                                <div class="text-[11px] md:text-xs flex items-center gap-1 truncate text-gray-400">
                                    <template x-if="currentChat.is_group">
                                        <span x-text="currentChat.member_count + ' участников'"></span>
                                    </template>
                                    <template x-if="!currentChat.is_group">
                                        <span :class="currentChat.partner_is_banned ? 'text-red-700 font-semibold' : (typing[currentChat.chat_id] ? 'text-blue-400 italic animate-pulse' : (currentChat.custom_status ? 'text-blue-300 font-semibold' : (currentChat.is_online ? 'text-blue-400' : 'text-gray-400')))" 
                                              x-text="currentChat.partner_is_banned ? 'заблокирован' : (typing[currentChat.chat_id] ? 'печатает...' : (currentChat.custom_status ? currentChat.custom_status : (currentChat.is_online ? 'в сети' : 'был(а) ' + (currentChat.last_seen || 'недавно'))))"></span>
                                    </template>
                                </div>
                            </div>
                        </div>

                        <template x-if="!currentChat.is_group">
                            <div class="relative flex items-center" x-data="{ menuOpen: false }">
                                <button @click="menuOpen = !menuOpen" class="p-2 text-gray-400 hover:text-white transition rounded-full hover:bg-gray-800">
                                    <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 24 24"><path d="M12 16a2 2 0 012 2 2 2 0 01-2 2 2 2 0 01-2-2 2 2 0 012-2m0-6a2 2 0 012 2 2 2 0 01-2 2 2 2 0 01-2-2 2 2 0 012-2z"></path></svg>
                                </button>
                                <div x-show="menuOpen" @click.away="menuOpen = false" x-transition.opacity.duration.200ms class="absolute right-0 top-full mt-2 w-48 bg-gray-800 rounded-2xl shadow-2xl border border-gray-700 py-2 z-50 text-sm font-medium">
                                    <button @click="menuOpen = false; openContactModal()" class="w-full text-left px-4 py-2 hover:bg-gray-700 text-white flex items-center gap-2">
                                        <span x-text="currentChat.is_explicit_contact ? 'Изменить контакт' : 'Добавить контакт'"></span>
                                    </button>
                                    <button @click="menuOpen = false; togglePersonalBlock()" class="w-full text-left px-4 py-2 hover:bg-red-950/50 text-red-400 border-t border-gray-700/80 mt-1 flex items-center gap-2">
                                        <span x-text="currentChat.i_blocked_partner ? 'Разблокировать' : 'Заблокировать'"></span>
                                    </button>
                                </div>
                            </div>
                        </template>
                    </div>

                    <div class="flex-1 overflow-y-auto p-4 md:p-6 space-y-4 max-h-full" id="messagesBox">
                        <template x-for="msg in messages" :key="msg.id">
                            <div class="flex w-full flex-col" :class="msg.sender_id === myId ? 'items-end' : 'items-start'">
                                <div class="max-w-[85%] md:max-w-[70%] rounded-2xl px-3 py-2 md:px-4 shadow-md relative group flex flex-col select-text"
                                     :class="msg.is_deleted ? 'bg-red-950/40 border border-red-900 text-red-200 rounded-sm' : (msg.sender_id === myId ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-gray-800 text-gray-100 rounded-tl-sm')"
                                     @contextmenu.prevent="openContextMenu($event, msg, false)"
                                     @touchstart="handleTouchStart($event, msg)"
                                     @touchend="handleTouchEnd()"
                                     @touchmove="handleTouchEnd()">

                                    <template x-if="currentChat.is_group && msg.sender_id !== myId && !msg.is_deleted">
                                        <div class="text-[11px] font-bold mb-1 flex items-center gap-1.5 cursor-pointer" @click.stop="openUserProfile(msg.sender_id)">
                                            <span class="text-indigo-400 hover:underline truncate" x-text="msg.sender_name"></span>
                                            <template x-if="msg.sender_tag">
                                                <span :class="(msg.sender_is_admin || msg.sender_is_owner) ? 'group-tag-admin' : 'group-tag-user'" x-text="msg.sender_tag"></span>
                                            </template>
                                        </div>
                                    </template>

                                    <template x-if="msg.forwarded_from_id">
                                        <div @click.stop="startChat(msg.forwarded_from_id)" class="text-[11px] text-blue-300 font-medium mb-1 border-b border-blue-500/20 pb-0.5 cursor-pointer hover:underline truncate">
                                            Переслано от: <span class="font-bold text-white" x-text="msg.forwarded_from_name"></span>
                                        </div>
                                    </template>

                                    <template x-if="msg.reply_to_id">
                                        <div class="bg-black/20 rounded-md px-2 py-1 mb-1 border-l-2 border-blue-400 text-[11px] text-gray-300 opacity-90 truncate">
                                            <span class="text-blue-400 font-bold block text-[9px] uppercase tracking-wide">Отвечено на:</span>
                                            <span x-text="msg.reply_text"></span>
                                        </div>
                                    </template>

                                    <template x-if="msg.image_base64">
                                        <img :src="msg.image_base64" class="rounded-lg mb-2 max-w-full h-auto cursor-pointer">
                                    </template>

                                    <template x-if="msg.video_base64">
                                        <video controls :src="msg.video_base64" class="rounded-lg mb-2 max-w-full h-auto"></video>
                                    </template>

                                    <template x-if="msg.file_base64">
                                        <a :href="msg.file_base64" :download="msg.file_name" class="flex items-center gap-2 bg-black/20 p-2 rounded-lg mb-2 hover:bg-black/30 transition text-blue-300 max-w-[200px] md:max-w-xs">
                                            <svg class="w-6 h-6 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                                            <span class="truncate text-sm font-medium" x-text="msg.file_name || 'Файл'"></span>
                                        </a>
                                    </template>

                                    <template x-if="msg.voice_base64">
                                        <div class="my-1">
                                            <audio controls :src="msg.voice_base64" class="max-w-[210px] md:max-w-[280px] h-9 outline-none"></audio>
                                        </div>
                                    </template>

                                    <template x-if="msg.text">
                                        <div class="text-[14px] md:text-[15px] leading-relaxed break-words" x-text="msg.text"></div>
                                    </template>

                                    <div class="text-[10px] text-right mt-1 flex items-center justify-end gap-1 opacity-70" :class="msg.is_deleted ? 'text-red-400' : (msg.sender_id === myId ? 'text-blue-200' : 'text-gray-400')">
                                        <template x-if="msg.is_edited && !msg.is_deleted">
                                            <span class="text-[9px] italic mr-1 text-gray-300">(изменено)</span>
                                        </template>
                                        <template x-if="msg.is_deleted">
                                            <span class="text-[9px] font-bold text-red-400 mr-1">(удалено)</span>
                                        </template>
                                        
                                        <span x-text="msg.time"></span>

                                        <template x-if="msg.sender_id === myId && !msg.is_deleted">
                                            <span class="font-bold text-[11px] ml-0.5">
                                                <template x-if="msg.is_pending">
                                                    <span class="text-yellow-300 animate-pulse" title="Отправка...">⏳</span>
                                                </template>
                                                <template x-if="!msg.is_pending">
                                                    <span :class="msg.is_read ? 'text-[#4da3ff]' : 'text-blue-200'" x-text="msg.is_read ? '✓✓' : '✓'"></span>
                                                </template>
                                            </span>
                                        </template>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>

                    <div class="bg-gray-900 border-t border-gray-800 w-full flex-shrink-0 pb-safe relative">
                        
                        <div x-show="replyToMessage" style="display:none;" x-transition.opacity class="bg-gray-800/80 p-2 px-4 flex justify-between items-center text-xs text-gray-300 border-b border-gray-700/50">
                            <div class="truncate flex items-center gap-1">
                                <span class="text-blue-400 font-bold uppercase text-[10px]">Ответить на:</span>
                                <span class="italic truncate max-w-xs" x-text="replyToMessage ? (replyToMessage.voice_base64 ? '[Голосовое]' : (replyToMessage.text || '[Фото/Файл]')) : ''"></span>
                            </div>
                            <button @click="replyToMessage = null" class="text-gray-400 hover:text-white font-bold text-sm px-1">✕</button>
                        </div>

                        <div x-show="editMessage" style="display:none;" x-transition.opacity class="bg-gray-800/80 p-2 px-4 flex justify-between items-center text-xs text-gray-300 border-b border-gray-700/50">
                            <div class="truncate flex items-center gap-1">
                                <span class="text-yellow-500 font-bold uppercase text-[10px]">Редактирование:</span>
                                <span class="italic truncate max-w-xs" x-text="editMessage ? editMessage.text : ''"></span>
                            </div>
                            <button @click="cancelEdit()" class="text-gray-400 hover:text-white font-bold text-sm px-1">✕</button>
                        </div>

                        <div x-show="imagePreview || videoPreview || filePreview" style="display:none;" x-transition.opacity class="p-2 bg-gray-800/50 border-b border-gray-700/50">
                            <div class="relative inline-block">
                                <template x-if="imagePreview">
                                    <img :src="imagePreview" class="h-16 rounded-lg border border-gray-600 shadow-md">
                                </template>
                                <template x-if="videoPreview">
                                    <video :src="videoPreview" class="h-16 rounded-lg border border-gray-600 shadow-md"></video>
                                </template>
                                <template x-if="filePreview">
                                    <div class="h-16 w-16 bg-gray-700 rounded-lg flex items-center justify-center border border-gray-600 shadow-md flex-col">
                                        <span class="text-2xl">📁</span>
                                    </div>
                                </template>
                                <button @click="imagePreview = null; videoPreview = null; filePreview = null" class="absolute -top-2 -right-2 bg-red-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs font-bold shadow">✕</button>
                            </div>
                            <div x-show="filePreview" class="text-xs text-gray-400 mt-1 truncate max-w-[200px]" x-text="previewFileName"></div>
                        </div>

                        <div x-show="showEmojiPicker" style="display:none;" x-transition.opacity.duration.200ms class="absolute bottom-full left-2 mb-2 bg-gray-800 border border-gray-700 p-3 rounded-2xl grid grid-cols-6 gap-2 md:gap-3 text-2xl shadow-2xl z-50">
                            <template x-for="emo in emojiList" :key="emo">
                                <span class="cursor-pointer hover:scale-125 transition transform" @click="newMessage += emo; if($refs.msgInput) $refs.msgInput.focus(); showEmojiPicker=false" x-text="emo"></span>
                            </template>
                        </div>

                        <div class="p-2 md:p-4 flex items-center gap-2 md:gap-3 w-full max-w-4xl mx-auto">
                            
                            <template x-if="!currentChat.is_group && currentChat.partner_is_banned">
                                <div class="flex-1 bg-gray-800/60 text-red-700 font-bold text-sm md:text-base rounded-full px-4 py-2 md:py-3 flex items-center border border-red-900/30 select-none">Пользователь Заблокирован</div>
                            </template>
                            
                            <template x-if="currentChat.is_group && currentChat.i_am_banned_in_group">
                                <div class="flex-1 bg-red-950/40 text-red-400 font-bold text-sm md:text-base rounded-full px-4 py-2 md:py-3 flex items-center justify-center border border-red-900/50 select-none">Вы исключены из группы</div>
                            </template>
                            
                            <template x-if="currentChat.is_group && !currentChat.i_am_banned_in_group && !currentChat.perms.send_text && !currentChat.i_am_admin && !currentChat.i_am_owner">
                                <div class="flex-1 bg-gray-800 text-gray-500 font-bold text-sm md:text-base rounded-full px-4 py-2 md:py-3 flex items-center justify-center border border-gray-700 select-none">Отправка сообщений запрещена</div>
                            </template>

                            <template x-if="!currentChat.partner_is_banned && currentChat.i_blocked_partner">
                                <div class="flex-1 bg-red-950/40 text-red-400 font-bold text-sm md:text-base rounded-full px-4 py-2 md:py-3 flex items-center justify-center border border-red-900/50 select-none">Вы заблокировали пользователя</div>
                            </template>

                            <template x-if="!currentChat.is_group && !currentChat.partner_is_banned && !currentChat.i_blocked_partner && currentChat.partner_blocked_me">
                                <div class="flex-1 bg-red-950/40 text-red-400 font-bold text-sm md:text-base rounded-full px-4 py-2 md:py-3 flex items-center justify-center border border-red-900/50 select-none">Вы заблокированы</div>
                            </template>

                            <template x-if="canInputMessages()">
                                <div class="flex items-center gap-1 md:gap-2">
                                    <template x-if="(!currentChat.is_group || currentChat.perms.send_photos || currentChat.i_am_admin || currentChat.i_am_owner) && !isRecording">
                                        <div class="relative" x-data="{ showAttachMenu: false }">
                                            <button type="button" @click="showAttachMenu = !showAttachMenu" class="p-2 text-gray-400 hover:text-blue-500 transition flex-shrink-0">
                                                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                                            </button>
                                            
                                            <div x-show="showAttachMenu" style="display:none;" @click.away="showAttachMenu = false" x-transition.opacity.duration.200ms class="absolute bottom-full left-0 mb-2 bg-gray-800 border border-gray-700 rounded-2xl flex flex-col p-2 shadow-2xl z-50 min-w-[140px]">
                                                <label class="px-3 py-2 hover:bg-gray-700 rounded-xl cursor-pointer text-white text-sm transition flex items-center gap-2">
                                                    <span>🖼️</span> Фото
                                                    <input type="file" accept="image/*" class="hidden" @change="handleFileSelect($event, 'photo'); showAttachMenu=false">
                                                </label>
                                                <label class="px-3 py-2 hover:bg-gray-700 rounded-xl cursor-pointer text-white text-sm transition flex items-center gap-2">
                                                    <span>🎥</span> Видео
                                                    <input type="file" accept="video/*" class="hidden" @change="handleFileSelect($event, 'video'); showAttachMenu=false">
                                                </label>
                                                <label class="px-3 py-2 hover:bg-gray-700 rounded-xl cursor-pointer text-white text-sm transition flex items-center gap-2">
                                                    <span>📁</span> Файл
                                                    <input type="file" class="hidden" @change="handleFileSelect($event, 'file'); showAttachMenu=false">
                                                </label>
                                            </div>
                                        </div>
                                    </template>

                                    <template x-if="(!currentChat.is_group || currentChat.perms.send_emoji || currentChat.i_am_admin || currentChat.i_am_owner) && !isRecording">
                                        <button type="button" @click="showEmojiPicker = !showEmojiPicker" class="p-2 text-gray-400 hover:text-yellow-400 transition flex-shrink-0 text-xl">😀</button>
                                    </template>

                                    <template x-if="isRecording">
                                        <button type="button" @click="cancelRecording()" class="p-2 text-white hover:text-red-400 transition flex-shrink-0" title="Отменить запись">
                                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                                        </button>
                                    </template>
                                </div>
                            </template>

                            <template x-if="canInputMessages() && !isRecording">
                                <input type="text" x-ref="msgInput" x-model="newMessage" @keydown.enter="sendMessage()" @input="sendTyping()" placeholder="Сообщение..." class="flex-1 min-w-0 bg-gray-800 text-sm md:text-base text-white rounded-full px-4 py-2 md:py-3 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner">
                            </template>

                            <template x-if="canInputMessages() && isRecording">
                                 <div class="flex-1 bg-red-950/60 border border-red-800 text-red-200 rounded-full px-4 py-2 md:py-3 flex items-center justify-center gap-2 font-mono font-bold animate-pulse">
                                    <div class="w-3 h-3 rounded-full bg-red-500"></div>
                                    <span x-text="formatTimer(recordTimer)"></span>
                                </div>
                            </template>

                            <template x-if="canInputMessages() && !isRecording">
                                 <button type="button" @click="sendMessage()" class="flex-shrink-0 bg-blue-600 hover:bg-blue-500 text-white rounded-full w-10 h-10 md:w-12 md:h-12 flex items-center justify-center transition shadow-lg" :disabled="!newMessage.trim() && !imagePreview && !videoPreview && !filePreview">
                                    <svg class="w-4 h-4 md:w-5 md:h-5 ml-1 transform -rotate-45" fill="currentColor" viewBox="0 0 20 20"><path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"></path></svg>
                                </button>
                            </template>

                            <template x-if="canInputMessages() && (!currentChat.is_group || currentChat.perms.send_voice || currentChat.i_am_admin || currentChat.i_am_owner)">
                                <button type="button" @click="toggleVoiceRecord()" :class="isRecording ? 'bg-red-600 text-white animate-pulse' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'" class="flex-shrink-0 rounded-full w-10 h-10 md:w-12 md:h-12 flex items-center justify-center transition shadow-lg">
                                    <svg class="w-4 h-4 md:w-5 md:h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clip-rule="evenodd"></path></svg>
                                </button>
                            </template>

                        </div>
                    </div>
                </div>
            </template>
        </div>

        <div x-show="showGroupProfileModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showGroupProfileModal = false">
            <div class="bg-[#1e293b] p-0 rounded-2xl border border-gray-700 w-full max-w-md shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
                <div class="p-6 pb-4 bg-gradient-to-b from-blue-900/40 to-[#1e293b] flex items-center justify-between border-b border-gray-800">
                    <h2 class="text-xl font-bold text-white flex items-center gap-2"><span>👥</span> Профиль группы</h2>
                    <div class="flex gap-2">
                        <template x-if="groupData.i_am_admin || groupData.i_am_owner || groupData.perms.change_profile">
                            <button @click="groupEditMode = !groupEditMode" class="text-gray-400 hover:text-blue-400 p-1"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg></button>
                        </template>
                        <button @click="showGroupProfileModal = false" class="text-gray-400 hover:text-white p-1 font-bold">✕</button>
                    </div>
                </div>

                <div class="flex border-b border-gray-800 text-sm">
                    <button @click="groupTab = 'info'" class="flex-1 py-3 font-semibold transition" :class="groupTab === 'info' ? 'text-blue-400 border-b-2 border-blue-500 bg-gray-800' : 'text-gray-400 hover:text-gray-200'">Информация</button>
                    <button @click="groupTab = 'members'" class="flex-1 py-3 font-semibold transition" :class="groupTab === 'members' ? 'text-blue-400 border-b-2 border-blue-500 bg-gray-800' : 'text-gray-400 hover:text-gray-200'">Участники</button>
                    <template x-if="groupData.i_am_admin || groupData.i_am_owner">
                        <button @click="groupTab = 'settings'" class="flex-1 py-3 font-semibold transition" :class="groupTab === 'settings' ? 'text-blue-400 border-b-2 border-blue-500 bg-gray-800' : 'text-gray-400 hover:text-gray-200'">Права</button>
                    </template>
                </div>

                <div class="p-6 overflow-y-auto flex-1">
                    <template x-if="groupTab === 'info'">
                        <div x-transition.opacity>
                            <div class="flex flex-col items-center mb-6 relative group">
                                <div class="w-24 h-24 rounded-full bg-gradient-to-tr from-green-500 to-blue-500 flex items-center justify-center text-4xl text-white shadow-lg overflow-hidden relative">
                                    <img x-show="groupData.avatar_url" :src="groupData.avatar_url" class="w-full h-full object-cover">
                                    <span x-show="!groupData.avatar_url">👥</span>
                                    <template x-if="groupEditMode">
                                        <label class="absolute inset-0 bg-black/60 hidden group-hover:flex items-center justify-center cursor-pointer transition">
                                            <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 16V8a2 2 0 012-2h3l1-2h6l1 2h3a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 13a3 3 0 100-6 3 3 0 000 6z"></path></svg>
                                            <input type="file" class="hidden" accept="image/*" @change="handleGroupAvatarSelect">
                                        </label>
                                    </template>
                                </div>
                            </div>
                            
                            <template x-if="!groupEditMode">
                                <div class="space-y-4 text-center">
                                    <h3 class="text-2xl font-black text-white" x-text="groupData.name"></h3>
                                    <p class="text-sm text-gray-400 italic" x-text="groupData.description || 'Описание отсутствует'"></p>
                                    <div class="pt-4 text-xs font-mono text-gray-500 border-t border-gray-800">ID Группы: <span x-text="groupData.chat_id"></span></div>
                                    <button @click="executeLeaveGroup()" class="mt-4 w-full bg-red-900/40 hover:bg-red-800/60 text-red-400 border border-red-800/50 py-2 rounded-xl text-sm font-bold transition">Покинуть группу</button>
                                </div>
                            </template>

                            <template x-if="groupEditMode">
                                <div class="space-y-3">
                                    <div><label class="text-xs text-blue-400 font-bold mb-1 block">Название группы</label>
                                    <input type="text" x-model="groupData.name" class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white text-sm outline-none focus:border-blue-500"></div>
                                    <div><label class="text-xs text-blue-400 font-bold mb-1 block">Описание</label>
                                    <textarea x-model="groupData.description" rows="3" class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white text-sm outline-none focus:border-blue-500"></textarea></div>
                                    <button @click="saveGroupProfile()" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-2 rounded-xl transition mt-2">Сохранить профиль</button>
                                </div>
                            </template>
                        </div>
                    </template>

                    <template x-if="groupTab === 'members'">
                        <div x-transition.opacity>
                            <template x-if="groupData.perms.add_members || groupData.i_am_admin || groupData.i_am_owner">
                                <div class="mb-4 flex gap-2">
                                    <input type="text" x-model="groupAddUserQuery" placeholder="Ник (@username) для добавления" class="flex-1 bg-gray-900 border border-gray-600 rounded-lg p-2 text-sm text-white focus:outline-none focus:border-blue-500">
                                    <button @click="executeAddGroupMember()" class="bg-blue-600 hover:bg-blue-500 text-white px-4 rounded-lg font-bold text-sm">Добавить</button>
                                </div>
                            </template>
                            <div class="space-y-2">
                                <template x-for="m in groupMembers" :key="m.user_id">
                                    <div class="bg-gray-800 p-2.5 rounded-xl border border-gray-700 flex items-center justify-between group">
                                        <div class="flex items-center gap-3 cursor-pointer" @click="openUserProfile(m.user_id)">
                                            <div class="w-8 h-8 rounded-full overflow-hidden bg-gray-700 flex-shrink-0 flex items-center justify-center">
                                                <img x-show="m.avatar" :src="m.avatar" class="w-full h-full object-cover">
                                                <span x-show="!m.avatar" x-text="m.name[0]"></span>
                                            </div>
                                            <div>
                                                <div class="text-sm font-bold text-white flex items-center gap-1.5">
                                                    <span x-text="m.name"></span>
                                                    <template x-if="m.is_owner"><span class="text-[9px] bg-yellow-600/30 text-yellow-400 px-1.5 py-0.5 rounded border border-yellow-600/50 uppercase">Владелец</span></template>
                                                    <template x-if="!m.is_owner && m.is_admin"><span class="text-[9px] bg-blue-600/30 text-blue-400 px-1.5 py-0.5 rounded border border-blue-600/50 uppercase">Админ</span></template>
                                                </div>
                                                <div class="text-[10px] text-gray-400" x-text="'@' + m.username"></div>
                                            </div>
                                        </div>
                                        <div class="flex items-center gap-2">
                                            <template x-if="m.role_tag"><span :class="(m.is_admin || m.is_owner) ? 'group-tag-admin' : 'group-tag-user'" x-text="m.role_tag"></span></template>
                                            <template x-if="groupData.i_am_owner || (groupData.i_am_admin && !m.is_owner)">
                                                <button @click="openManageMember(m)" class="text-gray-500 hover:text-white p-1 transition"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z"></path></svg></button>
                                            </template>
                                        </div>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </template>

                    <template x-if="groupTab === 'settings'">
                        <div class="space-y-4" x-transition.opacity>
                            <h4 class="text-xs font-black text-gray-400 uppercase tracking-wider">Глобальные разрешения</h4>
                            <p class="text-[10px] text-gray-500 -mt-2">Ограничения применяются ко всем участникам, кроме администраторов.</p>
                            
                            <div class="bg-gray-800 p-3 rounded-xl border border-gray-700 space-y-3">
                                <label class="flex items-center justify-between cursor-pointer"><span class="text-sm font-medium text-white">Отправка сообщений</span><input type="checkbox" x-model="groupData.perms.send_text" class="w-4 h-4"></label>
                                <label class="flex items-center justify-between cursor-pointer"><span class="text-sm font-medium text-white">Отправка фотографий</span><input type="checkbox" x-model="groupData.perms.send_photos" class="w-4 h-4"></label>
                                <label class="flex items-center justify-between cursor-pointer"><span class="text-sm font-medium text-white">Голосовые сообщения</span><input type="checkbox" x-model="groupData.perms.send_voice" class="w-4 h-4"></label>
                                <label class="flex items-center justify-between cursor-pointer"><span class="text-sm font-medium text-white">Использование эмодзи</span><input type="checkbox" x-model="groupData.perms.send_emoji" class="w-4 h-4"></label>
                            </div>
                            <h4 class="text-xs font-black text-gray-400 uppercase tracking-wider mt-4">Права группы</h4>
                            <div class="bg-gray-800 p-3 rounded-xl border border-gray-700 space-y-3">
                                <label class="flex items-center justify-between cursor-pointer"><span class="text-sm font-medium text-white">Добавление участников</span><input type="checkbox" x-model="groupData.perms.add_members" class="w-4 h-4"></label>
                                <label class="flex items-center justify-between cursor-pointer"><span class="text-sm font-medium text-white">Изменение профиля группы</span><input type="checkbox" x-model="groupData.perms.change_profile" class="w-4 h-4"></label>
                            </div>
                            <button @click="saveGroupPerms()" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-2.5 rounded-xl transition mt-4">Сохранить разрешения</button>
                        </div>
                    </template>
                </div>
            </div>
        </div>

        <div x-show="showManageMemberModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" x-transition.opacity.duration.200ms @click.self="showManageMemberModal = false">
            <div class="bg-[#1e293b] p-6 rounded-2xl border border-blue-500/30 w-full max-w-sm shadow-2xl flex flex-col max-h-[90vh]">
                <h3 class="text-white font-bold text-lg border-b border-gray-700 pb-2 mb-4" x-text="'Управление: ' + manageMemberData.name"></h3>
                
                <div class="overflow-y-auto flex-1 pr-1 space-y-4">
                    <template x-if="groupData.i_am_owner || (groupData.i_am_admin && groupData.my_perms.change_tags)">
                        <div>
                            <label class="text-xs font-bold text-blue-400 block mb-1">Кастомный Тег</label>
                            <input type="text" x-model="manageMemberData.role_tag" placeholder="Например: Модератор" class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white text-sm outline-none focus:border-blue-500">
                        </div>
                    </template>

                    <template x-if="groupData.i_am_owner || (groupData.i_am_admin && groupData.my_perms.assign_admins)">
                        <div class="bg-gray-800 p-3 rounded-xl border border-gray-700">
                            <label class="flex items-center justify-between cursor-pointer mb-2 border-b border-gray-700 pb-2">
                                <span class="text-sm font-bold text-red-400">Назначить Администратором</span>
                                <input type="checkbox" x-model="manageMemberData.is_admin" class="w-4 h-4">
                            </label>
                            
                            <div x-show="manageMemberData.is_admin" class="space-y-2 mt-2">
                                <p class="text-[10px] text-gray-500 leading-tight mb-2">Вы можете выдать только те права, которыми обладаете сами.</p>
                                
                                <label class="flex items-center gap-2 cursor-pointer" :class="!(groupData.i_am_owner || groupData.my_perms.change_profile) ? 'opacity-50' : ''">
                                    <input type="checkbox" x-model="manageMemberData.perm_change_profile" :disabled="!(groupData.i_am_owner || groupData.my_perms.change_profile)" class="w-3.5 h-3.5">
                                    <span class="text-xs text-white">Изменение профиля</span>
                                </label>
                                <label class="flex items-center gap-2 cursor-pointer" :class="!(groupData.i_am_owner || groupData.my_perms.delete_msgs) ? 'opacity-50' : ''">
                                    <input type="checkbox" x-model="manageMemberData.perm_delete_msgs" :disabled="!(groupData.i_am_owner || groupData.my_perms.delete_msgs)" class="w-3.5 h-3.5">
                                    <span class="text-xs text-white">Удаление чужих сообщений</span>
                                </label>
                                <label class="flex items-center gap-2 cursor-pointer" :class="!(groupData.i_am_owner || groupData.my_perms.ban_users) ? 'opacity-50' : ''">
                                    <input type="checkbox" x-model="manageMemberData.perm_ban_users" :disabled="!(groupData.i_am_owner || groupData.my_perms.ban_users)" class="w-3.5 h-3.5">
                                    <span class="text-xs text-white">Блокировка (исключение)</span>
                                </label>
                                <label class="flex items-center gap-2 cursor-pointer" :class="!(groupData.i_am_owner || groupData.my_perms.change_tags) ? 'opacity-50' : ''">
                                    <input type="checkbox" x-model="manageMemberData.perm_change_tags" :disabled="!(groupData.i_am_owner || groupData.my_perms.change_tags)" class="w-3.5 h-3.5">
                                    <span class="text-xs text-white">Изменение тегов</span>
                                </label>
                                <template x-if="groupData.i_am_owner">
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" x-model="manageMemberData.perm_assign_admins" class="w-3.5 h-3.5">
                                        <span class="text-xs text-red-300 font-bold">Назначение админов</span>
                                    </label>
                                </template>
                            </div>
                        </div>
                    </template>
                </div>

                <div class="mt-4 flex flex-col gap-2">
                    <button @click="saveManageMember()" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-2 rounded-xl transition">Сохранить</button>
                    <template x-if="groupData.i_am_owner || (groupData.i_am_admin && groupData.my_perms.ban_users)">
                        <button @click="executeKickMember()" class="w-full bg-red-900/40 hover:bg-red-800 text-red-400 font-bold py-1.5 rounded-xl transition text-xs border border-red-800">Исключить пользователя</button>
                    </template>
                    <button @click="showManageMemberModal = false" class="w-full bg-gray-700 hover:bg-gray-600 text-white font-bold py-2 rounded-xl transition">Закрыть</button>
                </div>
            </div>
        </div>

        <div x-show="showGroupCreateModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showGroupCreateModal = false">
            <div class="bg-[#1e293b] p-6 rounded-2xl border border-blue-500/40 w-full max-w-sm shadow-2xl text-white">
                <h3 class="font-bold text-xl mb-4 text-center">Создание Группы</h3>
                <input type="text" x-model="newGroupName" placeholder="Название группы..." class="w-full bg-gray-900 border border-gray-600 rounded-xl p-3 text-white text-sm outline-none focus:ring-1 focus:ring-blue-500 mb-4">
                <div class="flex gap-2">
                    <button @click="executeCreateGroup()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white font-bold py-2.5 rounded-xl transition text-sm">Создать</button>
                    <button @click="showGroupCreateModal = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white font-bold py-2.5 rounded-xl transition text-sm">Отмена</button>
                </div>
            </div>
        </div>

        <div x-show="showContactModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showContactModal = false">
            <div class="bg-[#1e293b] p-6 rounded-2xl border border-gray-700 w-full max-w-sm shadow-2xl">
                <h3 class="text-white font-bold text-lg mb-2">Никнейм контакта</h3>
                <p class="text-xs text-gray-400 mb-4">Измените имя пользователя чисто для своего отображения</p>
                <input type="text" x-model="contactCustomName" placeholder="Имя контакта..." class="w-full bg-gray-900 border border-gray-600 rounded-xl p-3 text-white text-sm focus:ring-1 focus:ring-blue-500 mb-4 outline-none">
                <div class="flex gap-2">
                    <button @click="saveContactCustomName()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white font-bold py-2.5 rounded-xl text-sm transition">Сохранить</button>
                    <button @click="showContactModal = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white font-bold py-2.5 rounded-xl text-sm transition">Отмена</button>
                </div>
            </div>
        </div>
        
        <div x-show="showProfileModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="closeProfileModal()">
             <div class="bg-[#242f3d] w-full max-w-sm rounded-2xl shadow-2xl overflow-hidden flex flex-col relative text-gray-100 max-h-[90vh]">

                <div class="absolute top-4 right-4 flex gap-4 z-20">
                    <button x-show="isMyProfile && !editMode && !privacyMode" @click="editMode = true" class="text-white hover:text-blue-400 drop-shadow-md">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg>
                    </button>
                    <button @click="closeProfileModal()" class="text-white hover:text-red-400 drop-shadow-md">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>

                <div x-show="!editMode && !privacyMode" class="flex flex-col overflow-y-auto" x-transition.opacity>
                    <div class="relative pb-6 bg-gradient-to-b from-[#1c242f] to-[#242f3d]">
                        <div class="w-24 h-24 md:w-32 md:h-32 mx-auto mt-8 rounded-full bg-blue-600 flex items-center justify-center text-4xl font-bold shadow-lg overflow-hidden border-2 border-transparent">
                            <img x-show="viewProfileData.avatar" :src="viewProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!viewProfileData.avatar" x-text="viewProfileData.first_name ? viewProfileData.first_name[0] : ''"></span>
                        </div>
                        <div class="text-center mt-4 px-4">
                            <div class="text-lg md:text-xl font-bold flex items-center justify-center gap-2 flex-wrap">
                                <span x-text="viewProfileData.display_name || (viewProfileData.first_name + ' ' + (viewProfileData.last_name || ''))"></span>
                                <template x-if="viewProfileData.is_admin"><span class="admin-badge">Admin</span></template>
                                <template x-if="viewProfileData.is_moderator"><span class="mod-badge">Moderator</span></template>
                            </div>
                             <div class="text-xs md:text-sm mt-1" :class="viewProfileData.is_online ? 'text-blue-400' : 'text-gray-400'" 
                                 x-text="viewProfileData.custom_status ? viewProfileData.custom_status : (viewProfileData.is_online ? 'в сети' : 'был(а) ' + (viewProfileData.last_seen || 'недавно'))"></div>
                        </div>

                        <div class="flex justify-center items-center gap-2.5 mt-5 px-4">
                            <template x-for="slotIdx in [0, 1, 2, 3]" :key="slotIdx">
                                <div @click="handleGiftSlotClick(slotIdx)" class="w-13 h-13 md:w-14 md:h-14 rounded-2xl bg-black/40 border border-white/10 flex items-center justify-center relative cursor-pointer hover:border-blue-500 transition group shadow-inner">
                                    <template x-if="getPinnedGiftAt(slotIdx)">
                                        <img :src="getPinnedGiftAt(slotIdx).img" class="w-9 h-9 md:w-10 md:h-10 object-contain filter drop-shadow">
                                    </template>
                                    <template x-if="!getPinnedGiftAt(slotIdx) && isMyProfile">
                                        <span class="text-white/20 text-2xl group-hover:text-blue-400 font-extralight">+</span>
                                    </template>
                                </div>
                            </template>
                        </div>
                    </div>

                    <div class="px-6 pb-6 space-y-4 pt-2">
                        <template x-if="viewProfileData.phone">
                            <div class="border-b border-gray-700 pb-2">
                                <div class="text-[14px] md:text-[15px] font-medium select-text" x-text="viewProfileData.phone"></div>
                                <div class="text-[10px] md:text-xs text-gray-500">Телефон</div>
                            </div>
                        </template>

                        <template x-if="viewProfileData.about_me">
                             <div class="border-b border-gray-700 pb-2">
                                <div class="text-[14px] md:text-[15px] whitespace-pre-wrap select-text" x-text="viewProfileData.about_me"></div>
                                <div class="text-[10px] md:text-xs text-gray-500">О себе</div>
                             </div>
                        </template>

                        <div class="border-b border-gray-700 pb-2">
                            <div class="text-[14px] md:text-[15px] text-blue-400 select-text" x-text="'@' + viewProfileData.username"></div>
                            <div class="text-[10px] md:text-xs text-gray-500">Имя пользователя</div>
                        </div>

                        <template x-if="viewProfileData.formatted_bday">
                             <div class="border-b border-gray-700 pb-2">
                                <div class="text-[14px] md:text-[15px]" x-text="viewProfileData.formatted_bday"></div>
                                <div class="text-[10px] md:text-xs text-gray-500">День рождения</div>
                             </div>
                        </template>

                        <div class="pt-2">
                            <button @click="openGiftsShop()" class="w-full bg-blue-950/40 hover:bg-blue-900/50 border border-blue-800/50 text-blue-300 p-3 rounded-xl font-bold flex items-center justify-between transition shadow-sm mb-3">
                                <div class="flex items-center gap-2.5">
                                    <span class="text-blue-400 text-lg">🎁</span>
                                    <span class="text-sm">Подарки</span>
                                </div>
                                <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
                            </button>

                            <template x-if="isMyProfile">
                                <div class="space-y-3">
                                    <button @click="privacyMode = true" class="w-full bg-blue-950/40 hover:bg-blue-900/50 border border-blue-800/50 text-blue-300 p-3 rounded-xl font-bold flex items-center justify-between transition shadow-sm">
                                        <div class="flex items-center gap-2.5">
                                            <span class="text-blue-400 text-lg">🔒</span>
                                            <span class="text-sm">Настройки Конфиденциальности</span>
                                        </div>
                                        <svg class="w-4 h-4 text-blue-400 transform -rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                                    </button>

                                    <div class="pt-2">
                                        <div class="text-[11px] font-bold text-gray-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                                            <span class="text-base">📁</span><span>Помощь</span>
                                        </div>
                                        <div class="bg-[#1c242f] rounded-xl p-3 border border-gray-800">
                                            <a href="/micro" class="text-blue-400 hover:text-blue-300 hover:underline font-medium text-sm block">Включение микрофона</a>
                                        </div>
                                    </div>
                                </div>
                            </template>
                        </div>

                    </div>
                </div>

                <div x-show="editMode" class="p-6 overflow-y-auto" x-transition.opacity>
                    <h3 class="text-base md:text-lg font-bold mb-4 text-blue-400">Редактирование профиля</h3>
                    <div class="flex flex-col items-center mb-4">
                        <div class="w-20 h-20 md:w-24 md:h-24 rounded-full bg-blue-600 mb-2 flex items-center justify-center text-3xl font-bold overflow-hidden relative group">
                            <img x-show="editProfileData.avatar" :src="editProfileData.avatar" class="w-full h-full object-cover">
                            <span x-show="!editProfileData.avatar" x-text="editProfileData.first_name[0]"></span>
                            <label class="absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center cursor-pointer transition">
                                <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 16V8a2 2 0 012-2h3l1-2h6l1 2h3a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 13a3 3 0 100-6 3 3 0 000 6z"></path></svg>
                                <input type="file" class="hidden" accept="image/*" @change="handleAvatarSelect">
                            </label>
                        </div>
                        <div class="text-[10px] md:text-xs text-gray-400">Нажмите для изменения фото</div>
                    </div>
                    <div class="space-y-4">
                         <div>
                            <label class="text-[10px] md:text-xs text-gray-400">Имя</label>
                            <input type="text" x-model="editProfileData.first_name" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-[10px] md:text-xs text-gray-400">Фамилия</label>
                            <input type="text" x-model="editProfileData.last_name" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-[10px] md:text-xs text-gray-400">Имя пользователя (никнейм)</label>
                            <input type="text" x-model="editProfileData.username" :disabled="editProfileData.username === 'admin'" :class="editProfileData.username === 'admin' ? 'opacity-50 cursor-not-allowed' : ''" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500 mb-2">

                            <label class="text-[10px] md:text-xs text-gray-400">День рождения</label>
                            <div class="flex gap-2">
                                <input type="number" x-model="editProfileData.birth_day" placeholder="День" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                                <input type="number" x-model="editProfileData.birth_month" placeholder="Мес" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                                <input type="number" x-model="editProfileData.birth_year" placeholder="Год" class="w-1/3 bg-[#1c242f] border-none rounded p-2 text-sm text-white text-center focus:ring-1 focus:ring-blue-500">
                            </div>
                        </div>
                        <div>
                            <label class="text-[10px] md:text-xs text-gray-400">Телефон</label>
                            <input type="text" x-model="editProfileData.phone" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500">
                        </div>
                        <div>
                            <label class="text-[10px] md:text-xs text-gray-400">О себе</label>
                            <textarea x-model="editProfileData.about_me" rows="2" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500"></textarea>
                        </div>
                        <div class="mt-4 pt-4 border-t border-gray-700">
                             <h4 class="text-xs md:text-sm font-semibold mb-2 text-gray-300">Смена пароля</h4>
                             <input type="password" x-model="editProfileData.new_password" placeholder="Новый пароль" class="w-full bg-[#1c242f] border-none rounded p-2 text-sm text-white focus:ring-1 focus:ring-blue-500">
                        </div>
                        <div class="flex gap-2 pt-4">
                            <button @click="saveProfile()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-2.5 rounded-xl text-sm font-bold transition">Сохранить</button>
                            <button @click="editMode = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2.5 rounded-xl text-sm font-bold transition">Отмена</button>
                        </div>
                    </div>
                </div>

                <div x-show="privacyMode" class="p-6 overflow-y-auto" x-transition.opacity>
                    <h3 class="text-base md:text-lg font-bold mb-4 text-blue-400 flex items-center gap-2">
                        <span>🔒</span><span>Кто видит мою информацию</span>
                    </h3>
                    <div class="space-y-4">
                         <div class="bg-[#1c242f] p-3 rounded-xl border border-gray-800">
                             <div class="flex justify-between items-center mb-2">
                                 <span class="text-sm font-semibold text-white">Телефон</span>
                                 <span class="text-xs text-blue-400 font-bold" x-text="translatePrivacy(editProfileData.privacy_phone)"></span>
                             </div>
                             <div class="grid grid-cols-3 gap-1.5 bg-gray-900 p-1 rounded-lg">
                                 <button @click="editProfileData.privacy_phone = 'nobody'" :class="editProfileData.privacy_phone === 'nobody' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Никто</button>
                                 <button @click="editProfileData.privacy_phone = 'contacts'" :class="editProfileData.privacy_phone === 'contacts' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Контакты</button>
                                 <button @click="editProfileData.privacy_phone = 'everyone'" :class="editProfileData.privacy_phone === 'everyone' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Все</button>
                             </div>
                         </div>
                         <div class="bg-[#1c242f] p-3 rounded-xl border border-gray-800">
                             <div class="flex justify-between items-center mb-2">
                                 <span class="text-sm font-semibold text-white">День рождения</span>
                                 <span class="text-xs text-blue-400 font-bold" x-text="translatePrivacy(editProfileData.privacy_bday)"></span>
                             </div>
                             <div class="grid grid-cols-3 gap-1.5 bg-gray-900 p-1 rounded-lg">
                                 <button @click="editProfileData.privacy_bday = 'nobody'" :class="editProfileData.privacy_bday === 'nobody' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Никто</button>
                                 <button @click="editProfileData.privacy_bday = 'contacts'" :class="editProfileData.privacy_bday === 'contacts' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Контакты</button>
                                 <button @click="editProfileData.privacy_bday = 'everyone'" :class="editProfileData.privacy_bday === 'everyone' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Все</button>
                             </div>
                         </div>
                         <div class="bg-[#1c242f] p-3 rounded-xl border border-gray-800">
                             <div class="flex justify-between items-center mb-2">
                                 <span class="text-sm font-semibold text-white">Время захода</span>
                                 <span class="text-xs text-blue-400 font-bold" x-text="translatePrivacy(editProfileData.privacy_last_seen)"></span>
                             </div>
                             <div class="grid grid-cols-3 gap-1.5 bg-gray-900 p-1 rounded-lg">
                                 <button @click="editProfileData.privacy_last_seen = 'nobody'" :class="editProfileData.privacy_last_seen === 'nobody' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Никто</button>
                                 <button @click="editProfileData.privacy_last_seen = 'contacts'" :class="editProfileData.privacy_last_seen === 'contacts' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Контакты</button>
                                 <button @click="editProfileData.privacy_last_seen = 'everyone'" :class="editProfileData.privacy_last_seen === 'everyone' ? 'bg-blue-600 text-white font-bold' : 'text-gray-400 hover:text-white'" class="py-1.5 rounded-md text-xs transition">Все</button>
                             </div>
                         </div>
                    </div>
                    <div class="flex gap-2 pt-6">
                        <button @click="saveProfile()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-2.5 rounded-xl text-sm font-bold transition">Применить</button>
                        <button @click="privacyMode = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2.5 rounded-xl text-sm font-bold transition">Назад</button>
                    </div>
                </div>

            </div>
        </div>

        <div x-show="showQuestsModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showQuestsModal = false">
            <div class="bg-[#1e293b] p-6 rounded-2xl border border-blue-500/40 w-full max-w-md shadow-2xl text-white max-h-[85vh] overflow-y-auto">
                <div class="flex justify-between items-center mb-4 border-b border-gray-700 pb-3">
                    <div class="flex items-center gap-2">
                        <img src="/molniya.png" class="w-8 h-8 object-contain" alt="⚡">
                        <h3 class="font-bold text-lg">Ежедневные задания</h3>
                    </div>
                    <button @click="showQuestsModal = false" class="text-gray-400 hover:text-white font-bold text-lg">✕</button>
                </div>
                <p class="text-xs text-gray-400 mb-4">Выполняйте задания и получайте молнии для покупки подарков. Обновление каждый день в 00:00 (МСК)</p>

                <div class="space-y-3">
                    <template x-for="(q, key) in questItems" :key="key">
                        <div class="bg-gray-800/80 border border-gray-700 p-3.5 rounded-xl flex items-center justify-between gap-3">
                            <div class="flex-1 min-w-0">
                                <div class="text-sm font-bold text-white truncate" x-text="q.name"></div>
                                <div class="text-xs text-gray-400 mt-0.5">
                                    Прогресс: <span class="text-blue-400 font-mono font-bold" x-text="q.progress + '/' + q.target"></span>
                                </div>
                            </div>
                            <button @click="executeClaimQuest(key)" :disabled="q.claimed || q.progress < q.target"
                                    :class="q.claimed ? 'bg-gray-900 text-gray-600 border border-gray-800 cursor-not-allowed' : (q.progress >= q.target ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg hover:scale-105' : 'bg-gray-700 text-gray-400 cursor-not-allowed')"
                                    class="px-3.5 py-2 rounded-xl font-black text-xs transition flex items-center justify-center gap-1 flex-shrink-0 min-w-[70px]">
                                <span x-text="q.claimed ? 'Забрано' : ('+' + q.reward)"></span>
                                <template x-if="!q.claimed"><img src="/molniya.png" class="w-4 h-4 inline-block align-middle"></template>
                            </button>
                        </div>
                    </template>
                </div>
            </div>
        </div>

        <div x-show="showShopModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showShopModal = false">
            <div class="bg-[#1e293b] p-6 rounded-2xl border border-blue-500/40 w-full max-w-lg shadow-2xl text-white max-h-[90vh] flex flex-col">
                <div class="flex justify-between items-center mb-4 flex-shrink-0 border-b border-gray-700 pb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-2xl">🎁</span>
                        <h3 class="font-bold text-lg" x-text="shopModeInventory ? 'Мои подарки' : 'Магазин подарков'"></h3>
                    </div>
                    <div class="flex items-center gap-3">
                        <div class="bg-blue-500/20 border border-blue-500/40 px-3 py-1 rounded-full text-blue-300 font-mono font-bold text-xs flex items-center gap-1">
                            <span x-text="myLightnings"></span><img src="/molniya.png" class="w-4 h-4 inline-block align-middle">
                        </div>
                        <button @click="showShopModal = false" class="text-gray-400 hover:text-white font-bold text-lg">✕</button>
                    </div>
                 </div>

                <template x-if="!shopModeInventory">
                    <div class="flex gap-2 mb-4 bg-gray-900 p-1.5 rounded-xl flex-shrink-0">
                        <button @click="shopTab = 'official'" :class="shopTab === 'official' ? 'bg-blue-600 text-white font-bold shadow' : 'text-gray-400 hover:text-white'" class="flex-1 py-2 rounded-lg text-xs md:text-sm transition">Новый магазин</button>
                        <button @click="shopTab = 'market'" :class="shopTab === 'market' ? 'bg-blue-600 text-white font-bold shadow' : 'text-gray-400 hover:text-white'" class="flex-1 py-2 rounded-lg text-xs md:text-sm transition">Магазин пользователей</button>
                    </div>
                </template>

                <div class="flex-1 overflow-y-auto min-h-[250px] pr-1">
                    <template x-if="!shopModeInventory && shopTab === 'official'">
                        <div class="grid grid-cols-2 gap-3" x-transition.opacity>
                            <template x-for="item in shopOfficial" :key="item.def_id">
                                <div class="bg-gray-800/60 border border-gray-700/80 rounded-2xl p-3.5 flex flex-col items-center text-center justify-between group hover:border-blue-500 transition">
                                    <img :src="item.img" class="w-16 h-16 md:w-20 md:h-20 object-contain my-2 filter drop-shadow-md group-hover:scale-110 transition transform">
                                    <div class="w-full">
                                        <div class="font-bold text-sm text-white truncate" x-text="item.name"></div>
                                        <button @click="executeBuyOfficialGift(item.def_id)" class="w-full mt-2.5 bg-blue-600 hover:bg-blue-500 text-white font-black py-2 rounded-xl text-xs shadow-md transition flex items-center justify-center gap-1">
                                            <span>Купить за</span><span x-text="item.price"></span><img src="/molniya.png" class="w-4 h-4 inline-block align-middle">
                                        </button>
                                    </div>
                                </div>
                            </template>
                        </div>
                    </template>
                    <template x-if="!shopModeInventory && shopTab === 'market'">
                        <div x-transition.opacity>
                            <template x-if="shopMarket.length === 0">
                                <div class="text-center py-10 text-gray-500 text-sm italic">На рынке пока нет выставленных подарков</div>
                            </template>
                            <div class="grid grid-cols-2 gap-3">
                                <template x-for="item in shopMarket" :key="item.user_gift_id">
                                    <div class="bg-gray-800/60 border border-gray-700/80 rounded-2xl p-3.5 flex flex-col items-center text-center justify-between group hover:border-blue-500 transition">
                                        <div class="text-[10px] text-gray-400 w-full text-left truncate">Продавец: <span class="text-blue-400 font-bold" x-text="'@' + item.seller"></span></div>
                                        <img :src="item.img" class="w-14 h-14 md:w-18 md:h-18 object-contain my-2 filter drop-shadow-md group-hover:scale-110 transition transform">
                                        <div class="w-full">
                                            <div class="font-bold text-xs md:text-sm text-white truncate" x-text="item.name"></div>
                                            <div class="text-[10px] text-gray-500 line-through flex items-center justify-center gap-0.5">Гос: <span x-text="item.store_price"></span><img src="/molniya.png" class="w-3.5 h-3.5 inline-block"></div>
                                            <button @click="executeBuyMarketGift(item.user_gift_id)" class="w-full mt-1.5 bg-blue-600 hover:bg-blue-500 text-white font-black py-2 rounded-xl text-xs shadow-md transition flex items-center justify-center gap-1">
                                                <span>Купить</span><span x-text="item.price"></span><img src="/molniya.png" class="w-4 h-4 inline-block align-middle">
                                            </button>
                                        </div>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </template>
                    <template x-if="shopModeInventory">
                        <div x-transition.opacity>
                            <template x-if="myGiftsInventory.length === 0">
                                <div class="text-center py-10 text-gray-500 text-sm italic">У вас пока нет подарков. Приобретите их в магазине!</div>
                            </template>
                            <div class="grid grid-cols-2 gap-3">
                                <template x-for="g in myGiftsInventory" :key="g.user_gift_id">
                                    <div @click="if(pinningSlotIndex !== null) executePinGiftToSlot(g.user_gift_id)" 
                                         class="bg-gray-800/80 border border-gray-700 rounded-2xl p-3.5 flex flex-col items-center text-center justify-between group transition cursor-pointer hover:border-blue-500">
                                        <div class="text-[10px] font-mono text-blue-400 font-bold w-full text-left">Подарок #<span x-text="g.user_gift_id"></span></div>
                                        <img :src="g.img" class="w-16 h-16 md:w-20 md:h-20 object-contain my-2 filter drop-shadow-lg group-hover:scale-110 transition transform">
                                        <div class="w-full">
                                            <div class="font-bold text-sm text-white truncate" x-text="g.name"></div>
                                            <template x-if="pinningSlotIndex !== null">
                                                <div class="mt-2 bg-blue-500/20 border border-blue-500 text-blue-300 text-[11px] font-black py-1 rounded-lg">Выбрать для слота</div>
                                            </template>
                                            <template x-if="pinningSlotIndex === null">
                                                <div class="text-[10px] text-gray-400 mt-1" x-text="g.is_pinned ? 'Закреплен' : (g.is_for_sale ? 'На продаже (' + g.sale_price + '⚡)' : 'В коллекции')"></div>
                                            </template>
                                        </div>
                                     </div>
                                </template>
                            </div>
                         </div>
                    </template>
                </div>
            </div>
        </div>

        <div x-show="showPartnerGiftsModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showPartnerGiftsModal = false">
             <div class="bg-[#1e293b] p-6 rounded-3xl border border-blue-500/40 w-full max-w-md shadow-2xl text-white max-h-[85vh] flex flex-col">
                <div class="flex justify-between items-center mb-4 border-b border-gray-700 pb-3 flex-shrink-0">
                    <div class="flex items-center gap-2">
                        <span class="text-2xl">🎁</span>
                        <h3 class="font-bold text-lg" x-text="'Подарки ' + (viewProfileData.first_name || '')"></h3>
                    </div>
                    <button @click="showPartnerGiftsModal = false" class="text-gray-400 hover:text-white font-bold text-lg">✕</button>
                </div>
                <div class="flex-1 overflow-y-auto pr-1">
                    <template x-if="partnerGiftsList.length === 0">
                        <div class="text-center py-10 text-gray-500 text-sm italic">У пользователя пока нет подарков</div>
                    </template>
                    <div class="grid grid-cols-2 gap-3">
                        <template x-for="g in partnerGiftsList" :key="g.user_gift_id">
                            <div class="bg-gray-800/80 border border-gray-700/80 rounded-2xl p-3.5 flex flex-col items-center text-center justify-between">
                                <div class="text-[10px] font-mono text-blue-400 font-bold w-full text-left">Подарок #<span x-text="g.user_gift_id"></span></div>
                                <img :src="g.img" class="w-16 h-16 md:w-20 md:h-20 object-contain my-2 filter drop-shadow-lg">
                                <div class="font-bold text-sm text-white truncate w-full" x-text="g.name"></div>
                                <div class="text-[10px] text-gray-400 mt-1" x-text="'Получен: ' + g.acquired_at"></div>
                            </div>
                        </template>
                    </div>
                </div>
             </div>
        </div>

        <div x-show="showGiftViewModal" style="display:none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" x-transition.opacity.duration.200ms @click.self="showGiftViewModal = false">
            <div class="bg-[#1e293b] p-6 rounded-3xl border border-blue-500/40 w-full max-w-xs shadow-2xl text-white text-center flex flex-col items-center relative">
                <button @click="showGiftViewModal = false" class="absolute top-4 right-4 text-gray-400 hover:text-white font-bold">✕</button>
                <div class="text-xs font-mono text-blue-400 font-bold bg-blue-500/10 border border-blue-500/30 px-3 py-1 rounded-full mb-3">
                    Подарок #<span x-text="selectedGiftView ? selectedGiftView.user_gift_id : ''"></span>
                </div>
                <img :src="selectedGiftView ? selectedGiftView.img : ''" class="w-24 h-24 md:w-28 md:h-28 object-contain my-2 filter drop-shadow-xl">
                <h3 class="text-lg font-black text-white mt-1" x-text="selectedGiftView ? selectedGiftView.name : ''"></h3>
                <div class="text-xs text-gray-400 mt-0.5 flex items-center justify-center gap-1">Гос. стоимость: <span class="text-white font-bold" x-text="selectedGiftView ? selectedGiftView.store_price : ''"></span> <img src="/molniya.png" class="w-4 h-4 inline-block align-middle"></div>
                <template x-if="isMyProfile && selectedGiftView">
                    <div class="w-full mt-5 pt-4 border-t border-gray-700/80 space-y-2" x-data="{ inputSalePrice: '' }">
                        <template x-if="selectedGiftView.is_pinned">
                            <button @click="executeUnpinGiftSlot(selectedGiftView.slot_index)" class="w-full bg-gray-700 hover:bg-gray-600 text-white font-bold py-2.5 rounded-xl text-xs transition">Открепить от профиля</button>
                        </template>
                        <div class="flex gap-1.5 pt-1">
                             <input type="number" x-model="inputSalePrice" placeholder="Цена" class="w-1/2 bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs text-center outline-none">
                            <button @click="executeToggleMarketSale(selectedGiftView.user_gift_id, inputSalePrice)" class="w-1/2 bg-blue-600 hover:bg-blue-500 text-white font-black py-2 rounded-xl text-xs transition shadow flex items-center justify-center gap-0.5">
                                <span x-text="selectedGiftView.is_for_sale ? 'Снять' : 'На Маркет'"></span>
                                <template x-if="!selectedGiftView.is_for_sale"><img src="/molniya.png" class="w-3.5 h-3.5 inline-block"></template>
                            </button>
                        </div>
                        <div x-show="selectedGiftView.is_for_sale" class="text-[10px] text-yellow-400 font-bold flex items-center justify-center gap-0.5">Выставлен за <span x-text="selectedGiftView.sale_price"></span><img src="/molniya.png" class="w-3.5 h-3.5 inline-block"></div>
                    </div>
                </template>
            </div>
        </div>

    </div>

    <script>
         function messengerApp() {
            return {
                socket: null,
                myId: {{ current_user.id }},
                myLightnings: {{ current_user.lightnings or 0 }},
                chats: [],
                searchQuery: '',
                searchResults: [],
                currentChat: null,
                messages: [],
                newMessage: '',
                
                imagePreview: null,
                videoPreview: null,
                filePreview: null,
                previewFileName: null,
                
                typing: {},
                
                showEmojiPicker: false,
                emojiList: ['😀','😂','🥰','😎','🤔','😭','😡','👍','👎','❤️','🔥','🎉','✨','💩','🤡','💀','👀','🙏'],

                // ОЧЕРЕДЬ ОТПРАВКИ СОХРАНЯЕТСЯ В LOCALSTORAGE!
                pendingMessagesToSend: JSON.parse(localStorage.getItem('youme_pending_queue') || '{}'), 
                savePendingQueue() { localStorage.setItem('youme_pending_queue', JSON.stringify(this.pendingMessagesToSend)); },

                isRecording: false,
                showMicInstructionBanner: false,
                mediaRecorder: null,
                audioChunks: [],
                recordTimer: 0,
                recordInterval: null,

                showContactModal: false,
                contactCustomName: '',

                // ГРУППЫ
                showGroupCreateModal: false,
                newGroupName: '',
                showGroupProfileModal: false,
                groupTab: 'info',
                groupEditMode: false,
                groupData: { perms: {}, my_perms: {} },
                groupMembers: [],
                groupAddUserQuery: '',
                showManageMemberModal: false,
                manageMemberData: {},

                // ПОДАРКИ И КВЕСТЫ
                showQuestsModal: false,
                questItems: {},
                showShopModal: false,
                shopTab: 'official',
                shopModeInventory: false,
                shopOfficial: [],
                shopMarket: [],
                myGiftsInventory: [],
                pinnedGiftsSlots: [], 
                showGiftViewModal: false,
                selectedGiftView: null,
                pinningSlotIndex: null,
                showPartnerGiftsModal: false,
                partnerGiftsList: [],

                contextMenu: { show: false, x: 0, y: 0, msg: null, canDelete: false },
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
                privacyMode: false,
                myProfileData: {}, 
                viewProfileData: {},
                editProfileData: {}, 

                init() {
                    this.fetchMyProfile();
                    this.socket = io();
                    
                    this.socket.on('connect', () => { 
                        this.loadChats(); 
                        for (let tId in this.pendingMessagesToSend) {
                             this.socket.emit('send_message', this.pendingMessagesToSend[tId]);
                        }
                    });
                    this.socket.on('force_logout', () => { window.location.href = '/logout'; });
                    
                    this.socket.on('new_message', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            if(data.sender_id !== this.myId) {
                                this.reloadCurrentMessages();
                            } else {
                                let match = this.messages.find(m => m.client_temp_id === data.client_temp_id || m.id === data.client_temp_id);
                                if (match) {
                                    match.id = data.id;
                                    match.is_pending = false; 
                                    delete this.pendingMessagesToSend[data.client_temp_id];
                                    this.savePendingQueue(); 
                                } else {
                                    this.messages.push(data);
                                }
                                this.scrollToBottom();
                            }
                        } else if (data.sender_id === this.myId && data.client_temp_id) {
                            delete this.pendingMessagesToSend[data.client_temp_id];
                            this.savePendingQueue(); 
                        }
                        this.loadChats();
                    });
                    
                    this.socket.on('message_updated', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) this.reloadCurrentMessages();
                    });
                    this.socket.on('messages_read', (data) => {
                        if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                            this.messages.forEach(m => { if (m.sender_id === this.myId) m.is_read = true; });
                        }
                        this.loadChats();
                    });
                    this.socket.on('typing_status', (data) => {
                        this.typing[data.chat_id] = data.is_typing;
                        setTimeout(() => { this.typing[data.chat_id] = false }, 3000);
                    });
                    this.socket.on('block_status_changed', (data) => {
                        this.loadChats().then(() => {
                            if (this.currentChat && this.currentChat.chat_id === data.chat_id) {
                                const updatedChat = this.chats.find(c => c.chat_id === data.chat_id);
                                if (updatedChat) this.currentChat = updatedChat;
                            }
                        });
                    });
                    this.socket.on('status_update', (data) => {
                         let chat = this.chats.find(c => c.partner_id === data.user_id && !c.is_group);
                         let customStatus = (this.myProfileData.perm_see_chatting_with && data.chatting_with_name) ? `общается с: ${data.chatting_with_name}` : null;
                         if (chat) {
                             chat.is_online = data.status === 'online';
                             if(data.last_seen) chat.last_seen = data.last_seen;
                             chat.custom_status = customStatus;
                         }
                         if (this.currentChat && !this.currentChat.is_group && this.currentChat.partner_id === data.user_id) {
                             this.currentChat.is_online = data.status === 'online';
                             if(data.last_seen) this.currentChat.last_seen = data.last_seen;
                             this.currentChat.custom_status = customStatus;
                         }
                         if (this.viewProfileData.id === data.user_id) {
                             this.viewProfileData.is_online = data.status === 'online';
                             if(data.last_seen) this.viewProfileData.last_seen = data.last_seen;
                             this.viewProfileData.custom_status = customStatus;
                         }
                         this.loadChats();
                    });
                },

                closeChat() {
                    this.currentChat = null;
                    this.replyToMessage = null;
                    this.editMessage = null;
                    this.showMicInstructionBanner = false;
                    this.stopRecording();
                    this.socket.emit('close_chat');
                },

                openContextMenu(e, msg, isMobile) {
                    this.contextMenu.msg = msg;
                    if (isMobile) {
                        this.contextMenu.x = Math.min(this.touchX, window.innerWidth - 190);
                        this.contextMenu.y = Math.min(this.touchY, window.innerHeight - 200);
                    } else {
                        this.contextMenu.x = Math.min(e.clientX, window.innerWidth - 190);
                        this.contextMenu.y = Math.min(e.clientY, window.innerHeight - 200);
                    }
                    
                    let canDel = msg.sender_id === this.myId || this.myProfileData.has_admin_priv || this.myProfileData.is_admin || this.myProfileData.can_see_deleted;
                    if(this.currentChat && this.currentChat.is_group && !canDel) {
                        if(this.currentChat.i_am_owner || (this.currentChat.i_am_admin && this.currentChat.my_perms.delete_msgs)) {
                            canDel = true;
                        }
                    }
                    this.contextMenu.canDelete = canDel;
                    this.contextMenu.show = true;
                },
                handleTouchStart(e, msg) {
                    if (e.touches && e.touches[0]) {
                        this.touchX = e.touches[0].clientX;
                        this.touchY = e.touches[0].clientY;
                    }
                    this.longPressTimer = setTimeout(() => { this.openContextMenu(null, msg, true); }, 500);
                },
                handleTouchEnd() { clearTimeout(this.longPressTimer); },

                actionReply() { this.contextMenu.show = false; this.editMessage = null; this.replyToMessage = this.contextMenu.msg; },
                actionEdit() { this.contextMenu.show = false; this.replyToMessage = null; this.editMessage = this.contextMenu.msg; this.newMessage = this.contextMenu.msg.text; },
                cancelEdit() { this.editMessage = null; this.newMessage = ''; },
                actionForward() { this.contextMenu.show = false; this.forwardMessageTarget = this.contextMenu.msg; this.forwardModal = true; },
                executeForward(chatId) {
                    if (!this.forwardMessageTarget) return;
                    let tempId = 'pending_fwd_' + Date.now() + '_' + Math.random().toString(36).substr(2, 4);
                    let payload = {
                        client_temp_id: tempId,
                        chat_id: chatId, text: this.forwardMessageTarget.text,
                        image_base64: this.forwardMessageTarget.image_base64, voice_base64: this.forwardMessageTarget.voice_base64,
                        forwarded_from_id: this.forwardMessageTarget.sender_id
                    };
                    this.pendingMessagesToSend[tempId] = payload;
                    this.savePendingQueue(); 
                    if(this.socket.connected) this.socket.emit('send_message', payload);
                    this.forwardModal = false; this.forwardMessageTarget = null; this.loadChats();
                },
                actionDelete() {
                    this.contextMenu.show = false;
                    if(confirm("Удалить это сообщение?")) this.socket.emit('delete_message', { message_id: this.contextMenu.msg.id });
                },
                actionShowHistory() {
                    this.contextMenu.show = false;
                    this.historyText = this.contextMenu.msg.original_text || 'История изменений отсутствует.';
                    this.showHistoryModal = true;
                },

                async fetchMyProfile() {
                    const res = await fetch('/api/profile/me');
                    this.myProfileData = await res.json();
                    this.myLightnings = this.myProfileData.lightnings || 0;
                },
                openMyProfile() {
                    this.isMyProfile = true;
                    this.editMode = false;
                    this.privacyMode = false;
                    this.viewProfileData = { ...this.myProfileData };
                    this.pinnedGiftsSlots = this.myProfileData.pinned_gifts || [];
                    this.editProfileData = { ...this.myProfileData, new_password: '' };
                    this.showProfileModal = true;
                },
                async openUserProfile(userId) {
                    if(userId === this.myId) return this.openMyProfile();
                    this.isMyProfile = false;
                    this.editMode = false;
                    this.privacyMode = false;
                    const res = await fetch('/api/profile/' + userId);
                    this.viewProfileData = await res.json();
                    this.pinnedGiftsSlots = this.viewProfileData.pinned_gifts || [];
                    this.showProfileModal = true;
                },
                closeProfileModal() { this.showProfileModal = false; this.editMode = false; this.privacyMode = false; },
                translatePrivacy(val) {
                    if(val === 'nobody') return 'Никто';
                    if(val === 'contacts') return 'Контакты';
                    return 'Все';
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
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.editProfileData)
                    });
                    if(res.ok) {
                        await this.fetchMyProfile();
                        this.viewProfileData = { ...this.myProfileData };
                        this.editMode = false; this.privacyMode = false;
                    }
                },

                // ГРУППЫ API
                async executeCreateGroup() {
                    if(!this.newGroupName.trim()) return;
                    const res = await fetch('/api/chat/group/create', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ name: this.newGroupName.trim() })
                    });
                    const data = await res.json();
                    this.newGroupName = '';
                    this.showGroupCreateModal = false;
                    await this.loadChats();
                    const chat = this.chats.find(c => c.chat_id === data.chat_id);
                    if (chat) this.openChat(chat);
                },
                async openGroupProfile(chatId) {
                    const res = await fetch('/api/chat/group/' + chatId + '/info');
                    this.groupData = await res.json();
                    this.groupMembers = this.groupData.members;
                    this.groupTab = 'info';
                    this.groupEditMode = false;
                    this.showGroupProfileModal = true;
                },
                handleGroupAvatarSelect(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (e) => { this.groupData.avatar_url = e.target.result; };
                    reader.readAsDataURL(file);
                },
                async saveGroupProfile() {
                    await fetch('/api/chat/group/' + this.groupData.chat_id + '/update', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ name: this.groupData.name, description: this.groupData.description, avatar_url: this.groupData.avatar_url })
                    });
                    this.groupEditMode = false;
                    await this.loadChats();
                    const updated = this.chats.find(c => c.chat_id === this.currentChat.chat_id);
                    if(updated) this.currentChat = updated;
                },
                async saveGroupPerms() {
                    await fetch('/api/chat/group/' + this.groupData.chat_id + '/update_perms', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.groupData.perms)
                    });
                    alert("Разрешения сохранены");
                },
                async executeAddGroupMember() {
                    if(!this.groupAddUserQuery) return;
                    const res = await fetch('/api/chat/group/' + this.groupData.chat_id + '/add_member', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ username: this.groupAddUserQuery.replace('@','') })
                    });
                    const data = await res.json();
                    if(data.success) {
                        this.groupAddUserQuery = '';
                        this.openGroupProfile(this.groupData.chat_id);
                    } else alert(data.error);
                },
                openManageMember(member) {
                    this.manageMemberData = { ...member };
                    this.showManageMemberModal = true;
                },
                async saveManageMember() {
                    await fetch('/api/chat/group/' + this.groupData.chat_id + '/admin', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.manageMemberData)
                    });
                    this.showManageMemberModal = false;
                    this.openGroupProfile(this.groupData.chat_id);
                },
                async executeKickMember() {
                    if(!confirm("Исключить пользователя из группы?")) return;
                    await fetch('/api/chat/group/' + this.groupData.chat_id + '/kick', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ target_id: this.manageMemberData.user_id })
                    });
                    this.showManageMemberModal = false;
                    this.openGroupProfile(this.groupData.chat_id);
                },
                async executeLeaveGroup() {
                    if(!confirm("Покинуть группу навсегда?")) return;
                    await fetch('/api/chat/group/' + this.groupData.chat_id + '/leave', { method: 'POST' });
                    this.showGroupProfileModal = false;
                    this.closeChat();
                    this.loadChats();
                },

                openContactModal() {
                    this.contactCustomName = this.currentChat.contact_custom_name || '';
                    this.showContactModal = true;
                },
                async saveContactCustomName() {
                    if(!this.currentChat) return;
                    await fetch('/api/contact/save/' + this.currentChat.partner_id, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ custom_name: this.contactCustomName })
                    });
                    this.showContactModal = false;
                    await this.loadChats();
                    const updated = this.chats.find(c => c.chat_id === this.currentChat.chat_id);
                    if(updated) this.currentChat = updated;
                },
                async togglePersonalBlock() {
                    if(!this.currentChat) return;
                    await fetch('/api/block/toggle/' + this.currentChat.partner_id, { method: 'POST' });
                },

                // ПОДАРКИ И КВЕСТЫ
                async openQuestsModal() {
                    const res = await fetch('/api/quests');
                    const data = await res.json();
                    this.myLightnings = data.lightnings;
                    this.questItems = data.quests;
                    this.showQuestsModal = true;
                },
                async executeClaimQuest(questId) {
                    const res = await fetch('/api/quests/claim', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ quest_id: questId })
                    });
                    const data = await res.json();
                    if(data.success) {
                        this.myLightnings = data.total;
                        this.questItems[questId].claimed = true;
                        if(this.isMyProfile) this.myProfileData.lightnings = data.total;
                    } else alert(data.error);
                },

                getPinnedGiftAt(slotIndex) {
                    return this.pinnedGiftsSlots.find(g => g.slot_index === slotIndex);
                },
                handleGiftSlotClick(slotIndex) {
                    const existing = this.getPinnedGiftAt(slotIndex);
                    if(existing) {
                        this.selectedGiftView = existing;
                        this.showGiftViewModal = true;
                    } else if(this.isMyProfile) {
                        this.pinningSlotIndex = slotIndex;
                        this.openGiftsShop(true); 
                    }
                },
                async openGiftsShop(forceInventory = false) {
                    if (this.isMyProfile || forceInventory) {
                        this.shopModeInventory = forceInventory || (event && event.target.innerText.includes('Мои'));
                        if(!this.shopModeInventory) {
                            const res = await fetch('/api/shop');
                            const data = await res.json();
                            this.shopOfficial = data.new_store;
                            this.shopMarket = data.user_market;
                        } else {
                            const res = await fetch('/api/gifts/my');
                            this.myGiftsInventory = await res.json();
                        }
                        this.showShopModal = true;
                    } else {
                        const res = await fetch('/api/gifts/user/' + this.viewProfileData.id);
                        this.partnerGiftsList = await res.json();
                        this.showPartnerGiftsModal = true;
                    }
                },
                async executeBuyOfficialGift(defId) {
                    const res = await fetch('/api/shop/buy_official', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ def_id: defId })
                    });
                    const data = await res.json();
                    if(data.success) {
                        this.myLightnings = data.new_balance;
                        alert("Подарок приобретен! Он добавлен в вашу коллекцию.");
                    } else alert(data.error);
                },
                async executeBuyMarketGift(userGiftId) {
                    const res = await fetch('/api/shop/buy_market', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ user_gift_id: userGiftId })
                    });
                    const data = await res.json();
                    if(data.success) {
                        this.myLightnings = data.new_balance;
                        this.shopMarket = this.shopMarket.filter(m => m.user_gift_id !== userGiftId);
                        alert("Подарок куплен с рынка!");
                    } else alert(data.error);
                },
                async executePinGiftToSlot(userGiftId) {
                    if(this.pinningSlotIndex === null) return;
                    await fetch('/api/gifts/pin', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ user_gift_id: userGiftId, slot_index: this.pinningSlotIndex })
                    });
                    this.pinningSlotIndex = null;
                    this.showShopModal = false;
                    await this.fetchMyProfile();
                    this.pinnedGiftsSlots = this.myProfileData.pinned_gifts || [];
                },
                async executeUnpinGiftSlot(slotIndex) {
                    await fetch('/api/gifts/unpin', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ slot_index: slotIndex })
                    });
                    this.showGiftViewModal = false;
                    await this.fetchMyProfile();
                    this.pinnedGiftsSlots = this.myProfileData.pinned_gifts || [];
                },
                async executeToggleMarketSale(userGiftId, priceStr) {
                    const res = await fetch('/api/gifts/market_toggle', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ user_gift_id: userGiftId, price: priceStr })
                    });
                    const data = await res.json();
                    if(data.success) {
                        this.selectedGiftView.is_for_sale = data.is_for_sale;
                        this.selectedGiftView.sale_price = data.is_for_sale ? priceStr : null;
                        alert(data.is_for_sale ? 'Выставлено на рынок!' : 'Снято с продажи');
                    } else alert(data.error || 'Ошибка');
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
                    this.replyToMessage = null;
                    this.editMessage = null;
                    this.showMicInstructionBanner = false;
                    this.stopRecording();
                    this.socket.emit('open_chat', { partner_id: chat.partner_id });
                    await this.reloadCurrentMessages();
                },
                async reloadCurrentMessages() {
                    if (!this.currentChat) return;
                    const res = await fetch('/api/chat/' + this.currentChat.chat_id + '/messages');
                    let dbMsgs = await res.json();
                    let unconfirmed = [];
                    for (let tId in this.pendingMessagesToSend) {
                        let p = this.pendingMessagesToSend[tId];
                        if (p.chat_id === this.currentChat.chat_id) {
                            unconfirmed.push({
                                id: tId, client_temp_id: tId, sender_id: this.myId,
                                text: p.text || '', image_base64: p.image_base64 || null, voice_base64: p.voice_base64 || null,
                                time: new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}),
                                is_read: false, is_pending: true
                            });
                        }
                    }
                    this.messages = [...dbMsgs, ...unconfirmed];
                    this.scrollToBottom();
                },
                
                canInputMessages() {
                    if(!this.currentChat) return false;
                    if(!this.currentChat.is_group) return !this.currentChat.partner_is_banned && !this.currentChat.i_blocked_partner && !this.currentChat.partner_blocked_me;
                    if(this.currentChat.i_am_banned_in_group) return false;
                    if(this.currentChat.i_am_admin || this.currentChat.i_am_owner) return true;
                    return this.currentChat.perms.send_text || this.currentChat.perms.send_photos || this.currentChat.perms.send_voice || this.imagePreview || this.videoPreview || this.filePreview;
                },

                handleFileSelect(event, type) {
                    const file = event.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (e) => { 
                        if (type === 'photo') { this.imagePreview = e.target.result; this.videoPreview = null; this.filePreview = null; }
                        else if (type === 'video') { this.videoPreview = e.target.result; this.imagePreview = null; this.filePreview = null; }
                        else if (type === 'file') { this.filePreview = e.target.result; this.previewFileName = file.name; this.imagePreview = null; this.videoPreview = null; }
                    };
                    reader.readAsDataURL(file);
                },
                
                sendMessage() {
                    if (!this.newMessage.trim() && !this.imagePreview && !this.videoPreview && !this.filePreview) return;
                    
                    if (this.editMessage) {
                        this.socket.emit('edit_message', {
                            message_id: this.editMessage.id, text: this.newMessage.trim()
                        });
                        this.editMessage = null;
                    } else {
                        let tempId = 'pending_' + Date.now() + '_' + Math.random().toString(36).substr(2, 4);
                        let payload = {
                            client_temp_id: tempId,
                            chat_id: this.currentChat.chat_id, text: this.newMessage.trim(),
                            image_base64: this.imagePreview, video_base64: this.videoPreview, file_base64: this.filePreview, file_name: this.previewFileName,
                            reply_to_id: this.replyToMessage ? this.replyToMessage.id : null
                        };
                        let repText = this.replyToMessage ? (this.replyToMessage.voice_base64 ? '[Голосовое]' : (this.replyToMessage.text || '[Фото/Файл]')) : '';
                        let localObj = {
                            id: tempId, client_temp_id: tempId, sender_id: this.myId,
                            text: payload.text, image_base64: payload.image_base64, video_base64: payload.video_base64, file_base64: payload.file_base64, file_name: payload.file_name, voice_base64: null,
                            time: new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}),
                            is_read: false, is_deleted: false, is_edited: false,
                            is_pending: true,
                            reply_to_id: payload.reply_to_id, reply_text: repText
                        };
                        this.messages.push(localObj);
                        this.scrollToBottom();

                        this.pendingMessagesToSend[tempId] = payload;
                        this.savePendingQueue(); 
                        
                        if (this.socket.connected) this.socket.emit('send_message', payload);
                        
                        this.replyToMessage = null;
                    }
                    this.newMessage = '';
                    this.imagePreview = null;
                    this.videoPreview = null;
                    this.filePreview = null;
                    this.previewFileName = null;
                    this.showEmojiPicker = false;
                },

                async toggleVoiceRecord() {
                    const isMobileAgent = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
                    const isMobileLayout = window.innerWidth < 768; 
                    
                    if ((isMobileAgent || isMobileLayout) && !localStorage.getItem('mic_mobile_instructed')) {
                        this.showMicInstructionBanner = true;
                        localStorage.setItem('mic_mobile_instructed', 'true');
                        return;
                    }

                    if (!this.isRecording) await this.startRecording();
                    else await this.stopRecording();
                },
                async startRecording() {
                    try {
                        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                        this.mediaRecorder = new MediaRecorder(stream);
                        this.audioChunks = []; this.recordTimer = 0; this.isRecording = true;
                        this.recordInterval = setInterval(() => { this.recordTimer++; }, 1000);

                        this.mediaRecorder.ondataavailable = (e) => {
                            if (e.data.size > 0) this.audioChunks.push(e.data);
                        };

                        this.mediaRecorder.onstop = () => {
                            clearInterval(this.recordInterval);
                            this.isRecording = false;

                            const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                            const reader = new FileReader();
                            reader.readAsDataURL(audioBlob);
                            reader.onloadend = () => {
                                const base64Audio = reader.result;
                                let tempId = 'pending_voice_' + Date.now() + '_' + Math.random().toString(36).substr(2, 4);
                                let payload = {
                                    client_temp_id: tempId, chat_id: this.currentChat.chat_id, 
                                    voice_base64: base64Audio, reply_to_id: this.replyToMessage ? this.replyToMessage.id : null
                                };
                                let repText = this.replyToMessage ? (this.replyToMessage.voice_base64 ? '[Голосовое]' : (this.replyToMessage.text || '[Фото/Файл]')) : '';
                                let localObj = {
                                    id: tempId, client_temp_id: tempId, sender_id: this.myId,
                                    text: '', image_base64: null, voice_base64: base64Audio,
                                    time: new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}),
                                    is_read: false, is_deleted: false, is_edited: false,
                                    is_pending: true,
                                    reply_to_id: payload.reply_to_id, reply_text: repText
                                };
                                this.messages.push(localObj);
                                this.scrollToBottom();

                                this.pendingMessagesToSend[tempId] = payload;
                                this.savePendingQueue(); 
                                
                                if (this.socket.connected) this.socket.emit('send_message', payload);

                                this.replyToMessage = null;
                            };
                            stream.getTracks().forEach(track => track.stop());
                        };
                        this.mediaRecorder.start();
                    } catch (err) {
                        alert('Не удалось получить доступ к микрофону: ' + err.message);
                        this.isRecording = false;
                    }
                },
                cancelRecording() {
                    if (this.mediaRecorder && this.isRecording) {
                        this.isRecording = false;
                        clearInterval(this.recordInterval);
                        this.mediaRecorder.onstop = null; // Отменяем событие отправки
                        this.mediaRecorder.stop();
                        this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
                        this.audioChunks = [];
                    }
                },
                stopRecording() { if (this.mediaRecorder && this.isRecording) this.mediaRecorder.stop(); },
                formatTimer(seconds) {
                    const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
                    const secs = (seconds % 60).toString().padStart(2, '0');
                    return `${mins}:${secs}`;
                },

                sendTyping() { if (this.currentChat) this.socket.emit('typing', { chat_id: this.currentChat.chat_id }); },
                scrollToBottom() {
                    setTimeout(() => {
                        const box = document.getElementById('messagesBox');
                        if (box) box.scrollTop = box.scrollHeight;
                    }, 100);
                }
            }
        }
    </script>
</body></html>
"""

ADMIN_TEMPLATE = BASE_HTML_HEAD + """
    <div class="container mx-auto p-4 md:p-6 pt-10 flex-1 overflow-y-auto max-h-full" x-data="adminApp()">
        <div class="flex justify-between items-center mb-8 flex-shrink-0">
            <h1 class="text-xl md:text-3xl font-bold text-white">Панель Управления</h1>
            <a href="{{ url_for('index') }}" class="text-blue-400 hover:text-blue-300 transition text-sm md:text-base">&larr; В мессенджер</a>
        </div>

        {% with messages = get_flashed_messages() %}{% if messages %}
            <div class="bg-blue-500/20 text-blue-300 p-3 rounded mb-4 text-sm border border-blue-500">
              {% for message in messages %}{{ message }}<br>{% endfor %}
            </div>
        {% endif %}{% endwith %}

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            {% if current_user.is_admin or current_user.perm_grant_lightnings %}
            <form action="{{ url_for('admin_grant_lightnings') }}" method="POST" class="bg-gray-800 p-4 rounded-2xl border border-gray-700 flex flex-col gap-2.5 shadow">
                <div class="font-black text-blue-400 text-xs md:text-sm flex items-center gap-1"><img src="/molniya.png" class="w-5 h-5 object-contain"><span>Зачислить молнии</span></div>
                <select name="user_id" class="bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs outline-none">
                    {% for u in users %}<option value="{{ u.id }}">{{ u.username }} ({{ u.first_name }})</option>{% endfor %}
                </select>
                <input type="number" name="amount" placeholder="Количество" required class="bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs outline-none">
                <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white font-black py-2 rounded-xl text-xs transition">Зачислить</button>
            </form>

            <form action="{{ url_for('admin_deduct_lightnings') }}" method="POST" class="bg-gray-800 p-4 rounded-2xl border border-gray-700 flex flex-col gap-2.5 shadow">
                <div class="font-black text-red-400 text-xs md:text-sm flex items-center gap-1"><img src="/molniya.png" class="w-5 h-5 object-contain"><span>Списать молнии</span></div>
                <select name="user_id" class="bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs outline-none">
                    {% for u in users %}<option value="{{ u.id }}">{{ u.username }} ({{ u.first_name }})</option>{% endfor %}
                </select>
                <input type="number" name="amount" placeholder="Количество" required class="bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs outline-none">
                <button type="submit" class="bg-red-600 hover:bg-red-500 text-white font-black py-2 rounded-xl text-xs transition">Списать</button>
            </form>
            {% endif %}

            {% if current_user.is_admin or current_user.perm_grant_gifts %}
            <form action="{{ url_for('admin_grant_gift') }}" method="POST" class="bg-gray-800 p-4 rounded-2xl border border-gray-700 flex flex-col gap-2.5 shadow">
                <div class="font-black text-blue-400 text-xs md:text-sm flex items-center gap-1"><span>🎁</span><span>Выдать подарок</span></div>
                <select name="user_id" class="bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs outline-none">
                    {% for u in users %}<option value="{{ u.id }}">{{ u.username }} ({{ u.first_name }})</option>{% endfor %}
                </select>
                <select name="def_id" class="bg-gray-900 border border-gray-600 rounded-xl p-2 text-white text-xs outline-none">
                    <option value="1">Песочный замок (1)</option>
                    <option value="2">Пляжный зонт (2)</option>
                    <option value="3">Шезлонг (3)</option>
                    <option value="4">Спасательный круг (4)</option>
                </select>
                <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white font-black py-2 rounded-xl text-xs transition">Подарить</button>
            </form>
            {% endif %}
        </div>

        <div class="bg-gray-800 rounded-xl shadow-xl border border-gray-700 overflow-x-auto mb-10">
            <table class="w-full text-left border-collapse min-w-[750px]">
                <thead>
                    <tr class="bg-gray-900 border-b border-gray-700 text-gray-400 uppercase text-[10px] md:text-xs">
                        <th class="p-3 md:p-4">ID</th><th class="p-3 md:p-4">Пользователь / Ник</th><th class="p-3 md:p-4">Статус</th><th class="p-3 md:p-4 text-right">Действия</th>
                    </tr>
                </thead>
                <tbody class="text-xs md:text-sm">
                    {% for u in users %}
                    <tr class="border-b border-gray-700 hover:bg-gray-750 transition">
                        <td class="p-3 md:p-4 text-gray-500">#{{ u.id }}</td>
                        <td class="p-3 md:p-4">
                            <div class="font-semibold text-white flex items-center gap-2">
                                 {{ u.first_name }} {{ u.last_name or '' }}
                                {% if u.is_admin %}<span class="admin-badge">Admin</span>{% endif %}
                                {% if u.is_moderator %}<span class="mod-badge">Moderator</span>{% endif %}
                                {% if u.banned_until %}<span class="bg-red-950 border border-red-700 text-red-400 px-1.5 py-0.5 rounded text-[10px] font-bold">Banned</span>{% endif %}
                            </div>
                            <div class="text-[10px] md:text-xs text-blue-400 flex items-center gap-2">
                                <span>@{{ u.username }}</span><span class="text-blue-400 font-mono font-bold flex items-center gap-0.5">{{ u.lightnings or 0 }}<img src="/molniya.png" class="w-4 h-4 inline-block"></span>
                            </div>
                        </td>
                        <td class="p-3 md:p-4 text-gray-400">
                            {% if u.id in connected %} <span class="text-blue-500 font-bold">В сети</span> 
                            {% else %} Был(а) {{ format_last_seen(u.last_seen) }}{% endif %}
                        </td>
                        <td class="p-3 md:p-4 text-right space-x-1 md:space-x-2">
                            <button @click="openHistory({{ u.id }}, '{{ u.first_name }} {{ u.last_name or '' }}')" class="inline-block bg-indigo-900/50 hover:bg-indigo-800 text-indigo-300 border border-indigo-700 px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition">Список общения</button>
                            
                            {% if can_ban_users %}
                                {% if u.id != current_user.id and not u.is_admin %}
                                    <button @click="openBanModal({{ u.id }}, '{{ u.first_name }} {{ u.last_name or '' }}', '{{ 'forever' if u.banned_until and u.banned_until.year >= 9999 else (u.banned_until.strftime('%Y-%m-%dT%H:%M') if u.banned_until else '') }}')" class="inline-block bg-red-900/50 hover:bg-red-800 text-red-300 border border-red-700 px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition">
                                        {{ 'Разблокировать' if u.banned_until else 'Блокировка' }}
                                    </button>
                                {% endif %}
                             {% endif %}

                            {% if has_admin_priv %}
                                {% if u.id != current_user.id %}
                                    <button @click="openPerms({ id: {{ u.id }}, is_admin: {{ 'true' if u.is_admin else 'false' }}, is_moderator: {{ 'true' if u.is_moderator else 'false' }}, perm_edit_history: {{ 'true' if u.perm_edit_history else 'false' }}, perm_deleted_messages: {{ 'true' if u.perm_deleted_messages else 'false' }}, perm_see_chatting_with: {{ 'true' if u.perm_see_chatting_with else 'false' }}, perm_ban_users: {{ 'true' if u.perm_ban_users else 'false' }}, perm_grant_gifts: {{ 'true' if u.perm_grant_gifts else 'false' }}, perm_grant_lightnings: {{ 'true' if u.perm_grant_lightnings else 'false' }} })" class="inline-block bg-green-900/50 hover:bg-green-800 text-green-300 border border-green-700 px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition">Управление правами</button>
                                    {% if current_user.promoted_by_id != u.id %}
                                         <a href="{{ url_for('impersonate', target_id=u.id) }}" class="inline-block bg-blue-600 hover:bg-blue-500 text-white px-2 py-1 md:px-3 md:py-1.5 rounded text-[10px] md:text-xs transition shadow">Войти как</a>
                                    {% endif %}
                                {% else %}<span class="text-gray-600 text-[10px] md:text-xs italic">Это вы</span>{% endif %}
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                 </tbody>
             </table>
        </div>

        <div x-show="showPermsModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" x-transition.opacity.duration.200ms @click.self="showPermsModal = false">
            <div class="bg-[#1e293b] p-6 rounded-2xl border border-gray-700 w-full max-w-sm shadow-2xl">
                <h3 class="text-white font-bold text-lg mb-4 border-b border-gray-700 pb-2">Права пользователя</h3>
                <div class="space-y-2.5 mb-6 max-h-60 overflow-y-auto pr-1">
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.is_admin" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-red-400">sudo admin</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.is_moderator" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-green-400">sudo moderate</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.perm_ban_users" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-purple-400">sudo блокировка</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.perm_grant_gifts" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-pink-400">sudo выдача подарков</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.perm_grant_lightnings" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-yellow-400">sudo выдача молний</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.perm_edit_history" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-gray-300">sudo история изменений</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.perm_deleted_messages" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-gray-300">sudo удаленные сообщения</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" x-model="permsUser.perm_see_chatting_with" class="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded"><span class="text-sm font-medium text-gray-300">sudo с кем общается</span></label>
                </div>
                <div class="flex gap-2">
                    <button @click="savePerms()" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white font-bold py-2.5 rounded-xl transition text-sm">Сохранить</button>
                    <button @click="showPermsModal = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white font-bold py-2.5 rounded-xl transition text-sm">Отмена</button>
                </div>
            </div>
         </div>

        <div x-show="showBanModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" x-transition.opacity.duration.200ms @click.self="showBanModal = false">
            <div class="bg-[#1e293b] p-6 rounded-xl border border-red-700 w-full max-w-sm shadow-2xl">
                <h3 class="text-red-400 font-bold text-lg mb-4 border-b border-gray-700 pb-2">Блокировка: <span class="text-white" x-text="banTargetName"></span></h3>
                <div class="space-y-4 mb-6">
                    <div>
                        <label class="flex items-center gap-2 cursor-pointer mb-2"><input type="radio" name="bmode" value="forever" x-model="banMode" class="text-red-600 bg-gray-700 border-gray-600"><span class="text-sm text-white font-semibold">Вечная блокировка</span></label>
                        <label class="flex items-center gap-2 cursor-pointer"><input type="radio" name="bmode" value="temporary" x-model="banMode" class="text-red-600 bg-gray-700 border-gray-600"><span class="text-sm text-white font-semibold">Свое время блокировки</span></label>
                    </div>
                    <div x-show="banMode === 'temporary'" class="pt-2">
                        <label class="text-xs text-gray-400 block mb-1">Разблокировать в (МСК):</label>
                        <input type="datetime-local" x-model="banCustomDate" class="w-full bg-gray-900 border border-gray-700 rounded p-2 text-white text-sm focus:ring-1 focus:ring-red-500">
                    </div>
                </div>
                <div class="flex flex-col gap-2">
                    <div class="flex gap-2">
                        <button @click="executeBan()" class="flex-1 bg-red-600 hover:bg-red-500 text-white font-bold py-2 rounded transition">Применить</button>
                        <button @click="showBanModal = false" class="flex-1 bg-gray-700 hover:bg-gray-600 text-white font-bold py-2 rounded transition">Отмена</button>
                    </div>
                    <button @click="executeUnban()" class="w-full bg-green-700/60 hover:bg-green-700 text-green-200 font-bold py-1.5 rounded text-xs transition mt-2">Снять блокировку</button>
                </div>
             </div>
        </div>

        <div x-show="showHistoryModal" style="display: none;" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" x-transition.opacity.duration.200ms @click.self="showHistoryModal = false">
            <div class="bg-[#1e293b] p-6 rounded-xl border border-gray-700 w-full max-w-lg shadow-2xl flex flex-col max-h-[80vh]">
                <h3 class="text-white font-bold text-lg mb-4 border-b border-gray-700 pb-2">Общение за 24ч: <span class="text-blue-400" x-text="historyUserName"></span></h3>
                <div class="flex-1 overflow-y-auto mb-4">
                    <template x-if="historyData.length === 0"><div class="text-gray-500 text-sm italic text-center py-4">Нет активности за последние сутки.</div></template>
                    <div class="space-y-2">
                         <template x-for="item in historyData" :key="item.username">
                            <div class="bg-gray-800 p-3 rounded border border-gray-700 flex justify-between items-center">
                                <div><div class="text-sm font-bold text-white" x-text="item.name"></div><div class="text-xs text-blue-400" x-text="'@' + item.username"></div></div>
                                <div class="text-xs font-mono text-gray-400 bg-gray-900 px-2 py-1 rounded" x-text="item.time_range"></div>
                             </div>
                        </template>
                     </div>
                </div>
                <button @click="showHistoryModal = false" class="w-full bg-gray-700 hover:bg-gray-600 text-white font-bold py-2 rounded transition">Закрыть</button>
             </div>
        </div>

    </div>

    <script>
        function adminApp() {
             return {
                showPermsModal: false,
                permsUser: {},
                showHistoryModal: false,
                historyUserName: '',
                historyData: [],

                showBanModal: false,
                banTargetId: null,
                banTargetName: '',
                banMode: 'temporary',
                banCustomDate: '',

                openPerms(userData) { this.permsUser = userData; this.showPermsModal = true; },
                async savePerms() {
                    await fetch('/api/admin/permissions/' + this.permsUser.id, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.permsUser)
                    });
                    location.reload();
                },
                async openHistory(userId, userName) {
                    this.historyUserName = userName;
                    const res = await fetch('/api/admin/history_24h/' + userId);
                    this.historyData = await res.json();
                    this.showHistoryModal = true;
                },

                openBanModal(id, name, currentBan) {
                    this.banTargetId = id;
                    this.banTargetName = name;
                    if (currentBan === 'forever') this.banMode = 'forever';
                    else if (currentBan !== '') { this.banMode = 'temporary'; this.banCustomDate = currentBan; }
                    else {
                        this.banMode = 'temporary';
                        let tomorrow = new Date(Date.now() + 86400000);
                        let tzoffset = tomorrow.getTimezoneOffset() * 60000;
                        this.banCustomDate = new Date(tomorrow - tzoffset).toISOString().slice(0, 16);
                    }
                    this.showBanModal = true;
                },
                async executeBan() {
                    await fetch('/api/admin/ban/' + this.banTargetId, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ action: 'ban', type: this.banMode, until: this.banCustomDate })
                    });
                    location.reload();
                },
                async executeUnban() {
                    await fetch('/api/admin/ban/' + this.banTargetId, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ action: 'unban' })
                    });
                    location.reload();
                }
            }
        }
    </script>
</body></html>
"""

# ==========================================
# МАРШРУТЫ И РОУТЫ КАРТИНОК
# ==========================================
@app.route('/logo.png')
def serve_logo():
    return send_from_directory(os.getcwd(), 'logo.png')

@app.route('/molniya.png')
def serve_molniya():
    return send_from_directory(os.getcwd(), 'molniya.png')

@app.route('/screen<int:num>')
def serve_instruction_screenshot(num):
    for ext in ['.jpg', '.png', '.jpeg', '.webp']:
        fname = f"screen{num}{ext}"
        if os.path.exists(os.path.join(os.getcwd(), fname)):
            return send_from_directory(os.getcwd(), fname)
    return "Скриншот не найден", 404

@app.route('/podarok<int:num>.png')
def serve_gift_png(num):
    fname = f"podarok{num}.png"
    if os.path.exists(os.path.join(os.getcwd(), fname)):
        return send_from_directory(os.getcwd(), fname)
    return "Подарок не найден", 404

@app.route('/micro')
@login_required
def micro_instruction_page():
    return render_template_string(MICRO_TEMPLATE)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and not session.get('original_admin_id'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username')
        password = request.form.get('password')

        if action == 'register':
            clean_un = username.lstrip('@').strip()
            if User.query.filter_by(username=clean_un).first():
                flash('Пользователь с таким логином уже существует')
                return redirect(url_for('login'))

            new_user = User(
                username=clean_un, password=password,
                first_name=request.form.get('first_name'),
                last_name=request.form.get('last_name') or None,
                last_seen=now_msk()
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for('index'))

        elif action == 'login':
            clean_username = username.lstrip('@').strip()
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
    current_user.last_seen = now_msk()
    db.session.commit()
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    banned, until_dt, is_perm = check_user_banned(current_user)
    if banned:
        ban_str = until_dt.strftime('%d.%m.%Y %H:%M:%S') if until_dt else ""
        return render_template_string(BANNED_TEMPLATE, is_permanent=is_perm, ban_date_str=ban_str)
    return render_template_string(APP_TEMPLATE)

# ==========================================
# API ПРОФИЛЯ, ПОДАРКОВ, КВЕСТОВ И МАГАЗИНА
# ==========================================
@app.route('/api/profile/me', methods=['GET', 'POST'])
@login_required
def my_profile():
    if request.method == 'POST':
        data = request.json or {}
        current_user.first_name = data.get('first_name', current_user.first_name)
        current_user.last_name = data.get('last_name') or None
        current_user.phone = data.get('phone')
        current_user.about_me = data.get('about_me')
        if data.get('avatar'): current_user.avatar_url = data.get('avatar')

        b_day = data.get('birth_day')
        b_month = data.get('birth_month')
        b_year = data.get('birth_year')
        if b_day and b_month and b_year:
            current_user.birth_date = f"{b_day}.{b_month}.{b_year}"

        if 'privacy_phone' in data: current_user.privacy_phone = data['privacy_phone']
        if 'privacy_bday' in data: current_user.privacy_bday = data['privacy_bday']
        if 'privacy_last_seen' in data: current_user.privacy_last_seen = data['privacy_last_seen']

        new_pwd = data.get('new_password')
        if new_pwd and new_pwd.strip() != "": current_user.password = new_pwd.strip()

        if current_user.username.lower() != 'admin':
            new_username = data.get('username')
            if new_username:
                clean_un = new_username.lstrip('@').strip()
                if clean_un and clean_un != current_user.username:
                    if not User.query.filter_by(username=clean_un).first():
                        current_user.username = clean_un

        db.session.commit()
        return jsonify({'status': 'ok'})

    bd = current_user.birth_date
    b_day, b_month, b_year = "", "", ""
    if bd and "." in bd:
        parts = bd.split(".")
        if len(parts) == 3: b_day, b_month, b_year = parts

    pinned = UserGift.query.filter_by(owner_id=current_user.id, is_pinned=True).all()
    pinned_data = [{
        'user_gift_id': p.id, 'slot_index': p.slot_index, 'name': p.gift_def.name,
        'img': f"/podarok{p.gift_def.id}.png", 'store_price': p.gift_def.price,
        'is_pinned': True, 'is_for_sale': p.is_for_sale, 'sale_price': p.sale_price
    } for p in pinned]

    return jsonify({
        'id': current_user.id, 
        'first_name': current_user.first_name, 'last_name': current_user.last_name,
        'username': current_user.username, 'avatar': current_user.avatar_url, 'phone': current_user.phone,
        'about_me': current_user.about_me, 'birth_day': b_day, 'birth_month': b_month, 'birth_year': b_year,
        'formatted_bday': format_bday(current_user.birth_date), 'privacy_phone': current_user.privacy_phone,
        'privacy_bday': current_user.privacy_bday, 'privacy_last_seen': current_user.privacy_last_seen,
        'is_admin': current_user.is_admin, 'is_moderator': current_user.is_moderator,
        'has_admin_priv': has_admin_priv(), 'can_see_deleted': can_see_deleted(), 'can_see_edits': can_see_edits(),
        'perm_see_chatting_with': can_see_chatting(), 'can_ban_users': can_ban_users(),
        'lightnings': current_user.lightnings or 0, 
        'pinned_gifts': pinned_data, 'is_online': True
    })

@app.route('/api/profile/<int:user_id>')
@login_required
def get_user_profile(user_id):
    user = User.query.get_or_404(user_id)
    show_ls = is_allowed_to_see(user, user.privacy_last_seen, current_user.id)
    last_seen_str = format_last_seen_str(user.last_seen) if show_ls else "недавно"

    custom_status = None
    if can_see_chatting() and user.id in active_chat_views:
        p_id = active_chat_views[user.id]
        p = User.query.get(p_id)
        if p: custom_status = f"общается с: {p.first_name} {p.last_name or ''}"

    contact = Contact.query.filter_by(user_id=current_user.id, contact_id=user.id, is_explicit=True).first()
    disp_name = contact.custom_name if (contact and contact.custom_name) else f"{user.first_name} {user.last_name or ''}"

    show_phone = is_allowed_to_see(user, user.privacy_phone, current_user.id)
    show_bday = is_allowed_to_see(user, user.privacy_bday, current_user.id)

    pinned = UserGift.query.filter_by(owner_id=user.id, is_pinned=True).all()
    pinned_data = [{
        'user_gift_id': p.id, 'slot_index': p.slot_index, 'name': p.gift_def.name,
        'img': f"/podarok{p.gift_def.id}.png", 'store_price': p.gift_def.price,
        'is_pinned': True, 'is_for_sale': p.is_for_sale, 'sale_price': p.sale_price
    } for p in pinned]

    return jsonify({
        'id': user.id, 'first_name': user.first_name, 'last_name': user.last_name,
        'display_name': disp_name, 'username': user.username, 'avatar': user.avatar_url,
        'is_admin': user.is_admin, 'is_moderator': user.is_moderator, 'is_online': user.id in connected_users,
        'last_seen': last_seen_str, 'custom_status': custom_status, 'phone': user.phone if show_phone else None,
        'about_me': user.about_me, 'formatted_bday': format_bday(user.birth_date) if show_bday else None,
        'pinned_gifts': pinned_data
    })

@app.route('/api/gifts/user/<int:target_id>')
@login_required
def get_user_all_gifts_endpoint(target_id):
    items = UserGift.query.filter_by(owner_id=target_id).order_by(UserGift.id.desc()).all()
    res = [{
        'user_gift_id': g.id, 'name': g.gift_def.name, 'store_price': g.gift_def.price,
        'img': f"/podarok{g.gift_def.id}.png", 'is_pinned': g.is_pinned,
        'acquired_at': g.acquired_at.strftime('%d.%m.%Y %H:%M')
    } for g in items]
    return jsonify(res)

@app.route('/api/quests', methods=['GET'])
@login_required
def get_daily_quests():
    q = get_or_create_quest(current_user.id)
    u = current_user
    q3_ready = bool(u.about_me and u.phone and u.birth_date)

    quests_map = {
        'q1': {'name': 'Написать 10 сообщений кому нибудь', 'progress': min(q.messages_sent, 10), 'target': 10, 'claimed': q.q1_claimed, 'reward': 5},
        'q2': {'name': 'Получить 2 ответа на сообщения', 'progress': min(q.replies_received, 2), 'target': 2, 'claimed': q.q2_claimed, 'reward': 7},
        'q3': {'name': 'Заполнить информацию о себе', 'progress': 1 if q3_ready else 0, 'target': 1, 'claimed': u.q3_claimed, 'reward': 20},
        'q4': {'name': 'Отправить 2 фотографии', 'progress': min(q.photos_sent, 2), 'target': 2, 'claimed': q.q4_claimed, 'reward': 3}
    }
    return jsonify({'lightnings': u.lightnings or 0, 'quests': quests_map})

@app.route('/api/quests/claim', methods=['POST'])
@login_required
def claim_quest_reward():
    qid = request.json.get('quest_id')
    q = get_or_create_quest(current_user.id)
    u = current_user
    rw = 0

    if qid == 'q1' and q.messages_sent >= 10 and not q.q1_claimed: q.q1_claimed = True; rw = 5
    elif qid == 'q2' and q.replies_received >= 2 and not q.q2_claimed: q.q2_claimed = True; rw = 7
    elif qid == 'q3' and (u.about_me and u.phone and u.birth_date) and not u.q3_claimed: u.q3_claimed = True; rw = 20
    elif qid == 'q4' and q.photos_sent >= 2 and not q.q4_claimed: q.q4_claimed = True; rw = 3
    else: return jsonify({'success': False, 'error': 'Задание не выполнено или уже забрано'})

    u.lightnings = (u.lightnings or 0) + rw
    db.session.commit()
    return jsonify({'success': True, 'total': u.lightnings})

@app.route('/api/shop', methods=['GET'])
@login_required
def get_gifts_shop():
    defs = GiftDefinition.query.all()
    market = UserGift.query.filter_by(is_for_sale=True).all()

    off_list = [{
        'def_id': d.id, 'name': d.name, 'price': d.price, 'img': f"/podarok{d.id}.png"
    } for d in defs]

    mkt_list = [{
        'user_gift_id': m.id, 'name': m.gift_def.name, 'price': m.sale_price,
        'store_price': m.gift_def.price, 'img': f"/podarok{m.gift_def.id}.png",
        'seller': m.owner.username, 'seller_id': m.owner.id
    } for m in market if m.owner_id != current_user.id]

    return jsonify({'new_store': off_list, 'user_market': mkt_list})

@app.route('/api/gifts/my', methods=['GET'])
@login_required
def get_my_all_gifts():
    my = UserGift.query.filter_by(owner_id=current_user.id).order_by(UserGift.id.desc()).all()
    res = [{
        'user_gift_id': g.id, 'name': g.gift_def.name, 'store_price': g.gift_def.price,
        'img': f"/podarok{g.gift_def.id}.png", 'is_pinned': g.is_pinned,
        'slot_index': g.slot_index, 'is_for_sale': g.is_for_sale, 'sale_price': g.sale_price
    } for g in my]
    return jsonify(res)

@app.route('/api/shop/buy_official', methods=['POST'])
@login_required
def buy_official_gift_endpoint():
    def_id = request.json.get('def_id')
    gdef = GiftDefinition.query.get(def_id)
    if not gdef or (current_user.lightnings or 0) < gdef.price:
        return jsonify({'success': False, 'error': 'Недостаточно молний'})

    current_user.lightnings -= gdef.price
    ug = UserGift(owner_id=current_user.id, gift_def_id=gdef.id)
    db.session.add(ug)
    db.session.commit()
    return jsonify({'success': True, 'new_balance': current_user.lightnings})

@app.route('/api/shop/buy_market', methods=['POST'])
@login_required
def buy_market_gift_endpoint():
    ug_id = request.json.get('user_gift_id')
    ug = UserGift.query.filter_by(id=ug_id, is_for_sale=True).first()
    if not ug: return jsonify({'success': False, 'error': 'Подарок уже продан'})
    if ug.owner_id == current_user.id: return jsonify({'success': False, 'error': 'Нельзя купить у себя'})
    if (current_user.lightnings or 0) < ug.sale_price: return jsonify({'success': False, 'error': 'Недостаточно молний'})

    seller = User.query.get(ug.owner_id)
    current_user.lightnings -= ug.sale_price
    seller.lightnings = (seller.lightnings or 0) + ug.sale_price

    ug.owner_id = current_user.id
    ug.is_for_sale = False
    ug.sale_price = None
    ug.is_pinned = False
    ug.slot_index = None
    db.session.commit()
    return jsonify({'success': True, 'new_balance': current_user.lightnings})

@app.route('/api/gifts/pin', methods=['POST'])
@login_required
def pin_gift_to_slot():
    ug_id = request.json.get('user_gift_id')
    slot = request.json.get('slot_index')
    if slot not in [0, 1, 2, 3]: return jsonify({'success': False})

    ug = UserGift.query.filter_by(id=ug_id, owner_id=current_user.id).first()
    if not ug: return jsonify({'success': False})

    old = UserGift.query.filter_by(owner_id=current_user.id, is_pinned=True, slot_index=slot).first()
    if old: old.is_pinned = False; old.slot_index = None

    ug.is_pinned = True
    ug.slot_index = slot
    ug.is_for_sale = False
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/gifts/unpin', methods=['POST'])
@login_required
def unpin_gift_slot():
    slot = request.json.get('slot_index')
    ug = UserGift.query.filter_by(owner_id=current_user.id, is_pinned=True, slot_index=slot).first()
    if ug: ug.is_pinned = False; ug.slot_index = None; db.session.commit()
    return jsonify({'success': True})

@app.route('/api/gifts/market_toggle', methods=['POST'])
@login_required
def toggle_gift_market_sale():
    ug_id = request.json.get('user_gift_id')
    price = request.json.get('price')

    ug = UserGift.query.filter_by(id=ug_id, owner_id=current_user.id).first()
    if not ug: return jsonify({'success': False})

    if ug.is_for_sale: ug.is_for_sale = False; ug.sale_price = None
    else:
        try:
            p = int(price)
            if p <= 0: raise ValueError
            ug.is_for_sale = True; ug.sale_price = p; ug.is_pinned = False; ug.slot_index = None
        except: return jsonify({'success': False, 'error': 'Неверная цена'})

    db.session.commit()
    return jsonify({'success': True, 'is_for_sale': ug.is_for_sale})

# ==========================================
# МАРШРУТЫ КОНТАКТОВ И БЛОКОВ
# ==========================================
@app.route('/api/contact/save/<int:partner_id>', methods=['POST'])
@login_required
def save_contact_endpoint(partner_id):
    data = request.json or {}
    custom_name = data.get('custom_name', '').strip()
    
    contact = Contact.query.filter_by(user_id=current_user.id, contact_id=partner_id).first()
    if not contact:
        contact = Contact(user_id=current_user.id, contact_id=partner_id)
        db.session.add(contact)
         
    contact.custom_name = custom_name if custom_name else None
    contact.is_explicit = True
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/block/toggle/<int:partner_id>', methods=['POST'])
@login_required
def toggle_personal_block_endpoint(partner_id):
    block = PersonalBlock.query.filter_by(blocker_id=current_user.id, blocked_id=partner_id).first()
    if block:
        db.session.delete(block)
        status = 'unblocked'
    else:
        new_block = PersonalBlock(blocker_id=current_user.id, blocked_id=partner_id)
        db.session.add(new_block)
        status = 'blocked'
    db.session.commit()
    
    my_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=current_user.id).all())
    target_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=partner_id).all())
    common = my_chats.intersection(target_chats)
    cid = list(common)[0] if common else 0

    socketio.emit('block_status_changed', {'chat_id': cid}, room=f"user_{current_user.id}", namespace='/')
    socketio.emit('block_status_changed', {'chat_id': cid}, room=f"user_{partner_id}", namespace='/')
    return jsonify({'status': status})

@app.route('/api/chats')
@login_required
def get_chats():
    participants = ChatParticipant.query.filter_by(user_id=current_user.id).all()
    chat_ids = [p.chat_id for p in participants]

    chats_data = []
    for cid in chat_ids:
        chat = Chat.query.get(cid)
        if not chat: continue
        
        last_msg = Message.query.filter_by(chat_id=cid).order_by(Message.timestamp.desc()).first()
        lmsg_preview = ''
        if last_msg:
            if last_msg.voice_base64: lmsg_preview = '[Голосовое]'
            elif last_msg.text: lmsg_preview = last_msg.text
            elif last_msg.image_base64 or last_msg.video_base64 or last_msg.file_base64: lmsg_preview = '[Вложение]'

        if chat.type == 'group':
            my_cp = ChatParticipant.query.filter_by(chat_id=cid, user_id=current_user.id).first()
            i_am_banned = my_cp.perm_ban_users == None 
            
            chats_data.append({
                'chat_id': cid, 'is_group': True, 'partner_id': cid, 
                'partner_name': chat.name or "Группа", 'partner_avatar': chat.avatar_url,
                'member_count': ChatParticipant.query.filter_by(chat_id=cid).count(),
                'custom_status': chat.description, 'last_message': lmsg_preview,
                'last_time': last_msg.timestamp.strftime('%H:%M') if last_msg else '',
                'is_online': False, 'partner_is_banned': False, 'i_blocked_partner': False, 'partner_blocked_me': False,
                'perms': {
                    'send_text': chat.global_send_text, 'send_photos': chat.global_send_photos,
                    'send_voice': chat.global_send_voice, 'send_emoji': chat.global_send_emoji
                },
                'i_am_admin': my_cp.is_admin, 'i_am_owner': chat.owner_id == current_user.id,
                'i_am_banned_in_group': False
            })
        else:
            partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == cid, ChatParticipant.user_id != current_user.id).first()
            if not partner_cp: continue

            partner = User.query.get(partner_cp.user_id)
            custom_status = None
            if can_see_chatting() and partner.id in active_chat_views:
                p = User.query.get(active_chat_views[partner.id])
                if p: custom_status = f"общается с: {p.first_name} {p.last_name or ''}"

            partner_banned, _, _ = check_user_banned(partner)
            contact = Contact.query.filter_by(user_id=current_user.id, contact_id=partner.id, is_explicit=True).first()
            disp_name = contact.custom_name if (contact and contact.custom_name) else f"{partner.first_name} {partner.last_name or ''}"

            i_blocked = PersonalBlock.query.filter_by(blocker_id=current_user.id, blocked_id=partner.id).first() is not None
            partner_blocked = PersonalBlock.query.filter_by(blocker_id=partner.id, blocked_id=current_user.id).first() is not None

            show_ls = is_allowed_to_see(partner, partner.privacy_last_seen, current_user.id)
            ls_str = format_last_seen_str(partner.last_seen) if show_ls else "недавно"

            chats_data.append({
                'chat_id': cid, 'is_group': False, 'partner_id': partner.id, 'partner_name': disp_name,
                'partner_avatar': partner.avatar_url,
                'contact_custom_name': contact.custom_name if contact else '',
                'is_explicit_contact': contact is not None, 'partner_is_admin': partner.is_admin,
                'partner_is_moderator': partner.is_moderator, 'partner_is_banned': partner_banned,
                'i_blocked_partner': i_blocked, 'partner_blocked_me': partner_blocked,
                'custom_status': custom_status, 'last_message': lmsg_preview,
                'last_time': last_msg.timestamp.strftime('%H:%M') if last_msg else '',
                'is_online': partner.id in connected_users, 'last_seen': ls_str,
                'perms': {}
            })

    chats_data.sort(key=lambda x: x['last_time'], reverse=True)
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
        'id': u.id, 'first_name': u.first_name, 'last_name': u.last_name,
        'username': u.username, 'avatar': u.avatar_url, 'is_admin': u.is_admin, 'is_moderator': u.is_moderator
    } for u in users])

@app.route('/api/chat/start/<int:target_id>', methods=['POST'])
@login_required
def start_chat(target_id):
    my_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=current_user.id).all())
    target_chats = set(cp.chat_id for cp in ChatParticipant.query.filter_by(user_id=target_id).all())
    
    common = None
    for cid in my_chats.intersection(target_chats):
        c = Chat.query.get(cid)
        if c and c.type == 'private':
            common = cid; break

    if common: chat_id = common
    else:
        new_chat = Chat(type='private')
        db.session.add(new_chat)
        db.session.commit()
        chat_id = new_chat.id
        db.session.add_all([
            ChatParticipant(chat_id=chat_id, user_id=current_user.id),
            ChatParticipant(chat_id=chat_id, user_id=target_id),
            Contact(user_id=current_user.id, contact_id=target_id, is_explicit=False),
            Contact(user_id=target_id, contact_id=current_user.id, is_explicit=False)
        ])
        db.session.commit()
    return jsonify({'chat_id': chat_id})

# ==========================================
# ГРУППОВЫЕ МАРШРУТЫ
# ==========================================
@app.route('/api/chat/group/create', methods=['POST'])
@login_required
def create_group():
    name = request.json.get('name', 'Новая группа')
    new_chat = Chat(type='group', name=name, owner_id=current_user.id)
    db.session.add(new_chat)
    db.session.commit()
    cp = ChatParticipant(chat_id=new_chat.id, user_id=current_user.id, is_admin=True, role_tag='Владелец')
    db.session.add(cp)
    db.session.commit()
    return jsonify({'chat_id': new_chat.id})

@app.route('/api/chat/group/<int:chat_id>/info', methods=['GET'])
@login_required
def get_group_info(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    if chat.type != 'group': return abort(400)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    if not my_cp: return abort(403)

    participants = ChatParticipant.query.filter_by(chat_id=chat_id).all()
    members = []
    for p in participants:
        u = User.query.get(p.user_id)
        members.append({
            'user_id': u.id, 'name': f"{u.first_name} {u.last_name or ''}", 'username': u.username,
            'avatar': u.avatar_url, 'is_admin': p.is_admin, 'is_owner': chat.owner_id == u.id,
            'role_tag': p.role_tag, 'perm_change_profile': p.perm_change_profile,
            'perm_delete_msgs': p.perm_delete_msgs, 'perm_ban_users': p.perm_ban_users,
            'perm_change_tags': p.perm_change_tags, 'perm_assign_admins': p.perm_assign_admins
        })

    return jsonify({
        'chat_id': chat.id, 'name': chat.name, 'avatar_url': chat.avatar_url, 'description': chat.description,
        'owner_id': chat.owner_id, 'i_am_owner': chat.owner_id == current_user.id, 'i_am_admin': my_cp.is_admin,
        'perms': {
            'send_text': chat.global_send_text, 'send_photos': chat.global_send_photos,
            'send_voice': chat.global_send_voice, 'send_emoji': chat.global_send_emoji,
            'add_members': chat.global_add_members, 'change_profile': chat.global_change_profile
        },
        'my_perms': {
            'change_profile': my_cp.perm_change_profile, 'delete_msgs': my_cp.perm_delete_msgs,
            'ban_users': my_cp.perm_ban_users, 'change_tags': my_cp.perm_change_tags, 'assign_admins': my_cp.perm_assign_admins
        },
        'members': members
    })

@app.route('/api/chat/group/<int:chat_id>/update', methods=['POST'])
@login_required
def update_group_profile(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    if not (chat.owner_id == current_user.id or (my_cp and my_cp.is_admin and my_cp.perm_change_profile) or chat.global_change_profile):
        return abort(403)
    
    data = request.json
    chat.name = data.get('name', chat.name)
    chat.description = data.get('description', chat.description)
    if 'avatar_url' in data: chat.avatar_url = data['avatar_url']
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/chat/group/<int:chat_id>/update_perms', methods=['POST'])
@login_required
def update_group_perms(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    if not (chat.owner_id == current_user.id or (my_cp and my_cp.is_admin)): return abort(403)
    
    data = request.json
    chat.global_send_text = data.get('send_text', chat.global_send_text)
    chat.global_send_photos = data.get('send_photos', chat.global_send_photos)
    chat.global_send_voice = data.get('send_voice', chat.global_send_voice)
    chat.global_send_emoji = data.get('send_emoji', chat.global_send_emoji)
    chat.global_add_members = data.get('add_members', chat.global_add_members)
    chat.global_change_profile = data.get('change_profile', chat.global_change_profile)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/chat/group/<int:chat_id>/add_member', methods=['POST'])
@login_required
def group_add_member(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    if not (chat.owner_id == current_user.id or my_cp.is_admin or chat.global_add_members): return jsonify({'success': False, 'error': 'Нет прав'})
    
    username = request.json.get('username')
    u = User.query.filter_by(username=username).first()
    if not u: return jsonify({'success': False, 'error': 'Пользователь не найден'})
    if ChatParticipant.query.filter_by(chat_id=chat_id, user_id=u.id).first(): return jsonify({'success': False, 'error': 'Уже в группе'})
    
    db.session.add(ChatParticipant(chat_id=chat_id, user_id=u.id))
    db.session.commit()
    socketio.emit('block_status_changed', {'chat_id': chat_id}, room=f"user_{u.id}", namespace='/')
    return jsonify({'success': True})

@app.route('/api/chat/group/<int:chat_id>/kick', methods=['POST'])
@login_required
def group_kick_member(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    target_id = request.json.get('target_id')
    
    if chat.owner_id == target_id: return jsonify({'success': False}) 
    if not (chat.owner_id == current_user.id or (my_cp and my_cp.is_admin and my_cp.perm_ban_users)): return jsonify({'success': False})

    target_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=target_id).first()
    if target_cp: 
        db.session.delete(target_cp)
        db.session.commit()
        socketio.emit('block_status_changed', {'chat_id': chat_id}, room=f"user_{target_id}", namespace='/')
    return jsonify({'success': True})

@app.route('/api/chat/group/<int:chat_id>/leave', methods=['POST'])
@login_required
def group_leave(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    if my_cp:
        if chat.owner_id == current_user.id:
            db.session.delete(chat) 
        else:
            db.session.delete(my_cp)
        db.session.commit()
    return jsonify({'success': True})

@app.route('/api/chat/group/<int:chat_id>/admin', methods=['POST'])
@login_required
def group_admin_manage(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
    data = request.json
    target_id = data.get('user_id')

    if chat.owner_id == target_id: return jsonify({'success': False})
    
    target_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=target_id).first()
    if not target_cp: return jsonify({'success': False})

    i_am_owner = chat.owner_id == current_user.id

    if i_am_owner or (my_cp.is_admin and my_cp.perm_change_tags):
        target_cp.role_tag = data.get('role_tag')

    if i_am_owner or (my_cp.is_admin and my_cp.perm_assign_admins):
        target_cp.is_admin = data.get('is_admin', False)
        if target_cp.is_admin:
            target_cp.perm_change_profile = data.get('perm_change_profile') if (i_am_owner or my_cp.perm_change_profile) else False
            target_cp.perm_delete_msgs = data.get('perm_delete_msgs') if (i_am_owner or my_cp.perm_delete_msgs) else False
            target_cp.perm_ban_users = data.get('perm_ban_users') if (i_am_owner or my_cp.perm_ban_users) else False
            target_cp.perm_change_tags = data.get('perm_change_tags') if (i_am_owner or my_cp.perm_change_tags) else False
            target_cp.perm_assign_admins = data.get('perm_assign_admins') if i_am_owner else False
        else:
            target_cp.perm_change_profile = False; target_cp.perm_delete_msgs = False; target_cp.perm_ban_users = False
            target_cp.perm_change_tags = False; target_cp.perm_assign_admins = False

    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/chat/<int:chat_id>/messages')
@login_required
def get_messages(chat_id):
    unread_msgs = Message.query.filter(Message.chat_id == chat_id, Message.sender_id != current_user.id, Message.is_read == False).all()
    if unread_msgs:
        for msg in unread_msgs: msg.is_read = True
        db.session.commit()
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id, ChatParticipant.user_id != current_user.id).first()
        if partner_cp: socketio.emit('messages_read', {'chat_id': chat_id}, room=f"user_{partner_cp.user_id}", namespace='/')

    if can_see_deleted(): messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    else: messages = Message.query.filter_by(chat_id=chat_id, is_deleted=False).order_by(Message.timestamp.asc()).all()

    chat = Chat.query.get(chat_id)
    
    result = []
    see_edits = can_see_edits()
    
    user_cache = {}
    if chat.type == 'group':
        for p in ChatParticipant.query.filter_by(chat_id=chat_id).all():
            u = User.query.get(p.user_id)
            if u: user_cache[p.user_id] = {'name': f"{u.first_name} {u.last_name or ''}", 'tag': p.role_tag, 'is_admin': p.is_admin, 'is_owner': chat.owner_id == p.user_id}

    for m in messages:
        reply_text = ""
        if m.reply_to_id:
            rm = Message.query.get(m.reply_to_id)
            if rm:
                if rm.voice_base64: reply_text = "[Голосовое]"
                elif rm.text: reply_text = (rm.text[:25] + "...") if len(rm.text) > 25 else rm.text
                else: reply_text = "[Вложение]"

        fwd_name = ""
        if m.forwarded_from_id:
            fu = User.query.get(m.forwarded_from_id)
            if fu: fwd_name = f"{fu.first_name} {fu.last_name or ''}"

        sender_info = user_cache.get(m.sender_id, {}) if chat.type == 'group' else {}

        result.append({
            'id': m.id, 'sender_id': m.sender_id, 'text': m.text,
            'sender_name': sender_info.get('name', 'Неизвестный'),
            'sender_tag': sender_info.get('tag', ''),
            'sender_is_admin': sender_info.get('is_admin', False),
            'sender_is_owner': sender_info.get('is_owner', False),
            'image_base64': m.image_base64, 'video_base64': m.video_base64, 
            'file_base64': m.file_base64, 'file_name': m.file_name,
            'voice_base64': m.voice_base64,
            'time': m.timestamp.strftime('%H:%M'),
            'is_read': m.is_read, 'is_deleted': m.is_deleted, 'is_edited': m.is_edited,
            'original_text': m.original_text if see_edits else None,
            'reply_to_id': m.reply_to_id, 'reply_text': reply_text,
            'forwarded_from_id': m.forwarded_from_id, 'forwarded_from_name': fwd_name
        })
    return jsonify(result)

# ==========================================
# АДМИН ПАНЕЛЬ И SUDO-РОЛИ
# ==========================================
@app.route('/admin')
@login_required
def admin_panel():
    if not (has_admin_priv() or current_user.is_moderator or current_user.perm_ban_users or current_user.perm_grant_gifts or current_user.perm_grant_lightnings):
        flash("Доступ запрещен")
        return redirect(url_for('index'))
    users = User.query.order_by(User.id.desc()).all()
    return render_template_string(ADMIN_TEMPLATE, users=users, connected=connected_users, 
                                  has_admin_priv=has_admin_priv(), can_ban_users=can_ban_users(),
                                  format_last_seen=format_last_seen_str)

@app.route('/admin/grant_lightnings', methods=['POST'])
@login_required
def admin_grant_lightnings():
    if not (has_admin_priv() or current_user.perm_grant_lightnings): abort(403)
    uid = request.form.get('user_id')
    amt = int(request.form.get('amount', 0))
    u = User.query.get(uid)
    if u and amt > 0:
        u.lightnings = (u.lightnings or 0) + amt
        db.session.commit()
        flash(f"Начислено {amt} молний пользователю {u.username}", 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/deduct_lightnings', methods=['POST'])
@login_required
def admin_deduct_lightnings():
    if not (has_admin_priv() or current_user.perm_grant_lightnings): abort(403)
    uid = request.form.get('user_id')
    amt = int(request.form.get('amount', 0))
    u = User.query.get(uid)
    if u and amt > 0:
        u.lightnings = max((u.lightnings or 0) - amt, 0)
        db.session.commit()
        flash(f"Списано {amt} молний у пользователя {u.username}", 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/grant_gift', methods=['POST'])
@login_required
def admin_grant_gift():
    if not (has_admin_priv() or current_user.perm_grant_gifts): abort(403)
    uid = request.form.get('user_id')
    def_id = request.form.get('def_id')
    u = User.query.get(uid)
    gdef = GiftDefinition.query.get(def_id)
    if u and gdef:
        ug = UserGift(owner_id=u.id, gift_def_id=gdef.id)
        db.session.add(ug)
        db.session.commit()
        flash(f"Подарок {gdef.name} выдан пользователю {u.username}", 'success')
    return redirect(url_for('admin_panel'))

@app.route('/api/admin/permissions/<int:target_id>', methods=['POST'])
@login_required
def update_permissions(target_id):
    if not has_admin_priv(): return "Forbidden", 403
    target = User.query.get_or_404(target_id)
    data = request.json or {}
    
    target.is_admin = data.get('is_admin', False)
    target.is_moderator = data.get('is_moderator', False)
    target.perm_edit_history = data.get('perm_edit_history', False)
    target.perm_deleted_messages = data.get('perm_deleted_messages', False)
    target.perm_see_chatting_with = data.get('perm_see_chatting_with', False)
    target.perm_ban_users = data.get('perm_ban_users', False)
    target.perm_grant_gifts = data.get('perm_grant_gifts', False)
    target.perm_grant_lightnings = data.get('perm_grant_lightnings', False)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/admin/ban/<int:target_id>', methods=['POST'])
@login_required
def admin_ban_user_endpoint(target_id):
    if not can_ban_users(): return "Forbidden", 403
    target = User.query.get_or_404(target_id)
    if target.is_admin and not current_user.is_admin: return "Cannot ban admin", 403

    data = request.json or {}
    action = data.get('action')
    ban_type = data.get('type', action)

    if action == 'unban' or ban_type == 'unban': target.banned_until = None
    elif ban_type == 'forever' or action == 'forever':
        target.banned_until = datetime(9999, 12, 31, 23, 59, 59)
        socketio.emit('force_logout', {}, room=f"user_{target.id}", namespace='/')
    elif ban_type in ['temporary', 'temp'] or action == 'temporary':
        until_str = data.get('until')
        if until_str:
            try:
                dt_str = until_str.replace("T", " ")[:16]
                target.banned_until = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                socketio.emit('force_logout', {}, room=f"user_{target.id}", namespace='/')
            except: return "Invalid date format", 400

    db.session.commit()
    broadcast_user_status(target.id)
    return jsonify({'status': 'ok'})

@app.route('/api/admin/history_24h/<int:target_id>')
@login_required
def admin_history_24h(target_id):
    if not (has_admin_priv() or current_user.is_moderator or can_ban_users()): return "Forbidden", 403
    
    yesterday = now_msk() - timedelta(days=1)
    participants = ChatParticipant.query.filter_by(user_id=target_id).all()
    
    result = []
    for p in participants:
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == p.chat_id, ChatParticipant.user_id != target_id).first()
        if not partner_cp: continue
        
        partner = User.query.get(partner_cp.user_id)
        if not partner: continue
        
        msgs = Message.query.filter(Message.chat_id == p.chat_id, Message.timestamp >= yesterday).all()
        if msgs:
            msgs.sort(key=lambda x: x.timestamp)
            first_time = msgs[0].timestamp.strftime('%H:%M')
            last_time = msgs[-1].timestamp.strftime('%H:%M')
            
            result.append({
                'name': f"{partner.first_name} {partner.last_name or ''}",
                'username': partner.username,
                'time_range': f"{first_time} - {last_time}"
            })
    return jsonify(result)

@app.route('/admin/impersonate/<int:target_id>')
@login_required
def impersonate(target_id):
    if not has_admin_priv(): return "Access denied", 403
    if 'original_admin_id' not in session: session['original_admin_id'] = current_user.id
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
def broadcast_user_status(user_id):
    status = 'online' if user_id in connected_users else 'offline'
    u = User.query.get(user_id)
    show_ls = is_allowed_to_see(u, u.privacy_last_seen if u else 'everyone', 0)
    last_seen = format_last_seen_str(u.last_seen) if (u and show_ls) else 'недавно'
    
    chatting_with_name = None
    if user_id in active_chat_views:
        partner = User.query.get(active_chat_views[user_id])
        if partner: chatting_with_name = f"{partner.first_name} {partner.last_name or ''}"

    socketio.emit('status_update', {
        'user_id': user_id, 'status': status, 'last_seen': last_seen,
        'chatting_with_name': chatting_with_name
    }, namespace='/')

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        banned, _, _ = check_user_banned(current_user)
        if banned: return False 

        uid = current_user.id
        connected_users[uid] = request.sid
        join_room(f"user_{uid}")
        current_user.last_seen = now_msk()
        db.session.commit()
        broadcast_user_status(uid)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        uid = current_user.id
        disc_sid = request.sid

        def delayed_offline_check(check_uid, check_sid):
            socketio.sleep(7.0) 
            if connected_users.get(check_uid) == check_sid:
                del connected_users[check_uid]
                if check_uid in active_chat_views:
                    del active_chat_views[check_uid]
                u = User.query.get(check_uid)
                if u: 
                    u.last_seen = now_msk()
                    db.session.commit()
                    broadcast_user_status(check_uid)

        socketio.start_background_task(delayed_offline_check, uid, disc_sid)

@socketio.on('open_chat')
def handle_open_chat(data):
    if current_user.is_authenticated:
        active_chat_views[current_user.id] = data.get('partner_id')
        broadcast_user_status(current_user.id)

@socketio.on('close_chat')
def handle_close_chat():
    if current_user.is_authenticated and current_user.id in active_chat_views:
        del active_chat_views[current_user.id]
        broadcast_user_status(current_user.id)

@socketio.on('typing')
def handle_typing(data):
    chat_id = data.get('chat_id')
    chat = Chat.query.get(chat_id)
    if chat and chat.type == 'group':
        for p in ChatParticipant.query.filter_by(chat_id=chat_id).all():
            if p.user_id != current_user.id:
                emit('typing_status', {'chat_id': chat_id, 'is_typing': True}, room=f"user_{p.user_id}")
    else:
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id, ChatParticipant.user_id != current_user.id).first()
        if partner_cp: emit('typing_status', {'chat_id': chat_id, 'is_typing': True}, room=f"user_{partner_cp.user_id}")

@socketio.on('send_message')
def handle_message(data):
    chat_id = data.get('chat_id')
    reply_to_id = data.get('reply_to_id')
    forwarded_from_id = data.get('forwarded_from_id')
    text = data.get('text', '')
    img = data.get('image_base64')
    video = data.get('video_base64')
    file_b64 = data.get('file_base64')
    file_name = data.get('file_name')
    voice = data.get('voice_base64')
    client_temp_id = data.get('client_temp_id')

    chat = Chat.query.get(chat_id)
    if not chat: return

    if chat.type == 'private':
        partner_cp = ChatParticipant.query.filter(ChatParticipant.chat_id == chat_id, ChatParticipant.user_id != current_user.id).first()
        if partner_cp:
            block1 = PersonalBlock.query.filter_by(blocker_id=current_user.id, blocked_id=partner_cp.user_id).first()
            block2 = PersonalBlock.query.filter_by(blocker_id=partner_cp.user_id, blocked_id=current_user.id).first()
            if block1 or block2: return 
    else:
        my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
        if not my_cp: return # Выгнали из группы
        if not (my_cp.is_admin or chat.owner_id == current_user.id):
            if text and not chat.global_send_text and not voice and not img and not video and not file_b64: return
            if (img or video or file_b64) and not chat.global_send_photos: return
            if voice and not chat.global_send_voice: return

    msg = Message(
        chat_id=chat_id, sender_id=current_user.id, 
        text=text, image_base64=img, video_base64=video, file_base64=file_b64, file_name=file_name, voice_base64=voice,
        reply_to_id=reply_to_id, forwarded_from_id=forwarded_from_id, is_read=False
    )
    db.session.add(msg)
    db.session.commit()

    has_photo = bool(img)
    hook_track_message(current_user.id, reply_to_id, has_photo)

    reply_text = ""
    if reply_to_id:
        rm = Message.query.get(reply_to_id)
        if rm:
            if rm.voice_base64: reply_text = "[Голосовое]"
            elif rm.text: reply_text = (rm.text[:25] + "...") if len(rm.text) > 25 else rm.text
            else: reply_text = "[Вложение]"

    fwd_name = ""
    if forwarded_from_id:
        fu = User.query.get(forwarded_from_id)
        if fu: fwd_name = f"{fu.first_name} {fu.last_name or ''}"

    sender_name = current_user.first_name
    sender_tag = ""
    sender_is_admin = False
    sender_is_owner = False

    if chat.type == 'group':
        my_cp = ChatParticipant.query.filter_by(chat_id=chat_id, user_id=current_user.id).first()
        if my_cp:
            sender_tag = my_cp.role_tag
            sender_is_admin = my_cp.is_admin
            sender_is_owner = chat.owner_id == current_user.id

    msg_data = {
        'id': msg.id, 'chat_id': chat_id, 'sender_id': current_user.id,
        'client_temp_id': client_temp_id, 
        'text': msg.text, 'image_base64': msg.image_base64, 'video_base64': msg.video_base64,
        'file_base64': msg.file_base64, 'file_name': msg.file_name, 'voice_base64': msg.voice_base64,
        'time': msg.timestamp.strftime('%H:%M'), 'is_read': False,
        'is_deleted': False, 'is_edited': False, 'original_text': None,
        'reply_to_id': reply_to_id, 'reply_text': reply_text,
        'forwarded_from_id': forwarded_from_id, 'forwarded_from_name': fwd_name,
        'sender_name': sender_name, 'sender_tag': sender_tag, 
        'sender_is_admin': sender_is_admin, 'sender_is_owner': sender_is_owner
    }
    for p in ChatParticipant.query.filter_by(chat_id=chat_id).all():
        emit('new_message', msg_data, room=f"user_{p.user_id}")

@socketio.on('edit_message')
def handle_edit_message(data):
    msg_id = data.get('message_id')
    new_text = data.get('text', '')
    msg = Message.query.get(msg_id)
    if msg and (msg.sender_id == current_user.id or has_admin_priv()) and not msg.is_deleted:
        if not msg.is_edited: msg.original_text = msg.text; msg.is_edited = True
        msg.text = new_text; db.session.commit()
        for p in ChatParticipant.query.filter_by(chat_id=msg.chat_id).all():
            emit('message_updated', {'chat_id': msg.chat_id}, room=f"user_{p.user_id}")

@socketio.on('delete_message')
def handle_delete_message(data):
    msg_id = data.get('message_id')
    msg = Message.query.get(msg_id)
    if not msg: return

    can_delete = False
    if msg.sender_id == current_user.id or has_admin_priv(): 
        can_delete = True
    else:
        chat = Chat.query.get(msg.chat_id)
        if chat and chat.type == 'group':
            my_cp = ChatParticipant.query.filter_by(chat_id=chat.id, user_id=current_user.id).first()
            if my_cp and (chat.owner_id == current_user.id or (my_cp.is_admin and my_cp.perm_delete_msgs)):
                can_delete = True

    if can_delete:
        msg.is_deleted = True; db.session.commit()
        for p in ChatParticipant.query.filter_by(chat_id=msg.chat_id).all():
            emit('message_updated', {'chat_id': msg.chat_id}, room=f"user_{p.user_id}")

# ==========================================
# ИНИЦИАЛИЗАЦИЯ И ЗАПУСК
# ==========================================
def init_db():
    with app.app_context():
        db.create_all()
        try:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_edited BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS original_text TEXT;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS forwarded_from_id INTEGER;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS voice_base64 TEXT;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS video_base64 TEXT;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_base64 TEXT;"))
            db.session.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name TEXT;"))
            
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_moderator BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_edit_history BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_deleted_messages BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_see_chatting_with BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_ban_users BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until TIMESTAMP;"))
            
            db.session.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS custom_name VARCHAR(50);"))
            db.session.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS is_explicit BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_phone VARCHAR(20) DEFAULT 'everyone';"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_bday VARCHAR(20) DEFAULT 'everyone';"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_last_seen VARCHAR(20) DEFAULT 'everyone';"))

            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS lightnings INTEGER DEFAULT 0;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS q3_claimed BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_grant_gifts BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_grant_lightnings BOOLEAN DEFAULT FALSE;"))

            # Апгрейд для Групповых чатов
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS name VARCHAR(100);"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS avatar_url TEXT;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS description TEXT;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS owner_id INTEGER;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS global_send_text BOOLEAN DEFAULT TRUE;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS global_send_photos BOOLEAN DEFAULT TRUE;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS global_send_voice BOOLEAN DEFAULT TRUE;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS global_send_emoji BOOLEAN DEFAULT TRUE;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS global_add_members BOOLEAN DEFAULT TRUE;"))
            db.session.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS global_change_profile BOOLEAN DEFAULT FALSE;"))

            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS role_tag VARCHAR(50);"))
            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS perm_change_profile BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS perm_delete_msgs BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS perm_ban_users BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS perm_change_tags BOOLEAN DEFAULT FALSE;"))
            db.session.execute(text("ALTER TABLE chat_participants ADD COLUMN IF NOT EXISTS perm_assign_admins BOOLEAN DEFAULT FALSE;"))

            db.session.execute(text("ALTER TABLE users ALTER COLUMN last_name DROP NOT NULL;"))
            db.session.commit()
            
            if not GiftDefinition.query.first():
                db.session.add_all([
                    GiftDefinition(id=1, name="Песочный замок", image_filename="podarok1.png", price=250),
                    GiftDefinition(id=2, name="Пляжный зонт", image_filename="podarok2.png", price=230),
                    GiftDefinition(id=3, name="Шезлонг", image_filename="podarok3.png", price=200),
                    GiftDefinition(id=4, name="Спасательный круг", image_filename="podarok4.png", price=300)
                ])
                db.session.commit()
                print("Базовый магазин подарков (4 шт) создан.")

        except Exception as e:
            db.session.rollback()
            print(f"Ошибка обновления структуры БД: {e}")

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password='admin', first_name='Admin', last_name='', is_admin=True, last_seen=now_msk())
            db.session.add(admin)
            db.session.commit()

init_db()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
