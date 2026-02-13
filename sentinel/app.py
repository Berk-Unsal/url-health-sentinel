import os
import time
import requests
import redis
import sys
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_DB_PORT', 6379))
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 10))
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', 5))

def connect_redis():
    """Establish connection to Redis with retry logic"""
    max_retries = 5
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            r = redis.Redis(
                host=REDIS_HOST, 
                port=REDIS_PORT, 
                db=0, 
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30
            )
            r.ping()
            
            # Create a default station if the database is empty
            if not r.exists('stations'):
                r.sadd('stations', 'General')
                logger.info("Created default 'General' station")
            
            logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
            return r
        except Exception as e:
            logger.error(f"Connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logger.critical("Failed to connect to Redis after all retries")
                sys.exit(1)

def check_url(url, headers):
    """Check a single URL and return its status"""
    try:
        start_time = time.time()
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response_time = int((time.time() - start_time) * 1000)  # ms
        
        if response.status_code == 200:
            return f"UP ({response_time}ms)"
        else:
            return f"DOWN ({response.status_code})"
    except requests.exceptions.Timeout:
        return "DOWN (Timeout)"
    except requests.exceptions.ConnectionError:
        return "DOWN (Connection Error)"
    except requests.exceptions.TooManyRedirects:
        return "DOWN (Too Many Redirects)"
    except requests.exceptions.RequestException as e:
        return f"DOWN (Error)"
    except Exception as e:
        logger.error(f"Unexpected error checking {url}: {e}")
        return "DOWN (Unknown Error)"

def check_urls(r):
    """Main monitoring loop"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    check_count = 0
    
    while True:
        try:
            check_count += 1
            start_time = time.time()
            
            # Get all Stations
            stations = r.smembers('stations')
            
            # Collect ALL URLs from ALL stations
            all_urls = set()
            for station in stations:
                station_urls = r.smembers(f'urls:{station}')
                all_urls.update(station_urls)
            
            logger.info(f"=== Check #{check_count}: {len(all_urls)} URLs across {len(stations)} stations ===")
            
            if not all_urls:
                logger.warning("No URLs to monitor")
            
            # Check each URL
            for i, url in enumerate(all_urls, 1):
                try:
                    status = check_url(url, headers)
                    r.set(f"status:{url}", status)
                    
                    # Log status with appropriate level
                    if "UP" in status:
                        logger.info(f"[{i}/{len(all_urls)}] ✓ {url}: {status}")
                    else:
                        logger.warning(f"[{i}/{len(all_urls)}] ✗ {url}: {status}")
                    
                    # Small delay between requests to avoid overwhelming servers
                    time.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error processing {url}: {e}")
                    r.set(f"status:{url}", "DOWN (Processing Error)")
            
            elapsed = time.time() - start_time
            logger.info(f"Check completed in {elapsed:.2f}s. Next check in {CHECK_INTERVAL}s")
            
            time.sleep(CHECK_INTERVAL)
            
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Lost connection to Redis: {e}")
            logger.info("Attempting to reconnect...")
            r = connect_redis()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal. Exiting gracefully...")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Unexpected error in monitoring loop: {e}")
            time.sleep(5)  # Wait before retrying

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("URL Sentinel Monitor Starting")
    logger.info(f"Redis: {REDIS_HOST}:{REDIS_PORT}")
    logger.info(f"Check Interval: {CHECK_INTERVAL}s")
    logger.info(f"Request Timeout: {REQUEST_TIMEOUT}s")
    logger.info("=" * 60)
    
    r = connect_redis()
    check_urls(r)