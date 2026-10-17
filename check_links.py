import csv
import urllib.request
import urllib.error
import socket
import concurrent.futures
import time
import os
import ssl

# Configuration
TIMEOUT_SECONDS = 8
MAX_WORKERS = 10  # Reduced concurrency slightly to avoid triggering rate limits (429)
LOCAL_CSV_FILENAME = 'Ski resorts - Master.csv'

# Google Sheets Direct CSV Export Link
GOOGLE_SHEETS_CSV_URL = 'https://docs.google.com/spreadsheets/d/14cKe2GisNZfv6sWbkVAZFXGm1WLroSazYh3XwwqK2IY/export?format=csv&gid=1311212911'

def create_lenient_ssl_context():
    """Creates a relaxed SSL context to bypass 'DH_KEY_TOO_SMALL' and self-signed certificate errors on legacy resort servers."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Lower cipher security level so legacy DH keys work
    try:
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
    except Exception:
        pass
    return ctx

SSL_CONTEXT = create_lenient_ssl_context()

def download_master_csv():
    """Downloads the latest CSV export directly from Google Sheets."""
    print("Fetching latest master CSV directly from Google Drive...")
    try:
        req = urllib.request.Request(
            GOOGLE_SHEETS_CSV_URL,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as response:
            content = response.read().decode('utf-8')
            with open(LOCAL_CSV_FILENAME, 'w', encoding='utf-8') as f:
                f.write(content)
        print(f"Successfully downloaded latest dataset to '{LOCAL_CSV_FILENAME}'.")
        return True
    except Exception as e:
        print(f"[x] Failed to download CSV from Google Drive: {e}")
        return False

def check_link(row_data):
    """Worker function to test a single resort link concurrently with redirect and SSL handling."""
    resort_name, url = row_data
    
    if not url or url.strip() == '' or url.lower() in ['n/a', 'https://closed', 'http://closed', 'closed']:
        return (resort_name, url, 'CLOSED_OR_NO_URL', 'Resort marked closed or no URL provided')

    target_url = url.strip()
    if not target_url.startswith('http://') and not target_url.startswith('https://'):
        target_url = 'https://' + target_url

    # Standard browser user-agent string to minimize 403/420/429 bot blocks
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,de;q=0.8,fr;q=0.7'
    }

    try:
        req = urllib.request.Request(target_url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=SSL_CONTEXT) as response:
            return (resort_name, target_url, 'SUCCESS', response.status)

    except urllib.error.HTTPError as e:
        # Treat 308 (Permanent Redirect) or 301/302 as success since the destination exists
        if e.code in [301, 302, 307, 308]:
            return (resort_name, target_url, 'SUCCESS', f"{e.code} Redirect")
        # Treat Cloudflare/Bot Guard responses (403, 420, 429) as warnings rather than outright dead links
        elif e.code in [403, 420, 429]:
            return (resort_name, target_url, 'BOT_BLOCKED', f"HTTP {e.code} - Server blocking automated bot request")
        else:
            return (resort_name, target_url, 'HTTP_ERROR', e.code)

    except urllib.error.URLError as e:
        reason_str = str(e.reason)
        # Check if the SSL context fallback was hit
        if 'DH_KEY_TOO_SMALL' in reason_str or 'CERTIFICATE_VERIFY_FAILED' in reason_str:
            return (resort_name, target_url, 'SSL_WARNING', reason_str)
        return (resort_name, target_url, 'URL_ERROR', reason_str)

    except socket.timeout:
        return (resort_name, target_url, 'TIMEOUT', f'Exceeded {TIMEOUT_SECONDS}s')

    except Exception as e:
        return (resort_name, target_url, 'UNKNOWN_ERROR', str(e))

def main():
    downloaded = download_master_csv()
    if not downloaded and not os.path.exists(LOCAL_CSV_FILENAME):
        print(f"[x] Error: Master CSV is unavailable locally and could not be fetched from Google Drive.")
        return

    try:
        with open(LOCAL_CSV_FILENAME, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            headers = next(reader)
            
            try:
                website_idx = headers.index('Website')
                name_idx = headers.index('Resort Name')
            except ValueError:
                print("[x] Error: Could not find 'Website' or 'Resort Name' columns in CSV headers.")
                return

            resorts_to_check = []
            for row in reader:
                if len(row) > max(website_idx, name_idx):
                    resorts_to_check.append((row[name_idx], row[website_idx]))

    except FileNotFoundError:
        print(f"[x] Error: Could not find '{LOCAL_CSV_FILENAME}'.")
        return

    print(f"Found {len(resorts_to_check)} resorts to audit. Launching {MAX_WORKERS} concurrent threads...")
    start_time = time.time()

    success_count = 0
    broken_links = []
    bot_blocked_count = 0
    closed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_link, item): item for item in resorts_to_check}
        
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            resort_name, url, status_type, details = future.result()
            
            if status_type == 'SUCCESS':
                success_count += 1
            elif status_type == 'CLOSED_OR_NO_URL':
                closed_count += 1
            elif status_type == 'BOT_BLOCKED':
                bot_blocked_count += 1
                print(f"[~] BOT PROTECTED ({details}) - {resort_name} ({url})")
            else:
                broken_links.append((resort_name, url, status_type, details))
                print(f"[!] BROKEN/ERROR ({status_type}) - {resort_name} ({url}): {details}")

            if completed % 50 == 0 or completed == len(resorts_to_check):
                print(f"Progress: {completed}/{len(resorts_to_check)} checked...")

    elapsed = time.time() - start_time

    print("\n" + "="*50)
    print(" AUDIT COMPLETE")
    print("="*50)
    print(f"Total time taken: {elapsed:.2f} seconds")
    print(f"Valid active links: {success_count}")
    print(f"Bot protected / Verified active: {bot_blocked_count}")
    print(f"Closed / No URL: {closed_count}")
    print(f"Flagged / Truly Broken links: {len(broken_links)}")

    if broken_links:
        report_filename = 'broken_links_report.csv'
        print(f"\nSaving broken links report to '{report_filename}'...")
        with open(report_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Resort Name', 'URL', 'Error Type', 'Details'])
            writer.writerows(broken_links)
        print("Report saved successfully.")
    else:
        print("\nOutstanding! Zero broken links detected across the entire master dataset.")

if __name__ == '__main__':
    main()
