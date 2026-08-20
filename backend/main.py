from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.agent import answer_question

load_dotenv()


app = FastAPI(title="Agentic SQL Dashboard (Foundry Agent Service)")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query")
def query(request: QueryRequest) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # SQL failures stay inside the agent run, because Foundry can retry them.
    # Provider or configuration failures have no SQL to repair, so FastAPI reports
    # them as service-level errors at the API boundary. The response contract is
    # unchanged from the LangGraph version, so the frontend needs no edits at all.
    try:
        return answer_question(question)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="The query could not be completed. Please try again.",
        ) from exc
