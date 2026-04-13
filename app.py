import os
import uuid
import boto3
import logging
from flask import Flask, render_template, request, redirect, url_for, session, flash
from botocore.exceptions import ClientError
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# ==========================================
# PRODUCTION LOGGING SETUP
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'super-secret-rental-key-prod-v2')

# ==========================================
# AWS CONFIGURATION (Hardcoded for ap-south-1)
# ==========================================
AWS_REGION = 'ap-south-1' # Explicitly set to match your SNS topic
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:336449003024:velocity'

# Initialize Boto3 Session
boto_session = boto3.Session(region_name=AWS_REGION)
dynamodb = boto_session.resource('dynamodb')
sns_client = boto_session.client('sns')

# Explicit DynamoDB Table Names
USERS_TABLE = 'Velocity_Users'
VEHICLES_TABLE = 'Velocity_Vehicles'
BOOKINGS_TABLE = 'Velocity_Bookings'

# ==========================================
# HELPER: INITIALIZE DYNAMODB TABLES & ADMIN
# ==========================================
def init_db_and_admin():
    """Creates tables if they don't exist and seeds the Admin account."""
    try:
        tables = [table.name for table in dynamodb.tables.all()]
        
        # 1. Create Tables
        if USERS_TABLE not in tables:
            dynamodb.create_table(
                TableName=USERS_TABLE,
                KeySchema=[{'AttributeName': 'user_id', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'user_id', 'AttributeType': 'S'}],
                ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
            )
            logger.info(f"Created table: {USERS_TABLE}")
            
        if VEHICLES_TABLE not in tables:
            dynamodb.create_table(
                TableName=VEHICLES_TABLE,
                KeySchema=[{'AttributeName': 'vehicle_id', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'vehicle_id', 'AttributeType': 'S'}],
                ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
            )
            logger.info(f"Created table: {VEHICLES_TABLE}")
            
        if BOOKINGS_TABLE not in tables:
            dynamodb.create_table(
                TableName=BOOKINGS_TABLE,
                KeySchema=[{'AttributeName': 'booking_id', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'booking_id', 'AttributeType': 'S'}],
                ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
            )
            logger.info(f"Created table: {BOOKINGS_TABLE}")
            
        # 2. Create Hardcoded Admin Account
        # Wait a moment for tables to be active before writing
        table = dynamodb.Table(USERS_TABLE)
        
        # Check if admin already exists by scanning for the email
        response = table.scan(FilterExpression=boto3.dynamodb.conditions.Attr('email').eq('admin@velocity.com'))
        if not response.get('Items'):
            table.put_item(
                Item={
                    'user_id': str(uuid.uuid4()),
                    'name': 'System Administrator',
                    'email': 'admin@velocity.com',
                    'password': generate_password_hash('admin123'), # Hardcoded Admin Password
                    'role': 'admin',
                    'created_at': datetime.now().isoformat()
                }
            )
            logger.info("✅ Hardcoded Admin created: admin@velocity.com / admin123")

    except ClientError as e:
        logger.error(f"Error checking/creating tables: {e}")

# Run initialization automatically
init_db_and_admin()

# ==========================================
# ROUTES
# ==========================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user_id = str(uuid.uuid4())
        name = request.form['name']
        email = request.form['email'].lower().strip()
        password = request.form['password']
        role = request.form.get('role', 'user')

        hashed_password = generate_password_hash(password)

        try:
            table = dynamodb.Table(USERS_TABLE)
            table.put_item(
                Item={
                    'user_id': user_id,
                    'name': name,
                    'email': email,
                    'password': hashed_password,
                    'role': role,
                    'created_at': datetime.now().isoformat()
                }
            )
            logger.info(f"New user registered: {email}")
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except ClientError as e:
            logger.error(f"Registration Error: {e}")
            flash('An error occurred during registration. Please try again.', 'error')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        password = request.form['password']

        try:
            table = dynamodb.Table(USERS_TABLE)
            response = table.scan(
                FilterExpression=boto3.dynamodb.conditions.Attr('email').eq(email)
            )
            items = response.get('Items', [])
            
            if items and check_password_hash(items[0]['password'], password):
                user = items[0]
                session['user_id'] = user['user_id']
                session['name'] = user['name']
                session['role'] = user['role']
                
                logger.info(f"User logged in: {email}")
                if user['role'] == 'admin':
                    return redirect(url_for('admin'))
                return redirect(url_for('dashboard'))
            else:
                logger.warning(f"Failed login attempt for: {email}")
                flash('Invalid credentials. Please try again.', 'error')
                
        except ClientError as e:
            logger.error(f"Login Error: {e}")
            flash('System error. Please try later.', 'error')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    table = dynamodb.Table(VEHICLES_TABLE)

    if request.method == 'POST':
        action = request.form.get('action')

        try:
            if action == 'single':
                table.put_item(
                    Item={
                        'vehicle_id': str(uuid.uuid4()),
                        'v_type': request.form['v_type'],
                        'power': request.form['power'],
                        'gear': request.form['gear'],
                        'location': request.form['location'].lower(),
                        'model_name': request.form['model_name'],
                        'price': int(request.form['price']),
                        'status': 'Available',
                        'image_url': request.form['image_url'] or 'https://images.unsplash.com/photo-1558981403-c5f9899a28bc?w=800&q=80'
                    }
                )
                logger.info("Admin added a single vehicle.")
                flash('Vehicle added successfully!', 'success')

            elif action == 'bulk':
                bulk_data = request.form['bulk_data']
                lines = bulk_data.strip().split('\n')
                count = 0
                with table.batch_writer() as batch:
                    for line in lines:
                        parts = [p.strip() for p in line.split(',')]
                        if len(parts) == 7:
                            batch.put_item(
                                Item={
                                    'vehicle_id': str(uuid.uuid4()),
                                    'v_type': parts[0],
                                    'power': parts[1],
                                    'gear': parts[2],
                                    'location': parts[3].lower(),
                                    'model_name': parts[4],
                                    'price': int(parts[5]),
                                    'status': 'Available',
                                    'image_url': parts[6]
                                }
                            )
                            count += 1
                logger.info(f"Admin bulk uploaded {count} vehicles.")
                flash(f'Bulk upload successful! {count} vehicles added.', 'success')
        except ClientError as e:
            logger.error(f"Admin Action Error: {e}")
            flash('Error processing request.', 'error')

    response = table.scan()
    vehicles = response.get('Items', [])
    return render_template('admin.html', vehicles=vehicles)

@app.route('/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    location_query = request.args.get('location', '').lower().strip()
    
    try:
        table_vehicles = dynamodb.Table(VEHICLES_TABLE)
        if location_query:
            response = table_vehicles.scan(
                FilterExpression=boto3.dynamodb.conditions.Attr('location').eq(location_query) &
                                 boto3.dynamodb.conditions.Attr('status').eq('Available')
            )
        else:
            response = table_vehicles.scan(
                FilterExpression=boto3.dynamodb.conditions.Attr('status').eq('Available')
            )
        vehicles = response.get('Items', [])

        table_bookings = dynamodb.Table(BOOKINGS_TABLE)
        booking_resp = table_bookings.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('user_id').eq(session['user_id'])
        )
        bookings = booking_resp.get('Items', [])
        
    except ClientError as e:
        logger.error(f"Dashboard Data Error: {e}")
        vehicles, bookings = [], []

    return render_template('user_dashboard.html', vehicles=vehicles, bookings=bookings, current_location=location_query)

@app.route('/payment/<vehicle_id>', methods=['GET'])
def payment(vehicle_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    try:
        table = dynamodb.Table(VEHICLES_TABLE)
        response = table.get_item(Key={'vehicle_id': vehicle_id})
        vehicle = response.get('Item')
        
        if not vehicle:
            flash('Vehicle not found.', 'error')
            return redirect(url_for('dashboard'))
            
    except ClientError as e:
        logger.error(f"Payment fetch error: {e}")
        return redirect(url_for('dashboard'))

    return render_template('payment.html', vehicle=vehicle)

@app.route('/process_payment/<vehicle_id>', methods=['POST'])
def process_payment(vehicle_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    days = int(request.form.get('days', 1))
    
    try:
        v_table = dynamodb.Table(VEHICLES_TABLE)
        v_resp = v_table.get_item(Key={'vehicle_id': vehicle_id})
        vehicle = v_resp.get('Item')
        
        if vehicle['status'] != 'Available':
            flash('Vehicle is no longer available.', 'error')
            return redirect(url_for('dashboard'))
        
        total_price = vehicle['price'] * days
        booking_id = f"BKG-{str(uuid.uuid4())[:8].upper()}"
        
        b_table = dynamodb.Table(BOOKINGS_TABLE)
        b_table.put_item(
            Item={
                'booking_id': booking_id,
                'user_id': session['user_id'],
                'vehicle_id': vehicle_id,
                'model_name': vehicle['model_name'],
                'days': days,
                'total_price': total_price,
                'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        )
        
        v_table.update_item(
            Key={'vehicle_id': vehicle_id},
            UpdateExpression="SET #s = :val",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':val': 'Booked'}
        )
        logger.info(f"Booking {booking_id} created for user {session['user_id']}")
        
        message = f"Hello {session['name']}, your booking ({booking_id}) for {vehicle['model_name']} is confirmed! Total: ${total_price}."
        try:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=message,
                Subject='Velocity Rentals: Booking Confirmed'
            )
            logger.info(f"SNS sent for booking {booking_id}")
        except ClientError as e:
            logger.warning(f"SNS Notification failed: {e}")
            
    except ClientError as e:
        logger.error(f"Payment processing error: {e}")
        flash("An error occurred processing your payment.", "error")
        return redirect(url_for('dashboard'))
    
    return redirect(url_for('letter', booking_id=booking_id))

@app.route('/letter/<booking_id>')
def letter(booking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        table = dynamodb.Table(BOOKINGS_TABLE)
        resp = table.get_item(Key={'booking_id': booking_id})
        booking = resp.get('Item')
        
        if not booking or booking['user_id'] != session['user_id']:
            return "Unauthorized or Not Found", 404
            
    except ClientError as e:
        logger.error(f"Letter fetch error: {e}")
        return "System error fetching letter.", 500
        
    return render_template('letter.html', booking=booking, name=session['name'])

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
