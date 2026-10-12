from fastapi import APIRouter

router = APIRouter()


# Also served under /api: in production CloudFront only forwards /api/* to the backend and the ALB
# accepts nothing but CloudFront, so this is the path a deploy check can reach.
@router.get("/health")
@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
