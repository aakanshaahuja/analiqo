"""
Flipkart Bulk Seller Scraper
=============================
Fetches seller details for thousands of Flipkart products using their internal Rome API.

USAGE:
  1. Prepare a CSV file with columns: product_id, listing_id
     (Extract these from Flipkart product URLs - pid & lid parameters)
  2. Run: python flipkart_bulk_sellers.py --input products.csv --output sellers_output.csv

HOW TO GET product_id (pid) and listing_id (lid):
  From any Flipkart product URL like:
  https://www.flipkart.com/.../p/itm...?pid=BLBGCH4TF6QZYXKX&lid=LSTBLBGCH4TF6QZYXKXW20GUX&...
  
  - product_id = pid parameter = BLBGCH4TF6QZYXKX
  - listing_id = lid parameter = LSTBLBGCH4TF6QZYXKXW20GUX
"""

import sys
import requests
import json
import csv
import time
import random
import argparse
import os
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ──────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────
API_URL = "https://1.rome.api.flipkart.com/3/page/dynamic/product-sellers"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.flipkart.com",
    "Referer": "https://www.flipkart.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "X-User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 FKUA/website/42/website/Desktop"
    ),
}

# Rate limiting settings
MIN_DELAY = 1.0      # Minimum seconds between requests
MAX_DELAY = 3.0      # Maximum seconds between requests
MAX_WORKERS = 3       # Max parallel requests (keep low to avoid blocks)
MAX_RETRIES = 3       # Retry count on failure


# ──────────────────────────────────────────────────
# CORE FUNCTIONS
# ──────────────────────────────────────────────────

def build_payload(product_id: str, listing_id: str, pincode: str = "") -> dict:
    """Build the JSON payload for the sellers API."""
    return {
        "requestContext": {
            "productId": product_id,
            "listingId": listing_id
        },
        "locationContext": {
            "pincode": pincode
        }
    }


def extract_pid_lid_from_url(url: str) -> tuple:
    """
    Extract product_id (pid) and listing_id (lid) from a Flipkart product URL.
    
    Example URL:
    https://www.flipkart.com/product-name/p/itm...?pid=BLBGCH4TF6QZYXKX&lid=LSTBLBGCH4TF6QZYXKXW20GUX
    
    Returns: (product_id, listing_id)
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    
    pid = params.get("pid", [None])[0]
    lid = params.get("lid", [None])[0]
    
    return pid, lid


def fetch_sellers(product_id: str, listing_id: str, pincode: str = "", session: requests.Session = None) -> dict:
    """
    Fetch seller details for a single product.
    
    Args:
        product_id: Flipkart product ID (e.g., 'BLBGCH4TF6QZYXKX')
        listing_id: Flipkart listing ID (e.g., 'LSTBLBGCH4TF6QZYXKXW20GUX')
        pincode: Optional pincode for location-specific pricing
        session: Optional requests.Session for connection pooling
    
    Returns:
        dict with raw API response or error info
    """
    payload = build_payload(product_id, listing_id, pincode)
    requester = session or requests
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requester.post(
                API_URL,
                json=payload,
                headers=HEADERS,
                timeout=15
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "product_id": product_id,
                    "listing_id": listing_id,
                    "data": response.json()
                }
            elif response.status_code == 429:
                # Rate limited - back off
                wait_time = (2 ** attempt) * 5
                print(f"  ⚠ Rate limited for {product_id}, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            elif response.status_code == 403:
                # Forbidden - may need different headers or session refresh
                print(f"  ✗ 403 Forbidden for {product_id} (attempt {attempt+1}/{MAX_RETRIES})")
                time.sleep(2 ** attempt)
                continue
            else:
                return {
                    "success": False,
                    "product_id": product_id,
                    "listing_id": listing_id,
                    "error": f"HTTP {response.status_code}: {response.text[:200]}"
                }
                
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Request error for {product_id}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    
    return {
        "success": False,
        "product_id": product_id,
        "listing_id": listing_id,
        "error": "Max retries exceeded"
    }


def parse_sellers_from_response(response_data: dict) -> list:
    """
    Parse seller information from the raw Rome API response.
    
    The structure is:
      RESPONSE.data.product_seller_detail_1.data[] -> each item has:
        value.sellerInfo.value  -> {name, id, rating: {average, base}}
        value.pricing.value     -> {finalPrice: {value}, discountAmount, totalDiscount}
        value.metadata          -> {codAvailable, faAvailable, freeDeliveryAvailable, averageRating}
        value.listingId
        value.deliveryInfo
    
    Returns: list of dicts with seller details
    """
    sellers = []
    
    if not response_data or not isinstance(response_data, dict):
        return sellers
    
    # Navigate to the seller listings
    resp = response_data.get("RESPONSE", {}).get("data", {})
    seller_detail = resp.get("product_seller_detail_1", {})
    
    if not seller_detail:
        # Fallback: try to find any key containing 'seller_detail'
        for key, val in resp.items():
            if "seller_detail" in key and isinstance(val, dict):
                seller_detail = val
                break
    
    data_items = seller_detail.get("data", [])
    if not isinstance(data_items, list):
        return sellers
    
    for item in data_items:
        val = item.get("value", {}) if isinstance(item, dict) else {}
        if not isinstance(val, dict):
            continue
        
        seller = {}
        
        # ── Seller Info ──
        seller_info = val.get("sellerInfo", {})
        si_value = seller_info.get("value", {}) if isinstance(seller_info, dict) else {}
        
        seller["seller_name"] = si_value.get("name", "") if isinstance(si_value, dict) else ""
        seller["seller_id"] = si_value.get("id", "") if isinstance(si_value, dict) else ""
        
        # Rating
        rating = si_value.get("rating", {}) if isinstance(si_value, dict) else {}
        if isinstance(rating, dict):
            seller["seller_rating"] = rating.get("average", "")
        else:
            seller["seller_rating"] = rating or ""
        
        seller["is_new_seller"] = si_value.get("newSeller", "") if isinstance(si_value, dict) else ""
        
        # ── Listing ID ──
        seller["listing_id"] = val.get("listingId", "")
        seller["is_selected"] = val.get("selected", False)
        
        # ── Pricing ──
        pricing = val.get("pricing", {})
        pr_value = pricing.get("value", {}) if isinstance(pricing, dict) else {}
        
        if isinstance(pr_value, dict):
            final_price = pr_value.get("finalPrice", {})
            seller["final_price"] = final_price.get("value", "") if isinstance(final_price, dict) else ""
            seller["discount_amount"] = pr_value.get("discountAmount", "")
            seller["total_discount_pct"] = pr_value.get("totalDiscount", "")
            
            # MRP (finalPrice.value + discountAmount)
            try:
                fp = int(seller["final_price"]) if seller["final_price"] else 0
                da = int(seller["discount_amount"]) if seller["discount_amount"] else 0
                seller["mrp"] = fp + da if fp and da else ""
            except (ValueError, TypeError):
                seller["mrp"] = ""
        else:
            seller["final_price"] = ""
            seller["discount_amount"] = ""
            seller["total_discount_pct"] = ""
            seller["mrp"] = ""
        
        # ── Metadata ──
        meta = val.get("metadata", {})
        if isinstance(meta, dict):
            seller["cod_available"] = meta.get("codAvailable", "")
            seller["flipkart_assured"] = meta.get("faAvailable", "")
            seller["free_delivery"] = meta.get("freeDeliveryAvailable", "")
        
        # ── Delivery Info ──
        delivery = val.get("deliveryInfo", {})
        if isinstance(delivery, dict):
            primary = delivery.get("primaryOption", {})
            if isinstance(primary, dict):
                seller["delivery_text"] = primary.get("text", "")
            else:
                seller["delivery_text"] = ""
        
        # Only add if we have a seller name
        if seller.get("seller_name"):
            sellers.append(seller)
    
    return sellers


def parse_sellers_from_text(text_data: str) -> list:
    """
    Fallback parser: extract seller info from text patterns in the response.
    Useful when the JSON structure doesn't match expected patterns.
    """
    import re
    sellers = []
    
    # Look for seller name patterns
    seller_names = re.findall(r'"sellerName"\s*:\s*"([^"]+)"', text_data)
    seller_ids = re.findall(r'"sellerId"\s*:\s*"([^"]+)"', text_data)
    
    for i, name in enumerate(seller_names):
        seller = {
            "seller_name": name,
            "seller_id": seller_ids[i] if i < len(seller_ids) else "",
        }
        sellers.append(seller)
    
    return sellers


# ──────────────────────────────────────────────────
# BULK PROCESSING
# ──────────────────────────────────────────────────

def load_products_from_csv(filepath: str) -> list:
    """
    Load product IDs from CSV. 
    
    Expected columns (any of these combinations):
      - product_id, listing_id
      - pid, lid  
      - url (full Flipkart product URL - pid/lid will be extracted)
    """
    products = []
    
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in reader.fieldnames]
        
        for row in reader:
            # Normalize keys
            row = {k.strip().lower(): v.strip() for k, v in row.items()}
            
            pid = row.get("product_id") or row.get("pid") or ""
            lid = row.get("listing_id") or row.get("lid") or ""
            url = row.get("url") or row.get("product_url") or ""
            
            # If URL provided, extract pid/lid from it
            if url and (not pid or not lid):
                extracted_pid, extracted_lid = extract_pid_lid_from_url(url)
                pid = pid or extracted_pid or ""
                lid = lid or extracted_lid or ""
            
            if pid and lid:
                products.append({"product_id": pid, "listing_id": lid})
            elif pid:
                print(f"  ⚠ Skipping {pid}: missing listing_id")
            
    return products


def load_products_from_urls(filepath: str) -> list:
    """Load products from a text file with one Flipkart URL per line."""
    products = []
    
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            
            pid, lid = extract_pid_lid_from_url(url)
            if pid and lid:
                products.append({"product_id": pid, "listing_id": lid})
            else:
                print(f"  ⚠ Could not extract pid/lid from: {url[:80]}...")
    
    return products


def process_bulk(products: list, output_file: str, pincode: str = ""):
    """
    Process a list of products and save seller details to CSV.
    
    Args:
        products: list of dicts with 'product_id' and 'listing_id'
        output_file: path to output CSV
        pincode: optional pincode for location-specific results
    """
    all_sellers = []
    success_count = 0
    fail_count = 0
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"\n{'='*60}")
    print(f"  Flipkart Bulk Seller Scraper")
    print(f"  Products to process: {len(products)}")
    print(f"  Output file: {output_file}")
    print(f"{'='*60}\n")
    
    for i, product in enumerate(products, 1):
        pid = product["product_id"]
        lid = product["listing_id"]
        
        print(f"[{i}/{len(products)}] Fetching sellers for {pid}...", end=" ")
        
        result = fetch_sellers(pid, lid, pincode, session)
        
        if result["success"]:
            # Try to parse sellers from the response
            sellers = parse_sellers_from_response(result["data"])
            
            # Fallback: try text-based parsing
            if not sellers:
                raw_text = json.dumps(result["data"])
                sellers = parse_sellers_from_text(raw_text)
            
            if sellers:
                for seller in sellers:
                    seller["product_id"] = pid
                    seller["listing_id_query"] = lid
                all_sellers.extend(sellers)
                print(f"✓ Found {len(sellers)} sellers")
                success_count += 1
            else:
                # Save raw response for debugging
                print(f"✓ Response OK but no sellers parsed (saving raw)")
                all_sellers.append({
                    "product_id": pid,
                    "listing_id_query": lid,
                    "seller_name": "RAW_RESPONSE",
                    "raw_data": json.dumps(result["data"])[:500]
                })
                success_count += 1
        else:
            print(f"✗ Error: {result.get('error', 'unknown')}")
            all_sellers.append({
                "product_id": pid,
                "listing_id_query": lid,
                "seller_name": "ERROR",
                "error": result.get("error", "")
            })
            fail_count += 1
        
        # Rate limiting with random jitter
        if i < len(products):
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            time.sleep(delay)
    
    # Save results
    if all_sellers:
        fieldnames = list(dict.fromkeys(
            key for seller in all_sellers for key in seller.keys()
        ))
        
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_sellers)
    
    # Also save raw JSON responses
    raw_output = output_file.replace(".csv", "_raw.json")
    
    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {fail_count}")
    print(f"  Total sellers found: {len([s for s in all_sellers if s.get('seller_name') not in ['ERROR', 'RAW_RESPONSE']])}")
    print(f"  Output saved to: {output_file}")
    print(f"{'='*60}\n")
    
    return all_sellers


# ──────────────────────────────────────────────────
# URL SCRAPING (to collect pid/lid from search pages)
# ──────────────────────────────────────────────────

def extract_pids_from_search_url(search_url: str) -> list:
    """
    Scrape product IDs from a Flipkart search results page.
    
    Example: https://www.flipkart.com/search?q=bulb&page=1
    
    Returns list of dicts with product_id and listing_id.
    """
    products = []
    
    try:
        response = requests.get(search_url, headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "text/html",
        }, timeout=15)
        
        if response.status_code != 200:
            print(f"  ✗ Failed to fetch search page: HTTP {response.status_code}")
            return products
        
        import re
        
        # Extract pid and lid from product links
        # Pattern: /p/itm...?pid=XXX&lid=YYY
        pattern = r'pid=([A-Z0-9]+).*?lid=(LST[A-Z0-9]+)'
        matches = re.findall(pattern, response.text)
        
        seen = set()
        for pid, lid in matches:
            if pid not in seen:
                seen.add(pid)
                products.append({"product_id": pid, "listing_id": lid})
        
        print(f"  Found {len(products)} unique products on search page")
        
    except Exception as e:
        print(f"  ✗ Error scraping search page: {e}")
    
    return products


def collect_products_from_search(query: str, num_pages: int = 5) -> list:
    """
    Collect product IDs by scraping multiple pages of Flipkart search results.
    
    Args:
        query: Search query (e.g., 'bulb', 'trimmer')
        num_pages: Number of search result pages to scrape
    
    Returns: list of dicts with product_id and listing_id
    """
    all_products = []
    
    print(f"\nCollecting products for query: '{query}'")
    
    for page in range(1, num_pages + 1):
        url = f"https://www.flipkart.com/search?q={query}&page={page}"
        print(f"  Scraping page {page}...", end=" ")
        
        products = extract_pids_from_search_url(url)
        all_products.extend(products)
        
        if not products:
            print(f"  No more results, stopping.")
            break
        
        time.sleep(random.uniform(2, 4))
    
    # Deduplicate
    seen = set()
    unique = []
    for p in all_products:
        if p["product_id"] not in seen:
            seen.add(p["product_id"])
            unique.append(p)
    
    print(f"\nTotal unique products collected: {len(unique)}")
    return unique


# ──────────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Flipkart Bulk Seller Details Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  # Fetch sellers for products listed in a CSV file
  python flipkart_bulk_sellers.py --input products.csv --output sellers.csv
  
  # Fetch sellers from a text file of product URLs  
  python flipkart_bulk_sellers.py --input urls.txt --output sellers.csv --url-file
  
  # Search for products and fetch their sellers
  python flipkart_bulk_sellers.py --search "LED bulb" --pages 10 --output sellers.csv
  
  # Quick test with specific product
  python flipkart_bulk_sellers.py --pid BLBGCH4TF6QZYXKX --lid LSTBLBGCH4TF6QZYXKXW20GUX
  
  # With pincode for location-specific pricing
  python flipkart_bulk_sellers.py --input products.csv --output sellers.csv --pincode 110001
        """
    )
    
    parser.add_argument("--input", "-i", help="Input CSV file with product_id, listing_id columns")
    parser.add_argument("--output", "-o", default="sellers_output.csv", help="Output CSV file path")
    parser.add_argument("--url-file", action="store_true", help="Treat input file as list of URLs (one per line)")
    parser.add_argument("--search", "-s", help="Search query to collect products from Flipkart")
    parser.add_argument("--pages", type=int, default=5, help="Number of search pages to scrape (with --search)")
    parser.add_argument("--pid", help="Single product ID to test")
    parser.add_argument("--lid", help="Single listing ID to test (use with --pid)")
    parser.add_argument("--pincode", default="", help="Pincode for location-specific results")
    parser.add_argument("--delay-min", type=float, default=MIN_DELAY, help="Min delay between requests (seconds)")
    parser.add_argument("--delay-max", type=float, default=MAX_DELAY, help="Max delay between requests (seconds)")
    
    args = parser.parse_args()
    
    # Update delay settings
    delay_min = args.delay_min
    delay_max = args.delay_max
    
    products = []
    
    # Mode 1: Single product test
    if args.pid:
        lid = args.lid or ""
        if not lid:
            # Try to construct listing ID (common pattern: LST + pid + suffix)
            print("⚠ No listing_id provided. You must provide --lid for accurate results.")
            return
        
        print(f"\nFetching sellers for single product: {args.pid}")
        result = fetch_sellers(args.pid, lid, args.pincode)
        
        print(f"\nRaw response (first 2000 chars):")
        print(json.dumps(result, indent=2)[:2000])
        
        if result["success"]:
            sellers = parse_sellers_from_response(result["data"])
            if sellers:
                print(f"\n\nParsed {len(sellers)} sellers:")
                for s in sellers:
                    print(f"  - {s.get('seller_name', 'N/A')} | "
                          f"Price: {s.get('final_price', 'N/A')} | "
                          f"MRP: {s.get('mrp', 'N/A')} | "
                          f"Discount: {s.get('total_discount_pct', 'N/A')}% | "
                          f"Rating: {s.get('seller_rating', 'N/A')} | "
                          f"COD: {s.get('cod_available', 'N/A')}")
            else:
                print("\nCould not parse sellers from response. Saving raw JSON...")
                with open("raw_response.json", "w") as f:
                    json.dump(result["data"], f, indent=2)
                print("Saved to raw_response.json - inspect this to understand the structure.")
        return
    
    # Mode 2: Search and scrape
    if args.search:
        products = collect_products_from_search(args.search, args.pages)
        
        # Save collected products
        products_file = args.output.replace(".csv", "_products.csv")
        with open(products_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["product_id", "listing_id"])
            writer.writeheader()
            writer.writerows(products)
        print(f"Saved product list to: {products_file}")
    
    # Mode 3: Load from file
    elif args.input:
        if args.url_file:
            products = load_products_from_urls(args.input)
        else:
            products = load_products_from_csv(args.input)
    
    if not products:
        print("No products to process. Use --input, --search, or --pid to specify products.")
        parser.print_help()
        return
    
    # Process all products
    process_bulk(products, args.output, args.pincode)


if __name__ == "__main__":
    main()
