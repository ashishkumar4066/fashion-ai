"""
Model generation router.

POST /api/v1/generate-model
    Generate a photorealistic human model image from a text prompt.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.exceptions import APIError, TaskTimeoutError
from services.model_generator import ModelGenerator, VALID_ASPECT_RATIOS

router = APIRouter()
_generator = ModelGenerator()


class GenerateModelRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Description of the model to generate.",
        examples=["young Indian male, casual standing pose, plain white background"],
    )
    aspect_ratio: str = Field(
        default="2:3",
        description="Image aspect ratio. '2:3' (portrait) recommended for full-body fashion.",
        examples=["2:3", "3:4", "1:1"],
    )


class GenerateModelResponse(BaseModel):
    file_path: str = Field(description="Local path where the image was saved.")
    image_url: str = Field(description="Original image URL from PiAPI.")
    message: str = Field(default="Model generated successfully.")


@router.post(
    "/generate-model",
    response_model=GenerateModelResponse,
    summary="Generate a human model image from a text prompt",
    description=(
        "Uses Gemini 2.5 Flash (via PiAPI) to generate a photorealistic fashion model image. "
        "The image is saved locally to data/model/. "
        "Use the returned file_path as the person_image input for the virtual try-on pipeline."
    ),
)
async def generate_model(request: GenerateModelRequest) -> GenerateModelResponse:
    if request.aspect_ratio not in VALID_ASPECT_RATIOS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid aspect_ratio '{request.aspect_ratio}'. "
                   f"Supported values: {', '.join(sorted(VALID_ASPECT_RATIOS))}",
        )

    try:
        file_path, image_url = await _generator.generate(
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
        )
    except TaskTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Model generation timed out after {exc.elapsed_seconds:.0f}s. Please try again.",
        ) from exc
    except APIError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return GenerateModelResponse(file_path=file_path, image_url=image_url)
