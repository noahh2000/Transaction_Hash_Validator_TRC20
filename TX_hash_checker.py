from datetime import datetime
import time
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError
import logging
from functools import wraps

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Define the USDT contract address on TRON network
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# Define the base URL for TRONSCAN API
BASE_URL = "https://apilist.tronscan.org/api"

# Configuration constants
MIN_CONFIRMATIONS = 20
TX_EXPIRY_SECONDS = 600
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 1

def retry_on_exception(max_retries=MAX_RETRIES, delay=RETRY_DELAY):
    """Decorator to retry function on RequestException"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (Timeout, ConnectionError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay} seconds...")
                        time.sleep(delay)
                    else:
                        raise
            return None
        return wrapper
    return decorator

@retry_on_exception(max_retries=MAX_RETRIES, delay=RETRY_DELAY)
def get_tx_info(tx_hash: str, wallet_customer: str, wallet_bugs: str) -> dict:
    """
    Validate TRON TRC20 transaction details.
    
    Args:
        tx_hash (str): Transaction hash to validate
        wallet_customer (str): Expected sender wallet address
        wallet_bugs (str): Expected receiver wallet address
        
    Returns:
        dict: Status and message with optional transaction details
    """
    try:
        # Validate input parameters
        if not tx_hash or not wallet_customer or not wallet_bugs:
            logger.error("Missing required parameters")
            return {'status': False, 'msg': 'Error: Missing required parameters'}
        
        logger.info(f"Validating transaction: {tx_hash}")
        
        # Get the transaction details by hash
        tx_url = f"{BASE_URL}/transaction-info?hash={tx_hash}"
        
        tx_response = requests.get(tx_url, timeout=REQUEST_TIMEOUT)
        
        # Check if the response status code is 200
        if tx_response.status_code != 200:
            logger.error(f"API returned status code: {tx_response.status_code}")
            return {'status': False, 'msg': f"Error: Received non-200 status code: {tx_response.status_code}"}
        
        # Check if the content type is application/json
        content_type = tx_response.headers.get("content-type", "").strip()
        if not content_type.startswith("application/json"):
            logger.error(f"Invalid content-type: {content_type}")
            return {'status': False, 'msg': "Error: Received non-JSON content type"}
        
        tx_data = tx_response.json()
        logger.debug(f"Transaction data received: {tx_data}")
        
        # Validate transaction completion status
        if tx_data.get("contractRet") != "SUCCESS":
            logger.warning(f"Transaction {tx_hash} failed")
            return {'status': False, 'msg': "The transaction didn't complete correctly (FAILED)."}
        
        # Check confirmation count
        confirmations = tx_data.get("confirmations", 0)
        is_confirmed = tx_data.get("confirmed", False)
        
        if confirmations < MIN_CONFIRMATIONS or not is_confirmed:
            logger.warning(f"Transaction {tx_hash} not confirmed enough. Confirmations: {confirmations}")
            return {'status': False, 'msg': f"Warning: The transaction is not confirmed by enough blocks. Current confirmations: {confirmations}/{MIN_CONFIRMATIONS}"}
        
        # Validate sender wallet
        sender = tx_data.get("ownerAddress", "")
        if not sender:
            logger.error("Sender address not found in transaction")
            return {'status': False, 'msg': "Error: Could not extract sender address"}
        
        if sender != wallet_customer:
            logger.warning(f"Sender mismatch. Expected: {wallet_customer}, Got: {sender}")
            return {'status': False, 'msg': f"The sender of the amount is not authorized. Please double-check the TX. Expected: {wallet_customer}"}
        
        # Validate receiver wallet
        trc20_info = tx_data.get("trc20TransferInfo")
        if not trc20_info or not isinstance(trc20_info, list) or len(trc20_info) == 0:
            logger.error("No TRC20 transfer information found")
            return {'status': False, 'msg': "Error: No TRC20 transfer information found in transaction"}
        
        receiver = trc20_info[0].get("to_address", "")
        if not receiver:
            logger.error("Receiver address not found")
            return {'status': False, 'msg': "Error: Could not extract receiver address"}
        
        if receiver != wallet_bugs:
            logger.warning(f"Receiver mismatch. Expected: {wallet_bugs}, Got: {receiver}")
            return {'status': False, 'msg': f"The receiver wallet doesn't belong to us. Please double-check the TX. Expected: {wallet_bugs}"}
        
        # Validate contract type
        contract_type = tx_data.get("contract_type", "").lower()
        if contract_type != "trc20":
            logger.warning(f"Invalid contract type: {contract_type}")
            return {'status': False, 'msg': f"The format of the TX is not trc20. Please double-check the TX. Got: {contract_type}"}
        
        # Check for revert
        if tx_data.get("revert", False) is True:
            logger.warning(f"Transaction {tx_hash} reverted")
            return {'status': False, 'msg': "Revert happened. Probably your money will come back to your wallet."}
        
        # Validate transaction timestamp
        time_tx_ms = tx_data.get("timestamp", 0)
        if not time_tx_ms:
            logger.error("Transaction timestamp not found")
            return {'status': False, 'msg': "Error: Could not extract transaction timestamp"}
        
        timestamp = time_tx_ms / 1000
        now = time.time()
        time_difference = now - timestamp
        
        if time_difference > TX_EXPIRY_SECONDS:
            logger.warning(f"Transaction {tx_hash} submission expired")
            return {'status': False, 'msg': f"Your submission time for the TX hash has expired (submitted {int(time_difference)} seconds ago). However, you can contact our support team for further information and assistance."}
        
        # Extract transfer amount (in sun, need to divide by 10**6 to get USDT)
        try:
            amount_sun = float(tx_data.get("trigger_info", {}).get("parameter", {}).get("_value", 0))
            amount = amount_sun / 10**6
        except (ValueError, TypeError) as e:
            logger.error(f"Could not parse transaction amount: {e}")
            return {'status': False, 'msg': f"Error: Could not extract transaction amount"}
        
        date_time = datetime.fromtimestamp(timestamp)
        
        logger.info(f"Transaction {tx_hash} validated successfully. Amount: {amount} USDT")
        
        return {
            'status': True,
            'msg': "TX was successfully validated",
            'time': date_time,
            'amount': amount,
            'tx_hash': tx_hash,
            'sender': sender,
            'receiver': receiver,
            'confirmations': confirmations
        }
        
    except Timeout:
        logger.error(f"Request timeout for transaction {tx_hash}")
        return {'status': False, 'msg': "Error: Request timeout. The API server is not responding in time."}
    except ConnectionError as e:
        logger.error(f"Connection error for transaction {tx_hash}: {e}")
        return {'status': False, 'msg': "Error: Connection error. Please check your internet connection."}
    except ValueError as e:
        logger.error(f"JSON parsing error for transaction {tx_hash}: {e}")
        return {'status': False, 'msg': "Error: Invalid response format from API"}
    except RequestException as e:
        logger.error(f"Request error for transaction {tx_hash}: {e}")
        return {'status': False, 'msg': f"Error: Request failed: {str(e)[:100]}"}
    except Exception as e:
        logger.error(f"Unexpected error validating transaction {tx_hash}: {e}", exc_info=True)
        return {'status': False, 'msg': f"TX checker has unexpected error: {str(e)[:100]}"}