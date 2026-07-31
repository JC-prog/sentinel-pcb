from fastapi import FastAPI

app = FastAPI(title="SentinelPCB")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
