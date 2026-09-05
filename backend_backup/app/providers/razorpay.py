import os
import requests


RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayProviderError(Exception):
    pass


class RazorpayProvider:

    def __init__(self):

        # Read credentials when the provider is created
        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")

        if not key_id or not key_secret:
            raise RazorpayProviderError(
                "Razorpay API credentials are not configured"
            )

        self.auth = (
            key_id,
            key_secret
        )

    # -----------------------------------------
    # Fetch payment from Razorpay
    # -----------------------------------------

    def fetch_payment(
        self,
        payment_id: str
    ):

        url = (
            f"{RAZORPAY_BASE_URL}"
            f"/payments/{payment_id}"
        )

        try:
            response = requests.get(
                url,
                auth=self.auth,
                timeout=10
            )

        except requests.RequestException as error:
            raise RazorpayProviderError(
                f"Razorpay API request failed: {error}"
            )

        if response.status_code != 200:
            raise RazorpayProviderError(
                f"Razorpay API returned "
                f"{response.status_code}: "
                f"{response.text}"
            )

        data = response.json()

        razorpay_status = data.get("status")

        status_mapping = {
            "created": "PENDING",
            "authorized": "AUTHORIZED",
            "captured": "SUCCESS",
            "failed": "FAILED",
            "refunded": "REFUNDED"
        }

        internal_status = status_mapping.get(
            razorpay_status,
            "UNKNOWN"
        )

        return {
            "payment_id": data.get("id"),
            "order_id": data.get("order_id"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "provider_status": razorpay_status,
            "internal_status": internal_status,
            "captured": data.get("captured"),
            "raw": data
        }

    # -----------------------------------------
    # Create Razorpay order
    # -----------------------------------------

    def create_order(
        self,
        amount: int,
        currency: str = "INR"
    ):

        url = f"{RAZORPAY_BASE_URL}/orders"

        payload = {
            "amount": amount,
            "currency": currency,
            "receipt": f"recovery_test_{amount}"
        }

        try:
            response = requests.post(
                url,
                json=payload,
                auth=self.auth,
                timeout=10
            )

        except requests.RequestException as error:
            raise RazorpayProviderError(
                f"Razorpay API request failed: {error}"
            )

        if response.status_code not in [200, 201]:
            raise RazorpayProviderError(
                f"Razorpay API returned "
                f"{response.status_code}: "
                f"{response.text}"
            )

        data = response.json()

        return {
            "order_id": data.get("id"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "status": data.get("status"),
            "receipt": data.get("receipt"),
            "raw": data
        }