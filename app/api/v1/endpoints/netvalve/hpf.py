"""
NetValve HPF (Hosted Payment Fields) Session Routes.

Endpoints:
  POST /api/v1/netvalve/hpf/session — Initialize an HPF session
  GET  /api/v1/netvalve/hpf/session — Same as POST (convenience for testing)

The 5-step initialization waterfall is fully preserved:
  Step 0:   NETVALVE_HPP_DIRECT_URL override
  Step 0.5: NETVALVE_HPF_SCRIPT_SRC static override
  Step 1:   Payment API → HPF initializeSession (primary)
  Step 2:   Backoffice token + HPF script fetch (legacy)
  Step 3:   HPP fallback redirect
  Step 4:   Fallback HPF script
  Step 5:   Diagnostic 502 error
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.schemas.netvalve import (
    HpfSessionRequest,
    HpfSessionResponse,
    HpfSessionErrorResponse,
)
from app.services.netvalve_service import netvalve_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/hpf/session",
    summary="Initialize NetValve HPF session",
    description=(
        "Initializes a Hosted Payment Fields (HPF) session with NetValve. "
        "Follows a 5-step waterfall to obtain an HPF script URL or HPP redirect. "
    ),
    responses={
        200: {
            "description": "HPF session initialized (flow = hpf or hpp)",
            "model": HpfSessionResponse,
        },
        502: {
            "description": "All initialization paths failed",
            "model": HpfSessionErrorResponse,
        },
        500: {
            "description": "Unexpected server error",
            "model": HpfSessionErrorResponse,
        },
    },
    tags=["netvalve", "hpf"],
)
async def create_hpf_session(body: HpfSessionRequest):
    """
    POST /api/v1/netvalve/hpf/session

    Initialize a NetValve HPF session for card payment collection.
    The response indicates which flow to use:
      - flow="hpf": render Hosted Payment Fields using the returned script_src
      - flow="hpp": redirect the customer to the returned redirect_url

    """
    body_dict = body.model_dump(exclude_none=True)

    status_code, response_body = await netvalve_service.create_hpf_session(body_dict)

    return JSONResponse(content=response_body, status_code=status_code)


@router.get(
    "/hpf/session",
    summary="Initialize NetValve HPF session (GET)",
    description=(
        "GET convenience endpoint — delegates to POST by mapping query params. "
    ),
    responses={
        200: {
            "description": "HPF session initialized (flow = hpf or hpp)",
            "model": HpfSessionResponse,
        },
        502: {
            "description": "All initialization paths failed",
            "model": HpfSessionErrorResponse,
        },
    },
    tags=["netvalve", "hpf"],
)
async def get_hpf_session(
    version: Optional[str] = Query(None),
    currency_code: Optional[str] = Query(None),
    amount: Optional[float] = Query(None),
    cart_id: Optional[str] = Query(None),
):
    """
    GET /api/v1/netvalve/hpf/session

    Convenience endpoint that maps query params to a POST body and
    delegates to the POST handler.

    """
    body = HpfSessionRequest(
        version=version,
        currency_code=currency_code,
        amount=amount,
        cart_id=cart_id,
    )
    body_dict = body.model_dump(exclude_none=True)

    status_code, response_body = await netvalve_service.create_hpf_session(body_dict)

    return JSONResponse(content=response_body, status_code=status_code)
