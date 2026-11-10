import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from fpdf import FPDF
from apscheduler.schedulers.background import BackgroundScheduler
import io

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')

db_url = os.environ.get('DATABASE_URL', 'postgresql://nailuser:nailpassword@db:5432/salonschema')
if db_url and db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELOS ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(120))
    is_blocked = db.Column(db.Boolean, default=False)
    block_reason = db.Column(db.String(255), default="")
    appointments = db.relationship('Appointment', backref='client', cascade="all, delete-orphan")

class Collaborator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    payout_cycle = db.Column(db.String(20), default="15_30")
    commission_rate = db.Column(db.Float, default=40.0)
    active = db.Column(db.Boolean, default=True)

class CollaboratorBlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey('collaborator.id'), nullable=False)
    block_date = db.Column(db.String(10), nullable=False)
    reason = db.Column(db.String(200))
    collaborator = db.relationship('Collaborator', backref='blocks')

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Float, nullable=False)
    duration_min = db.Column(db.Integer, default=60)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    time = db.Column(db.String(5), nullable=False)
    status = db.Column(db.String(30), default="Pendente")
    payment_method = db.Column(db.String(50), default="Maquininha")
    reminder_sent = db.Column(db.Boolean, default=False)
    items = db.relationship('AppointmentItem', backref='appointment', cascade="all, delete-orphan")

class AppointmentItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    collaborator_id = db.Column(db.Integer, db.ForeignKey('collaborator.id'), nullable=False)
    price = db.Column(db.Float, nullable=False)
    service = db.relationship('Service')
    collaborator = db.relationship('Collaborator')

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    min_quantity = db.Column(db.Integer, default=5)
    cost_price = db.Column(db.Float, default=0.0)
    sell_price = db.Column(db.Float, default=0.0)

class SalonConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    salon_name = db.Column(db.String(120), default="Nail Studio")

class CollaboratorPayout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey('collaborator.id'), nullable=False)
    start_date = db.Column(db.String(10), nullable=False)
    end_date = db.Column(db.String(10), nullable=False)
    is_paid = db.Column(db.Boolean, default=False)
    pix_receipt = db.Column(db.String(255), default="")
    paid_at = db.Column(db.String(30), default="")

class WhatsAppConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    api_url = db.Column(db.String(255), default="http://localhost:8080/message/sendText/instancia")
    api_key = db.Column(db.String(255), default="")
    message_template = db.Column(db.Text, default="Olá {cliente}! Lembrando do seu horário amanhã ({data}) às {hora} no nosso Salão. Serviços: {servicos}. Te esperamos!")
    active = db.Column(db.Boolean, default=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_salon_name():
    cfg = SalonConfig.query.first()
    return cfg.salon_name if cfg else "Nail Studio"

@app.context_processor
def inject_salon():
    return dict(salon_name=get_salon_name())

def check_whatsapp_reminders():
    with app.app_context():
        config = WhatsAppConfig.query.first()
        if not config or not config.active:
            return
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        appts = Appointment.query.filter_by(date=tomorrow, reminder_sent=False).all()
        for a in appts:
            a.reminder_sent = True
        db.session.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_whatsapp_reminders, trigger="interval", minutes=30)
scheduler.start()

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", password_hash=generate_password_hash("admin123"))
        db.session.add(admin)
    if not WhatsAppConfig.query.first():
        db.session.add(WhatsAppConfig())
    if not SalonConfig.query.first():
        db.session.add(SalonConfig(salon_name="Nail Studio"))
    db.session.commit()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user)
            return redirect(url_for('agenda'))
        flash('Usuário ou senha inválidos.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('admin_authenticated', None)
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return redirect(url_for('agenda'))

# --- CLIENTES ---
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    search = request.args.get('q', '')
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            c = Client(name=request.form['name'], phone=request.form['phone'], email=request.form.get('email', ''))
            db.session.add(c)
            db.session.commit()
            flash('Cliente cadastrada com sucesso!', 'success')
        elif action == 'edit':
            c = Client.query.get(request.form['id'])
            c.name = request.form['name']
            c.phone = request.form['phone']
            c.email = request.form.get('email', '')
            db.session.commit()
            flash('Dados da cliente atualizados!', 'success')
        elif action == 'block':
            c = Client.query.get(request.form['id'])
            c.is_blocked = not c.is_blocked
            c.block_reason = request.form.get('reason', 'Cliente bloqueada.') if c.is_blocked else ""
            db.session.commit()
            flash('Status de bloqueio atualizado!', 'warning')
        elif action == 'delete':
            c = Client.query.get(request.form['id'])
            db.session.delete(c)
            db.session.commit()
            flash('Cliente excluída!', 'success')
        return redirect(url_for('clientes'))

    query = Client.query
    if search:
        query = query.filter(Client.name.ilike(f'%{search}%') | Client.phone.ilike(f'%{search}%'))
    clients = query.limit(50).all()
    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    return render_template('clientes.html', clients=clients, search=search, low_stock=low_stock)

# --- AGENDA ---
@app.route('/agenda', methods=['GET', 'POST'])
@login_required
def agenda():
    search = request.args.get('q', '')
    if request.method == 'POST':
        client_id = request.form['client_id']
        client = Client.query.get(client_id)
        if client.is_blocked:
            flash(f'BLOQUEADA: {client.name} não pode ser agendada. Motivo: {client.block_reason}', 'error')
            return redirect(url_for('agenda'))

        date = request.form['date']
        time = request.form['time']
        service_ids = request.form.getlist('service_id[]')
        collab_ids = request.form.getlist('collaborator_id[]')
        prices = request.form.getlist('price[]')
        force = request.form.get('force', 'false') == 'true'

        for cid in collab_ids:
            blocked = CollaboratorBlock.query.filter_by(collaborator_id=cid, block_date=date).first()
            if blocked:
                col = Collaborator.query.get(cid)
                flash(f'Colaborador(a) {col.name} está bloqueado(a) no dia {date} ({blocked.reason}).', 'error')
                return redirect(url_for('agenda'))

            if not force:
                existing_items = AppointmentItem.query.join(Appointment).filter(
                    Appointment.date == date,
                    Appointment.time == time,
                    AppointmentItem.collaborator_id == cid
                ).all()
                if existing_items:
                    col = Collaborator.query.get(cid)
                    return render_template('agenda_conflito.html', 
                                         client_id=client_id, date=date, time=time,
                                         service_ids=service_ids, collab_ids=collab_ids, prices=prices,
                                         collab_name=col.name, time_str=time, date_str=date,
                                         clients=Client.query.filter_by(is_blocked=False).all(),
                                         services=Service.query.all(), collaborators=Collaborator.query.filter_by(active=True).all(),
                                         low_stock=Product.query.filter(Product.quantity <= Product.min_quantity).all())

        appt = Appointment(client_id=client_id, date=date, time=time)
        db.session.add(appt)
        db.session.flush()

        for s_id, c_id, p in zip(service_ids, collab_ids, prices):
            item = AppointmentItem(appointment_id=appt.id, service_id=s_id, collaborator_id=c_id, price=float(p))
            db.session.add(item)

        db.session.commit()
        flash('Agendamento realizado com sucesso!', 'success')
        return redirect(url_for('agenda'))

    clients = Client.query.filter_by(is_blocked=False).limit(20).all()
    services = Service.query.all()
    collaborators = Collaborator.query.filter_by(active=True).all()
    
    query = Appointment.query.join(Client)
    if search:
        query = query.filter(Client.name.ilike(f'%{search}%') | Client.phone.ilike(f'%{search}%') | Appointment.date.ilike(f'%{search}%'))
    appointments = query.order_by(Appointment.date.desc(), Appointment.time.asc()).all()
    
    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    return render_template('agenda.html', clients=clients, services=services, collaborators=collaborators, appointments=appointments, low_stock=low_stock, search=search)

@app.route('/api/clients_search')
@login_required
def api_clients_search():
    term = request.args.get('q', '')
    clients = Client.query.filter(Client.is_blocked == False, Client.name.ilike(f'%{term}%')).limit(15).all()
    return jsonify([{'id': c.id, 'name': f"{c.name} ({c.phone})"} for c in clients])

@app.route('/api/appointments')
@login_required
def api_appointments():
    appts = Appointment.query.all()
    events = []
    for a in appts:
        desc = ", ".join([f"{i.service.name} ({i.collaborator.name})" for i in a.items])
        events.append({
            "id": a.id,
            "title": f"{a.client.name} - {desc}",
            "start": f"{a.date}T{a.time}:00",
            "color": "#ec4899" if a.status != "Pago" else "#10b981"
        })
    return jsonify(events)

@app.route('/agenda/editar/<int:appt_id>', methods=['POST'])
@login_required
def editar_agendamento(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    appt.date = request.form['date']
    appt.time = request.form['time']
    db.session.commit()
    flash('Data e horário atualizados!', 'success')
    return redirect(url_for('agenda'))

@app.route('/agenda/excluir/<int:appt_id>', methods=['POST'])
@login_required
def excluir_agendamento(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    db.session.delete(appt)
    db.session.commit()
    flash('Agendamento excluído!', 'success')
    return redirect(url_for('agenda'))

@app.route('/agenda/editar_item/<int:item_id>', methods=['POST'])
@login_required
def editar_item_agenda(item_id):
    item = AppointmentItem.query.get_or_404(item_id)
    item.collaborator_id = request.form['collaborator_id']
    item.price = float(request.form['price'])
    db.session.commit()
    flash('Serviço/Colaborador atualizado!', 'success')
    return redirect(url_for('agenda'))

@app.route('/agenda/remover_item/<int:item_id>', methods=['POST'])
@login_required
def remover_item_agenda(item_id):
    item = AppointmentItem.query.get_or_404(item_id)
    appt_id = item.appointment_id
    db.session.delete(item)
    db.session.commit()
    if not AppointmentItem.query.filter_by(appointment_id=appt_id).first():
        Appointment.query.filter_by(id=appt_id).delete()
        db.session.commit()
    flash('Serviço removido!', 'success')
    return redirect(url_for('agenda'))

# --- PAGAMENTOS ---
@app.route('/pagamentos', methods=['GET', 'POST'])
@login_required
def pagamentos():
    if request.method == 'POST':
        appt = Appointment.query.get(request.form['appointment_id'])
        appt.status = request.form['status']
        appt.payment_method = request.form.get('payment_method', 'Maquininha')
        db.session.commit()
        flash(f'Pagamento atualizado para {appt.status}!', 'success')
        return redirect(url_for('pagamentos'))

    appointments = Appointment.query.order_by(Appointment.date.desc()).all()
    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    return render_template('pagamentos.html', appointments=appointments, low_stock=low_stock)

# --- FATURAMENTO & PDF ---
@app.route('/faturamento', methods=['GET', 'POST'])
@login_required
def faturamento():
    start_date = request.args.get('start_date', datetime.now().replace(day=1).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))

    if request.method == 'POST':
        collab_id = request.form['collaborator_id']
        s_date = request.form['start_date']
        e_date = request.form['end_date']
        receipt = request.form.get('pix_receipt', '')
        
        payout = CollaboratorPayout.query.filter_by(collaborator_id=collab_id, start_date=s_date, end_date=e_date).first()
        if not payout:
            payout = CollaboratorPayout(collaborator_id=collab_id, start_date=s_date, end_date=e_date)
            db.session.add(payout)
        
        payout.is_paid = True
        payout.pix_receipt = receipt
        payout.paid_at = datetime.now().strftime('%d/%m/%Y %H:%M')
        db.session.commit()
        flash('Comprovante de pagamento registrado com sucesso!', 'success')
        return redirect(url_for('faturamento', start_date=s_date, end_date=e_date))

    paid_appts = Appointment.query.filter(
        Appointment.status == 'Pago',
        Appointment.date >= start_date,
        Appointment.date <= end_date
    ).all()

    total_bruto = 0.0
    total_salao = 0.0
    total_colab = 0.0
    colab_stats = {}

    for a in paid_appts:
        for item in a.items:
            total_bruto += item.price
            rate = item.collaborator.commission_rate / 100.0
            colab_part = item.price * rate
            salao_part = item.price - colab_part
            total_salao += salao_part
            total_colab += colab_part

            cid = item.collaborator.id
            if cid not in colab_stats:
                payout_record = CollaboratorPayout.query.filter_by(collaborator_id=cid, start_date=start_date, end_date=end_date).first()
                colab_stats[cid] = {
                    'id': cid,
                    'name': item.collaborator.name,
                    'rate': item.collaborator.commission_rate,
                    'cycle': 'Dia 15 e 30' if item.collaborator.payout_cycle == '15_30' else 'Dia 05 e 20',
                    'total_bruto': 0.0,
                    'a_receber': 0.0,
                    'atendimentos': [],
                    'payout': payout_record
                }
            colab_stats[cid]['total_bruto'] += item.price
            colab_stats[cid]['a_receber'] += colab_part
            colab_stats[cid]['atendimentos'].append({
                'data': a.date, 'hora': a.time, 'cliente': a.client.name,
                'servico': item.service.name, 'valor': item.price, 'comissao': colab_part
            })

    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    collaborators = Collaborator.query.filter_by(active=True).all()
    return render_template('faturamento.html', start_date=start_date, end_date=end_date,
                           total_bruto=total_bruto, total_salao=total_salao,
                           total_colab=total_colab, colab_stats=colab_stats, low_stock=low_stock, collaborators=collaborators)

@app.route('/faturamento/pdf')
@login_required
def faturamento_pdf():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    collab_id = request.args.get('collab_id')

    paid_appts = Appointment.query.filter(
        Appointment.status == 'Pago',
        Appointment.date >= start_date,
        Appointment.date <= end_date
    ).all()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"Relatorio de Faturamento - {get_salon_name()}", ln=True, align="C")
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 6, f"Periodo: {start_date} a {end_date}", ln=True, align="C")
    pdf.ln(5)

    if collab_id:
        collab = Collaborator.query.get_or_404(collab_id)
        pdf.set_font("Arial", "B", 13)
        pdf.cell(0, 8, f"Extrato da Colaboradora: {collab.name} ({collab.commission_rate}% comissao)", ln=True)
        pdf.ln(3)
        
        c_total = 0.0
        pdf.set_font("Arial", "", 10)
        for a in paid_appts:
            for item in a.items:
                if item.collaborator_id == int(collab_id):
                    comissao = item.price * (collab.commission_rate / 100.0)
                    c_total += comissao
                    pdf.cell(0, 6, f"  - {a.date} {a.time} | Cliente: {a.client.name} | Servico: {item.service.name} | R$ {item.price:.2f} (Comissao: R$ {comissao:.2f})", ln=True)
        pdf.ln(3)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, f"Total a Receber: R$ {c_total:.2f}", ln=True)
    else:
        total_bruto = sum(item.price for a in paid_appts for item in a.items)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, f"Faturamento Bruto Total: R$ {total_bruto:.2f}", ln=True)
        pdf.ln(5)

    output = io.BytesIO()
    pdf.output(output)
    output.seek(0)
    filename = f"faturamento_{collab_id if collab_id else 'geral'}_{start_date}_{end_date}.pdf"
    return send_file(output, download_name=filename, as_attachment=True)

# --- ESTOQUE ---
@app.route('/estoque', methods=['GET', 'POST'])
@login_required
def estoque():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            p = Product(
                name=request.form['name'],
                quantity=int(request.form['quantity']),
                min_quantity=int(request.form['min_quantity']),
                cost_price=float(request.form['cost_price']),
                sell_price=float(request.form['sell_price'])
            )
            db.session.add(p)
        elif action == 'entrada':
            p = Product.query.get(request.form['id'])
            p.quantity += int(request.form['qtd'])
        elif action == 'saida':
            p = Product.query.get(request.form['id'])
            p.quantity = max(0, p.quantity - int(request.form['qtd']))
        elif action == 'update_min':
            p = Product.query.get(request.form['id'])
            p.min_quantity = int(request.form['min_quantity'])
            p.cost_price = float(request.form['cost_price'])
            p.sell_price = float(request.form['sell_price'])
        db.session.commit()
        flash('Estoque atualizado!', 'success')
        return redirect(url_for('estoque'))

    products = Product.query.all()
    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    return render_template('estoque.html', products=products, low_stock=low_stock)

# --- ADMIN ---
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if not session.get('admin_authenticated'):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_salon':
            cfg = SalonConfig.query.first()
            if cfg:
                cfg.salon_name = request.form['salon_name']
                db.session.commit()
                flash('Nome do salão atualizado!', 'success')
        elif action == 'add_collab':
            db.session.add(Collaborator(
                name=request.form['name'],
                phone=request.form['phone'],
                payout_cycle=request.form['payout_cycle'],
                commission_rate=float(request.form.get('commission_rate', 40.0))
            ))
        elif action == 'edit_collab_rate':
            col = Collaborator.query.get(request.form['id'])
            col.commission_rate = float(request.form['commission_rate'])
            db.session.commit()
            flash('Comissão atualizada com sucesso!', 'success')
        elif action == 'del_collab':
            Collaborator.query.filter_by(id=request.form['id']).delete()
        elif action == 'add_service':
            db.session.add(Service(name=request.form['name'], price=float(request.form['price']), duration_min=int(request.form['duration_min'])))
        elif action == 'del_service':
            Service.query.filter_by(id=request.form['id']).delete()
        elif action == 'add_user':
            new_user = request.form['new_username']
            new_pass = request.form['new_password']
            if not User.query.filter_by(username=new_user).first():
                db.session.add(User(username=new_user, password_hash=generate_password_hash(new_pass)))
                flash('Usuário criado com sucesso!', 'success')
            else:
                flash('Usuário já existe.', 'error')
        elif action == 'change_password':
            u = User.query.get(request.form['user_id'])
            if u:
                u.password_hash = generate_password_hash(request.form['updated_password'])
                flash(f'Senha de {u.username} alterada!', 'success')
        elif action == 'delete_user':
            u = User.query.get(request.form['user_id'])
            if u and u.username != 'admin':
                db.session.delete(u)
                flash('Usuário excluído!', 'success')
        db.session.commit()
        return redirect(url_for('admin'))

    collaborators = Collaborator.query.all()
    services = Service.query.all()
    users = User.query.all()
    salon_config = SalonConfig.query.first()
    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    return render_template('admin.html', collaborators=collaborators, services=services, users=users, salon_config=salon_config, low_stock=low_stock)

@app.route('/admin-login', methods=['GET', 'POST'])
@login_required
def admin_login():
    if request.method == 'POST':
        admin_user = User.query.filter_by(username='admin').first()
        entered_pass = request.form.get('password', '')
        if admin_user and check_password_hash(admin_user.password_hash, entered_pass):
            session['admin_authenticated'] = True
            return redirect(url_for('admin'))
        flash('Senha incorreta.', 'error')
    return render_template('admin_login.html')

# --- WHATSAPP ---
@app.route('/whatsapp', methods=['GET', 'POST'])
@login_required
def whatsapp():
    config = WhatsAppConfig.query.first()
    if request.method == 'POST':
        config.api_url = request.form['api_url']
        config.api_key = request.form['api_key']
        config.message_template = request.form['message_template']
        config.active = 'active' in request.form
        db.session.commit()
        flash('Configurações salvas!', 'success')
        return redirect(url_for('whatsapp'))
    low_stock = Product.query.filter(Product.quantity <= Product.min_quantity).all()
    return render_template('whatsapp.html', config=config, low_stock=low_stock)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)