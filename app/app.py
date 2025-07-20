# main.py

import threading
from fastapi import FastAPI, HTTPException
from src.models import ResumeRequest
from src.services.resume import ResumeReviewService
from src.services.prompt import PromptBuilder
from src.services.discord import DiscordBotClient
from contextlib import asynccontextmanager


# Launch Discord bot in a background thread using lifespan event handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    discord_bot = DiscordBotClient()
    discord_thread = threading.Thread(target=discord_bot.run)
    discord_thread.start()
    yield


# Initialize services
app = FastAPI(lifespan=lifespan)
reviewer = ResumeReviewService()


@app.post("/review")
async def review_resume(req: ResumeRequest):
    try:
        prompt = PromptBuilder.build_review_prompt(req.resume_text)
        feedback = reviewer.generate_feedback(prompt)
        return {"feedback": feedback}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
