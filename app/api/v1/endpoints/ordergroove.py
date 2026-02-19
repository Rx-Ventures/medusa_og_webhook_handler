import uuid
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from app.core.config import settings
from app.services.ordergroove_service import ordergroove_service
from app.schemas.ordergroove import (
    PurchasePostRequest,
    PurchasePostResponse,
    TestScenario,
    User,
    ShippingAddress,
    BillingAddress,
    Payment,
    Product,
    PurchaseInfo,
    SubscriptionInfo,
    TrackingOverride,
    EveryPeriod,
    CreditCardType,
    TestRequest
)

router = APIRouter()

@router.post('/axetest')
async def purchase_post(
    request: Request
):
    return {"message":"hehe"}

#put in conftest later
def generate_test_user() -> User:
    return User(
        user_id=f"cus_{uuid.uuid4()}",
        first_name="axe",
        last_name="faller",
        email="test@example.com",
        phone_number="555-123-4567",
        shipping_address=ShippingAddress(
            first_name="axe",
            last_name="faller",
            address="123 Main Street",
            address2="Apt 4B",
            city="philipiines",
            state_province_code="zc",
            zip_postal_code="1632",
            country_code="ph",
            phone="9919391",
        ),
        billing_address=BillingAddress(
            first_name="axe",
            last_name="faller",
            address="123 Main Street",
            address2="Apt 4B",
            city="philipiines",
            state_province_code="zc",
            zip_postal_code="1632",
            country_code="ph",
            phone="9919391",
        ),
    )

#put in conftest later
def generate_test_payment() -> Payment:
    return Payment(
        cc_number="4242424242424242",
        cc_holder="test user",
        cc_exp_date="12/2029",
        cc_type=CreditCardType.VISA
    )

@router.post("/test", response_model=PurchasePostResponse)
async def test_purchase_post(
    request: TestRequest
):
    user_id = request.user_id
    merchant_order_id = request.merchant_order_id
    print('starting the test from test_purchase_post!!')

    print(f"user_id: {user_id}")
    print(f"merchant_order_id: {merchant_order_id}")

    print("-" * 20)
    print("-" * 20)

    if not merchant_order_id:
        merchant_order_id = f"test_order_{uuid.uuid4().hex[:12]}" #?? what is this 

    user = generate_test_user()
    if user_id:
        user.user_id = user_id

    products = []
    payment = None 

    '''
    subscription test 
    for payload it consist of 

    payload = {
    somekeys: ..
    user: {..}

    }
    payment: {...}
    products: {...}

    '''
    #subs!

    product_1016 = Product(
        product="variant_01KGH4WP1QP52PDDETE1E7XRXB",
        sku="1016-1",
        subscription_info=SubscriptionInfo(
            quantity=1,
            tracking_override=TrackingOverride(
                every=1,
                every_period=EveryPeriod.MONTHS,
            ),
        ),
        purchase_info=PurchaseInfo(
            quantity=1,
            price="100.00",
            discounted_price="100.00",
            total="100.00",
        ),
    )

    payment = generate_test_payment()
    products.append(product_1016)

    request = PurchasePostRequest(
        merchant_id=settings.ORDERGROOVE_MERCHANT_ID,
        merchant_order_id=merchant_order_id,
        session_id=f"session_{uuid.uuid4().hex[:8]}",
        og_cart_tracking=False,
        user=user,
        payment=payment,
        products=products
    )

    return await ordergroove_service.send_purchase_post(request)

    # payload = request.model_dump(exclude_none=True)
    # print(f"payload: {payload}")
    # print(json.dumps(payload, indent=3, default=str))
    # return request
