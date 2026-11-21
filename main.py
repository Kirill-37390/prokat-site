from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal
from datetime import date
from pathlib import Path
from contextlib import contextmanager
import sqlite3, hashlib, secrets, os, hmac

BASE_DIR = Path(__file__).resolve().parent
DB = os.getenv('PROKAT_DB', str(BASE_DIR / 'equipment.db'))
app = FastAPI(title='Прокат оборудования')
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')

@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA busy_timeout = 15000')
    try:
        yield con
    finally:
        con.close()

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return 'pbkdf2$310000$%s$%s' % (salt.hex(), digest.hex())

def verify_password(password: str, stored: str) -> tuple[bool, Optional[str]]:
    if stored.startswith('pbkdf2$'):
        try:
            _, rounds, salt_hex, digest_hex = stored.split('$')
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), int(rounds))
            return hmac.compare_digest(digest.hex(), digest_hex), None
        except (ValueError, TypeError):
            return False, None
    # Поддержка старых учебных БД с прежним SHA-256-хешем и автоматическая миграция при входе.
    legacy = hashlib.sha256(('prokat_salt_2026' + password).encode()).hexdigest()
    return hmac.compare_digest(legacy, stored), hash_password(password) if hmac.compare_digest(legacy, stored) else None

def init_db():
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('client','manager','admin'))
        );
        CREATE TABLE IF NOT EXISTS equipment (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
          description TEXT, price REAL NOT NULL CHECK(price >= 0), quantity INTEGER NOT NULL CHECK(quantity >= 0),
          status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','repair','written_off')), photo TEXT
        );
        CREATE TABLE IF NOT EXISTS bookings (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
          equipment_id INTEGER NOT NULL REFERENCES equipment(id), start_date TEXT NOT NULL, end_date TEXT NOT NULL,
          total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'booked', condition_out TEXT, condition_in TEXT,
          fine REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(end_date >= start_date)
        );
        CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id));
        CREATE INDEX IF NOT EXISTS idx_bookings_equipment_dates ON bookings(equipment_id, start_date, end_date, status);
        CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
        ''')
        if con.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            con.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', ('Администратор', 'admin@prokat.local', hash_password('admin123'), 'admin'))
            con.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', ('Менеджер', 'manager@prokat.local', hash_password('manager123'), 'manager'))
            con.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', ('Тестовый клиент', 'client@prokat.local', hash_password('client123'), 'client'))
        if con.execute('SELECT COUNT(*) FROM equipment').fetchone()[0] == 0:
            items = [('Палатка туристическая', 'Туризм', 'Двухместная палатка для походов', 700, 5), ('Велосипед горный', 'Спорт', 'Горный велосипед с 21 скоростью', 900, 4), ('Перфоратор', 'Строительство', 'Электрический перфоратор', 500, 3), ('Спальный мешок', 'Туризм', 'Спальный мешок для походов', 250, 8), ('Лыжи', 'Спорт', 'Комплект горных лыж', 800, 6)]
            con.executemany('INSERT INTO equipment(name,category,description,price,quantity) VALUES(?,?,?,?,?)', items)
        con.commit()

@app.on_event('startup')
def startup(): init_db()

def current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Сначала войдите в систему')
    with db() as con:
        row = con.execute('SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id WHERE sessions.token=?', (authorization[7:],)).fetchone()
    if not row: raise HTTPException(401, 'Сессия недействительна')
    return dict(row)

def require_roles(*roles):
    def check(user=Depends(current_user)):
        if user['role'] not in roles: raise HTTPException(403, 'Недостаточно прав')
        return user
    return check

class Register(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=6, max_length=128)
class Login(BaseModel):
    email: str
    password: str
class Booking(BaseModel):
    equipment_id: int = Field(gt=0)
    start_date: date
    end_date: date
class EquipmentData(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=80)
    description: str = Field(default='', max_length=1000)
    price: float = Field(ge=0, le=10000000)
    quantity: int = Field(ge=0, le=100000)
    status: Literal['available','repair','written_off'] = 'available'
class ReturnData(BaseModel):
    condition_in: str = Field(default='Исправно', min_length=1, max_length=300)
    damage_fine: float = Field(default=0, ge=0, le=10000000)
class RoleData(BaseModel):
    # Новых администраторов назначают вручную в БД/конфигурации, не из обычной панели.
    role: Literal['client','manager']

@app.get('/')
def home(): return FileResponse(str(BASE_DIR / 'static' / 'index.html'))

@app.post('/api/register')
def register(data: Register):
    email = data.email.strip().lower()
    if '@' not in email: raise HTTPException(400, 'Укажите корректную почту')
    with db() as con:
        try:
            cur = con.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', (data.name.strip(), email, hash_password(data.password), 'client'))
            con.commit(); return {'message':'Регистрация выполнена','user_id':cur.lastrowid}
        except sqlite3.IntegrityError: raise HTTPException(400, 'Пользователь с такой почтой уже существует')

@app.post('/api/login')
def login(data: Login):
    with db() as con:
        user = con.execute('SELECT * FROM users WHERE email=?', (data.email.strip().lower(),)).fetchone()
        if not user: raise HTTPException(401, 'Неверная почта или пароль')
        valid, upgraded = verify_password(data.password, user['password'])
        if not valid: raise HTTPException(401, 'Неверная почта или пароль')
        if upgraded: con.execute('UPDATE users SET password=? WHERE id=?', (upgraded, user['id']))
        token = secrets.token_urlsafe(32)
        con.execute('INSERT INTO sessions(token,user_id) VALUES(?,?)', (token, user['id']))
        con.commit()
        return {'token':token,'name':user['name'],'role':user['role']}

@app.post('/api/logout')
def logout(authorization: Optional[str]=Header(None)):
    if authorization and authorization.startswith('Bearer '):
        with db() as con: con.execute('DELETE FROM sessions WHERE token=?', (authorization[7:],)); con.commit()
    return {'message':'Вы вышли из системы'}

@app.get('/api/me')
def me(user=Depends(current_user)): return {k:user[k] for k in ('id','name','email','role')}

@app.get('/api/equipment')
def equipment(search: str='', category: str=''):
    with db() as con:
        sql = "SELECT * FROM equipment WHERE status='available'"; args=[]
        if search: sql += ' AND name LIKE ?'; args.append('%'+search[:100]+'%')
        if category: sql += ' AND category=?'; args.append(category[:80])
        return [dict(r) for r in con.execute(sql+' ORDER BY name', args).fetchall()]

@app.post('/api/bookings')
def create_booking(data: Booking, user=Depends(current_user)):
    if data.end_date < data.start_date: raise HTTPException(400, 'Дата возврата не может быть раньше даты начала')
    if data.start_date < date.today(): raise HTTPException(400, 'Нельзя оформить бронирование задним числом')
    days=(data.end_date-data.start_date).days+1
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        item=con.execute("SELECT * FROM equipment WHERE id=? AND status='available'",(data.equipment_id,)).fetchone()
        if not item: raise HTTPException(404,'Оборудование не найдено или недоступно')
        overlap=con.execute("SELECT COUNT(*) FROM bookings WHERE equipment_id=? AND status IN ('booked','issued','overdue') AND NOT(end_date < ? OR start_date > ?)",(data.equipment_id,data.start_date.isoformat(),data.end_date.isoformat())).fetchone()[0]
        if overlap >= item['quantity']: raise HTTPException(400,'На выбранные даты всё оборудование занято')
        total=days*item['price']
        cur=con.execute('INSERT INTO bookings(user_id,equipment_id,start_date,end_date,total) VALUES(?,?,?,?,?)',(user['id'],data.equipment_id,data.start_date.isoformat(),data.end_date.isoformat(),total))
        con.commit(); return {'message':'Бронирование создано','booking_id':cur.lastrowid,'days':days,'total':total}

@app.get('/api/my-bookings')
def my_bookings(user=Depends(current_user)):
    with db() as con:
        con.execute("UPDATE bookings SET status='overdue' WHERE status='issued' AND end_date < ?",(date.today().isoformat(),)); con.commit()
        return [dict(r) for r in con.execute('SELECT b.*,e.name AS equipment_name FROM bookings b JOIN equipment e ON e.id=b.equipment_id WHERE b.user_id=? ORDER BY b.id DESC',(user['id'],)).fetchall()]

@app.get('/api/manage/bookings')
def all_bookings(user=Depends(require_roles('manager','admin'))):
    with db() as con:
        con.execute("UPDATE bookings SET status='overdue' WHERE status='issued' AND end_date < ?",(date.today().isoformat(),)); con.commit()
        return [dict(r) for r in con.execute('SELECT b.*,e.name AS equipment_name,u.name AS client_name FROM bookings b JOIN equipment e ON e.id=b.equipment_id JOIN users u ON u.id=b.user_id ORDER BY b.id DESC').fetchall()]

@app.post('/api/manage/bookings/{booking_id}/issue')
def issue_booking(booking_id:int,user=Depends(require_roles('manager','admin'))):
    with db() as con:
        cur=con.execute("UPDATE bookings SET status='issued',condition_out='Исправно' WHERE id=? AND status='booked'",(booking_id,)); con.commit()
        if not cur.rowcount: raise HTTPException(400,'Нельзя выдать это бронирование')
    return {'message':'Оборудование выдано'}

@app.post('/api/manage/bookings/{booking_id}/return')
def return_booking(booking_id:int,data:ReturnData,user=Depends(require_roles('manager','admin'))):
    with db() as con:
        row=con.execute("SELECT * FROM bookings WHERE id=? AND status IN ('issued','overdue')",(booking_id,)).fetchone()
        if not row: raise HTTPException(404,'Активная аренда не найдена')
        late=max(0,(date.today()-date.fromisoformat(row['end_date'])).days)
        daily=con.execute('SELECT price FROM equipment WHERE id=?',(row['equipment_id'],)).fetchone()['price']
        fine=late*daily+data.damage_fine
        con.execute("UPDATE bookings SET status='returned',condition_in=?,fine=? WHERE id=?",(data.condition_in,fine,booking_id)); con.commit()
    return {'message':'Возврат оформлен','fine':fine,'late_days':late}

@app.get('/api/admin/equipment')
def admin_equipment(user=Depends(require_roles('admin'))):
    with db() as con: return [dict(r) for r in con.execute('SELECT * FROM equipment ORDER BY id').fetchall()]
@app.post('/api/admin/equipment')
def add_equipment(data:EquipmentData,user=Depends(require_roles('admin'))):
    with db() as con:
        cur=con.execute('INSERT INTO equipment(name,category,description,price,quantity,status) VALUES(?,?,?,?,?,?)',(data.name.strip(),data.category.strip(),data.description.strip(),data.price,data.quantity,data.status)); con.commit()
        return {'id':cur.lastrowid,'message':'Оборудование добавлено'}
@app.put('/api/admin/equipment/{item_id}')
def update_equipment(item_id:int,data:EquipmentData,user=Depends(require_roles('admin'))):
    with db() as con:
        cur=con.execute('UPDATE equipment SET name=?,category=?,description=?,price=?,quantity=?,status=? WHERE id=?',(data.name.strip(),data.category.strip(),data.description.strip(),data.price,data.quantity,data.status,item_id)); con.commit()
        if not cur.rowcount: raise HTTPException(404,'Оборудование не найдено')
    return {'message':'Данные обновлены'}
@app.get('/api/admin/users')
def users(user=Depends(require_roles('admin'))):
    with db() as con: return [dict(r) for r in con.execute('SELECT id,name,email,role FROM users ORDER BY id').fetchall()]
@app.get('/api/stats')
def stats(user=Depends(require_roles('manager','admin'))):
    with db() as con:
        total=con.execute('SELECT COUNT(*) FROM bookings').fetchone()[0]
        active=con.execute("SELECT COUNT(*) FROM bookings WHERE status IN ('booked','issued','overdue')").fetchone()[0]
        revenue=con.execute("SELECT COALESCE(SUM(total+fine),0) FROM bookings WHERE status='returned'").fetchone()[0]
        categories=con.execute('SELECT category,COUNT(*) AS count FROM equipment GROUP BY category').fetchall()
        return {'bookings':total,'active':active,'revenue':revenue,'categories':[dict(r) for r in categories]}
@app.post('/api/admin/equipment/{item_id}/repair')
def repair(item_id:int,user=Depends(require_roles('admin'))):
    with db() as con:
        cur=con.execute("UPDATE equipment SET status='repair' WHERE id=?",(item_id,)); con.commit()
        if not cur.rowcount: raise HTTPException(404,'Не найдено')
    return {'message':'Оборудование отправлено в ремонт'}
@app.post('/api/admin/equipment/{item_id}/writeoff')
def writeoff(item_id:int,user=Depends(require_roles('admin'))):
    with db() as con:
        cur=con.execute("UPDATE equipment SET status='written_off',quantity=0 WHERE id=?",(item_id,)); con.commit()
        if not cur.rowcount: raise HTTPException(404,'Не найдено')
    return {'message':'Оборудование списано'}

@app.put('/api/admin/users/{user_id}/role')
def change_user_role(user_id:int,data:RoleData,user=Depends(require_roles('admin'))):
    # Защита от случайной потери административного доступа:
    # нельзя менять собственную роль и нельзя понижать/переназначать существующего администратора.
    if user_id == user['id']:
        raise HTTPException(400,'Нельзя изменять собственную роль')
    with db() as con:
        target=con.execute('SELECT id,role FROM users WHERE id=?',(user_id,)).fetchone()
        if not target: raise HTTPException(404,'Пользователь не найден')
        if target['role']=='admin':
            raise HTTPException(403,'Роль действующего администратора защищена от изменения')
        cur=con.execute('UPDATE users SET role=? WHERE id=?',(data.role,user_id)); con.commit()
    return {'message':'Роль пользователя изменена'}
