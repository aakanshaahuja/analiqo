import logging
import requests
import time
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

class FlipkartScraper:
    API_URL = "https://1.rome.api.flipkart.com/3/page/dynamic/product-sellers"
    HEADERS = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://www.flipkart.com",
        "Referer": "https://www.flipkart.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "X-User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 FKUA/website/42/website/Desktop",
    }
    MAX_RETRIES = 3

    @staticmethod
    def extract_pid_lid(url: str) -> tuple:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        pid = params.get("pid", [None])[0]
        lid = params.get("lid", [None])[0]
        return pid, lid

    @classmethod
    def fetch_sellers(cls, pid: str, lid: str, pincode: str = "") -> list:
        payload = {
            "requestContext": {
                "productId": pid,
                "listingId": lid
            },
            "locationContext": {
                "pincode": pincode
            }
        }
        
        session = requests.Session()
        session.headers.update(cls.HEADERS)
        
        for attempt in range(cls.MAX_RETRIES):
            try:
                response = session.post(cls.API_URL, json=payload, timeout=15)
                
                if response.status_code == 200:
                    return cls.parse_sellers_from_response(response.json())
                elif response.status_code in (403, 429):
                    time.sleep((2 ** attempt) * 2)
                    continue
                else:
                    logger.error(f"API Error {response.status_code} for PID {pid}")
                    return []
            except requests.exceptions.RequestException as e:
                logger.error(f"Request Error for PID {pid}: {e}")
                time.sleep(2 ** attempt)
        
        return []

    @classmethod
    def parse_sellers_from_response(cls, response_data: dict) -> list:
        sellers = []
        if not response_data or not isinstance(response_data, dict):
            return sellers
        
        resp = response_data.get("RESPONSE", {}).get("data", {})
        seller_detail = resp.get("product_seller_detail_1", {})
        
        if not seller_detail:
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
            seller_info = val.get("sellerInfo", {})
            si_value = seller_info.get("value", {}) if isinstance(seller_info, dict) else {}
            
            seller["seller_name"] = si_value.get("name", "") if isinstance(si_value, dict) else ""
            seller["seller_id"] = si_value.get("id", "") if isinstance(si_value, dict) else ""
            
            rating = si_value.get("rating", {}) if isinstance(si_value, dict) else {}
            if isinstance(rating, dict):
                seller["seller_rating"] = rating.get("average", None)
            else:
                seller["seller_rating"] = rating or None
            
            seller["is_selected"] = val.get("selected", False)
            
            pricing = val.get("pricing", {})
            pr_value = pricing.get("value", {}) if isinstance(pricing, dict) else {}
            
            if isinstance(pr_value, dict):
                final_price = pr_value.get("finalPrice", {})
                seller["final_price"] = final_price.get("value", None) if isinstance(final_price, dict) else None
                
                discount_amount = pr_value.get("discountAmount", None)
                try:
                    fp = int(seller["final_price"]) if seller["final_price"] else 0
                    da = int(discount_amount) if discount_amount else 0
                    seller["mrp"] = fp + da if fp and da else None
                except (ValueError, TypeError):
                    seller["mrp"] = None
            else:
                seller["final_price"] = None
                seller["mrp"] = None
            
            if seller.get("seller_name") and seller.get("final_price") is not None:
                sellers.append(seller)
        
        return sellers
