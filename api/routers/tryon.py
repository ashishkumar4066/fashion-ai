"""
Virtual try-on router.

POST /api/v1/try-on
    Submit a person + garment image URL pair to the Kling AI virtual try-on pipeline.
    Returns the result image URL. Typical latency: 30–120 seconds.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.exceptions import APIError, TaskTimeoutError
from services.tryon_service import TryonService, VALID_GARMENT_TYPES

router = APIRouter()
_service = TryonService()


class TryonRequest(BaseModel):
    user_id: int = Field(
        ...,
        description="Caller's user ID.",
        examples=[123456789],
    )
    person_image_url: str = Field(
        ...,
        description="Publicly accessible URL of the person/model image.",
        examples=["https://example.com/person.jpg"],
    )
    garment_image_url: str = Field(
        ...,
        description="Publicly accessible URL of the garment image.",
        examples=["https://example.com/shirt.jpg"],
    )
    garment_type: str = Field(
        default="upper",
        description="Part of the body the garment covers: 'upper', 'lower', or 'overall'.",
        examples=["upper"],
    )


class TryonResponse(BaseModel):
    result_url: str = Field(description="Result image URL from PiAPI.")


@router.post(
    "/try-on",
    response_model=TryonResponse,
    summary="Virtual try-on",
    description=(
        "Submits a person + garment image pair to Kling AI virtual try-on (via PiAPI). "
        "Polls until the task completes and returns the result image URL. "
        "Typical latency: 30–120 seconds."
    ),
)
async def try_on(request: TryonRequest) -> TryonResponse:
    if request.garment_type not in VALID_GARMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid garment_type '{request.garment_type}'. "
                f"Supported values: {', '.join(sorted(VALID_GARMENT_TYPES))}"
            ),
        )

    try:
        result_url = await _service.run(
            user_id=request.user_id,
            person_image_url=request.person_image_url,
            garment_image_url=request.garment_image_url,
            garment_type=request.garment_type,
        )
    except TaskTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Try-on timed out after {exc.elapsed_seconds:.0f}s. Please try again.",
        ) from exc
    except APIError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return TryonResponse(result_url=result_url)
