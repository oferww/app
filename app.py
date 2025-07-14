from flask import Flask, render_template, request, jsonify
from datetime import datetime
from bs4 import BeautifulSoup
import re
import uuid
import threading
from threading import Lock
from playwright.sync_api import sync_playwright
import requests
import logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Global storage for flight data with thread-safe access
flight_data_store = {}
store_lock = Lock()

days_in_month = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31
}

# Airport codes and their display names
AIRPORTS = {
    'TLV': 'Tel Aviv (TLV)',
    'NYC': 'New York (NYC)',
    'LON': 'London (LON)',
    'PAR': 'Paris (PAR)',
    'ROM': 'Rome (ROM)',
    'BER': 'Berlin (BER)',
    'MAD': 'Madrid (MAD)',
    'BCN': 'Barcelona (BCN)',
    'MIL': 'Milan (MIL)',
    'FRA': 'Frankfurt (FRA)',
    'AMS': 'Amsterdam (AMS)',
    'ZUR': 'Zurich (ZUR)',
    'VIE': 'Vienna (VIE)',
    'PRG': 'Prague (PRG)',
    'BUD': 'Budapest (BUD)',
    'ATH': 'Athens (ATH)',
    'IST': 'Istanbul (IST)',
    'DXB': 'Dubai (DXB)',
    'BKK': 'Bangkok (BKK)',
    'NRT': 'Tokyo (NRT)',
    'HKG': 'Hong Kong (HKG)',
    'SIN': 'Singapore (SIN)',
    'SYD': 'Sydney (SYD)',
    'MEL': 'Melbourne (MEL)',
    'LAX': 'Los Angeles (LAX)',
    'MIA': 'Miami (MIA)',
    'CHI': 'Chicago (CHI)',
    'TOR': 'Toronto (TOR)'
}

class FlightDataExtractor:
    def __init__(self):
        pass  # No browser setup needed for Playwright

    def get_rendered_html(self, url, wait_time=60):
        print(f"Playwright fetching: {url}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            try:
                # Wait for any of the selectors to appear
                page.wait_for_selector(
                    "xpath=//div[contains(@nagish-checkbox, 'ישיר')]/following-sibling::div[@class='extra-data']", timeout=30*1000)
                page.wait_for_selector(
                    "xpath=//div[contains(@nagish-checkbox, '1 עצירות')]/following-sibling::div[@class='extra-data']", timeout=30*1000)
                page.wait_for_selector(
                    "xpath=//div[contains(@nagish-checkbox, '2+ עצירות')]/following-sibling::div[@class='extra-data']", timeout=30*1000)
            except Exception as e:
                print(f"Playwright wait timeout or error: {e}")
            html = page.content()
            with open('debug_rendered_playwright.html', 'w', encoding='utf-8') as f:
                f.write(html)
            browser.close()
            return html

    def extract_flight_data(self, url):
        """Extract flight data from bookaflight URL using Playwright for JS rendering"""
        try:
            html = self.get_rendered_html(url, wait_time=60)
            soup = BeautifulSoup(html, 'html.parser')
            flight_data = {
                'direct_flights': [],
                'one_stop_flights': [],
                'multi_stop_flights': [],
                'error': None
            }
            # --- Custom direct flight extraction using BeautifulSoup ---
            # Direct flights
            direct_divs = soup.find_all('div', attrs={"nagish-checkbox": True})
            for div in direct_divs:
                nagish_val = div.get('nagish-checkbox', '')
                # Direct
                if 'ישיר' in nagish_val:
                    next_sibling = div.find_next_sibling('div', class_='extra-data')
                    if next_sibling:
                        inner_divs = next_sibling.find_all('div', recursive=False)
                        if len(inner_divs) >= 2:
                            price_text = inner_divs[1].get_text(strip=True)
                            if price_text:
                                flight_data['direct_flights'].append({
                                    'price': price_text,
                                    'stops': 0,
                                    'airline': None,
                                    'duration': None,
                                    'departure_time': None,
                                    'arrival_time': None
                                })
                # 1 stop
                elif '1 עצירות' in nagish_val:
                    next_sibling = div.find_next_sibling('div', class_='extra-data')
                    if next_sibling:
                        inner_divs = next_sibling.find_all('div', recursive=False)
                        if len(inner_divs) >= 2:
                            price_text = inner_divs[1].get_text(strip=True)
                            if price_text:
                                flight_data['one_stop_flights'].append({
                                    'price': price_text,
                                    'stops': 1,
                                    'airline': None,
                                    'duration': None,
                                    'departure_time': None,
                                    'arrival_time': None
                                })
                # 2+ stops
                elif '2+ עצירות' in nagish_val:
                    next_sibling = div.find_next_sibling('div', class_='extra-data')
                    if next_sibling:
                        inner_divs = next_sibling.find_all('div', recursive=False)
                        if len(inner_divs) >= 2:
                            price_text = inner_divs[1].get_text(strip=True)
                            if price_text:
                                flight_data['multi_stop_flights'].append({
                                    'price': price_text,
                                    'stops': 2,
                                    'airline': None,
                                    'duration': None,
                                    'departure_time': None,
                                    'arrival_time': None
                                })
            # --- End custom extraction ---
            # Only run the old logic if no flights found by this method
            if not any([flight_data['direct_flights'], flight_data['one_stop_flights'], flight_data['multi_stop_flights']]):
                flight_containers = soup.find_all(['div', 'li'], class_=re.compile(r'flight|result|option', re.I))
                for container in flight_containers:
                    flight_info = self._parse_flight_container(container)
                    if flight_info:
                        stops = flight_info.get('stops', 0)
                        if stops == 0:
                            flight_data['direct_flights'].append(flight_info)
                        elif stops == 1:
                            flight_data['one_stop_flights'].append(flight_info)
                        else:
                            flight_data['multi_stop_flights'].append(flight_info)
            # If no flights found, try alternative parsing methods
            if not any([flight_data['direct_flights'], flight_data['one_stop_flights'], flight_data['multi_stop_flights']]):
                flight_data = self._alternative_parsing(soup)
            return flight_data
        except Exception as e:
            return {'error': f'Parsing failed: {str(e)}', 'direct_flights': [], 'one_stop_flights': [], 'multi_stop_flights': []}

    def _parse_flight_container(self, container):
        """Parse individual flight container"""
        try:
            # Look for price information
            price_text = self._extract_price(container)
            
            # Look for stops information
            stops = self._extract_stops(container)
            
            # Look for airline information
            airline = self._extract_airline(container)
            
            # Look for duration information
            duration = self._extract_duration(container)
            
            # Look for departure/arrival times
            times = self._extract_times(container)
            
            if price_text:  # Only return if we found a price
                return {
                    'price': price_text,
                    'stops': stops,
                    'airline': airline,
                    'duration': duration,
                    'departure_time': times.get('departure'),
                    'arrival_time': times.get('arrival')
                }
            
        except Exception as e:
            print(f"Error parsing flight container: {e}")
            
        return None
    
    def _extract_price(self, container):
        """Extract price from container"""
        # Look for price patterns
        price_patterns = [
            r'₪[\d,]+',
            r'\$[\d,]+',
            r'€[\d,]+',
            r'[\d,]+\s*₪',
            r'[\d,]+\s*\$',
            r'[\d,]+\s*€'
        ]
        
        text = container.get_text()
        for pattern in price_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group()
        
        # Try to find price in specific elements
        price_elements = container.find_all(['span', 'div', 'p'], class_=re.compile(r'price|cost|amount', re.I))
        for element in price_elements:
            text = element.get_text().strip()
            for pattern in price_patterns:
                match = re.search(pattern, text)
                if match:
                    return match.group()
        
        return None
    
    def _extract_stops(self, container):
        """Extract number of stops"""
        text = container.get_text().lower()
        
        # Hebrew patterns
        if 'טיסות ישירות' in text or 'ישיר' in text:
            return 0
        elif '1 עצירות ביניים' in text:
            return 1
        elif '2+ עצירות ביניים' in text:
            return 2
        
        # English patterns
        if 'direct' in text or 'nonstop' in text:
            return 0
        elif '1 stop' in text:
            return 1
        elif '2 stop' in text or 'stops' in text:
            return 2
        
        # Try to find stop indicators in elements
        stop_elements = container.find_all(['span', 'div'], class_=re.compile(r'stop|connection', re.I))
        for element in stop_elements:
            element_text = element.get_text().lower()
            if 'direct' in element_text or 'nonstop' in element_text:
                return 0
            elif '1' in element_text:
                return 1
            elif '2' in element_text:
                return 2
        
        return 0  # Default to direct
    
    def _extract_airline(self, container):
        """Extract airline information"""
        # Look for airline names or codes
        airline_patterns = [
            r'[A-Z]{2}\d{3,4}',  # Flight codes like EL123
            r'El Al', r'Lufthansa', r'Emirates', r'Turkish Airlines',
            r'אל על', r'לופטהנזה', r'אמירייטס', r'טורקיש איירליינס'
        ]
        
        text = container.get_text()
        for pattern in airline_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group()
        
        return None
    
    def _extract_duration(self, container):
        """Extract flight duration"""
        duration_pattern = r'(\d+h\s*\d*m?|\d+:\d+)'
        text = container.get_text()
        match = re.search(duration_pattern, text)
        if match:
            return match.group()
        return None
    
    def _extract_times(self, container):
        """Extract departure and arrival times"""
        time_pattern = r'(\d{1,2}:\d{2})'
        text = container.get_text()
        times = re.findall(time_pattern, text)
        
        result = {}
        if len(times) >= 2:
            result['departure'] = times[0]
            result['arrival'] = times[1]
        elif len(times) == 1:
            result['departure'] = times[0]
        
        return result
    
    def _alternative_parsing(self, soup):
        """Alternative parsing method if main method fails"""
        flight_data = {
            'direct_flights': [],
            'one_stop_flights': [],
            'multi_stop_flights': [],
            'error': None
        }
        
        # Try to find any elements that might contain flight information
        potential_elements = soup.find_all(['div', 'li', 'tr'], string=re.compile(r'₪|\$|€', re.I))
        
        for element in potential_elements:
            parent = element.parent
            if parent:
                flight_info = self._parse_flight_container(parent)
                if flight_info:
                    stops = flight_info.get('stops', 0)
                    if stops == 0:
                        flight_data['direct_flights'].append(flight_info)
                    elif stops == 1:
                        flight_data['one_stop_flights'].append(flight_info)
                    else:
                        flight_data['multi_stop_flights'].append(flight_info)
        
        # If still no results, create dummy data to show the structure
        if not any([flight_data['direct_flights'], flight_data['one_stop_flights'], flight_data['multi_stop_flights']]):
            flight_data['error'] = 'No flight data found. The website structure may have changed.'
            # Add sample data for demonstration
            flight_data['direct_flights'] = [{
                'price': 'Price not available',
                'stops': 0,
                'airline': 'Data extraction needed',
                'duration': 'N/A',
                'departure_time': 'N/A',
                'arrival_time': 'N/A'
            }]
        
        return flight_data


def fix_number(num):
    return f'0{num}' if num < 10 else str(num)


def generate_days(day_start, day_end, month_start, month_end):
    all_days = []
    if month_start == month_end and day_start == day_end:
        all_days.append((day_start, month_start))
        return all_days
    if month_start != month_end:
        for month in range(month_start + 1, month_end):
            for day in range(1, days_in_month[month] + 1):
                all_days.append((day, month))
    if day_end < 32 and day_start > day_end:
        for day in range(day_start, days_in_month[month_start] + 1):
            all_days.append((day, month_start))
        for day in range(1, day_end + 1):
            all_days.append((day, month_end))
    if day_start < day_end:
        for day in range(day_start, day_end + 1):
            all_days.append((day, month_end))
    all_days = sorted(all_days, key=lambda x: (x[1], x[0]))
    return all_days


def create_dates(ida_day_start, ida_month_start, ida_day_end, ida_month_end, vuelta_day_start, vuelta_month_start,
                 vuelta_day_end, vuelta_month_end):
    # CORRECTED: Swapped ida_month_start/ida_day_end and vuelta_month_start/vuelta_day_end
    all_days_ida = generate_days(ida_day_start, ida_day_end, ida_month_start, ida_month_end)
    all_days_vuelta = generate_days(vuelta_day_start, vuelta_day_end, vuelta_month_start, vuelta_month_end)
    return all_days_ida, all_days_vuelta


def find_flight_options(ida_day_start, ida_month_start, ida_day_end, ida_month_end, vuelta_day_start, vuelta_month_start,
                       vuelta_day_end, vuelta_month_end, minimum_days, origin, destination, ida_year, vuelta_year):
    ida_days, vuelta_days = create_dates(ida_day_start, ida_month_start, ida_day_end, ida_month_end,
                                         vuelta_day_start, vuelta_month_start, vuelta_day_end, vuelta_month_end)

    combinations = []
    for ida in ida_days:
        for vuelta in vuelta_days:
            diff = (datetime(vuelta_year, vuelta[1], vuelta[0]) -
                    datetime(ida_year, ida[1], ida[0])).days
            if diff >= minimum_days:
                combinations.append((ida, vuelta))

    flight_options = []
    for ida, vuelta in combinations:
        departure_date = datetime(ida_year, ida[1], ida[0])
        return_date = datetime(vuelta_year, vuelta[1], vuelta[0])
        duration = (return_date - departure_date).days
        values = {
            'day_1': fix_number(ida[0]),
            'month_1': fix_number(ida[1]),
            'day_2': fix_number(vuelta[0]),
            'month_2': fix_number(vuelta[1])
        }
        url = f"https://www.bookaflight.co.il/results?q=BOOKAFLIGHT-PRECISE-TOUR-REGULAR-1-{origin}-" \
              f"{values['day_1']}-{values['month_1']}-{ida_year}-{destination}-2-{destination}-" \
              f"{values['day_2']}-{values['month_2']}-{vuelta_year}-{origin}-ADT-1-CHD-0-INF-0-YCD-0&filters=DR-OS-TS-FTC-FTLC-FTO-FTTOW"
        flight_option = {
            'id': str(uuid.uuid4()),
            'origin': origin,
            'destination': destination,
            'departure_date': departure_date.strftime('%Y-%m-%d'),
            'departure_day': departure_date.strftime('%A'),
            'return_date': return_date.strftime('%Y-%m-%d'),
            'return_day': return_date.strftime('%A'),
            'duration': duration,
            'url': url,
            'flight_data': None,  # Will be populated after extraction
            'loading': True  # Initially loading
        }
        flight_options.append(flight_option)
    return flight_options


def extract_flight_data_background(flight_options, search_id):
    """Extract flight data in background as quickly as possible, updating each category as soon as it's found"""
    extractor = FlightDataExtractor()

    def update_store(option_id, key, value):
        with store_lock:
            for stored_option in flight_data_store[search_id]:
                if stored_option['id'] == option_id:
                    if key == 'flight_data':
                        stored_option['flight_data'] = value
                    else:
                        stored_option['flight_data'][key] = value
                    break
    def set_loading(option_id, value):
        with store_lock:
            for stored_option in flight_data_store[search_id]:
                if stored_option['id'] == option_id:
                    stored_option['loading'] = value
                    break

    def extract_for_option(option):
        try:
            print(f"Starting extraction for option {option['id']}")
            # Initialize empty flight_data for incremental updates
            update_store(option['id'], 'flight_data', {
                'direct_flights': [],
                'one_stop_flights': [],
                'multi_stop_flights': [],
                'error': None
            })
            # Fetch and parse rendered HTML
            html = extractor.get_rendered_html(option['url'], wait_time=60)
            soup = BeautifulSoup(html, 'html.parser')
            # --- Direct flights ---
            direct_flights = []
            direct_divs = soup.find_all('div', attrs={"nagish-checkbox": True})
            for div in direct_divs:
                nagish_val = div.get('nagish-checkbox', '')
                if 'ישיר' in nagish_val:
                    next_sibling = div.find_next_sibling('div', class_='extra-data')
                    if next_sibling:
                        inner_divs = next_sibling.find_all('div', recursive=False)
                        if len(inner_divs) >= 2:
                            price_text = inner_divs[1].get_text(strip=True)
                            if price_text:
                                direct_flights.append({
                                    'price': price_text,
                                    'stops': 0,
                                    'airline': None,
                                    'duration': None,
                                    'departure_time': None,
                                    'arrival_time': None
                                })
            update_store(option['id'], 'direct_flights', direct_flights)
            # --- 1-stop flights ---
            one_stop_flights = []
            for div in direct_divs:
                nagish_val = div.get('nagish-checkbox', '')
                if '1 עצירות' in nagish_val:
                    next_sibling = div.find_next_sibling('div', class_='extra-data')
                    if next_sibling:
                        inner_divs = next_sibling.find_all('div', recursive=False)
                        if len(inner_divs) >= 2:
                            price_text = inner_divs[1].get_text(strip=True)
                            if price_text:
                                one_stop_flights.append({
                                    'price': price_text,
                                    'stops': 1,
                                    'airline': None,
                                    'duration': None,
                                    'departure_time': None,
                                    'arrival_time': None
                                })
            update_store(option['id'], 'one_stop_flights', one_stop_flights)
            # --- 2+ stops flights ---
            multi_stop_flights = []
            for div in direct_divs:
                nagish_val = div.get('nagish-checkbox', '')
                if '2+ עצירות' in nagish_val:
                    next_sibling = div.find_next_sibling('div', class_='extra-data')
                    if next_sibling:
                        inner_divs = next_sibling.find_all('div', recursive=False)
                        if len(inner_divs) >= 2:
                            price_text = inner_divs[1].get_text(strip=True)
                            if price_text:
                                multi_stop_flights.append({
                                    'price': price_text,
                                    'stops': 2,
                                    'airline': None,
                                    'duration': None,
                                    'departure_time': None,
                                    'arrival_time': None
                                })
            update_store(option['id'], 'multi_stop_flights', multi_stop_flights)
            # --- Error handling ---
            if not any([direct_flights, one_stop_flights, multi_stop_flights]):
                update_store(option['id'], 'error', 'No flight data found. The website structure may have changed.')
            # Mark as done
            set_loading(option['id'], False)
            print(f"Updated data for option {option['id']}")
            return option
        except Exception as e:
            print(f"Error extracting data for option {option['id']}: {e}")
            update_store(option['id'], 'flight_data', {
                'error': f'Failed to extract data: {str(e)}',
                'direct_flights': [],
                'one_stop_flights': [],
                'multi_stop_flights': []
            })
            set_loading(option['id'], False)
            return option

    # Start extraction for each option as soon as possible (no delay)
    threads = []
    for option in flight_options:
        thread = threading.Thread(target=extract_for_option, args=(option,))
        thread.daemon = True
        thread.start()
        threads.append(thread)
    return threads


@app.template_filter('datetimeformat')
def datetimeformat(value, format='%a, %d %b %Y'):
    if not value:
        return ''
    try:
        # Accepts 'YYYY-MM-DD' or datetime/date object
        if isinstance(value, str):
            dt = datetime.strptime(value, '%Y-%m-%d')
        else:
            dt = value
        return dt.strftime(format)
    except Exception:
        return value  # fallback: return as is


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        data = request.form

        ida_start = datetime.strptime(data['ida_start'], '%Y-%m-%d')
        ida_end = datetime.strptime(data['ida_end'], '%Y-%m-%d')
        vuelta_start = datetime.strptime(data['vuelta_start'], '%Y-%m-%d')
        vuelta_end = datetime.strptime(data['vuelta_end'], '%Y-%m-%d')
        min_days = int(data['min_days'])
        # Get up to 3 origins and 3 destinations
        origins = []
        for val in request.form.getlist('origin'):
            origins.extend(val.split(','))
        origins = [o.strip() for o in origins if o.strip()][:3]

        destinations = []
        for val in request.form.getlist('destination'):
            destinations.extend(val.split(','))
        destinations = [d.strip() for d in destinations if d.strip()][:3]

        flight_options = []
        for origin in origins:
            for destination in destinations:
                if origin == destination:
                    continue
                flight_options += find_flight_options(
                    ida_start.day, ida_start.month,
                    ida_end.day, ida_end.month,
                    vuelta_start.day, vuelta_start.month,
                    vuelta_end.day, vuelta_end.month,
                    min_days,
                    origin,
                    destination,
                    ida_start.year,
                    vuelta_start.year
                )
        # Generate unique search ID
        search_id = str(uuid.uuid4())
        # Store flight options in memory
        with store_lock:
            flight_data_store[search_id] = flight_options
        # Start background extraction for each option
        extraction_threads = extract_flight_data_background(flight_options, search_id)
        # Prepare search_params for all selected origins/destinations
        search_params = {
            'origins': [AIRPORTS[o] for o in origins],
            'destinations': [AIRPORTS[d] for d in destinations],
            'origin_codes': origins,
            'destination_codes': destinations,
            'ida_start': ida_start.strftime('%Y-%m-%d'),
            'ida_end': ida_end.strftime('%Y-%m-%d'),
            'vuelta_start': vuelta_start.strftime('%Y-%m-%d'),
            'vuelta_end': vuelta_end.strftime('%Y-%m-%d'),
            'min_days': min_days,
            'search_id': search_id
        }
        # Add flag_map for template
        flag_map = {
            'TLV': '🇮🇱',
            'NYC': '🇺🇸',
            'LON': '🇬🇧',
            'PAR': '🇫🇷',
            'ROM': '🇮🇹',
            'BER': '🇩🇪',
            'MAD': '🇪🇸',
            'BCN': '🇪🇸',
            'MIL': '🇮🇹',
            'FRA': '🇩🇪',
            'AMS': '🇳🇱',
            'ZUR': '🇨🇭',
            'VIE': '🇦🇹',
            'PRG': '🇨🇿',
            'BUD': '🇭🇺',
            'ATH': '🇬🇷',
            'IST': '🇹🇷',
            'DXB': '🇦🇪',
            'BKK': '🇹🇭',
            'NRT': '🇯🇵',
            'HKG': '🇭🇰',
            'SIN': '🇸🇬',
            'SYD': '🇦🇺',
            'MEL': '🇦🇺',
            'LAX': '🇺🇸',
            'MIA': '🇺🇸',
            'CHI': '🇺🇸',
            'TOR': '🇨🇦'
        }
        # Add country_code_map for template
        country_code_map = {
            'TLV': 'il',
            'NYC': 'us',
            'LON': 'gb',
            'PAR': 'fr',
            'ROM': 'it',
            'BER': 'de',
            'MAD': 'es',
            'BCN': 'es',
            'MIL': 'it',
            'FRA': 'de',
            'AMS': 'nl',
            'ZUR': 'ch',
            'VIE': 'at',
            'PRG': 'cz',
            'BUD': 'hu',
            'ATH': 'gr',
            'IST': 'tr',
            'DXB': 'ae',
            'BKK': 'th',
            'NRT': 'jp',
            'HKG': 'hk',
            'SIN': 'sg',
            'SYD': 'au',
            'MEL': 'au',
            'LAX': 'us',
            'MIA': 'us',
            'CHI': 'us',
            'TOR': 'ca'
        }
        return render_template(
            'results.html',
            flight_options=[],  # No results yet
            search_params=search_params,
            flag_map=flag_map,
            country_code_map=country_code_map,
            AIRPORTS=AIRPORTS,
            zip=zip
        )
    return render_template('form.html', airports=AIRPORTS)


@app.route('/render_results_content', methods=['POST'])
def render_results_content():
    data = request.get_json()
    return render_template(
        'results_content.html',
        flight_options=data['options'],
        search_params=data['search_params'],
        flag_map=data['flag_map'],
        country_code_map=data['country_code_map'],
        AIRPORTS=data['AIRPORTS'],
        zip=zip
    )


@app.route('/get_flight_data_status/<search_id>')
def get_flight_data_status(search_id):
    """API endpoint to check if all flight data is ready and return it if so"""
    with store_lock:
        options = flight_data_store.get(search_id)
        if not options:
            return jsonify({'ready': False, 'data': [], 'error': 'Search ID not found'})
        all_ready = all(not opt.get('loading', True) for opt in options)
        return jsonify({'ready': all_ready, 'data': options})


@app.route('/get_flight_data/<search_id>')
def get_flight_data(search_id):
    """API endpoint to get updated flight data"""
    with store_lock:
        if search_id in flight_data_store:
            print('Returning flight data:', flight_data_store[search_id])
            return jsonify({
                'success': True,
                'data': flight_data_store[search_id]
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Search ID not found'
            })


@app.route('/extract_single', methods=['POST'])
def extract_single():
    """API endpoint to extract data for a single flight option"""
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    extractor = FlightDataExtractor()
    flight_data = extractor.extract_flight_data(url)
    
    return jsonify(flight_data)


@app.route('/cancel_search/<search_id>', methods=['POST'])
def cancel_search(search_id):
    with store_lock:
        # Mark as cancelled (implement thread/session cleanup as needed)
        if search_id in flight_data_store:
            flight_data_store[search_id] = 'cancelled'
    return '', 204


COHERE_API_KEY = "m0Xl6rgeczEonBWV9Oq8z8W3dHPDi4v5O6ecNG2Q"  # Or use os.environ.get("COHERE_API_KEY")

@app.route('/ai_recommendation/<search_id>')
def ai_recommendation(search_id):
    logging.info(f"AI recommendation endpoint hit for search_id: {search_id}")
    with store_lock:
        options = flight_data_store.get(search_id)
        if not options:
            logging.warning(f"Search ID {search_id} not found in flight_data_store.")
            return jsonify({'success': False, 'error': 'Search ID not found'})
        summary = summarize_options_for_ai(options)
        prompt = (
            f"The data below gives only the cheapest price for each type, and for the rest, the price difference from the cheapest.\n{summary}\n"
            "Write a helpful, detailed recommendation for the user, explaining the tradeoffs between direct and connecting flights, price differences, and which option might be best. "
            "If a direct flight is only slightly more expensive, mention the convenience. If a connecting flight is much cheaper, mention the savings. Be clear and user-focused. "
            "Include percentages of savings, difference in prices, and tradeoffs of convenience and price. "
            "Do NOT mention locations of stops, baggage, baggage allowances, average flight duration, or the airlines you'd be traveling with. "
            "Do NOT end your reply with a question. Only provide an insight based on the info above. "
            "Keep your reply under 100 words. Be even more concise."
        )
        headers = {
            "Authorization": f"Bearer {COHERE_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "command",  # or "command-light" for faster/cheaper
            "prompt": prompt,
            "max_tokens": 200,
            "temperature": 0.7
        }
        try:
            logging.info(f"Calling Cohere API with prompt: {prompt}")
            response = requests.post("https://api.cohere.ai/v1/generate", headers=headers, json=data)
            logging.info(f"Cohere API response status: {response.status_code}")
            logging.info(f"Cohere API response body: {response.text}")
            if response.status_code == 200:
                ai_text = response.json()["generations"][0]["text"]
                return jsonify({'success': True, 'recommendation': ai_text})
            else:
                logging.error(f"Cohere API call failed with status: {response.status_code}, response: {response.text}")
                return jsonify({'success': False, 'error': response.text})
        except Exception as e:
            logging.exception(f"Exception during Cohere API call: {e}")
            return jsonify({'success': False, 'error': str(e)})

def summarize_options_for_ai(options):
    # Send only the cheapest price for each type, and for the rest, the difference from the cheapest
    best = {'direct': float('inf'), 'one_stop': float('inf'), 'multi_stop': float('inf')}
    best_opt = {}
    for opt in options:
        if not opt.get('flight_data'):
            continue
        origin_code = opt.get('origin')
        destination_code = opt.get('destination')
        origin_val = AIRPORTS.get(origin_code)
        origin = origin_val.split(' (')[0] if origin_val else origin_code
        destination_val = AIRPORTS.get(destination_code)
        destination = destination_val.split(' (')[0] if destination_val else destination_code
        # Direct
        for flight in opt['flight_data'].get('direct_flights', []):
            price = int(''.join(filter(str.isdigit, str(flight.get('price', '0'))))) if flight.get('price') else float('inf')
            if price < best['direct']:
                best['direct'] = price
                best_opt['direct'] = (origin, destination, price)
        # 1-Stop
        for flight in opt['flight_data'].get('one_stop_flights', []):
            price = int(''.join(filter(str.isdigit, str(flight.get('price', '0'))))) if flight.get('price') else float('inf')
            if price < best['one_stop']:
                best['one_stop'] = price
                best_opt['one_stop'] = (origin, destination, price)
        # 2+ Stops
        for flight in opt['flight_data'].get('multi_stop_flights', []):
            price = int(''.join(filter(str.isdigit, str(flight.get('price', '0'))))) if flight.get('price') else float('inf')
            if price < best['multi_stop']:
                best['multi_stop'] = price
                best_opt['multi_stop'] = (origin, destination, price)
    summary_lines = []
    # Find the absolute cheapest price
    cheapest_type = min((k for k in best if best[k] < float('inf')), key=lambda k: best[k], default=None)
    if cheapest_type:
        cheapest_price = best[cheapest_type]
        cheapest_label = {'direct': 'Direct', 'one_stop': '1-Stop', 'multi_stop': '2+ Stops'}[cheapest_type]
        summary_lines.append(f"Cheapest: {cheapest_label} {best_opt[cheapest_type][0]} → {best_opt[cheapest_type][1]} for ${cheapest_price}")
        for k, label in [('direct', 'Direct'), ('one_stop', '1-Stop'), ('multi_stop', '2+ Stops')]:
            if k != cheapest_type and k in best_opt and best[k] < float('inf'):
                diff = best[k] - cheapest_price
                summary_lines.append(f"{label} is ${diff} more expensive")
    return '\n'.join(summary_lines)


if __name__ == '__main__':
    app.run(debug=True)