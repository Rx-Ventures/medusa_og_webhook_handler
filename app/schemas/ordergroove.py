from pydantic import BaseModel, Field
from enum import Enum


class CreditCardType(str, Enum):
    VISA = "1"
    MASTERCARD = "2"
    AMEX = "3"
    DISCOVER = "4"


class EveryPeriod(int, Enum):
    DAYS = 1
    WEEKS = 2
    MONTHS = 3
    YEARS = 4


class ShippingAddress(BaseModel):
    first_name: str
    last_name: str
    address: str
    address2: str | None = None
    city: str
    state_province_code: str
    zip_postal_code: str
    country_code: str = Field(max_length=2)
    phone: str
    company_name: str | None = None
    fax: str | None = None


class BillingAddress(BaseModel):
    first_name: str
    last_name: str
    address: str
    address2: str | None = None
    city: str
    state_province_code: str
    zip_postal_code: str
    country_code: str = Field(max_length=2)
    phone: str
    company_name: str | None = None
    fax: str | None = None


class User(BaseModel):
    user_id: str
    first_name: str
    last_name: str
    email: str
    phone_number: str | None = None
    shipping_address: ShippingAddress
    billing_address: BillingAddress | None = None


class Payment(BaseModel):
    token_id: str | None = None
    cc_number: str | None = None
    cc_holder: str | None = None
    cc_exp_date: str | None = None
    cc_type: CreditCardType | None = None
    payment_method: str | None = "credit card"


class TrackingOverride(BaseModel):
    every: int
    every_period: EveryPeriod
    offer: str | None = None
    product: str | None = None


class SubscriptionInfo(BaseModel):
    quantity: int = 1
    price: str | None = None
    first_order_place_date: str | None = None
    tracking_override: TrackingOverride | None = None
    subscription_type: str | None = None
    prepaid_orders_per_billing: int | None = None
    renewal_behavior: str | None = None


class PurchaseInfo(BaseModel):
    quantity: int
    price: str
    discounted_price: str | None = None
    total: str


class Product(BaseModel):
    product: str
    sku: str
    subscription_info: SubscriptionInfo | None = None
    purchase_info: PurchaseInfo | None = None


class PurchasePostRequest(BaseModel):
    merchant_id: str
    merchant_order_id: str
    session_id: str | None = None
    og_cart_tracking: bool = False
    user: User
    payment: Payment | None = None
    products: list[Product]


class PurchasePostResponse(BaseModel):
    result: str | None = None
    subs_req_id: str | None = None
    error: str | None = None
    error_message: str | None = None


class TestScenario(str, Enum):
    SUBSCRIPTION_ONLY = "subscription_only"
    ONE_TIME_ONLY = "one_time_only"
    MIXED_CART = "mixed_cart"

#### test 


class TestRequest(BaseModel):
    user_id: str
    merchant_order_id: str
