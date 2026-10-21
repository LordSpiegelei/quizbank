from fastapi import FastAPI

app = FastAPI(title="quizbank")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
