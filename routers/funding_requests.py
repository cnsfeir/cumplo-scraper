# pylint: disable=no-member

import json
from http import HTTPStatus
from logging import getLogger
from typing import cast

from cumplo_common.database.firestore import firestore_client
from cumplo_common.integrations.cloud_tasks import create_http_task
from cumplo_common.models.user import User
from fastapi import APIRouter
from fastapi.requests import Request

from integrations import cumplo
from schemas.funding_requests import FilterFundingRequestPayload
from utils.constants import CUMPLO_HERALD_QUEUE, CUMPLO_HERALD_URL, CUMPLO_SPOTTER_QUEUE, CUMPLO_SPOTTER_URL

logger = getLogger(__name__)

router = APIRouter(prefix="/funding-requests")
internal = APIRouter(prefix="/funding-requests")


@router.get("", status_code=HTTPStatus.OK)
async def get_funding_requests(_request: Request) -> list[dict]:
    """
    Gets a list of available funding requests.
    """
    funding_requests = await cumplo.get_available_funding_requests()
    return [funding_request.serialize() for funding_request in funding_requests]


@router.get("/promising", status_code=HTTPStatus.OK)
async def get_promising_funding_requests(request: Request) -> list[dict]:
    """
    Gets a list of promising funding requests based on the user's configuration.
    """
    funding_requests = await cumplo.get_available_funding_requests()
    user = cast(User, request.state.user)

    promising_funding_requests = set()
    for configuration in user.configurations.values():
        promising_funding_requests.update(cumplo.filter_funding_requests(funding_requests, configuration))

    return [funding_request.serialize() for funding_request in promising_funding_requests]


@internal.post(path="/fetch", status_code=HTTPStatus.NO_CONTENT)
async def fetch_funding_requests(_request: Request) -> None:
    """
    Fetches a list of funding requests and schedules the filtering process.
    """
    funding_requests = await cumplo.get_available_funding_requests()

    for user in firestore_client.get_users():
        if not user.webhook_url:
            logger.info(f"User {user.id} does not have a webhook URL configured. Skipping")
            continue

        payload = FilterFundingRequestPayload(id_user=user.id, funding_requests=funding_requests)
        create_http_task(
            url=f"{CUMPLO_SPOTTER_URL}/funding-requests/filter",
            task_id=f"filter-funding-requests-{user.id}",
            queue=CUMPLO_SPOTTER_QUEUE,
            payload=json.loads(payload.model_dump_json()),
        )


@internal.post(path="/filter", status_code=HTTPStatus.NO_CONTENT)
async def filter_funding_requests(_request: Request, payload: FilterFundingRequestPayload) -> None:
    """
    Filters a list of funding requests based on the user's configuration.
    """
    user = firestore_client.get_user(payload.id_user)

    promising_funding_requests = set()
    for configuration in user.configurations.values():
        promising_funding_requests.update(cumplo.filter_funding_requests(payload.funding_requests, configuration))

    if not promising_funding_requests:
        logger.info(f"No promising funding requests for user {user.id}")
        return

    logger.info(f"Found {len(promising_funding_requests)} promising funding requests for user {user.id}")
    body = {funding_request.id: funding_request.serialize() for funding_request in promising_funding_requests}

    assert user.webhook_url, f"User {user.id} does not have a webhook URL configured"

    logger.info(f"Schedulling funding requests webhook to user {user.id}")
    create_http_task(
        url=f"{CUMPLO_HERALD_URL}/funding-requests/webhook/send",
        task_id=f"send-funding-requests-webhook-{user.id}",
        queue=CUMPLO_HERALD_QUEUE,
        payload=body,
    )
