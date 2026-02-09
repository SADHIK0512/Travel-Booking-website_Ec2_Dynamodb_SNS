from flask import Flask, render_template, request, redirect, session, url_for, flash
import uuid
import boto3
import os
from decimal import Decimal
from boto3.dynamodb.conditions import Attr
from utils.data import transport_data, hotel_data 
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = "travelgo_secret" 

# --- SECURE AWS SETUP ---
# Boto3 will automatically retrieve credentials from the IAM Role attached 
# to the EC2 instance, ECS task, or Lambda function.
# We only explicitly set the region if provided in env, otherwise it defaults.
aws_region = os.getenv('AWS_DEFAULT_REGION', 'ap-south-1')

dynamodb = boto3.resource('dynamodb', region_name=aws_region)
sns = boto3.client('sns', region_name=aws_region)

# DynamoDB Tables
users_table = dynamodb.Table('travel-Users')
bookings_table = dynamodb.Table('Bookinngs')
services_table = dynamodb.Table('TravelServices') 

# --- ADMIN CREDENTIALS SETTINGS ---
ADMIN_EMAIL = "sadzx.0512@gmail.com"
ADMIN_PASSWORD = "Sadzx@05" 

def is_admin():
    return session.get('user') == ADMIN_EMAIL

# --- ADMIN ROUTES ---

@app.route('/admin')
def admin_portal():
    if 'user' in session and is_admin():
        return render_template('admin_dashboard.html')
    return redirect('/login')

@app.route('/admin/add_transport', methods=['POST'])
def add_transport():
    if not is_admin(): return redirect('/')
    
    service_id = str(uuid.uuid4())[:8]
    category = request.form['category']
    
    new_entry = {
        'service_id': service_id,
        'category': category,
        'source': request.form['source'],
        'destination': request.form['destination'],
        'name': request.form['name'],
        'price': Decimal(request.form['price']),
        'details': request.form['details']
    }
    
    services_table.put_item(Item=new_entry)
    flash(f"New {category} added successfully!")
    return redirect('/admin')

@app.route('/admin/add_hotel', methods=['POST'])
def add_hotel():
    if not is_admin(): return redirect('/')
    
    service_id = str(uuid.uuid4())[:8]
    new_hotel = {
        'service_id': service_id,
        'category': 'hotel',
        'location': request.form['location'],
        'name': request.form['name'],
        'price': Decimal(request.form['price']),
        'details': request.form['details']
    }
    services_table.put_item(Item=new_hotel)
    flash("Hotel added successfully!")
    return redirect('/admin')

@app.route('/migrate')
def migrate_data():
    if not is_admin(): return "Unauthorized", 403
    
    for cat, items in transport_data.items():
        for item in items:
            item['service_id'] = str(uuid.uuid4())[:8]
            item['category'] = cat
            item['price'] = Decimal(str(item['price']))
            services_table.put_item(Item=item)
            
    for hotel in hotel_data:
        hotel['service_id'] = str(uuid.uuid4())[:8]
        hotel['category'] = 'hotel'
        hotel['price'] = Decimal(str(hotel['price']))
        services_table.put_item(Item=hotel)
        
    return "Data Migration to DynamoDB Successful!"

# --- SEARCH LOGIC (Query DynamoDB) ---

def get_search_results(category, source, destination):
    response = services_table.scan(
        FilterExpression=Attr('category').eq(category) & 
                         Attr('source').eq(source) & 
                         Attr('destination').eq(destination)
    )
    return response.get('Items', [])

@app.route('/bus', methods=['GET', 'POST'])

@app.route('/buses', methods=['GET', 'POST'])
def bus():
    if request.method == 'POST':
        s, d = request.form['source'].strip(), request.form['destination'].strip()
        results = get_search_results('bus', s, d)
        return render_template('bus.html', buses=results, source=s, destination=d)
    return render_template('bus.html', buses=None)

@app.route('/train', methods=['GET', 'POST'])
def train():
    if request.method == 'POST':
        s, d = request.form['source'].strip(), request.form['destination'].strip()
        results = get_search_results('train', s, d)
        return render_template('train.html', trains=results, source=s, destination=d)
    return render_template('train.html', trains=None)

@app.route('/flight', methods=['GET', 'POST'])
@app.route('/flights', methods=['GET', 'POST'])
def flight():
    if request.method == 'POST':
        s, d = request.form['source'].strip(), request.form['destination'].strip()
        results = get_search_results('flight', s, d)
        return render_template('flight.html', flights=results, source=s, destination=d)
    return render_template('flight.html', flights=None)

@app.route('/hotels', methods=['GET', 'POST'])
def hotels():
    if request.method == 'POST':
        city = request.form.get('city').strip()
        response = services_table.scan(
            FilterExpression=Attr('category').eq('hotel') & Attr('location').eq(city)
        )
        results = response.get('Items', [])
        return render_template('hotels.html', hotels=results, city=city)
    return render_template('hotels.html', hotels=None)

# --- USER AUTHENTICATION & BOOKING ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['user'] = email
            return redirect('/admin')
            
        user = users_table.get_item(Key={'email': email}).get('Item')
        if user and user['password'] == password:
            session['user'] = email
            users_table.update_item(
                Key={'email': email}, 
                UpdateExpression="ADD logins :inc", 
                ExpressionAttributeValues={':inc': Decimal(1)}
            )
            return redirect('/dashboard')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session: return redirect('/login')
    email = session['user']
    user = users_table.get_item(Key={'email': email}).get('Item')
    try:
        response = bookings_table.scan(FilterExpression=Attr('email').eq(email))
        bookings = response.get('Items', [])
    except Exception as e:
        bookings = []
    return render_template('dashboard.html', name=user['name'], bookings=bookings)

@app.route('/book', methods=['POST'])
def book():
    if 'user' not in session: return redirect('/login')
    session['pending_booking'] = {
        "booking_id": str(uuid.uuid4())[:8],
        "type": request.form['type'],
        "source": request.form.get('source', 'N/A'),
        "destination": request.form.get('destination', 'N/A'),
        "date": request.form.get('date', 'N/A'),
        "details": request.form['details'],
        "price": Decimal(request.form['price']),
        "user_email": session['user']
    }
    if session['pending_booking']['type'] in ['Bus', 'Train', 'Flight']:
        return redirect('/select_seats')
    return render_template("payment.html", booking=session['pending_booking'])

@app.route('/confirm_seats', methods=['POST'])
def confirm_seats():
    if 'user' not in session or 'pending_booking' not in session: return redirect('/login')
    selected = request.form.get('selected_seats')
    booking = session['pending_booking']
    booking['details'] = f"{booking['details']} | Seats: {selected}"
    session['pending_booking'] = booking
    return render_template("payment.html", booking=session['pending_booking'])

@app.route('/payment', methods=['POST'])
def payment():
    if 'user' not in session or 'pending_booking' not in session: return redirect('/login')
    booking = session.pop('pending_booking')
    booking['email'] = session['user']
    booking['payment_method'] = request.form['method']
    booking['payment_reference'] = request.form['reference']
    try:
        bookings_table.put_item(Item=booking)
        sns.publish(TopicArn=os.getenv('SNS_TOPIC_ARN'), Message=f"Confirmed! {booking['details']}", Subject="TravelGo Confirmation")
    except Exception as e: print(e)
    return redirect('/dashboard')

@app.route('/print_ticket/<booking_id>')
def print_ticket(booking_id):
    if 'user' not in session: return redirect('/login')
    response = bookings_table.get_item(Key={'email': session['user'], 'booking_id': booking_id})
    booking = response.get('Item')
    if not booking: return "Booking not found", 404
    return render_template('ticket.html', booking=booking)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        if 'Item' in users_table.get_item(Key={'email': email}):
            return render_template('register.html', message="User already exists")
        users_table.put_item(Item={'email': email, 'name': request.form['name'], 'password': request.form['password'], 'logins': 0})
        return redirect('/login')
    return render_template('register.html')

@app.route('/remove_booking', methods=['POST'])
def remove_booking():
    if 'user' not in session: return redirect('/login')
    bookings_table.delete_item(Key={'email': session['user'], 'booking_id': request.form.get('booking_id')})
    return redirect('/dashboard')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/select_seats')
def select_seats():
    if 'user' not in session or 'pending_booking' not in session: return redirect('/login')
    return render_template('select_seats.html')

@app.route('/')
def home():
    # This tells Flask what to show at the main URL
    return render_template('index.html', logged_in='user' in session)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)

