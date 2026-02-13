import os
import redis
from flask import Flask, render_template, request, redirect, jsonify, flash
import logging
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_DB_PORT', 6379))

try:
    r = redis.Redis(
        host=REDIS_HOST, 
        port=REDIS_PORT, 
        db=0, 
        decode_responses=True,
        socket_connect_timeout=5,
        socket_keepalive=True
    )
    r.ping()
    logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    r = None

def check_redis_connection():
    """Helper function to check Redis connection"""
    if r is None:
        return False
    try:
        r.ping()
        return True
    except:
        return False

@app.route('/')
def index():
    if not check_redis_connection():
        return render_template('error.html', 
                             error="Cannot connect to Redis database"), 503
    
    try:
        # Get all stations
        stations = sorted(list(r.smembers('stations')))
        if not stations:
            # Default setup
            r.sadd('stations', 'General')
            stations = ['General']

        station_data = {}
        total_urls = 0
        up_count = 0
        down_count = 0
        pending_count = 0
        
        # Build a dictionary: { 'Work': ['google.com'], 'Gaming': ['steam.com'] }
        for station in stations:
            urls = sorted(list(r.smembers(f'urls:{station}')))
            station_data[station] = []
            for url in urls:
                total_urls += 1
                status = r.get(f"status:{url}") or "PENDING"
                
                # Count statuses
                if status == "PENDING":
                    pending_count += 1
                elif "UP" in status:
                    up_count += 1
                else:
                    down_count += 1
                
                station_data[station].append({
                    'url': url, 
                    'status': status
                })
        
        stats = {
            'total': total_urls,
            'up': up_count,
            'down': down_count,
            'pending': pending_count
        }
                
        return render_template('index.html', 
                             station_data=station_data, 
                             stats=stats)
    except Exception as e:
        logger.error(f"Error in index route: {e}")
        return render_template('error.html', 
                             error="An error occurred while loading the dashboard"), 500

@app.route('/add_station', methods=['POST'])
def add_station():
    if not check_redis_connection():
        flash('Cannot connect to database', 'error')
        return redirect('/')
    
    try:
        name = request.form.get('name', '').strip()
        if name:
            # Validate station name
            if len(name) > 50:
                flash('Station name too long (max 50 characters)', 'error')
            elif name in r.smembers('stations'):
                flash(f'Station "{name}" already exists', 'warning')
            else:
                r.sadd('stations', name)
                flash(f'Station "{name}" created successfully', 'success')
                logger.info(f"Created station: {name}")
        else:
            flash('Station name cannot be empty', 'error')
    except Exception as e:
        logger.error(f"Error adding station: {e}")
        flash('Failed to create station', 'error')
    
    return redirect('/')

@app.route('/delete_station', methods=['POST'])
def delete_station():
    if not check_redis_connection():
        flash('Cannot connect to database', 'error')
        return redirect('/')
    
    try:
        name = request.form.get('name')
        if name and name != 'General':  # Protect General station
            # Get URLs count before deletion
            urls_count = r.scard(f'urls:{name}')
            
            r.srem('stations', name)
            r.delete(f'urls:{name}')
            
            flash(f'Station "{name}" deleted ({urls_count} URLs removed)', 'success')
            logger.info(f"Deleted station: {name}")
        elif name == 'General':
            flash('Cannot delete the General station', 'error')
    except Exception as e:
        logger.error(f"Error deleting station: {e}")
        flash('Failed to delete station', 'error')
    
    return redirect('/')

@app.route('/add_url', methods=['POST'])
def add_url():
    if not check_redis_connection():
        flash('Cannot connect to database', 'error')
        return redirect('/')
    
    try:
        url = request.form.get('url', '').strip()
        station = request.form.get('station')
        
        if url and station:
            # Normalize URL
            if not url.startswith(('http://', 'https://')):
                url = f'https://{url}'
            url = url.rstrip('/')
            
            # Check if URL already exists in this station
            if url in r.smembers(f'urls:{station}'):
                flash(f'URL already exists in {station}', 'warning')
            else:
                r.sadd(f'urls:{station}', url)
                r.set(f"status:{url}", "PENDING")  # Set initial status
                flash(f'URL added to {station}', 'success')
                logger.info(f"Added URL {url} to station {station}")
        else:
            flash('URL and station are required', 'error')
    except Exception as e:
        logger.error(f"Error adding URL: {e}")
        flash('Failed to add URL', 'error')
    
    return redirect('/')

@app.route('/delete_url', methods=['POST'])
def delete_url():
    if not check_redis_connection():
        flash('Cannot connect to database', 'error')
        return redirect('/')
    
    try:
        url = request.form.get('url')
        station = request.form.get('station')
        
        if url and station:
            r.srem(f'urls:{station}', url)
            
            # Check if URL exists in other stations before deleting status
            url_exists_elsewhere = False
            for s in r.smembers('stations'):
                if url in r.smembers(f'urls:{s}'):
                    url_exists_elsewhere = True
                    break
            
            if not url_exists_elsewhere:
                r.delete(f"status:{url}")
            
            flash('URL removed', 'success')
            logger.info(f"Deleted URL {url} from station {station}")
    except Exception as e:
        logger.error(f"Error deleting URL: {e}")
        flash('Failed to delete URL', 'error')
    
    return redirect('/')

@app.route('/api/status')
def api_status():
    """API endpoint to get current status without full page reload"""
    if not check_redis_connection():
        return jsonify({'error': 'Database connection failed'}), 503
    
    try:
        stations = sorted(list(r.smembers('stations')))
        station_data = {}
        
        for station in stations:
            urls = sorted(list(r.smembers(f'urls:{station}')))
            station_data[station] = []
            for url in urls:
                status = r.get(f"status:{url}") or "PENDING"
                station_data[station].append({'url': url, 'status': status})
        
        return jsonify({'stations': station_data, 'timestamp': datetime.now().isoformat()})
    except Exception as e:
        logger.error(f"Error in API status: {e}")
        return jsonify({'error': 'Failed to fetch status'}), 500

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error="Page not found"), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('error.html', error="Internal server error"), 500

if __name__ == "__main__":
    # Production settings
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    
    app.run(
        host='0.0.0.0', 
        port=port,
        debug=debug_mode
    )