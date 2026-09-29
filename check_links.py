import csv
import urllib.request
import urllib.error
import socket
import concurrent.futures
import time

# Configuration
TIMEOUT_SECONDS = 6
MAX_WORKERS = 15  # Number of concurrent threads to speed up the scan
CSV_FILENAME = 'Ski resorts - Master.csv'

def check_link(row_data):
    """Worker function to test a single resort link concurrently."""
    resort_name, url = row_data
    
    if not url or url.strip() == '' or url.lower() == 'n/a':
        return (resort_name, url, 'NO_URL', 'No website provided')

    # Ensure URL has a scheme
    target_url = url.strip()
    if not target_url.startswith('http://') and not target_url.startswith('https://'):
        target_url = 'https://' + target_url

    try:
        req = urllib.request.Request(
            target_url,
            headers={'User-Agent': 'EuropeSkiValidatorBot/2.0 (Compatible; +https://europa-ski.com)'}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return (resort_name, target_url, 'SUCCESS', response.status)
    except urllib.error.HTTPError as e:
        return (resort_name, target_url, 'HTTP_ERROR', e.code)
    except urllib.error.URLError as e:
        return (resort_name, target_url, 'URL_ERROR', str(e.reason))
    except socket.timeout:
        return (resort_name, target_url, 'TIMEOUT', f'Exceeded {TIMEOUT_SECONDS}s')
    except Exception as e:
        return (resort_name, target_url, 'UNKNOWN_ERROR', str(e))

def main():
    print(f"Loading '{CSV_FILENAME}'...")
    
    try:
        with open(CSV_FILENAME, 'r', encoding='utf-8') as file:
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
        print(f"[x] Error: Could not find '{CSV_FILENAME}'. Please ensure it is in your current directory.")
        return

    print(f"Found {len(resorts_to_check)} resorts to audit. Launching {MAX_WORKERS} concurrent threads...")
    start_time = time.time()

    success_count = 0
    broken_links = []
    no_website_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_link, item): item for item in resorts_to_check}
        
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            resort_name, url, status_type, details = future.result()
            
            if status_type == 'SUCCESS':
                success_count += 1
            elif status_type == 'NO_URL':
                no_website_count += 1
            else:
                broken_links.append((resort_name, url, status_type, details))
                print(f"[!] BROKEN/ERROR ({status_type}) - {resort_name} ({url}): {details}")

            # Print periodic progress
            if completed % 50 == 0 or completed == len(resorts_to_check):
                print(f"Progress: {completed}/{len(resorts_to_check)} checked...")

    elapsed = time.time() - start_time

    print("\n" + "="*50)
    print(" AUDIT COMPLETE")
    print("="*50)
    print(f"Total time taken: {elapsed:.2f} seconds")
    print(f"Valid active links: {success_count}")
    print(f"Resorts without websites: {no_website_count}")
    print(f"Flagged / Broken links: {len(broken_links)}")

    if broken_links:
        report_filename = 'broken_links_report.csv'
        print(f"\nSaving broken links report to '{report_filename}'...")
        with open(report_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Resort Name', 'URL', 'Error Type', 'Details'])
            writer.writerows(broken_links)
        print("Report saved successfully. You can now review or fix these URLs in your master dataset.")
    else:
        print("\nOutstanding! Zero broken links detected across the entire master dataset.")

if __name__ == '__main__':
    main()